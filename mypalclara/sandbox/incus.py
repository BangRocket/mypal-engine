"""
Incus-based code execution for Clara.

Provides sandboxed code execution via Incus containers or VMs.
Uses the incus CLI via subprocess for maximum compatibility.

Incus provides:
- System containers (lighter, faster startup)
- VMs (stronger isolation for untrusted code)

Usage:
    from mypalclara.sandbox.incus import IncusSandboxManager

    manager = IncusSandboxManager()
    result = await manager.execute_code(user_id, "print('Hello!')")

Environment variables:
    INCUS_SANDBOX_IMAGE - Base image (default: images:debian/12/cloud)
    INCUS_SANDBOX_TYPE - "container" or "vm" (default: container)
    INCUS_SANDBOX_TIMEOUT - Idle timeout in seconds (default: 900)
    INCUS_SANDBOX_MEMORY - Memory limit (default: 512MiB)
    INCUS_SANDBOX_CPU - CPU limit (default: 1)
    INCUS_REMOTE - Incus remote to use (default: local)
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from mypalclara.config.logging import get_logger

logger = get_logger("sandbox.incus")

# Check if incus CLI is available
INCUS_AVAILABLE = shutil.which("incus") is not None

# Configuration
INCUS_IMAGE = os.getenv("INCUS_SANDBOX_IMAGE", "images:debian/12/cloud")
INCUS_TYPE = os.getenv("INCUS_SANDBOX_TYPE", "container")  # "container" or "vm"
INCUS_TIMEOUT = int(os.getenv("INCUS_SANDBOX_TIMEOUT", "900"))
INCUS_MEMORY = os.getenv("INCUS_SANDBOX_MEMORY", "512MiB")
INCUS_CPU = os.getenv("INCUS_SANDBOX_CPU", "1")
INCUS_REMOTE = os.getenv("INCUS_REMOTE", "local")
SANDBOX_IDLE_TIMEOUT = INCUS_TIMEOUT

# Profile for Python development environment
PYTHON_PROFILE = "clara-python"


@dataclass
class IncusSession:
    """Tracks a user's Incus instance session."""

    instance_name: str
    user_id: str
    instance_type: str  # "container" or "vm"
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    last_used: datetime = field(default_factory=lambda: datetime.now(UTC))
    execution_count: int = 0
    installed_packages: set[str] = field(default_factory=set)


@dataclass
class ExecutionResult:
    """Result of code execution."""

    success: bool
    output: str
    error: str | None = None
    files: list[dict] = field(default_factory=list)
    execution_time: float = 0.0


