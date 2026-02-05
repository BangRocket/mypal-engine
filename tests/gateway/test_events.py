"""Tests for gateway event system."""

import asyncio
from datetime import datetime

import pytest

from mypalclara.gateway.events import (
    Event,
    EventEmitter,
    EventType,
    reset_event_emitter,
)


@pytest.fixture
def emitter():
    """Create a fresh event emitter."""
    return EventEmitter()


@pytest.fixture(autouse=True)
def reset_global():
    """Reset global emitter between tests."""
    yield
    reset_event_emitter()


class TestEvent:
    """Tests for Event dataclass."""

    def test_event_creation(self):
        event = Event(type=EventType.GATEWAY_STARTUP)
        assert event.type == EventType.GATEWAY_STARTUP
        assert isinstance(event.timestamp, datetime)
        assert event.data == {}

    def test_event_with_data(self):
        event = Event(
            type=EventType.MESSAGE_RECEIVED,
            data={"content": "hello"},
            user_id="user-123",
            channel_id="channel-456",
        )
        assert event.data["content"] == "hello"
        assert event.user_id == "user-123"
        assert event.channel_id == "channel-456"


class TestEventEmitter:
    """Tests for EventEmitter."""

    @pytest.mark.asyncio
    async def test_basic_handler(self, emitter):
        received = []

        async def handler(event: Event):
            received.append(event)

        emitter.on(EventType.GATEWAY_STARTUP, handler)

        event = Event(type=EventType.GATEWAY_STARTUP)
        await emitter.emit(event)

        assert len(received) == 1
        assert received[0] is event

    @pytest.mark.asyncio
    async def test_multiple_handlers(self, emitter):
        results = []

        async def handler1(event: Event):
            results.append("handler1")

        async def handler2(event: Event):
            results.append("handler2")

        emitter.on(EventType.GATEWAY_STARTUP, handler1)
        emitter.on(EventType.GATEWAY_STARTUP, handler2)

        await emitter.emit(Event(type=EventType.GATEWAY_STARTUP))

        assert "handler1" in results
        assert "handler2" in results

    @pytest.mark.asyncio
    async def test_handler_priority(self, emitter):
        results = []

        async def low_priority(event: Event):
            results.append("low")

        async def high_priority(event: Event):
            results.append("high")

        emitter.on(EventType.GATEWAY_STARTUP, low_priority, priority=0)
        emitter.on(EventType.GATEWAY_STARTUP, high_priority, priority=10)

        await emitter.emit(Event(type=EventType.GATEWAY_STARTUP))

        # High priority should run first
        assert results[0] == "high"
        assert results[1] == "low"

    @pytest.mark.asyncio
    async def test_wildcard_handler(self, emitter):
        received = []

        async def handler(event: Event):
            received.append(event.type)

        emitter.on("*", handler)

        await emitter.emit(Event(type=EventType.GATEWAY_STARTUP))
        await emitter.emit(Event(type=EventType.MESSAGE_RECEIVED))

        assert len(received) == 2
        assert EventType.GATEWAY_STARTUP in received
        assert EventType.MESSAGE_RECEIVED in received

    @pytest.mark.asyncio
    async def test_handler_error_isolation(self, emitter):
        results = []

        async def failing_handler(event: Event):
            raise ValueError("Test error")

        async def working_handler(event: Event):
            results.append("success")

        emitter.on(EventType.GATEWAY_STARTUP, failing_handler, priority=10)
        emitter.on(EventType.GATEWAY_STARTUP, working_handler, priority=0)

        # Should not raise, and working handler should still run
        await emitter.emit(Event(type=EventType.GATEWAY_STARTUP))

        assert "success" in results

    @pytest.mark.asyncio
    async def test_off_removes_handler(self, emitter):
        received = []

        async def handler(event: Event):
            received.append(event)

        emitter.on(EventType.GATEWAY_STARTUP, handler)
        removed = emitter.off(EventType.GATEWAY_STARTUP, handler)
        assert removed is True

        await emitter.emit(Event(type=EventType.GATEWAY_STARTUP))
        assert len(received) == 0

    def test_event_history(self, emitter):
        # Emit without async for history test
        loop = asyncio.new_event_loop()

        for i in range(5):
            loop.run_until_complete(emitter.emit(Event(type=EventType.GATEWAY_STARTUP, data={"i": i})))

        history = emitter.get_history(limit=3)
        assert len(history) == 3
        # Newest first
        assert history[0].data["i"] == 4
        assert history[2].data["i"] == 2

        loop.close()

    def test_stats(self, emitter):
        async def handler(event: Event):
            pass

        emitter.on(EventType.GATEWAY_STARTUP, handler)
        emitter.on(EventType.MESSAGE_RECEIVED, handler)
        emitter.on("*", handler)

        stats = emitter.get_stats()
        assert stats["wildcard_handlers"] == 1
        assert EventType.GATEWAY_STARTUP.value in stats["handler_counts"]
