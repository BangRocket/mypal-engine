"""Core types for the Clara plugin system.

This module defines all dataclasses and type aliases used throughout the plugin system,
following the architecture of OpenClaw's plugin system.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import (
    TYPE_CHECKING,
    Any,
    Awaitable,
    Callable,
    TypeAlias,
)

if TYPE_CHECKING:
    from mypalclara.tools._base import ToolDef

    from .runtime import PluginRuntime


class PluginKind(Enum):
    """Plugin kind/type classification."""

    TOOLS = "tools"
    MCP = "mcp"
    MEMORY = "memory"
    CHANNEL = "channel"
    PROVIDER = "provider"
    SERVICE = "service"


class PluginOrigin(Enum):
    """Where the plugin was discovered from."""

    BUNDLED = "bundled"
    GLOBAL = "global"
    WORKSPACE = "workspace"
    CONFIG = "config"


class DiagnosticLevel(Enum):
    """Severity level for diagnostic messages."""

    WARN = "warn"
    ERROR = "error"


@dataclass
class Diagnostic:
    """Diagnostic message from plugin system."""

    level: DiagnosticLevel
    message: str
    plugin_id: str | None = None
    source: str | None = None


@dataclass
class PluginContext:
    """Context passed to tool factories.

    Provides runtime state to plugins when they create tools.
    """

    user_id: str | None = None
    session_key: str | None = None
    message_channel: str | None = None
    agent_id: str | None = None
    platform: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class PluginConfig:
    """Validated plugin-specific configuration."""

    enabled: bool
    raw: dict[str, Any]
    validated: dict[str, Any] | None


@dataclass
class PluginRecord:
    """Record of a loaded plugin in the registry."""

    id: str
    name: str
    version: str | None
    description: str | None
    kind: PluginKind | None
    origin: PluginOrigin
    source: str
    workspace_dir: Path | None
    enabled: bool
    status: str
    error: str | None

    # Track what the plugin registered
    tool_names: list[str] = field(default_factory=list)
    hook_names: list[str] = field(default_factory=list)
    channel_ids: list[str] = field(default_factory=list)
    provider_ids: list[str] = field(default_factory=list)
    service_ids: list[str] = field(default_factory=list)
    command_names: list[str] = field(default_factory=list)

    # Config
    config_schema: dict[str, Any] | None = None
    config_ui_hints: dict[str, Any] | None = None


@dataclass
class PluginManifest:
    """Metadata from plugin manifest file.

    This is parsed from `clara.plugin.json` or equivalent.
    """

    id: str
    name: str | None = None
    version: str | None = None
    description: str | None = None
    kind: PluginKind | None = None
    config_schema: dict[str, Any] | None = None
    config_ui_hints: dict[str, Any] | None = None

    # Optional: plugin capabilities/features
    tools: list[str] | None = None
    hooks: list[str] | None = None
    channels: list[str] | None = None
    providers: list[str] | None = None


# Type aliases for handlers and factories
ToolHandler: TypeAlias = Callable[[dict[str, Any], PluginContext], Awaitable[str]]
ToolFactory: TypeAlias = Callable[[PluginContext], "ToolDef | list[ToolDef] | None"]
HookHandler: TypeAlias = Callable[..., Awaitable[Any] | Any]
PluginRegisterFunc: TypeAlias = Callable[["PluginAPI"], Any]


@dataclass
class PluginAPI:
    """API passed to plugin register() function.

    This is the main interface plugins use to register their
    tools, hooks, and other extensions.
    """

    # Plugin metadata
    id: str
    name: str
    version: str | None
    description: str | None
    source: str

    # Configuration
    config: dict[str, Any]  # Main app config
    plugin_config: dict[str, Any]  # Plugin-specific config

    # Runtime (PluginRuntime from runtime.py)
    runtime: PluginRuntime

    # Logging
    logger: logging.Logger

    # Registration methods
    register_tool: Callable[[ToolFactory | "ToolDef"], None]
    register_hook: Callable[[str | list[str], HookHandler], None]
    register_channel: Callable[[Any], None]
    register_provider: Callable[[Any], None]
    register_service: Callable[[Any], None]
    register_command: Callable[[Any], None]

    # Utility methods
    resolve_path: Callable[[str], Path]

    def debug(self, msg: str, *args: Any) -> None:
        """Convenience method for debug logging."""
        self.logger.debug(f"[{self.id}] {msg}", *args)

    def info(self, msg: str, *args: Any) -> None:
        """Convenience method for info logging."""
        self.logger.info(f"[{self.id}] {msg}", *args)

    def warn(self, msg: str, *args: Any) -> None:
        """Convenience method for warning logging."""
        self.logger.warning(f"[{self.id}] {msg}", *args)

    def error(self, msg: str, *args: Any) -> None:
        """Convenience method for error logging."""
        self.logger.error(f"[{self.id}] {msg}", *args)
