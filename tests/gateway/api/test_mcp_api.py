"""Tests for the MCP management endpoints (core, non-OAuth)."""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import mypalclara.gateway.api.mcp as mcp_api
from mypalclara.gateway.api.app import create_app

H = {"X-Gateway-Secret": "s3cr3t"}


class _FakeManager:
    def get_all_server_status(self):
        return [{"name": "s1", "status": "running"}]

    def get_server_status(self, name):
        return {"name": name, "status": "running"} if name == "s1" else None

    def get_all_tools(self):
        return [("s1", SimpleNamespace(name="t1", description="does things"))]

    async def start_server(self, name):
        return True

    async def stop_server(self, name):
        return True

    async def restart_server(self, name):
        return True

    async def enable_server(self, name):
        return True

    async def disable_server(self, name):
        return True

    async def reload(self):
        return {"s1": True}


class _FakeInstaller:
    async def install(self, source, name=None, installed_by=None):
        return SimpleNamespace(
            success=True,
            server_type="local",
            error=None,
            tools_discovered=3,
            local_config=None,
            remote_config=None,
        )

    async def uninstall(self, name):
        return True


class _FakeSmithery:
    async def search(self, query, page=1, page_size=10):
        return {"servers": [{"qualifiedName": "x/y", "displayName": "Y"}], "total": 1}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("CLARA_GATEWAY_SECRET", "s3cr3t")
    monkeypatch.setattr(mcp_api, "get_mcp_manager", lambda: _FakeManager())
    monkeypatch.setattr(mcp_api, "MCPInstaller", _FakeInstaller)
    monkeypatch.setattr(mcp_api, "SmitheryClient", _FakeSmithery)
    return TestClient(create_app())


def test_requires_secret(client):
    assert client.get("/api/v1/mcp/servers").status_code == 401


def test_list_servers(client):
    r = client.get("/api/v1/mcp/servers", headers=H)
    assert r.status_code == 200 and r.json()[0]["name"] == "s1"


def test_server_status_found_and_404(client):
    assert client.get("/api/v1/mcp/servers/s1/status", headers=H).status_code == 200
    assert client.get("/api/v1/mcp/servers/nope/status", headers=H).status_code == 404


def test_list_tools(client):
    r = client.get("/api/v1/mcp/tools", headers=H)
    assert r.json() == [{"server": "s1", "name": "t1", "description": "does things"}]


def test_lifecycle_actions(client):
    for action in ("start", "stop", "restart", "enable", "disable"):
        r = client.post(f"/api/v1/mcp/servers/s1/{action}", headers=H)
        assert r.status_code == 200 and r.json() == {"ok": True}


def test_lifecycle_rejects_bad_action(client):
    assert client.post("/api/v1/mcp/servers/s1/frobnicate", headers=H).status_code == 400


def test_reload(client):
    assert client.post("/api/v1/mcp/reload", headers=H).json() == {"s1": True}


def test_search(client):
    r = client.get("/api/v1/mcp/search?query=foo", headers=H)
    assert r.status_code == 200 and r.json()["result"]["total"] == 1


def test_install(client):
    r = client.post("/api/v1/mcp/install", json={"source": "npm:foo"}, headers=H)
    assert r.status_code == 200 and r.json()["success"] is True and r.json()["tools_discovered"] == 3


def test_uninstall(client):
    assert client.delete("/api/v1/mcp/servers/s1", headers=H).json() == {"ok": True}
