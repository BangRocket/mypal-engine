"""Plugin registry - Central registry for all plugins.

This module provides the PluginRegistry class that manages all loaded plugins,
their registered tools, hooks, channels, providers, and services.

Integrates with:
- PolicyEngine for tool access control
- ToolNameNormalizer for consistent tool naming
"""

from __future__ import annotations

import asyncio
import logging
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable

if TYPE_CHECKING:
    from mypalclara.tools._base import ToolContext, ToolDef

    from .types import Diagnostic, PluginContext, PluginManifest

from .normalization import ToolNameNormalizer, get_normalizer
from .policies import PolicyAction, PolicyContext, PolicyEngine, get_policy_engine
from .runtime import PluginRuntime
from .types import (
    Diagnostic,
    DiagnosticLevel,
    HookHandler,
    PluginKind,
    PluginOrigin,
    PluginRecord,
    ToolFactory,
)

logger = logging.getLogger(__name__)


@dataclass
class PluginToolRegistration:
    """Registration info for a plugin tool."""

    plugin_id: str
    factory: ToolFactory | None = None
    tool: "ToolDef | None" = None
    optional: bool = False
    source: str = ""


@dataclass
class PluginRegistry:
    """Central registry for all Clara plugins.

    Manages plugin loading, registration, and provides
    unified access to all plugin-registered resources.

    Integrates with:
    - PolicyEngine for tool access control
    - ToolNameNormalizer for consistent tool naming

    Usage:
        registry = PluginRegistry()
        await registry.initialize()
        tools = registry.get_tools()
        result = await registry.execute("tool_name", {"arg": "value"}, context)
        await registry.emit_hook("event_name", **kwargs)
    """

    # Plugin records
    plugins: dict[str, PluginRecord] = field(default_factory=dict)

    # Registered resources
    tools: dict[str, "ToolDef"] = field(default_factory=dict)
    hooks: dict[str, list[HookHandler]] = field(default_factory=dict)
    channels: dict[str, Any] = field(default_factory=dict)
    providers: dict[str, Any] = field(default_factory=dict)
    services: dict[str, Any] = field(default_factory=dict)
    commands: dict[str, Any] = field(default_factory=dict)

    # Tool registrations (factory-based)
    tool_registrations: list[PluginToolRegistration] = field(default_factory=list)

    # Tool metadata (for backwards compatibility with ToolRegistry)
    tool_sources: dict[str, str] = field(default_factory=dict)  # tool_name -> plugin_id
    system_prompts: dict[str, str] = field(default_factory=dict)  # plugin_id -> prompt

    # Diagnostics
    diagnostics: list[Diagnostic] = field(default_factory=list)

    # Runtime and config (set during initialization)
    runtime: PluginRuntime | None = None
    config: dict[str, Any] = field(default_factory=dict)

    # Policy engine and normalizer (initialized in __post_init__)
    policy_engine: PolicyEngine | None = None
    normalizer: ToolNameNormalizer | None = None

    # State
    _initialized: bool = False

    def __post_init__(self) -> None:
        """Initialize default runtime, policy engine, and normalizer."""
        if self.runtime is None:
            self.runtime = PluginRuntime(
                logger=logger,
                state_dir=Path.cwd() / ".clara" / "state",
                config_dir=Path.cwd() / ".clara" / "config",
            )

        # Use global singletons for policy engine and normalizer
        if self.policy_engine is None:
            self.policy_engine = get_policy_engine()
        if self.normalizer is None:
            self.normalizer = get_normalizer()

    def push_diagnostic(self, diagnostic: Diagnostic) -> None:
        """Add a diagnostic message.

        Args:
            diagnostic: Diagnostic to add
        """
        self.diagnostics.append(diagnostic)
        level_str = diagnostic.level.value.upper()

        if diagnostic.level == DiagnosticLevel.ERROR:
            logger.error(f"[{diagnostic.plugin_id or 'system'}] {diagnostic.message}")
        else:
            logger.warning(f"[{diagnostic.plugin_id or 'system'}] {diagnostic.message}")

    def register_plugin(
        self,
        record: PluginRecord,
    ) -> None:
        """Register a plugin record.

        Args:
            record: Plugin record to add to registry
        """
        if record.id in self.plugins:
            existing = self.plugins[record.id]
            self.push_diagnostic(
                Diagnostic(
                    level=DiagnosticLevel.ERROR,
                    message=f"Plugin '{record.id}' already registered by {existing.origin}",
                    plugin_id=record.id,
                    source=record.source,
                )
            )
            return

        self.plugins[record.id] = record
        logger.info(f"Registered plugin: {record.id} ({record.name}) " f"from {record.origin}")

    def register_tool(
        self,
        tool: "ToolDef | ToolFactory",
        plugin_id: str,
        optional: bool = False,
        source: str = "",
    ) -> None:
        """Register a tool or tool factory.

        Args:
            tool: ToolDef or ToolFactory function
            plugin_id: Plugin registering the tool
            optional: Whether tool is optional (needs allowlist)
            source: Source file/path
        """
        from mypalclara.tools._base import ToolDef

        registration = PluginToolRegistration(
            plugin_id=plugin_id,
            optional=optional,
            source=source,
        )

        if isinstance(tool, ToolDef):
            # Normalize tool name for consistent lookup
            canonical_name = self.normalizer.resolve(tool.name) if self.normalizer else tool.name

            # Check for conflicts
            if canonical_name in self.tools:
                existing_source = self.tool_sources.get(canonical_name, "unknown")
                if existing_source != plugin_id:
                    self.push_diagnostic(
                        Diagnostic(
                            level=DiagnosticLevel.ERROR,
                            message=f"Tool '{tool.name}' already registered by '{existing_source}'",
                            plugin_id=plugin_id,
                            source=source,
                        )
                    )
                    return

            # Direct tool registration
            registration.tool = tool
            self.tools[canonical_name] = tool
            self.tool_sources[canonical_name] = plugin_id

            # Update plugin record if exists
            if plugin_id in self.plugins:
                self.plugins[plugin_id].tool_names.append(canonical_name)

            # Add tool to appropriate policy group
            if self.policy_engine:
                self._add_tool_to_policy_groups(canonical_name, plugin_id)

            logger.debug(f"Registered tool: {canonical_name} from {plugin_id}")
        elif callable(tool):
            # Factory registration - store for later instantiation
            registration.factory = tool
            self.tool_registrations.append(registration)
            logger.debug(f"Registered tool factory from {plugin_id}")
        else:
            self.push_diagnostic(
                Diagnostic(
                    level=DiagnosticLevel.ERROR,
                    message=f"Invalid tool registration from {plugin_id}",
                    plugin_id=plugin_id,
                    source=source,
                )
            )

    def _add_tool_to_policy_groups(self, tool_name: str, plugin_id: str) -> None:
        """Add a tool to appropriate policy groups based on plugin ID.

        Args:
            tool_name: Canonical tool name
            plugin_id: Plugin that registered the tool
        """
        if not self.policy_engine:
            return

        # MCP tools go to group:mcp
        if plugin_id == "mcp" or plugin_id.startswith("mcp:"):
            self.policy_engine.add_to_group("group:mcp", tool_name)

    def register_hook(
        self,
        event: str | list[str],
        handler: HookHandler,
        plugin_id: str,
        source: str = "",
    ) -> None:
        """Register a hook handler.

        Args:
            event: Event name or list of event names
            handler: Handler function
            plugin_id: Plugin registering the hook
            source: Source file/path
        """
        events = [event] if isinstance(event, str) else event

        for evt in events:
            if evt not in self.hooks:
                self.hooks[evt] = []

            self.hooks[evt].append(handler)
            if plugin_id in self.plugins:
                self.plugins[plugin_id].hook_names.append(evt)

            logger.debug(f"Registered hook: {evt} from {plugin_id}")

    def register_channel(
        self,
        plugin: Any,
        plugin_id: str,
    ) -> None:
        """Register a channel plugin.

        Args:
            plugin: Channel plugin object
            plugin_id: Plugin registering the channel
        """
        if not hasattr(plugin, "id"):
            self.push_diagnostic(
                Diagnostic(
                    level=DiagnosticLevel.ERROR,
                    message="Channel plugin missing 'id' attribute",
                    plugin_id=plugin_id,
                )
            )
            return

        channel_id = getattr(plugin, "id")
        if channel_id in self.channels:
            self.push_diagnostic(
                Diagnostic(
                    level=DiagnosticLevel.ERROR,
                    message=f"Channel '{channel_id}' already registered",
                    plugin_id=plugin_id,
                )
            )
            return

        self.channels[channel_id] = plugin
        if plugin_id in self.plugins:
            self.plugins[plugin_id].channel_ids.append(channel_id)

        logger.info(f"Registered channel: {channel_id} from {plugin_id}")

    def register_provider(
        self,
        provider: Any,
        plugin_id: str,
    ) -> None:
        """Register a model provider plugin.

        Args:
            provider: Provider plugin object
            plugin_id: Plugin registering the provider
        """
        if not hasattr(provider, "id"):
            self.push_diagnostic(
                Diagnostic(
                    level=DiagnosticLevel.ERROR,
                    message="Provider plugin missing 'id' attribute",
                    plugin_id=plugin_id,
                )
            )
            return

        provider_id = getattr(provider, "id")
        if provider_id in self.providers:
            self.push_diagnostic(
                Diagnostic(
                    level=DiagnosticLevel.ERROR,
                    message=f"Provider '{provider_id}' already registered",
                    plugin_id=plugin_id,
                )
            )
            return

        self.providers[provider_id] = provider
        if plugin_id in self.plugins:
            self.plugins[plugin_id].provider_ids.append(provider_id)

        logger.info(f"Registered provider: {provider_id} from {plugin_id}")

    def register_service(
        self,
        service: Any,
        plugin_id: str,
    ) -> None:
        """Register a background service.

        Args:
            service: Service object
            plugin_id: Plugin registering the service
        """
        if not hasattr(service, "id"):
            self.push_diagnostic(
                Diagnostic(
                    level=DiagnosticLevel.ERROR,
                    message="Service missing 'id' attribute",
                    plugin_id=plugin_id,
                )
            )
            return

        service_id = getattr(service, "id")
        if service_id in self.services:
            self.push_diagnostic(
                Diagnostic(
                    level=DiagnosticLevel.ERROR,
                    message=f"Service '{service_id}' already registered",
                    plugin_id=plugin_id,
                )
            )
            return

        self.services[service_id] = service
        if plugin_id in self.plugins:
            self.plugins[plugin_id].service_ids.append(service_id)

        logger.info(f"Registered service: {service_id} from {plugin_id}")

    async def emit_hook(
        self,
        event_name: str,
        **kwargs: Any,
    ) -> list[Any]:
        """Call all handlers for a hook event.

        Args:
            event_name: Hook event to emit
            **kwargs: Arguments to pass to handlers

        Returns:
            List of results from all handlers
        """
        if event_name not in self.hooks:
            return []

        results = []
        for handler in self.hooks[event_name]:
            try:
                result = handler(**kwargs)
                if asyncio.iscoroutine(result):
                    result = await result
                results.append(result)
            except Exception as e:
                logger.error(f"Hook error for '{event_name}': {e}", exc_info=True)

        return results

    def get_tools(
        self,
        platform: str | None = None,
        capabilities: dict[str, bool] | None = None,
        format: str = "raw",
        policy_context: PolicyContext | None = None,
    ) -> list["ToolDef"] | list[dict[str, Any]]:
        """Get all registered tools with optional filtering.

        Args:
            platform: Filter to tools available on this platform (None = all)
            capabilities: Dict of capability -> available (e.g., {"docker": True})
            format: Output format - "raw" (ToolDef), "openai", "mcp", or "claude"
            policy_context: Context for policy filtering (None = no filtering)

        Returns:
            List of ToolDef objects or formatted dicts
        """
        tools = []
        for tool in self.tools.values():
            # Platform filter
            if platform and tool.platforms and platform not in tool.platforms:
                continue

            # Capability filter
            if capabilities:
                skip = False
                for cap in tool.requires:
                    if not capabilities.get(cap, False):
                        skip = True
                        break
                if skip:
                    continue

            # Policy filter
            if policy_context and self.policy_engine:
                action = self.policy_engine.evaluate(tool.name, policy_context)
                if action == PolicyAction.DENY:
                    continue

            tools.append(tool)

        # Format conversion
        if format == "raw":
            return tools
        elif format == "mcp":
            return [tool.to_mcp_format() for tool in tools]
        elif format == "claude":
            return [tool.to_claude_format() for tool in tools]
        else:  # openai
            return [tool.to_openai_format() for tool in tools]

    def get_tools_formatted(
        self,
        platform: str | None = None,
        capabilities: dict[str, bool] | None = None,
        format: str = "openai",
    ) -> list[dict[str, Any]]:
        """Get tools in a specific format (backwards compatibility).

        Args:
            platform: Filter to tools available on this platform
            capabilities: Dict of capability -> available
            format: Output format - "openai", "mcp", or "claude"

        Returns:
            List of formatted tool dicts
        """
        result = self.get_tools(platform, capabilities, format)
        # Type narrowing - when format is not "raw", result is list[dict]
        return result if isinstance(result, list) and (not result or isinstance(result[0], dict)) else []

    def get_plugin(self, plugin_id: str) -> PluginRecord | None:
        """Get a plugin record by ID.

        Args:
            plugin_id: Plugin ID to look up

        Returns:
            PluginRecord or None if not found
        """
        return self.plugins.get(plugin_id)

    def get_tool(self, tool_name: str) -> "ToolDef | None":
        """Get a tool by name, with alias resolution.

        Args:
            tool_name: Tool name (may be an alias)

        Returns:
            ToolDef or None if not found
        """
        # Resolve alias to canonical name
        canonical_name = self.normalizer.resolve(tool_name) if self.normalizer else tool_name
        return self.tools.get(canonical_name)

    def unregister_tool(self, tool_name: str) -> bool:
        """Unregister a single tool.

        Args:
            tool_name: Name of the tool to unregister

        Returns:
            True if the tool was unregistered, False if not found
        """
        canonical_name = self.normalizer.resolve(tool_name) if self.normalizer else tool_name

        if canonical_name in self.tools:
            del self.tools[canonical_name]
            # Remove from tool sources
            if canonical_name in self.tool_sources:
                del self.tool_sources[canonical_name]
            return True
        return False

    def unregister_module(self, plugin_id: str) -> list[str]:
        """Unregister all tools from a specific plugin.

        Args:
            plugin_id: Plugin ID whose tools should be unregistered

        Returns:
            List of tool names that were unregistered
        """
        removed = []
        for tool_name, source in list(self.tool_sources.items()):
            if source == plugin_id:
                del self.tools[tool_name]
                del self.tool_sources[tool_name]
                removed.append(tool_name)
        return removed

    def register_system_prompt(self, plugin_id: str, prompt: str) -> None:
        """Register a system prompt from a plugin.

        Args:
            plugin_id: Plugin registering the prompt
            prompt: The system prompt text
        """
        if prompt and prompt.strip():
            self.system_prompts[plugin_id] = prompt.strip()

    def unregister_system_prompt(self, plugin_id: str) -> bool:
        """Unregister a system prompt.

        Args:
            plugin_id: Plugin whose prompt to remove

        Returns:
            True if a prompt was removed, False if not found
        """
        if plugin_id in self.system_prompts:
            del self.system_prompts[plugin_id]
            return True
        return False

    def get_system_prompts(
        self,
        platform: str | None = None,
        allowed_modules: list[str] | None = None,
    ) -> str:
        """Get system prompts concatenated, optionally filtered by module.

        Args:
            platform: Optional platform filter (not currently used but reserved)
            allowed_modules: If provided, only include prompts from these modules

        Returns:
            Filtered system prompts joined with newlines
        """
        if not self.system_prompts:
            return ""

        if allowed_modules is not None:
            allowed_set = set(allowed_modules)
            prompts = [prompt for plugin_id, prompt in self.system_prompts.items() if plugin_id in allowed_set]
        else:
            prompts = list(self.system_prompts.values())

        return "\n\n".join(prompts)

    def get_tool_names(self) -> list[str]:
        """Get list of all registered tool names."""
        return list(self.tools.keys())

    def get_tools_by_module(self) -> dict[str, list[str]]:
        """Get a mapping of plugin IDs to their tool names."""
        result: dict[str, list[str]] = {}
        for tool_name, plugin_id in self.tool_sources.items():
            if plugin_id not in result:
                result[plugin_id] = []
            result[plugin_id].append(tool_name)
        return result

    async def execute(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        context: "ToolContext",
    ) -> str:
        """Execute a tool by name with policy enforcement.

        Args:
            tool_name: Name of the tool to execute (may be an alias)
            arguments: Arguments to pass to the tool handler
            context: Execution context with user/platform info

        Returns:
            Tool execution result as a string
        """
        # Resolve alias to canonical name
        canonical_name = self.normalizer.resolve(tool_name) if self.normalizer else tool_name

        tool = self.tools.get(canonical_name)
        if not tool:
            return f"Error: Unknown tool '{tool_name}'. " f"Available tools: {', '.join(self.tools.keys())}"

        # Check policy
        if self.policy_engine:
            policy_context = PolicyContext(
                user_id=context.user_id,
                platform=context.platform,
                channel_id=context.channel_id,
                extra=context.extra,
            )
            action = self.policy_engine.evaluate(canonical_name, policy_context)

            if action == PolicyAction.DENY:
                return f"Error: Access denied to tool '{tool_name}' by policy"
            elif action == PolicyAction.ASK:
                # For now, treat 'ask' as 'deny' with a different message
                # In a full implementation, this would prompt for confirmation
                return f"Error: Tool '{tool_name}' requires confirmation (not implemented)"

        # Emit TOOL_START hook
        await self.emit_hook(
            "tool_start",
            tool_name=canonical_name,
            arguments=arguments,
            user_id=context.user_id,
            session_key=context.extra.get("session_key"),
            platform=context.platform,
        )

        start_time = time.time()
        try:
            result = await tool.handler(arguments, context)

            # Emit TOOL_END hook on success
            await self.emit_hook(
                "tool_end",
                tool_name=canonical_name,
                arguments=arguments,
                result=result,
                duration_ms=(time.time() - start_time) * 1000,
                user_id=context.user_id,
                session_key=context.extra.get("session_key"),
                platform=context.platform,
            )

            return result

        except Exception as e:
            error_msg = f"Error executing {tool_name}: {str(e)}"
            logger.error(error_msg, exc_info=True)

            # Emit TOOL_ERROR hook
            await self.emit_hook(
                "tool_error",
                tool_name=canonical_name,
                arguments=arguments,
                error=str(e),
                traceback=traceback.format_exc(),
                user_id=context.user_id,
                session_key=context.extra.get("session_key"),
                platform=context.platform,
            )

            return error_msg

    async def resolve_factory_tools(
        self,
        context: PluginContext | None = None,
        allowlist: list[str] | None = None,
    ) -> list["ToolDef"]:
        """Resolve factory-based tools into actual tool definitions.

        Args:
            context: Context for tool creation
            allowlist: Optional list of allowed tool/plugin names

        Returns:
            List of resolved ToolDef objects
        """
        from .types import PluginContext

        if context is None:
            context = PluginContext()

        allowed_set = set(allowlist) if allowlist else set()

        resolved = []

        for registration in self.tool_registrations:
            plugin_record = self.plugins.get(registration.plugin_id)
            if not plugin_record or not plugin_record.enabled:
                continue

            # Check allowlist for optional tools
            if registration.optional:
                allowed = False

                # Check tool name (will be known after resolution)
                # Check plugin ID
                if allowed_set:
                    plugin_normalized = registration.plugin_id.lower().replace("-", "_")
                    if any(a.lower().replace("-", "_") == plugin_normalized for a in allowed_set):
                        allowed = True

                    # Check "group:plugins" catch-all
                    if "group:plugins" in [a.lower() for a in allowed_set]:
                        allowed = True

                if not allowed:
                    continue

            try:
                result = registration.factory(context)

                # Handle async factories
                if asyncio.iscoroutine(result):
                    result = await result

                if result is None:
                    continue

                from mypalclara.tools._base import ToolDef

                tools: list[ToolDef] = [result] if isinstance(result, ToolDef) else result

                for tool in tools:
                    if tool.name in self.tools:
                        self.push_diagnostic(
                            Diagnostic(
                                level=DiagnosticLevel.ERROR,
                                message=f"Tool '{tool.name}' already registered",
                                plugin_id=registration.plugin_id,
                                source=registration.source,
                            )
                        )
                        continue

                    self.tools[tool.name] = tool
                    plugin_record.tool_names.append(tool.name)
                    resolved.append(tool)

                    logger.debug(f"Resolved tool: {tool.name} from factory " f"({registration.plugin_id})")

            except Exception as e:
                logger.error(
                    f"Error resolving tool factory for {registration.plugin_id}: {e}",
                    exc_info=True,
                )
                self.push_diagnostic(
                    Diagnostic(
                        level=DiagnosticLevel.ERROR,
                        message=f"Tool factory error: {e}",
                        plugin_id=registration.plugin_id,
                        source=registration.source,
                    )
                )

        return resolved

    def get_diagnostics(self) -> list[Diagnostic]:
        """Get all diagnostic messages.

        Returns:
            List of Diagnostic objects
        """
        return self.diagnostics

    def is_initialized(self) -> bool:
        """Check if registry has been initialized.

        Returns:
            True if initialized
        """
        return self._initialized

    def __len__(self) -> int:
        """Return the number of registered tools."""
        return len(self.tools)

    def __contains__(self, tool_name: str) -> bool:
        """Check if a tool is registered (supports alias resolution)."""
        canonical_name = self.normalizer.resolve(tool_name) if self.normalizer else tool_name
        return canonical_name in self.tools
