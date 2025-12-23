"""Local file storage tools.

Provides managed storage for Clara to save and retrieve files.
Tools: save_to_local, list_local_files, read_local_file, delete_local_file,
       download_from_sandbox, upload_to_sandbox, send_local_file

Supports both local filesystem and S3-compatible storage.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ._base import ToolContext, ToolDef

if TYPE_CHECKING:
    from storage.local_files import LocalFileManager

MODULE_NAME = "local_files"
MODULE_VERSION = "1.0.0"

# Lazy-loaded manager
_manager: LocalFileManager | None = None


def _get_manager() -> LocalFileManager:
    """Get or create the LocalFileManager singleton."""
    global _manager
    if _manager is None:
        from storage.local_files import LocalFileManager

        _manager = LocalFileManager()
    return _manager


def _format_file_list(files: list) -> str:
    """Format a list of FileInfo objects for display."""
    if not files:
        return "No files found."

    lines = ["**Saved Files:**"]
    for f in files:
        size_kb = f.size / 1024
        if size_kb < 1:
            size_str = f"{f.size} bytes"
        elif size_kb < 1024:
            size_str = f"{size_kb:.1f} KB"
        else:
            size_str = f"{size_kb/1024:.1f} MB"
        lines.append(f"- `{f.name}` ({size_str})")

    return "\n".join(lines)


# --- Tool Handlers ---


async def save_to_local(args: dict[str, Any], ctx: ToolContext) -> str:
    """Save content to a local file."""
    manager = _get_manager()
    filename = args.get("filename", "unnamed.txt")
    content = args.get("content", "")

    result = manager.save_file(ctx.user_id, filename, content, ctx.channel_id)
    return result.message


async def list_local_files(args: dict[str, Any], ctx: ToolContext) -> str:
    """List files in local storage."""
    manager = _get_manager()
    files = manager.list_files(ctx.user_id, ctx.channel_id)
    return _format_file_list(files)


async def read_local_file(args: dict[str, Any], ctx: ToolContext) -> str:
    """Read content from a local file."""
    manager = _get_manager()
    filename = args.get("filename", "")

    result = manager.read_file(ctx.user_id, filename, ctx.channel_id)
    if result.success:
        return result.message
    return f"Error: {result.message}"


async def delete_local_file(args: dict[str, Any], ctx: ToolContext) -> str:
    """Delete a file from local storage."""
    manager = _get_manager()
    filename = args.get("filename", "")

    result = manager.delete_file(ctx.user_id, filename, ctx.channel_id)
    return result.message


async def download_from_sandbox(args: dict[str, Any], ctx: ToolContext) -> str:
    """Download a file from Docker sandbox to local storage."""
    manager = _get_manager()
    sandbox_path = args.get("sandbox_path", "")
    local_filename = args.get("local_filename")

    # Get sandbox manager from context or import
    sandbox_manager = ctx.extra.get("sandbox_manager")
    if sandbox_manager is None:
        try:
            from sandbox.docker import DockerSandboxManager

            sandbox_manager = DockerSandboxManager()
        except ImportError:
            return "Error: Docker sandbox not available"

    # Read from sandbox
    read_result = await sandbox_manager.read_file(ctx.user_id, sandbox_path)
    if not read_result.success:
        return f"Error reading from sandbox: {read_result.error or 'File not found'}"

    # Determine local filename
    if not local_filename:
        local_filename = sandbox_path.split("/")[-1]

    # Save locally
    result = manager.save_file(
        ctx.user_id, local_filename, read_result.output, ctx.channel_id
    )
    return result.message


async def upload_to_sandbox(args: dict[str, Any], ctx: ToolContext) -> str:
    """Upload a file from local storage to Docker sandbox."""
    manager = _get_manager()
    local_filename = args.get("local_filename", "")
    sandbox_path = args.get("sandbox_path")

    # Read local file
    read_result = manager.read_file(ctx.user_id, local_filename, ctx.channel_id)
    if not read_result.success:
        return f"Error: {read_result.message}"

    # Get sandbox manager
    sandbox_manager = ctx.extra.get("sandbox_manager")
    if sandbox_manager is None:
        try:
            from sandbox.docker import DockerSandboxManager

            sandbox_manager = DockerSandboxManager()
        except ImportError:
            return "Error: Docker sandbox not available"

    # Determine sandbox path
    if not sandbox_path:
        sandbox_path = f"/home/user/{local_filename}"

    # Write to sandbox
    write_result = await sandbox_manager.write_file(
        ctx.user_id, sandbox_path, read_result.message
    )
    if write_result.success:
        return f"Uploaded {local_filename} to sandbox at {sandbox_path}"
    return f"Error uploading to sandbox: {write_result.error or 'Unknown error'}"


async def send_local_file(args: dict[str, Any], ctx: ToolContext) -> str:
    """Send a local file to chat (Discord-specific)."""
    filename = args.get("filename", "")

    # This tool requires platform-specific handling
    # The actual file sending is done by the platform adapter
    files_to_send = ctx.extra.get("files_to_send")
    if files_to_send is None:
        return f"File '{filename}' queued for sending (platform will handle delivery)"

    # Add to files queue
    manager = _get_manager()
    file_path = manager._storage_dir(ctx.user_id, ctx.channel_id) / filename

    if not file_path.exists():
        return f"Error: File '{filename}' not found in local storage"

    files_to_send.append({"path": str(file_path), "filename": filename})
    return f"File '{filename}' will be sent to chat"


# --- Tool Definitions ---

TOOLS = [
    ToolDef(
        name="save_to_local",
        description=(
            "Save content to a local file that persists across sessions. "
            "Use this to save important results, generated content, or data "
            "that should be available later. Files are stored per-user."
        ),
        parameters={
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": (
                        "Name for the file (e.g., 'results.csv', 'notes.md'). "
                        "Will be saved in the user's personal storage."
                    ),
                },
                "content": {
                    "type": "string",
                    "description": "The content to save to the file.",
                },
            },
            "required": ["filename", "content"],
        },
        handler=save_to_local,
        requires=["files"],
    ),
    ToolDef(
        name="list_local_files",
        description=(
            "List files saved in local storage. "
            "Shows files you've saved or that were uploaded by the user."
        ),
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
        },
        handler=list_local_files,
        requires=["files"],
    ),
    ToolDef(
        name="read_local_file",
        description=(
            "Read content from a locally saved file. "
            "Use this to retrieve previously saved data or uploaded files."
        ),
        parameters={
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": "Name of the file to read.",
                },
            },
            "required": ["filename"],
        },
        handler=read_local_file,
        requires=["files"],
    ),
    ToolDef(
        name="delete_local_file",
        description="Delete a file from local storage.",
        parameters={
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": "Name of the file to delete.",
                },
            },
            "required": ["filename"],
        },
        handler=delete_local_file,
        requires=["files"],
    ),
    ToolDef(
        name="download_from_sandbox",
        description=(
            "Download a file from the Docker sandbox to local storage. "
            "Use this to save sandbox results permanently or to share them in chat."
        ),
        parameters={
            "type": "object",
            "properties": {
                "sandbox_path": {
                    "type": "string",
                    "description": (
                        "Path to the file in the sandbox "
                        "(e.g., '/home/user/output.csv')"
                    ),
                },
                "local_filename": {
                    "type": "string",
                    "description": (
                        "Optional: name for the local file. "
                        "If not provided, uses the original filename."
                    ),
                },
            },
            "required": ["sandbox_path"],
        },
        handler=download_from_sandbox,
        requires=["files", "docker"],
    ),
    ToolDef(
        name="upload_to_sandbox",
        description=(
            "Upload a file from local storage to the Docker sandbox. "
            "Use this to make locally saved files available for code execution, "
            "data analysis, or processing in the sandbox environment."
        ),
        parameters={
            "type": "object",
            "properties": {
                "local_filename": {
                    "type": "string",
                    "description": "Name of the local file to upload.",
                },
                "sandbox_path": {
                    "type": "string",
                    "description": (
                        "Optional: destination path in the sandbox. "
                        "Defaults to /home/user/<filename>"
                    ),
                },
            },
            "required": ["local_filename"],
        },
        handler=upload_to_sandbox,
        requires=["files", "docker"],
    ),
    ToolDef(
        name="send_local_file",
        description=(
            "Send a locally saved file to the chat. "
            "Use this when the user asks to see or download a saved file."
        ),
        parameters={
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": "Name of the file to send to chat.",
                },
            },
            "required": ["filename"],
        },
        handler=send_local_file,
        platforms=["discord"],  # Discord-specific
        requires=["files"],
    ),
]


# --- Lifecycle Hooks ---


async def initialize() -> None:
    """Initialize file storage on module load."""
    manager = _get_manager()
    print(f"[local_files] Storage directory: {manager.base_dir}")


async def cleanup() -> None:
    """Cleanup on module unload."""
    global _manager
    _manager = None
