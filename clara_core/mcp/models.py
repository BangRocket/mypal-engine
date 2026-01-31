"""MCP server configuration models with JSON file storage.

This module defines dataclass models for MCP server configuration,
stored as JSON files in the MCP_SERVERS_DIR directory.

New Structure:
    .mcp_servers/
        local/                   # Local MCP servers (stdio transport)
            rustterm/
                config.json
            clara-mcp/
                config.json
            {user-installed}/
                config.json
        remote/                  # Remote MCP servers (HTTP transport)
            {server_name}/
                config.json      # Standard MCP config format
        .oauth/                  # OAuth tokens
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any
from uuid import uuid4

logger = logging.getLogger(__name__)

# Default directory for MCP server files and configs
MCP_SERVERS_DIR = Path(os.getenv("MCP_SERVERS_DIR", ".mcp_servers"))


def gen_uuid() -> str:
    """Generate a UUID string."""
    return str(uuid4())


def utcnow_iso() -> str:
    """Return current UTC time as ISO string."""
    return datetime.now(timezone.utc).isoformat()


class ServerType(str, Enum):
    """Type of MCP server."""

    LOCAL = "local"
    REMOTE = "remote"


class ServerStatus(str, Enum):
    """Server connection status."""

    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    ERROR = "error"
    PENDING_AUTH = "pending_auth"


@dataclass
class LocalServerConfig:
    """Configuration for a local MCP server (stdio transport).

    Local servers run as subprocesses and communicate via stdio.
    They include:
    - Built-in servers: rustterm, clara-mcp
    - User-installed servers from npm, GitHub, local paths
    """

    name: str
    command: str  # Command to run (e.g., "npx", "python", "node", binary path)
    args: list[str] = field(default_factory=list)  # Command arguments

    # Optional fields
    id: str = field(default_factory=gen_uuid)
    display_name: str | None = None
    cwd: str | None = None  # Working directory
    env: dict[str, str] = field(default_factory=dict)  # Environment variables

    # Source tracking
    source_type: str = "local"  # "local", "npm", "github", "smithery"
    source_url: str | None = None

    # Auto-start and hot reload
    enabled: bool = True
    auto_start: bool = True
    hot_reload: bool = False  # Watch for file changes and restart

    # Status tracking
    status: str = "stopped"
    last_error: str | None = None
    last_error_at: str | None = None
    pid: int | None = None  # Process ID when running

    # Cached tool info
    tool_count: int = 0
    tools: list[dict[str, Any]] = field(default_factory=list)

    # Metadata
    installed_by: str | None = None
    created_at: str = field(default_factory=utcnow_iso)
    updated_at: str = field(default_factory=utcnow_iso)

    def get_args(self) -> list[str]:
        """Get command arguments as a list."""
        return self.args or []

    def set_args(self, args: list[str]) -> None:
        """Set command arguments from a list."""
        self.args = args

    def get_env(self) -> dict[str, str]:
        """Get environment variables as a dict."""
        return self.env or {}

    def set_env(self, env: dict[str, str]) -> None:
        """Set environment variables from a dict."""
        self.env = env

    def get_tools(self) -> list[dict[str, Any]]:
        """Get cached tools as a list of tool definitions."""
        return self.tools or []

    def set_tools(self, tools: list[dict[str, Any]]) -> None:
        """Set cached tools from a list of tool definitions."""
        self.tools = tools
        self.tool_count = len(tools)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LocalServerConfig:
        """Create instance from dictionary."""
        # Handle legacy field names
        if "tools_json" in data and "tools" not in data:
            try:
                data["tools"] = json.loads(data.pop("tools_json"))
            except (json.JSONDecodeError, TypeError):
                data["tools"] = []
                data.pop("tools_json", None)

        # Handle legacy args as JSON string
        if isinstance(data.get("args"), str):
            try:
                data["args"] = json.loads(data["args"])
            except (json.JSONDecodeError, TypeError):
                data["args"] = []

        # Handle legacy env as JSON string
        if isinstance(data.get("env"), str):
            try:
                data["env"] = json.loads(data["env"])
            except (json.JSONDecodeError, TypeError):
                data["env"] = {}

        # Filter to only known fields
        known_fields = {f.name for f in cls.__dataclass_fields__.values()}
        filtered_data = {k: v for k, v in data.items() if k in known_fields}

        return cls(**filtered_data)

    def __repr__(self) -> str:
        return f"<LocalServerConfig name={self.name} cmd={self.command} status={self.status}>"


@dataclass
class RemoteServerConfig:
    """Configuration for a remote MCP server (HTTP/SSE transport).

    Remote servers connect via HTTP transport to external endpoints.
    Config follows the standard MCP format:

    {
        "mcpServers": {
            "server_name": {
                "serverUrl": "https://example.com/mcp",
                "headers": {
                    "Authorization": "Bearer token",
                    "X-Custom-Header": "value"
                }
            }
        }
    }
    """

    name: str
    server_url: str  # MCP endpoint URL
    headers: dict[str, str] = field(default_factory=dict)  # Custom headers

    # Optional fields
    id: str = field(default_factory=gen_uuid)
    display_name: str | None = None

    # Transport configuration
    transport: str = "streamable-http"  # "sse" or "streamable-http"

    # Source tracking
    source_type: str = "remote"  # "remote", "smithery-hosted"
    source_url: str | None = None

    # Connection settings
    enabled: bool = True
    timeout: int = 30  # Connection timeout in seconds
    retry_count: int = 3  # Number of retries on failure

    # OAuth settings (for Smithery-hosted)
    oauth_required: bool = False
    oauth_server_url: str | None = None

    # Status tracking
    status: str = "stopped"
    last_error: str | None = None
    last_error_at: str | None = None

    # Cached tool info
    tool_count: int = 0
    tools: list[dict[str, Any]] = field(default_factory=list)

    # Metadata
    installed_by: str | None = None
    created_at: str = field(default_factory=utcnow_iso)
    updated_at: str = field(default_factory=utcnow_iso)

    def get_tools(self) -> list[dict[str, Any]]:
        """Get cached tools as a list of tool definitions."""
        return self.tools or []

    def set_tools(self, tools: list[dict[str, Any]]) -> None:
        """Set cached tools from a list of tool definitions."""
        self.tools = tools
        self.tool_count = len(tools)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)

    def to_standard_format(self) -> dict[str, Any]:
        """Convert to standard MCP config format.

        Returns:
            Dict in standard format: {"mcpServers": {"name": {"serverUrl": ..., "headers": ...}}}
        """
        return {
            "mcpServers": {
                self.name: {
                    "serverUrl": self.server_url,
                    "headers": self.headers,
                }
            }
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RemoteServerConfig:
        """Create instance from dictionary."""
        # Handle legacy or malformed configs - ensure required fields
        if "name" not in data:
            raise ValueError("RemoteServerConfig requires 'name' field")

        # server_url is required - check various field names
        if "server_url" not in data:
            # Try legacy field names
            if "serverUrl" in data:
                data["server_url"] = data.pop("serverUrl")
            elif "endpoint_url" in data:
                data["server_url"] = data.pop("endpoint_url")
            elif "url" in data:
                data["server_url"] = data.pop("url")
            else:
                raise ValueError(f"RemoteServerConfig '{data['name']}' requires 'server_url' field")

        # Filter to only known fields
        known_fields = {f.name for f in cls.__dataclass_fields__.values()}
        filtered_data = {k: v for k, v in data.items() if k in known_fields}
        return cls(**filtered_data)

    @classmethod
    def from_standard_format(cls, name: str, data: dict[str, Any]) -> RemoteServerConfig:
        """Create from standard MCP config format.

        Args:
            name: Server name
            data: Dict with serverUrl, headers (optional)

        Returns:
            RemoteServerConfig instance
        """
        return cls(
            name=name,
            server_url=data.get("serverUrl", ""),
            headers=data.get("headers", {}),
        )

    def __repr__(self) -> str:
        return f"<RemoteServerConfig name={self.name} url={self.server_url} status={self.status}>"


# Type alias for either config type
ServerConfig = LocalServerConfig | RemoteServerConfig


# --- Directory Structure Helpers ---


def get_local_servers_dir() -> Path:
    """Get the directory for local server configs."""
    return MCP_SERVERS_DIR / "local"


def get_remote_servers_dir() -> Path:
    """Get the directory for remote server configs."""
    return MCP_SERVERS_DIR / "remote"


def get_oauth_dir() -> Path:
    """Get the directory for OAuth tokens."""
    return MCP_SERVERS_DIR / ".oauth"


def get_local_server_dir(server_name: str) -> Path:
    """Get the directory for a local server's files and config."""
    return get_local_servers_dir() / server_name


