"""Internal memory operations for adapters (gateway-secret, explicit user_id).

Proxies ClaraMemory.get_all/search/delete_all by an explicit (platform) user_id,
preserving the adapters' existing direct-call behavior. The canonical-scoped
/api/v1/memories router (X-Canonical-User-Id) remains for Rails/web-UI.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from mypalclara.gateway.api.auth import require_gateway_secret

router = APIRouter()


def _memory():
    from mypalclara.core.memory import ClaraMemory

    return ClaraMemory()


@router.get("/count")
async def memory_count(user_id: str, _: bool = Depends(require_gateway_secret)) -> dict:
    memories = _memory().get_all(user_id=user_id)
    return {"count": len(memories) if memories else 0}


@router.get("/search")
async def memory_search(
    user_id: str,
    query: str,
    limit: int = 10,
    _: bool = Depends(require_gateway_secret),
) -> dict:
    results = _memory().search(query, user_id=user_id, limit=limit)
    return {"results": results or []}


@router.delete("")
async def memory_delete_all(user_id: str, _: bool = Depends(require_gateway_secret)) -> dict:
    _memory().delete_all(user_id=user_id)
    return {"deleted": True}
