"""Workspace file tools - Clara core tool.

Gives Clara runtime read/write access to her workspace files
(SOUL.md, IDENTITY.md, USER.md, HEARTBEAT.md, etc.).

SOUL.md and IDENTITY.md are read-only (owner-controlled).
Clara can read, write, and create other .md files in the workspace.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mypalclara.tools._base import ToolContext, ToolDef

MODULE_NAME = "workspace"
MODULE_VERSION = "1.0.0"

WORKSPACE_DIR = Path(__file__).parent.parent.parent / "workspace"
READONLY_FILES = frozenset({"SOUL.md", "IDENTITY.md"})

SYSTEM_PROMPT = """
## Workspace Files
You have access to workspace files that shape your behavior and memory.
Use the workspace tools to read, update, or create these files.

**Read-only files (owner-controlled):**
- SOUL.md — Core behavioral instructions (you cannot edit this)
- IDENTITY.md — Identity fields (you cannot edit this)

**Editable files:**
- USER.md — Notes about the user
- MEMORY.md — Persistent memory notes
- HEARTBEAT.md — Instructions for your periodic heartbeat checks
- AGENTS.md — Agent configuration notes
- TOOLS.md — Tool-specific notes
- Any .md file you create

**When to use:**
- Update MEMORY.md when you learn something worth remembering
- Update HEARTBEAT.md to change what you check periodically
- Update USER.md when you learn user preferences
- Create new files for project notes, habits, or any persistent context
""".strip()


def _sanitize_filename(filename: str) -> str | None:
    """Sanitize a filename, returning None if invalid.

    Prevents path traversal and ensures the file stays in workspace dir.
    """
    # Strip any path components
    name = Path(filename).name
    if not name or name != filename:
        return None
    if ".." in name or "/" in name or "\\" in name:
        return None
    return name


async def _handle_workspace_list(args: dict[str, Any], ctx: ToolContext) -> str:
    """List all workspace files."""
    if not WORKSPACE_DIR.is_dir():
        return "Workspace directory not found."

    files = sorted(WORKSPACE_DIR.glob("*.md"))
    if not files:
        return "No workspace files found."

    lines = []
    for f in files:
        size = f.stat().st_size
        readonly = " (read-only)" if f.name in READONLY_FILES else ""
        lines.append(f"- **{f.name}** ({size:,} bytes){readonly}")

    return "**Workspace files:**\n" + "\n".join(lines)


async def _handle_workspace_read(args: dict[str, Any], ctx: ToolContext) -> str:
    """Read a workspace file."""
    filename = args.get("filename", "")
    safe_name = _sanitize_filename(filename)
    if not safe_name:
        return f"Error: Invalid filename '{filename}'."

    filepath = WORKSPACE_DIR / safe_name
    if not filepath.exists():
        return f"Error: '{safe_name}' not found. Use workspace_list to see available files."

    content = filepath.read_text(encoding="utf-8")
    return f"**{safe_name}:**\n\n{content}"


async def _handle_workspace_write(args: dict[str, Any], ctx: ToolContext) -> str:
    """Write to a workspace file."""
    filename = args.get("filename", "")
    safe_name = _sanitize_filename(filename)
    if not safe_name:
        return f"Error: Invalid filename '{filename}'."

    if safe_name in READONLY_FILES:
        return f"Error: '{safe_name}' is read-only (owner-controlled). You cannot edit this file."

    filepath = WORKSPACE_DIR / safe_name
    if not filepath.exists():
        return f"Error: '{safe_name}' not found. Use workspace_create to make a new file."

    content = args.get("content", "")
    mode = args.get("mode", "overwrite")

    if mode == "append":
        existing = filepath.read_text(encoding="utf-8")
        filepath.write_text(existing + "\n" + content, encoding="utf-8")
        new_size = filepath.stat().st_size
        return f"Appended to '{safe_name}' ({new_size:,} bytes)."
    else:
        filepath.write_text(content, encoding="utf-8")
        return f"Updated '{safe_name}' ({len(content):,} bytes)."


async def _handle_workspace_create(args: dict[str, Any], ctx: ToolContext) -> str:
    """Create a new workspace file."""
    filename = args.get("filename", "")
    safe_name = _sanitize_filename(filename)
    if not safe_name:
        return f"Error: Invalid filename '{filename}'."

    if not safe_name.endswith(".md"):
        return f"Error: Workspace files must be markdown (.md). Got '{safe_name}'."

    if safe_name in READONLY_FILES:
        return f"Error: '{safe_name}' is a reserved name."

    filepath = WORKSPACE_DIR / safe_name
    if filepath.exists():
        return f"Error: '{safe_name}' already exists. Use workspace_write to update it."

    WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
    content = args.get("content", "")
    filepath.write_text(content, encoding="utf-8")
    return f"Created '{safe_name}' ({len(content):,} bytes)."


TOOLS = [
    ToolDef(
        name="workspace_list",
        description="List all workspace files that shape your behavior and memory.",
        parameters={"type": "object", "properties": {}},
        handler=_handle_workspace_list,
        emoji="\U0001f4c2",
        label="Workspace",
        detail_keys=[],
        risk_level="safe",
        intent="read",
    ),
    ToolDef(
        name="workspace_read",
        description=("Read a workspace file by name. Use workspace_list first to see available files."),
        parameters={
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": "Name of the file to read (e.g., 'MEMORY.md')",
                },
            },
            "required": ["filename"],
        },
        handler=_handle_workspace_read,
        emoji="\U0001f4c4",
        label="Read Workspace",
        detail_keys=["filename"],
        risk_level="safe",
        intent="read",
    ),
    ToolDef(
        name="workspace_write",
        description=(
            "Update a workspace file. SOUL.md and IDENTITY.md are read-only. "
            "Use mode 'append' to add to the end, or 'overwrite' to replace entirely."
        ),
        parameters={
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": "Name of the file to write (e.g., 'MEMORY.md')",
                },
                "content": {
                    "type": "string",
                    "description": "Content to write",
                },
                "mode": {
                    "type": "string",
                    "enum": ["overwrite", "append"],
                    "description": "Write mode: 'overwrite' replaces the file, 'append' adds to the end",
                },
            },
            "required": ["filename", "content"],
        },
        handler=_handle_workspace_write,
        emoji="\u270f\ufe0f",
        label="Write Workspace",
        detail_keys=["filename", "mode"],
        risk_level="moderate",
        intent="write",
    ),
    ToolDef(
        name="workspace_create",
        description=(
            "Create a new .md file in the workspace. Cannot create files that already exist "
            "or use reserved names (SOUL.md, IDENTITY.md)."
        ),
        parameters={
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": "Name for the new file (must end in .md, e.g., 'PROJECTS.md')",
                },
                "content": {
                    "type": "string",
                    "description": "Initial content for the file (optional, defaults to empty)",
                },
            },
            "required": ["filename"],
        },
        handler=_handle_workspace_create,
        emoji="\u2728",
        label="Create Workspace",
        detail_keys=["filename"],
        risk_level="moderate",
        intent="write",
    ),
]


async def initialize() -> None:
    """Initialize workspace tool module."""
    pass


async def cleanup() -> None:
    """Cleanup on module unload."""
    pass
