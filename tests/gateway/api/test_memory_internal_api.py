"""Tests for the internal memory endpoints (gateway-secret, explicit user_id)."""

import pytest
from fastapi.testclient import TestClient

import mypalclara.gateway.api.memory_internal as mem_api
from mypalclara.gateway.api.app import create_app

H = {"X-Gateway-Secret": "s3cr3t"}


class _FakeMemory:
    def get_all(self, user_id=None):
        return [{"memory": "a"}, {"memory": "b"}] if user_id == "u1" else []

    def search(self, query, user_id=None, limit=10):
        return [{"memory": f"match:{query}"}]

    def delete_all(self, user_id=None):
        self.deleted = user_id


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("CLARA_GATEWAY_SECRET", "s3cr3t")
    monkeypatch.setattr(mem_api, "_memory", lambda: _FakeMemory())
    return TestClient(create_app())


def test_count_requires_secret(client):
    assert client.get("/api/v1/memory/count?user_id=u1").status_code == 401


def test_count(client):
    r = client.get("/api/v1/memory/count?user_id=u1", headers=H)
    assert r.status_code == 200 and r.json() == {"count": 2}


def test_count_empty(client):
    r = client.get("/api/v1/memory/count?user_id=other", headers=H)
    assert r.json() == {"count": 0}


def test_search(client):
    r = client.get("/api/v1/memory/search?user_id=u1&query=hi", headers=H)
    assert r.status_code == 200
    assert r.json()["results"] == [{"memory": "match:hi"}]


def test_delete_all(client):
    r = client.request("DELETE", "/api/v1/memory", params={"user_id": "u1"}, headers=H)
    assert r.status_code == 200 and r.json() == {"deleted": True}
