"""Guild configuration endpoints (internal, gateway-secret auth)."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from mypalclara.db import guild_config as gc
from mypalclara.gateway.api.auth import require_gateway_secret

router = APIRouter()


class GuildConfigUpdate(BaseModel):
    default_tier: str | None = None
    auto_tier_enabled: str | None = None
    ors_enabled: str | None = None
    ors_channel_id: str | None = None
    ors_quiet_start: str | None = None
    ors_quiet_end: str | None = None
    sandbox_mode: str | None = None


@router.get("/guilds/{guild_id}/config")
async def get_config(guild_id: str, _: bool = Depends(require_gateway_secret)) -> dict:
    return gc.get_or_create_guild_config(guild_id)


@router.put("/guilds/{guild_id}/config")
async def update_config(
    guild_id: str,
    body: GuildConfigUpdate,
    _: bool = Depends(require_gateway_secret),
) -> dict:
    return gc.update_guild_config(guild_id, **body.model_dump(exclude_unset=True))
