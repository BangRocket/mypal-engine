"""Discord embed helper functions.

Provides consistent, styled embeds for Clara's slash command responses.
"""

from __future__ import annotations

import discord

# Color constants matching Discord's color palette
EMBED_COLOR_PRIMARY = 0x5865F2  # Discord blurple
EMBED_COLOR_SUCCESS = 0x57F287  # Green
EMBED_COLOR_WARNING = 0xFEE75C  # Yellow
EMBED_COLOR_ERROR = 0xED4245  # Red
EMBED_COLOR_INFO = 0x5865F2  # Blurple (same as primary)


def create_success_embed(title: str, description: str | None = None) -> discord.Embed:
    """Create a success embed (green).

    Args:
        title: Embed title
        description: Optional description text

    Returns:
        Configured Discord Embed
    """
    embed = discord.Embed(
        title=f"\u2705 {title}",
        description=description,
        color=EMBED_COLOR_SUCCESS,
    )
    return embed


def create_error_embed(title: str, description: str | None = None) -> discord.Embed:
    """Create an error embed (red).

    Args:
        title: Embed title
        description: Optional description text

    Returns:
        Configured Discord Embed
    """
    embed = discord.Embed(
        title=f"\u274c {title}",
        description=description,
        color=EMBED_COLOR_ERROR,
    )
    return embed


def create_warning_embed(title: str, description: str | None = None) -> discord.Embed:
    """Create a warning embed (yellow).

    Args:
        title: Embed title
        description: Optional description text

    Returns:
        Configured Discord Embed
    """
    embed = discord.Embed(
        title=f"\u26a0\ufe0f {title}",
        description=description,
        color=EMBED_COLOR_WARNING,
    )
    return embed


def create_info_embed(title: str, description: str | None = None) -> discord.Embed:
    """Create an info embed (blurple).

    Args:
        title: Embed title
        description: Optional description text

    Returns:
        Configured Discord Embed
    """
    embed = discord.Embed(
        title=title,
        description=description,
        color=EMBED_COLOR_INFO,
    )
    return embed


def create_status_embed(
    title: str,
    fields: list[tuple[str, str, bool]] | None = None,
    description: str | None = None,
    color: int = EMBED_COLOR_PRIMARY,
) -> discord.Embed:
    """Create a status embed with optional fields.

    Args:
        title: Embed title
        fields: List of (name, value, inline) tuples
        description: Optional description text
        color: Embed color (default: blurple)

    Returns:
        Configured Discord Embed
    """
    embed = discord.Embed(
        title=title,
        description=description,
        color=color,
    )

    if fields:
        for name, value, inline in fields:
            embed.add_field(name=name, value=value, inline=inline)

    return embed


def create_list_embed(
    title: str,
    items: list[str],
    description: str | None = None,
    color: int = EMBED_COLOR_PRIMARY,
    max_items: int = 25,
) -> discord.Embed:
    """Create an embed with a bulleted list.

    Args:
        title: Embed title
        items: List of items to display
        description: Optional description before the list
        color: Embed color
        max_items: Maximum items to show (default: 25)

    Returns:
        Configured Discord Embed
    """
    # Build list content
    display_items = items[:max_items]
    list_text = "\n".join(f"\u2022 {item}" for item in display_items)

    if len(items) > max_items:
        list_text += f"\n... and {len(items) - max_items} more"

    full_description = description + "\n\n" + list_text if description else list_text

    embed = discord.Embed(
        title=title,
        description=full_description,
        color=color,
    )

    return embed


def create_help_embed(
    topic: str | None = None,
    commands_info: dict[str, str] | None = None,
) -> discord.Embed:
    """Create a help embed for Clara commands.

    Args:
        topic: Optional specific help topic
        commands_info: Optional dict mapping command names to descriptions

    Returns:
        Configured Discord Embed
    """
    embed = discord.Embed(
        title="\U0001f4da Clara Commands",
        color=EMBED_COLOR_PRIMARY,
    )

    if topic:
        embed.title = f"\U0001f4da Help: {topic}"

    if commands_info:
        for cmd, desc in commands_info.items():
            embed.add_field(name=cmd, value=desc, inline=False)
    else:
        embed.description = (
            "Use `/clara help <topic>` for detailed help.\n\n"
            "**Command Groups:**\n"
            "\U0001f50c `/mcp` - MCP server management\n"
            "\U0001f916 `/model` - Model and tier settings\n"
            "\U0001f4ac `/ors` - Proactive messaging\n"
            "\U0001f4e6 `/sandbox` - Code execution\n"
            "\U0001f9e0 `/memory` - Memory system\n"
            "\U0001f4e7 `/email` - Email monitoring\n"
            "\U0001f527 `/clara` - General utilities"
        )

    return embed
