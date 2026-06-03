"""Tests for the require_gateway_secret internal-auth dependency."""

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from mypalclara.gateway.api.auth import require_gateway_secret


@pytest.fixture
def app_client(monkeypatch):
    monkeypatch.setenv("CLARA_GATEWAY_SECRET", "s3cr3t")
    app = FastAPI()

    @app.get("/internal/ping")
    async def ping(_: bool = Depends(require_gateway_secret)):
        return {"ok": True}

    return TestClient(app)


def test_rejects_missing_secret(app_client):
    assert app_client.get("/internal/ping").status_code == 401


def test_rejects_wrong_secret(app_client):
    r = app_client.get("/internal/ping", headers={"X-Gateway-Secret": "nope"})
    assert r.status_code == 401


def test_accepts_correct_secret(app_client):
    r = app_client.get("/internal/ping", headers={"X-Gateway-Secret": "s3cr3t"})
    assert r.status_code == 200 and r.json() == {"ok": True}
