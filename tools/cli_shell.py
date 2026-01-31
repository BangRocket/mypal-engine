"""CLI shell command execution - Clara tool module.

Provides tools for executing shell commands in CLI mode with tiered safety controls:
- SAFE: Auto-approve (ls, cat, pwd, etc.)
- NORMAL: Require 'y' confirmation (most commands)
- DANGEROUS: Require typing 'yes' (rm -rf, sudo, etc.)

All commands timeout after ~60 seconds.

Tools: cli_shell_command
Platform: CLI only
"""

from __future__ import annotations

import os
from typing import Any

from adapters.cli.shell_executor import (
    CommandSafety,
    classify_command,
    execute_shell,
)
from tools._base import ToolContext, ToolDef

MODULE_NAME = "cli_shell"
MODULE_VERSION = "0.1.0"

SYSTEM_PROMPT = """
## CLI Shell Commands

You can execute shell commands directly on the user's filesystem (CLI only).

**Safety Levels:**
- **SAFE**: Auto-approved (ls, cat, pwd, echo, grep, find, etc.)
- **NORMAL**: Require 'y' confirmation (most commands)
- **DANGEROUS**: Require typing 'yes' (rm -rf, sudo, chmod 777, etc.)

**Working Directory:**
- Use 'cd <directory>' to change working directory (tracked in session)
- All commands execute in the current working directory
- Current directory persists across commands in the same session

**Timeouts:**
- Commands timeout after 60 seconds by default
- Custom timeout can be specified per command

**When to Use:**
- User asks to list files, check system info, run scripts
- Working with the filesystem or system commands
- Any task requiring shell command execution

**Note:** This tool only works in CLI mode, not Discord.
""".strip()


# --- Tool Handlers ---


async def cli_shell_command(args: dict[str, Any], ctx: ToolContext) -> str:
    """Execute a shell command with safety classification and timeout."""
    command = args.get("command", "")
    timeout = args.get("timeout", 60)

    if not command:
        return "Error: No command provided"

    # Get console and session from context extra
    console = ctx.extra.get("console")
    session = ctx.extra.get("session")

    if not console or not session:
        return "Error: CLI shell tool requires console and session context (only works in interactive CLI)"

    # Get or initialize working directory
    cwd = ctx.extra.get("shell_cwd", os.getcwd())

    # Get or initialize environment variables
    env = ctx.extra.get("shell_env", {})

    # Handle 'cd' command specially - don't execute, just update cwd
    if command.strip().startswith("cd "):
        # Parse target directory
        target = command.strip()[3:].strip()
        if not target:
            target = os.path.expanduser("~")
        else:
            target = os.path.expanduser(target)

        # Make absolute path relative to current cwd
        if not os.path.isabs(target):
            target = os.path.join(cwd, target)

        # Resolve the path
        try:
            target = os.path.realpath(target)
        except Exception as e:
            return f"Error resolving path: {e}"

        # Check if directory exists
        if not os.path.isdir(target):
            return f"Error: Not a directory: {target}"

        # Update cwd in context
        ctx.extra["shell_cwd"] = target
        return f"Changed to: {target}"

    # Classify command safety
    safety = classify_command(command)

    # Handle approval based on safety level
    approved = False

    if safety == CommandSafety.SAFE:
        # Auto-approve safe commands
        approved = True
    elif safety == CommandSafety.NORMAL:
        # Show command and prompt for 'y'
        console.print(f"\n[yellow]Execute:[/yellow] {command}")
        console.print(f"[dim]Working directory: {cwd}[/dim]")
        prompt_text = "Execute? [y/n]: "
        try:
            response = await session.prompt_async(prompt_text)
            approved = response.strip().lower() == "y"
        except (KeyboardInterrupt, EOFError):
            return "Command cancelled."
    elif safety == CommandSafety.DANGEROUS:
        # Show warning and require typing 'yes'
        console.print(f"\n[red bold]⚠ Dangerous command![/red bold]")
        console.print(f"[yellow]Command:[/yellow] {command}")
        console.print(f"[dim]Working directory: {cwd}[/dim]")
        prompt_text = "Type 'yes' to confirm: "
        try:
            response = await session.prompt_async(prompt_text)
            approved = response.strip() == "yes"
        except (KeyboardInterrupt, EOFError):
            return "Command cancelled."

    if not approved:
        return "Command cancelled."

    # Execute the command
    result = await execute_shell(
        command,
        cwd=cwd,
        env={**os.environ, **env},
        timeout=timeout,
    )

    # Format the result
    output_parts = []

    if result.stdout:
        output_parts.append(result.stdout)

    if result.stderr:
        output_parts.append(f"[stderr]\n{result.stderr}")

    if result.timed_out:
        output_parts.append(f"\n⚠ Command timed out after {timeout} seconds")

    if result.exit_code != 0:
        output_parts.append(f"\n[Exit code: {result.exit_code}]")

    if not output_parts:
        return "[No output]"

    return "\n".join(output_parts)


# --- Tool Definitions ---

TOOLS = [
    ToolDef(
        name="cli_shell_command",
        description=(
            "Execute a shell command on the user's system. "
            "Safe commands (ls, cat, pwd) are auto-approved. "
            "Normal commands require 'y' confirmation. "
            "Dangerous commands (rm -rf, sudo) require typing 'yes'. "
            "Use 'cd <directory>' to change working directory (persists in session). "
            "Commands timeout after 60 seconds by default."
        ),
        parameters={
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Shell command to execute (e.g., 'ls -la', 'cd /tmp', 'python script.py')",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in seconds (default: 60)",
                },
            },
            "required": ["command"],
        },
        handler=cli_shell_command,
        platforms=["cli"],  # CLI-only
    ),
]


# --- Lifecycle Hooks ---


async def initialize() -> None:
    """Initialize CLI shell module."""
    pass  # No special initialization needed


async def cleanup() -> None:
    """Cleanup on module unload."""
    pass
