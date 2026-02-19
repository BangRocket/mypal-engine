"""Tool execution for the Clara Gateway.

Handles:
- MCP tool routing
- Docker sandbox tools
- Local file tools
- Modular registry tools
"""

from __future__ import annotations

import time
from typing import Any

from config.logging import get_logger

logger = get_logger("gateway.tools")


class ToolExecutor:
    """Executes tools from various sources.

    Routes tool calls to:
    - MCP servers (via manager)
    - Docker sandbox
    - Local file storage
    - Modular tool registry (GitHub, ADO, etc.)
    """

    def __init__(self) -> None:
        """Initialize the executor."""
        from collections.abc import Callable

        from clara_core.security.circuit_breaker import CircuitBreaker

        self._initialized = False
        self._sandbox_manager: Any = None
        self._file_manager: Any = None
        self._mcp_manager: Any = None
        self._tool_registry: Any = None
        self._mcp_initialized = False
        self._modular_initialized = False
        self._circuit_breaker = CircuitBreaker()
        self._tool_handlers: dict[str, Callable[..., Any]] = {}

    async def initialize(self) -> None:
        """Initialize tool systems.

        Loads the sandbox manager, file manager, MCP servers, and tool registry.
        """
        if self._initialized:
            return

        # Import and initialize sandbox
        from sandbox.manager import get_sandbox_manager

        self._sandbox_manager = get_sandbox_manager()

        # Import and initialize file storage
        from clara_core.core_tools.files_tool import get_file_manager

        self._file_manager = get_file_manager()

        # Initialize modular tools
        await self._init_modular_tools()

        # Initialize MCP
        await self._init_mcp()

        self._register_handlers()
        self._initialized = True
        logger.info("ToolExecutor initialized")

    async def shutdown(self) -> None:
        """Shut down tool systems, including MCP servers."""
        if self._mcp_manager and self._mcp_initialized:
            await self._mcp_manager.shutdown()

    async def _init_modular_tools(self) -> None:
        """Initialize modular tools system."""
        try:
            from tools import init_tools

            results = await init_tools(hot_reload=False)
            loaded = [name for name, success in results.items() if success]
            if loaded:
                logger.info(f"Loaded tool modules: {', '.join(loaded)}")

            from tools import get_registry

            self._tool_registry = get_registry()
            self._modular_initialized = True
        except Exception as e:
            logger.error(f"Failed to initialize modular tools: {e}")

    def _init_discord_tools(self, capabilities: list[str] | None = None) -> list[dict[str, Any]]:
        """Initialize Discord-specific tools.

        Args:
            capabilities: List of adapter capabilities to filter tools

        Returns:
            List of Discord tool definitions in OpenAI format
        """
        caps = capabilities or []
        tools = []

        # Basic formatting tool (always available)
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": "format_discord_message",
                    "description": (
                        "Format a Discord message with Discord-specific markdown features. "
                        "Use this for: code blocks with syntax highlighting, spoilers, or "
                        "special formatting. This ensures proper Discord rendering."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "content": {
                                "type": "string",
                                "description": "Plain text content of the message",
                            },
                            "code_block": {
                                "type": "string",
                                "description": "Code to format as a code block (used instead of content)",
                            },
                            "language": {
                                "type": "string",
                                "description": "Programming language for syntax highlighting (e.g., 'python', 'javascript', 'bash')",
                            },
                            "spoiler": {
                                "type": "string",
                                "description": "Text to hide behind spoiler tags (click to reveal)",
                            },
                        },
                    },
                },
            }
        )

        # Reaction tool (requires reactions capability)
        if "reactions" in caps or not caps:
            tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": "add_discord_reaction",
                        "description": (
                            "Add an emoji reaction to the user's message or to Clara's own response. "
                            "Use this for quick acknowledgments or to mark task completion. "
                            "Available reactions: ✅ (success), ❌ (error), ⚠️ (warning), "
                            "🎉 (celebration), 🤔 (thinking), 👍 (thumbs up), 👎 (thumbs down), "
                            "🔥 (fire), 💯 (100), ❓ (question)."
                        ),
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "emoji": {
                                    "type": "string",
                                    "description": "Emoji to react with (e.g., '✅', '🎉', '👍')",
                                },
                            },
                            "required": ["emoji"],
                        },
                    },
                }
            )

        # File attachment tool (requires attachments capability)
        if "attachments" in caps or not caps:
            tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": "send_discord_file",
                        "description": (
                            "IMPORTANT: Use this tool to send files to Discord. Creates a file with the "
                            "given content and sends it as a Discord attachment. Use this when: sharing "
                            "code files, sending documents, sharing configuration files, or when content "
                            "is too long for a message. The file will be attached to your response. "
                            "Do NOT use write_file or save_to_local for sending files - use THIS tool."
                        ),
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "filename": {
                                    "type": "string",
                                    "description": "Name for the file with extension (e.g., 'code.py', 'notes.md', 'config.json')",
                                },
                                "content": {
                                    "type": "string",
                                    "description": "The full text content to put in the file",
                                },
                            },
                            "required": ["filename", "content"],
                        },
                    },
                }
            )

        # Embed tool (requires embeds capability)
        if "embeds" in caps or not caps:
            tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": "send_discord_embed",
                        "description": (
                            "Send a rich embedded message with title, description, fields, and color. "
                            "Use embeds for structured information, status displays, or visually "
                            "distinct content. Types: success (green), error (red), warning (yellow), "
                            "info (blue), status (with fields), custom (specify color)."
                        ),
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "type": {
                                    "type": "string",
                                    "enum": ["success", "error", "warning", "info", "status", "custom"],
                                    "description": "Embed type determining color and styling",
                                },
                                "title": {
                                    "type": "string",
                                    "description": "Embed title (required)",
                                },
                                "description": {
                                    "type": "string",
                                    "description": "Main embed content/description",
                                },
                                "fields": {
                                    "type": "array",
                                    "description": "List of embed fields (for status type)",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "name": {"type": "string"},
                                            "value": {"type": "string"},
                                            "inline": {"type": "boolean", "default": False},
                                        },
                                        "required": ["name", "value"],
                                    },
                                },
                                "color": {
                                    "type": "integer",
                                    "description": "Hex color as integer (only for custom type, e.g., 0xFF5733)",
                                },
                                "footer": {
                                    "type": "string",
                                    "description": "Footer text at bottom of embed",
                                },
                            },
                            "required": ["type", "title"],
                        },
                    },
                }
            )

        # Thread creation tool (requires threads capability)
        if "threads" in caps or not caps:
            tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": "create_discord_thread",
                        "description": (
                            "Create a new thread for focused discussion. The thread will be created "
                            "from the user's message, and Clara's response will be posted in the new thread. "
                            "Use threads for in-depth topics, code reviews, or lengthy conversations."
                        ),
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "name": {
                                    "type": "string",
                                    "description": "Thread name (max 100 characters)",
                                },
                                "auto_archive_minutes": {
                                    "type": "integer",
                                    "enum": [60, 1440, 4320, 10080],
                                    "description": "Auto-archive duration: 60 (1 hour), 1440 (1 day), 4320 (3 days), 10080 (1 week)",
                                },
                            },
                            "required": ["name"],
                        },
                    },
                }
            )

        # Message editing tool (requires editing capability)
        if "editing" in caps or not caps:
            tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": "edit_discord_message",
                        "description": (
                            "Edit a previously sent message instead of sending a new one. "
                            "Use this for updating status messages, correcting mistakes, or "
                            "replacing placeholder content with final results."
                        ),
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "target": {
                                    "type": "string",
                                    "enum": ["last", "status"],
                                    "description": "'last' to edit the last sent message, 'status' to edit the status message",
                                },
                            },
                            "required": ["target"],
                        },
                    },
                }
            )

        # Button tool (requires buttons capability)
        if "buttons" in caps or not caps:
            tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": "send_discord_buttons",
                        "description": (
                            "Add interactive buttons to the response message. Buttons can be used "
                            "for confirmations, dismissals, or simple user choices. The message "
                            "content will be sent along with the buttons."
                        ),
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "buttons": {
                                    "type": "array",
                                    "description": "List of buttons to add (max 5)",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "label": {
                                                "type": "string",
                                                "description": "Button text",
                                            },
                                            "style": {
                                                "type": "string",
                                                "enum": ["primary", "secondary", "success", "danger"],
                                                "description": "Button style/color",
                                            },
                                            "action": {
                                                "type": "string",
                                                "enum": ["dismiss", "confirm"],
                                                "description": "'dismiss' removes buttons, 'confirm' updates message to confirmed",
                                            },
                                        },
                                        "required": ["label"],
                                    },
                                    "maxItems": 5,
                                },
                            },
                            "required": ["buttons"],
                        },
                    },
                }
            )

        return tools

    async def _init_mcp(self) -> None:
        """Initialize MCP plugin system."""
        try:
            from clara_core.mcp import get_mcp_manager, init_mcp

            await init_mcp()
            self._mcp_manager = get_mcp_manager()
            self._mcp_initialized = True

            # Setup official MCP servers
            try:
                from clara_core.core_tools import setup_official_mcp_servers

                await setup_official_mcp_servers()
            except ImportError:
                pass
            except Exception as e:
                logger.warning(f"MCP official server setup error: {e}")

            tool_count = len(self._mcp_manager.get_tools_openai_format())
            logger.info(f"MCP: {len(self._mcp_manager)} servers, {tool_count} tools")
        except Exception as e:
            logger.error(f"Failed to initialize MCP: {e}")

    def get_all_tools(
        self,
        include_docker: bool = True,
        adapter_capabilities: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Get all available tools in OpenAI format.

        Args:
            include_docker: Whether to include Docker sandbox tools
            adapter_capabilities: Optional list of adapter capabilities to filter Discord tools

        Returns:
            List of tool definitions
        """
        import os

        if not self._initialized:
            logger.warning("ToolExecutor not initialized")
            return []

        tools: list[dict[str, Any]] = []

        # Get modular registry tools
        if self._modular_initialized and self._tool_registry:
            capabilities = {
                "docker": include_docker,
                "files": True,
                "discord": False,  # Gateway doesn't have Discord context
            }

            # Check for OAuth capabilities
            if os.getenv("GOOGLE_CLIENT_ID") and os.getenv("GOOGLE_CLIENT_SECRET"):
                capabilities["google_oauth"] = True

            native_tools = self._tool_registry.get_tools(
                platform="gateway",
                capabilities=capabilities,
                format="openai",
            )
            tools.extend(native_tools)

        # Add Discord-specific tools (filtered by adapter capabilities)
        discord_tools = self._init_discord_tools(adapter_capabilities)
        tools.extend(discord_tools)

        # Get MCP tools
        if self._mcp_initialized and self._mcp_manager:
            try:
                mcp_tools = self._mcp_manager.get_tools_openai_format()
                tools.extend(mcp_tools)
            except Exception as e:
                logger.warning(f"Failed to get MCP tools: {e}")

        # Deduplicate
        seen = set()
        unique_tools = []
        for tool in tools:
            name = tool.get("function", {}).get("name", "")
            if name not in seen:
                seen.add(name)
                unique_tools.append(tool)

        return unique_tools

    async def execute(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        user_id: str,
        channel_id: str | None = None,
        files_to_send: list[str] | None = None,
        platform_context: dict[str, Any] | None = None,
    ) -> str:
        """Execute a tool and return the result.

        Args:
            tool_name: Name of the tool to execute
            arguments: Tool arguments
            user_id: User ID for context
            channel_id: Optional channel ID
            files_to_send: Optional list to append file paths for attachment
            platform_context: Optional platform-specific context

        Returns:
            Tool output as string
        """
        if not self._initialized:
            return "Error: Tool executor not initialized"

        # Circuit breaker pre-check
        allowed, reason = self._circuit_breaker.can_execute(tool_name)
        if not allowed:
            logger.warning(f"Circuit breaker blocked {tool_name}: {reason}")
            return f"Tool temporarily unavailable: {reason}"

        start_time = time.time()
        logger.debug(f"Executing {tool_name}")

        try:
            result = await self._route_tool(
                tool_name=tool_name,
                arguments=arguments,
                user_id=user_id,
                channel_id=channel_id,
                files_to_send=files_to_send if files_to_send is not None else [],
                platform_context=platform_context or {},
            )
            duration = time.time() - start_time
            logger.debug(f"{tool_name} completed in {duration:.2f}s")
            self._circuit_breaker.record_success(tool_name)
            return result
        except Exception as e:
            logger.exception(f"Tool {tool_name} failed: {e}")
            self._circuit_breaker.record_failure(tool_name, str(e))
            return f"Error: {e}"

    def get_tool_health(self) -> dict[str, Any]:
        """Return circuit breaker health status for all tracked tools."""
        return self._circuit_breaker.get_health_summary()

    def _register_handlers(self) -> None:
        """Register tool name → handler group mappings."""
        # Sandbox tools
        for name in (
            "execute_python",
            "install_package",
            "read_file",
            "write_file",
            "list_files",
            "run_shell",
            "unzip_file",
            "web_search",
            "run_claude_code",
        ):
            self._tool_handlers[name] = self._handle_sandbox_tool

        # Local file tools
        for name in (
            "save_to_local",
            "list_local_files",
            "read_local_file",
            "delete_local_file",
            "download_from_sandbox",
            "upload_to_sandbox",
            "send_local_file",
        ):
            self._tool_handlers[name] = self._handle_file_tool

        # Discord tools
        for name in (
            "format_discord_message",
            "add_discord_reaction",
            "send_discord_embed",
            "create_discord_thread",
            "edit_discord_message",
            "send_discord_buttons",
            "send_discord_file",
        ):
            self._tool_handlers[name] = self._handle_discord_tool

    async def _route_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        user_id: str,
        channel_id: str | None,
        files_to_send: list[str],
        platform_context: dict[str, Any],
    ) -> str:
        """Route tool call to appropriate handler."""
        # MCP tools (dynamic, checked by prefix)
        if "__" in tool_name and self._mcp_initialized:
            if self._mcp_manager.is_mcp_tool(tool_name):
                return await self._mcp_manager.call_tool(tool_name, arguments)

        # Registry lookup
        handler = self._tool_handlers.get(tool_name)
        if handler:
            return await handler(tool_name, arguments, user_id, channel_id, files_to_send, platform_context)

        # Modular registry fallback (GitHub, ADO, etc.)
        if self._modular_initialized and self._tool_registry and tool_name in self._tool_registry:
            from tools import ToolContext

            ctx = ToolContext(
                user_id=user_id,
                channel_id=channel_id,
                platform="gateway",
                extra={"files_to_send": files_to_send, **platform_context},
            )
            return await self._tool_registry.execute(tool_name, arguments, ctx)

        return f"Unknown tool: {tool_name}"

    # ---------- Handler groups ----------

    async def _handle_sandbox_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        user_id: str,
        channel_id: str | None,
        files_to_send: list[str],
        platform_context: dict[str, Any],
    ) -> str:
        """Handle Docker sandbox tools."""
        result = await self._sandbox_manager.handle_tool_call(user_id, tool_name, arguments)
        return result.output if result.success else f"Error: {result.error}"

    async def _handle_file_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        user_id: str,
        channel_id: str | None,
        files_to_send: list[str],
        platform_context: dict[str, Any],
    ) -> str:
        """Handle local file storage tools."""
        if tool_name == "save_to_local":
            result = self._file_manager.save_file(
                user_id,
                arguments.get("filename", "unnamed.txt"),
                arguments.get("content", ""),
                channel_id,
            )
            return result.message

        if tool_name == "list_local_files":
            files = self._file_manager.list_files(user_id, channel_id)
            if not files:
                return "No files saved yet."
            lines = []
            for f in files:
                size = f"{f.size} bytes" if f.size < 1024 else f"{f.size / 1024:.1f} KB"
                lines.append(f"- {f.name} ({size})")
            return "Saved files:\n" + "\n".join(lines)

        if tool_name == "read_local_file":
            result = self._file_manager.read_file(
                user_id,
                arguments.get("filename", ""),
                channel_id,
            )
            return result.message

        if tool_name == "delete_local_file":
            result = self._file_manager.delete_file(
                user_id,
                arguments.get("filename", ""),
                channel_id,
            )
            return result.message

        if tool_name == "download_from_sandbox":
            sandbox_path = arguments.get("sandbox_path", "")
            local_filename = arguments.get("local_filename", "")
            if not local_filename:
                local_filename = sandbox_path.split("/")[-1] if "/" in sandbox_path else sandbox_path
            read_result = await self._sandbox_manager.read_file(user_id, sandbox_path)
            if not read_result.success:
                return f"Error reading from sandbox: {read_result.error}"
            save_result = self._file_manager.save_file(user_id, local_filename, read_result.output, channel_id)
            return save_result.message

        if tool_name == "upload_to_sandbox":
            local_filename = arguments.get("local_filename", "")
            sandbox_path = arguments.get("sandbox_path", "")
            content, error = self._file_manager.read_file_bytes(user_id, local_filename, channel_id)
            if content is None:
                return f"Error: {error}"
            if not sandbox_path:
                sandbox_path = f"/home/user/{local_filename}"
            write_result = await self._sandbox_manager.write_file(user_id, sandbox_path, content)
            if write_result.success:
                size_kb = len(content) / 1024
                return f"Uploaded '{local_filename}' ({size_kb:.1f} KB) to sandbox at {sandbox_path}"
            return f"Error uploading to sandbox: {write_result.error}"

        if tool_name == "send_local_file":
            filename = arguments.get("filename", "")
            file_path = self._file_manager.get_file_path(user_id, filename, channel_id)
            if file_path:
                files_to_send.append(str(file_path))
                return f"File '{filename}' will be sent to chat."
            return f"File not found: {filename}"

        return f"Unknown file tool: {tool_name}"

    async def _handle_discord_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        user_id: str,
        channel_id: str | None,
        files_to_send: list[str],
        platform_context: dict[str, Any],
    ) -> str:
        """Handle Discord-specific tools."""
        import json

        if tool_name == "format_discord_message":
            content = arguments.get("content", "")
            code_block = arguments.get("code_block", "")
            language = arguments.get("language", "")
            spoiler = arguments.get("spoiler", "")
            formatted = f"```{language}\n{code_block}\n```" if code_block else content
            if spoiler:
                formatted = f"||{spoiler}||\n\n{formatted}"
            return formatted

        if tool_name == "add_discord_reaction":
            return f"__REACTION__:{arguments.get('emoji', '✅')}"

        if tool_name == "send_discord_embed":
            embed_data = {
                "type": arguments.get("type", "info"),
                "title": arguments.get("title", ""),
                "description": arguments.get("description"),
                "fields": arguments.get("fields"),
                "color": arguments.get("color"),
                "footer": arguments.get("footer"),
            }
            embed_data = {k: v for k, v in embed_data.items() if v is not None}
            return f"__EMBED__:{json.dumps(embed_data)}"

        if tool_name == "create_discord_thread":
            name = arguments.get("name", "Discussion")[:100]
            auto_archive = arguments.get("auto_archive_minutes", 1440)
            return f"__THREAD__:{name}:{auto_archive}"

        if tool_name == "edit_discord_message":
            return f"__EDIT__:{arguments.get('target', 'last')}"

        if tool_name == "send_discord_buttons":
            buttons = arguments.get("buttons", [])
            normalized = [
                {
                    "label": btn.get("label", "Button"),
                    "style": btn.get("style", "secondary"),
                    "action": btn.get("action", "dismiss"),
                }
                for btn in buttons[:5]
            ]
            return f"__BUTTONS__:{json.dumps(normalized)}"

        if tool_name == "send_discord_file":
            filename = arguments.get("filename", "file.txt")
            content = arguments.get("content", "")
            result = self._file_manager.save_file(user_id, filename, content, channel_id)
            if not result.success:
                return f"Error saving file: {result.message}"
            file_path = self._file_manager.get_file_path(user_id, filename, channel_id)
            if file_path:
                files_to_send.append(str(file_path))
                logger.info(f"[send_discord_file] Added file to send: {file_path}")
                return f"File '{filename}' will be sent as an attachment."
            return f"Error: Could not locate saved file '{filename}'"

        return f"Unknown Discord tool: {tool_name}"
