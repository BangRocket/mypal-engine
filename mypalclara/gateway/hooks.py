"""Hook system for the Clara Gateway.

Hooks are automations triggered by gateway events. They can be:
- Shell commands executed in subprocess
- Python callables registered programmatically
- Loaded from a hooks configuration file

Configuration file format (hooks.yaml):
```yaml
hooks:
  - name: log-sessions
    event: session:start
    command: echo "Session started for ${USER_ID}"

  - name: backup-on-shutdown
    event: gateway:shutdown
    command: ./scripts/backup.sh
    timeout: 60

  - name: notify-errors
    event: tool:error
    command: curl -X POST https://webhook.example.com/error -d '{"error": "${ERROR}"}'
```
"""

from __future__ import annotations

import asyncio
import os
import shlex
import string
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Coroutine

import yaml

from mypalclara.config.logging import get_logger
from mypalclara.gateway.events import Event, EventEmitter, EventType, get_event_emitter

logger = get_logger("gateway.hooks")


class HookType(str, Enum):
    """Types of hooks."""

    SHELL = "shell"  # Execute shell command
    PYTHON = "python"  # Call Python function
    WEBHOOK = "webhook"  # HTTP webhook (future)


@dataclass
class HookResult:
    """Result of hook execution."""

    hook_name: str
    success: bool
    output: str = ""
    error: str | None = None
    duration_ms: int = 0
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class Hook:
    """A hook configuration."""

    name: str
    event: EventType | str
    type: HookType = HookType.SHELL

    # For shell hooks
    command: str | None = None
    timeout: float = 30.0
    working_dir: str | None = None

    # For Python hooks
    handler: Callable[[Event], Coroutine[Any, Any, None]] | None = None

    # Metadata
    enabled: bool = True
    description: str = ""
    priority: int = 0

    def __repr__(self) -> str:
        return f"Hook({self.name}, event={self.event}, type={self.type.value})"


