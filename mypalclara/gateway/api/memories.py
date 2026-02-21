"""Memory CRUD + search REST endpoints."""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session as DBSession

from mypalclara.db.models import (
    CanonicalUser,
    MemoryDynamics,
    MemoryHistory,
    MemorySupersession,
)
from mypalclara.db.user_identity import resolve_all_user_ids_for_canonical
from mypalclara.gateway.api.auth import get_approved_user, get_db

logger = logging.getLogger("gateway.api.memories")
router = APIRouter()


# --- Request / Response Schemas --------------------------------------------


class MemoryCreate(BaseModel):
    content: str = Field(..., min_length=1)
    category: str | None = None
    is_key: bool = False
    metadata: dict[str, Any] | None = None


class MemoryUpdate(BaseModel):
    content: str | None = None
    category: str | None = None
    is_key: bool | None = None
    metadata: dict[str, Any] | None = None


class MemorySearchRequest(BaseModel):
    query: str = Field(..., min_length=1)
    category: str | None = None
    is_key: bool | None = None
    limit: int = Field(20, ge=1, le=100)
    threshold: float = Field(0.0, ge=0.0, le=1.0)


# --- Helpers ---------------------------------------------------------------


def _get_user_ids(user: CanonicalUser, db: DBSession) -> list[str]:
    """Get all prefixed user IDs for a canonical user (cross-platform)."""
    return resolve_all_user_ids_for_canonical(user.id, db)


def _get_memory_client():
    """Get the ClaraMemory singleton (Rook)."""
    from mypalclara.core.memory import ROOK

    if ROOK is None:
        logger.error("Rook memory system not initialized")
        raise HTTPException(status_code=503, detail="Memory system unavailable")
    return ROOK


# --- Endpoints -------------------------------------------------------------


@router.get("")
async def list_memories(
    category: str | None = None,
    is_key: bool | None = None,
    sort: str = Query("created_at", pattern="^(created_at|updated_at|stability|access_count)$"),
    order: str = Query("desc", pattern="^(asc|desc)$"),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    user: CanonicalUser = Depends(get_approved_user),
    db: DBSession = Depends(get_db),
):
    """List memories with pagination and filters."""
    user_ids = _get_user_ids(user, db)
    if not user_ids:
        return {"memories": [], "total": 0, "offset": offset, "limit": limit}

    memory = _get_memory_client()
    all_memories = []

    for uid in user_ids:
        try:
            result = memory.get_all(user_id=uid)
            if result and "results" in result:
                all_memories.extend(result["results"])
        except Exception as e:
            logger.warning(f"Failed to fetch memories for {uid}: {e}")

    # Enrich with dynamics data
    memory_ids = [m.get("id") for m in all_memories if m.get("id")]
    dynamics_map = {}
    if memory_ids:
        dynamics = db.query(MemoryDynamics).filter(MemoryDynamics.memory_id.in_(memory_ids)).all()
        dynamics_map = {d.memory_id: d for d in dynamics}

    enriched = []
    for m in all_memories:
        mid = m.get("id")
        dyn = dynamics_map.get(mid)

        # Apply filters
        if category and dyn and dyn.category != category:
            continue
        if is_key is not None and dyn and dyn.is_key != is_key:
            continue

        enriched.append(
            {
                "id": mid,
                "content": m.get("memory", ""),
                "metadata": m.get("metadata", {}),
                "created_at": m.get("created_at"),
                "updated_at": m.get("updated_at"),
                "user_id": m.get("user_id"),
                "dynamics": {
                    "stability": dyn.stability if dyn else None,
                    "difficulty": dyn.difficulty if dyn else None,
                    "retrieval_strength": dyn.retrieval_strength if dyn else None,
                    "storage_strength": dyn.storage_strength if dyn else None,
                    "is_key": dyn.is_key if dyn else False,
                    "category": dyn.category if dyn else None,
                    "access_count": dyn.access_count if dyn else 0,
                    "last_accessed_at": dyn.last_accessed_at.isoformat() if dyn and dyn.last_accessed_at else None,
                }
                if dyn
                else None,
            }
        )

    # Sort
    def sort_key(item):
        if sort == "stability" and item.get("dynamics"):
            return item["dynamics"].get("stability") or 0
        if sort == "access_count" and item.get("dynamics"):
            return item["dynamics"].get("access_count") or 0
        return item.get(sort) or ""

    enriched.sort(key=sort_key, reverse=(order == "desc"))

    total = len(enriched)
    paginated = enriched[offset : offset + limit]

    return {"memories": paginated, "total": total, "offset": offset, "limit": limit}


