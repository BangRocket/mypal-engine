"""Claude Code agent tool for autonomous coding tasks.

Uses the Claude Agent SDK to delegate coding tasks to Claude Code,
which can read/write files, run commands, and execute complex
multi-step coding workflows.

Authentication (one of these):
- Claude Max/Pro subscription: Login via `claude login` in terminal
- API key: Set ANTHROPIC_API_KEY env var

Optional: CLAUDE_CODE_WORKDIR env var (default working directory)
"""

from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path
from typing import Any

from ._base import ToolContext, ToolDef

MODULE_NAME = "claude_code"
MODULE_VERSION = "1.1.0"

SYSTEM_PROMPT = """
## Claude Code Integration
You can delegate complex coding tasks to Claude Code, a powerful AI coding agent.

**Tools:**
- `claude_code` - Execute coding tasks autonomously using Claude Code
- `claude_code_status` - Check Claude Code availability and auth method
- `claude_code_set_workdir` / `claude_code_get_workdir` - Manage working directory

**Authentication:**
- Works with Claude Max/Pro subscription (no API key needed)
- Or with ANTHROPIC_API_KEY for API-based auth
- Use `claude_code_status` to check current auth method

**Capabilities:**
- Read and write files in the configured working directory
- Execute shell commands (bash, python, npm, etc.)
- Search code with glob and grep patterns
- Multi-step, agentic workflows

**When to Use:**
- Complex coding tasks that require reading/modifying multiple files
- Running tests and fixing issues
- Refactoring or adding features to a codebase
- Any task that benefits from autonomous file operations

**Working Directory:**
- Set via CLAUDE_CODE_WORKDIR env var or `working_dir` parameter
- Claude Code can only access files within this directory

**Example Prompts:**
- "Add error handling to the parse_config function"
- "Write unit tests for the utils module"
- "Fix the type errors in src/api/"
- "Create a new REST endpoint for user preferences"
""".strip()

# Configuration
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
DEFAULT_WORKDIR = os.getenv("CLAUDE_CODE_WORKDIR", "")
MAX_TURNS = int(os.getenv("CLAUDE_CODE_MAX_TURNS", "10"))

# Per-user working directories (session state)
_user_workdirs: dict[str, str] = {}

# Cached auth status
_auth_status: dict[str, Any] | None = None


def _find_claude_cli() -> str | None:
    """Find the Claude Code CLI executable."""
    # Check if claude is in PATH
    claude_path = shutil.which("claude")
    if claude_path:
        return claude_path

    # Check common installation locations
    home = Path.home()
    common_paths = [
        home / ".claude" / "bin" / "claude",
        home / ".local" / "bin" / "claude",
        Path("/usr/local/bin/claude"),
    ]
    for path in common_paths:
        if path.exists() and path.is_file():
            return str(path)

    return None


