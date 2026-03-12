"""Docker Sandbox MCP Server - Code execution via MCP protocol."""

import asyncio
import logging
import sys
from mcp.server.fastmcp import FastMCP

# Import existing implementation
from sandbox.docker import DockerSandboxManager, get_sandbox_manager, DOCKER_AVAILABLE

# stderr-only logging (stdout reserved for stdio transport JSON-RPC)
logger = logging.getLogger(__name__)
handler = logging.StreamHandler(sys.stderr)
handler.setFormatter(logging.Formatter("[docker_sandbox] %(levelname)s: %(message)s"))
logger.addHandler(handler)
logger.setLevel(logging.INFO)

# Create MCP server
mcp = FastMCP("docker-sandbox")


def _get_manager() -> DockerSandboxManager:
    """Get the sandbox manager singleton."""
    return get_sandbox_manager()


def _check_available() -> str | None:
    """Check if Docker is available. Returns error message or None."""
    if not DOCKER_AVAILABLE:
        return "Docker SDK not installed (pip install docker)"
    manager = _get_manager()
    if not manager.is_available():
        return "Docker daemon not running or not accessible"
    return None


@mcp.tool()
async def execute_python(user_id: str, code: str, description: str = "") -> str:
    """Execute Python code in a Docker sandbox.

    The sandbox has internet access and can install packages.
    Variables persist across executions within the same session.

    Args:
        user_id: User identifier for session isolation
        code: Python code to execute (use print() for output)
        description: Optional description for logging

    Returns:
        Execution output or error message
    """
    if err := _check_available():
        return f"Error: {err}"

    manager = _get_manager()
    result = await manager.execute_code(user_id, code, description)

    if result.success:
        timing = f" [{result.execution_time:.2f}s]" if result.execution_time else ""
        return f"{result.output}{timing}"
    return f"Error: {result.error or 'Unknown error'}\n{result.output}"


@mcp.tool()
async def install_package(user_id: str, package: str) -> str:
    """Install a Python package using pip in the sandbox.

    Packages persist within the user's session.
    Use before importing non-standard-library packages.

    Args:
        user_id: User identifier
        package: Package name (e.g., 'requests', 'pandas==2.0.0')

    Returns:
        Success/failure message
    """
    if err := _check_available():
        return f"Error: {err}"

    manager = _get_manager()
    result = await manager.install_package(user_id, package)

    if result.success:
        return result.output
    return f"Error: {result.error or 'Installation failed'}"


@mcp.tool()
async def run_shell(user_id: str, command: str) -> str:
    """Run a shell command in the sandbox.

    Useful for git, curl, system operations, etc.

    Args:
        user_id: User identifier
        command: Shell command to execute

    Returns:
        Command output or error
    """
    if err := _check_available():
        return f"Error: {err}"

    manager = _get_manager()
    result = await manager.run_shell(user_id, command)

    if result.success:
        return result.output
    return f"Error (exit {result.error}): {result.output}"


@mcp.tool()
async def sandbox_read_file(user_id: str, path: str) -> str:
    """Read a file from the sandbox filesystem.

    Args:
        user_id: User identifier
        path: File path in sandbox (e.g., '/home/user/output.txt')

    Returns:
        File contents or error message
    """
    if err := _check_available():
        return f"Error: {err}"

    manager = _get_manager()
    result = await manager.read_file(user_id, path)

    if result.success:
        return result.output
    return f"Error: {result.error or 'File not found'}"


@mcp.tool()
async def sandbox_write_file(user_id: str, path: str, content: str) -> str:
    """Write content to a file in the sandbox.

    Args:
        user_id: User identifier
        path: File path in sandbox
        content: Content to write

    Returns:
        Success/failure message
    """
    if err := _check_available():
        return f"Error: {err}"

    manager = _get_manager()
    result = await manager.write_file(user_id, path, content)

    if result.success:
        return result.output
    return f"Error: {result.error}"


@mcp.tool()
async def sandbox_list_files(user_id: str, path: str = "/home/user") -> str:
    """List files in a sandbox directory.

    Args:
        user_id: User identifier
        path: Directory path (default: /home/user)

    Returns:
        Directory listing or error
    """
    if err := _check_available():
        return f"Error: {err}"

    manager = _get_manager()
    result = await manager.list_files(user_id, path)

    if result.success:
        return result.output
    return f"Error: {result.error or 'Directory not found'}"


@mcp.tool()
def sandbox_status() -> str:
    """Check Docker sandbox availability and status.

    Returns:
        Status information including availability and active sessions
    """
    if not DOCKER_AVAILABLE:
        return "Docker SDK not installed. Run: pip install docker"

    manager = _get_manager()
    if not manager.is_available():
        return "Docker daemon not running or not accessible"

    stats = manager.get_stats()
    lines = [
        "Docker sandbox available",
        f"Active sessions: {stats['active_sessions']}"
    ]

    if stats['sessions']:
        lines.append("\nSessions:")
        for uid, session in stats['sessions'].items():
            lines.append(f"  - {uid}: {session['execution_count']} executions")

    return "\n".join(lines)


# Entry point for stdio transport
if __name__ == "__main__":
    mcp.run()
