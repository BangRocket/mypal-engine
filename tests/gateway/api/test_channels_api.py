"""Tests for the channel-mode configuration endpoints."""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from mypalclara.gateway.api.app import create_app

H = {"X-Gateway-Secret": "s3cr3t"}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("CLARA_GATEWAY_SECRET", "s3cr3t")
    monkeypatch.setattr("mypalclara.db.channel_config.get_channel_mode", lambda cid: "mention")
    monkeypatch.setattr(
        "mypalclara.db.channel_config.set_channel_mode",
        lambda cid, gid, mode, cb=None: SimpleNamespace(channel_id=cid, mode=mode),
    )
    monkeypatch.setattr(
        "mypalclara.db.channel_config.get_guild_channels",
        lambda gid: [SimpleNamespace(channel_id="c1", mode="active")],
    )
    return TestClient(create_app())


def test_get_mode_requires_secret(client):
    assert client.get("/api/v1/channels/c1/mode").status_code == 401


def test_get_mode_ok(client):
    r = client.get("/api/v1/channels/c1/mode", headers=H)
    assert r.status_code == 200 and r.json() == {"mode": "mention"}


def test_set_mode_ok(client):
    r = client.put(
        "/api/v1/channels/c1/mode",
        json={"guild_id": "g1", "mode": "active", "configured_by": "u1"},
        headers=H,
    )
    assert r.status_code == 200 and r.json() == {"mode": "active"}


def test_set_mode_rejects_bad_mode(client):
    r = client.put(
        "/api/v1/channels/c1/mode",
        json={"guild_id": "g1", "mode": "bogus"},
        headers=H,
    )
    assert r.status_code == 400


def test_list_guild_channels_ok(client):
    r = client.get("/api/v1/guilds/g1/channels", headers=H)
    assert r.status_code == 200
    assert r.json() == [{"channel_id": "c1", "mode": "active"}]