def get_remote_server_dir(server_name: str) -> Path:
    """Get the directory for a remote server's config."""
    return get_remote_servers_dir() / server_name


def get_local_config_path(server_name: str) -> Path:
    """Get the path to a local server's config.json file."""
    return get_local_server_dir(server_name) / "config.json"


def get_remote_config_path(server_name: str) -> Path:
    """Get the path to a remote server's config.json file."""
    return get_remote_server_dir(server_name) / "config.json"


# --- Local Server Config Functions ---


def load_local_server_config(server_name: str) -> LocalServerConfig | None:
    """Load a local server configuration from its JSON file.

    Args:
        server_name: Name of the server

    Returns:
        LocalServerConfig if found, None otherwise
    """
    config_path = get_local_config_path(server_name)
    if not config_path.exists():
        return None

    try:
        with open(config_path) as f:
            data = json.load(f)
        return LocalServerConfig.from_dict(data)
    except (json.JSONDecodeError, OSError) as e:
        logger.error(f"[MCP] Failed to load local config for {server_name}: {e}")
        return None


def save_local_server_config(config: LocalServerConfig) -> bool:
    """Save a local server configuration to its JSON file.

    Args:
        config: The server configuration to save

    Returns:
        True if successful, False otherwise
    """
    server_dir = get_local_server_dir(config.name)
    config_path = get_local_config_path(config.name)

    try:
        server_dir.mkdir(parents=True, exist_ok=True)
        config.updated_at = utcnow_iso()

        with open(config_path, "w") as f:
            json.dump(config.to_dict(), f, indent=2)

        return True
    except OSError as e:
        logger.error(f"[MCP] Failed to save local config for {config.name}: {e}")
        return False


