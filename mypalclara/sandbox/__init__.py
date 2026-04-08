"""Sandbox execution environments for Clara.

Provides sandboxed code execution via Docker containers.

Usage:
    from mypalclara.sandbox import get_sandbox_manager

    manager = get_sandbox_manager()
    result = await manager.execute_code("user123", "print('hello')")
"""

from mypalclara.sandbox.docker import DOCKER_AVAILABLE, DOCKER_TOOLS, DockerSandboxManager
from mypalclara.sandbox.manager import (
    UnifiedSandboxManager,
    get_sandbox_manager,
    reset_sandbox_manager,
)

__all__ = [
    # Legacy exports (for backward compatibility)
    "DockerSandboxManager",
    "DOCKER_TOOLS",
    "DOCKER_AVAILABLE",
    # Unified interface
    "UnifiedSandboxManager",
    "get_sandbox_manager",
    "reset_sandbox_manager",
]
