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

    async def list_backups(self, database=None, limit=10):
        from datetime import datetime

        return [
            SimpleNamespace(
                database=database or "clara",
                filename="clara-2026.sql.gz",
                size_bytes=2048,
                timestamp=datetime(2026, 6, 3, 12, 0, 0),
                s3_key="backups/clara-2026.sql.gz",
            )
        ]


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


def test_backup_list_ok(client):
    r = client.get("/api/v1/backup/list?limit=5", headers={"X-Gateway-Secret": "s3cr3t"})
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1
    assert body[0]["filename"] == "clara-2026.sql.gz"
    assert body[0]["timestamp"] == "2026-06-03T12:00:00"  # datetime serialized to ISO


def test_backup_list_requires_secret(client):
    assert client.get("/api/v1/backup/list").status_code == 401