def delete_local_server_config(server_name: str) -> bool:
    """Delete a local server's configuration file.

    Args:
        server_name: Name of the server

    Returns:
        True if deleted or didn't exist, False on error
    """
    config_path = get_local_config_path(server_name)

    try:
        if config_path.exists():
            config_path.unlink()
        return True
    except OSError as e:
        logger.error(f"[MCP] Failed to delete local config for {server_name}: {e}")
        return False


def list_local_server_configs() -> list[LocalServerConfig]:
    """List all local server configurations.

    Returns:
        List of all LocalServerConfig objects
    """
    configs = []
    local_dir = get_local_servers_dir()

    if not local_dir.exists():
        return configs

    for server_dir in local_dir.iterdir():
        if not server_dir.is_dir():
            continue

        try:
            config = load_local_server_config(server_dir.name)
            if config:
                configs.append(config)
        except Exception as e:
            logger.warning(f"[MCP] Skipping invalid local config '{server_dir.name}': {e}")
            continue

    return configs


# --- Remote Server Config Functions ---


def load_remote_server_config(server_name: str) -> RemoteServerConfig | None:
    """Load a remote server configuration from its JSON file.

    Handles both the new internal format and the standard MCP format.

    Args:
        server_name: Name of the server

    Returns:
        RemoteServerConfig if found, None otherwise
    """
    config_path = get_remote_config_path(server_name)
    if not config_path.exists():
        return None

    try:
        with open(config_path) as f:
            data = json.load(f)

        # Check if it's in standard MCP format
        if "mcpServers" in data:
            server_data = data["mcpServers"].get(server_name, {})
            if not server_data:
                return None

            # Create base config from standard format
            config = RemoteServerConfig.from_standard_format(server_name, server_data)

            # Merge in _metadata if present (from our save format)
            if "_metadata" in data:
                metadata = data["_metadata"]
                # Update config with metadata fields
                if metadata.get("id"):
                    config.id = metadata["id"]
                if metadata.get("display_name"):
                    config.display_name = metadata["display_name"]
                if metadata.get("transport"):
                    config.transport = metadata["transport"]
                if metadata.get("source_type"):
                    config.source_type = metadata["source_type"]
                if metadata.get("source_url"):
                    config.source_url = metadata["source_url"]
                if "enabled" in metadata:
                    config.enabled = metadata["enabled"]
                if metadata.get("timeout"):
                    config.timeout = metadata["timeout"]
                if metadata.get("retry_count"):
                    config.retry_count = metadata["retry_count"]
                if "oauth_required" in metadata:
                    config.oauth_required = metadata["oauth_required"]
                if metadata.get("oauth_server_url"):
                    config.oauth_server_url = metadata["oauth_server_url"]
                if metadata.get("status"):
                    config.status = metadata["status"]
                if metadata.get("last_error"):
                    config.last_error = metadata["last_error"]
                if metadata.get("last_error_at"):
                    config.last_error_at = metadata["last_error_at"]
                if metadata.get("tool_count"):
                    config.tool_count = metadata["tool_count"]
                if metadata.get("tools"):
                    config.tools = metadata["tools"]
                if metadata.get("installed_by"):
                    config.installed_by = metadata["installed_by"]
                if metadata.get("created_at"):
                    config.created_at = metadata["created_at"]
                if metadata.get("updated_at"):
                    config.updated_at = metadata["updated_at"]

            return config

        # Otherwise it's our internal format
        return RemoteServerConfig.from_dict(data)

    except (json.JSONDecodeError, OSError, ValueError) as e:
        logger.error(f"[MCP] Failed to load remote config for {server_name}: {e}")
        return None