class HookManager:
    """Manages hook registration and execution.

    Hooks are triggered by gateway events and can execute shell commands
    or Python callables.
    """

    def __init__(
        self,
        emitter: EventEmitter | None = None,
        hooks_dir: str | Path | None = None,
    ) -> None:
        """Initialize the hook manager.

        Args:
            emitter: Event emitter to subscribe to (uses global if None)
            hooks_dir: Directory containing hooks.yaml and hook scripts
        """
        self._emitter = emitter or get_event_emitter()
        self._hooks: dict[str, Hook] = {}
        self._results: list[HookResult] = []
        self._results_limit = 100
        self._hooks_dir = Path(hooks_dir) if hooks_dir else Path("hooks")

    def register(self, hook: Hook) -> None:
        """Register a hook.

        Args:
            hook: Hook configuration to register
        """
        if hook.name in self._hooks:
            logger.warning(f"Overwriting existing hook: {hook.name}")

        self._hooks[hook.name] = hook

        # Subscribe to the event
        event_key = hook.event.value if isinstance(hook.event, EventType) else hook.event
        self._emitter.on(event_key, self._create_handler(hook), priority=hook.priority)

        logger.info(f"Registered hook: {hook.name} for event {event_key}")

    def unregister(self, name: str) -> bool:
        """Unregister a hook.

        Args:
            name: Hook name to remove

        Returns:
            True if hook was found and removed
        """
        if name not in self._hooks:
            return False

        hook = self._hooks.pop(name)
        event_key = hook.event.value if isinstance(hook.event, EventType) else hook.event

        # Note: We can't easily unsubscribe the specific handler, but disabling works
        logger.info(f"Unregistered hook: {name}")
        return True

    def enable(self, name: str) -> bool:
        """Enable a hook.

        Args:
            name: Hook name

        Returns:
            True if hook was found
        """
        if name not in self._hooks:
            return False
        self._hooks[name].enabled = True
        return True

    def disable(self, name: str) -> bool:
        """Disable a hook.

        Args:
            name: Hook name

        Returns:
            True if hook was found
        """
        if name not in self._hooks:
            return False
        self._hooks[name].enabled = False
        return True

    def _create_handler(self, hook: Hook) -> Callable[[Event], Coroutine[Any, Any, None]]:
        """Create an event handler for a hook.

        Args:
            hook: The hook to create handler for

        Returns:
            Async handler function
        """

        async def handler(event: Event) -> None:
            if not hook.enabled:
                return
            await self._execute_hook(hook, event)

        return handler

    async def _execute_hook(self, hook: Hook, event: Event) -> HookResult:
        """Execute a hook.

        Args:
            hook: The hook to execute
            event: The triggering event

        Returns:
            Execution result
        """
        start = datetime.now()

        try:
            if hook.type == HookType.SHELL and hook.command:
                result = await self._execute_shell_hook(hook, event)
            elif hook.type == HookType.PYTHON and hook.handler:
                await hook.handler(event)
                result = HookResult(
                    hook_name=hook.name,
                    success=True,
                    output="Python handler completed",
                )
            else:
                result = HookResult(
                    hook_name=hook.name,
                    success=False,
                    error=f"Invalid hook configuration: type={hook.type}",
                )

        except asyncio.TimeoutError:
            result = HookResult(
                hook_name=hook.name,
                success=False,
                error=f"Timeout after {hook.timeout}s",
            )
        except Exception as e:
            logger.exception(f"Hook {hook.name} failed: {e}")
            result = HookResult(
                hook_name=hook.name,
                success=False,
                error=str(e),
            )

        # Calculate duration
        duration = datetime.now() - start
        result.duration_ms = int(duration.total_seconds() * 1000)

        # Store result
        self._results.append(result)
        if len(self._results) > self._results_limit:
            self._results.pop(0)

        # Log result
        if result.success:
            logger.debug(f"Hook {hook.name} completed in {result.duration_ms}ms")
        else:
            logger.warning(f"Hook {hook.name} failed: {result.error}")

        return result

    async def _execute_shell_hook(self, hook: Hook, event: Event) -> HookResult:
        """Execute a shell command hook.

        Args:
            hook: The hook with shell command
            event: The triggering event

        Returns:
            Execution result
        """
        if not hook.command:
            return HookResult(
                hook_name=hook.name,
                success=False,
                error="No command specified",
            )

        # Build environment with event data
        env = os.environ.copy()
        env["CLARA_EVENT_TYPE"] = event.type.value
        env["CLARA_TIMESTAMP"] = event.timestamp.isoformat()

        if event.node_id:
            env["CLARA_NODE_ID"] = event.node_id
        if event.platform:
            env["CLARA_PLATFORM"] = event.platform
        if event.user_id:
            env["CLARA_USER_ID"] = event.user_id
        if event.channel_id:
            env["CLARA_CHANNEL_ID"] = event.channel_id
        if event.request_id:
            env["CLARA_REQUEST_ID"] = event.request_id

        # Add event data as JSON
        import json

        env["CLARA_EVENT_DATA"] = json.dumps(event.data)

        # Also expose individual data keys
        for key, value in event.data.items():
            if isinstance(value, (str, int, float, bool)):
                env[f"CLARA_{key.upper()}"] = str(value)

        # Variable substitution in command
        command = self._substitute_vars(hook.command, env)

        # Determine working directory
        cwd = hook.working_dir or str(self._hooks_dir)

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                cwd=cwd,
            )

            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=hook.timeout,
            )

            output = stdout.decode("utf-8", errors="replace")
            error_output = stderr.decode("utf-8", errors="replace")

            if proc.returncode == 0:
                return HookResult(
                    hook_name=hook.name,
                    success=True,
                    output=output,
                )
            else:
                return HookResult(
                    hook_name=hook.name,
                    success=False,
                    output=output,
                    error=f"Exit code {proc.returncode}: {error_output}",
                )

        except asyncio.TimeoutError:
            proc.kill()
            raise

    def _substitute_vars(self, command: str, env: dict[str, str]) -> str:
        """Substitute ${VAR} patterns in command with environment values.

        Args:
            command: Command string with variables
            env: Environment dict

        Returns:
            Command with substitutions applied
        """
        # Use string.Template for ${VAR} syntax
        template = string.Template(command)
        try:
            return template.safe_substitute(env)
        except Exception:
            return command

    def load_from_file(self, path: str | Path | None = None) -> int:
        """Load hooks from a YAML configuration file.

        Args:
            path: Path to hooks.yaml (uses hooks_dir/hooks.yaml if None)

        Returns:
            Number of hooks loaded
        """
        if path is None:
            path = self._hooks_dir / "hooks.yaml"
        else:
            path = Path(path)

        if not path.exists():
            logger.debug(f"No hooks file at {path}")
            return 0

        try:
            with open(path) as f:
                config = yaml.safe_load(f)
        except Exception as e:
            logger.error(f"Failed to load hooks file: {e}")
            return 0

        if not config or "hooks" not in config:
            return 0

        count = 0
        for hook_config in config["hooks"]:
            try:
                hook = self._parse_hook_config(hook_config)
                self.register(hook)
                count += 1
            except Exception as e:
                logger.error(f"Failed to parse hook config: {e}")

        logger.info(f"Loaded {count} hooks from {path}")
        return count

    def _parse_hook_config(self, config: dict[str, Any]) -> Hook:
        """Parse a hook configuration dict.

        Args:
            config: Hook configuration from YAML

        Returns:
            Hook instance

        Raises:
            ValueError: If configuration is invalid
        """
        name = config.get("name")
        if not name:
            raise ValueError("Hook must have a name")

        event_str = config.get("event")
        if not event_str:
            raise ValueError("Hook must have an event")

        # Try to map to EventType enum
        try:
            event = EventType(event_str)
        except ValueError:
            event = event_str  # Custom event type

        return Hook(
            name=name,
            event=event,
            type=HookType(config.get("type", "shell")),
            command=config.get("command"),
            timeout=float(config.get("timeout", 30)),
            working_dir=config.get("working_dir"),
            enabled=config.get("enabled", True),
            description=config.get("description", ""),
            priority=int(config.get("priority", 0)),
        )

    def get_hooks(self) -> list[Hook]:
        """Get all registered hooks."""
        return list(self._hooks.values())

    def get_hook(self, name: str) -> Hook | None:
        """Get a specific hook by name."""
        return self._hooks.get(name)

    def get_results(self, limit: int = 50) -> list[HookResult]:
        """Get recent hook execution results.

        Args:
            limit: Maximum results to return

        Returns:
            List of results (newest first)
        """
        return list(reversed(self._results[-limit:]))

    def get_stats(self) -> dict[str, Any]:
        """Get hook manager statistics."""
        total = len(self._results)
        successful = sum(1 for r in self._results if r.success)
        failed = total - successful

        hooks_by_event: dict[str, int] = {}
        for hook in self._hooks.values():
            key = hook.event.value if isinstance(hook.event, EventType) else hook.event
            hooks_by_event[key] = hooks_by_event.get(key, 0) + 1

        return {
            "total_hooks": len(self._hooks),
            "enabled_hooks": sum(1 for h in self._hooks.values() if h.enabled),
            "hooks_by_event": hooks_by_event,
            "executions_total": total,
            "executions_successful": successful,
            "executions_failed": failed,
        }


