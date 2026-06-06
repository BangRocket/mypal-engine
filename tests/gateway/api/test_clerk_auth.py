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
