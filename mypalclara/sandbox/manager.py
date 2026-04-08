"""Unified sandbox manager for Clara.

Provides a single interface for Docker-based code execution.
"""

from __future__ import annotations

from typing import Any

from .docker import DOCKER_AVAILABLE, DockerSandboxManager, ExecutionResult


class UnifiedSandboxManager:
    """Sandbox manager using Docker containers.

    Usage:
        manager = get_sandbox_manager()
        result = await manager.execute_code("user123", "print('hello')")
    """

    def __init__(self):
        self._docker: DockerSandboxManager | None = None

    @property
    def docker(self) -> DockerSandboxManager:
        """Get Docker manager (lazy initialization)."""
        if self._docker is None:
            self._docker = DockerSandboxManager()
        return self._docker

    def is_available(self) -> bool:
        """Check if Docker is available."""
        return DOCKER_AVAILABLE and self.docker.is_available()

    async def health_check(self) -> bool:
        """Check if Docker is healthy."""
        return self.docker.is_available()

    def _get_manager(self) -> DockerSandboxManager | None:
        """Get the Docker manager if available."""
        if self.is_available():
            return self.docker
        return None

    # =========================================================================
    # Forward all methods to Docker backend
    # =========================================================================

    async def execute_code(
        self, user_id: str, code: str, description: str = ""
    ) -> ExecutionResult:
        """Execute Python code in the sandbox."""
        manager = self._get_manager()
        if not manager:
            return ExecutionResult(success=False, output="", error="No sandbox backend available")
        return await manager.execute_code(user_id, code, description)

    async def run_shell(self, user_id: str, command: str) -> ExecutionResult:
        """Run shell command in the sandbox."""
        manager = self._get_manager()
        if not manager:
            return ExecutionResult(success=False, output="", error="No sandbox backend available")
        return await manager.run_shell(user_id, command)

    async def install_package(self, user_id: str, package: str) -> ExecutionResult:
        """Install pip package in the sandbox."""
        manager = self._get_manager()
        if not manager:
            return ExecutionResult(success=False, output="", error="No sandbox backend available")
        return await manager.install_package(user_id, package)

    async def ensure_packages(self, user_id: str, packages: list[str]) -> ExecutionResult:
        """Ensure multiple packages are installed (batch install)."""
        manager = self._get_manager()
        if not manager:
            return ExecutionResult(success=False, output="", error="No sandbox backend available")
        if hasattr(manager, "ensure_packages"):
            return await manager.ensure_packages(user_id, packages)
        for pkg in packages:
            result = await manager.install_package(user_id, pkg)
            if not result.success:
                return result
        return ExecutionResult(success=True, output=f"All {len(packages)} packages installed")

    async def read_file(self, user_id: str, path: str) -> ExecutionResult:
        """Read file from the sandbox."""
        manager = self._get_manager()
        if not manager:
            return ExecutionResult(success=False, output="", error="No sandbox backend available")
        return await manager.read_file(user_id, path)

    async def write_file(self, user_id: str, path: str, content: str | bytes) -> ExecutionResult:
        """Write file to the sandbox."""
        manager = self._get_manager()
        if not manager:
            return ExecutionResult(success=False, output="", error="No sandbox backend available")
        return await manager.write_file(user_id, path, content)

    async def list_files(self, user_id: str, path: str = "/home/user") -> ExecutionResult:
        """List files in the sandbox."""
        manager = self._get_manager()
        if not manager:
            return ExecutionResult(success=False, output="", error="No sandbox backend available")
        return await manager.list_files(user_id, path)

    async def unzip_file(
        self, user_id: str, path: str, destination: str | None = None
    ) -> ExecutionResult:
        """Extract archive in the sandbox."""
        manager = self._get_manager()
        if not manager:
            return ExecutionResult(success=False, output="", error="No sandbox backend available")
        return await manager.unzip_file(user_id, path, destination)

    async def web_search(
        self, query: str, max_results: int = 5, search_depth: str = "basic"
    ) -> ExecutionResult:
        """Web search (Tavily API)."""
        manager = self._get_manager()
        if not manager:
            return ExecutionResult(success=False, output="", error="No sandbox backend available")
        if hasattr(manager, "web_search"):
            return await manager.web_search(query, max_results, search_depth)
        return ExecutionResult(success=False, output="", error="Web search not available")

    async def handle_tool_call(
        self, user_id: str, tool_name: str, arguments: dict
    ) -> ExecutionResult:
        """Handle tool call (unified interface)."""
        manager = self._get_manager()
        if not manager:
            return ExecutionResult(success=False, output="", error="No sandbox backend available")
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

    async def cleanup_idle_sessions(self) -> int:
        """Cleanup idle sessions."""
        if self._docker:
            return await self._docker.cleanup_idle_sessions()
        return 0

    def get_stats(self) -> dict[str, Any]:
        """Get sandbox statistics."""
        manager = self._get_manager()
        stats = manager.get_stats() if manager else {"available": False}
        stats["active_backend"] = "docker" if manager else "none"
        return stats


# =============================================================================
# Global Singleton
# =============================================================================

_unified_manager: UnifiedSandboxManager | None = None


def get_sandbox_manager() -> UnifiedSandboxManager:
    """Get the global unified sandbox manager."""
    global _unified_manager
    if _unified_manager is None:
        _unified_manager = UnifiedSandboxManager()
    return _unified_manager


def reset_sandbox_manager() -> None:
    """Reset the global sandbox manager (for testing)."""
    global _unified_manager
    _unified_manager = None