def save_remote_server_config(config: RemoteServerConfig, use_standard_format: bool = True) -> bool:
    """Save a remote server configuration to its JSON file.

    Args:
        config: The server configuration to save
        use_standard_format: If True, save in standard MCP format

    Returns:
        True if successful, False otherwise
    """
    server_dir = get_remote_server_dir(config.name)
    config_path = get_remote_config_path(config.name)

    try:
        server_dir.mkdir(parents=True, exist_ok=True)
        config.updated_at = utcnow_iso()

        if use_standard_format:
            # Save in standard MCP format with extra metadata
            output = config.to_standard_format()
            # Add metadata that's not in standard format
            output["_metadata"] = {
                "id": config.id,
                "display_name": config.display_name,
                "transport": config.transport,
                "source_type": config.source_type,
                "source_url": config.source_url,
                "enabled": config.enabled,
                "timeout": config.timeout,
                "retry_count": config.retry_count,
                "oauth_required": config.oauth_required,
                "oauth_server_url": config.oauth_server_url,
                "status": config.status,
                "last_error": config.last_error,
                "last_error_at": config.last_error_at,
                "tool_count": config.tool_count,
                "tools": config.tools,
                "installed_by": config.installed_by,
                "created_at": config.created_at,
                "updated_at": config.updated_at,
            }
        else:
            output = config.to_dict()

        with open(config_path, "w") as f:
            json.dump(output, f, indent=2)

        return True
    except OSError as e:
        logger.error(f"[MCP] Failed to save remote config for {config.name}: {e}")
        return False


def delete_remote_server_config(server_name: str) -> bool:
    """Delete a remote server's configuration file.

    Args:
        server_name: Name of the server

    Returns:
        True if deleted or didn't exist, False on error
    """
    config_path = get_remote_config_path(server_name)

    try:
        if config_path.exists():
            config_path.unlink()
        return True
    except OSError as e:
        logger.error(f"[MCP] Failed to delete remote config for {server_name}: {e}")
        return False


def list_remote_server_configs() -> list[RemoteServerConfig]:
    """List all remote server configurations.

    Returns:
        List of all RemoteServerConfig objects
    """
    configs = []
    remote_dir = get_remote_servers_dir()

    if not remote_dir.exists():
        return configs

    for server_dir in remote_dir.iterdir():
        if not server_dir.is_dir():
            continue

        try:
            config = load_remote_server_config(server_dir.name)
            if config:
                configs.append(config)
        except Exception as e:
            logger.warning(f"[MCP] Skipping invalid remote config '{server_dir.name}': {e}")
            continue

    return configs


# --- Combined Functions ---


def list_all_server_configs() -> list[ServerConfig]:
    """List all server configurations (local and remote).

    Returns:
        List of all server configs
    """
    return list_local_server_configs() + list_remote_server_configs()


def get_enabled_local_servers() -> list[LocalServerConfig]:
    """Get all enabled local server configurations.

    Returns:
        List of enabled LocalServerConfig objects
    """
    return [c for c in list_local_server_configs() if c.enabled]


