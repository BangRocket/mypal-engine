"""Tests for gateway message router."""

import asyncio
from unittest.mock import MagicMock

import pytest

from mypalclara.gateway.protocol import ChannelInfo, MessageRequest, UserInfo
from mypalclara.gateway.router import MessageRouter, RequestStatus


@pytest.fixture
def router():
    """Create a fresh message router."""
    return MessageRouter()


@pytest.fixture
def mock_websocket():
    """Create a mock WebSocket."""
    ws = MagicMock()

    async def mock_send(*args, **kwargs):
        return None

    ws.send = MagicMock(side_effect=mock_send)
    return ws


def make_request(
    request_id: str = "req-1",
    user_id: str = "user-1",
    channel_id: str = "channel-1",
    content: str = "Hello",
) -> MessageRequest:
    """Create a test message request."""
    return MessageRequest(
        id=request_id,
        user=UserInfo(
            id=user_id,
            platform_id=user_id,
            name="Test User",
            display_name="Test",
        ),
        channel=ChannelInfo(
            id=channel_id,
            type="dm",
        ),
        content=content,
    )


class TestMessageRouter:
    """Tests for MessageRouter."""

    @pytest.mark.asyncio
    async def test_first_request_acquires_immediately(self, router, mock_websocket):
        """First request should acquire the channel immediately."""
        request = make_request()
        acquired, position = await router.submit(request, mock_websocket, "node-1")

        assert acquired is True
        assert position == 0
        assert await router.is_channel_busy("channel-1")

    @pytest.mark.asyncio
    async def test_second_request_queues(self, router, mock_websocket):
        """Second request should be queued."""
        req1 = make_request("req-1")
        req2 = make_request("req-2", content="Different content")

        await router.submit(req1, mock_websocket, "node-1")
        acquired, position = await router.submit(req2, mock_websocket, "node-1")

        assert acquired is False
        assert position == 1
        assert await router.get_queue_length("channel-1") == 1

    @pytest.mark.asyncio
    async def test_complete_releases_channel(self, router, mock_websocket):
        """Completing a request should release the channel."""
        request = make_request()
        await router.submit(request, mock_websocket, "node-1")

        next_req = await router.complete("req-1")

        assert next_req is None
        assert not await router.is_channel_busy("channel-1")

    @pytest.mark.asyncio
    async def test_complete_dequeues_next(self, router, mock_websocket):
        """Completing should dequeue the next request."""
        req1 = make_request("req-1")
        req2 = make_request("req-2", content="Second")

        await router.submit(req1, mock_websocket, "node-1")
        await router.submit(req2, mock_websocket, "node-1")

        next_req = await router.complete("req-1")

        assert next_req is not None
        assert next_req.request_id == "req-2"
        assert await router.get_request_status("req-2") == RequestStatus.ACTIVE

    @pytest.mark.asyncio
    async def test_cancel_active_request(self, router, mock_websocket):
        """Should be able to cancel an active request."""
        request = make_request()
        await router.submit(request, mock_websocket, "node-1")

        cancelled = await router.cancel("req-1")

        assert cancelled is True
        assert await router.get_request_status("req-1") == RequestStatus.CANCELLED

    @pytest.mark.asyncio
    async def test_cancel_queued_request(self, router, mock_websocket):
        """Should be able to cancel a queued request."""
        req1 = make_request("req-1")
        req2 = make_request("req-2", content="Second")

        await router.submit(req1, mock_websocket, "node-1")
        await router.submit(req2, mock_websocket, "node-1")

        cancelled = await router.cancel("req-2")

        assert cancelled is True
        assert await router.get_request_status("req-2") == RequestStatus.CANCELLED
        assert await router.get_queue_length("channel-1") == 0

    @pytest.mark.asyncio
    async def test_cancel_channel(self, router, mock_websocket):
        """Should be able to cancel all requests for a channel."""
        req1 = make_request("req-1")
        req2 = make_request("req-2", content="Second")
        req3 = make_request("req-3", content="Third")

        await router.submit(req1, mock_websocket, "node-1")
        await router.submit(req2, mock_websocket, "node-1")
        await router.submit(req3, mock_websocket, "node-1")

        had_active, num_queued = await router.cancel_channel("channel-1")

        # had_active is only True if there was a task to cancel
        # Since we didn't register a task, it's False
        assert num_queued == 2  # req-2 and req-3 were queued
        # Request statuses should be cancelled
        assert await router.get_request_status("req-1") == RequestStatus.CANCELLED
        assert await router.get_request_status("req-2") == RequestStatus.CANCELLED
        assert await router.get_request_status("req-3") == RequestStatus.CANCELLED

    @pytest.mark.asyncio
    async def test_different_channels_independent(self, router, mock_websocket):
        """Different channels should be processed independently."""
        req1 = make_request("req-1", channel_id="channel-1")
        req2 = make_request("req-2", channel_id="channel-2")

        acquired1, _ = await router.submit(req1, mock_websocket, "node-1")
        acquired2, _ = await router.submit(req2, mock_websocket, "node-1")

        assert acquired1 is True
        assert acquired2 is True
        assert await router.is_channel_busy("channel-1")
        assert await router.is_channel_busy("channel-2")


