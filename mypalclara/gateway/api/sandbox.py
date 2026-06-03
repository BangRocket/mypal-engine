"""Sandbox status endpoint (internal, gateway-secret auth)."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from mypalclara.gateway.api.auth import require_gateway_secret
from mypalclara.sandbox.manager import get_sandbox_manager

router = APIRouter()


@router.get("/status")
async def sandbox_status(_: bool = Depends(require_gateway_secret)) -> dict:
    """Synthesized sandbox status.

    Note: the manager exposes is_available()/get_stats(), not a get_status();
    this endpoint composes them into the shape the client wants.
    """
    manager = get_sandbox_manager()
    return {"available": manager.is_available(), "stats": manager.get_stats()}