def get_enabled_remote_servers() -> list[RemoteServerConfig]:
    """Get all enabled remote server configurations.

    Returns:
        List of enabled RemoteServerConfig objects
    """
    return [c for c in list_remote_server_configs() if c.enabled]


def get_enabled_servers() -> list[ServerConfig]:
    """Get all enabled server configurations.

    Returns:
        List of enabled server configs
    """
    return get_enabled_local_servers() + get_enabled_remote_servers()


def find_server_config(server_name: str) -> tuple[ServerType, ServerConfig] | None:
    """Find a server config by name.

    Args:
        server_name: Name of the server

    Returns:
        Tuple of (server_type, config) or None if not found
    """
    # Check local first
    local_config = load_local_server_config(server_name)
    if local_config:
        return (ServerType.LOCAL, local_config)

    # Check remote
    remote_config = load_remote_server_config(server_name)
    if remote_config:
        return (ServerType.REMOTE, remote_config)

    return None


# --- Migration Helpers ---


def migrate_legacy_config(legacy_path: Path) -> bool:
    """Migrate a legacy config from old .mcp_servers/{name}/config.json format.

    Args:
        legacy_path: Path to legacy config.json

    Returns:
        True if migration successful
    """
    try:
        with open(legacy_path) as f:
            data = json.load(f)

        server_name = data.get("name")
        if not server_name:
            return False

        transport = data.get("transport", "stdio")

        if transport == "stdio":
            # Migrate to local config
            config = LocalServerConfig.from_dict(data)
            return save_local_server_config(config)
        else:
            # Migrate to remote config
            config = RemoteServerConfig(
                name=server_name,
                server_url=data.get("endpoint_url", ""),
                headers={},
                display_name=data.get("display_name"),
                transport=transport,
                source_type=data.get("source_type", "remote"),
                source_url=data.get("source_url"),
                enabled=data.get("enabled", True),
                status=data.get("status", "stopped"),
                last_error=data.get("last_error"),
                tool_count=data.get("tool_count", 0),
                tools=data.get("tools", []),
                installed_by=data.get("installed_by"),
                created_at=data.get("created_at", utcnow_iso()),
            )
            return save_remote_server_config(config)

    except Exception as e:
        logger.error(f"[MCP] Migration failed for {legacy_path}: {e}")
        return False


# --- Legacy Compatibility ---

# Keep old class names as aliases for backwards compatibility
MCPServerConfig = LocalServerConfig
MCPServer = LocalServerConfig


# Legacy functions that work with the old flat structure
def get_server_dir(server_name: str) -> Path:
    """Get the directory for a server's files and config (legacy)."""
    # First check if it exists in the new structure
    local_dir = get_local_server_dir(server_name)
    if local_dir.exists():
        return local_dir

    remote_dir = get_remote_server_dir(server_name)
    if remote_dir.exists():
        return remote_dir

    # Default to legacy location
    return MCP_SERVERS_DIR / server_name


def get_config_path(server_name: str) -> Path:
    """Get the path to a server's config.json file (legacy)."""
    return get_server_dir(server_name) / "config.json"


def load_server_config(server_name: str) -> LocalServerConfig | None:
    """Load a server configuration (legacy, assumes local).

    For new code, use load_local_server_config or load_remote_server_config.
    """
    # Try new locations first
    config = load_local_server_config(server_name)
    if config:
        return config

    # Try legacy location
    legacy_path = MCP_SERVERS_DIR / server_name / "config.json"
    if legacy_path.exists():
        try:
            with open(legacy_path) as f:
                data = json.load(f)
            return LocalServerConfig.from_dict(data)
        except Exception:
            pass

    return None


def save_server_config(config: LocalServerConfig) -> bool:
    """Save a server configuration (legacy, saves to local).

    For new code, use save_local_server_config or save_remote_server_config.
    """
    return save_local_server_config(config)


def delete_server_config(server_name: str) -> bool:
    """Delete a server's configuration file (legacy).

    For new code, use delete_local_server_config or delete_remote_server_config.
    """
    # Try both locations
    deleted_local = delete_local_server_config(server_name)
    deleted_remote = delete_remote_server_config(server_name)
    return deleted_local or deleted_remote


def list_server_configs() -> list[LocalServerConfig]:
    """List all local server configurations (legacy).

    For new code, use list_local_server_configs, list_remote_server_configs,
    or list_all_server_configs.
    """
    return list_local_server_configs()
