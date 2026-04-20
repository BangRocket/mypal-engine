"""Tests for the ObsidianClient factory (identity-service-backed)."""

from __future__ import annotations

import time

import httpx
import pytest

from mypalclara.core.obsidian import factory as factory_module
from mypalclara.core.obsidian.client import ObsidianClient
from mypalclara.core.obsidian.factory import (
    clear_client_cache,
    get_client_for_user,
)

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.setenv("IDENTITY_SERVICE_URL", "https://id.example")
    monkeypatch.setenv("IDENTITY_SERVICE_SECRET", "shared-secret")
    clear_client_cache()
    yield
    clear_client_cache()


async def test_returns_none_when_unconfigured(httpx_mock):
    httpx_mock.add_response(
        url="https://id.example/users/user-1/obsidian-token",
        status_code=404,
    )
    client = await get_client_for_user("user-1")
    assert client is None


async def test_returns_client_when_configured(httpx_mock):
    httpx_mock.add_response(
        url="https://id.example/users/user-1/obsidian-token",
        json={
            "api_token": "the-token",
            "api_host": "obsidian.shmp.app",
            "verify_tls": True,
        },
    )
    client = await get_client_for_user("user-1")
    assert isinstance(client, ObsidianClient)
    assert client.api_host == "obsidian.shmp.app"
    assert client.api_token == "the-token"
    assert client.verify_tls is True


async def test_sends_service_secret_header(httpx_mock):
    httpx_mock.add_response(
        url="https://id.example/users/user-2/obsidian-token",
        json={"api_token": "t", "api_host": "h", "verify_tls": True},
    )
    await get_client_for_user("user-2")
    request = httpx_mock.get_request()
    assert request.headers["X-Service-Secret"] == "shared-secret"


async def test_caches_client_per_user(httpx_mock):
    """Second call within TTL reuses instance, no extra HTTP."""
    httpx_mock.add_response(
        url="https://id.example/users/user-3/obsidian-token",
        json={"api_token": "t", "api_host": "h", "verify_tls": True},
    )
    c1 = await get_client_for_user("user-3")
    c2 = await get_client_for_user("user-3")
    assert c1 is c2
    # Only one HTTP request was made
    assert len(httpx_mock.get_requests()) == 1


async def test_different_users_get_different_clients(httpx_mock):
    httpx_mock.add_response(
        url="https://id.example/users/alice/obsidian-token",
        json={"api_token": "a-tok", "api_host": "ha", "verify_tls": True},
    )
    httpx_mock.add_response(
        url="https://id.example/users/bob/obsidian-token",
        json={"api_token": "b-tok", "api_host": "hb", "verify_tls": False},
    )
    c_alice = await get_client_for_user("alice")
    c_bob = await get_client_for_user("bob")
    assert c_alice is not c_bob
    assert c_alice.api_token == "a-tok"
    assert c_bob.api_token == "b-tok"
    assert c_bob.verify_tls is False


async def test_unconfigured_is_not_cached(httpx_mock):
    """After 404, a later 200 should be picked up without waiting for TTL."""
    httpx_mock.add_response(
        url="https://id.example/users/user-4/obsidian-token",
        status_code=404,
    )
    assert await get_client_for_user("user-4") is None

    # Simulate user configuring later
    httpx_mock.add_response(
        url="https://id.example/users/user-4/obsidian-token",
        json={"api_token": "t", "api_host": "h", "verify_tls": True},
    )
    client = await get_client_for_user("user-4")
    assert client is not None


async def test_connection_error_returns_none(httpx_mock):
    httpx_mock.add_exception(httpx.ConnectError("dns fail"))
    client = await get_client_for_user("user-5")
    assert client is None


async def test_cache_expires_after_ttl(httpx_mock, monkeypatch):
    """Past the TTL, a new fetch happens."""
    # First fetch populates cache
    httpx_mock.add_response(
        url="https://id.example/users/user-6/obsidian-token",
        json={"api_token": "t1", "api_host": "h1", "verify_tls": True},
    )
    c1 = await get_client_for_user("user-6")

    # Move monotonic clock forward past TTL
    real_mono = time.monotonic
    fake_now = real_mono() + factory_module._CLIENT_TTL_SECONDS + 1
    monkeypatch.setattr(factory_module.time, "monotonic", lambda: fake_now)

    # Second fetch should re-hit identity service
    httpx_mock.add_response(
        url="https://id.example/users/user-6/obsidian-token",
        json={"api_token": "t2", "api_host": "h2", "verify_tls": True},
    )
    c2 = await get_client_for_user("user-6")
    assert c1 is not c2
    assert c2.api_token == "t2"


async def test_default_identity_url_when_env_unset(httpx_mock, monkeypatch):
    monkeypatch.delenv("IDENTITY_SERVICE_URL", raising=False)
    clear_client_cache()
    httpx_mock.add_response(
        url="http://localhost:18791/users/user-7/obsidian-token",
        status_code=404,
    )
    result = await get_client_for_user("user-7")
    assert result is None
