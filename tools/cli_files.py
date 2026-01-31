"""CLI file operations - Clara tool module.

Provides tools for reading and writing files in CLI mode with appropriate
safety controls (auto-approve reads, require approval with diff preview for writes).

Tools: cli_read_file, cli_write_file
Platform: CLI only
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from adapters.cli.approval import get_write_approval, show_write_preview
from tools._base import ToolContext, ToolDef

MODULE_NAME = "cli_files"
MODULE_VERSION = "0.1.0"

SYSTEM_PROMPT = """
## CLI File Operations

You can read and write files directly on the user's filesystem (CLI only).

**Tools:**
- `cli_read_file` - Read file contents (auto-approved)
- `cli_write_file` - Write content to a file (requires user approval with diff preview)

**Safety:**
- Reads are automatically approved
- Writes show a diff preview and require 'y' confirmation
- Binary files are detected and handled appropriately

**When to Use:**
- User asks to read a file
- User asks to create or modify a file
- Working with configuration files, code, documents

**Note:** These tools only work in CLI mode, not Discord.
""".strip()


# --- Tool Handlers ---


async def cli_read_file(args: dict[str, Any], ctx: ToolContext) -> str:
    """Read contents of a file - automatically approved for CLI."""
    path_str = args.get("path", "")
    if not path_str:
        return "Error: No path provided"

    # Expand ~ and resolve to absolute path
    try:
        file_path = Path(path_str).expanduser().resolve()
    except Exception as e:
        return f"Error: Invalid path '{path_str}': {e}"

    # Check if file exists
    if not file_path.exists():
        return f"Error: File not found: {path_str}"

    if file_path.is_dir():
        return f"Error: Path is a directory, not a file: {path_str}"

    # Try to read as text
    try:
        content = file_path.read_text(encoding="utf-8")
        return content
    except UnicodeDecodeError:
        # Binary file - return info instead of content
        size = file_path.stat().st_size
        return f"[Binary file: {size} bytes - use appropriate tool to view]"
    except Exception as e:
        return f"Error reading file: {e}"


async def cli_write_file(args: dict[str, Any], ctx: ToolContext) -> str:
    """Write content to a file - requires user approval with diff preview."""
    path_str = args.get("path", "")
    content = args.get("content", "")

    if not path_str:
        return "Error: No path provided"

    if content is None:
        return "Error: No content provided"

    # Get console and session from context extra
    # These are passed by cli_bot.py during initialization
    console = ctx.extra.get("console")
    session = ctx.extra.get("session")

    if not console or not session:
        return "Error: CLI file write tool requires console and session context (only works in interactive CLI)"

    # Expand ~ and resolve to absolute path
    try:
        file_path = Path(path_str).expanduser().resolve()
    except Exception as e:
        return f"Error: Invalid path '{path_str}': {e}"

    # Show diff preview
    try:
        show_write_preview(console, file_path, content)
    except Exception as e:
        return f"Error showing preview: {e}"

    # Get user approval
    try:
        approved = await get_write_approval(session)
    except Exception as e:
        return f"Error getting approval: {e}"

    if not approved:
        return "Write cancelled by user."

    # Create parent directories if needed
    try:
        file_path.parent.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        return f"Error creating parent directories: {e}"

    # Write the file
    try:
        file_path.write_text(content, encoding="utf-8")
        byte_count = len(content.encode("utf-8"))
        return f"Wrote {byte_count} bytes to {file_path}"
    except Exception as e:
        return f"Error writing file: {e}"


# --- Tool Definitions ---

TOOLS = [
    ToolDef(
        name="cli_read_file",
        description=(
            "Read the contents of a file from the filesystem. "
            "Automatically approved for CLI. Use this to read configuration files, "
            "code, documents, or any text file the user references. "
            "Binary files are detected and not read."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute or relative file path (supports ~ expansion)",
                },
            },
            "required": ["path"],
        },
        handler=cli_read_file,
        platforms=["cli"],  # CLI-only
    ),
    ToolDef(
        name="cli_write_file",
        description=(
            "Write content to a file on the filesystem. "
            "Shows a diff preview and requires user approval with 'y' confirmation. "
            "Use this to create new files or modify existing ones. "
            "Parent directories are created automatically if needed."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute or relative file path (supports ~ expansion)",
                },
                "content": {
                    "type": "string",
                    "description": "Content to write to the file",
                },
            },
            "required": ["path", "content"],
        },
        handler=cli_write_file,
        platforms=["cli"],  # CLI-only
    ),
]


# --- Lifecycle Hooks ---


async def initialize() -> None:
    """Initialize CLI files module."""
    pass  # No special initialization needed


async def cleanup() -> None:
    """Cleanup on module unload."""
    pass
