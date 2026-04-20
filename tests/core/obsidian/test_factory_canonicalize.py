"""Tests that get_client_for_user accepts EITHER a canonical UUID or a
platform-prefixed user_id (e.g. "discord-123").

The gateway passes ``request.user.id`` as the prefixed user_id; the identity
service stores and keys the Obsidian config on the canonical UUID. The factory
must resolve prefixed → canonical before hitting the identity service.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from mypalclara.core.obsidian.factory import clear_client_cache, get_client_for_user

pytestmark = pytest.mark.asyncio


CANONICAL_UUID = "e84bbbd8-477b-4483-8ff7-5437699b5eac"
PREFIXED_ID = "discord-271274659385835521"


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.setenv("IDENTITY_SERVICE_URL", "https://id.example")
    monkeypatch.setenv("IDENTITY_SERVICE_SECRET", "s")
    clear_client_cache()
    yield
    clear_client_cache()


async def test_canonical_uuid_hits_identity_service_directly(httpx_mock):
    """A UUID-shaped user_id is used as-is."""
    httpx_mock.add_response(
        url=f"https://id.example/users/{CANONICAL_UUID}/obsidian-token",
        json={"api_token": "t", "api_host": "h", "verify_tls": True},
    )
    client = await get_client_for_user(CANONICAL_UUID)
    assert client is not None


async def test_prefixed_id_is_resolved_to_canonical_via_platform_link(httpx_mock):
    """A platform-prefixed user_id (e.g. 'discord-123') must be resolved
    to the canonical UUID via PlatformLink BEFORE the identity-service lookup."""
    # PlatformLink resolves the prefix to the canonical UUID
    fake_link = type("Link", (), {"canonical_user_id": CANONICAL_UUID})()

    class _FakeSession:
        def query(self, _model):
            return self
        def filter_by(self, **_kw):
            return self
        def first(self):
            return fake_link
        def close(self):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *a):
            pass

    # Identity service ONLY sees the canonical UUID.
    httpx_mock.add_response(
        url=f"https://id.example/users/{CANONICAL_UUID}/obsidian-token",
        json={"api_token": "t", "api_host": "obsidian.shmp.app", "verify_tls": True},
    )

    with patch(
        "mypalclara.db.SessionLocal",
        return_value=_FakeSession(),
    ):
        client = await get_client_for_user(PREFIXED_ID)

    assert client is not None
    assert client.api_token == "t"

    # Verify the identity-service request used the canonical UUID, not the prefix.
    request = httpx_mock.get_request()
    assert CANONICAL_UUID in str(request.url)
    assert PREFIXED_ID not in str(request.url)


async def test_prefixed_id_without_platform_link_returns_none(httpx_mock):
    """If there's no PlatformLink for the prefix, the factory gives up
    gracefully (returns None) WITHOUT hitting the identity service with a
    prefix that will always 404."""
    class _EmptySession:
        def query(self, _model):
            return self
        def filter_by(self, **_kw):
            return self
        def first(self):
            return None
        def close(self):
            pass

    with patch("mypalclara.db.SessionLocal", return_value=_EmptySession()):
        client = await get_client_for_user(PREFIXED_ID)
    assert client is None
    # Factory MUST NOT call identity service with the prefixed id.
    assert len(httpx_mock.get_requests()) == 0


async def test_prefixed_id_resolution_db_failure_falls_back_safely(httpx_mock):
    """DB unreachable for the PlatformLink lookup must not raise — factory
    returns None and prompt build continues without Obsidian."""

    def _broken_session(*a, **kw):
        raise RuntimeError("postgres unreachable")

    with patch("mypalclara.db.SessionLocal", side_effect=_broken_session):
        client = await get_client_for_user(PREFIXED_ID)

    assert client is None
    assert len(httpx_mock.get_requests()) == 0


async def test_canonical_uuid_does_not_consult_platform_link(httpx_mock):
    """Performance: for UUIDs, skip the DB query entirely."""
    httpx_mock.add_response(
        url=f"https://id.example/users/{CANONICAL_UUID}/obsidian-token",
        json={"api_token": "t", "api_host": "h", "verify_tls": True},
    )

    def _boom(*a, **kw):
        raise AssertionError("SessionLocal should NOT be called for UUIDs")

    with patch("mypalclara.db.SessionLocal", side_effect=_boom):
        client = await get_client_for_user(CANONICAL_UUID)
    assert client is not None
