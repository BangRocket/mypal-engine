"""WebSocket protocol message types for the Clara Gateway.

Defines the message types exchanged between adapters and the gateway using Pydantic.
All messages are JSON-serializable for WebSocket transport.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class MessageType(str, Enum):
    """Types of messages in the gateway protocol."""

    # Registration
    REGISTER = "register"
    REGISTERED = "registered"
    UNREGISTER = "unregister"

    # Heartbeat
    PING = "ping"
    PONG = "pong"

    # Message flow
    MESSAGE = "message"
    RESPONSE_START = "response_start"
    RESPONSE_CHUNK = "response_chunk"
    RESPONSE_END = "response_end"

    # Tool execution
    TOOL_START = "tool_start"
    TOOL_RESULT = "tool_result"

    # Control
    CANCEL = "cancel"
    CANCELLED = "cancelled"
    ERROR = "error"
    STATUS = "status"

    # Proactive (ORS)
    PROACTIVE_MESSAGE = "proactive_message"

    # MCP Management
    MCP_LIST = "mcp_list"
    MCP_LIST_RESPONSE = "mcp_list_response"
    MCP_INSTALL = "mcp_install"
    MCP_INSTALL_RESPONSE = "mcp_install_response"
    MCP_UNINSTALL = "mcp_uninstall"
    MCP_UNINSTALL_RESPONSE = "mcp_uninstall_response"
    MCP_STATUS = "mcp_status"
    MCP_STATUS_RESPONSE = "mcp_status_response"
    MCP_RESTART = "mcp_restart"
    MCP_RESTART_RESPONSE = "mcp_restart_response"
    MCP_ENABLE = "mcp_enable"
    MCP_ENABLE_RESPONSE = "mcp_enable_response"


class UserInfo(BaseModel):
    """Information about a user."""

    id: str = Field(..., description="Platform-prefixed user ID (e.g., discord-123)")
    platform_id: str = Field(..., description="Original platform user ID")
    name: str | None = Field(None, description="Username")
    display_name: str | None = Field(None, description="Display name")


class ChannelInfo(BaseModel):
    """Information about a channel."""

    id: str = Field(..., description="Channel ID")
    type: Literal["dm", "server", "group"] = Field("server", description="Channel type")
    name: str | None = Field(None, description="Channel name")
    guild_id: str | None = Field(None, description="Server/guild ID if applicable")
    guild_name: str | None = Field(None, description="Server/guild name if applicable")


class AttachmentInfo(BaseModel):
    """Information about a message attachment."""

    type: Literal["image", "file", "text"] = Field(..., description="Attachment type")
    filename: str = Field(..., description="Original filename")
    media_type: str | None = Field(None, description="MIME type")
    base64_data: str | None = Field(None, description="Base64-encoded content")
    content: str | None = Field(None, description="Text content (for text files)")
    size: int | None = Field(None, description="File size in bytes")


class NodeInfo(BaseModel):
    """Information about a connected adapter node."""

    node_id: str = Field(..., description="Unique node identifier")
    platform: str = Field(..., description="Platform name (discord, cli, slack, etc.)")
    capabilities: list[str] = Field(default_factory=list, description="Supported features")
    connected_at: datetime = Field(default_factory=datetime.now)
    metadata: dict[str, Any] = Field(default_factory=dict)


# ============================================================================
# Registration Messages
# ============================================================================


class RegisterMessage(BaseModel):
    """Adapter -> Gateway: Register a new adapter node."""

    type: Literal[MessageType.REGISTER] = MessageType.REGISTER
    node_id: str = Field(..., description="Unique identifier for this adapter instance")
    platform: str = Field(..., description="Platform name (discord, cli, slack)")
    capabilities: list[str] = Field(
        default_factory=list,
        description="Supported features (streaming, attachments, reactions, etc.)",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Additional adapter-specific info",
    )


class RegisteredMessage(BaseModel):
    """Gateway -> Adapter: Confirm registration."""

    type: Literal[MessageType.REGISTERED] = MessageType.REGISTERED
    node_id: str = Field(..., description="Confirmed node ID")
    session_id: str = Field(..., description="Gateway session ID for reconnection")
    server_time: datetime = Field(default_factory=datetime.now)


# ============================================================================
# Heartbeat Messages
# ============================================================================


class PingMessage(BaseModel):
    """Bidirectional heartbeat ping."""

    type: Literal[MessageType.PING] = MessageType.PING
    timestamp: datetime = Field(default_factory=datetime.now)


class PongMessage(BaseModel):
    """Bidirectional heartbeat pong."""

    type: Literal[MessageType.PONG] = MessageType.PONG
    timestamp: datetime = Field(default_factory=datetime.now)


# ============================================================================
# Message Request/Response
# ============================================================================


class MessageRequest(BaseModel):
    """Adapter -> Gateway: Process a user message."""

    type: Literal[MessageType.MESSAGE] = MessageType.MESSAGE
    id: str = Field(..., description="Unique message ID for tracking")
    user: UserInfo = Field(..., description="User information")
    channel: ChannelInfo = Field(..., description="Channel information")
    content: str = Field(..., description="Message text content")
    attachments: list[AttachmentInfo] = Field(
        default_factory=list,
        description="Message attachments (images, files)",
    )
    reply_chain: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Previous messages in the reply chain",
    )
    tier_override: str | None = Field(None, description="Model tier override (high/mid/low)")
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Platform-specific metadata",
    )


class ResponseStart(BaseModel):
    """Gateway -> Adapter: Response generation started."""

    type: Literal[MessageType.RESPONSE_START] = MessageType.RESPONSE_START
    id: str = Field(..., description="Response ID (correlates with message ID)")
    request_id: str = Field(..., description="Original request message ID")
    model_tier: str | None = Field(None, description="Model tier being used")


class ResponseChunk(BaseModel):
    """Gateway -> Adapter: Streaming response chunk."""

    type: Literal[MessageType.RESPONSE_CHUNK] = MessageType.RESPONSE_CHUNK
    id: str = Field(..., description="Response ID")
    request_id: str = Field(..., description="Original request message ID")
    chunk: str = Field(..., description="Text chunk")
    accumulated: str | None = Field(None, description="Full accumulated text so far")


class ButtonInfo(BaseModel):
    """Information about an interactive button."""

    label: str = Field(..., description="Button display text")
    style: Literal["primary", "secondary", "success", "danger"] = Field("secondary", description="Button style/color")
    action: Literal["dismiss", "confirm"] = Field(
        "dismiss", description="Button action: dismiss removes buttons, confirm updates message"
    )
    disabled: bool = Field(False, description="Whether button is disabled")


class FileData(BaseModel):
    """File data for sending as attachment over WebSocket."""

    filename: str = Field(..., description="Original filename")
    content_base64: str = Field(..., description="Base64-encoded file content")
    media_type: str = Field("application/octet-stream", description="MIME type")


class ResponseEnd(BaseModel):
    """Gateway -> Adapter: Response generation complete."""

    type: Literal[MessageType.RESPONSE_END] = MessageType.RESPONSE_END
    id: str = Field(..., description="Response ID")
    request_id: str = Field(..., description="Original request message ID")
    full_text: str = Field(..., description="Complete response text")
    files: list[str] = Field(default_factory=list, description="File paths (deprecated, use file_data)")
    file_data: list[FileData] = Field(default_factory=list, description="Files with content to attach")
    tool_count: int = Field(0, description="Number of tools executed")
    tokens_used: int | None = Field(None, description="Tokens used if available")
    edit_target: str | None = Field(
        None, description="'last' to edit last sent message, 'status' to edit status message"
    )
    components: list[ButtonInfo] = Field(default_factory=list, description="Interactive button components to add")


# ============================================================================
# Tool Execution Messages
# ============================================================================


class ToolStart(BaseModel):
    """Gateway -> Adapter: Tool execution started."""

    type: Literal[MessageType.TOOL_START] = MessageType.TOOL_START
    id: str = Field(..., description="Response ID")
    request_id: str = Field(..., description="Original request message ID")
    tool_name: str = Field(..., description="Name of the tool being executed")
    step: int = Field(..., description="Tool step number (1-indexed)")
    description: str | None = Field(None, description="Human-readable description")
    emoji: str = Field("⚙️", description="Emoji for this tool")


class ToolResult(BaseModel):
    """Gateway -> Adapter: Tool execution completed."""

    type: Literal[MessageType.TOOL_RESULT] = MessageType.TOOL_RESULT
    id: str = Field(..., description="Response ID")
    request_id: str = Field(..., description="Original request message ID")
    tool_name: str = Field(..., description="Name of the tool")
    success: bool = Field(..., description="Whether execution succeeded")
    output_preview: str | None = Field(None, description="Truncated output preview")
    duration_ms: int | None = Field(None, description="Execution time in ms")


# ============================================================================
# Control Messages
# ============================================================================


class CancelMessage(BaseModel):
    """Adapter -> Gateway: Cancel in-flight request."""

    type: Literal[MessageType.CANCEL] = MessageType.CANCEL
    request_id: str = Field(..., description="ID of request to cancel")
    reason: str | None = Field(None, description="Reason for cancellation")


class CancelledMessage(BaseModel):
    """Gateway -> Adapter: Request was cancelled."""

    type: Literal[MessageType.CANCELLED] = MessageType.CANCELLED
    request_id: str = Field(..., description="ID of cancelled request")


class ErrorMessage(BaseModel):
    """Gateway -> Adapter: Error occurred."""

    type: Literal[MessageType.ERROR] = MessageType.ERROR
    request_id: str | None = Field(None, description="Related request ID if applicable")
    code: str = Field(..., description="Error code")
    message: str = Field(..., description="Error message")
    recoverable: bool = Field(True, description="Whether client can retry")


class StatusMessage(BaseModel):
    """Bidirectional status information."""

    type: Literal[MessageType.STATUS] = MessageType.STATUS
    node_id: str | None = Field(None, description="Node ID if from adapter")
    active_requests: int = Field(0, description="Number of requests being processed")
    queue_length: int = Field(0, description="Number of queued requests")
    uptime_seconds: int | None = Field(None, description="Gateway uptime")


# ============================================================================
# Proactive Messages (ORS)
# ============================================================================


class ProactiveMessage(BaseModel):
    """Gateway -> Adapter: Proactive message from ORS."""

    type: Literal[MessageType.PROACTIVE_MESSAGE] = MessageType.PROACTIVE_MESSAGE
    user: UserInfo = Field(..., description="Target user")
    channel: ChannelInfo = Field(..., description="Target channel")
    content: str = Field(..., description="Message content")
    priority: str = Field("normal", description="Priority level (low, normal, high)")


# ============================================================================
# MCP Management Messages
# ============================================================================


class MCPServerInfo(BaseModel):
    """Information about an MCP server."""

    name: str = Field(..., description="Server name")
    status: str = Field(..., description="Server status (running, stopped, error)")
    enabled: bool = Field(True, description="Whether server is enabled")
    connected: bool = Field(False, description="Whether server is connected")
    tool_count: int = Field(0, description="Number of tools available")
    source_type: str = Field("unknown", description="Source type (npm, smithery, github, etc.)")
    transport: str | None = Field(None, description="Transport type (stdio, http)")
    tools: list[str] = Field(default_factory=list, description="List of tool names")
    last_error: str | None = Field(None, description="Last error message if any")


class MCPListRequest(BaseModel):
    """Adapter -> Gateway: List all MCP servers."""

    type: Literal[MessageType.MCP_LIST] = MessageType.MCP_LIST
    request_id: str = Field(..., description="Request ID for correlation")


class MCPListResponse(BaseModel):
    """Gateway -> Adapter: List of MCP servers."""

    type: Literal[MessageType.MCP_LIST_RESPONSE] = MessageType.MCP_LIST_RESPONSE
    request_id: str = Field(..., description="Request ID for correlation")
    success: bool = Field(True, description="Whether request succeeded")
    servers: list[MCPServerInfo] = Field(default_factory=list, description="List of servers")
    error: str | None = Field(None, description="Error message if failed")


class MCPInstallRequest(BaseModel):
    """Adapter -> Gateway: Install an MCP server."""

    type: Literal[MessageType.MCP_INSTALL] = MessageType.MCP_INSTALL
    request_id: str = Field(..., description="Request ID for correlation")
    source: str = Field(..., description="Server source (npm package, smithery:name, github URL)")
    name: str | None = Field(None, description="Custom name for the server")
    requested_by: str | None = Field(None, description="User ID who requested installation")


class MCPInstallResponse(BaseModel):
    """Gateway -> Adapter: Installation result."""

    type: Literal[MessageType.MCP_INSTALL_RESPONSE] = MessageType.MCP_INSTALL_RESPONSE
    request_id: str = Field(..., description="Request ID for correlation")
    success: bool = Field(..., description="Whether installation succeeded")
    server_name: str | None = Field(None, description="Name of installed server")
    tools_discovered: int = Field(0, description="Number of tools discovered")
    error: str | None = Field(None, description="Error message if failed")


class MCPUninstallRequest(BaseModel):
    """Adapter -> Gateway: Uninstall an MCP server."""

    type: Literal[MessageType.MCP_UNINSTALL] = MessageType.MCP_UNINSTALL
    request_id: str = Field(..., description="Request ID for correlation")
    server_name: str = Field(..., description="Name of server to uninstall")


class MCPUninstallResponse(BaseModel):
    """Gateway -> Adapter: Uninstall result."""

    type: Literal[MessageType.MCP_UNINSTALL_RESPONSE] = MessageType.MCP_UNINSTALL_RESPONSE
    request_id: str = Field(..., description="Request ID for correlation")
    success: bool = Field(..., description="Whether uninstall succeeded")
    error: str | None = Field(None, description="Error message if failed")


class MCPStatusRequest(BaseModel):
    """Adapter -> Gateway: Get status of an MCP server."""

    type: Literal[MessageType.MCP_STATUS] = MessageType.MCP_STATUS
    request_id: str = Field(..., description="Request ID for correlation")
    server_name: str | None = Field(None, description="Server name (None for overall status)")


class MCPStatusResponse(BaseModel):
    """Gateway -> Adapter: Server status."""

    type: Literal[MessageType.MCP_STATUS_RESPONSE] = MessageType.MCP_STATUS_RESPONSE
    request_id: str = Field(..., description="Request ID for correlation")
    success: bool = Field(True, description="Whether request succeeded")
    server: MCPServerInfo | None = Field(None, description="Server info if specific server requested")
    total_servers: int = Field(0, description="Total number of servers")
    connected_servers: int = Field(0, description="Number of connected servers")
    enabled_servers: int = Field(0, description="Number of enabled servers")
    error: str | None = Field(None, description="Error message if failed")


class MCPRestartRequest(BaseModel):
    """Adapter -> Gateway: Restart an MCP server."""

    type: Literal[MessageType.MCP_RESTART] = MessageType.MCP_RESTART
    request_id: str = Field(..., description="Request ID for correlation")
    server_name: str = Field(..., description="Name of server to restart")


class MCPRestartResponse(BaseModel):
    """Gateway -> Adapter: Restart result."""

    type: Literal[MessageType.MCP_RESTART_RESPONSE] = MessageType.MCP_RESTART_RESPONSE
    request_id: str = Field(..., description="Request ID for correlation")
    success: bool = Field(..., description="Whether restart succeeded")
    error: str | None = Field(None, description="Error message if failed")


class MCPEnableRequest(BaseModel):
    """Adapter -> Gateway: Enable or disable an MCP server."""

    type: Literal[MessageType.MCP_ENABLE] = MessageType.MCP_ENABLE
    request_id: str = Field(..., description="Request ID for correlation")
    server_name: str = Field(..., description="Name of server to enable/disable")
    enabled: bool = Field(..., description="True to enable, False to disable")


class MCPEnableResponse(BaseModel):
    """Gateway -> Adapter: Enable/disable result."""

    type: Literal[MessageType.MCP_ENABLE_RESPONSE] = MessageType.MCP_ENABLE_RESPONSE
    request_id: str = Field(..., description="Request ID for correlation")
    success: bool = Field(..., description="Whether operation succeeded")
    enabled: bool = Field(..., description="Current enabled state")
    error: str | None = Field(None, description="Error message if failed")


# ============================================================================
# Union Types for Parsing
# ============================================================================

# All message types that can be sent adapter -> gateway
AdapterMessage = (
    RegisterMessage
    | PingMessage
    | MessageRequest
    | CancelMessage
    | StatusMessage
    | MCPListRequest
    | MCPInstallRequest
    | MCPUninstallRequest
    | MCPStatusRequest
    | MCPRestartRequest
    | MCPEnableRequest
)

# All message types that can be sent gateway -> adapter
GatewayMessage = (
    RegisteredMessage
    | PongMessage
    | ResponseStart
    | ResponseChunk
    | ResponseEnd
    | ToolStart
    | ToolResult
    | CancelledMessage
    | ErrorMessage
    | StatusMessage
    | ProactiveMessage
    | MCPListResponse
    | MCPInstallResponse
    | MCPUninstallResponse
    | MCPStatusResponse
    | MCPRestartResponse
    | MCPEnableResponse
)


def parse_adapter_message(data: dict[str, Any]) -> AdapterMessage:
    """Parse a message from an adapter based on its type field.

    Args:
        data: Dict parsed from JSON

    Returns:
        Appropriate message model

    Raises:
        ValueError: If message type is unknown or invalid
    """
    msg_type = data.get("type")
    if not msg_type:
        raise ValueError("Message missing 'type' field")

    parsers = {
        MessageType.REGISTER: RegisterMessage,
        MessageType.PING: PingMessage,
        MessageType.MESSAGE: MessageRequest,
        MessageType.CANCEL: CancelMessage,
        MessageType.STATUS: StatusMessage,
        # MCP Management
        MessageType.MCP_LIST: MCPListRequest,
        MessageType.MCP_INSTALL: MCPInstallRequest,
        MessageType.MCP_UNINSTALL: MCPUninstallRequest,
        MessageType.MCP_STATUS: MCPStatusRequest,
        MessageType.MCP_RESTART: MCPRestartRequest,
        MessageType.MCP_ENABLE: MCPEnableRequest,
    }

    parser = parsers.get(msg_type)
    if not parser:
        raise ValueError(f"Unknown message type from adapter: {msg_type}")

    return parser.model_validate(data)


def parse_gateway_message(data: dict[str, Any]) -> GatewayMessage:
    """Parse a message from the gateway based on its type field.

    Args:
        data: Dict parsed from JSON

    Returns:
        Appropriate message model

    Raises:
        ValueError: If message type is unknown or invalid
    """
    msg_type = data.get("type")
    if not msg_type:
        raise ValueError("Message missing 'type' field")

    parsers = {
        MessageType.REGISTERED: RegisteredMessage,
        MessageType.PONG: PongMessage,
        MessageType.RESPONSE_START: ResponseStart,
        MessageType.RESPONSE_CHUNK: ResponseChunk,
        MessageType.RESPONSE_END: ResponseEnd,
        MessageType.TOOL_START: ToolStart,
        MessageType.TOOL_RESULT: ToolResult,
        MessageType.CANCELLED: CancelledMessage,
        MessageType.ERROR: ErrorMessage,
        MessageType.STATUS: StatusMessage,
        MessageType.PROACTIVE_MESSAGE: ProactiveMessage,
        # MCP Management Responses
        MessageType.MCP_LIST_RESPONSE: MCPListResponse,
        MessageType.MCP_INSTALL_RESPONSE: MCPInstallResponse,
        MessageType.MCP_UNINSTALL_RESPONSE: MCPUninstallResponse,
        MessageType.MCP_STATUS_RESPONSE: MCPStatusResponse,
        MessageType.MCP_RESTART_RESPONSE: MCPRestartResponse,
        MessageType.MCP_ENABLE_RESPONSE: MCPEnableResponse,
    }

    parser = parsers.get(msg_type)
    if not parser:
        raise ValueError(f"Unknown message type from gateway: {msg_type}")

    return parser.model_validate(data)
