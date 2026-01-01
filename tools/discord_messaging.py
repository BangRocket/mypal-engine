"""Discord messaging tools.

Provides tools for sending messages to Discord channels other than the current one.
Useful for cross-channel posting (e.g., posting job search results to a #jobs channel).
"""

from __future__ import annotations

from typing import Any

from ._base import ToolContext, ToolDef

MODULE_NAME = "discord_messaging"
MODULE_VERSION = "1.0.0"

SYSTEM_PROMPT = """
## Cross-Channel Messaging
You can send messages to Discord channels other than the one you're currently in.

**Tools:**
- `send_message_to_channel` - Send a message to a specific Discord channel by ID

**Usage Notes:**
- You need the channel ID (a large number like "1234567890123456789")
- You can only send to channels in servers (guilds) that Clara is a member of
- Sending will fail if Clara lacks permission to send messages in that channel
- Use this for posting results to dedicated channels (e.g., job listings to #jobs)
""".strip()


async def send_message_to_channel(args: dict[str, Any], ctx: ToolContext) -> str:
    """Send a message to a specified Discord channel."""
    channel_id = args.get("channel_id")
    content = args.get("content", "")
    embed_data = args.get("embed")

    if not channel_id:
        return "Error: channel_id is required"

    if not content and not embed_data:
        return "Error: Either content or embed is required"

    # Get the Discord bot from context
    bot = ctx.extra.get("bot")
    if not bot:
        return "Error: Discord bot not available in this context"

    # Parse channel ID (handle string or int)
    try:
        channel_id_int = int(channel_id)
    except (ValueError, TypeError):
        return (
            f"Error: Invalid channel_id '{channel_id}' - "
            "must be a valid Discord channel ID (large integer)"
        )

    # Get the channel
    try:
        channel = bot.get_channel(channel_id_int)
        if not channel:
            # Try fetching if not in cache
            try:
                channel = await bot.fetch_channel(channel_id_int)
            except Exception:
                return (
                    f"Error: Channel {channel_id} not found. "
                    "Make sure Clara is in the server containing this channel."
                )
    except Exception as e:
        return f"Error accessing channel: {e}"

    # Verify it's a text channel we can send to
    if not hasattr(channel, "send"):
        return (
            f"Error: Channel {channel_id} is not a text channel "
            "that can receive messages"
        )

    # Check permissions
    if hasattr(channel, "guild") and channel.guild:
        me = channel.guild.me
        if me:
            permissions = channel.permissions_for(me)
            if not permissions.send_messages:
                return (
                    f"Error: Clara doesn't have permission to send messages "
                    f"in #{channel.name}"
                )
            if embed_data and not permissions.embed_links:
                return (
                    f"Error: Clara doesn't have permission to embed links "
                    f"in #{channel.name}"
                )

    # Build embed if provided
    embed = None
    if embed_data:
        try:
            import discord

            embed = discord.Embed()
            if "title" in embed_data:
                embed.title = embed_data["title"]
            if "description" in embed_data:
                embed.description = embed_data["description"]
            if "color" in embed_data:
                embed.color = discord.Color(int(embed_data["color"]))
            if "url" in embed_data:
                embed.url = embed_data["url"]
            if "fields" in embed_data:
                for field in embed_data["fields"]:
                    embed.add_field(
                        name=field.get("name", ""),
                        value=field.get("value", ""),
                        inline=field.get("inline", False),
                    )
            if "footer" in embed_data:
                footer = embed_data["footer"]
                embed.set_footer(
                    text=footer.get("text", ""),
                    icon_url=footer.get("icon_url"),
                )
            if "thumbnail" in embed_data:
                embed.set_thumbnail(url=embed_data["thumbnail"])
            if "image" in embed_data:
                embed.set_image(url=embed_data["image"])
        except Exception as e:
            return f"Error building embed: {e}"

    # Send the message
    try:
        if embed:
            await channel.send(content=content or None, embed=embed)
        else:
            await channel.send(content=content)

        channel_name = getattr(channel, "name", str(channel_id))
        guild_name = getattr(channel, "guild", None)
        guild_str = f" in {guild_name.name}" if guild_name else ""

        return f"Message sent to #{channel_name}{guild_str}"

    except Exception as e:
        return f"Error sending message: {e}"


# --- Tool Definitions ---

TOOLS = [
    ToolDef(
        name="send_message_to_channel",
        description=(
            "Send a message to a specific Discord channel by ID. "
            "Use this to post content to channels other than the current "
            "conversation. For example, posting job search results to a "
            "#jobs channel, or alerts to a #notifications channel."
        ),
        parameters={
            "type": "object",
            "properties": {
                "channel_id": {
                    "type": "string",
                    "description": (
                        "The Discord channel ID to send to (a large number "
                        "like '1234567890123456789'). Get this by right-clicking "
                        "a channel in Discord with Developer Mode enabled."
                    ),
                },
                "content": {
                    "type": "string",
                    "description": (
                        "The message text to send. Supports Discord markdown."
                    ),
                },
                "embed": {
                    "type": "object",
                    "description": (
                        "Optional: A Discord embed object for rich formatting. "
                        "Supports title, description, color, url, fields, "
                        "footer, thumbnail, image."
                    ),
                    "properties": {
                        "title": {"type": "string"},
                        "description": {"type": "string"},
                        "color": {
                            "type": "integer",
                            "description": "Embed color as integer (0x00FF00)",
                        },
                        "url": {"type": "string"},
                        "fields": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "name": {"type": "string"},
                                    "value": {"type": "string"},
                                    "inline": {"type": "boolean"},
                                },
                            },
                        },
                        "footer": {
                            "type": "object",
                            "properties": {
                                "text": {"type": "string"},
                                "icon_url": {"type": "string"},
                            },
                        },
                        "thumbnail": {"type": "string"},
                        "image": {"type": "string"},
                    },
                },
            },
            "required": ["channel_id"],
        },
        handler=send_message_to_channel,
        platforms=["discord"],
        requires=["discord"],
    ),
]


# --- Lifecycle Hooks ---


async def initialize() -> None:
    """Initialize on module load."""
    print("[discord_messaging] Cross-channel messaging tools loaded")


async def cleanup() -> None:
    """Cleanup on module unload."""
    pass
