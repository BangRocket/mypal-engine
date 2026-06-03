"""Guild (server) configuration management.

Engine-side home for the guild-config helpers that previously lived inline in the
Discord adapter. Returns plain dicts (serialized inside the session) so callers —
including the HTTP API — never touch detached ORM instances.
"""

from __future__ import annotations

from sqlalchemy import select

from .connection import get_session
from .models import GuildConfig

# Mutable fields a caller may set via update_guild_config().
EDITABLE_FIELDS = (
    "default_tier",
    "auto_tier_enabled",
    "ors_enabled",
    "ors_channel_id",
    "ors_quiet_start",
    "ors_quiet_end",
    "sandbox_mode",
)


def _to_dict(config: GuildConfig) -> dict:
    return {
        "guild_id": config.guild_id,
        "default_tier": config.default_tier,
        "auto_tier_enabled": config.auto_tier_enabled,
        "ors_enabled": config.ors_enabled,
        "ors_channel_id": config.ors_channel_id,
        "ors_quiet_start": config.ors_quiet_start,
        "ors_quiet_end": config.ors_quiet_end,
        "sandbox_mode": config.sandbox_mode,
    }


def get_guild_config(guild_id: str) -> dict | None:
    """Return the guild's config as a dict, or None if unconfigured."""
    with get_session() as session:
        config = session.execute(select(GuildConfig).where(GuildConfig.guild_id == guild_id)).scalar_one_or_none()
        return _to_dict(config) if config else None


def get_or_create_guild_config(guild_id: str) -> dict:
    """Return the guild's config, creating a default row if missing."""
    with get_session() as session:
        config = session.execute(select(GuildConfig).where(GuildConfig.guild_id == guild_id)).scalar_one_or_none()
        if config is None:
            config = GuildConfig(guild_id=guild_id)
            session.add(config)
            session.commit()
            session.refresh(config)
        return _to_dict(config)


def update_guild_config(guild_id: str, **fields) -> dict:
    """Upsert the guild's config, setting only known, non-None fields."""
    with get_session() as session:
        config = session.execute(select(GuildConfig).where(GuildConfig.guild_id == guild_id)).scalar_one_or_none()
        if config is None:
            config = GuildConfig(guild_id=guild_id)
            session.add(config)
        for key, value in fields.items():
            if key in EDITABLE_FIELDS and value is not None:
                setattr(config, key, value)
        session.commit()
        session.refresh(config)
        return _to_dict(config)
