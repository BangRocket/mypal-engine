"""Docker Sandbox MCP Server - Sandboxed code execution via MCP protocol.

Wraps Clara's DockerSandboxManager to provide:
- Python code execution in isolated containers
- Package installation via pip
- File operations (read, write, list)
- Shell command execution
- Per-user session isolation
"""

from .server import mcp

__all__ = ["mcp"]