class TestDeduplication:
    """Tests for message deduplication."""

    @pytest.mark.asyncio
    async def test_duplicate_detected(self, router, mock_websocket):
        """Duplicate messages should be detected."""
        request = make_request()

        # First submission
        acquired1, pos1 = await router.submit(request, mock_websocket, "node-1")
        assert acquired1 is True

        # Complete the first one
        await router.complete("req-1")

        # Submit identical message (new ID but same content/user/channel)
        duplicate = make_request("req-2", content="Hello")  # Same content
        acquired2, pos2 = await router.submit(duplicate, mock_websocket, "node-1")

        assert acquired2 is False
        assert pos2 == -1  # Rejected as duplicate

    @pytest.mark.asyncio
    async def test_different_content_not_duplicate(self, router, mock_websocket):
        """Different content should not be detected as duplicate."""
        req1 = make_request("req-1", content="Hello")
        req2 = make_request("req-2", content="Goodbye")

        await router.submit(req1, mock_websocket, "node-1")
        await router.complete("req-1")

        acquired, pos = await router.submit(req2, mock_websocket, "node-1")

        assert acquired is True
        assert pos == 0

    @pytest.mark.asyncio
    async def test_skip_dedup_flag(self, router, mock_websocket):
        """Skip dedup flag should allow duplicates."""
        request = make_request()

        await router.submit(request, mock_websocket, "node-1")
        await router.complete("req-1")

        # Submit identical message with skip_dedup
        duplicate = make_request("req-2", content="Hello")
        acquired, pos = await router.submit(duplicate, mock_websocket, "node-1", skip_dedup=True)

        assert acquired is True


class TestBatchProcessing:
    """Tests for batch processing of active mode messages."""

    @pytest.mark.asyncio
    async def test_batchable_requests_batched(self, router, mock_websocket):
        """Batchable requests should be collected together."""
        req1 = make_request("req-1")
        req2 = make_request("req-2", content="Second")
        req3 = make_request("req-3", content="Third")

        await router.submit(req1, mock_websocket, "node-1")
        await router.submit(req2, mock_websocket, "node-1", is_batchable=True)
        await router.submit(req3, mock_websocket, "node-1", is_batchable=True)

        batch = await router.complete_batch("req-1")

        assert len(batch) == 2
        assert batch[0].request_id == "req-2"
        assert batch[1].request_id == "req-3"

    @pytest.mark.asyncio
    async def test_non_batchable_breaks_batch(self, router, mock_websocket):
        """Non-batchable request should break batch collection."""
        req1 = make_request("req-1")
        req2 = make_request("req-2", content="Second")
        req3 = make_request("req-3", content="Third")

        await router.submit(req1, mock_websocket, "node-1")
        await router.submit(req2, mock_websocket, "node-1", is_batchable=False)
        await router.submit(req3, mock_websocket, "node-1", is_batchable=True)

        batch = await router.complete_batch("req-1")

        # Should return empty because first queued is not batchable
        assert len(batch) == 0
