"""Database models for MCP server configurations.

This module defines SQLAlchemy models for storing MCP server configuration
and tracking their status.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text

from db.models import Base, gen_uuid


def utcnow():
    """Return current UTC time (naive, for SQLite compatibility)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class MCPServer(Base):
    """Configuration and status for an MCP server.

    MCP servers can be installed from various sources:
    - GitHub repos: Clone, detect type, build/run
    - Docker images: Pull and run with exposed MCP endpoint
    - npm packages: npx @scope/mcp-server pattern
    - Local paths: Point to existing MCP server directories
    """

    __tablename__ = "mcp_servers"

    id = Column(String, primary_key=True, default=gen_uuid)
    name = Column(String, nullable=False, unique=True, index=True)  # e.g., "weather", "filesystem"
    display_name = Column(String, nullable=True)  # Human-friendly name

    # Source configuration
    source_type = Column(String, nullable=False)  # "github", "docker", "npm", "local"
    source_url = Column(String, nullable=True)  # Repo URL, image name, npm package, or local path

    # Transport configuration
    transport = Column(String, nullable=False, default="stdio")  # "stdio", "sse", "streamable-http"

    # Stdio transport config
    command = Column(String, nullable=True)  # Command to run (e.g., "npx", "python", "node")
    args = Column(Text, nullable=True)  # JSON: Arguments for the command
    cwd = Column(String, nullable=True)  # Working directory for the command

    # Environment variables (JSON object)
    env = Column(Text, nullable=True)  # JSON: Environment variables to pass

    # HTTP/SSE transport config
    endpoint_url = Column(String, nullable=True)  # URL for HTTP/SSE transport

    # Docker-specific config (JSON)
    docker_config = Column(Text, nullable=True)  # JSON: {image, port, volumes, etc.}

    # Server state
    enabled = Column(Boolean, default=True)  # Active or disabled
    status = Column(String, default="stopped")  # "running", "stopped", "error", "starting"
    last_error = Column(Text, nullable=True)  # Most recent error message
    last_error_at = Column(DateTime, nullable=True)  # When the error occurred

    # Cached tool info
    tool_count = Column(Integer, default=0)  # Number of tools discovered
    tools_json = Column(Text, nullable=True)  # JSON: Cached tool definitions

    # Metadata
    installed_by = Column(String, nullable=True)  # User ID who installed this server
    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    def get_args(self) -> list[str]:
        """Get command arguments as a list."""
        if not self.args:
            return []
        try:
            return json.loads(self.args)
        except json.JSONDecodeError:
            return []

    def set_args(self, args: list[str]) -> None:
        """Set command arguments from a list."""
        self.args = json.dumps(args)

    def get_env(self) -> dict[str, str]:
        """Get environment variables as a dict."""
        if not self.env:
            return {}
        try:
            return json.loads(self.env)
        except json.JSONDecodeError:
            return {}

    def set_env(self, env: dict[str, str]) -> None:
        """Set environment variables from a dict."""
        self.env = json.dumps(env)

    def get_docker_config(self) -> dict[str, Any]:
        """Get Docker configuration as a dict."""
        if not self.docker_config:
            return {}
        try:
            return json.loads(self.docker_config)
        except json.JSONDecodeError:
            return {}

    def set_docker_config(self, config: dict[str, Any]) -> None:
        """Set Docker configuration from a dict."""
        self.docker_config = json.dumps(config)

    def get_tools(self) -> list[dict[str, Any]]:
        """Get cached tools as a list of tool definitions."""
        if not self.tools_json:
            return []
        try:
            return json.loads(self.tools_json)
        except json.JSONDecodeError:
            return []

    def set_tools(self, tools: list[dict[str, Any]]) -> None:
        """Set cached tools from a list of tool definitions."""
        self.tools_json = json.dumps(tools)
        self.tool_count = len(tools)

    def __repr__(self) -> str:
        return f"<MCPServer name={self.name} source={self.source_type} status={self.status}>"
