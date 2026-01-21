"""MCP server configuration models with JSON file storage.

This module defines dataclass models for MCP server configuration,
stored as JSON files in the MCP_SERVERS_DIR directory.

Structure:
    .mcp_servers/
        {server_name}/
            config.json    # Server configuration
            ...            # Cloned repo files, built artifacts, etc.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
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


@dataclass
class MCPServerConfig:
    """Configuration and status for an MCP server.

    MCP servers can be installed from various sources:
    - GitHub repos: Clone, detect type, build/run
    - Docker images: Pull and run with exposed MCP endpoint
    - npm packages: npx @scope/mcp-server pattern
    - Local paths: Point to existing MCP server directories
    """

    name: str  # e.g., "weather", "filesystem"
    source_type: str  # "github", "docker", "npm", "local"

    # Optional fields with defaults
    id: str = field(default_factory=gen_uuid)
    display_name: str | None = None
    source_url: str | None = None

    # Transport configuration
    transport: str = "stdio"  # "stdio", "sse", "streamable-http"

    # Stdio transport config
    command: str | None = None  # Command to run (e.g., "npx", "python", "node")
    args: list[str] = field(default_factory=list)  # Arguments for the command
    cwd: str | None = None  # Working directory for the command

    # Environment variables
    env: dict[str, str] = field(default_factory=dict)

    # HTTP/SSE transport config
    endpoint_url: str | None = None

    # Docker-specific config
    docker_config: dict[str, Any] = field(default_factory=dict)

    # Server state
    enabled: bool = True
    status: str = "stopped"  # "running", "stopped", "error", "starting"
    last_error: str | None = None
    last_error_at: str | None = None

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

    def get_docker_config(self) -> dict[str, Any]:
        """Get Docker configuration as a dict."""
        return self.docker_config or {}

    def set_docker_config(self, config: dict[str, Any]) -> None:
        """Set Docker configuration from a dict."""
        self.docker_config = config

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
    def from_dict(cls, data: dict[str, Any]) -> MCPServerConfig:
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

        # Handle legacy docker_config as JSON string
        if isinstance(data.get("docker_config"), str):
            try:
                data["docker_config"] = json.loads(data["docker_config"])
            except (json.JSONDecodeError, TypeError):
                data["docker_config"] = {}

        # Filter to only known fields
        known_fields = {f.name for f in cls.__dataclass_fields__.values()}
        filtered_data = {k: v for k, v in data.items() if k in known_fields}

        return cls(**filtered_data)

    def __repr__(self) -> str:
        return f"<MCPServerConfig name={self.name} source={self.source_type} status={self.status}>"


def get_server_dir(server_name: str) -> Path:
    """Get the directory for a server's files and config."""
    return MCP_SERVERS_DIR / server_name


def get_config_path(server_name: str) -> Path:
    """Get the path to a server's config.json file."""
    return get_server_dir(server_name) / "config.json"


def load_server_config(server_name: str) -> MCPServerConfig | None:
    """Load a server configuration from its JSON file.

    Args:
        server_name: Name of the server

    Returns:
        MCPServerConfig if found, None otherwise
    """
    config_path = get_config_path(server_name)
    if not config_path.exists():
        return None

    try:
        with open(config_path) as f:
            data = json.load(f)
        return MCPServerConfig.from_dict(data)
    except (json.JSONDecodeError, OSError) as e:
        logger.error(f"[MCP] Failed to load config for {server_name}: {e}")
        return None


def save_server_config(config: MCPServerConfig) -> bool:
    """Save a server configuration to its JSON file.

    Args:
        config: The server configuration to save

    Returns:
        True if successful, False otherwise
    """
    server_dir = get_server_dir(config.name)
    config_path = get_config_path(config.name)

    try:
        # Ensure directory exists
        server_dir.mkdir(parents=True, exist_ok=True)

        # Update timestamp
        config.updated_at = utcnow_iso()

        # Write config
        with open(config_path, "w") as f:
            json.dump(config.to_dict(), f, indent=2)

        return True
    except OSError as e:
        logger.error(f"[MCP] Failed to save config for {config.name}: {e}")
        return False


def delete_server_config(server_name: str) -> bool:
    """Delete a server's configuration file.

    Note: This only deletes the config.json, not the server directory.
    Use shutil.rmtree(get_server_dir(server_name)) to remove everything.

    Args:
        server_name: Name of the server

    Returns:
        True if deleted or didn't exist, False on error
    """
    config_path = get_config_path(server_name)

    try:
        if config_path.exists():
            config_path.unlink()
        return True
    except OSError as e:
        logger.error(f"[MCP] Failed to delete config for {server_name}: {e}")
        return False


def list_server_configs() -> list[MCPServerConfig]:
    """List all server configurations.

    Returns:
        List of all MCPServerConfig objects found in MCP_SERVERS_DIR
    """
    configs = []

    if not MCP_SERVERS_DIR.exists():
        return configs

    for server_dir in MCP_SERVERS_DIR.iterdir():
        if not server_dir.is_dir():
            continue

        config = load_server_config(server_dir.name)
        if config:
            configs.append(config)

    return configs


def get_enabled_servers() -> list[MCPServerConfig]:
    """Get all enabled server configurations.

    Returns:
        List of enabled MCPServerConfig objects
    """
    return [c for c in list_server_configs() if c.enabled]


# Legacy compatibility: Keep MCPServer as alias
MCPServer = MCPServerConfig