# Global hook manager singleton
_manager: HookManager | None = None


def get_hook_manager() -> HookManager:
    """Get the global hook manager instance."""
    global _manager
    if _manager is None:
        _manager = HookManager()
    return _manager


def reset_hook_manager() -> None:
    """Reset the global hook manager (for testing)."""
    global _manager
    _manager = None


# Convenience decorator for Python hooks
def hook(
    event: EventType | str,
    name: str | None = None,
    priority: int = 0,
) -> Callable[[Callable[[Event], Coroutine[Any, Any, None]]], Callable[[Event], Coroutine[Any, Any, None]]]:
    """Decorator to register a Python function as a hook.

    Usage:
        @hook(EventType.SESSION_START)
        async def on_session_start(event: Event):
            print(f"Session started: {event.user_id}")

    Args:
        event: Event type to trigger on
        name: Hook name (defaults to function name)
        priority: Handler priority

    Returns:
        Decorator function
    """

    def decorator(
        func: Callable[[Event], Coroutine[Any, Any, None]],
    ) -> Callable[[Event], Coroutine[Any, Any, None]]:
        hook_name = name or func.__name__

        hook_obj = Hook(
            name=hook_name,
            event=event,
            type=HookType.PYTHON,
            handler=func,
            priority=priority,
        )

        get_hook_manager().register(hook_obj)
        return func

    return decorator
