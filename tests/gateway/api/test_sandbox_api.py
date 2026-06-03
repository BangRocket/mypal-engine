"""Tests for the sandbox status endpoint."""

import pytest
from fastapi.testclient import TestClient

import mypalclara.gateway.api.sandbox as sandbox_api
from mypalclara.gateway.api.app import create_app


class _FakeManager:
    def is_available(self):
        return True

    def get_stats(self):
        return {"active_sessions": 2, "backend": "docker"}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("CLARA_GATEWAY_SECRET", "s3cr3t")
    monkeypatch.setattr(sandbox_api, "get_sandbox_manager", lambda: _FakeManager())
    return TestClient(create_app())


def test_sandbox_status_requires_secret(client):
    assert client.get("/api/v1/sandbox/status").status_code == 401


def test_sandbox_status_ok(client):
    r = client.get("/api/v1/sandbox/status", headers={"X-Gateway-Secret": "s3cr3t"})
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is True
    assert body["stats"]["backend"] == "docker"
