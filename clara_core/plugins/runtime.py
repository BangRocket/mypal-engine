"""Plugin runtime capabilities.

This module provides runtime functionality available to plugins,
such as logging, file I/O, state management, etc.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, TypeAlias

logger = logging.getLogger(__name__)


HookHandler: TypeAlias = Callable[..., Any]


@dataclass
class PluginRuntime:
    """Runtime capabilities available to plugins.

    Provides access to core system functionality.
    """

    logger: logging.Logger
    state_dir: Path
    config_dir: Path

    # Optional: state store
    _state_store: dict[str, Any] = field(default_factory=dict)

    def resolve_path(self, input_path: str) -> Path:
        """Resolve a path relative to config or state dir.

        Args:
            input_path: Path to resolve (can be absolute or relative)

        Returns:
            Resolved Path object
        """
        path = Path(input_path)

        # If absolute, return as-is
        if path.is_absolute():
            return path

        # Otherwise, resolve relative to config dir
        return self.config_dir / path

    def get_state(self, key: str, default: Any = None) -> Any:
        """Get a value from the plugin state store.

        Args:
            key: State key to retrieve
            default: Default value if key not found

        Returns:
            Stored value or default
        """
        return self._state_store.get(key, default)

    def set_state(self, key: str, value: Any) -> None:
        """Set a value in the plugin state store.

        Args:
            key: State key to set
            value: Value to store
        """
        self._state_store[key] = value

    async def run_command(
        self, command: str, timeout: int = 60, **kwargs: Any
    ) -> tuple[int, str, str]:
        """Run a shell command.

        Args:
            command: Command to execute
            timeout: Maximum execution time in seconds
            **kwargs: Additional arguments for subprocess

        Returns:
            (return_code, stdout, stderr) tuple
        """
        process = None
        try:
            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                **kwargs,
            )

            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=timeout
            )

            return process.returncode or 0, stdout.decode(), stderr.decode()

        except asyncio.TimeoutError:
            if process is not None:
                process.kill()
                try:
                    stdout, stderr = await process.communicate()
                except Exception:
                    stdout, stderr = b"", b""
                return (
                    -1,
                    stdout.decode(),
                    stderr.decode() or "Command timed out",
                )
            return -1, "", "Command timed out"
        except Exception as e:
            logger.error(f"Command execution error: {e}")
            return -1, "", str(e)

    def register_hook(self, event_name: str, handler: HookHandler) -> None:
        """Register a hook handler (placeholder for now).

        Actual hook registration will be handled by the registry.

        Args:
            event_name: Hook event name
            handler: Handler function
        """
        # This will be connected to the main registry
        logger.debug(f"Hook registered: {event_name}")
