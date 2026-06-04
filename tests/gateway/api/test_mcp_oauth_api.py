"""Tests for the MCP Smithery OAuth endpoints."""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import mypalclara.gateway.api.mcp_oauth as oauth_api
from mypalclara.gateway.api.app import create_app

H = {"X-Gateway-Secret": "s3cr3t"}


class _OAuthClient:
    def __init__(self, server, server_url=None):
        self.server = server

    async def start_oauth_flow(self, redirect_uri):
        return "https://smithery.ai/auth?x=1"

    async def exchange_code(self, code, redirect_uri=None):
        return code == "goodcode"

    def set_tokens_manually(self, access_token, refresh_token=None):
        self.token = access_token


class _Manager:
    async def start_server(self, server):
        return True

    def get_server_status(self, server):
        return {"tool_count": 4}


def _cfg(source_type="smithery-hosted"):
    return SimpleNamespace(source_type=source_type, server_url="https://srv", status="pending", last_error=None)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("CLARA_GATEWAY_SECRET", "s3cr3t")
    monkeypatch.setattr(oauth_api, "load_server_config", lambda s: _cfg() if s == "hosted" else None)
    monkeypatch.setattr(oauth_api, "save_server_config", lambda c: True)
    monkeypatch.setattr(oauth_api, "SmitheryOAuthClient", _OAuthClient)
    monkeypatch.setattr(oauth_api, "get_mcp_manager", lambda: _Manager())
    monkeypatch.setattr(
        oauth_api,
        "load_oauth_state",
        lambda s: SimpleNamespace(tokens=SimpleNamespace(expires_at="2026-12-31T00:00:00", is_expired=lambda: False)),
    )
    return TestClient(create_app())


def test_start_requires_secret(client):
    assert client.post("/api/v1/mcp/servers/hosted/oauth/start").status_code == 401


def test_start_unknown_server_404(client):
    assert client.post("/api/v1/mcp/servers/nope/oauth/start", headers=H).status_code == 404


def test_start_ok(client):
    r = client.post("/api/v1/mcp/servers/hosted/oauth/start", headers=H)
    assert r.status_code == 200
    assert r.json()["auth_url"].startswith("https://smithery.ai")
    assert r.json()["oob"] is True  # no CLARA_API_URL set


def test_complete_ok(client):
    r = client.post("/api/v1/mcp/servers/hosted/oauth/complete", json={"code": "goodcode"}, headers=H)
    assert r.status_code == 200
    assert r.json() == {"success": True, "connected": True, "tool_count": 4}


def test_complete_bad_code_400(client):
    r = client.post("/api/v1/mcp/servers/hosted/oauth/complete", json={"code": "bad"}, headers=H)
    assert r.status_code == 400


def test_status_authorized(client):
    r = client.get("/api/v1/mcp/servers/hosted/oauth/status", headers=H)
    assert r.status_code == 200
    body = r.json()
    assert body["hosted"] is True and body["authorized"] is True and body["expired"] is False


def test_set_token_ok(client):
    r = client.post("/api/v1/mcp/servers/hosted/oauth/token", json={"access_token": "tok"}, headers=H)
    assert r.status_code == 200 and r.json()["success"] is True and r.json()["connected"] is True