class IncusSandboxManager:
    """Manages Incus container/VM sessions for users."""

    def __init__(self, instance_type: str | None = None):
        """Initialize the Incus sandbox manager.

        Args:
            instance_type: Override for instance type ("container" or "vm")
        """
        self.sessions: dict[str, IncusSession] = {}
        self._lock = asyncio.Lock()
        self.instance_type = instance_type or INCUS_TYPE
        self._profile_initialized = False

    async def _run_incus(
        self,
        *args: str,
        input_data: str | None = None,
        timeout: float = 60.0,
    ) -> tuple[int, str, str]:
        """Run an incus CLI command.

        Args:
            *args: Command arguments (after 'incus')
            input_data: Optional stdin data
            timeout: Command timeout in seconds

        Returns:
            Tuple of (return_code, stdout, stderr)
        """
        cmd = ["incus", *args]
        logger.debug(f"Running: {' '.join(cmd)}")

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE if input_data else None,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            stdout, stderr = await asyncio.wait_for(
                proc.communicate(input_data.encode() if input_data else None),
                timeout=timeout,
            )

            return (
                proc.returncode or 0,
                stdout.decode("utf-8", errors="replace"),
                stderr.decode("utf-8", errors="replace"),
            )

        except asyncio.TimeoutError:
            proc.kill()
            return -1, "", f"Command timed out after {timeout}s"
        except Exception as e:
            return -1, "", str(e)

    def is_available(self) -> bool:
        """Check if Incus is available."""
        if not INCUS_AVAILABLE:
            return False
        try:
            # Quick sync check for availability
            import subprocess

            result = subprocess.run(
                ["incus", "info"],
                capture_output=True,
                timeout=5,
            )
            return result.returncode == 0
        except Exception:
            return False

    async def _ensure_profile(self) -> bool:
        """Ensure the Clara Python profile exists.

        Creates a profile with Python and common tools pre-installed.
        """
        if self._profile_initialized:
            return True

        # Check if profile exists
        code, stdout, _ = await self._run_incus("profile", "list", "--format=json")
        if code == 0:
            profiles = json.loads(stdout)
            if any(p.get("name") == PYTHON_PROFILE for p in profiles):
                self._profile_initialized = True
                return True

        # Create profile with cloud-init for Python setup
        cloud_config = """#cloud-config
packages:
  - python3
  - python3-pip
  - python3-venv
  - git
  - curl
  - unzip

runcmd:
  - mkdir -p /home/user
  - chown 1000:1000 /home/user
"""

        # Create the profile
        code, _, stderr = await self._run_incus("profile", "create", PYTHON_PROFILE)
        if code != 0 and "already exists" not in stderr:
            logger.warning(f"Failed to create profile: {stderr}")
            return False

        # Set cloud-init config
        code, _, stderr = await self._run_incus(
            "profile",
            "set",
            PYTHON_PROFILE,
            "cloud-init.user-data",
            cloud_config,
        )
        if code != 0:
            logger.warning(f"Failed to set cloud-init: {stderr}")

        # Set resource limits
        await self._run_incus("profile", "set", PYTHON_PROFILE, f"limits.memory={INCUS_MEMORY}")
        await self._run_incus("profile", "set", PYTHON_PROFILE, f"limits.cpu={INCUS_CPU}")

        self._profile_initialized = True
        logger.info(f"Created Incus profile: {PYTHON_PROFILE}")
        return True

    def _instance_name(self, user_id: str) -> str:
        """Generate instance name for a user."""
        safe_id = "".join(c if c.isalnum() else "-" for c in user_id).lower()
        return f"clara-{safe_id}"

    async def get_sandbox(self, user_id: str) -> str | None:
        """Get or create an Incus instance for a user.

        Returns:
            Instance name if successful, None otherwise
        """
        if not self.is_available():
            return None

        async with self._lock:
            # Check for existing session
            if user_id in self.sessions:
                session = self.sessions[user_id]

                # Verify instance is still running
                code, stdout, _ = await self._run_incus(
                    "list",
                    session.instance_name,
                    "--format=json",
                )
                if code == 0:
                    instances = json.loads(stdout)
                    if instances and instances[0].get("status") == "Running":
                        session.last_used = datetime.now(UTC)
                        return session.instance_name

                # Instance not running, remove from sessions
                logger.info(f"Instance stopped for {user_id}, recreating")
                del self.sessions[user_id]

            # Ensure profile exists
            await self._ensure_profile()

            # Create new instance
            instance_name = self._instance_name(user_id)

            try:
                # Delete any existing stopped instance
                await self._run_incus("delete", instance_name, "--force")

                # Launch new instance
                launch_args = [
                    "launch",
                    INCUS_IMAGE,
                    instance_name,
                    f"--profile={PYTHON_PROFILE}",
                ]

                if self.instance_type == "vm":
                    launch_args.append("--vm")

                code, stdout, stderr = await self._run_incus(*launch_args, timeout=120)

                if code != 0:
                    logger.error(f"Failed to create instance for {user_id}: {stderr}")
                    return None

                # Wait for instance to be ready
                await self._wait_for_ready(instance_name)

                # Ensure Python is available (cloud-init may still be running)
                await self._ensure_python(instance_name)

                session = IncusSession(
                    instance_name=instance_name,
                    user_id=user_id,
                    instance_type=self.instance_type,
                )
                self.sessions[user_id] = session

                logger.info(f"Created Incus {self.instance_type} for {user_id}: {instance_name}")
                return instance_name

            except Exception as e:
                logger.exception(f"Failed to create instance for {user_id}: {e}")
                return None

    async def _wait_for_ready(self, instance_name: str, timeout: float = 60) -> bool:
        """Wait for instance to be ready for commands."""
        start = datetime.now(UTC)
        while (datetime.now(UTC) - start).total_seconds() < timeout:
            code, _, _ = await self._run_incus(
                "exec",
                instance_name,
                "--",
                "true",
                timeout=5,
            )
            if code == 0:
                return True
            await asyncio.sleep(2)
        return False

    async def _ensure_python(self, instance_name: str) -> None:
        """Ensure Python is installed in the instance."""
        # Check if Python is available
        code, _, _ = await self._run_incus(
            "exec",
            instance_name,
            "--",
            "python3",
            "--version",
            timeout=10,
        )

        if code != 0:
            # Install Python
            logger.info(f"Installing Python in {instance_name}")
            await self._run_incus(
                "exec",
                instance_name,
                "--",
                "sh",
                "-c",
                "apt-get update && apt-get install -y python3 python3-pip",
                timeout=120,
            )

    async def execute_code(
        self,
        user_id: str,
        code: str,
        description: str = "",
    ) -> ExecutionResult:
        """Execute Python code in a user's instance."""
        start_time = datetime.now(UTC)

        instance = await self.get_sandbox(user_id)
        if not instance:
            return ExecutionResult(
                success=False,
                output="",
                error="Incus sandbox not available. Is Incus running?",
            )

        try:
            # Write code to temp file via stdin
            script_path = "/tmp/script.py"
            write_code, _, write_err = await self._run_incus(
                "exec",
                instance,
                "--",
                "sh",
                "-c",
                f"cat > {script_path}",
                input_data=code,
            )

            if write_code != 0:
                return ExecutionResult(
                    success=False,
                    output="",
                    error=f"Failed to write script: {write_err}",
                )

            # Execute the script
            exit_code, stdout, stderr = await self._run_incus(
                "exec",
                instance,
                "--cwd=/home/user",
                "--",
                "python3",
                script_path,
                timeout=60,
            )

            # Update session stats
            if user_id in self.sessions:
                self.sessions[user_id].execution_count += 1
                self.sessions[user_id].last_used = datetime.now(UTC)

            elapsed = (datetime.now(UTC) - start_time).total_seconds()

            if exit_code != 0:
                return ExecutionResult(
                    success=False,
                    output=stdout,
                    error=stderr or f"Exit code: {exit_code}",
                    execution_time=elapsed,
                )

            return ExecutionResult(
                success=True,
                output=stdout or "(no output)",
                execution_time=elapsed,
            )

        except Exception as e:
            elapsed = (datetime.now(UTC) - start_time).total_seconds()
            return ExecutionResult(
                success=False,
                output="",
                error=str(e),
                execution_time=elapsed,
            )

    async def run_shell(self, user_id: str, command: str) -> ExecutionResult:
        """Run a shell command in a user's instance."""
        instance = await self.get_sandbox(user_id)
        if not instance:
            return ExecutionResult(
                success=False,
                output="",
                error="Sandbox not available",
            )

        try:
            exit_code, stdout, stderr = await self._run_incus(
                "exec",
                instance,
                "--cwd=/home/user",
                "--",
                "sh",
                "-c",
                command,
            )

            combined = stdout
            if stderr:
                combined += f"\n[stderr]: {stderr}"

            if exit_code != 0:
                return ExecutionResult(
                    success=False,
                    output=combined,
                    error=f"Exit code: {exit_code}",
                )

            return ExecutionResult(success=True, output=combined or "(no output)")

        except Exception as e:
            return ExecutionResult(success=False, output="", error=str(e))

    async def install_package(self, user_id: str, package: str) -> ExecutionResult:
        """Install a pip package in a user's instance."""
        # Normalize package name
        pkg_lower = package.lower().split("[")[0].split(">=")[0].split("==")[0].strip()

        # Check if already tracked
        session = self.sessions.get(user_id)
        if session and pkg_lower in session.installed_packages:
            return ExecutionResult(
                success=True,
                output=f"Package '{package}' is already installed",
                execution_time=0.0,
            )

        # Install
        result = await self.run_shell(user_id, f"pip3 install -q {package}")

        # Track on success
        if result.success and session:
            session.installed_packages.add(pkg_lower)

        return result

    async def ensure_packages(self, user_id: str, packages: list[str]) -> ExecutionResult:
        """Ensure multiple packages are installed (batch install)."""
        session = self.sessions.get(user_id)
        tracked = session.installed_packages if session else set()

        # Filter to only missing packages
        missing = []
        for pkg in packages:
            pkg_lower = pkg.lower().split("[")[0].split(">=")[0].split("==")[0].strip()
            if pkg_lower not in tracked:
                missing.append(pkg)

        if not missing:
            return ExecutionResult(
                success=True,
                output=f"All {len(packages)} packages already installed",
                execution_time=0.0,
            )

        # Install
        result = await self.run_shell(user_id, f"pip3 install -q {' '.join(missing)}")

        # Track on success
        if result.success and session:
            for pkg in missing:
                pkg_name = pkg.lower().split("[")[0].split(">=")[0].split("==")[0].strip()
                session.installed_packages.add(pkg_name)
            result = ExecutionResult(
                success=True,
                output=f"Installed {len(missing)} packages: {', '.join(missing)}",
                execution_time=result.execution_time,
            )

        return result

    async def read_file(self, user_id: str, path: str) -> ExecutionResult:
        """Read a file from a user's instance."""
        instance = await self.get_sandbox(user_id)
        if not instance:
            return ExecutionResult(success=False, output="", error="Sandbox not available")

        try:
            exit_code, stdout, stderr = await self._run_incus(
                "exec",
                instance,
                "--",
                "cat",
                path,
            )

            if exit_code != 0:
                return ExecutionResult(
                    success=False,
                    output="",
                    error=stderr or f"File not found: {path}",
                )

            return ExecutionResult(success=True, output=stdout)

        except Exception as e:
            return ExecutionResult(success=False, output="", error=str(e))

    async def write_file(self, user_id: str, path: str, content: str | bytes) -> ExecutionResult:
        """Write a file to a user's instance."""
        instance = await self.get_sandbox(user_id)
        if not instance:
            return ExecutionResult(success=False, output="", error="Sandbox not available")

        try:
            if isinstance(content, bytes):
                content = content.decode("utf-8", errors="replace")

            # Ensure directory exists
            dir_path = os.path.dirname(path)
            if dir_path:
                await self._run_incus(
                    "exec",
                    instance,
                    "--",
                    "mkdir",
                    "-p",
                    dir_path,
                )

            # Write via stdin
            exit_code, _, stderr = await self._run_incus(
                "exec",
                instance,
                "--",
                "sh",
                "-c",
                f"cat > '{path}'",
                input_data=content,
            )

            if exit_code != 0:
                return ExecutionResult(
                    success=False,
                    output="",
                    error=stderr or "Failed to write file",
                )

            return ExecutionResult(success=True, output=f"File written to {path}")

        except Exception as e:
            return ExecutionResult(success=False, output="", error=str(e))

    async def list_files(self, user_id: str, path: str = "/home/user") -> ExecutionResult:
        """List files in a directory in a user's instance."""
        instance = await self.get_sandbox(user_id)
        if not instance:
            return ExecutionResult(success=False, output="", error="Sandbox not available")

        try:
            exit_code, stdout, stderr = await self._run_incus(
                "exec",
                instance,
                "--",
                "ls",
                "-la",
                path,
            )

            if exit_code != 0:
                return ExecutionResult(
                    success=False,
                    output="",
                    error=stderr or f"Directory not found: {path}",
                )

            return ExecutionResult(success=True, output=stdout or "(empty directory)")

        except Exception as e:
            return ExecutionResult(success=False, output="", error=str(e))

    async def unzip_file(
        self,
        user_id: str,
        path: str,
        destination: str | None = None,
    ) -> ExecutionResult:
        """Extract an archive in a user's instance."""
        if not destination:
            destination = os.path.dirname(path) or "/home/user"

        # Build extraction command based on file extension
        path_lower = path.lower()
        if path_lower.endswith(".zip"):
            cmd = f"unzip -o '{path}' -d '{destination}'"
        elif path_lower.endswith(".tar.gz") or path_lower.endswith(".tgz"):
            cmd = f"tar -xzf '{path}' -C '{destination}'"
        elif path_lower.endswith(".tar.bz2"):
            cmd = f"tar -xjf '{path}' -C '{destination}'"
        elif path_lower.endswith(".tar"):
            cmd = f"tar -xf '{path}' -C '{destination}'"
        elif path_lower.endswith(".gz"):
            cmd = f"gunzip -k '{path}'"
        else:
            cmd = f"unzip -o '{path}' -d '{destination}' 2>/dev/null || tar -xf '{path}' -C '{destination}'"

        result = await self.run_shell(user_id, f"mkdir -p '{destination}' && {cmd}")

        if result.success:
            ls_result = await self.run_shell(user_id, f"ls -la '{destination}'")
            result.output += f"\n\nExtracted to {destination}:\n{ls_result.output}"

        return result

    async def handle_tool_call(
        self,
        user_id: str,
        tool_name: str,
        arguments: dict,
    ) -> ExecutionResult:
        """Handle a tool call from the LLM."""
        logger.debug(f"handle_tool_call: {tool_name} with args: {arguments}")

        try:
            if tool_name == "execute_python":
                code = arguments.get("code") or arguments.get("python_code") or arguments.get("script")
                if not code:
                    return ExecutionResult(
                        success=False,
                        output="",
                        error=f"Missing 'code' argument. Received: {list(arguments.keys())}",
                    )
                return await self.execute_code(user_id, code, arguments.get("description", ""))

            elif tool_name == "install_package":
                package = arguments.get("package") or arguments.get("name")
                if not package:
                    return ExecutionResult(
                        success=False,
                        output="",
                        error=f"Missing 'package' argument. Received: {list(arguments.keys())}",
                    )
                return await self.install_package(user_id, package)

            elif tool_name == "read_file":
                path = arguments.get("path") or arguments.get("file_path") or arguments.get("filename")
                if not path:
                    return ExecutionResult(
                        success=False,
                        output="",
                        error=f"Missing 'path' argument. Received: {list(arguments.keys())}",
                    )
                return await self.read_file(user_id, path)

            elif tool_name == "write_file":
                path = arguments.get("path") or arguments.get("file_path") or arguments.get("filename")
                content = arguments.get("content") or arguments.get("data") or arguments.get("text")
                if not path or content is None:
                    return ExecutionResult(
                        success=False,
                        output="",
                        error=f"Missing 'path' or 'content'. Received: {list(arguments.keys())}",
                    )
                return await self.write_file(user_id, path, content)

            elif tool_name == "list_files":
                return await self.list_files(
                    user_id,
                    arguments.get("path") or arguments.get("directory") or "/home/user",
                )

            elif tool_name == "run_shell":
                command = arguments.get("command") or arguments.get("cmd")
                if not command:
                    return ExecutionResult(
                        success=False,
                        output="",
                        error=f"Missing 'command' argument. Received: {list(arguments.keys())}",
                    )
                return await self.run_shell(user_id, command)

            elif tool_name == "unzip_file":
                path = arguments.get("path") or arguments.get("file_path") or arguments.get("archive")
                if not path:
                    return ExecutionResult(
                        success=False,
                        output="",
                        error=f"Missing 'path' argument. Received: {list(arguments.keys())}",
                    )
                return await self.unzip_file(
                    user_id,
                    path,
                    arguments.get("destination") or arguments.get("dest"),
                )

            else:
                return ExecutionResult(
                    success=False,
                    output="",
                    error=f"Unknown tool: {tool_name}",
                )

        except KeyError as e:
            return ExecutionResult(
                success=False,
                output="",
                error=f"Missing required argument {e}. Available: {list(arguments.keys())}",
            )

    async def cleanup_idle_sessions(self) -> int:
        """Clean up instances that have been idle too long."""
        async with self._lock:
            now = datetime.now(UTC)
            idle_threshold = timedelta(seconds=SANDBOX_IDLE_TIMEOUT)

            to_remove = []
            for user_id, session in self.sessions.items():
                if now - session.last_used > idle_threshold:
                    to_remove.append(user_id)

            for user_id in to_remove:
                session = self.sessions.pop(user_id)
                try:
                    await self._run_incus("stop", session.instance_name, "--force", timeout=10)
                    await self._run_incus("delete", session.instance_name, "--force", timeout=10)
                    logger.info(f"Cleaned up idle instance for {user_id}")
                except Exception as e:
                    logger.warning(f"Error cleaning up instance for {user_id}: {e}")

            return len(to_remove)

    async def cleanup_all(self) -> None:
        """Clean up all instance sessions."""
        async with self._lock:
            for user_id, session in list(self.sessions.items()):
                try:
                    await self._run_incus("stop", session.instance_name, "--force", timeout=10)
                    await self._run_incus("delete", session.instance_name, "--force", timeout=10)
                    logger.info(f"Cleaned up instance for {user_id}")
                except Exception as e:
                    logger.warning(f"Error cleaning up instance for {user_id}: {e}")
            self.sessions.clear()

    def get_stats(self) -> dict[str, Any]:
        """Get sandbox manager statistics."""
        return {
            "available": self.is_available(),
            "backend": "incus",
            "instance_type": self.instance_type,
            "active_sessions": len(self.sessions),
            "sessions": {
                user_id: {
                    "instance_name": session.instance_name,
                    "instance_type": session.instance_type,
                    "created_at": session.created_at.isoformat(),
                    "last_used": session.last_used.isoformat(),
                    "execution_count": session.execution_count,
                }
                for user_id, session in self.sessions.items()
            },
        }


# Global singleton instance
_incus_manager: IncusSandboxManager | None = None


def get_incus_manager(instance_type: str | None = None) -> IncusSandboxManager:
    """Get the global Incus sandbox manager instance."""
    global _incus_manager
    if _incus_manager is None:
        _incus_manager = IncusSandboxManager(instance_type=instance_type)
    return _incus_manager
