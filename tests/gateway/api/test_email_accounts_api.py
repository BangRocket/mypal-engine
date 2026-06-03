"""Tests for the email-accounts listing endpoint."""

import tempfile

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from mypalclara.db.models import Base, EmailAccount
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

    seed = Session()
    seed.add(EmailAccount(user_id="u1", email_address="a@x.com", provider_type="imap", imap_password="SECRET"))
    seed.add(EmailAccount(user_id="u2", email_address="b@x.com", provider_type="gmail"))
    seed.commit()
    seed.close()

    app = create_app()

    def _override_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override_db
    return TestClient(app)


def test_requires_secret(client):
    assert client.get("/api/v1/email-accounts?user_id=u1").status_code == 401


def test_scopes_to_user_and_hides_password(client):
    r = client.get("/api/v1/email-accounts?user_id=u1", headers=H)
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1
    assert body[0]["email_address"] == "a@x.com"
    assert "imap_password" not in body[0]


def test_other_user_isolated(client):
    r = client.get("/api/v1/email-accounts?user_id=u2", headers=H)
    assert r.status_code == 200
    assert [a["email_address"] for a in r.json()] == ["b@x.com"]
