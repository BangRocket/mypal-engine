"""Hook system for plugin lifecycle events.

This module defines hook events that plugins can register handlers for.
Hooks are emitted at key points in Clara's lifecycle.

Hooks allow plugins to:
- React to incoming/outgoing messages
- Modify tool execution behavior
- Respond to session state changes
- Monitor and influence system behavior
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable, TypeAlias


class HookEvent(str, Enum):
    """All hook events that can be registered.

    Events are emitted at specific points in Clara's lifecycle.
    """

    # Message hooks
    MESSAGE_RECEIVED = "message_received"
    MESSAGE_SENDING = "message_sending"
    MESSAGE_SENT = "message_sent"

    # Tool hooks
    TOOL_START = "tool_start"
    TOOL_END = "tool_end"
    TOOL_ERROR = "tool_error"

    # Session hooks
    SESSION_START = "session_start"
    SESSION_END = "session_end"
    SESSION_TIMEOUT = "session_timeout"

    # System hooks
    SYSTEM_START = "system_start"
    SYSTEM_STOP = "system_stop"

    # LLM hooks
    LLM_REQUEST = "llm_request"
    LLM_RESPONSE = "llm_response"
    LLM_ERROR = "llm_error"

    # Memory hooks
    MEMORY_READ = "memory_read"
    MEMORY_WRITE = "memory_write"

    # MCP hooks
    MCP_SERVER_START = "mcp_server_start"
    MCP_SERVER_STOP = "mcp_server_stop"
    MCP_SERVER_ERROR = "mcp_server_error"


@dataclass
class MessageReceivedEvent:
    """Event data for MESSAGE_RECEIVED hook."""

    user_id: str
    content: str
    channel_id: str | None = None
    platform: str = "discord"
    message_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class MessageSendingEvent:
    """Event data for MESSAGE_SENDING hook.

    Handlers can return modified content to change what's sent.
    """

    user_id: str
    content: str
    channel_id: str | None = None
    platform: str = "discord"
    message_id: str | None = None


@dataclass
class MessageSentEvent:
    """Event data for MESSAGE_SENT hook."""

    user_id: str
    content: str
    channel_id: str | None = None
    platform: str = "discord"
    message_id: str | None = None
    success: bool = True
    error: str | None = None


@dataclass
class MessageSendingResult:
    """Result from MESSAGE_SENDING hook.

    Handlers can return this to modify/abort sending.
    """

    content: str | None = None
    cancel: bool = False
    cancel_reason: str | None = None


@dataclass
class ToolStartEvent:
    """Event data for TOOL_START hook."""

    tool_name: str
    arguments: dict[str, Any]
    user_id: str | None = None
    session_key: str | None = None
    platform: str = "discord"


@dataclass
class ToolEndEvent:
    """Event data for TOOL_END hook."""

    tool_name: str
    arguments: dict[str, Any]
    result: str
    duration_ms: float = 0.0
    user_id: str | None = None
    session_key: str | None = None
    platform: str = "discord"
    error: str | None = None


@dataclass
class ToolErrorEvent:
    """Event data for TOOL_ERROR hook."""

    tool_name: str
    arguments: dict[str, Any]
    error: str
    traceback: str | None = None
    user_id: str | None = None
    session_key: str | None = None
    platform: str = "discord"


@dataclass
class SessionStartEvent:
    """Event data for SESSION_START hook."""

    user_id: str
    session_key: str
    platform: str = "discord"
    resumed_from: str | None = None


@dataclass
class SessionEndEvent:
    """Event data for SESSION_END hook."""

    user_id: str
    session_key: str
    platform: str = "discord"
    message_count: int = 0
    duration_ms: float = 0.0
    summary: str | None = None


@dataclass
class SessionTimeoutEvent:
    """Event data for SESSION_TIMEOUT hook."""

    user_id: str
    session_key: str
    platform: str = "discord"
    idle_minutes: float = 30.0


@dataclass
class LLMRequestEvent:
    """Event data for LLM_REQUEST hook."""

    user_id: str
    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]]
    model: str
    session_key: str | None = None
    platform: str = "discord"
    max_tokens: int | None = None


@dataclass
class LLMResponseEvent:
    """Event data for LLM_RESPONSE hook."""

    user_id: str
    content: str
    tool_calls: list[dict[str, Any]]
    model: str
    session_key: str | None = None
    platform: str = "discord"
    tokens_used: int | None = None
    duration_ms: float = 0.0


@dataclass
class LLMErrorEvent:
    """Event data for LLM_ERROR hook."""

    user_id: str
    error: str
    model: str
    session_key: str | None = None
    platform: str = "discord"
    retry_count: int = 0


@dataclass
class MemoryReadEvent:
    """Event data for MEMORY_READ hook."""

    user_id: str
    query: str
    limit: int | None = None
    result_count: int = 0


@dataclass
class MemoryWriteEvent:
    """Event data for MEMORY_WRITE hook."""

    user_id: str
    content: str
    memory_type: str = "fact"
    success: bool = True


@dataclass
class MCPServerStartEvent:
    """Event data for MCP_SERVER_START hook."""

    server_name: str
    server_type: str  # "local" or "remote"
    transport: str  # "stdio" or "http"


@dataclass
class MCPServerStopEvent:
    """Event data for MCP_SERVER_STOP hook."""

    server_name: str
    server_type: str
    duration_ms: float = 0.0


@dataclass
class MCPServerErrorEvent:
    """Event data for MCP_SERVER_ERROR hook."""

    server_name: str
    server_type: str
    error: str
    transport: str


# Hook handler type aliases
MessageReceivedHandler: TypeAlias = Callable[[MessageReceivedEvent], Awaitable[Any]]
MessageSendingHandler: TypeAlias = Callable[[MessageSendingEvent], Awaitable[MessageSendingResult | None]]
MessageSentHandler: TypeAlias = Callable[[MessageSentEvent], Awaitable[Any]]
ToolStartHandler: TypeAlias = Callable[[ToolStartEvent], Awaitable[Any]]
ToolEndHandler: TypeAlias = Callable[[ToolEndEvent], Awaitable[Any]]
ToolErrorHandler: TypeAlias = Callable[[ToolErrorEvent], Awaitable[Any]]
SessionStartHandler: TypeAlias = Callable[[SessionStartEvent], Awaitable[Any]]
SessionEndHandler: TypeAlias = Callable[[SessionEndEvent], Awaitable[Any]]
SessionTimeoutHandler: TypeAlias = Callable[[SessionTimeoutEvent], Awaitable[Any]]
LLMRequestHandler: TypeAlias = Callable[[LLMRequestEvent], Awaitable[Any]]
LLMResponseHandler: TypeAlias = Callable[[LLMResponseEvent], Awaitable[Any]]
LLMErrorHandler: TypeAlias = Callable[[LLMErrorEvent], Awaitable[Any]]
MemoryReadHandler: TypeAlias = Callable[[MemoryReadEvent], Awaitable[Any]]
MemoryWriteHandler: TypeAlias = Callable[[MemoryWriteEvent], Awaitable[Any]]
MCPServerStartHandler: TypeAlias = Callable[[MCPServerStartEvent], Awaitable[Any]]
MCPServerStopHandler: TypeAlias = Callable[[MCPServerStopEvent], Awaitable[Any]]
MCPServerErrorHandler: TypeAlias = Callable[[MCPServerErrorEvent], Awaitable[Any]]


# Event type to handler type mapping
HOOK_EVENT_TYPES: dict[str, type] = {
    HookEvent.MESSAGE_RECEIVED: MessageReceivedEvent,
    HookEvent.MESSAGE_SENDING: MessageSendingEvent,
    HookEvent.MESSAGE_SENT: MessageSentEvent,
    HookEvent.TOOL_START: ToolStartEvent,
    HookEvent.TOOL_END: ToolEndEvent,
    HookEvent.TOOL_ERROR: ToolErrorEvent,
    HookEvent.SESSION_START: SessionStartEvent,
    HookEvent.SESSION_END: SessionEndEvent,
    HookEvent.SESSION_TIMEOUT: SessionTimeoutEvent,
    HookEvent.LLM_REQUEST: LLMRequestEvent,
    HookEvent.LLM_RESPONSE: LLMResponseEvent,
    HookEvent.LLM_ERROR: LLMErrorEvent,
    HookEvent.MEMORY_READ: MemoryReadEvent,
    HookEvent.MEMORY_WRITE: MemoryWriteEvent,
    HookEvent.MCP_SERVER_START: MCPServerStartEvent,
    HookEvent.MCP_SERVER_STOP: MCPServerStopEvent,
    HookEvent.MCP_SERVER_ERROR: MCPServerErrorEvent,
}


def validate_hook_handler(event: str, handler: Callable) -> bool:
    """Validate that a handler matches the expected signature.

    Args:
        event: Hook event name
        handler: Handler function to validate

    Returns:
        True if handler is callable, False otherwise
    """
    if not callable(handler):
        return False

    return True


def get_event_type(event: str) -> type | None:
    """Get the expected event data type for a hook.

    Args:
        event: Hook event name

    Returns:
        Event dataclass type or None
    """
    return HOOK_EVENT_TYPES.get(event)