@router.get("/stats")
async def memory_stats(
    user: CanonicalUser = Depends(get_approved_user),
    db: DBSession = Depends(get_db),
):
    """Get memory statistics."""
    user_ids = _get_user_ids(user, db)
    if not user_ids:
        return {"total": 0, "by_category": {}, "key_count": 0}

    dynamics = db.query(MemoryDynamics).filter(MemoryDynamics.user_id.in_(user_ids)).all()

    by_category: dict[str, int] = {}
    key_count = 0
    for d in dynamics:
        cat = d.category or "uncategorized"
        by_category[cat] = by_category.get(cat, 0) + 1
        if d.is_key:
            key_count += 1

    return {"total": len(dynamics), "by_category": by_category, "key_count": key_count}


@router.get("/{memory_id}")
async def get_memory(
    memory_id: str,
    user: CanonicalUser = Depends(get_approved_user),
    db: DBSession = Depends(get_db),
):
    """Get a single memory with full metadata."""
    memory = _get_memory_client()
    user_ids = _get_user_ids(user, db)

    # Try to fetch from each user_id
    for uid in user_ids:
        try:
            result = memory.get(memory_id)
            if result:
                dyn = db.query(MemoryDynamics).filter(MemoryDynamics.memory_id == memory_id).first()
                return {
                    "id": memory_id,
                    "content": result.get("memory", ""),
                    "metadata": result.get("metadata", {}),
                    "created_at": result.get("created_at"),
                    "updated_at": result.get("updated_at"),
                    "user_id": result.get("user_id"),
                    "dynamics": {
                        "stability": dyn.stability,
                        "difficulty": dyn.difficulty,
                        "retrieval_strength": dyn.retrieval_strength,
                        "storage_strength": dyn.storage_strength,
                        "is_key": dyn.is_key,
                        "category": dyn.category,
                        "access_count": dyn.access_count,
                        "last_accessed_at": dyn.last_accessed_at.isoformat() if dyn.last_accessed_at else None,
                    }
                    if dyn
                    else None,
                }
        except Exception:
            continue

    raise HTTPException(status_code=404, detail="Memory not found")


@router.post("")
async def create_memory(
    body: MemoryCreate,
    user: CanonicalUser = Depends(get_approved_user),
    db: DBSession = Depends(get_db),
):
    """Create a new memory."""
    user_ids = _get_user_ids(user, db)
    if not user_ids:
        raise HTTPException(status_code=400, detail="No platform linked")

    memory = _get_memory_client()
    primary_uid = user_ids[0]

    messages = [{"role": "user", "content": body.content}]
    metadata = body.metadata or {}

    result = memory.add(messages, user_id=primary_uid, metadata=metadata)

    # Set dynamics if provided
    if result and "results" in result:
        for r in result["results"]:
            mid = r.get("id")
            if mid and (body.category or body.is_key):
                dyn = db.query(MemoryDynamics).filter(MemoryDynamics.memory_id == mid).first()
                if dyn:
                    if body.category:
                        dyn.category = body.category
                    if body.is_key:
                        dyn.is_key = body.is_key
                    db.commit()

    return {"ok": True, "result": result}


@router.put("/{memory_id}")
async def update_memory(
    memory_id: str,
    body: MemoryUpdate,
    user: CanonicalUser = Depends(get_approved_user),
    db: DBSession = Depends(get_db),
):
    """Update a memory's content and/or metadata."""
    memory = _get_memory_client()

    if body.content is not None:
        memory.update(memory_id, data=body.content)

    # Update dynamics
    if body.category is not None or body.is_key is not None:
        dyn = db.query(MemoryDynamics).filter(MemoryDynamics.memory_id == memory_id).first()
        if dyn:
            if body.category is not None:
                dyn.category = body.category
            if body.is_key is not None:
                dyn.is_key = body.is_key
            db.commit()

    return {"ok": True}


@router.delete("/{memory_id}")
async def delete_memory(
    memory_id: str,
    user: CanonicalUser = Depends(get_approved_user),
    db: DBSession = Depends(get_db),
):
    """Delete a memory."""
    memory = _get_memory_client()
    memory.delete(memory_id)
    return {"ok": True}