async def _check_cli_auth() -> dict[str, Any]:
    """Check Claude CLI authentication status.

    Returns dict with:
        - cli_installed: bool
        - cli_path: str | None
        - authenticated: bool
        - auth_method: "api_key" | "subscription" | None
        - error: str | None
    """
    global _auth_status
    if _auth_status is not None:
        return _auth_status

    result: dict[str, Any] = {
        "cli_installed": False,
        "cli_path": None,
        "authenticated": False,
        "auth_method": None,
        "error": None,
    }

    # Check for API key first
    if ANTHROPIC_API_KEY:
        result["authenticated"] = True
        result["auth_method"] = "api_key"
        # Still check if CLI exists for execution
        cli_path = _find_claude_cli()
        result["cli_installed"] = cli_path is not None
        result["cli_path"] = cli_path
        _auth_status = result
        return result

    # Check for CLI
    cli_path = _find_claude_cli()
    if not cli_path:
        result["error"] = "Claude CLI not found"
        _auth_status = result
        return result

    result["cli_installed"] = True
    result["cli_path"] = cli_path

    # Check CLI auth status by running 'claude --version' or similar
    # The SDK handles auth internally, so we just verify CLI works
    try:
        proc = await asyncio.create_subprocess_exec(
            cli_path,
            "--version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)

        if proc.returncode == 0:
            # CLI works - assume subscription auth if no API key
            result["authenticated"] = True
            result["auth_method"] = "subscription"
        else:
            result["error"] = f"CLI error: {stderr.decode()[:100]}"
    except TimeoutError:
        result["error"] = "CLI timeout"
    except Exception as e:
        result["error"] = f"CLI check failed: {str(e)}"

    _auth_status = result
    return result


def is_configured() -> bool:
    """Check if Claude Code is configured (has API key).

    Note: This only checks for API key. The CLI may also work with
    Max/Pro subscription auth. Use check_availability() for full check.
    """
    return bool(ANTHROPIC_API_KEY)


async def check_availability() -> tuple[bool, str]:
    """Check if Claude Code is available (API key or subscription).

    Returns:
        (available, message) tuple
    """
    status = await _check_cli_auth()

    if status["authenticated"]:
        method = status["auth_method"]
        if method == "api_key":
            return True, "Claude Code ready (API key)"
        else:
            return True, "Claude Code ready (Max/Pro subscription)"

    if status["error"]:
        return False, f"Claude Code not available: {status['error']}"

    return (
        False,
        "Claude Code not configured. Set ANTHROPIC_API_KEY or 'claude login'",
    )


def get_workdir(user_id: str) -> str | None:
    """Get the working directory for a user."""
    return _user_workdirs.get(user_id) or DEFAULT_WORKDIR or None


def set_workdir(user_id: str, workdir: str) -> None:
    """Set the working directory for a user."""
    _user_workdirs[user_id] = workdir


async def claude_code_execute(args: dict[str, Any], ctx: ToolContext) -> str:
    """Execute a coding task using Claude Code agent."""
    try:
        from claude_agent_sdk import ClaudeAgentOptions, query
        from claude_agent_sdk.types import (
            AssistantMessage,
            ResultMessage,
            TextBlock,
            ToolResultBlock,
            ToolUseBlock,
        )
    except ImportError:
        return (
            "Error: claude-agent-sdk not installed. "
            "Run: pip install claude-agent-sdk"
        )

    prompt = args.get("prompt", "").strip()
    if not prompt:
        return "Error: No prompt provided. Describe the coding task you want done."

    # Get working directory
    working_dir = args.get("working_dir", "")
    if not working_dir:
        working_dir = get_workdir(ctx.user_id)

    if not working_dir:
        return (
            "Error: No working directory configured. "
            "Either set CLAUDE_CODE_WORKDIR env var, or pass working_dir parameter."
        )

    # Validate working directory exists
    workdir_path = Path(working_dir).resolve()
    if not workdir_path.exists():
        return f"Error: Working directory does not exist: {workdir_path}"
    if not workdir_path.is_dir():
        return f"Error: Path is not a directory: {workdir_path}"

    # Store for future calls
    set_workdir(ctx.user_id, str(workdir_path))

    # Get optional parameters
    max_turns = args.get("max_turns", MAX_TURNS)
    allowed_tools = args.get("allowed_tools")

    # Configure Claude Code options
    options = ClaudeAgentOptions(
        cwd=str(workdir_path),
        max_turns=max_turns,
        # Accept file edits automatically for smooth operation
        permission_mode="acceptEdits",
    )

    # Optionally restrict tools
    if allowed_tools:
        if isinstance(allowed_tools, str):
            allowed_tools = [t.strip() for t in allowed_tools.split(",")]
        options.allowed_tools = allowed_tools

    # Collect results
    results: list[str] = []
    tool_calls: list[str] = []

    try:
        async for message in query(prompt=prompt, options=options):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        results.append(block.text)
                    elif isinstance(block, ToolUseBlock):
                        tool_calls.append(f"[Tool: {block.name}]")
            elif isinstance(message, ResultMessage):
                # Final result
                if hasattr(message, "content"):
                    for block in message.content:
                        if isinstance(block, TextBlock):
                            results.append(block.text)
                        elif isinstance(block, ToolResultBlock):
                            # Summarize tool results (can be verbose)
                            if block.is_error:
                                results.append(
                                    f"[Tool Error: {block.content[:200]}...]"
                                )
                            elif len(block.content) > 500:
                                chars = len(block.content)
                                results.append(f"[Output truncated: {chars} chars]")

    except Exception as e:
        return f"Claude Code error: {str(e)}"

    # Format output
    output_parts = []

    if tool_calls:
        output_parts.append(f"**Tools used:** {', '.join(tool_calls[:10])}")
        if len(tool_calls) > 10:
            output_parts.append(f"...and {len(tool_calls) - 10} more")

    if results:
        output_parts.append("\n**Result:**")
        # Combine results, truncating if too long
        combined = "\n".join(results)
        if len(combined) > 4000:
            combined = combined[:4000] + "\n\n[Output truncated]"
        output_parts.append(combined)
    else:
        output_parts.append("Task completed (no output text).")

    return "\n".join(output_parts)


async def claude_code_set_workdir(args: dict[str, Any], ctx: ToolContext) -> str:
    """Set the working directory for Claude Code operations."""
    workdir = args.get("directory", "").strip()
    if not workdir:
        return "Error: No directory provided."

    workdir_path = Path(workdir).resolve()
    if not workdir_path.exists():
        return f"Error: Directory does not exist: {workdir_path}"
    if not workdir_path.is_dir():
        return f"Error: Path is not a directory: {workdir_path}"

    set_workdir(ctx.user_id, str(workdir_path))
    return f"Working directory set to: {workdir_path}"


async def claude_code_get_workdir(args: dict[str, Any], ctx: ToolContext) -> str:
    """Get the current working directory for Claude Code operations."""
    workdir = get_workdir(ctx.user_id)
    if workdir:
        return f"Current working directory: {workdir}"
    return (
        "No working directory configured. "
        "Set CLAUDE_CODE_WORKDIR or use claude_code_set_workdir."
    )


async def claude_code_status(args: dict[str, Any], ctx: ToolContext) -> str:
    """Check Claude Code availability and authentication status."""
    status = await _check_cli_auth()

    lines = ["**Claude Code Status**\n"]

    # CLI status
    if status["cli_installed"]:
        lines.append(f"CLI installed: Yes ({status['cli_path']})")
    else:
        lines.append("CLI installed: No")

    # Auth status
    if status["authenticated"]:
        method = status["auth_method"]
        if method == "api_key":
            lines.append("Authentication: API key (ANTHROPIC_API_KEY)")
        else:
            lines.append("Authentication: Max/Pro subscription")
        lines.append("Status: Ready")
    else:
        lines.append("Authentication: Not configured")
        if status["error"]:
            lines.append(f"Error: {status['error']}")
        lines.append("\nTo configure:")
        lines.append("- Set ANTHROPIC_API_KEY env var, OR")
        lines.append("- Run `claude login` in terminal for Max/Pro subscription")

    # Working directory
    workdir = get_workdir(ctx.user_id)
    if workdir:
        lines.append(f"\nWorking directory: {workdir}")
    elif DEFAULT_WORKDIR:
        lines.append(f"\nDefault workdir: {DEFAULT_WORKDIR}")
    else:
        lines.append("\nWorking directory: Not set")

    return "\n".join(lines)


# --- Tool Definitions ---

TOOLS = [
    ToolDef(
        name="claude_code",
        description=(
            "Execute a coding task using Claude Code, an autonomous AI coding agent. "
            "Claude Code can read/write files, run shell commands, search code, "
            "and perform complex multi-step coding tasks. "
            "Ideal for: refactoring, adding features, fixing bugs, writing tests, etc. "
            "The agent works within the configured working directory."
        ),
        parameters={
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": (
                        "The coding task to perform. Be specific about what "
                        "you want done. Examples: 'Add input validation to "
                        "the user form', 'Fix the failing tests in tests/api/', "
                        "'Create a new endpoint for user preferences'"
                    ),
                },
                "working_dir": {
                    "type": "string",
                    "description": (
                        "Optional: Working directory for this task. "
                        "Overrides the default/previously set directory. "
                        "Must be an absolute path."
                    ),
                },
                "max_turns": {
                    "type": "integer",
                    "description": (
                        "Maximum number of turns/steps for the agent (default: 10). "
                        "Increase for complex tasks that require many file operations."
                    ),
                },
                "allowed_tools": {
                    "type": "string",
                    "description": (
                        "Comma-separated list of tools to allow. "
                        "Default: all tools (Read, Write, Bash, Glob, Grep, etc.)"
                    ),
                },
            },
            "required": ["prompt"],
        },
        handler=claude_code_execute,
        platforms=["discord"],  # Available on Discord platform
    ),
    ToolDef(
        name="claude_code_set_workdir",
        description=(
            "Set the working directory for Claude Code operations. "
            "This directory is where Claude Code will read/write files. "
            "Persists for the current session."
        ),
        parameters={
            "type": "object",
            "properties": {
                "directory": {
                    "type": "string",
                    "description": "Absolute path to the working directory",
                },
            },
            "required": ["directory"],
        },
        handler=claude_code_set_workdir,
        platforms=["discord"],
    ),
    ToolDef(
        name="claude_code_get_workdir",
        description="Get the current working directory for Claude Code operations.",
        parameters={
            "type": "object",
            "properties": {},
        },
        handler=claude_code_get_workdir,
        platforms=["discord"],
    ),
    ToolDef(
        name="claude_code_status",
        description=(
            "Check Claude Code availability, authentication method, "
            "and current configuration. Shows if using API key or "
            "Max/Pro subscription."
        ),
        parameters={
            "type": "object",
            "properties": {},
        },
        handler=claude_code_status,
        platforms=["discord"],
    ),
]


# --- Lifecycle Hooks ---


async def initialize() -> None:
    """Initialize Claude Code module."""
    available, message = await check_availability()

    if available:
        print(f"[claude_code] {message}")
        if DEFAULT_WORKDIR:
            print(f"[claude_code] Default workdir: {DEFAULT_WORKDIR}")
    else:
        print(f"[claude_code] {message}")
        print("[claude_code] Tools will be disabled")
        global TOOLS
        TOOLS = []


async def cleanup() -> None:
    """Cleanup on module unload."""
    global _auth_status
    _user_workdirs.clear()
    _auth_status = None
