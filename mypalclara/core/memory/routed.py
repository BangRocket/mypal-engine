"""Routed memory layer — toggles between embedded ClaraMemory and remote Palace service.

Phase A of the migration described in `docs/migrating-mypalclara.md`.

Behavior:
- `USE_PALACE_SERVICE=true` → `PALACE` is a `RemotePalace` that proxies the
  embedded `ClaraMemory` surface to a Palace HTTP service via the async
  `mypalace_client.PalaceClient`. Sync callers are bridged to the async
  client through a dedicated background-thread asyncio loop.
- otherwise → `PALACE` is the embedded singleton from
  `mypalclara.core.memory.config` (unchanged behavior, fully reversible).

`MemoryManager` is re-exported from `mypalclara.core.memory_manager` so callers
can swap their import once and not chase it again when Phase C collapses the
manager into a thin client wrapper.

Env vars (only read when USE_PALACE_SERVICE=true):
  PALACE_SERVICE_URL        Required. Base URL of the Palace deployment.
  PALACE_SERVICE_API_KEY    Required. The plaintext_key minted via
                            POST /v1/admin/keys. We deliberately do *not*
                            reuse `PALACE_API_KEY` — that name is already
                            bound to the embedded memory-extraction LLM
                            provider in `memory/config.py`.
  PALACE_SERVICE_TIMEOUT    HTTP timeout in seconds (default: 30).

Lossy translations to be aware of (Phase B will surface real breakages):
- `RemotePalace.search()` results lack `agent_id`, `actor_id`, `role`,
  `visibility`, and arbitrary metadata fields — the remote ScoredMemory
  shape is thinner than the embedded one. `get_all()` does carry full
  metadata (Memory shape).
- `RemotePalace.history()` has no remote endpoint in mypalace-client
  0.7.x — returns `[]` with a warning.
- `RemotePalace.embedding_model`, `.graph`, `.vector_store`, `.db` are all
  `None`. Code paths that touch these (graph search, manual embedding,
  vector-store reset, history DB) become no-ops or skip via existing
  `is not None` guards. Migration scripts that need direct access should
  keep importing from `mypalclara.core.memory` (the embedded path).
"""

from __future__ import annotations

import asyncio
import atexit
import logging
import os
import threading
from collections.abc import Coroutine
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mypalace_client import PalaceClient

logger = logging.getLogger("clara.palace.routed")


USE_PALACE_SERVICE = os.getenv("USE_PALACE_SERVICE", "false").lower() in ("1", "true", "yes")


# ---------------------------------------------------------------------------
# Async-to-sync bridge
# ---------------------------------------------------------------------------


