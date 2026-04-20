"""Factory that fetches per-user Obsidian credentials from the identity service
and returns a configured ObsidianClient (or None if the user hasn't set up
Obsidian integration).

Clients are cached in-process per canonical_user_id for a short TTL so that
a single LLM turn with several Obsidian tool calls doesn't re-fetch credentials
each time. The cache is cleared on process restart and can be cleared
programmatically via `clear_client_cache()` (useful in tests).
"""

from __future__ import annotations

import logging
import os
import re
import time

import httpx

from mypalclara.core.obsidian.client import ObsidianClient

logger = logging.getLogger("clara.obsidian.factory")

_CLIENT_TTL_SECONDS = 60.0
_cache: dict[str, tuple[float, ObsidianClient]] = {}

# UUID (CanonicalUser.id) — any caller passing this shape skips the
# PlatformLink lookup. Everything else is treated as a prefixed user_id
# (e.g. "discord-271274659385835521") and resolved to the canonical UUID
# before hitting the identity service.
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def _canonicalize_user_id(user_id: str) -> str | None:
    """Return the CanonicalUser.id for a gateway user_id, or None if unknown.

    - UUIDs pass through unchanged.
    - Prefixed IDs (e.g. "discord-123") are resolved via the local PlatformLink
      mirror table. Missing link or DB failure returns None so the caller
      treats the user as unconfigured rather than 404-ing the identity service.
    """
    if _UUID_RE.match(user_id):
        return user_id

    try:
        from mypalclara.db import SessionLocal
        from mypalclara.db.models import PlatformLink
    except Exception as e:
        logger.debug("PlatformLink import failed: %s", e)
        return None

    try:
        db = SessionLocal()
    except Exception as e:
        logger.debug("SessionLocal() failed: %s", e)
        return None

    try:
        link = db.query(PlatformLink).filter_by(prefixed_user_id=user_id).first()
        if link is None:
            return None
        return link.canonical_user_id
    except Exception as e:
        logger.debug("PlatformLink lookup failed for %s: %s", user_id, e)
        return None
    finally:
        try:
            db.close()
        except Exception:
            pass


def clear_client_cache() -> None:
    """Clear all cached clients. Primarily for tests."""
    _cache.clear()


def _identity_base_url() -> str:
    return os.environ.get("IDENTITY_SERVICE_URL", "http://localhost:18791").rstrip("/")


def _identity_secret() -> str:
    return os.environ.get("IDENTITY_SERVICE_SECRET", "")


async def get_client_for_user(user_id: str) -> ObsidianClient | None:
    """Return an ObsidianClient for the given user, or None if unconfigured.

    Accepts either a canonical CanonicalUser UUID or a platform-prefixed
    user_id (e.g. "discord-123"). Prefixed IDs are resolved to canonical
    via PlatformLink before the identity-service lookup.

    Caches the client for ~60s keyed by canonical_user_id.
    """
    canonical_user_id = _canonicalize_user_id(user_id)
    if canonical_user_id is None:
        return None

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
