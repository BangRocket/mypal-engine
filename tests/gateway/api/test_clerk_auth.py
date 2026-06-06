"""Clerk JWT verification + user resolution for the web app."""

import datetime as dt

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from mypalclara.gateway.api import clerk_auth


@pytest.fixture
def rsa_keys():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    pub = key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return priv, pub


def _make_token(priv, *, sub="clerk_user_1", iss="https://clerk.test", exp_delta=3600, **extra):
    now = dt.datetime.now(dt.timezone.utc)
    payload = {"sub": sub, "iss": iss, "iat": now, "exp": now + dt.timedelta(seconds=exp_delta), **extra}
    return jwt.encode(payload, priv, algorithm="RS256")


def test_verify_valid_token_returns_claims(monkeypatch, rsa_keys):
    priv, pub = rsa_keys
    monkeypatch.setattr(clerk_auth, "_get_signing_key", lambda token: pub)
    monkeypatch.setenv("CLERK_ISSUER", "https://clerk.test")
    token = _make_token(priv, sub="clerk_abc", email="a@b.co")
    claims = clerk_auth.verify_clerk_jwt(token)
    assert claims["sub"] == "clerk_abc"
    assert claims["email"] == "a@b.co"


def test_verify_expired_token_raises(monkeypatch, rsa_keys):
    priv, pub = rsa_keys
    monkeypatch.setattr(clerk_auth, "_get_signing_key", lambda token: pub)
    monkeypatch.setenv("CLERK_ISSUER", "https://clerk.test")
    token = _make_token(priv, exp_delta=-10)
    with pytest.raises(clerk_auth.ClerkAuthError):
        clerk_auth.verify_clerk_jwt(token)


def test_verify_wrong_issuer_raises(monkeypatch, rsa_keys):
    priv, pub = rsa_keys
    monkeypatch.setattr(clerk_auth, "_get_signing_key", lambda token: pub)
    monkeypatch.setenv("CLERK_ISSUER", "https://clerk.test")
    token = _make_token(priv, iss="https://evil.test")
    with pytest.raises(clerk_auth.ClerkAuthError):
        clerk_auth.verify_clerk_jwt(token)


from mypalclara.db.connection import SessionLocal
from mypalclara.db.models import Base, CanonicalUser, PlatformLink


@pytest.fixture
def db(monkeypatch, tmp_path):
    # Isolated SQLite db for this test module
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(f"sqlite:///{tmp_path}/t.db")
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)
    monkeypatch.setattr("mypalclara.gateway.api.clerk_auth.SessionLocal", TestSession, raising=False)
    s = TestSession()
    yield s
    s.close()


def test_get_or_create_creates_then_reuses(db):
    u1 = clerk_auth.get_or_create_clerk_user(db, sub="clerk_x", email="x@y.co", name="X", avatar=None)
    assert u1.id
    link = db.query(PlatformLink).filter_by(prefixed_user_id="clerk-clerk_x").one()
    assert link.platform == "clerk" and link.canonical_user_id == u1.id
    u2 = clerk_auth.get_or_create_clerk_user(db, sub="clerk_x", email="x@y.co", name="X", avatar=None)
    assert u2.id == u1.id
    assert db.query(CanonicalUser).count() == 1


from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient


def test_get_clerk_user_dependency(monkeypatch, rsa_keys, db):
    priv, pub = rsa_keys
    monkeypatch.setattr(clerk_auth, "_get_signing_key", lambda token: pub)
    monkeypatch.setenv("CLERK_ISSUER", "https://clerk.test")
    monkeypatch.setattr(clerk_auth, "get_db", lambda: iter([db]))

    app = FastAPI()

    @app.get("/me")
    async def me(user=Depends(clerk_auth.get_clerk_user)):
        return {"id": user.id, "name": user.display_name}

    client = TestClient(app)
    token = _make_token(priv, sub="clerk_dep", name="Dep", email="d@e.co")

    assert client.get("/me").status_code == 401  # no header
    r = client.get("/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200 and r.json()["name"] == "Dep"
    assert client.get("/me", headers={"Authorization": "Bearer garbage"}).status_code == 401
