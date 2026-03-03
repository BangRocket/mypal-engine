"""Human-readable tool summary generation for system prompts."""

import logging

logger = logging.getLogger(__name__)

DEFAULT_MAX_CHARS = 5000
DESC_MAX_CHARS = 80


def build_tool_summary_section(
    tools: list[dict],
    max_chars: int = DEFAULT_MAX_CHARS,
) -> list[str]:
    """Build a concise, human-readable tool listing for injection into the system prompt.

    Groups tools by prefix (Core, MCP, Subagent) and returns a list of lines.
    Core group always comes first, then alphabetical by group name.

    Args:
        tools: List of tool dicts with 'name', 'description', and 'parameters' keys.
        max_chars: Budget for group content (header/footer excluded from budget).

    Returns:
        List of lines forming the tool summary section, or empty list if no tools.
    """
    if not tools:
        return []

    groups: dict[str, list[tuple[str, str]]] = {}

    for tool in tools:
        name = tool.get("name", "")
        desc = tool.get("description", "")
        desc = _truncate_desc(desc)
        group = _get_group(name)
        if group not in groups:
            groups[group] = []
        groups[group].append((name, desc))

    lines = [
        "## Available Tools",
        "Tool names are case-sensitive. Call tools exactly as listed.",
        "",
    ]

    total_chars = 0
    remaining_count = 0

    # Core first, then alphabetical by group name
    sorted_groups = sorted(groups.keys(), key=lambda g: (0 if g == "Core" else 1, g))

    for group in sorted_groups:
        group_lines = [f"{group} Tools:"]
        for name, desc in groups[group]:
            group_lines.append(f"- {name}: {desc}")
        group_lines.append("")

        group_text = "\n".join(group_lines)
        if total_chars + len(group_text) > max_chars:
            remaining_count += len(groups[group])
            continue

        lines.extend(group_lines)
        total_chars += len(group_text)

    if remaining_count > 0:
        lines.append(f"...and {remaining_count} more tools available")

    return lines


def _truncate_desc(desc: str) -> str:
    """Extract first sentence and truncate to DESC_MAX_CHARS."""
    for sep in [". ", ".\n", "\n"]:
        if sep in desc:
            desc = desc[: desc.index(sep)]
            break
    if len(desc) > DESC_MAX_CHARS:
        desc = desc[: DESC_MAX_CHARS - 3] + "..."
    return desc


def _get_group(name: str) -> str:
    """Determine the group for a tool based on its name prefix."""
    if name.startswith("mcp__"):
        parts = name.split("__")
        if len(parts) >= 2:
            return f"MCP ({parts[1]})"
        return "MCP"
    if name.startswith("subagent_"):
        return "Subagent"
    return "Core"
