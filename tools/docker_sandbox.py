"""Docker sandbox execution tools.

Provides sandboxed code execution via Docker containers.
Tools: execute_python, install_package, read_file, write_file,
       list_files, run_shell, unzip_file

Requires: Docker daemon running, docker-py installed
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ._base import ToolContext, ToolDef

if TYPE_CHECKING:
    from docker_tools import DockerSandboxManager

MODULE_NAME = "docker_sandbox"
MODULE_VERSION = "1.0.0"

# Lazy-loaded manager (shared across all handlers)
_manager: DockerSandboxManager | None = None


def _get_manager() -> DockerSandboxManager:
    """Get or create the DockerSandboxManager singleton."""
    global _manager
    if _manager is None:
        from docker_tools import DockerSandboxManager

        _manager = DockerSandboxManager()
    return _manager


def is_available() -> bool:
    """Check if Docker sandbox is available."""
    try:
        from docker_tools import DOCKER_AVAILABLE

        return DOCKER_AVAILABLE
    except ImportError:
        return False


# --- Tool Handlers ---


async def execute_python(args: dict[str, Any], ctx: ToolContext) -> str:
    """Execute Python code in the sandbox."""
    manager = _get_manager()
    code = args.get("code", "")
    description = args.get("description", "")

    result = await manager.execute_code(ctx.user_id, code, description)
    if result.success:
        return result.output or "(no output)"
    return f"Error: {result.error or 'Unknown error'}\n{result.output or ''}"


async def install_package(args: dict[str, Any], ctx: ToolContext) -> str:
    """Install a pip package in the sandbox."""
    manager = _get_manager()
    package = args.get("package", "")

    result = await manager.install_package(ctx.user_id, package)
    if result.success:
        return result.output or f"Successfully installed {package}"
    return f"Error installing {package}: {result.error or result.output}"


async def read_file(args: dict[str, Any], ctx: ToolContext) -> str:
    """Read a file from the sandbox filesystem."""
    manager = _get_manager()
    path = args.get("path", "")

    result = await manager.read_file(ctx.user_id, path)
    if result.success:
        return result.output or "(empty file)"
    return f"Error reading {path}: {result.error or 'File not found'}"


async def write_file(args: dict[str, Any], ctx: ToolContext) -> str:
    """Write content to a file in the sandbox."""
    manager = _get_manager()
    path = args.get("path", "")
    content = args.get("content", "")

    result = await manager.write_file(ctx.user_id, path, content)
    if result.success:
        return result.output or f"Successfully wrote to {path}"
    return f"Error writing to {path}: {result.error or 'Unknown error'}"


async def list_files(args: dict[str, Any], ctx: ToolContext) -> str:
    """List files in a directory in the sandbox."""
    manager = _get_manager()
    path = args.get("path", "/home/user")

    result = await manager.list_files(ctx.user_id, path)
    if result.success:
        return result.output or "(empty directory)"
    return f"Error listing {path}: {result.error or 'Directory not found'}"


async def run_shell(args: dict[str, Any], ctx: ToolContext) -> str:
    """Run a shell command in the sandbox."""
    manager = _get_manager()
    command = args.get("command", "")

    result = await manager.run_shell(ctx.user_id, command)
    if result.success:
        return result.output or "(no output)"
    return f"Error: {result.error or 'Command failed'}\n{result.output or ''}"


async def unzip_file(args: dict[str, Any], ctx: ToolContext) -> str:
    """Extract an archive in the sandbox."""
    manager = _get_manager()
    path = args.get("path", "")
    destination = args.get("destination")

    result = await manager.unzip_file(ctx.user_id, path, destination)
    if result.success:
        return result.output or f"Successfully extracted {path}"
    return f"Error extracting {path}: {result.error or 'Unknown error'}"


# --- Tool Definitions ---

TOOLS = [
    ToolDef(
        name="execute_python",
        description=(
            "Execute Python code in a secure Docker sandbox. "
            "The sandbox has internet access and can install packages with pip. "
            "Code execution is stateful - variables persist across calls. "
            "Use this for: calculations, data analysis, file generation, "
            "web requests, package installation, and any Python code."
        ),
        parameters={
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": (
                        "The Python code to execute. Can be multi-line. "
                        "Use print() to output results. "
                        "Variables persist across executions."
                    ),
                },
                "description": {
                    "type": "string",
                    "description": (
                        "Brief description of what this code does "
                        "(for logging/display purposes)"
                    ),
                },
            },
            "required": ["code"],
        },
        handler=execute_python,
        requires=["docker"],
    ),
    ToolDef(
        name="install_package",
        description=(
            "Install a Python package using pip in the sandbox. "
            "Use this before importing non-standard-library packages."
        ),
        parameters={
            "type": "object",
            "properties": {
                "package": {
                    "type": "string",
                    "description": (
                        "The package name to install (e.g., 'requests', "
                        "'pandas', 'numpy'). Can include version specifiers."
                    ),
                },
            },
            "required": ["package"],
        },
        handler=install_package,
        requires=["docker"],
    ),
    ToolDef(
        name="read_file",
        description=(
            "Read the contents of a file from the sandbox filesystem. "
            "Useful for checking generated files or reading uploaded content."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "The file path to read (e.g., '/home/user/output.txt')",
                },
            },
            "required": ["path"],
        },
        handler=read_file,
        requires=["docker"],
    ),
    ToolDef(
        name="write_file",
        description=(
            "Write content to a file in the sandbox filesystem. "
            "Useful for creating files that can be executed or downloaded."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "The file path to write to",
                },
                "content": {
                    "type": "string",
                    "description": "The content to write to the file",
                },
            },
            "required": ["path", "content"],
        },
        handler=write_file,
        requires=["docker"],
    ),
    ToolDef(
        name="list_files",
        description=(
            "List files and directories in a path within the sandbox. "
            "Useful for exploring the filesystem or checking generated files."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "The directory path to list (default: '/home/user')",
                },
            },
            "required": [],
        },
        handler=list_files,
        requires=["docker"],
    ),
    ToolDef(
        name="run_shell",
        description=(
            "Run a shell command in the sandbox. "
            "Useful for system operations, git, curl, etc."
        ),
        parameters={
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The shell command to execute",
                },
            },
            "required": ["command"],
        },
        handler=run_shell,
        requires=["docker"],
    ),
    ToolDef(
        name="unzip_file",
        description=(
            "Extract a zip archive in the sandbox. "
            "Supports .zip, .tar, .tar.gz, .tgz, .tar.bz2 formats. "
            "Useful after downloading or receiving compressed files."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": (
                        "Path to the archive file to extract "
                        "(e.g., '/home/user/archive.zip')"
                    ),
                },
                "destination": {
                    "type": "string",
                    "description": (
                        "Directory to extract to (default: same directory as archive)"
                    ),
                },
            },
            "required": ["path"],
        },
        handler=unzip_file,
        requires=["docker"],
    ),
]


# --- Lifecycle Hooks ---


async def initialize() -> None:
    """Initialize Docker connection on module load."""
    if not is_available():
        print("[docker_sandbox] Docker not available - tools will be disabled")
        return

    # Pre-create manager to verify Docker connection
    try:
        manager = _get_manager()
        if manager.is_available():
            print("[docker_sandbox] Docker connection verified")
        else:
            print("[docker_sandbox] Docker daemon not running")
    except Exception as e:
        print(f"[docker_sandbox] Error connecting to Docker: {e}")


async def cleanup() -> None:
    """Cleanup Docker resources on module unload."""
    global _manager
    if _manager:
        try:
            await _manager.cleanup_all()
        except Exception as e:
            print(f"[docker_sandbox] Error during cleanup: {e}")
        _manager = None
