"""Message routing and queue management for the Clara Gateway.

Handles:
- Per-channel message queuing
- Request lifecycle (pending, active, completed)
- Cancellation support
- Batch processing for active mode
- Message deduplication
"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import TYPE_CHECKING, Any

from config.logging import get_logger
from mypalclara.gateway.protocol import MessageRequest

if TYPE_CHECKING:
    from websockets.server import WebSocketServerProtocol

logger = get_logger("gateway.router")

# Deduplication settings
DEDUP_WINDOW_SECONDS = 30  # Time window to check for duplicates
DEDUP_MAX_ENTRIES = 1000  # Maximum entries in dedup cache


class RequestStatus(str, Enum):
    """Status of a message request."""

    PENDING = "pending"
    QUEUED = "queued"
    ACTIVE = "active"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


@dataclass
class QueuedRequest:
    """A request waiting to be processed."""

    request: MessageRequest
    websocket: WebSocketServerProtocol
    node_id: str
    queued_at: datetime = field(default_factory=datetime.now)
    position: int = 0
    is_batchable: bool = False  # True for active-mode channel messages

    @property
    def request_id(self) -> str:
        """Get the request ID."""
        return self.request.id

    @property
    def channel_id(self) -> str:
        """Get the channel ID."""
        return self.request.channel.id


@dataclass
class ActiveRequest:
    """A request currently being processed."""

    request: MessageRequest
    websocket: WebSocketServerProtocol
    node_id: str
    task: asyncio.Task[Any] | None = None
    started_at: datetime = field(default_factory=datetime.now)
    tool_count: int = 0

    @property
    def request_id(self) -> str:
        """Get the request ID."""
        return self.request.id

    @property
    def channel_id(self) -> str:
        """Get the channel ID."""
        return self.request.channel.id


class MessageRouter:
    """Routes messages to processing and manages queues.

    Provides channel-level locking to prevent concurrent tool execution
    within the same channel, while allowing parallel processing across channels.
    Also provides message deduplication to prevent processing the same message twice.
    """

    def __init__(self) -> None:
        # Active requests by channel
        self._active: dict[str, ActiveRequest] = {}
        # Queued requests by channel
        self._queues: dict[str, list[QueuedRequest]] = {}
        # All known requests by ID
        self._requests: dict[str, RequestStatus] = {}
        # Lock for thread-safe operations
        self._lock = asyncio.Lock()
        # Deduplication cache: fingerprint -> timestamp
        self._seen_messages: dict[str, datetime] = {}
        # Lock for dedup cache
        self._dedup_lock = asyncio.Lock()

    def _compute_fingerprint(self, request: MessageRequest) -> str:
        """Compute a fingerprint for deduplication.

        Args:
            request: The message request

        Returns:
            Hash string identifying this message
        """
        # Create fingerprint from content + user + channel
        data = f"{request.user.id}|{request.channel.id}|{request.content}"
        return hashlib.sha256(data.encode()).hexdigest()[:16]

    async def is_duplicate(self, request: MessageRequest) -> bool:
        """Check if a message is a duplicate within the dedup window.

        Args:
            request: The message request to check

        Returns:
            True if this message was seen recently
        """
        fingerprint = self._compute_fingerprint(request)
        now = datetime.now()
        cutoff = now - timedelta(seconds=DEDUP_WINDOW_SECONDS)

        async with self._dedup_lock:
            # Clean old entries periodically
            if len(self._seen_messages) > DEDUP_MAX_ENTRIES:
                self._seen_messages = {fp: ts for fp, ts in self._seen_messages.items() if ts > cutoff}

            # Check if seen recently
            if fingerprint in self._seen_messages:
                last_seen = self._seen_messages[fingerprint]
                if last_seen > cutoff:
                    logger.debug(f"Duplicate message detected: {request.id}")
                    return True

            # Record this message
            self._seen_messages[fingerprint] = now
            return False

    async def submit(
        self,
        request: MessageRequest,
        websocket: WebSocketServerProtocol,
        node_id: str,
        is_batchable: bool = False,
        skip_dedup: bool = False,
    ) -> tuple[bool, int]:
        """Submit a request for processing.

        Args:
            request: The message request
            websocket: Originating WebSocket connection
            node_id: Originating node ID
            is_batchable: Whether this can be batched with other requests
            skip_dedup: Skip deduplication check (for retries)

        Returns:
            Tuple of (acquired_immediately, queue_position)
            If acquired is True, caller should process immediately.
            If False, queue_position indicates position in queue (1-indexed).
            If (False, -1), the message was rejected as a duplicate.
        """
        # Check for duplicates first
        if not skip_dedup and await self.is_duplicate(request):
            return False, -1  # Rejected as duplicate

        channel_id = request.channel.id

        async with self._lock:
            self._requests[request.id] = RequestStatus.PENDING

            if channel_id not in self._active:
                # No active request, start immediately
                self._active[channel_id] = ActiveRequest(
                    request=request,
                    websocket=websocket,
                    node_id=node_id,
                )
                self._requests[request.id] = RequestStatus.ACTIVE
                logger.debug(f"Request {request.id} acquired channel {channel_id}")
                return True, 0

            # Channel is busy, queue the request
            if channel_id not in self._queues:
                self._queues[channel_id] = []

            queue = self._queues[channel_id]
            position = len(queue) + 1

            queued = QueuedRequest(
                request=request,
                websocket=websocket,
                node_id=node_id,
                position=position,
                is_batchable=is_batchable,
            )
            queue.append(queued)
            self._requests[request.id] = RequestStatus.QUEUED

            logger.debug(f"Request {request.id} queued at position {position} for channel {channel_id}")
            return False, position

    async def complete(self, request_id: str) -> QueuedRequest | None:
        """Mark a request as complete and return the next queued request.

        Args:
            request_id: The completed request ID

        Returns:
            Next queued request if any, or None
        """
        async with self._lock:
            # Find which channel this request was for
            channel_id = None
            for ch_id, active in self._active.items():
                if active.request_id == request_id:
                    channel_id = ch_id
                    break

            if not channel_id:
                logger.warning(f"Completed unknown request {request_id}")
                return None

            # Mark as completed
            self._requests[request_id] = RequestStatus.COMPLETED

            # Remove from active
            del self._active[channel_id]

            # Get next queued request
            if channel_id in self._queues and self._queues[channel_id]:
                next_req = self._queues[channel_id].pop(0)
                self._active[channel_id] = ActiveRequest(
                    request=next_req.request,
                    websocket=next_req.websocket,
                    node_id=next_req.node_id,
                )
                self._requests[next_req.request_id] = RequestStatus.ACTIVE
                logger.debug(
                    f"Dequeued request {next_req.request_id} for channel {channel_id}, "
                    f"{len(self._queues[channel_id])} remaining"
                )
                return next_req

            return None

    async def complete_batch(self, request_id: str) -> list[QueuedRequest]:
        """Mark a request as complete and return all batchable queued requests.

        For active mode, allows responding to multiple messages at once.

        Args:
            request_id: The completed request ID

        Returns:
            List of batchable requests (may be empty)
        """
        async with self._lock:
            # Find which channel this request was for
            channel_id = None
            for ch_id, active in self._active.items():
                if active.request_id == request_id:
                    channel_id = ch_id
                    break

            if not channel_id:
                logger.warning(f"Completed unknown request {request_id}")
                return []

            # Mark as completed
            self._requests[request_id] = RequestStatus.COMPLETED

            # Remove from active
            del self._active[channel_id]

            # Check for batchable queued requests
            if channel_id not in self._queues or not self._queues[channel_id]:
                return []

            queue = self._queues[channel_id]

            # If first request isn't batchable, don't batch
            if not queue[0].is_batchable:
                return []

            # Collect consecutive batchable requests
            batch: list[QueuedRequest] = []
            while queue and queue[0].is_batchable:
                batch.append(queue.pop(0))

            if batch:
                # Mark last request as active
                last = batch[-1]
                self._active[channel_id] = ActiveRequest(
                    request=last.request,
                    websocket=last.websocket,
                    node_id=last.node_id,
                )
                for req in batch:
                    self._requests[req.request_id] = RequestStatus.ACTIVE

                logger.debug(f"Batched {len(batch)} request(s) for channel {channel_id}, " f"{len(queue)} remaining")

            return batch

    async def cancel(self, request_id: str) -> bool:
        """Cancel a request.

        If the request is active, its task will be cancelled.
        If queued, it will be removed from the queue.

        Args:
            request_id: The request to cancel

        Returns:
            True if request was found and cancelled
        """
        async with self._lock:
            status = self._requests.get(request_id)
            if not status:
                return False

            if status == RequestStatus.ACTIVE:
                # Find and cancel the active task
                for channel_id, active in self._active.items():
                    if active.request_id == request_id:
                        if active.task and not active.task.done():
                            active.task.cancel()
                        del self._active[channel_id]
                        self._requests[request_id] = RequestStatus.CANCELLED
                        logger.info(f"Cancelled active request {request_id}")
                        return True

            elif status == RequestStatus.QUEUED:
                # Find and remove from queue
                for queue in self._queues.values():
                    for i, queued in enumerate(queue):
                        if queued.request_id == request_id:
                            queue.pop(i)
                            self._requests[request_id] = RequestStatus.CANCELLED
                            logger.info(f"Cancelled queued request {request_id}")
                            return True

            return False

    async def cancel_channel(self, channel_id: str) -> tuple[bool, int]:
        """Cancel all requests for a channel.

        Args:
            channel_id: The channel to cancel

        Returns:
            Tuple of (had_active_task, num_queued_cancelled)
        """
        async with self._lock:
            had_active = False
            num_queued = 0

            # Cancel active request
            if channel_id in self._active:
                active = self._active[channel_id]
                if active.task and not active.task.done():
                    active.task.cancel()
                    had_active = True
                self._requests[active.request_id] = RequestStatus.CANCELLED
                del self._active[channel_id]

            # Cancel queued requests
            if channel_id in self._queues:
                queue = self._queues[channel_id]
                num_queued = len(queue)
                for queued in queue:
                    self._requests[queued.request_id] = RequestStatus.CANCELLED
                del self._queues[channel_id]

            if had_active or num_queued:
                logger.info(f"Cancelled channel {channel_id}: " f"active={had_active}, queued={num_queued}")

            return had_active, num_queued

    async def register_task(self, request_id: str, task: asyncio.Task[Any]) -> None:
        """Register the processing task for a request.

        Args:
            request_id: The request being processed
            task: The asyncio task handling it
        """
        async with self._lock:
            for active in self._active.values():
                if active.request_id == request_id:
                    active.task = task
                    return

    async def get_active(self, channel_id: str) -> ActiveRequest | None:
        """Get the active request for a channel."""
        async with self._lock:
            return self._active.get(channel_id)

    async def get_queue_length(self, channel_id: str) -> int:
        """Get the number of queued requests for a channel."""
        async with self._lock:
            if channel_id in self._queues:
                return len(self._queues[channel_id])
            return 0

    async def is_channel_busy(self, channel_id: str) -> bool:
        """Check if a channel has an active request."""
        async with self._lock:
            return channel_id in self._active

    async def get_request_status(self, request_id: str) -> RequestStatus | None:
        """Get the status of a request."""
        async with self._lock:
            return self._requests.get(request_id)

    async def increment_tool_count(self, request_id: str) -> int:
        """Increment the tool count for an active request.

        Returns:
            The new tool count
        """
        async with self._lock:
            for active in self._active.values():
                if active.request_id == request_id:
                    active.tool_count += 1
                    return active.tool_count
            return 0

    async def get_stats(self) -> dict[str, Any]:
        """Get router statistics."""
        async with self._lock:
            total_queued = sum(len(q) for q in self._queues.values())
            by_status: dict[str, int] = {}
            for status in self._requests.values():
                by_status[status.value] = by_status.get(status.value, 0) + 1

            return {
                "active_channels": len(self._active),
                "total_queued": total_queued,
                "by_status": by_status,
            }

    async def cleanup_old_requests(self, max_age_hours: int = 24) -> int:
        """Clean up old completed/failed/cancelled request records.

        Args:
            max_age_hours: Maximum age in hours

        Returns:
            Number of records cleaned
        """
        # For now, just limit the size of the requests dict
        async with self._lock:
            if len(self._requests) > 10000:
                # Keep only recent and active requests
                keep_statuses = {RequestStatus.PENDING, RequestStatus.QUEUED, RequestStatus.ACTIVE}
                to_remove = [rid for rid, status in self._requests.items() if status not in keep_statuses]
                # Remove oldest half
                for rid in to_remove[: len(to_remove) // 2]:
                    del self._requests[rid]
                return len(to_remove) // 2
            return 0
