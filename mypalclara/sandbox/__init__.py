"""Sandbox execution environments for Clara.

Provides sandboxed code execution via:
- Local Docker containers
- Incus containers or VMs

Usage:
    from sandbox import get_sandbox_manager

    manager = get_sandbox_manager()
    result = await manager.execute_code("user123", "print('hello')")

The manager automatically selects the appropriate backend based on SANDBOX_MODE:
- "docker" or "local": Use local Docker
- "incus": Use Incus containers
- "incus-vm": Use Incus VMs (stronger isolation)
- "auto" (default): Use Incus if available, fall back to Docker
"""

from sandbox.docker import DOCKER_AVAILABLE, DOCKER_TOOLS, DockerSandboxManager
from sandbox.manager import (
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
