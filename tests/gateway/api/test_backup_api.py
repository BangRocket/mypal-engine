"""Tests for the backup management endpoints."""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import mypalclara.gateway.api.backup as backup_api
from mypalclara.gateway.api.app import create_app


class _FakeService:
    async def backup_now(self, databases=None):
        return SimpleNamespace(
            success=True,
            message="ok",
            databases_backed_up=databases or ["clara"],
            databases_failed=[],
            databases_skipped=[],
            timestamp="2026-06-03T00:00:00",
            errors=[],
        )

    async def get_status(self):
        return {"configured": True, "last_backup": None}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("CLARA_GATEWAY_SECRET", "s3cr3t")
    monkeypatch.setattr(backup_api, "get_backup_service", lambda: _FakeService())
    return TestClient(create_app())


def test_backup_run_requires_secret(client):
    assert client.post("/api/v1/backup/run", json={"databases": None}).status_code == 401


def test_backup_run_ok(client):
    r = client.post(
        "/api/v1/backup/run",
        json={"databases": ["clara"]},
        headers={"X-Gateway-Secret": "s3cr3t"},
    )
    assert r.status_code == 200
    assert r.json()["success"] is True
    assert r.json()["databases_backed_up"] == ["clara"]


def test_backup_status_ok(client):
    r = client.get("/api/v1/backup/status", headers={"X-Gateway-Secret": "s3cr3t"})
    assert r.status_code == 200 and r.json()["configured"] is True
