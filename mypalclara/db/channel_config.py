"""Channel configuration management for Discord."""

from __future__ import annotations

import os
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession

from .connection import get_session
from .models import ChannelConfig

# Default mode for unconfigured channels
DEFAULT_MODE = "mention"

# Role name that grants Clara-Admin permissions (configurable via env)
CLARA_ADMIN_ROLE = os.getenv("CLARA_ADMIN_ROLE", "Clara-Admin")

ChannelMode = Literal["active", "mention", "off"]


def get_channel_mode(channel_id: str) -> ChannelMode:
    """Get the configured mode for a channel.

    Returns:
        "active" - Clara participates actively in conversation
        "mention" - Clara only responds to mentions
        "off" - Clara ignores the channel entirely
    """
    with get_session() as session:
        config = session.execute(
            select(ChannelConfig).where(ChannelConfig.channel_id == channel_id)
        ).scalar_one_or_none()

        if config:
            return config.mode
        return DEFAULT_MODE


def set_channel_mode(
    channel_id: str,
    guild_id: str,
    mode: ChannelMode,
    configured_by: str | None = None,
) -> ChannelConfig:
    """Set the mode for a channel.

    Args:
        channel_id: Discord channel ID
        guild_id: Discord server/guild ID
        mode: The mode to set (active, mention, off)
        configured_by: User ID who made the change

    Returns:
        The updated or created ChannelConfig
    """
    with get_session() as session:
        config = session.execute(
            select(ChannelConfig).where(ChannelConfig.channel_id == channel_id)
        ).scalar_one_or_none()

        if config:
            config.mode = mode
            config.configured_by = configured_by
        else:
            config = ChannelConfig(
                channel_id=channel_id,
                guild_id=guild_id,
                mode=mode,
                configured_by=configured_by,
            )
            session.add(config)

        session.commit()
        session.refresh(config)
        return config


def get_guild_channels(guild_id: str) -> list[ChannelConfig]:
    """Get all configured channels for a guild."""
    with get_session() as session:
        configs = session.execute(select(ChannelConfig).where(ChannelConfig.guild_id == guild_id)).scalars().all()
        return list(configs)


def should_respond_to_message(channel_id: str, is_mention: bool) -> bool:
    """Check if Clara should respond to a message in this channel.

    Args:
        channel_id: The channel ID
        is_mention: Whether Clara was mentioned in the message

    Returns:
        True if Clara should process/respond to the message
    """
    mode = get_channel_mode(channel_id)

    if mode == "off":
        return False
    elif mode == "mention":
        return is_mention
    else:  # active
        return True
