"""Channel-mode configuration endpoints (internal, gateway-secret auth)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from mypalclara.db import channel_config as cc
from mypalclara.gateway.api.auth import require_gateway_secret

router = APIRouter()

_VALID_MODES = {"active", "mention", "off"}


class ChannelModeUpdate(BaseModel):
    guild_id: str
    mode: str
    configured_by: str | None = None


@router.get("/channels/{channel_id}/mode")
async def get_channel_mode(channel_id: str, _: bool = Depends(require_gateway_secret)) -> dict:
    return {"mode": cc.get_channel_mode(channel_id)}


@router.put("/channels/{channel_id}/mode")
async def set_channel_mode(
    channel_id: str,
    body: ChannelModeUpdate,
    _: bool = Depends(require_gateway_secret),
) -> dict:
    if body.mode not in _VALID_MODES:
        raise HTTPException(status_code=400, detail=f"mode must be one of {sorted(_VALID_MODES)}")
    config = cc.set_channel_mode(channel_id, body.guild_id, body.mode, body.configured_by)
    return {"mode": config.mode}


@router.get("/guilds/{guild_id}/channels")
async def list_guild_channels(guild_id: str, _: bool = Depends(require_gateway_secret)) -> list[dict]:
    return [{"channel_id": c.channel_id, "mode": c.mode} for c in cc.get_guild_channels(guild_id)]
