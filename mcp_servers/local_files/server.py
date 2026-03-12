"""Local Files MCP Server - Per-user file storage via MCP protocol."""
import logging
import sys

from mcp.server.fastmcp import FastMCP

# Import existing implementation
from storage.local_files import get_file_manager

# stderr-only logging (critical for stdio transport - stdout reserved for JSON-RPC)
logger = logging.getLogger(__name__)
handler = logging.StreamHandler(sys.stderr)
handler.setFormatter(logging.Formatter("[local_files] %(levelname)s: %(message)s"))
logger.addHandler(handler)
logger.setLevel(logging.INFO)

# Create MCP server
mcp = FastMCP("local-files")


@mcp.tool()
def save_file(
    user_id: str, filename: str, content: str, channel_id: str | None = None
) -> str:
    """Save content to a local file for a user.

    Args:
        user_id: User identifier for isolation
        filename: Name for the file (e.g., 'results.csv', 'notes.md')
        content: Content to save
        channel_id: Optional channel for further isolation

    Returns:
        Success/failure message
    """
    manager = get_file_manager()
    result = manager.save_file(user_id, filename, content, channel_id)
    if result.success:
        return f"Saved: {result.file_info.name} ({result.file_info.size} bytes)"
    return f"Error: {result.message}"


@mcp.tool()
def read_file(user_id: str, filename: str, channel_id: str | None = None) -> str:
    """Read content from a locally saved file.

    Args:
        user_id: User identifier
        filename: Name of file to read
        channel_id: Optional channel identifier

    Returns:
        File content or error message
    """
    manager = get_file_manager()
    result = manager.read_file(user_id, filename, channel_id)
    if result.success:
        return result.message  # Contains file content
    return f"Error: {result.message}"


@mcp.tool()
def list_files(user_id: str, channel_id: str | None = None) -> str:
    """List files saved for a user.

    Args:
        user_id: User identifier
        channel_id: Optional channel identifier

    Returns:
        Formatted list of files
    """
    manager = get_file_manager()
    files = manager.list_files(user_id, channel_id)
    if not files:
        return "No files saved."

    lines = []
    for f in files:
        size = f"{f.size} bytes" if f.size < 1024 else f"{f.size / 1024:.1f} KB"
        lines.append(f"- {f.name} ({size})")
    return "\n".join(lines)


@mcp.tool()
def delete_file(user_id: str, filename: str, channel_id: str | None = None) -> str:
    """Delete a file from local storage.

    Args:
        user_id: User identifier
        filename: Name of file to delete
        channel_id: Optional channel identifier

    Returns:
        Success/failure message
    """
    manager = get_file_manager()
    result = manager.delete_file(user_id, filename, channel_id)
    return result.message


# Entry point for stdio transport
if __name__ == "__main__":
    mcp.run()
