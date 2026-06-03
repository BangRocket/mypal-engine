"""Tests for the identity-link management endpoints (CLI linking)."""

import tempfile

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from mypalclara.db.models import Base
from mypalclara.gateway.api.app import create_app
from mypalclara.gateway.api.auth import get_db

H = {"X-Gateway-Secret": "s3cr3t"}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("CLARA_GATEWAY_SECRET", "s3cr3t")
    db_path = tempfile.NamedTemporaryFile(suffix=".db", delete=False).name
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    app = create_app()

    def _override_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override_db
    return TestClient(app)


def _make_link(client, prefixed="cli-alice", canonical_user_id=None):
    return client.post(
        "/api/v1/users/links",
        json={
            "platform": "cli",
            "platform_user_id": "alice",
            "prefixed_user_id": prefixed,
            "display_name": "Alice",
            "canonical_user_id": canonical_user_id,
            "linked_via": "cli-command",
        },
        headers=H,
    )


def test_requires_secret(client):
    assert client.get("/api/v1/users/links/cli-alice").status_code == 401


def test_create_link_makes_canonical_user(client):
    r = _make_link(client)
    assert r.status_code == 201
    body = r.json()
    assert body["prefixed_user_id"] == "cli-alice"
    assert body["canonical_user_id"]  # a CanonicalUser was created


def test_resolve_and_list(client):
    created = _make_link(client).json()
    cuid = created["canonical_user_id"]

    r = client.get("/api/v1/users/links/cli-alice", headers=H)
    assert r.status_code == 200
    assert r.json()["link"]["canonical_user_id"] == cuid
    assert r.json()["canonical_user"]["display_name"] == "Alice"

    r = client.get(f"/api/v1/users/{cuid}/links", headers=H)
    assert r.status_code == 200 and len(r.json()["links"]) == 1


def test_second_identity_links_to_same_user(client):
    cuid = _make_link(client).json()["canonical_user_id"]
    r = client.post(
        "/api/v1/users/links",
        json={
            "platform": "discord",
            "platform_user_id": "123",
            "prefixed_user_id": "discord-123",
            "canonical_user_id": cuid,
        },
        headers=H,
    )
    assert r.status_code == 201 and r.json()["canonical_user_id"] == cuid
    r = client.get(f"/api/v1/users/{cuid}/links", headers=H)
    assert len(r.json()["links"]) == 2


def test_duplicate_prefixed_conflicts(client):
    _make_link(client)
    assert _make_link(client).status_code == 409


def test_resolve_missing_is_404(client):
    assert client.get("/api/v1/users/links/nope", headers=H).status_code == 404


def test_delete_link(client):
    _make_link(client)
    assert client.delete("/api/v1/users/links/cli-alice", headers=H).json() == {"deleted": True}
    assert client.get("/api/v1/users/links/cli-alice", headers=H).status_code == 404
    assert client.delete("/api/v1/users/links/cli-alice", headers=H).json() == {"deleted": False}
