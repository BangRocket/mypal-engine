"""Unified sandbox manager for Clara.

Provides a single interface that can use Docker or Incus.
Automatically selects the appropriate backend based on configuration.
"""

from __future__ import annotations

import os
from typing import Any

from .docker import DOCKER_AVAILABLE, DockerSandboxManager, ExecutionResult
from .incus import INCUS_AVAILABLE, IncusSandboxManager

# Mode configuration
# - "docker" or "local": Use local Docker
# - "incus": Use Incus containers
# - "incus-vm": Use Incus VMs (stronger isolation)
# - "auto": Use Incus if available, fall back to Docker
SANDBOX_MODE = os.getenv("SANDBOX_MODE", "auto")


class UnifiedSandboxManager:
    """Unified sandbox manager that switches between Docker and Incus.

    Mode selection:
    - "docker" or "local": Use local Docker containers
    - "incus": Use Incus containers (lighter, faster)
    - "incus-vm": Use Incus VMs (stronger isolation for untrusted code)
    - "auto" (default): Use Incus if available, fall back to Docker

    Usage:
        manager = get_sandbox_manager()
        result = await manager.execute_code("user123", "print('hello')")

    Environment variables:
        SANDBOX_MODE: "docker", "incus", "incus-vm", or "auto"
        INCUS_SANDBOX_IMAGE: Base image for Incus (default: images:debian/12/cloud)
        INCUS_SANDBOX_TYPE: "container" or "vm" (default: container)
    """

    def __init__(self, mode: str | None = None):
        self.mode = mode or SANDBOX_MODE
        self._docker: DockerSandboxManager | None = None
        self._incus: IncusSandboxManager | None = None
        self._active_backend: str | None = None

    @property
    def local(self) -> DockerSandboxManager:
        """Get local Docker manager (lazy initialization). Alias for docker."""
        return self.docker

    @property
    def docker(self) -> DockerSandboxManager:
        """Get Docker manager (lazy initialization)."""
        if self._docker is None:
            self._docker = DockerSandboxManager()
        return self._docker

    @property
    def incus(self) -> IncusSandboxManager:
        """Get Incus manager (lazy initialization)."""
        if self._incus is None:
            # Use VM mode if explicitly requested
            instance_type = "vm" if self.mode == "incus-vm" else "container"
            self._incus = IncusSandboxManager(instance_type=instance_type)
        return self._incus

    def _select_backend(self) -> str:
        """Select which backend to use based on mode and availability."""
        if self.mode in ("local", "docker"):
            if DOCKER_AVAILABLE and self.docker.is_available():
                return "docker"
            return "none"

        elif self.mode in ("incus", "incus-vm"):
            if INCUS_AVAILABLE and self.incus.is_available():
                return "incus"
            return "none"

        else:  # auto
            # Try Incus first (if available)
            if INCUS_AVAILABLE and self.incus.is_available():
                return "incus"
            # Fall back to Docker
            if DOCKER_AVAILABLE and self.docker.is_available():
                return "docker"
            return "none"

    def is_available(self) -> bool:
        """Check if any sandbox backend is available."""
        return self._select_backend() != "none"

    async def health_check(self) -> bool:
        """Check if the active backend is healthy."""
        backend = self._select_backend()
        if backend == "docker":
            return self.docker.is_available()
        elif backend == "incus":
            return self.incus.is_available()
        return False

    def _get_manager(self) -> DockerSandboxManager | IncusSandboxManager | None:
        """Get the active manager based on mode."""
        backend = self._select_backend()
        self._active_backend = backend
        if backend == "docker":
            return self.docker
        elif backend == "incus":
            return self.incus
        return None

    # =========================================================================
    # Forward all methods to the active backend
    # =========================================================================

    async def execute_code(
        self,
        user_id: str,
        code: str,
        description: str = "",
    ) -> ExecutionResult:
        """Execute Python code in the sandbox."""
        manager = self._get_manager()
        if not manager:
            return ExecutionResult(
                success=False,
                output="",
                error="No sandbox backend available",
            )
        return await manager.execute_code(user_id, code, description)

    async def run_shell(
        self,
        user_id: str,
        command: str,
    ) -> ExecutionResult:
        """Run shell command in the sandbox."""
        manager = self._get_manager()
        if not manager:
            return ExecutionResult(
                success=False,
                output="",
                error="No sandbox backend available",
            )
        return await manager.run_shell(user_id, command)

    async def install_package(
        self,
        user_id: str,
        package: str,
    ) -> ExecutionResult:
        """Install pip package in the sandbox."""
        manager = self._get_manager()
        if not manager:
            return ExecutionResult(
                success=False,
                output="",
                error="No sandbox backend available",
            )
        return await manager.install_package(user_id, package)

    async def ensure_packages(
        self,
        user_id: str,
        packages: list[str],
    ) -> ExecutionResult:
        """Ensure multiple packages are installed (batch install)."""
        manager = self._get_manager()
        if not manager:
            return ExecutionResult(
                success=False,
                output="",
                error="No sandbox backend available",
            )
        if hasattr(manager, "ensure_packages"):
            return await manager.ensure_packages(user_id, packages)
        # Fall back to individual installs
        for pkg in packages:
            result = await manager.install_package(user_id, pkg)
            if not result.success:
                return result
        return ExecutionResult(
            success=True,
            output=f"All {len(packages)} packages installed",
        )

    async def read_file(
        self,
        user_id: str,
        path: str,
    ) -> ExecutionResult:
        """Read file from the sandbox."""
        manager = self._get_manager()
        if not manager:
            return ExecutionResult(
                success=False,
                output="",
                error="No sandbox backend available",
            )
        return await manager.read_file(user_id, path)

    async def write_file(
        self,
        user_id: str,
        path: str,
        content: str | bytes,
    ) -> ExecutionResult:
        """Write file to the sandbox."""
        manager = self._get_manager()
        if not manager:
            return ExecutionResult(
                success=False,
                output="",
                error="No sandbox backend available",
            )
        return await manager.write_file(user_id, path, content)

    async def list_files(
        self,
        user_id: str,
        path: str = "/home/user",
    ) -> ExecutionResult:
        """List files in the sandbox."""
        manager = self._get_manager()
        if not manager:
            return ExecutionResult(
                success=False,
                output="",
                error="No sandbox backend available",
            )
        return await manager.list_files(user_id, path)

    async def unzip_file(
        self,
        user_id: str,
        path: str,
        destination: str | None = None,
    ) -> ExecutionResult:
        """Extract archive in the sandbox."""
        manager = self._get_manager()
        if not manager:
            return ExecutionResult(
                success=False,
                output="",
                error="No sandbox backend available",
            )
        return await manager.unzip_file(user_id, path, destination)

    async def web_search(
        self,
        query: str,
        max_results: int = 5,
        search_depth: str = "basic",
    ) -> ExecutionResult:
        """Web search (only available on Docker backend)."""
        manager = self._get_manager()
        if not manager:
            return ExecutionResult(
                success=False,
                output="",
                error="No sandbox backend available",
            )

        # Web search is only available on Docker sandbox
        if self._active_backend == "docker" and hasattr(manager, "web_search"):
            return await manager.web_search(query, max_results, search_depth)
        else:
            return ExecutionResult(
                success=False,
                output="",
                error="Web search only available on Docker sandbox.",
            )

    async def handle_tool_call(
        self,
        user_id: str,
        tool_name: str,
        arguments: dict,
    ) -> ExecutionResult:
        """Handle tool call (unified interface)."""
        manager = self._get_manager()
        if not manager:
            return ExecutionResult(
                success=False,
                output="",
                error="No sandbox backend available",
            )
        return await manager.handle_tool_call(user_id, tool_name, arguments)

    async def get_sandbox(self, user_id: str) -> Any:
        """Get or create sandbox for user."""
        manager = self._get_manager()
        if not manager:
            return None
        return await manager.get_sandbox(user_id)

    # =========================================================================
    # Lifecycle & Statistics
    # =========================================================================

    async def cleanup_all(self) -> None:
        """Cleanup all sandbox sessions."""
        if self._docker:
            await self._docker.cleanup_all()
        if self._incus:
            await self._incus.cleanup_all()

    async def cleanup_idle_sessions(self) -> int:
        """Cleanup idle sessions."""
        count = 0
        if self._active_backend == "docker" and self._docker:
            count += await self._docker.cleanup_idle_sessions()
        if self._active_backend == "incus" and self._incus:
            count += await self._incus.cleanup_idle_sessions()
        return count

    def get_stats(self) -> dict[str, Any]:
        """Get combined statistics."""
        backend = self._select_backend()
        manager = self._get_manager()
        stats = manager.get_stats() if manager else {"available": False}
        stats["mode"] = self.mode
        stats["active_backend"] = backend
        return stats


# =============================================================================
# Global Singleton
# =============================================================================

_unified_manager: UnifiedSandboxManager | None = None


def get_sandbox_manager() -> UnifiedSandboxManager:
    """Get the global unified sandbox manager.

    Uses SANDBOX_MODE environment variable to determine backend.
    """
    global _unified_manager
    if _unified_manager is None:
        _unified_manager = UnifiedSandboxManager()
    return _unified_manager


def reset_sandbox_manager() -> None:
    """Reset the global sandbox manager (for testing)."""
    global _unified_manager
    _unified_manager = None
