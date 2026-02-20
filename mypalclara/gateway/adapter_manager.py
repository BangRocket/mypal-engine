"""Adapter subprocess management for Clara Gateway.

Manages adapter lifecycle: spawning, supervision, restart on failure.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

import yaml

from mypalclara.adapters.manifest import get_adapter, list_adapters
from mypalclara.config.logging import get_logger
from mypalclara.gateway.daemon import get_adapter_pidfile

logger = get_logger("adapter_manager")

DEFAULT_CONFIG_PATH = Path(__file__).parent / "adapters.yaml"


class AdapterState(Enum):
    """Adapter process states."""

    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    FAILED = "failed"
    DISABLED = "disabled"


@dataclass
class AdapterMetrics:
    """Metrics for tracking adapter lifecycle and performance."""

    total_starts: int = 0
    total_restarts: int = 0
    total_failures: int = 0
    last_start_time: float | None = None
    last_failure_time: float | None = None
    last_error_category: str | None = None
    total_uptime_seconds: float = 0.0
    current_uptime_start: float | None = None

    def record_start(self) -> None:
        """Record an adapter start."""
        self.total_starts += 1
        self.last_start_time = time.time()
        self.current_uptime_start = time.time()

    def record_restart(self) -> None:
        """Record an adapter restart."""
        self.total_restarts += 1
        self._accumulate_uptime()
        self.current_uptime_start = time.time()

    def record_failure(self, error_category: str | None = None) -> None:
        """Record an adapter failure."""
        self.total_failures += 1
        self.last_failure_time = time.time()
        self.last_error_category = error_category
        self._accumulate_uptime()

    def record_stop(self) -> None:
        """Record a clean adapter stop."""
        self._accumulate_uptime()

    def _accumulate_uptime(self) -> None:
        """Accumulate uptime from current session."""
        if self.current_uptime_start is not None:
            self.total_uptime_seconds += time.time() - self.current_uptime_start
            self.current_uptime_start = None

    def get_current_uptime(self) -> float:
        """Get current uptime including active session."""
        uptime = self.total_uptime_seconds
        if self.current_uptime_start is not None:
            uptime += time.time() - self.current_uptime_start
        return uptime

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "total_starts": self.total_starts,
            "total_restarts": self.total_restarts,
            "total_failures": self.total_failures,
            "last_start_time": self.last_start_time,
            "last_failure_time": self.last_failure_time,
            "last_error_category": self.last_error_category,
            "total_uptime_seconds": self.get_current_uptime(),
        }


class RestartPolicy(Enum):
    """Adapter restart policies."""

    ALWAYS = "always"  # Always restart on exit
    ON_FAILURE = "on_failure"  # Only restart on non-zero exit
    NEVER = "never"  # Never restart


@dataclass
class AdapterConfig:
    """Configuration for a single adapter."""

    name: str
    enabled: bool = True
    module: str = ""  # Python module path (e.g., "adapters.discord")
    env: dict[str, str] = field(default_factory=dict)
    restart_policy: RestartPolicy = RestartPolicy.ALWAYS
    restart_delay: float = 5.0  # Seconds to wait before restart
    max_restarts: int = 10  # Max restarts within reset_window
    reset_window: float = 300.0  # Seconds before restart count resets

    @classmethod
    def from_dict(cls, name: str, data: dict[str, Any]) -> "AdapterConfig":
        """Create config from dictionary."""
        restart_policy_str = data.get("restart_policy", "always")
        try:
            restart_policy = RestartPolicy(restart_policy_str)
        except ValueError:
            restart_policy = RestartPolicy.ALWAYS

        return cls(
            name=name,
            enabled=data.get("enabled", True),
            module=data.get("module", f"adapters.{name}"),
            env=data.get("env", {}),
            restart_policy=restart_policy,
            restart_delay=data.get("restart_delay", 5.0),
            max_restarts=data.get("max_restarts", 10),
            reset_window=data.get("reset_window", 300.0),
        )


@dataclass
class AdapterProcess:
    """Tracks a running adapter process."""

    config: AdapterConfig
    process: subprocess.Popen | None = None
    state: AdapterState = AdapterState.STOPPED
    restart_count: int = 0
    first_restart_time: float | None = None
    last_exit_code: int | None = None


class AdapterManager:
    """Manages adapter subprocesses with supervision and restart logic."""

    def __init__(self, config_path: str | Path | None = None):
        """Initialize the adapter manager.

        Args:
            config_path: Path to adapters.yaml config file
        """
        self.config_path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
        self.adapters: dict[str, AdapterProcess] = {}
        self._metrics: dict[str, AdapterMetrics] = {}
        self._running = False
        self._supervisor_task: asyncio.Task | None = None

    def load_config(self) -> dict[str, AdapterConfig]:
        """Load adapter configurations from YAML file.

        Returns:
            Dictionary of adapter name to config
        """
        configs: dict[str, AdapterConfig] = {}

        if not self.config_path.exists():
            logger.warning(f"Adapter config not found: {self.config_path}")
            return configs

        try:
            with open(self.config_path) as f:
                data = yaml.safe_load(f) or {}

            adapters_data = data.get("adapters", {})
            for name, adapter_data in adapters_data.items():
                # Expand environment variables in env dict
                env = adapter_data.get("env", {})
                expanded_env = {}
                for key, value in env.items():
                    if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
                        env_var = value[2:-1]
                        expanded_env[key] = os.environ.get(env_var, "")
                    else:
                        expanded_env[key] = str(value)
                adapter_data["env"] = expanded_env

                configs[name] = AdapterConfig.from_dict(name, adapter_data)

            logger.info(f"Loaded {len(configs)} adapter configurations")

        except Exception as e:
            logger.error(f"Failed to load adapter config: {e}")

        return configs

    def discover_from_manifest(self) -> dict[str, dict[str, Any]]:
        """Discover available adapters from the manifest registry.

        Returns:
            Dictionary of adapter name to manifest info
        """
        discovered: dict[str, dict[str, Any]] = {}

        try:
            registered_adapters = list_adapters()
            for adapter_name in registered_adapters:
                result = get_adapter(adapter_name)
                if result:
                    adapter_cls, manifest = result
                    discovered[adapter_name] = {
                        "class": adapter_cls,
                        "manifest": manifest,
                        "module": f"adapters.{adapter_name}",
                        "capabilities": manifest.capabilities,
                        "required_env": manifest.required_env,
                        "optional_env": manifest.optional_env,
                    }
            logger.info(f"Discovered {len(discovered)} adapters from manifest registry")
        except Exception as e:
            logger.warning(f"Failed to discover adapters from manifest: {e}")

        return discovered

    def check_adapter_env(self, name: str) -> tuple[bool, list[str]]:
        """Check if required environment variables are set for an adapter.

        Args:
            name: Adapter name

        Returns:
            Tuple of (all_set, missing_vars)
        """
        result = get_adapter(name)
        if not result:
            return True, []  # Unknown adapter, assume OK

        _, manifest = result
        missing = []
        for env_var in manifest.required_env:
            if not os.environ.get(env_var):
                missing.append(env_var)

        return len(missing) == 0, missing

    def get_adapter_manifest(self, name: str) -> dict[str, Any] | None:
        """Get manifest info for a specific adapter.

        Args:
            name: Adapter name

        Returns:
            Manifest data dict or None if not found
        """
        result = get_adapter(name)
        if not result:
            return None

        _, manifest = result
        return {
            "name": manifest.name,
            "platform": manifest.platform,
            "version": manifest.version,
            "display_name": manifest.display_name,
            "description": manifest.description,
            "icon": manifest.icon,
            "capabilities": manifest.capabilities,
            "required_env": manifest.required_env,
            "optional_env": manifest.optional_env,
        }

    async def start(self, adapter_names: list[str] | None = None) -> None:
        """Start the adapter manager and configured adapters.

        Args:
            adapter_names: Specific adapters to start, or None for all enabled
        """
        self._running = True

        # Load configurations
        configs = self.load_config()

        # Initialize adapter processes
        for name, config in configs.items():
            self.adapters[name] = AdapterProcess(config=config)

            if not config.enabled:
                self.adapters[name].state = AdapterState.DISABLED

        # Determine which adapters to start
        to_start = adapter_names if adapter_names else [name for name, ap in self.adapters.items() if ap.config.enabled]

        # Start requested adapters
        for name in to_start:
            if name in self.adapters:
                await self.start_adapter(name)
            else:
                logger.warning(f"Unknown adapter: {name}")

        # Start supervisor task
        self._supervisor_task = asyncio.create_task(self._supervise())

    async def stop(self) -> None:
        """Stop all adapters and the manager."""
        self._running = False

        # Cancel supervisor
        if self._supervisor_task:
            self._supervisor_task.cancel()
            try:
                await self._supervisor_task
            except asyncio.CancelledError:
                pass

        # Stop all adapters
        for name in list(self.adapters.keys()):
            await self.stop_adapter(name)

    async def start_adapter(self, name: str) -> bool:
        """Start a single adapter subprocess.

        Args:
            name: Adapter name

        Returns:
            True if started successfully
        """
        if name not in self.adapters:
            logger.error(f"Unknown adapter: {name}")
            return False

        ap = self.adapters[name]

        if ap.state == AdapterState.RUNNING:
            logger.warning(f"Adapter {name} already running")
            return True

        if ap.state == AdapterState.DISABLED:
            logger.info(f"Adapter {name} is disabled, skipping")
            return False

        # Check required environment variables
        env_ok, missing = self.check_adapter_env(name)
        if not env_ok:
            logger.error(f"Adapter {name} missing required environment variables: {', '.join(missing)}")
            ap.state = AdapterState.FAILED
            return False

        ap.state = AdapterState.STARTING
        config = ap.config

        # Build environment
        env = os.environ.copy()
        env.update(config.env)

        # Build command
        cmd = [sys.executable, "-m", config.module]

        try:
            logger.info(f"Starting adapter {name}: {' '.join(cmd)}")

            # Start subprocess
            process = subprocess.Popen(
                cmd,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                start_new_session=True,  # Create new process group
            )

            ap.process = process
            ap.state = AdapterState.RUNNING
            ap.last_exit_code = None

            # Write PID file
            pidfile = get_adapter_pidfile(name)
            with open(pidfile, "w") as f:
                f.write(str(process.pid))

            # Track metrics
            if name not in self._metrics:
                self._metrics[name] = AdapterMetrics()
            self._metrics[name].record_start()

            logger.info(f"Adapter {name} started (PID: {process.pid})")

            # Start output reader task
            asyncio.create_task(self._read_output(name, process))

            return True

        except Exception as e:
            logger.error(f"Failed to start adapter {name}: {e}")
            ap.state = AdapterState.FAILED
            return False

    async def stop_adapter(self, name: str, timeout: float = 10.0) -> bool:
        """Stop a single adapter subprocess.

        Args:
            name: Adapter name
            timeout: Seconds to wait before SIGKILL

        Returns:
            True if stopped successfully
        """
        if name not in self.adapters:
            logger.error(f"Unknown adapter: {name}")
            return False

        ap = self.adapters[name]

        if ap.state not in (AdapterState.RUNNING, AdapterState.STARTING):
            return True

        ap.state = AdapterState.STOPPING
        process = ap.process

        if process is None:
            ap.state = AdapterState.STOPPED
            return True

        try:
            # Send SIGTERM
            logger.info(f"Stopping adapter {name} (PID: {process.pid})")
            process.terminate()

            # Wait for graceful shutdown
            start = time.time()
            while time.time() - start < timeout:
                if process.poll() is not None:
                    break
                await asyncio.sleep(0.1)

            # Force kill if still running
            if process.poll() is None:
                logger.warning(f"Adapter {name} did not stop gracefully, killing...")
                process.kill()
                await asyncio.sleep(0.5)

            ap.last_exit_code = process.returncode
            ap.state = AdapterState.STOPPED
            ap.process = None

            # Clean up PID file
            pidfile = get_adapter_pidfile(name)
            if os.path.exists(pidfile):
                os.remove(pidfile)

            # Track metrics
            if name in self._metrics:
                self._metrics[name].record_stop()

            logger.info(f"Adapter {name} stopped (exit code: {ap.last_exit_code})")
            return True

        except Exception as e:
            logger.error(f"Error stopping adapter {name}: {e}")
            ap.state = AdapterState.FAILED
            # Track failure
            if name in self._metrics:
                self._metrics[name].record_failure("stop_error")
            return False

    async def restart_adapter(self, name: str) -> bool:
        """Restart an adapter.

        Args:
            name: Adapter name

        Returns:
            True if restarted successfully
        """
        await self.stop_adapter(name)
        await asyncio.sleep(0.5)
        return await self.start_adapter(name)

    def get_status(self) -> dict[str, dict]:
        """Get status of all adapters.

        Returns:
            Dictionary of adapter statuses
        """
        status = {}
        for name, ap in self.adapters.items():
            pid = ap.process.pid if ap.process and ap.process.poll() is None else None
            adapter_status = {
                "state": ap.state.value,
                "pid": pid,
                "enabled": ap.config.enabled,
                "restart_count": ap.restart_count,
                "last_exit_code": ap.last_exit_code,
            }

            # Include manifest info if available
            manifest_info = self.get_adapter_manifest(name)
            if manifest_info:
                adapter_status["manifest"] = manifest_info
                # Check env vars
                env_ok, missing = self.check_adapter_env(name)
                adapter_status["env_configured"] = env_ok
                if not env_ok:
                    adapter_status["missing_env"] = missing

            status[name] = adapter_status
        return status

    def get_metrics(self) -> dict[str, dict[str, Any]]:
        """Get metrics for all adapters.

        Returns:
            Dictionary of adapter name to metrics dict
        """
        metrics = {}
        for name, adapter_metrics in self._metrics.items():
            metrics[name] = adapter_metrics.to_dict()
        return metrics

    def get_adapter_metrics(self, name: str) -> AdapterMetrics | None:
        """Get metrics for a specific adapter.

        Args:
            name: Adapter name

        Returns:
            AdapterMetrics instance or None if not found
        """
        return self._metrics.get(name)

    async def _supervise(self) -> None:
        """Supervisor loop - monitors adapters and restarts on failure."""
        while self._running:
            try:
                await asyncio.sleep(1.0)

                for name, ap in self.adapters.items():
                    if ap.state != AdapterState.RUNNING:
                        continue

                    process = ap.process
                    if process is None:
                        continue

                    # Check if process exited
                    exit_code = process.poll()
                    if exit_code is not None:
                        ap.last_exit_code = exit_code
                        ap.state = AdapterState.STOPPED
                        logger.warning(f"Adapter {name} exited with code {exit_code}")

                        # Clean up PID file
                        pidfile = get_adapter_pidfile(name)
                        if os.path.exists(pidfile):
                            os.remove(pidfile)

                        # Check restart policy
                        should_restart = self._should_restart(ap, exit_code)

                        if should_restart:
                            # Update restart tracking
                            now = time.time()
                            if ap.first_restart_time is None or now - ap.first_restart_time > ap.config.reset_window:
                                ap.first_restart_time = now
                                ap.restart_count = 0

                            ap.restart_count += 1

                            if ap.restart_count > ap.config.max_restarts:
                                logger.error(
                                    f"Adapter {name} exceeded max restarts "
                                    f"({ap.config.max_restarts}), not restarting"
                                )
                                ap.state = AdapterState.FAILED
                                # Track failure
                                if name in self._metrics:
                                    self._metrics[name].record_failure("max_restarts_exceeded")
                                continue

                            # Track restart
                            if name in self._metrics:
                                self._metrics[name].record_restart()

                            logger.info(
                                f"Restarting adapter {name} in "
                                f"{ap.config.restart_delay}s "
                                f"(restart {ap.restart_count}/{ap.config.max_restarts})"
                            )
                            await asyncio.sleep(ap.config.restart_delay)
                            await self.start_adapter(name)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Supervisor error: {e}")

    def _should_restart(self, ap: AdapterProcess, exit_code: int) -> bool:
        """Determine if adapter should be restarted.

        Args:
            ap: Adapter process info
            exit_code: Process exit code

        Returns:
            True if should restart
        """
        policy = ap.config.restart_policy

        if policy == RestartPolicy.NEVER:
            return False
        if policy == RestartPolicy.ALWAYS:
            return True
        if policy == RestartPolicy.ON_FAILURE:
            return exit_code != 0

        return False

    async def _read_output(self, name: str, process: subprocess.Popen) -> None:
        """Read and log adapter output.

        Args:
            name: Adapter name
            process: Subprocess to read from
        """
        if process.stdout is None:
            return

        # Pattern to strip ANSI color codes
        ansi_pattern = re.compile(r"\x1b\[[0-9;]*m")

        # Pattern to match log format: "HH:MM:SS LEVEL     [logger] message"
        # Level is padded to 8 chars, logger name is in brackets
        log_pattern = re.compile(r"^(\d{2}:\d{2}:\d{2})\s+(\w+)\s+\[([^\]]+)\]\s*(.*)$")
        adapter_logger = get_logger(f"adapter.{name}")

        try:
            while True:
                line = await asyncio.get_event_loop().run_in_executor(None, process.stdout.readline)
                if not line:
                    break
                line_str = line.decode("utf-8", errors="replace").rstrip()
                if not line_str:
                    continue

                # Strip ANSI color codes before parsing
                clean_line = ansi_pattern.sub("", line_str)

                # Try to parse as a log line and re-log uniformly
                match = log_pattern.match(clean_line)
                if match:
                    level_str = match.group(2).upper()
                    message = match.group(4)
                    level = getattr(logging, level_str, logging.INFO)
                    adapter_logger.log(level, message)
                else:
                    # Non-log output (like "[logging] Initializing...")
                    adapter_logger.info(clean_line)
        except Exception:
            pass  # Process likely terminated


# Singleton instance
_manager: AdapterManager | None = None


def get_adapter_manager(config_path: str | Path | None = None) -> AdapterManager:
    """Get or create the singleton adapter manager.

    Args:
        config_path: Path to config file (only used on first call)

    Returns:
        AdapterManager instance
    """
    global _manager
    if _manager is None:
        _manager = AdapterManager(config_path)
    return _manager
