"""Tests for the guild configuration endpoints."""

import pytest
from fastapi.testclient import TestClient

from mypalclara.gateway.api.app import create_app

H = {"X-Gateway-Secret": "s3cr3t"}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("CLARA_GATEWAY_SECRET", "s3cr3t")
    store: dict[str, dict] = {}

    def _get_or_create(guild_id):
        return store.setdefault(
            guild_id,
            {
                "guild_id": guild_id,
                "default_tier": None,
                "auto_tier_enabled": "false",
                "ors_enabled": "false",
                "ors_channel_id": None,
                "ors_quiet_start": None,
                "ors_quiet_end": None,
                "sandbox_mode": "docker",
            },
        )

    def _update(guild_id, **fields):
        cfg = _get_or_create(guild_id)
        cfg.update({k: v for k, v in fields.items() if v is not None})
        return cfg

    monkeypatch.setattr("mypalclara.db.guild_config.get_or_create_guild_config", _get_or_create)
    monkeypatch.setattr("mypalclara.db.guild_config.update_guild_config", _update)
    return TestClient(create_app())


def test_requires_secret(client):
    assert client.get("/api/v1/guilds/g1/config").status_code == 401


def test_get_creates_default(client):
    r = client.get("/api/v1/guilds/g1/config", headers=H)
    assert r.status_code == 200
    body = r.json()
    assert body["guild_id"] == "g1" and body["sandbox_mode"] == "docker"


def test_put_updates(client):
    r = client.put("/api/v1/guilds/g1/config", json={"sandbox_mode": "e2b"}, headers=H)
    assert r.status_code == 200 and r.json()["sandbox_mode"] == "e2b"