@router.get("/{memory_id}/history")
async def memory_history(
    memory_id: str,
    user: CanonicalUser = Depends(get_approved_user),
    db: DBSession = Depends(get_db),
):
    """Get change history for a memory."""
    history = (
        db.query(MemoryHistory)
        .filter(MemoryHistory.memory_id == memory_id)
        .order_by(MemoryHistory.created_at.desc())
        .all()
    )
    return {
        "history": [
            {
                "id": h.id,
                "event": h.event,
                "old_memory": h.old_memory,
                "new_memory": h.new_memory,
                "created_at": h.created_at.isoformat() if h.created_at else None,
            }
            for h in history
        ]
    }


@router.get("/{memory_id}/dynamics")
async def memory_dynamics(
    memory_id: str,
    user: CanonicalUser = Depends(get_approved_user),
    db: DBSession = Depends(get_db),
):
    """Get FSRS dynamics state for a memory."""
    dyn = db.query(MemoryDynamics).filter(MemoryDynamics.memory_id == memory_id).first()
    if not dyn:
        raise HTTPException(status_code=404, detail="Dynamics not found")

    # Get supersessions
    supersessions = (
        db.query(MemorySupersession)
        .filter((MemorySupersession.old_memory_id == memory_id) | (MemorySupersession.new_memory_id == memory_id))
        .all()
    )

    return {
        "memory_id": dyn.memory_id,
        "stability": dyn.stability,
        "difficulty": dyn.difficulty,
        "retrieval_strength": dyn.retrieval_strength,
        "storage_strength": dyn.storage_strength,
        "is_key": dyn.is_key,
        "importance_weight": dyn.importance_weight,
        "category": dyn.category,
        "access_count": dyn.access_count,
        "last_accessed_at": dyn.last_accessed_at.isoformat() if dyn.last_accessed_at else None,
        "created_at": dyn.created_at.isoformat() if dyn.created_at else None,
        "supersessions": [
            {
                "id": s.id,
                "old_memory_id": s.old_memory_id,
                "new_memory_id": s.new_memory_id,
                "reason": s.reason,
                "confidence": s.confidence,
            }
            for s in supersessions
        ],
    }


@router.post("/search")
async def search_memories(
    body: MemorySearchRequest,
    user: CanonicalUser = Depends(get_approved_user),
    db: DBSession = Depends(get_db),
):
    """Semantic search across memories."""
    user_ids = _get_user_ids(user, db)
    if not user_ids:
        return {"results": []}

    memory = _get_memory_client()
    all_results = []

    for uid in user_ids:
        try:
            result = memory.search(body.query, user_id=uid, limit=body.limit)
            if result and "results" in result:
                all_results.extend(result["results"])
        except Exception as e:
            logger.warning(f"Search failed for {uid}: {e}")

    # Filter by threshold
    if body.threshold > 0:
        all_results = [r for r in all_results if r.get("score", 0) >= body.threshold]

    # Sort by score descending
    all_results.sort(key=lambda r: r.get("score", 0), reverse=True)

    # Enrich with dynamics
    memory_ids = [r.get("id") for r in all_results if r.get("id")]
    dynamics_map = {}
    if memory_ids:
        dynamics = db.query(MemoryDynamics).filter(MemoryDynamics.memory_id.in_(memory_ids)).all()
        dynamics_map = {d.memory_id: d for d in dynamics}

    enriched = []
    for r in all_results[: body.limit]:
        mid = r.get("id")
        dyn = dynamics_map.get(mid)

        if body.category and dyn and dyn.category != body.category:
            continue
        if body.is_key is not None and dyn and dyn.is_key != body.is_key:
            continue

        enriched.append(
            {
                "id": mid,
                "content": r.get("memory", ""),
                "score": r.get("score"),
                "metadata": r.get("metadata", {}),
                "dynamics": {
                    "is_key": dyn.is_key if dyn else False,
                    "category": dyn.category if dyn else None,
                    "stability": dyn.stability if dyn else None,
                }
                if dyn
                else None,
            }
        )

    return {"results": enriched}


# --- Tags ------------------------------------------------------------------


class TagUpdate(BaseModel):
    tags: list[str] = Field(..., max_length=50)


@router.put("/{memory_id}/tags")
async def update_tags(
    memory_id: str,
    body: TagUpdate,
    user: CanonicalUser = Depends(get_approved_user),
    db: DBSession = Depends(get_db),
):
    """Update tags for a memory."""
    dyn = db.query(MemoryDynamics).filter(MemoryDynamics.memory_id == memory_id).first()
    if not dyn:
        raise HTTPException(status_code=404, detail="Memory dynamics not found")

    dyn.tags = json.dumps(body.tags)
    db.commit()
    return {"ok": True, "tags": body.tags}


