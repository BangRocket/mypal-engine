"""Claude Code MCP Server - Delegate coding tasks to Claude Code CLI."""
import logging
import sys
from mcp.server.fastmcp import FastMCP

# Import existing implementation
from tools.claude_code import (
    _check_cli_auth,
    DEFAULT_WORKDIR,
    MAX_TURNS,
    get_workdir,
    set_workdir,
)

# stderr-only logging
logger = logging.getLogger(__name__)
handler = logging.StreamHandler(sys.stderr)
handler.setFormatter(logging.Formatter("[claude_code] %(levelname)s: %(message)s"))
logger.addHandler(handler)
logger.setLevel(logging.INFO)

# Create MCP server
mcp = FastMCP("claude-code")


@mcp.tool()
async def claude_code(
    user_id: str,
    prompt: str,
    working_dir: str = "",
    max_turns: int = 0,
    allowed_tools: str = "",
) -> str:
    """Execute a coding task using Claude Code agent.

    Claude Code can read/write files, run shell commands, search code,
    and perform complex multi-step coding tasks autonomously.

    Args:
        user_id: User identifier for workdir isolation
        prompt: Description of the coding task to perform
        working_dir: Optional working directory (overrides default)
        max_turns: Max agent steps (default: 10)
        allowed_tools: Comma-separated list of allowed tools (default: all)

    Returns:
        Task result or error message
    """
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
        return "Error: claude-agent-sdk not installed. Run: pip install claude-agent-sdk"

    if not prompt.strip():
        return "Error: No prompt provided. Describe the coding task you want done."

    # Resolve working directory
    workdir = working_dir or get_workdir(user_id)
    if not workdir:
        return (
            "Error: No working directory configured. "
            "Either set CLAUDE_CODE_WORKDIR env var, pass working_dir parameter, "
            "or use claude_code_set_workdir first."
        )

    from pathlib import Path

    workdir_path = Path(workdir).resolve()
    if not workdir_path.exists():
        return f"Error: Working directory does not exist: {workdir_path}"
    if not workdir_path.is_dir():
        return f"Error: Path is not a directory: {workdir_path}"

    # Store for future calls
    set_workdir(user_id, str(workdir_path))

    # Build options
    options = ClaudeAgentOptions(
        cwd=str(workdir_path),
        max_turns=max_turns if max_turns > 0 else MAX_TURNS,
        permission_mode="acceptEdits",
    )

    if allowed_tools:
        options.allowed_tools = [t.strip() for t in allowed_tools.split(",")]

    # Execute
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
                if hasattr(message, "content"):
                    for block in message.content:
                        if isinstance(block, TextBlock):
                            results.append(block.text)
                        elif isinstance(block, ToolResultBlock):
                            if block.is_error:
                                results.append(f"[Tool Error: {block.content[:200]}...]")
                            elif len(block.content) > 500:
                                results.append(f"[Output truncated: {len(block.content)} chars]")
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
        combined = "\n".join(results)
        if len(combined) > 4000:
            combined = combined[:4000] + "\n\n[Output truncated]"
        output_parts.append(combined)
    else:
        output_parts.append("Task completed (no output text).")

    return "\n".join(output_parts)


@mcp.tool()
async def claude_code_status(user_id: str = "") -> str:
    """Check Claude Code availability and authentication status.

    Args:
        user_id: Optional user ID to check their workdir

    Returns:
        Status information including auth method and workdir
    """
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
    workdir = get_workdir(user_id) if user_id else None
    if workdir:
        lines.append(f"\nWorking directory: {workdir}")
    elif DEFAULT_WORKDIR:
        lines.append(f"\nDefault workdir: {DEFAULT_WORKDIR}")
    else:
        lines.append("\nWorking directory: Not set")

    return "\n".join(lines)


@mcp.tool()
def claude_code_set_workdir(user_id: str, directory: str) -> str:
    """Set the working directory for Claude Code operations.

    This directory is where Claude Code will read/write files.
    Setting persists for the user's session.

    Args:
        user_id: User identifier
        directory: Absolute path to the working directory

    Returns:
        Success/failure message
    """
    if not directory.strip():
        return "Error: No directory provided."

    from pathlib import Path

    workdir_path = Path(directory).resolve()

    if not workdir_path.exists():
        return f"Error: Directory does not exist: {workdir_path}"
    if not workdir_path.is_dir():
        return f"Error: Path is not a directory: {workdir_path}"

    set_workdir(user_id, str(workdir_path))
    return f"Working directory set to: {workdir_path}"


@mcp.tool()
def claude_code_get_workdir(user_id: str) -> str:
    """Get the current working directory for Claude Code operations.

    Args:
        user_id: User identifier

    Returns:
        Current working directory or message if not set
    """
    workdir = get_workdir(user_id)
    if workdir:
        return f"Current working directory: {workdir}"
    return (
        "No working directory configured. "
        "Set CLAUDE_CODE_WORKDIR or use claude_code_set_workdir."
    )


# Entry point for stdio transport
if __name__ == "__main__":
    mcp.run()
