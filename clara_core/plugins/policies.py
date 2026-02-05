"""Tool policies and access control.

This module provides a policy system for controlling tool access based on
groups, user context, and configurable rules.

Inspired by OpenClaw's policy system.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from .types import PluginContext

logger = logging.getLogger(__name__)


class PolicyAction(Enum):
    """Policy action to take."""

    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


class RiskLevel(Enum):
    """Tool risk level for policy evaluation."""

    SAFE = "safe"
    MODERATE = "moderate"
    DANGEROUS = "dangerous"


class ToolIntent(Enum):
    """Tool intent classification."""

    READ = "read"
    WRITE = "write"
    EXECUTE = "execute"
    NETWORK = "network"


@dataclass
class RateLimitConfig:
    """Configuration for rate limiting a tool or group of tools."""

    max_calls: int = 100
    window_seconds: int = 3600  # 1 hour


@dataclass
class EnhancedPolicyConfig:
    """Enhanced policy configuration with risk levels and rate limits.

    Attributes:
        max_risk_level: Maximum allowed risk level (default: DANGEROUS = all allowed)
        allowed_intents: List of allowed tool intents (default: all)
        rate_limits: Per-tool rate limit configurations
        require_admin_for_dangerous: Whether dangerous tools require admin
        audit_enabled: Whether to emit audit events
    """

    max_risk_level: RiskLevel = RiskLevel.DANGEROUS
    allowed_intents: list[ToolIntent] = field(default_factory=lambda: list(ToolIntent))
    rate_limits: dict[str, RateLimitConfig] = field(default_factory=dict)
    require_admin_for_dangerous: bool = True
    audit_enabled: bool = True


# Built-in tool groups mapping group names to tool names
BUILTIN_GROUPS: dict[str, list[str]] = {
    # Memory operations
    "group:memory": [
        "chat_history_search",
        "user_memory_search",
        "add_memory",
        "search_memories",
    ],
    # Web access
    "group:web": [
        "web_search",
        "fetch_url",
        "tavily_search",
    ],
    # File system operations
    "group:fs": [
        "read_file",
        "write_file",
        "list_directory",
        "save_to_local",
        "list_local_files",
        "read_local_file",
        "delete_local_file",
    ],
    # Sandbox/code execution
    "group:sandbox": [
        "execute_python",
        "execute_shell",
        "run_code",
    ],
    # GitHub operations
    "group:github": [
        "github_get_me",
        "github_search_repositories",
        "github_get_repository",
        "github_list_issues",
        "github_get_issue",
        "github_create_issue",
        "github_list_pull_requests",
        "github_get_pull_request",
        "github_create_pull_request",
        "github_list_commits",
        "github_get_commit",
        "github_get_file_contents",
        "github_create_or_update_file",
        "github_list_workflows",
        "github_list_workflow_runs",
        "github_run_workflow",
        "github_list_gists",
        "github_create_gist",
    ],
    # Google Workspace operations
    "group:google": [
        "google_connect",
        "google_status",
        "google_disconnect",
        "google_sheets_create",
        "google_sheets_read",
        "google_sheets_write",
        "google_sheets_append",
        "google_sheets_list",
        "google_drive_list",
        "google_drive_upload",
        "google_drive_download",
        "google_drive_create_folder",
        "google_drive_share",
        "google_docs_create",
        "google_docs_read",
        "google_docs_write",
        "google_calendar_list_events",
        "google_calendar_get_event",
        "google_calendar_create_event",
        "google_calendar_update_event",
        "google_calendar_delete_event",
        "google_calendar_list_calendars",
    ],
    # Email operations
    "group:email": [
        "email_connect_gmail",
        "email_connect_imap",
        "email_list_accounts",
        "email_disconnect",
        "email_set_alert_channel",
        "email_set_quiet_hours",
        "email_toggle_ping",
        "email_apply_preset",
        "email_list_presets",
        "email_add_rule",
        "email_list_rules",
        "email_remove_rule",
        "email_status",
        "email_recent_alerts",
    ],
    # Claude Code operations
    "group:claude_code": [
        "claude_code",
        "claude_code_status",
        "claude_code_set_workdir",
        "claude_code_get_workdir",
    ],
    # MCP operations (dynamically populated)
    "group:mcp": [],
    # Admin/management tools
    "group:admin": [
        "mcp_install",
        "mcp_uninstall",
        "mcp_enable",
        "mcp_disable",
        "mcp_restart",
    ],
}


@dataclass
class ToolPolicy:
    """A policy rule for tool access control.

    Attributes:
        name: Human-readable name for the policy
        groups: List of group names this policy applies to
        tools: List of specific tool names this policy applies to
        action: Action to take (allow, deny, ask)
        conditions: Additional conditions for the policy
        priority: Higher priority policies are evaluated first
    """

    name: str
    groups: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    action: PolicyAction = PolicyAction.ALLOW
    conditions: dict[str, Any] = field(default_factory=dict)
    priority: int = 0

    def matches_tool(self, tool_name: str, groups: dict[str, list[str]]) -> bool:
        """Check if this policy matches a tool.

        Args:
            tool_name: Name of the tool to check
            groups: Mapping of group names to tool lists

        Returns:
            True if this policy applies to the tool
        """
        # Direct tool match
        if tool_name in self.tools:
            return True

        # Group match
        for group_name in self.groups:
            group_tools = groups.get(group_name, [])
            if tool_name in group_tools:
                return True

        return False


@dataclass
class PolicyContext:
    """Context for policy evaluation.

    Contains information about the request that can be used
    in policy condition evaluation.
    """

    user_id: str | None = None
    platform: str | None = None
    channel_id: str | None = None
    session_key: str | None = None
    roles: list[str] = field(default_factory=list)
    is_admin: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def has_admin(self) -> bool:
        """Alias for is_admin for compatibility."""
        return self.is_admin

    @classmethod
    def from_plugin_context(cls, ctx: "PluginContext") -> "PolicyContext":
        """Create PolicyContext from PluginContext.

        Args:
            ctx: PluginContext to convert

        Returns:
            PolicyContext with values from PluginContext
        """
        return cls(
            user_id=ctx.user_id,
            platform=ctx.platform,
            channel_id=ctx.message_channel,
            session_key=ctx.session_key,
            roles=ctx.extra.get("roles", []),
            is_admin=ctx.extra.get("is_admin", False),
            extra=ctx.extra,
        )


class PolicyEngine:
    """Engine for evaluating tool access policies.

    Manages a collection of policies and evaluates them
    against tool requests. Supports enhanced features:
    - Risk level evaluation
    - Intent classification
    - Rate limiting
    - Audit logging hooks
    """

    def __init__(self, config: EnhancedPolicyConfig | None = None) -> None:
        """Initialize the policy engine.

        Args:
            config: Optional enhanced policy configuration
        """
        self._policies: list[ToolPolicy] = []
        self._groups: dict[str, list[str]] = dict(BUILTIN_GROUPS)
        self._default_action = PolicyAction.ALLOW
        self._config = config or EnhancedPolicyConfig()

        # Rate limiting state: tool_name -> user_id -> list of call timestamps
        self._call_counts: dict[str, dict[str, list[datetime]]] = defaultdict(lambda: defaultdict(list))

        # Audit callbacks
        self._audit_callbacks: list[callable] = []

    def add_policy(self, policy: ToolPolicy) -> None:
        """Add a policy to the engine.

        Args:
            policy: Policy to add
        """
        self._policies.append(policy)
        # Keep sorted by priority (descending)
        self._policies.sort(key=lambda p: p.priority, reverse=True)
        logger.debug(f"Added policy: {policy.name} (priority {policy.priority})")

    def remove_policy(self, name: str) -> bool:
        """Remove a policy by name.

        Args:
            name: Name of the policy to remove

        Returns:
            True if a policy was removed
        """
        original_len = len(self._policies)
        self._policies = [p for p in self._policies if p.name != name]
        return len(self._policies) < original_len

    def clear_policies(self) -> None:
        """Remove all policies."""
        self._policies.clear()

    def register_group(self, group_name: str, tool_names: list[str]) -> None:
        """Register or update a tool group.

        Args:
            group_name: Name of the group (should start with "group:")
            tool_names: List of tool names in the group
        """
        if not group_name.startswith("group:"):
            group_name = f"group:{group_name}"
        self._groups[group_name] = tool_names
        logger.debug(f"Registered group: {group_name} with {len(tool_names)} tools")

    def add_to_group(self, group_name: str, tool_name: str) -> None:
        """Add a tool to a group.

        Args:
            group_name: Name of the group
            tool_name: Tool to add
        """
        if not group_name.startswith("group:"):
            group_name = f"group:{group_name}"
        if group_name not in self._groups:
            self._groups[group_name] = []
        if tool_name not in self._groups[group_name]:
            self._groups[group_name].append(tool_name)

    def get_groups(self) -> dict[str, list[str]]:
        """Get all registered groups.

        Returns:
            Dict mapping group names to tool lists
        """
        return dict(self._groups)

    def get_tools_in_group(self, group_name: str) -> list[str]:
        """Get all tools in a group.

        Args:
            group_name: Name of the group

        Returns:
            List of tool names in the group
        """
        if not group_name.startswith("group:"):
            group_name = f"group:{group_name}"
        return list(self._groups.get(group_name, []))

    def set_default_action(self, action: PolicyAction) -> None:
        """Set the default action when no policy matches.

        Args:
            action: Default action to take
        """
        self._default_action = action

    def evaluate(
        self,
        tool_name: str,
        context: PolicyContext | None = None,
    ) -> PolicyAction:
        """Evaluate policies for a tool request.

        Args:
            tool_name: Name of the tool being requested
            context: Context for condition evaluation

        Returns:
            PolicyAction indicating what to do
        """
        context = context or PolicyContext()

        for policy in self._policies:
            if not policy.matches_tool(tool_name, self._groups):
                continue

            # Check conditions
            if not self._evaluate_conditions(policy.conditions, context):
                continue

            logger.debug(f"Policy '{policy.name}' matched tool '{tool_name}' -> {policy.action}")
            return policy.action

        # No policy matched - use default
        logger.debug(f"No policy matched tool '{tool_name}' -> {self._default_action}")
        return self._default_action

    def _evaluate_conditions(
        self,
        conditions: dict[str, Any],
        context: PolicyContext,
    ) -> bool:
        """Evaluate policy conditions against context.

        Args:
            conditions: Conditions to evaluate
            context: Context to evaluate against

        Returns:
            True if all conditions are satisfied
        """
        if not conditions:
            return True

        # Check user_id condition
        if "user_id" in conditions:
            allowed_users = conditions["user_id"]
            if isinstance(allowed_users, str):
                allowed_users = [allowed_users]
            if context.user_id not in allowed_users:
                return False

        # Check platform condition
        if "platform" in conditions:
            allowed_platforms = conditions["platform"]
            if isinstance(allowed_platforms, str):
                allowed_platforms = [allowed_platforms]
            if context.platform not in allowed_platforms:
                return False

        # Check roles condition
        if "roles" in conditions:
            required_roles = conditions["roles"]
            if isinstance(required_roles, str):
                required_roles = [required_roles]
            if not any(role in context.roles for role in required_roles):
                return False

        # Check is_admin condition
        if "is_admin" in conditions:
            if conditions["is_admin"] != context.is_admin:
                return False

        return True

    def get_allowed_tools(
        self,
        all_tools: list[str],
        context: PolicyContext | None = None,
    ) -> list[str]:
        """Filter a list of tools to only those allowed by policy.

        Args:
            all_tools: List of all tool names
            context: Context for evaluation

        Returns:
            List of allowed tool names
        """
        return [tool for tool in all_tools if self.evaluate(tool, context) == PolicyAction.ALLOW]

    # ==================== Enhanced Policy Methods ====================

    def set_config(self, config: EnhancedPolicyConfig) -> None:
        """Set the enhanced policy configuration.

        Args:
            config: New configuration to use
        """
        self._config = config
        logger.info(
            f"Policy config updated: max_risk={config.max_risk_level.value}, "
            f"require_admin_for_dangerous={config.require_admin_for_dangerous}"
        )

    def get_config(self) -> EnhancedPolicyConfig:
        """Get the current enhanced policy configuration."""
        return self._config

    def check_risk_level(
        self,
        tool_risk_level: str,
        context: PolicyContext,
    ) -> tuple[bool, str]:
        """Check if a tool's risk level is allowed.

        Args:
            tool_risk_level: Tool's risk level ("safe", "moderate", "dangerous")
            context: Policy context with user info

        Returns:
            (allowed, reason) - Whether allowed and explanation if not
        """
        try:
            tool_risk = RiskLevel(tool_risk_level)
        except ValueError:
            # Unknown risk level, treat as dangerous
            tool_risk = RiskLevel.DANGEROUS

        max_risk = self._config.max_risk_level
        risk_order = [RiskLevel.SAFE, RiskLevel.MODERATE, RiskLevel.DANGEROUS]

        if risk_order.index(tool_risk) > risk_order.index(max_risk):
            return False, f"Tool risk level '{tool_risk_level}' exceeds maximum allowed '{max_risk.value}'"

        # Require admin for dangerous tools if configured
        if tool_risk == RiskLevel.DANGEROUS and self._config.require_admin_for_dangerous and not context.has_admin:
            return False, "Dangerous tools require admin privileges"

        return True, ""

    def check_intent(
        self,
        tool_intent: str,
        context: PolicyContext,
    ) -> tuple[bool, str]:
        """Check if a tool's intent is allowed.

        Args:
            tool_intent: Tool's intent ("read", "write", "execute", "network")
            context: Policy context

        Returns:
            (allowed, reason)
        """
        try:
            intent = ToolIntent(tool_intent)
        except ValueError:
            # Unknown intent, allow by default
            return True, ""

        if intent not in self._config.allowed_intents:
            return False, f"Tool intent '{tool_intent}' not allowed by policy"

        return True, ""

    def check_rate_limit(
        self,
        tool_name: str,
        user_id: str | None,
    ) -> tuple[bool, str]:
        """Check if rate limit allows execution.

        Args:
            tool_name: Name of the tool
            user_id: User ID for per-user limits

        Returns:
            (allowed, reason)
        """
        if user_id is None:
            return True, ""

        limit_config = self._config.rate_limits.get(tool_name)
        if not limit_config:
            return True, ""

        now = datetime.now()
        calls = self._call_counts[tool_name][user_id]

        # Remove old calls outside window
        cutoff = now - timedelta(seconds=limit_config.window_seconds)
        calls[:] = [t for t in calls if t > cutoff]

        if len(calls) >= limit_config.max_calls:
            return False, (
                f"Rate limit exceeded: {limit_config.max_calls} calls per "
                f"{limit_config.window_seconds}s for tool '{tool_name}'"
            )

        return True, ""

    def record_call(self, tool_name: str, user_id: str | None) -> None:
        """Record a tool call for rate limiting.

        Args:
            tool_name: Name of the tool called
            user_id: User ID who called it
        """
        if user_id is None:
            return
        self._call_counts[tool_name][user_id].append(datetime.now())

    def set_rate_limit(
        self,
        tool_name: str,
        max_calls: int,
        window_seconds: int = 3600,
    ) -> None:
        """Set rate limit for a specific tool.

        Args:
            tool_name: Name of the tool
            max_calls: Maximum calls allowed in the window
            window_seconds: Time window in seconds
        """
        self._config.rate_limits[tool_name] = RateLimitConfig(
            max_calls=max_calls,
            window_seconds=window_seconds,
        )
        logger.debug(f"Rate limit set for {tool_name}: {max_calls}/{window_seconds}s")

    def clear_rate_limits(self) -> None:
        """Clear all rate limit state."""
        self._call_counts.clear()

    def register_audit_callback(self, callback: callable) -> None:
        """Register a callback to be called for audit events.

        Args:
            callback: Function(tool_name, user_id, action, context, result_status, error_message)
        """
        self._audit_callbacks.append(callback)

    async def emit_audit_event(
        self,
        tool_name: str,
        context: PolicyContext,
        action: PolicyAction,
        result_status: str = "allowed",
        error_message: str | None = None,
        tool_risk_level: str = "safe",
        tool_intent: str = "read",
        execution_time_ms: int | None = None,
        parameters: dict[str, Any] | None = None,
    ) -> None:
        """Emit an audit event to all registered callbacks.

        Args:
            tool_name: Name of the tool
            context: Policy context
            action: Policy action taken
            result_status: Result status ("allowed", "denied", "success", "error")
            error_message: Error message if applicable
            tool_risk_level: Tool's risk level
            tool_intent: Tool's intent
            execution_time_ms: Execution time in milliseconds
            parameters: Tool parameters (for logging)
        """
        if not self._config.audit_enabled:
            return

        import asyncio

        for callback in self._audit_callbacks:
            try:
                result = callback(
                    tool_name=tool_name,
                    user_id=context.user_id,
                    platform=context.platform,
                    action=action.value,
                    result_status=result_status,
                    error_message=error_message,
                    risk_level=tool_risk_level,
                    intent=tool_intent,
                    execution_time_ms=execution_time_ms,
                    parameters=parameters,
                )
                if asyncio.iscoroutine(result):
                    await result
            except Exception as e:
                logger.error(f"Audit callback error: {e}")

    def evaluate_enhanced(
        self,
        tool_name: str,
        tool_risk_level: str,
        tool_intent: str,
        context: PolicyContext | None = None,
    ) -> tuple[PolicyAction, str]:
        """Evaluate policies with enhanced checks including risk and intent.

        Args:
            tool_name: Name of the tool being requested
            tool_risk_level: Tool's risk level
            tool_intent: Tool's intent
            context: Context for condition evaluation

        Returns:
            (PolicyAction, reason) tuple
        """
        context = context or PolicyContext()

        # First check basic policy rules
        action = self.evaluate(tool_name, context)
        if action != PolicyAction.ALLOW:
            return action, "Denied by policy rule"

        # Check risk level
        allowed, reason = self.check_risk_level(tool_risk_level, context)
        if not allowed:
            return PolicyAction.DENY, reason

        # Check intent
        allowed, reason = self.check_intent(tool_intent, context)
        if not allowed:
            return PolicyAction.DENY, reason

        # Check rate limit
        allowed, reason = self.check_rate_limit(tool_name, context.user_id)
        if not allowed:
            return PolicyAction.DENY, reason

        return PolicyAction.ALLOW, ""


# Global policy engine singleton
_policy_engine: PolicyEngine | None = None


def get_policy_engine() -> PolicyEngine:
    """Get the global policy engine singleton.

    Returns:
        PolicyEngine instance
    """
    global _policy_engine
    if _policy_engine is None:
        _policy_engine = PolicyEngine()
    return _policy_engine


def reset_policy_engine() -> None:
    """Reset the global policy engine. Useful for testing."""
    global _policy_engine
    _policy_engine = None