@router.get("/tags/all")
async def list_all_tags(
    user: CanonicalUser = Depends(get_approved_user),
    db: DBSession = Depends(get_db),
):
    """List all unique tags used by this user's memories."""
    user_ids = _get_user_ids(user, db)
    if not user_ids:
        return {"tags": []}

    dynamics = db.query(MemoryDynamics).filter(MemoryDynamics.user_id.in_(user_ids)).all()
    tags: set[str] = set()
    for d in dynamics:
        if d.tags:
            try:
                for tag in json.loads(d.tags):
                    tags.add(tag)
            except (json.JSONDecodeError, TypeError):
                pass

    return {"tags": sorted(tags)}


# --- Export / Import -------------------------------------------------------


@router.get("/export")
async def export_memories(
    user: CanonicalUser = Depends(get_approved_user),
    db: DBSession = Depends(get_db),
):
    """Export all memories as a downloadable JSON file."""
    user_ids = _get_user_ids(user, db)
    if not user_ids:
        return StreamingResponse(
            iter([json.dumps({"memories": []}, indent=2)]),
            media_type="application/json",
            headers={"Content-Disposition": "attachment; filename=clara-memories-export.json"},
        )

    memory = _get_memory_client()
    all_memories = []

    for uid in user_ids:
        try:
            result = memory.get_all(user_id=uid)
            if result and "results" in result:
                all_memories.extend(result["results"])
        except Exception as e:
            logger.warning(f"Export failed for {uid}: {e}")

    # Enrich with dynamics
    memory_ids = [m.get("id") for m in all_memories if m.get("id")]
    dynamics_map = {}
    if memory_ids:
        dynamics = db.query(MemoryDynamics).filter(MemoryDynamics.memory_id.in_(memory_ids)).all()
        dynamics_map = {d.memory_id: d for d in dynamics}

    export_data = []
    for m in all_memories:
        mid = m.get("id")
        dyn = dynamics_map.get(mid)
        tags = []
        if dyn and dyn.tags:
            try:
                tags = json.loads(dyn.tags)
            except (json.JSONDecodeError, TypeError):
                pass

        export_data.append(
            {
                "id": mid,
                "content": m.get("memory", ""),
                "metadata": m.get("metadata", {}),
                "category": dyn.category if dyn else None,
                "is_key": dyn.is_key if dyn else False,
                "tags": tags,
                "created_at": m.get("created_at"),
            }
        )

    payload = json.dumps({"memories": export_data, "exported_at": str(_utcnow_iso())}, indent=2)
    return StreamingResponse(
        iter([payload]),
        media_type="application/json",
        headers={"Content-Disposition": "attachment; filename=clara-memories-export.json"},
    )


class MemoryImportItem(BaseModel):
    content: str
    category: str | None = None
    is_key: bool = False
    tags: list[str] = []
    metadata: dict[str, Any] = {}


class MemoryImportRequest(BaseModel):
    memories: list[MemoryImportItem]


@router.post("/import")
async def import_memories(
    body: MemoryImportRequest,
    user: CanonicalUser = Depends(get_approved_user),
    db: DBSession = Depends(get_db),
):
    """Import memories from a JSON payload."""
    user_ids = _get_user_ids(user, db)
    if not user_ids:
        raise HTTPException(status_code=400, detail="No platform linked")

    memory = _get_memory_client()
    primary_uid = user_ids[0]
    imported = 0

    for item in body.memories:
        try:
            messages = [{"role": "user", "content": item.content}]
            result = memory.add(messages, user_id=primary_uid, metadata=item.metadata)

            if result and "results" in result:
                for r in result["results"]:
                    mid = r.get("id")
                    if mid:
                        dyn = db.query(MemoryDynamics).filter(MemoryDynamics.memory_id == mid).first()
                        if dyn:
                            if item.category:
                                dyn.category = item.category
                            if item.is_key:
                                dyn.is_key = item.is_key
                            if item.tags:
                                dyn.tags = json.dumps(item.tags)
                            db.commit()
            imported += 1
        except Exception as e:
            logger.warning(f"Failed to import memory: {e}")

    return {"ok": True, "imported": imported, "total": len(body.memories)}


def _utcnow_iso() -> str:
    """Get current UTC datetime string."""
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat()
