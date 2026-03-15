"""Workspace file tools - Clara core tool.

Gives Clara runtime read/write access to her workspace files
(SOUL.md, IDENTITY.md, USER.md, HEARTBEAT.md, etc.).

SOUL.md and IDENTITY.md are read-only (owner-controlled).
Clara can read, write, and create other .md files in the workspace.

When a user has a per-user VM, workspace tools route through the
VM manager (incus exec) instead of direct host filesystem access.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mypalclara.tools._base import ToolContext, ToolDef

MODULE_NAME = "workspace"
MODULE_VERSION = "1.1.0"

WORKSPACE_DIR = Path(__file__).parent.parent.parent / "workspace"
READONLY_FILES = frozenset({"SOUL.md", "IDENTITY.md"})

# Per-user VM state — set by processor when USER_VM_ENABLED
_vm_manager: Any = None  # VMManager instance (or None)
_vm_users: set[str] = set()  # user_ids with active VMs


def set_vm_manager(vm_manager: Any) -> None:
    """Set the VM manager for VM-backed workspace access.

    Also pre-populates _vm_users from the manager's known instances
    so that workspace tools route correctly immediately after restart.
    """
    global _vm_manager
    _vm_manager = vm_manager
    # Pre-populate _vm_users from VMManager's loaded state (from DB)
    if vm_manager is not None and hasattr(vm_manager, "_instances"):
        for user_id in vm_manager._instances:
            _vm_users.add(user_id)


def register_user_workspace(user_id: str, workspace_path: Any = None) -> None:
    """Register a user as having a VM workspace.

    After registration, workspace tools for this user route through
    the VM manager instead of direct filesystem access.

    Args:
        user_id: The user to register.
        workspace_path: Ignored (kept for backward compatibility).
    """
    _vm_users.add(user_id)


def unregister_user_workspace(user_id: str) -> None:
    """Remove a per-user VM workspace registration."""
    _vm_users.discard(user_id)


def _user_has_vm(ctx: ToolContext) -> bool:
    """Check if this user's workspace is backed by a VM."""
    return _vm_manager is not None and ctx.user_id in _vm_users


SYSTEM_PROMPT = """
## Workspace Files
You have access to workspace files that shape your behavior and memory.
Use the workspace tools to read, update, or create these files.

**IMPORTANT:** Each user's workspace lives inside their personal VM (Incus container). The `workspace_*` tools automatically route to the correct VM. Do NOT use terminal commands (`execute_command`, `read_file`, `ls`, `cat`, etc.) to access workspace files — those operate on the host server, not the user's VM. Always use `workspace_read`, `workspace_write`, `workspace_list`, and `workspace_create`.

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


# ---------------------------------------------------------------------------
# VM-backed file operations
# ---------------------------------------------------------------------------


async def _vm_list_files(user_id: str) -> list[tuple[str, int]]:
    """List .md files in a user's VM workspace. Returns [(name, size), ...]."""
    from mypalclara.core.vm_manager import VM_WORKSPACE_DIR

    try:
        output = await _vm_manager.exec_in_vm(
            user_id,
            ["sh", "-c", f"cd '{VM_WORKSPACE_DIR}' && stat -c '%n %s' *.md 2>/dev/null || true"],
        )
    except Exception:
        return []

    files = []
    for line in output.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.rsplit(" ", 1)
        if len(parts) == 2:
            name = parts[0]
            try:
                size = int(parts[1])
            except ValueError:
                size = 0
            files.append((name, size))
    return sorted(files)


async def _vm_read_file(user_id: str, filename: str) -> str:
    """Read a file from the user's VM workspace."""
    from mypalclara.core.vm_manager import VM_WORKSPACE_DIR

    path = f"{VM_WORKSPACE_DIR}/{filename}"
    return await _vm_manager.read_file(user_id, path)


async def _vm_write_file(user_id: str, filename: str, content: str) -> None:
    """Write a file in the user's VM workspace."""
    from mypalclara.core.vm_manager import VM_WORKSPACE_DIR

    path = f"{VM_WORKSPACE_DIR}/{filename}"
    await _vm_manager.write_file(user_id, path, content)


async def _vm_file_exists(user_id: str, filename: str) -> bool:
    """Check if a file exists in the user's VM workspace."""
    from mypalclara.core.vm_manager import VM_WORKSPACE_DIR

    try:
        await _vm_manager.exec_in_vm(
            user_id,
            ["test", "-f", f"{VM_WORKSPACE_DIR}/{filename}"],
        )
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------


async def _handle_workspace_list(args: dict[str, Any], ctx: ToolContext) -> str:
    """List all workspace files."""
    if _user_has_vm(ctx):
        files = await _vm_list_files(ctx.user_id)
        if not files:
            return "No workspace files found in your VM."
        lines = []
        for name, size in files:
            readonly = " (read-only)" if name in READONLY_FILES else ""
            lines.append(f"- **{name}** ({size:,} bytes){readonly}")
        return "**Workspace files (VM):**\n" + "\n".join(lines)

    ws_dir = WORKSPACE_DIR
    if not ws_dir.is_dir():
        return "Workspace directory not found."

    files = sorted(ws_dir.glob("*.md"))
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

    # Global files (SOUL.md, IDENTITY.md) always come from the shared workspace
    if safe_name in READONLY_FILES:
        filepath = WORKSPACE_DIR / safe_name
        if not filepath.exists():
            return f"Error: '{safe_name}' not found."
        content = filepath.read_text(encoding="utf-8")
        return f"**{safe_name}:**\n\n{content}"

    if _user_has_vm(ctx):
        try:
            content = await _vm_read_file(ctx.user_id, safe_name)
            return f"**{safe_name}:**\n\n{content}"
        except Exception:
            return f"Error: '{safe_name}' not found in your VM workspace. Use workspace_list to see available files."

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

    content = args.get("content", "")
    mode = args.get("mode", "overwrite")

    if _user_has_vm(ctx):
        if not await _vm_file_exists(ctx.user_id, safe_name):
            return f"Error: '{safe_name}' not found. Use workspace_create to make a new file."

        if mode == "append":
            existing = await _vm_read_file(ctx.user_id, safe_name)
            content = existing + "\n" + content

        await _vm_write_file(ctx.user_id, safe_name, content)
        return f"{'Appended to' if mode == 'append' else 'Updated'} '{safe_name}' in VM workspace."

    ws_dir = WORKSPACE_DIR
    filepath = ws_dir / safe_name
    if not filepath.exists():
        return f"Error: '{safe_name}' not found. Use workspace_create to make a new file."

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

    content = args.get("content", "")

    if _user_has_vm(ctx):
        if await _vm_file_exists(ctx.user_id, safe_name):
            return f"Error: '{safe_name}' already exists. Use workspace_write to update it."
        await _vm_write_file(ctx.user_id, safe_name, content)
        return f"Created '{safe_name}' in VM workspace ({len(content):,} bytes)."

    ws_dir = WORKSPACE_DIR
    filepath = ws_dir / safe_name
    if filepath.exists():
        return f"Error: '{safe_name}' already exists. Use workspace_write to update it."

    ws_dir.mkdir(parents=True, exist_ok=True)
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