class _AsyncBridge:
    """Singleton background thread running an asyncio loop.

    Sync callers schedule coroutines on this loop via `submit(coro)`, which
    blocks the calling thread until the coroutine completes. Because the loop
    runs on a *different* thread, this works correctly even when the calling
    thread itself is inside an event loop — the coroutine is dispatched out
    and resolved without re-entering the caller's loop.
    """

    _instance: "_AsyncBridge | None" = None
    _instance_lock = threading.Lock()

    @classmethod
    def get(cls) -> "_AsyncBridge":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ready = threading.Event()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="palace-async-bridge",
            daemon=True,
        )
        self._thread.start()
        self._ready.wait()
        atexit.register(self._shutdown)

    def _run_loop(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        self._ready.set()
        try:
            loop.run_forever()
        finally:
            try:
                pending = asyncio.all_tasks(loop)
                for task in pending:
                    task.cancel()
                if pending:
                    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            finally:
                loop.close()

    def submit(self, coro: Coroutine[Any, Any, Any]) -> Any:
        if self._loop is None or self._loop.is_closed():
            raise RuntimeError("Palace async bridge is not running")
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result()

    def _shutdown(self) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        loop.call_soon_threadsafe(loop.stop)
        self._thread.join(timeout=5)


# ---------------------------------------------------------------------------
# Remote-Palace facade
# ---------------------------------------------------------------------------


class RemotePalace:
    """Sync facade that mimics the embedded `ClaraMemory` surface.

    Methods translate to async `PalaceClient` calls dispatched on the bridge
    loop. The shapes of return values match the embedded path closely enough
    that existing callers don't need to change — see module docstring for the
    fields that are intentionally lossy.
    """

    # Embedded-only attributes that have no remote analog. Set to None so
    # existing `if PALACE.graph is not None:` guards skip cleanly. Code paths
    # that unconditionally call methods on these will fail loudly (the right
    # behavior — those callers need to migrate to remote-aware logic).
    embedding_model: Any = None
    graph: Any = None
    vector_store: Any = None
    db: Any = None
    enable_graph: bool = False

    def __init__(
        self,
        base_url: str,
        api_key: str | None,
        timeout: float = 30.0,
    ) -> None:
        self._base_url = base_url
        self._api_key = api_key
        self._timeout = timeout
        self._client: PalaceClient | None = None
        self._client_lock = threading.Lock()
        self._bridge = _AsyncBridge.get()
        atexit.register(self._close_client_if_open)

    # ---- client lifecycle ----

    def _get_client(self) -> "PalaceClient":
        from mypalace_client import PalaceClient

        if self._client is None:
            with self._client_lock:
                if self._client is None:

                    async def _make() -> PalaceClient:
                        return PalaceClient(
                            base_url=self._base_url,
                            api_key=self._api_key,
                            timeout=self._timeout,
                        )

                    self._client = self._bridge.submit(_make())
        return self._client

    def _close_client_if_open(self) -> None:
        if self._client is None:
            return
        client = self._client
        self._client = None
        try:
            self._bridge.submit(client.aclose())
        except Exception as e:
            logger.debug("PalaceClient close failed during shutdown: %s", e)

    # ---- helpers ----

    @staticmethod
    def _normalize_messages(messages: Any) -> list[dict]:
        """Match ClaraMemory.add's input normalization."""
        if isinstance(messages, str):
            return [{"role": "user", "content": messages}]
        if isinstance(messages, dict):
            return [messages]
        if isinstance(messages, list):
            out: list[dict] = []
            for m in messages:
                if isinstance(m, dict):
                    out.append(m)
                elif hasattr(m, "to_dict"):
                    out.append(m.to_dict())
                else:
                    raise ValueError("messages list items must be dict or have to_dict(); " f"got {type(m).__name__}")
            return out
        raise ValueError("messages must be str, dict, list[dict], or list[Message]")

    @staticmethod
    def _memory_to_dict(memory: Any) -> dict:
        """Translate a remote Memory into the embedded item shape."""
        item: dict[str, Any] = {
            "id": memory.id,
            "memory": memory.content,
            "user_id": memory.user_id,
        }
        if memory.agent_id is not None:
            item["agent_id"] = memory.agent_id
        if memory.created_at is not None:
            item["created_at"] = memory.created_at.isoformat()
        if memory.updated_at is not None:
            item["updated_at"] = memory.updated_at.isoformat()
        if memory.metadata:
            promoted = ("actor_id", "role", "visibility", "run_id", "hash")
            for key in promoted:
                if key in memory.metadata:
                    item[key] = memory.metadata[key]
            extra = {k: v for k, v in memory.metadata.items() if k not in promoted}
            if extra:
                item["metadata"] = extra
        return item

    @staticmethod
    def _scored_to_dict(scored: Any) -> dict:
        """Translate a remote ScoredMemory into the embedded item shape.

        Note: ScoredMemory is intentionally thinner than Memory — agent_id,
        user_id, metadata, and promoted keys are not in the wire shape.
        Downstream callers that need those fields should switch to get_all
        or per-id get() for the remote path.
        """
        item: dict[str, Any] = {
            "id": scored.id,
            "memory": scored.content,
            "score": scored.score,
        }
        if scored.created_at is not None:
            item["created_at"] = scored.created_at.isoformat()
        return item

    # ---- ClaraMemory surface ----

    def add(
        self,
        messages: Any,
        *,
        user_id: str | None = None,
        agent_id: str | None = None,
        run_id: str | None = None,
        metadata: dict | None = None,
        infer: bool = True,
        memory_type: str | None = None,
        prompt: str | None = None,  # noqa: ARG002 — server-side extraction prompt is not configurable from client
        timestamp: int | None = None,  # noqa: ARG002 — wire shape carries server-assigned timestamps
    ) -> dict:
        if user_id is None:
            raise ValueError("user_id is required for RemotePalace.add()")
        msgs = self._normalize_messages(messages)
        merged_metadata = dict(metadata or {})
        if run_id is not None:
            merged_metadata["run_id"] = run_id

        client = self._get_client()
        memories = self._bridge.submit(
            client.add(
                messages=msgs,
                user_id=user_id,
                agent_id=agent_id,
                memory_type=memory_type or "episodic",
                metadata=merged_metadata or None,
                infer=infer,
            )
        )
        results = [
            {
                "id": m.id,
                "memory": m.content,
                "event": "ADD",
            }
            for m in memories
        ]
        return {"results": results}

    def search(
        self,
        query: str,
        *,
        user_id: str | None = None,
        agent_id: str | None = None,
        run_id: str | None = None,  # noqa: ARG002 — remote search doesn't filter by run_id
        limit: int = 100,
        filters: dict | None = None,  # noqa: ARG002 — remote search has no equivalent free-form filter
        threshold: float | None = None,
    ) -> dict:
        if not query or not query.strip():
            return {"results": []}
        client = self._get_client()
        scored = self._bridge.submit(
            client.search(
                query=query,
                user_id=user_id,
                agent_id=agent_id,
                limit=limit,
                min_score=threshold or 0.0,
            )
        )
        return {"results": [self._scored_to_dict(s) for s in scored]}

    def get_all(
        self,
        *,
        user_id: str | None = None,
        agent_id: str | None = None,
        run_id: str | None = None,
        filters: dict | None = None,
        limit: int = 100,
    ) -> dict:
        client = self._get_client()
        memories = self._bridge.submit(
            client.get_all(
                user_id=user_id,
                agent_id=agent_id,
                run_id=run_id,
                metadata=filters,
                limit=limit,
            )
        )
        return {"results": [self._memory_to_dict(m) for m in memories]}

    def delete(self, memory_id: str) -> dict:
        client = self._get_client()
        self._bridge.submit(client.delete(memory_id))
        return {"message": "Memory deleted successfully!"}

    def delete_all(
        self,
        user_id: str | None = None,
        agent_id: str | None = None,
        run_id: str | None = None,
    ) -> dict:
        if not user_id:
            raise ValueError(
                "RemotePalace.delete_all requires user_id (remote API: " "DELETE /v1/users/{user_id}/memories)"
            )
        client = self._get_client()
        self._bridge.submit(client.delete_all(user_id=user_id, agent_id=agent_id, run_id=run_id))
        return {"message": "Memories deleted successfully!"}

    def history(self, memory_id: str) -> list:  # noqa: ARG002 — no remote endpoint in 0.7.x
        logger.warning(
            "RemotePalace.history(%s) called but mypalace-client 0.7.x has "
            "no /v1/memories/{id}/history endpoint — returning [].",
            memory_id,
        )
        return []

    def update_memory_visibility(self, memory_id: str, visibility: str) -> None:
        if visibility not in ("public", "private"):
            raise ValueError(f"Invalid visibility: {visibility}. Must be 'public' or 'private'.")
        client = self._get_client()
        self._bridge.submit(client.update(memory_id, metadata={"visibility": visibility}))

    # ---- direct PalaceClient access for code that needs richer surface ----

    @property
    def client(self) -> "PalaceClient":
        """Direct access to the underlying async PalaceClient.

        For callers that need endpoints beyond the embedded ClaraMemory
        surface (layered context, intentions, episodes, dynamics, etc.).
        Calls must be awaited or dispatched on the bridge.
        """
        return self._get_client()

    @property
    def bridge(self) -> _AsyncBridge:
        """Bridge for sync callers wanting to dispatch raw client coroutines."""
        return self._bridge


# ---------------------------------------------------------------------------
# PALACE selector
# ---------------------------------------------------------------------------


def _build_remote_palace() -> RemotePalace:
    base_url = os.getenv("PALACE_SERVICE_URL")
    if not base_url:
        raise RuntimeError(
            "USE_PALACE_SERVICE=true but PALACE_SERVICE_URL is not set. "
            "Either point it at a Palace deployment (e.g. http://palace:8000) "
            "or unset USE_PALACE_SERVICE to use the embedded path."
        )
    api_key = os.getenv("PALACE_SERVICE_API_KEY")
    if not api_key:
        logger.warning(
            "PALACE_SERVICE_API_KEY is not set — Palace deployments with "
            "auth enabled will reject every call with 401. Continuing anyway "
            "in case the server has PALACE_AUTH_DISABLED=true."
        )
    timeout = float(os.getenv("PALACE_SERVICE_TIMEOUT", "30"))
    logger.info("Routed memory: REMOTE Palace at %s (timeout=%.1fs)", base_url, timeout)
    return RemotePalace(base_url=base_url, api_key=api_key, timeout=timeout)


if USE_PALACE_SERVICE:
    PALACE: Any = _build_remote_palace()
else:
    # Embedded path — defer the heavy import until we know we need it.
    from mypalclara.core.memory.config import PALACE as _EMBEDDED_PALACE

    PALACE = _EMBEDDED_PALACE
    logger.info("Routed memory: EMBEDDED ClaraMemory (USE_PALACE_SERVICE=false)")


# Re-export MemoryManager so callsites can import both PALACE and the manager
# from this module — matches the pattern in docs/migrating-mypalclara.md.
from mypalclara.core.memory_manager import MemoryManager  # noqa: E402

__all__ = ["PALACE", "MemoryManager", "RemotePalace", "USE_PALACE_SERVICE"]
