"""Terminal tool for Clara.

Provides terminal command execution and file system operations.
Python implementation of rustterm-mcp capabilities.

Features:
- Command execution with configurable timeouts
- Command history tracking
- Directory navigation
- File operations with row-level precision
- Security: Blocks dangerous commands
- Cross-platform support
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shlex
import subprocess
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tools._base import ToolContext, ToolDef

MODULE_NAME = "terminal"
MODULE_VERSION = "1.0.0"

logger = logging.getLogger(__name__)

# Configuration
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB limit for file operations
DEFAULT_TIMEOUT = 30  # Default command timeout in seconds
MAX_HISTORY = 50  # Maximum command history entries

SYSTEM_PROMPT = """
## Terminal Operations

You have access to terminal command execution and file system operations.

**Command Execution:**
- `execute_command` - Run terminal commands with optional timeout
- `get_command_history` - View recent command history

**Directory Navigation:**
- `get_current_directory` - Get current working directory
- `change_directory` - Change working directory
- `list_directory` - List directory contents

**File Operations:**
- `read_file` - Read file with optional row selection
- `write_file` - Write/append to file
- `insert_file_content` - Insert at specific rows
- `delete_file_content` - Delete rows or substrings
- `update_file_content` - Update/replace at specific rows

**Security Notes:**
- Dangerous commands are blocked (rm -rf /, mkfs, fork bombs)
- File operations limited to 10 MB
- Commands have configurable timeouts (default 30s)
""".strip()


# =============================================================================
# Dangerous Command Detection
# =============================================================================

DANGEROUS_PATTERNS = [
    r"rm\s+(-[rfRv]+\s+)*(/|/\*|\*/)",  # rm -rf / or rm -rf /*
    r"rm\s+(-[rfRv]+\s+)+\.\.",  # rm -rf ..
    r"mkfs\s",  # mkfs commands
    r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:",  # Fork bomb
    r"dd\s+.*of=/dev/(sd|hd|nvme)",  # dd to raw disk
    r">\s*/dev/(sd|hd|nvme)",  # Redirect to raw disk
    r"chmod\s+(-R\s+)?777\s+/",  # chmod 777 /
    r"chown\s+(-R\s+)?.*\s+/\s*$",  # chown /
]

DANGEROUS_COMPILED = [re.compile(p, re.IGNORECASE) for p in DANGEROUS_PATTERNS]


def is_dangerous_command(command: str) -> tuple[bool, str]:
    """Check if a command is potentially dangerous.

    Returns:
        tuple: (is_dangerous, reason)
    """
    for pattern in DANGEROUS_COMPILED:
        if pattern.search(command):
            return True, f"Command matches dangerous pattern: {pattern.pattern}"
    return False, ""


# =============================================================================
# Command History
# =============================================================================


@dataclass
class CommandHistoryEntry:
    """A single command execution record."""
    command: str
    timestamp: datetime
    exit_code: int | None
    duration_ms: int
    stdout_preview: str  # First 500 chars
    stderr_preview: str  # First 500 chars
    success: bool


# Global state
_command_history: list[CommandHistoryEntry] = []
_current_directory: Path = Path.cwd()


def _add_to_history(entry: CommandHistoryEntry) -> None:
    """Add an entry to command history, maintaining max size."""
    global _command_history
    _command_history.append(entry)
    if len(_command_history) > MAX_HISTORY:
        _command_history = _command_history[-MAX_HISTORY:]


# =============================================================================
# Command Execution
# =============================================================================


async def execute_command(args: dict[str, Any], ctx: ToolContext) -> str:
    """Execute a terminal command with optional timeout."""
    command = args.get("command")
    timeout = args.get("timeout", DEFAULT_TIMEOUT)

    if not command:
        return "Error: command is required"

    if not isinstance(timeout, int) or timeout < 1:
        timeout = DEFAULT_TIMEOUT

    # Security check
    is_dangerous, reason = is_dangerous_command(command)
    if is_dangerous:
        return f"Error: Command blocked for security reasons. {reason}"

    start_time = time.time()

    try:
        # Run command in current directory
        process = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(_current_directory),
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout
            )
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            duration_ms = int((time.time() - start_time) * 1000)

            entry = CommandHistoryEntry(
                command=command,
                timestamp=datetime.now(UTC),
                exit_code=None,
                duration_ms=duration_ms,
                stdout_preview="",
                stderr_preview="",
                success=False,
            )
            _add_to_history(entry)

            return f"Error: Command timed out after {timeout} seconds"

        duration_ms = int((time.time() - start_time) * 1000)
        stdout_str = stdout.decode("utf-8", errors="replace")
        stderr_str = stderr.decode("utf-8", errors="replace")

        entry = CommandHistoryEntry(
            command=command,
            timestamp=datetime.now(UTC),
            exit_code=process.returncode,
            duration_ms=duration_ms,
            stdout_preview=stdout_str[:500],
            stderr_preview=stderr_str[:500],
            success=process.returncode == 0,
        )
        _add_to_history(entry)

        # Build response
        result_parts = []
        if stdout_str:
            result_parts.append(stdout_str)
        if stderr_str:
            if result_parts:
                result_parts.append(f"\n--- stderr ---\n{stderr_str}")
            else:
                result_parts.append(stderr_str)

        result = "".join(result_parts) if result_parts else "(no output)"

        # Add exit code if non-zero
        if process.returncode != 0:
            result = f"{result}\n\n[Exit code: {process.returncode}]"

        return result

    except Exception as e:
        duration_ms = int((time.time() - start_time) * 1000)
        entry = CommandHistoryEntry(
            command=command,
            timestamp=datetime.now(UTC),
            exit_code=None,
            duration_ms=duration_ms,
            stdout_preview="",
            stderr_preview=str(e)[:500],
            success=False,
        )
        _add_to_history(entry)
        return f"Error executing command: {e}"


async def get_command_history(args: dict[str, Any], ctx: ToolContext) -> str:
    """Get recent command execution history."""
    count = args.get("count", 10)
    if not isinstance(count, int) or count < 1:
        count = 10
    count = min(count, MAX_HISTORY)

    if not _command_history:
        return "No command history."

    entries = _command_history[-count:]
    lines = ["**Command History:**"]

    for i, entry in enumerate(entries, 1):
        status = "✅" if entry.success else "❌"
        ts = entry.timestamp.strftime("%H:%M:%S")
        lines.append(f"{i}. {status} `{entry.command[:60]}{'...' if len(entry.command) > 60 else ''}` ({ts}, {entry.duration_ms}ms)")

    return "\n".join(lines)


# =============================================================================
# Directory Navigation
# =============================================================================


async def get_current_directory(args: dict[str, Any], ctx: ToolContext) -> str:
    """Get the current working directory."""
    return str(_current_directory)


async def change_directory(args: dict[str, Any], ctx: ToolContext) -> str:
    """Change the current working directory."""
    global _current_directory

    path = args.get("path")
    if not path:
        return "Error: path is required"

    # Resolve relative to current directory
    if not os.path.isabs(path):
        new_path = (_current_directory / path).resolve()
    else:
        new_path = Path(path).resolve()

    if not new_path.exists():
        return f"Error: Directory does not exist: {new_path}"

    if not new_path.is_dir():
        return f"Error: Not a directory: {new_path}"

    _current_directory = new_path
    return f"Changed directory to: {_current_directory}"


async def list_directory(args: dict[str, Any], ctx: ToolContext) -> str:
    """List files and directories."""
    path = args.get("path")

    if path:
        if not os.path.isabs(path):
            target = (_current_directory / path).resolve()
        else:
            target = Path(path).resolve()
    else:
        target = _current_directory

    if not target.exists():
        return f"Error: Path does not exist: {target}"

    if not target.is_dir():
        return f"Error: Not a directory: {target}"

    try:
        entries = sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))

        lines = [f"**Contents of {target}:**"]
        for entry in entries:
            if entry.is_dir():
                lines.append(f"  📁 {entry.name}/")
            elif entry.is_symlink():
                lines.append(f"  🔗 {entry.name} -> {entry.resolve()}")
            else:
                size = entry.stat().st_size
                size_str = _format_size(size)
                lines.append(f"  📄 {entry.name} ({size_str})")

        if len(entries) == 0:
            lines.append("  (empty)")

        return "\n".join(lines)

    except PermissionError:
        return f"Error: Permission denied: {target}"
    except Exception as e:
        return f"Error listing directory: {e}"


# =============================================================================
# File Operations
# =============================================================================


async def read_file(args: dict[str, Any], ctx: ToolContext) -> str:
    """Read file contents with optional row selection."""
    path = args.get("path")
    start_row = args.get("start_row")
    end_row = args.get("end_row")
    as_json = args.get("as_json", False)

    if not path:
        return "Error: path is required"

    # Resolve path
    if not os.path.isabs(path):
        file_path = (_current_directory / path).resolve()
    else:
        file_path = Path(path).resolve()

    if not file_path.exists():
        return f"Error: File does not exist: {file_path}"

    if not file_path.is_file():
        return f"Error: Not a file: {file_path}"

    # Check file size
    size = file_path.stat().st_size
    if size > MAX_FILE_SIZE:
        return f"Error: File too large ({_format_size(size)}). Maximum is {_format_size(MAX_FILE_SIZE)}."

    try:
        content = file_path.read_text(encoding="utf-8", errors="replace")

        # Apply row filtering if specified
        if start_row is not None or end_row is not None:
            lines = content.splitlines(keepends=True)
            start = start_row if start_row is not None else 0
            end = (end_row + 1) if end_row is not None else len(lines)

            # Clamp to valid range
            start = max(0, start)
            end = min(len(lines), end)

            content = "".join(lines[start:end])

        # Parse as JSON if requested
        if as_json:
            try:
                parsed = json.loads(content)
                content = json.dumps(parsed, indent=2)
            except json.JSONDecodeError as e:
                return f"Error parsing JSON: {e}"

        return content if content else "(empty file)"

    except Exception as e:
        return f"Error reading file: {e}"


async def write_file(args: dict[str, Any], ctx: ToolContext) -> str:
    """Write content to a file."""
    path = args.get("path")
    content = args.get("content")
    mode = args.get("mode", "overwrite")

    if not path:
        return "Error: path is required"
    if content is None:
        return "Error: content is required"

    # Resolve path
    if not os.path.isabs(path):
        file_path = (_current_directory / path).resolve()
    else:
        file_path = Path(path).resolve()

    # Check content size
    content_bytes = content.encode("utf-8")
    if len(content_bytes) > MAX_FILE_SIZE:
        return f"Error: Content too large ({_format_size(len(content_bytes))}). Maximum is {_format_size(MAX_FILE_SIZE)}."

    try:
        # Ensure parent directory exists
        file_path.parent.mkdir(parents=True, exist_ok=True)

        if mode == "append":
            with file_path.open("a", encoding="utf-8") as f:
                f.write(content)
            action = "Appended to"
        else:
            file_path.write_text(content, encoding="utf-8")
            action = "Wrote"

        return f"{action} {_format_size(len(content_bytes))} to {file_path}"

    except Exception as e:
        return f"Error writing file: {e}"


async def insert_file_content(args: dict[str, Any], ctx: ToolContext) -> str:
    """Insert content at specific row(s)."""
    path = args.get("path")
    content = args.get("content")
    row = args.get("row")
    rows = args.get("rows")

    if not path:
        return "Error: path is required"
    if content is None:
        return "Error: content is required"
    if row is None and rows is None:
        return "Error: either row or rows is required"

    # Resolve path
    if not os.path.isabs(path):
        file_path = (_current_directory / path).resolve()
    else:
        file_path = Path(path).resolve()

    if not file_path.exists():
        return f"Error: File does not exist: {file_path}"

    # Check file size
    size = file_path.stat().st_size
    if size > MAX_FILE_SIZE:
        return f"Error: File too large ({_format_size(size)}). Maximum is {_format_size(MAX_FILE_SIZE)}."

    try:
        lines = file_path.read_text(encoding="utf-8").splitlines(keepends=True)

        # Normalize to list of row indices
        target_rows = [row] if row is not None else (rows or [])
        target_rows = sorted(set(target_rows), reverse=True)  # Process from end to start

        # Ensure content ends with newline for insertion
        insert_content = content if content.endswith("\n") else content + "\n"

        for r in target_rows:
            if 0 <= r <= len(lines):
                lines.insert(r, insert_content)

        new_content = "".join(lines)

        # Check new size
        if len(new_content.encode("utf-8")) > MAX_FILE_SIZE:
            return f"Error: Resulting file would exceed {_format_size(MAX_FILE_SIZE)} limit."

        file_path.write_text(new_content, encoding="utf-8")
        return f"Inserted content at row(s) {target_rows[::-1]} in {file_path}"

    except Exception as e:
        return f"Error inserting content: {e}"


async def delete_file_content(args: dict[str, Any], ctx: ToolContext) -> str:
    """Delete row(s) or substrings within rows."""
    path = args.get("path")
    row = args.get("row")
    rows = args.get("rows")
    substring = args.get("substring")

    if not path:
        return "Error: path is required"
    if row is None and rows is None:
        return "Error: either row or rows is required"

    # Resolve path
    if not os.path.isabs(path):
        file_path = (_current_directory / path).resolve()
    else:
        file_path = Path(path).resolve()

    if not file_path.exists():
        return f"Error: File does not exist: {file_path}"

    try:
        lines = file_path.read_text(encoding="utf-8").splitlines(keepends=True)

        # Normalize to list of row indices
        target_rows = [row] if row is not None else (rows or [])
        target_rows = sorted(set(target_rows), reverse=True)  # Process from end to start

        if substring:
            # Delete substring within specified rows
            for r in target_rows:
                if 0 <= r < len(lines):
                    lines[r] = lines[r].replace(substring, "")
            action = f"Deleted substring '{substring}' from row(s)"
        else:
            # Delete entire rows
            for r in target_rows:
                if 0 <= r < len(lines):
                    del lines[r]
            action = "Deleted row(s)"

        new_content = "".join(lines)
        file_path.write_text(new_content, encoding="utf-8")
        return f"{action} {target_rows[::-1]} in {file_path}"

    except Exception as e:
        return f"Error deleting content: {e}"


async def update_file_content(args: dict[str, Any], ctx: ToolContext) -> str:
    """Update/replace content at specific row(s)."""
    path = args.get("path")
    content = args.get("content")
    row = args.get("row")
    rows = args.get("rows")
    substring = args.get("substring")

    if not path:
        return "Error: path is required"
    if content is None:
        return "Error: content is required"
    if row is None and rows is None:
        return "Error: either row or rows is required"

    # Resolve path
    if not os.path.isabs(path):
        file_path = (_current_directory / path).resolve()
    else:
        file_path = Path(path).resolve()

    if not file_path.exists():
        return f"Error: File does not exist: {file_path}"

    # Check file size
    size = file_path.stat().st_size
    if size > MAX_FILE_SIZE:
        return f"Error: File too large ({_format_size(size)}). Maximum is {_format_size(MAX_FILE_SIZE)}."

    try:
        lines = file_path.read_text(encoding="utf-8").splitlines(keepends=True)

        # Normalize to list of row indices
        target_rows = [row] if row is not None else (rows or [])

        if substring:
            # Replace substring within specified rows
            for r in target_rows:
                if 0 <= r < len(lines):
                    lines[r] = lines[r].replace(substring, content)
            action = f"Replaced '{substring}' with new content in row(s)"
        else:
            # Replace entire rows
            # Ensure content ends with newline
            replace_content = content if content.endswith("\n") else content + "\n"
            for r in target_rows:
                if 0 <= r < len(lines):
                    lines[r] = replace_content
            action = "Updated row(s)"

        new_content = "".join(lines)

        # Check new size
        if len(new_content.encode("utf-8")) > MAX_FILE_SIZE:
            return f"Error: Resulting file would exceed {_format_size(MAX_FILE_SIZE)} limit."

        file_path.write_text(new_content, encoding="utf-8")
        return f"{action} {target_rows} in {file_path}"

    except Exception as e:
        return f"Error updating content: {e}"


# =============================================================================
# Helper Functions
# =============================================================================


def _format_size(size: int) -> str:
    """Format file size for display."""
    if size < 1024:
        return f"{size} B"
    elif size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    else:
        return f"{size / (1024 * 1024):.1f} MB"


# =============================================================================
# Tool Definitions
# =============================================================================


TOOLS = [
    # Command execution
    ToolDef(
        name="execute_command",
        description=(
            "Execute a terminal command with optional timeout. "
            "Dangerous commands (rm -rf /, mkfs, fork bombs) are blocked for security."
        ),
        parameters={
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The command to execute",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in seconds (default: 30)",
                },
            },
            "required": ["command"],
        },
        handler=execute_command,
        emoji="💻",
        label="Execute",
        detail_keys=["command"],
        risk_level="dangerous",
        intent="execute",
    ),
    ToolDef(
        name="get_command_history",
        description="Retrieve recent command execution history with timestamps and status.",
        parameters={
            "type": "object",
            "properties": {
                "count": {
                    "type": "integer",
                    "description": "Number of entries to retrieve (default: 10, max: 50)",
                },
            },
        },
        handler=get_command_history,
        emoji="📜",
        label="History",
        detail_keys=[],
        risk_level="safe",
        intent="read",
    ),
    # Directory navigation
    ToolDef(
        name="get_current_directory",
        description="Get the current working directory.",
        parameters={
            "type": "object",
            "properties": {},
        },
        handler=get_current_directory,
        emoji="📍",
        label="Get CWD",
        detail_keys=[],
        risk_level="safe",
        intent="read",
    ),
    ToolDef(
        name="change_directory",
        description="Change the current working directory.",
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to change to (absolute or relative)",
                },
            },
            "required": ["path"],
        },
        handler=change_directory,
        emoji="📂",
        label="Change Dir",
        detail_keys=["path"],
        risk_level="safe",
        intent="execute",
    ),
    ToolDef(
        name="list_directory",
        description="List files and directories in the specified path or current directory.",
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to list (default: current directory)",
                },
            },
        },
        handler=list_directory,
        emoji="📁",
        label="List Dir",
        detail_keys=["path"],
        risk_level="safe",
        intent="read",
    ),
    # File operations
    ToolDef(
        name="read_file",
        description=(
            "Read file contents with optional row selection and JSON parsing. "
            "Files larger than 10 MB are rejected."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to the file",
                },
                "start_row": {
                    "type": "integer",
                    "description": "Starting row (0-based, optional)",
                },
                "end_row": {
                    "type": "integer",
                    "description": "Ending row (0-based, inclusive, optional)",
                },
                "as_json": {
                    "type": "boolean",
                    "description": "Parse and pretty-print as JSON (default: false)",
                },
            },
            "required": ["path"],
        },
        handler=read_file,
        emoji="📖",
        label="Read",
        detail_keys=["path"],
        risk_level="safe",
        intent="read",
    ),
    ToolDef(
        name="write_file",
        description=(
            "Write content to a file. Mode can be 'overwrite' (default) or 'append'. "
            "Creates parent directories if needed."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to the file",
                },
                "content": {
                    "type": "string",
                    "description": "Content to write",
                },
                "mode": {
                    "type": "string",
                    "enum": ["overwrite", "append"],
                    "description": "Write mode (default: overwrite)",
                },
            },
            "required": ["path", "content"],
        },
        handler=write_file,
        emoji="📝",
        label="Write",
        detail_keys=["path"],
        risk_level="moderate",
        intent="write",
    ),
    ToolDef(
        name="insert_file_content",
        description="Insert content at specific row(s) in a file. Rows are 0-based.",
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to the file",
                },
                "content": {
                    "type": "string",
                    "description": "Content to insert",
                },
                "row": {
                    "type": "integer",
                    "description": "Single row number (0-based)",
                },
                "rows": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "Multiple row numbers",
                },
            },
            "required": ["path", "content"],
        },
        handler=insert_file_content,
        emoji="➕",
        label="Insert",
        detail_keys=["path"],
        risk_level="moderate",
        intent="write",
    ),
    ToolDef(
        name="delete_file_content",
        description=(
            "Delete row(s) or substrings within rows. "
            "If substring is provided, only that text is removed from the specified rows."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to the file",
                },
                "row": {
                    "type": "integer",
                    "description": "Single row number (0-based)",
                },
                "rows": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "Multiple row numbers",
                },
                "substring": {
                    "type": "string",
                    "description": "Delete only this substring within rows (optional)",
                },
            },
            "required": ["path"],
        },
        handler=delete_file_content,
        emoji="➖",
        label="Delete Content",
        detail_keys=["path"],
        risk_level="moderate",
        intent="write",
    ),
    ToolDef(
        name="update_file_content",
        description=(
            "Update/replace content at specific row(s). "
            "If substring is provided, only that text is replaced within the rows."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to the file",
                },
                "content": {
                    "type": "string",
                    "description": "New content",
                },
                "row": {
                    "type": "integer",
                    "description": "Single row number (0-based)",
                },
                "rows": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "Multiple row numbers",
                },
                "substring": {
                    "type": "string",
                    "description": "Replace only this substring within rows (optional)",
                },
            },
            "required": ["path", "content"],
        },
        handler=update_file_content,
        emoji="✏️",
        label="Update Content",
        detail_keys=["path"],
        risk_level="moderate",
        intent="write",
    ),
]


# =============================================================================
# Lifecycle Hooks
# =============================================================================


async def initialize() -> None:
    """Initialize terminal tool module."""
    global _current_directory
    _current_directory = Path.cwd()
    logger.info(f"[terminal_tool] Initialized with cwd: {_current_directory}")


async def cleanup() -> None:
    """Cleanup on module unload."""
    global _command_history
    _command_history = []
