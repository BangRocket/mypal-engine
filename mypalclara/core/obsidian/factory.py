"""Factory that fetches per-user Obsidian credentials from the identity service
and returns a configured ObsidianClient (or None if the user hasn't set up
Obsidian integration).

Clients are cached in-process per canonical_user_id for a short TTL so that
a single LLM turn with several Obsidian tool calls doesn't re-fetch credentials
each time. The cache is cleared on process restart and can be cleared
programmatically via `clear_client_cache()` (useful in tests).
"""

from __future__ import annotations

import os
import time

import httpx

from mypalclara.core.obsidian.client import ObsidianClient

_CLIENT_TTL_SECONDS = 60.0
_cache: dict[str, tuple[float, ObsidianClient]] = {}


def clear_client_cache() -> None:
    """Clear all cached clients. Primarily for tests."""
    _cache.clear()


def _identity_base_url() -> str:
    return os.environ.get("IDENTITY_SERVICE_URL", "http://localhost:18791").rstrip("/")


def _identity_secret() -> str:
    return os.environ.get("IDENTITY_SERVICE_SECRET", "")


async def get_client_for_user(canonical_user_id: str) -> ObsidianClient | None:
    """Return an ObsidianClient for the given user, or None if unconfigured.

    Caches the client for ~60s keyed by canonical_user_id.
    """
    now = time.monotonic()
    cached = _cache.get(canonical_user_id)
    if cached is not None:
        cached_at, client = cached
        if now - cached_at < _CLIENT_TTL_SECONDS:
            return client
        # expired — fall through and re-fetch
        _cache.pop(canonical_user_id, None)

    url = f"{_identity_base_url()}/users/{canonical_user_id}/obsidian-token"
    headers = {"X-Service-Secret": _identity_secret()}

    try:
        async with httpx.AsyncClient(timeout=5.0) as http:
            resp = await http.get(url, headers=headers)
    except httpx.HTTPError:
        # Network/timeout to identity service — treat as "not available right now",
        # NOT as "user not configured". Don't cache this decision.
        return None

    if resp.status_code == 404:
        # User has not configured Obsidian. Don't cache (so a later PUT is picked up).
        return None
    if resp.status_code != 200:
        # Any other non-200 (500, 401, etc.) — log-level concern but treat as unavailable.
        return None

    data = resp.json()
    client = ObsidianClient(
        api_host=data["api_host"],
        api_token=data["api_token"],
        verify_tls=bool(data.get("verify_tls", True)),
    )
    _cache[canonical_user_id] = (now, client)
    return client
