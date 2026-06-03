"""Backup management endpoints (internal, gateway-secret auth)."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from mypalclara.core.services.backup import get_backup_service
from mypalclara.gateway.api.auth import require_gateway_secret

router = APIRouter()


class BackupRunRequest(BaseModel):
    databases: list[str] | None = None


def _to_dict(result: Any) -> dict:
    if is_dataclass(result):
        return asdict(result)
    if hasattr(result, "__dict__"):
        return dict(result.__dict__)
    return dict(result)


@router.post("/run")
async def run_backup(body: BackupRunRequest, _: bool = Depends(require_gateway_secret)) -> dict:
    service = get_backup_service()
    result = await service.backup_now(databases=body.databases)
    return _to_dict(result)


@router.get("/status")
async def backup_status(_: bool = Depends(require_gateway_secret)) -> dict:
    service = get_backup_service()
    return await service.get_status()
