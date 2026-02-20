"""Local MCP server lifecycle management.

This module handles starting, stopping, and hot-reloading local MCP servers
that run as subprocesses and communicate via stdio.

Features:
- Start/stop individual servers
- Hot reload support (watch for file changes and restart)
- Process management with proper cleanup
- Tool discovery on connect
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from .client import LoggerPipe
from .models import (
    LocalServerConfig,
    get_local_server_dir,
    load_local_server_config,
    save_local_server_config,
    utcnow_iso,
)

if TYPE_CHECKING:
    from watchdog.observers import Observer

logger = logging.getLogger(__name__)


@dataclass
class MCPTool:
    """Represents a tool discovered from an MCP server."""

    name: str
    description: str
    input_schema: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        """Convert to a dictionary for JSON serialization."""
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
        }


@dataclass
class LocalServerState:
    """Tracks the runtime state of a local MCP server."""

    connected: bool = False
    tools: list[MCPTool] = field(default_factory=list)
    last_error: str | None = None
    reconnect_attempts: int = 0
    max_reconnect_attempts: int = 3


class LocalServerProcess:
    """Manages a single local MCP server process.

    Handles:
    - Starting the server process
    - Establishing MCP connection via stdio
    - Tool discovery
    - Graceful shutdown
    - Automatic reconnection on failure
    """

    def __init__(
        self,
        config: LocalServerConfig,
        on_tools_changed: Callable[[str, list[MCPTool]], None] | None = None,
    ) -> None:
        """Initialize the local server process manager.

        Args:
            config: Server configuration
            on_tools_changed: Callback when tools are discovered/changed
        """
        self.config = config
        self.state = LocalServerState()
        self._session: ClientSession | None = None
        self._exit_stack: AsyncExitStack | None = None
        self._lock = asyncio.Lock()
        self._on_tools_changed = on_tools_changed
        self._watcher_task: asyncio.Task | None = None
        self._observer: Observer | None = None

    @property
    def name(self) -> str:
        """Return the server name."""
        return self.config.name

    @property
    def is_connected(self) -> bool:
        """Check if the server is connected."""
        return self.state.connected and self._session is not None

    async def start(self) -> bool:
        """Start the local MCP server.

        Returns:
            True if connection was successful
        """
        async with self._lock:
            if self.is_connected:
                logger.debug(f"[MCP:{self.name}] Already connected")
                return True

            try:
                return await self._connect()
            except Exception as e:
                self.state.last_error = str(e)
                self.state.connected = False
                logger.error(f"[MCP:{self.name}] Start failed: {e}")
                self._update_config_status("error", error=str(e))
                return False

    async def _connect(self) -> bool:
        """Establish connection to the MCP server."""
        if not self.config.command:
            self.state.last_error = "No command specified"
            return False

        # Build environment
        env = dict(os.environ)
        env.update(self.config.get_env())

        # Create server parameters
        params = StdioServerParameters(
            command=self.config.command,
            args=self.config.get_args(),
            env=env,
            cwd=self.config.cwd,
        )

        logger.info(f"[MCP:{self.name}] Starting: {params.command} {' '.join(params.args or [])}")

        try:
            self._exit_stack = AsyncExitStack()
            await self._exit_stack.__aenter__()

            # Route server stderr through logging system
            server_errlog = LoggerPipe(logger, logging.DEBUG, prefix=f"[{self.name}] ")

            # Enter stdio client context with logged stderr
            read_stream, write_stream = await self._exit_stack.enter_async_context(
                stdio_client(params, errlog=server_errlog)
            )

            # Enter session context
            self._session = await self._exit_stack.enter_async_context(ClientSession(read_stream, write_stream))

            # Initialize the connection
            await self._session.initialize()

            # Discover tools
            await self._discover_tools()

            self.state.connected = True
            self.state.reconnect_attempts = 0
            self._update_config_status("running")

            # Start hot reload watcher if enabled
            if self.config.hot_reload:
                await self._start_file_watcher()

            logger.info(f"[MCP:{self.name}] Connected, discovered {len(self.state.tools)} tools")
            return True

        except Exception as e:
            self.state.last_error = str(e)
            self.state.connected = False

            if self._exit_stack:
                try:
                    await self._exit_stack.__aexit__(None, None, None)
                except Exception:
                    pass
                self._exit_stack = None

            logger.error(f"[MCP:{self.name}] Connection failed: {e}")
            return False

    async def _discover_tools(self) -> None:
        """Discover available tools from the server."""
        if not self._session:
            return

        try:
            tools_result = await self._session.list_tools()
            self.state.tools = [
                MCPTool(
                    name=tool.name,
                    description=tool.description or "",
                    input_schema=(tool.inputSchema if tool.inputSchema else {"type": "object", "properties": {}}),
                )
                for tool in tools_result.tools
            ]

            # Update cached tools in config
            self.config.set_tools([t.to_dict() for t in self.state.tools])
            save_local_server_config(self.config)

            # Notify callback
            if self._on_tools_changed:
                self._on_tools_changed(self.name, self.state.tools)

            logger.debug(f"[MCP:{self.name}] Discovered tools: {[t.name for t in self.state.tools]}")

        except Exception as e:
            logger.warning(f"[MCP:{self.name}] Failed to discover tools: {e}")
            self.state.tools = []

    async def stop(self) -> None:
        """Stop the local MCP server."""
        async with self._lock:
            # Stop file watcher first
            await self._stop_file_watcher()

            was_connected = self.state.connected
            self.state.connected = False
            self._session = None

            if self._exit_stack:
                exit_stack = self._exit_stack
                self._exit_stack = None
                try:
                    await exit_stack.__aexit__(None, None, None)
                except Exception as e:
                    error_msg = str(e)
                    if "cancel scope" in error_msg.lower() or "different task" in error_msg.lower():
                        logger.debug(f"[MCP:{self.name}] Cross-task stop (expected): {e}")
                    else:
                        logger.warning(f"[MCP:{self.name}] Error during stop: {e}")

            if was_connected:
                self._update_config_status("stopped")
                logger.info(f"[MCP:{self.name}] Stopped")

    async def restart(self) -> bool:
        """Restart the local MCP server.

        Returns:
            True if restart was successful
        """
        logger.info(f"[MCP:{self.name}] Restarting...")
        await self.stop()
        await asyncio.sleep(0.5)  # Brief pause before restart
        return await self.start()

    async def reconnect(self) -> bool:
        """Attempt to reconnect to the server.

        Returns:
            True if reconnection was successful
        """
        self.state.reconnect_attempts += 1
        if self.state.reconnect_attempts > self.state.max_reconnect_attempts:
            logger.warning(f"[MCP:{self.name}] Max reconnect attempts reached")
            return False

        logger.info(
            f"[MCP:{self.name}] Reconnect attempt "
            f"{self.state.reconnect_attempts}/{self.state.max_reconnect_attempts}"
        )

        await self.stop()
        await asyncio.sleep(1.0 * self.state.reconnect_attempts)  # Exponential backoff
        return await self.start()

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> str:
        """Call a tool on the MCP server.

        Args:
            tool_name: Name of the tool to call
            arguments: Arguments to pass to the tool

        Returns:
            Result of the tool call as a string
        """
        if not self.is_connected or not self._session:
            if not await self.reconnect():
                return f"Error: Not connected to MCP server '{self.name}'"

        try:
            from mcp import types

            result = await self._session.call_tool(tool_name, arguments)

            # Extract text content from the result
            output_parts = []
            for content in result.content:
                if isinstance(content, types.TextContent):
                    output_parts.append(content.text)
                elif isinstance(content, types.ImageContent):
                    output_parts.append(f"[Image: {content.mimeType}]")
                elif isinstance(content, types.EmbeddedResource):
                    output_parts.append(f"[Resource: {content.resource.uri}]")
                else:
                    output_parts.append(str(content))

            # Check for structured content
            if result.structuredContent:
                import json

                output_parts.append(f"\nStructured: {json.dumps(result.structuredContent, indent=2)}")

            return "\n".join(output_parts) if output_parts else "Tool executed successfully (no output)"

        except Exception as e:
            error_msg = str(e)
            self.state.last_error = error_msg
            logger.error(f"[MCP:{self.name}] Tool call '{tool_name}' failed: {e}")

            # Check if connection error
            if "connection" in error_msg.lower() or "closed" in error_msg.lower():
                self.state.connected = False
                if await self.reconnect():
                    # Retry the tool call
                    try:
                        result = await self._session.call_tool(tool_name, arguments)
                        output_parts = []
                        for content in result.content:
                            if hasattr(content, "text"):
                                output_parts.append(content.text)
                        return "\n".join(output_parts) if output_parts else "Tool executed successfully"
                    except Exception as retry_error:
                        return f"Error calling tool '{tool_name}': {retry_error}"

            return f"Error calling tool '{tool_name}': {error_msg}"

    def get_tools(self) -> list[MCPTool]:
        """Get the list of discovered tools."""
        return self.state.tools

    def get_tool_names(self) -> list[str]:
        """Get the names of all discovered tools."""
        return [tool.name for tool in self.state.tools]

    def get_status(self) -> dict[str, Any]:
        """Get the current status of the server."""
        return {
            "name": self.name,
            "type": "local",
            "connected": self.is_connected,
            "command": self.config.command,
            "args": self.config.get_args(),
            "cwd": self.config.cwd,
            "tool_count": len(self.state.tools),
            "tools": [t.name for t in self.state.tools],
            "last_error": self.state.last_error,
            "reconnect_attempts": self.state.reconnect_attempts,
            "hot_reload": self.config.hot_reload,
            "enabled": self.config.enabled,
            "status": self.config.status,
        }

    def _update_config_status(self, status: str, error: str | None = None) -> None:
        """Update the config status and save."""
        self.config.status = status
        if error:
            self.config.last_error = error
            self.config.last_error_at = utcnow_iso()
        self.config.updated_at = utcnow_iso()
        save_local_server_config(self.config)

    # --- Hot Reload Support ---

    async def _start_file_watcher(self) -> None:
        """Start watching for file changes to hot reload."""
        if not self.config.hot_reload:
            return

        watch_path = self.config.cwd or str(get_local_server_dir(self.name))
        if not Path(watch_path).exists():
            logger.warning(f"[MCP:{self.name}] Hot reload path not found: {watch_path}")
            return

        try:
            from watchdog.events import FileSystemEventHandler
            from watchdog.observers import Observer

            class ReloadHandler(FileSystemEventHandler):
                def __init__(self, server: LocalServerProcess) -> None:
                    self.server = server
                    self._debounce_task: asyncio.Task | None = None
                    self._loop = asyncio.get_event_loop()

                def on_modified(self, event) -> None:
                    if event.is_directory:
                        return
                    # Debounce rapid changes
                    self._schedule_reload()

                def on_created(self, event) -> None:
                    if event.is_directory:
                        return
                    self._schedule_reload()

                def _schedule_reload(self) -> None:
                    if self._debounce_task and not self._debounce_task.done():
                        self._debounce_task.cancel()
                    self._debounce_task = self._loop.create_task(self._debounced_reload())

                async def _debounced_reload(self) -> None:
                    await asyncio.sleep(1.0)  # 1 second debounce
                    logger.info(f"[MCP:{self.server.name}] File change detected, reloading...")
                    await self.server.restart()

            self._observer = Observer()
            self._observer.schedule(ReloadHandler(self), watch_path, recursive=True)
            self._observer.start()

            logger.info(f"[MCP:{self.name}] Hot reload enabled, watching: {watch_path}")

        except ImportError:
            logger.warning(f"[MCP:{self.name}] Hot reload requires 'watchdog' package: pip install watchdog")
        except Exception as e:
            logger.warning(f"[MCP:{self.name}] Failed to start file watcher: {e}")

    async def _stop_file_watcher(self) -> None:
        """Stop the file watcher."""
        if self._watcher_task and not self._watcher_task.done():
            self._watcher_task.cancel()
            self._watcher_task = None

        if self._observer:
            self._observer.stop()
            self._observer.join(timeout=2)
            self._observer = None


class LocalServerManager:
    """Manages multiple local MCP server processes.

    Provides:
    - Central management of all local servers
    - Automatic startup of enabled servers
    - Tool aggregation across servers
    """

    def __init__(self) -> None:
        """Initialize the local server manager."""
        self._servers: dict[str, LocalServerProcess] = {}
        self._lock = asyncio.Lock()
        self._initialized = False

    async def initialize(self) -> dict[str, bool]:
        """Initialize all enabled local servers.

        Returns:
            Dict mapping server names to connection success status
        """
        async with self._lock:
            if self._initialized:
                return {name: srv.is_connected for name, srv in self._servers.items()}

            from .models import get_enabled_local_servers

            configs = get_enabled_local_servers()
            logger.info(f"[MCP Local] Found {len(configs)} enabled local servers")

            results = {}
            for config in configs:
                results[config.name] = await self._start_server(config)

            self._initialized = True
            connected = sum(1 for v in results.values() if v)
            logger.info(f"[MCP Local] Initialized {connected}/{len(results)} servers")
            return results

    async def _start_server(self, config: LocalServerConfig) -> bool:
        """Start a single local server."""
        if config.name in self._servers:
            return self._servers[config.name].is_connected

        process = LocalServerProcess(
            config,
            on_tools_changed=self._on_tools_changed,
        )
        self._servers[config.name] = process

        return await process.start()

    def _on_tools_changed(self, server_name: str, tools: list[MCPTool]) -> None:
        """Callback when a server's tools change."""
        logger.debug(f"[MCP Local] Tools changed for {server_name}: {len(tools)} tools")

    async def start_server(self, server_name: str) -> bool:
        """Start a specific server by name.

        Args:
            server_name: Name of the server to start

        Returns:
            True if started successfully
        """
        async with self._lock:
            # Check if already running
            if server_name in self._servers:
                return await self._servers[server_name].start()

            # Load config
            config = load_local_server_config(server_name)
            if not config:
                logger.error(f"[MCP Local] Server '{server_name}' not found")
                return False

            return await self._start_server(config)

    async def stop_server(self, server_name: str) -> bool:
        """Stop a specific server by name.

        Args:
            server_name: Name of the server to stop

        Returns:
            True if stopped successfully
        """
        async with self._lock:
            if server_name not in self._servers:
                logger.warning(f"[MCP Local] Server '{server_name}' not running")
                return False

            await self._servers[server_name].stop()
            return True

    async def restart_server(self, server_name: str) -> bool:
        """Restart a specific server.

        Args:
            server_name: Name of the server to restart

        Returns:
            True if restarted successfully
        """
        if server_name in self._servers:
            return await self._servers[server_name].restart()
        return await self.start_server(server_name)

    async def hot_reload_server(self, server_name: str) -> bool:
        """Enable hot reload for a server and restart it.

        Args:
            server_name: Name of the server

        Returns:
            True if hot reload enabled successfully
        """
        config = load_local_server_config(server_name)
        if not config:
            return False

        config.hot_reload = True
        save_local_server_config(config)

        if server_name in self._servers:
            self._servers[server_name].config.hot_reload = True
            return await self._servers[server_name].restart()

        return await self.start_server(server_name)

    async def disable_hot_reload(self, server_name: str) -> bool:
        """Disable hot reload for a server.

        Args:
            server_name: Name of the server

        Returns:
            True if disabled successfully
        """
        config = load_local_server_config(server_name)
        if not config:
            return False

        config.hot_reload = False
        save_local_server_config(config)

        if server_name in self._servers:
            self._servers[server_name].config.hot_reload = False
            await self._servers[server_name]._stop_file_watcher()

        return True

    def get_server(self, server_name: str) -> LocalServerProcess | None:
        """Get a server process by name."""
        return self._servers.get(server_name)

    def get_all_tools(self) -> list[tuple[str, MCPTool]]:
        """Get all tools from all connected servers.

        Returns:
            List of (server_name, tool) tuples
        """
        tools = []
        for server_name, process in self._servers.items():
            if process.is_connected:
                for tool in process.get_tools():
                    tools.append((server_name, tool))
        return tools

    def get_namespaced_tools(self) -> dict[str, MCPTool]:
        """Get all tools with namespaced names.

        Returns:
            Dict mapping namespaced names (server__tool) to MCPTool
        """
        result = {}
        for server_name, tool in self.get_all_tools():
            namespaced_name = f"{server_name}__{tool.name}"
            result[namespaced_name] = tool
        return result

    async def call_tool(self, server_name: str, tool_name: str, arguments: dict[str, Any]) -> str:
        """Call a tool on a specific server.

        Args:
            server_name: Name of the server
            tool_name: Name of the tool
            arguments: Tool arguments

        Returns:
            Tool execution result
        """
        process = self._servers.get(server_name)
        if not process:
            return f"Error: Server '{server_name}' not running"

        return await process.call_tool(tool_name, arguments)

    def get_server_status(self, server_name: str) -> dict[str, Any] | None:
        """Get status of a specific server."""
        process = self._servers.get(server_name)
        if process:
            return process.get_status()

        config = load_local_server_config(server_name)
        if config:
            return {
                "name": config.name,
                "type": "local",
                "connected": False,
                "enabled": config.enabled,
                "command": config.command,
                "tool_count": config.tool_count,
                "status": config.status,
                "last_error": config.last_error,
            }
        return None

    def get_all_status(self) -> list[dict[str, Any]]:
        """Get status of all servers."""
        from .models import list_local_server_configs

        statuses = []
        configs = list_local_server_configs()

        for config in configs:
            process = self._servers.get(config.name)
            if process:
                statuses.append(process.get_status())
            else:
                statuses.append(
                    {
                        "name": config.name,
                        "type": "local",
                        "connected": False,
                        "enabled": config.enabled,
                        "command": config.command,
                        "tool_count": config.tool_count,
                        "status": config.status,
                        "last_error": config.last_error,
                    }
                )

        return statuses

    async def shutdown(self) -> None:
        """Shutdown all local servers."""
        async with self._lock:
            logger.info(f"[MCP Local] Shutting down {len(self._servers)} servers...")

            for server_name, process in list(self._servers.items()):
                try:
                    await process.stop()
                except Exception as e:
                    logger.warning(f"[MCP Local] Error stopping '{server_name}': {e}")

            self._servers.clear()
            self._initialized = False
            logger.info("[MCP Local] Shutdown complete")

    def __len__(self) -> int:
        """Return number of running servers."""
        return len(self._servers)

    def __contains__(self, server_name: str) -> bool:
        """Check if a server is managed."""
        return server_name in self._servers
