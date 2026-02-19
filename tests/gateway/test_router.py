"""Tests for gateway message router."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

import mypalclara.gateway.router as router_module
from mypalclara.gateway.protocol import ChannelInfo, MessageRequest, UserInfo
from mypalclara.gateway.router import DEBOUNCE_SECONDS, MessageRouter, RequestStatus


@pytest.fixture
def router(monkeypatch):
    """Create a fresh message router with debounce disabled."""
    monkeypatch.setattr(router_module, "DEBOUNCE_SECONDS", 0.0)
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


class TestDebounce:
    """Tests for message debounce (rapid-fire consolidation)."""

    @pytest.fixture
    def debounce_router(self, monkeypatch):
        """Create a router with a short debounce window for testing."""
        monkeypatch.setattr(router_module, "DEBOUNCE_SECONDS", 0.1)
        return MessageRouter()

    @pytest.mark.asyncio
    async def test_single_message_processes_after_delay(self, debounce_router, mock_websocket):
        """A single message should be processed after the debounce window."""
        callback = AsyncMock()
        debounce_router.set_debounce_callback(callback)

        req = make_request("req-1", content="hey clara")
        acquired, position = await debounce_router.submit(req, mock_websocket, "node-1")

        # Should not acquire immediately — debouncing
        assert acquired is False
        assert position == 0
        assert await debounce_router.get_request_status("req-1") == RequestStatus.DEBOUNCE

        # Wait for debounce to expire
        await asyncio.sleep(0.2)

        # Callback should have been called with the request
        callback.assert_called_once()
        call_args = callback.call_args
        assert call_args[0][0] == "channel-1"  # channel_id
        assert call_args[0][1].request.content == "hey clara"
        assert await debounce_router.get_request_status("req-1") == RequestStatus.ACTIVE

    @pytest.mark.asyncio
    async def test_rapid_messages_consolidated(self, debounce_router, mock_websocket):
        """Multiple rapid messages should be consolidated into one."""
        callback = AsyncMock()
        debounce_router.set_debounce_callback(callback)

        req1 = make_request("req-1", content="hey")
        req2 = make_request("req-2", content="can you help me")
        req3 = make_request("req-3", content="with python?")

        await debounce_router.submit(req1, mock_websocket, "node-1")
        await debounce_router.submit(req2, mock_websocket, "node-1")
        await debounce_router.submit(req3, mock_websocket, "node-1")

        # Wait for debounce to expire
        await asyncio.sleep(0.2)

        # Should have been called once with consolidated content
        callback.assert_called_once()
        consolidated = callback.call_args[0][1]
        assert consolidated.request.content == "hey\ncan you help me\nwith python?"
        # First request's ID is kept
        assert consolidated.request.id == "req-1"

    @pytest.mark.asyncio
    async def test_mention_skips_debounce(self, debounce_router, mock_websocket):
        """Mentions should skip debounce and acquire immediately."""
        req = make_request("req-1", content="@Clara help")
        acquired, position = await debounce_router.submit(
            req, mock_websocket, "node-1", is_mention=True
        )

        assert acquired is True
        assert position == 0
        assert await debounce_router.get_request_status("req-1") == RequestStatus.ACTIVE

    @pytest.mark.asyncio
    async def test_debounce_during_active_request_queues_normally(
        self, debounce_router, mock_websocket
    ):
        """Messages arriving while channel is busy should queue, not debounce."""
        # First request acquires via mention (skip debounce)
        req1 = make_request("req-1", content="first")
        await debounce_router.submit(req1, mock_websocket, "node-1", is_mention=True)

        # Second request while channel is busy — should queue
        req2 = make_request("req-2", content="second")
        acquired, position = await debounce_router.submit(req2, mock_websocket, "node-1")

        assert acquired is False
        assert position == 1  # Queued, not debounced (would be 0)
        assert await debounce_router.get_request_status("req-2") == RequestStatus.QUEUED

    @pytest.mark.asyncio
    async def test_consolidated_uses_latest_metadata(self, debounce_router, mock_websocket):
        """Consolidated request should use latest request's metadata."""
        callback = AsyncMock()
        debounce_router.set_debounce_callback(callback)

        req1 = make_request("req-1", content="hey")
        req1.metadata = {"source": "old"}
        req1.tier_override = "low"

        req2 = make_request("req-2", content="help me")
        req2.metadata = {"source": "new", "extra": True}
        req2.tier_override = "high"

        await debounce_router.submit(req1, mock_websocket, "node-1")
        await debounce_router.submit(req2, mock_websocket, "node-1")

        await asyncio.sleep(0.2)

        consolidated = callback.call_args[0][1]
        assert consolidated.request.metadata == {"source": "new", "extra": True}
        assert consolidated.request.tier_override == "high"

    @pytest.mark.asyncio
    async def test_consolidated_marks_folded_requests_completed(
        self, debounce_router, mock_websocket
    ):
        """Folded request IDs should be marked as completed."""
        callback = AsyncMock()
        debounce_router.set_debounce_callback(callback)

        req1 = make_request("req-1", content="hey")
        req2 = make_request("req-2", content="there")

        await debounce_router.submit(req1, mock_websocket, "node-1")
        await debounce_router.submit(req2, mock_websocket, "node-1")

        await asyncio.sleep(0.2)

        # First request becomes the active consolidated request
        assert await debounce_router.get_request_status("req-1") == RequestStatus.ACTIVE
        # Second request was folded in and marked completed
        assert await debounce_router.get_request_status("req-2") == RequestStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_cancel_channel_cleans_debounce(self, debounce_router, mock_websocket):
        """Cancelling a channel should clean up debounce state."""
        req1 = make_request("req-1", content="hey")
        req2 = make_request("req-2", content="there")

        await debounce_router.submit(req1, mock_websocket, "node-1")
        await debounce_router.submit(req2, mock_websocket, "node-1")

        # Cancel while still debouncing
        _, num_cancelled = await debounce_router.cancel_channel("channel-1")

        assert num_cancelled == 2
        assert await debounce_router.get_request_status("req-1") == RequestStatus.CANCELLED
        assert await debounce_router.get_request_status("req-2") == RequestStatus.CANCELLED

    @pytest.mark.asyncio
    async def test_debounce_timer_resets_on_new_message(self, debounce_router, mock_websocket):
        """Each new message during debounce should reset the timer."""
        callback = AsyncMock()
        debounce_router.set_debounce_callback(callback)

        req1 = make_request("req-1", content="hey")
        await debounce_router.submit(req1, mock_websocket, "node-1")

        # Wait half the debounce window, then send another
        await asyncio.sleep(0.06)
        req2 = make_request("req-2", content="there")
        await debounce_router.submit(req2, mock_websocket, "node-1")

        # At 0.06s, the first timer (would expire at 0.1s) was cancelled.
        # New timer starts, expires at 0.06+0.1=0.16s
        await asyncio.sleep(0.06)
        # At 0.12s — original timer would have expired, but callback should NOT have fired yet
        callback.assert_not_called()

        # Wait for second timer to expire
        await asyncio.sleep(0.1)
        callback.assert_called_once()
