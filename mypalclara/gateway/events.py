"""Event system for the Clara Gateway.

Provides an async event emitter for gateway lifecycle and message events.
Hooks can subscribe to these events for automation.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Coroutine

from config.logging import get_logger

logger = get_logger("gateway.events")


class EventType(str, Enum):
    """Gateway event types."""

    # Lifecycle events
    GATEWAY_STARTUP = "gateway:startup"
    GATEWAY_SHUTDOWN = "gateway:shutdown"

    # Adapter events
    ADAPTER_CONNECTED = "adapter:connected"
    ADAPTER_DISCONNECTED = "adapter:disconnected"

    # Session events
    SESSION_START = "session:start"
    SESSION_END = "session:end"
    SESSION_TIMEOUT = "session:timeout"

    # Message events
    MESSAGE_RECEIVED = "message:received"
    MESSAGE_SENT = "message:sent"
    MESSAGE_CANCELLED = "message:cancelled"

    # Tool events
    TOOL_START = "tool:start"
    TOOL_END = "tool:end"
    TOOL_ERROR = "tool:error"

    # Scheduler events
    SCHEDULED_TASK_RUN = "scheduler:task_run"
    SCHEDULED_TASK_ERROR = "scheduler:task_error"

    # Custom events (for user-defined hooks)
    CUSTOM = "custom"


@dataclass
class Event:
    """An event emitted by the gateway."""

    type: EventType
    timestamp: datetime = field(default_factory=datetime.now)
    data: dict[str, Any] = field(default_factory=dict)

    # Context about where the event originated
    node_id: str | None = None
    platform: str | None = None
    user_id: str | None = None
    channel_id: str | None = None
    request_id: str | None = None

    def __repr__(self) -> str:
        return f"Event({self.type.value}, data={self.data})"


# Type alias for event handlers
EventHandler = Callable[[Event], Coroutine[Any, Any, None]]


class EventEmitter:
    """Async event emitter for gateway events.

    Supports:
    - Multiple handlers per event type
    - Wildcard handlers (receive all events)
    - Handler priority ordering
    - Error isolation (one handler failure doesn't stop others)
    """

    def __init__(self) -> None:
        self._handlers: dict[EventType | str, list[tuple[int, EventHandler]]] = defaultdict(list)
        self._wildcard_handlers: list[tuple[int, EventHandler]] = []
        self._event_history: list[Event] = []
        self._history_limit = 100

    def on(
        self,
        event_type: EventType | str,
        handler: EventHandler,
        priority: int = 0,
    ) -> None:
        """Register an event handler.

        Args:
            event_type: The event type to handle, or "*" for all events
            handler: Async function to call when event fires
            priority: Higher priority handlers run first (default: 0)
        """
        if event_type == "*":
            self._wildcard_handlers.append((priority, handler))
            self._wildcard_handlers.sort(key=lambda x: -x[0])
        else:
            key = event_type.value if isinstance(event_type, EventType) else event_type
            self._handlers[key].append((priority, handler))
            self._handlers[key].sort(key=lambda x: -x[0])

        logger.debug(f"Registered handler for {event_type} (priority={priority})")

    def off(self, event_type: EventType | str, handler: EventHandler) -> bool:
        """Remove an event handler.

        Args:
            event_type: The event type
            handler: The handler to remove

        Returns:
            True if handler was found and removed
        """
        if event_type == "*":
            for i, (_, h) in enumerate(self._wildcard_handlers):
                if h == handler:
                    self._wildcard_handlers.pop(i)
                    return True
            return False

        key = event_type.value if isinstance(event_type, EventType) else event_type
        for i, (_, h) in enumerate(self._handlers[key]):
            if h == handler:
                self._handlers[key].pop(i)
                return True
        return False

    async def emit(self, event: Event) -> None:
        """Emit an event to all registered handlers.

        Args:
            event: The event to emit
        """
        # Store in history
        self._event_history.append(event)
        if len(self._event_history) > self._history_limit:
            self._event_history.pop(0)

        key = event.type.value

        # Collect all handlers (specific + wildcard)
        handlers: list[tuple[int, EventHandler]] = []
        handlers.extend(self._handlers.get(key, []))
        handlers.extend(self._wildcard_handlers)

        # Sort by priority (higher first)
        handlers.sort(key=lambda x: -x[0])

        if not handlers:
            logger.debug(f"No handlers for event {key}")
            return

        logger.debug(f"Emitting {key} to {len(handlers)} handlers")

        # Run all handlers concurrently
        tasks = [self._run_handler(handler, event) for _, handler in handlers]
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _run_handler(self, handler: EventHandler, event: Event) -> None:
        """Run a single handler with error isolation.

        Args:
            handler: The handler to run
            event: The event to pass
        """
        try:
            await handler(event)
        except Exception as e:
            logger.exception(f"Handler error for {event.type.value}: {e}")

    def get_history(self, limit: int = 50) -> list[Event]:
        """Get recent event history.

        Args:
            limit: Maximum events to return

        Returns:
            List of recent events (newest first)
        """
        return list(reversed(self._event_history[-limit:]))

    def get_stats(self) -> dict[str, Any]:
        """Get emitter statistics."""
        handler_counts = {k: len(v) for k, v in self._handlers.items()}
        return {
            "handler_counts": handler_counts,
            "wildcard_handlers": len(self._wildcard_handlers),
            "history_size": len(self._event_history),
        }


# Global event emitter singleton
_emitter: EventEmitter | None = None


def get_event_emitter() -> EventEmitter:
    """Get the global event emitter instance."""
    global _emitter
    if _emitter is None:
        _emitter = EventEmitter()
    return _emitter


def reset_event_emitter() -> None:
    """Reset the global event emitter (for testing)."""
    global _emitter
    _emitter = None


# Convenience functions
async def emit(event: Event) -> None:
    """Emit an event using the global emitter."""
    await get_event_emitter().emit(event)


def on(event_type: EventType | str, handler: EventHandler, priority: int = 0) -> None:
    """Register a handler on the global emitter."""
    get_event_emitter().on(event_type, handler, priority)


def off(event_type: EventType | str, handler: EventHandler) -> bool:
    """Remove a handler from the global emitter."""
    return get_event_emitter().off(event_type, handler)
