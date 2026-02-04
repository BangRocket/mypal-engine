"""Plugin system for Clara.

This module provides a unified plugin system inspired by OpenClaw's architecture.
Plugins can register tools, hooks, channels, providers, services, and commands.

Key concepts:
- Plugin: Any extension to the system (tools, MCP servers, channels, providers)
- Manifest: Metadata describing the plugin (id, version, config schema)
- Registry: Central registry managing all loaded plugins
- Hooks: Lifecycle events that plugins can respond to
- Policies: Access control for tools via groups and rules
- Normalization: Consistent tool naming with aliases
"""

from .audit import (
    AuditEntry,
    AuditLogger,
    get_audit_logger,
    init_audit_logger,
    reset_audit_logger,
)
from .hooks import (
    HOOK_EVENT_TYPES,
    HookEvent,
    LLMErrorEvent,
    LLMRequestEvent,
    LLMResponseEvent,
    MCPServerErrorEvent,
    MCPServerStartEvent,
    MCPServerStopEvent,
    MemoryReadEvent,
    MemoryWriteEvent,
    MessageReceivedEvent,
    MessageSendingEvent,
    MessageSendingResult,
    MessageSentEvent,
    SessionEndEvent,
    SessionStartEvent,
    SessionTimeoutEvent,
    ToolEndEvent,
    ToolErrorEvent,
    ToolStartEvent,
)
from .http_handler import (
    ToolInvocationRequest,
    ToolInvocationResponse,
    create_tool_router,
)
from .http_handler import (
    create_app as create_tool_api,
)
from .manifest import PLUGIN_MANIFEST_FILENAMES, load_plugin_manifest, resolve_manifest_path
from .normalization import (
    DEFAULT_ALIASES,
    ToolNameNormalizer,
    get_normalizer,
    normalize_tool_name,
    reset_normalizer,
    resolve_tool_name,
)
from .policies import (
    BUILTIN_GROUPS,
    EnhancedPolicyConfig,
    PolicyAction,
    PolicyContext,
    PolicyEngine,
    RateLimitConfig,
    RiskLevel,
    ToolIntent,
    ToolPolicy,
    get_policy_engine,
    reset_policy_engine,
)
from .registry import PluginRegistry
from .runtime import PluginRuntime
from .system import (
    emit_system_hooks,
    get_loader,
    get_plugin_status,
    get_registry,
    initialize_mcp_integration,
    initialize_plugins,
    reload_plugins,
    shutdown_plugins,
)
from .types import (
    Diagnostic,
    DiagnosticLevel,
    HookHandler,
    PluginAPI,
    PluginConfig,
    PluginContext,
    PluginKind,
    PluginManifest,
    PluginOrigin,
    PluginRecord,
    ToolFactory,
)
from .xml_tools import (
    ParsedFunctionCall,
    convert_to_openai_tool_calls,
    extract_text_after_function_calls,
    extract_text_before_function_calls,
    format_function_result,
    format_multiple_function_results,
    has_function_calls,
    parse_function_calls,
    tools_to_xml,
    tools_to_xml_from_dicts,
)

__all__ = [
    # Types
    "PluginManifest",
    "PluginConfig",
    "PluginRecord",
    "PluginContext",
    "PluginAPI",
    "PluginRegistry",
    "PluginRuntime",
    "ToolFactory",
    "HookHandler",
    "Diagnostic",
    "DiagnosticLevel",
    "PluginOrigin",
    "PluginKind",
    # Manifest
    "load_plugin_manifest",
    "resolve_manifest_path",
    "PLUGIN_MANIFEST_FILENAMES",
    # Policies
    "PolicyAction",
    "PolicyContext",
    "PolicyEngine",
    "ToolPolicy",
    "BUILTIN_GROUPS",
    "get_policy_engine",
    "reset_policy_engine",
    # Enhanced policy features
    "RiskLevel",
    "ToolIntent",
    "RateLimitConfig",
    "EnhancedPolicyConfig",
    # Audit logging
    "AuditEntry",
    "AuditLogger",
    "get_audit_logger",
    "init_audit_logger",
    "reset_audit_logger",
    # Normalization
    "ToolNameNormalizer",
    "DEFAULT_ALIASES",
    "get_normalizer",
    "reset_normalizer",
    "normalize_tool_name",
    "resolve_tool_name",
    # HTTP Handler
    "create_tool_router",
    "create_tool_api",
    "ToolInvocationRequest",
    "ToolInvocationResponse",
    # XML Tools (OpenClaw-style)
    "ParsedFunctionCall",
    "tools_to_xml",
    "tools_to_xml_from_dicts",
    "parse_function_calls",
    "has_function_calls",
    "extract_text_before_function_calls",
    "extract_text_after_function_calls",
    "format_function_result",
    "format_multiple_function_results",
    "convert_to_openai_tool_calls",
    # Hooks
    "HookEvent",
    "MessageReceivedEvent",
    "MessageSendingEvent",
    "MessageSentEvent",
    "MessageSendingResult",
    "ToolStartEvent",
    "ToolEndEvent",
    "ToolErrorEvent",
    "SessionStartEvent",
    "SessionEndEvent",
    "SessionTimeoutEvent",
    "LLMRequestEvent",
    "LLMResponseEvent",
    "LLMErrorEvent",
    "MemoryReadEvent",
    "MemoryWriteEvent",
    "MCPServerStartEvent",
    "MCPServerStopEvent",
    "MCPServerErrorEvent",
    "HOOK_EVENT_TYPES",
    # System
    "get_registry",
    "get_loader",
    "initialize_plugins",
    "initialize_mcp_integration",
    "reload_plugins",
    "get_plugin_status",
    "emit_system_hooks",
    "shutdown_plugins",
]
