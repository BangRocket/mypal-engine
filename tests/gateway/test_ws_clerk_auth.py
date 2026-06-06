"""Gateway WebSocket: browser clients register with a Clerk JWT, not the secret."""

import datetime as dt
import json
from unittest.mock import AsyncMock, MagicMock

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from mypal_protocol import RegisterMessage
from mypalclara.gateway.api import clerk_auth
from mypalclara.gateway.server import GatewayServer


def _ws():
    ws = MagicMock()
    ws.send = AsyncMock()
    ws.close = AsyncMock()
    return ws


@pytest.fixture
def keys_and_db(monkeypatch, tmp_path):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    pub = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from mypalclara.db.models import Base

    engine = create_engine(f"sqlite:///{tmp_path}/ws.db")
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)
    monkeypatch.setattr(clerk_auth, "_get_signing_key", lambda token: pub)
    monkeypatch.setattr(clerk_auth, "SessionLocal", TestSession, raising=False)
    monkeypatch.setenv("CLERK_ISSUER", "https://clerk.test")
    return priv


def _token(priv, sub="clerk_ws"):
    now = dt.datetime.now(dt.timezone.utc)
    return jwt.encode(
        {"sub": sub, "iss": "https://clerk.test", "iat": now, "exp": now + dt.timedelta(hours=1)},
        priv,
        algorithm="RS256",
    )


@pytest.mark.asyncio
async def test_web_register_with_valid_jwt_succeeds(keys_and_db):
    priv = keys_and_db
    server = GatewayServer(host="127.0.0.1", port=0, secret="right")
    ws = _ws()
    msg = RegisterMessage(node_id="web-ignored", platform="web", auth_token=_token(priv, sub="abc"))

    result = await server._handle_register(ws, msg)

    assert result == "clerk-abc"  # node id derived from the Clerk user
    node = await server.node_registry.get_node("clerk-abc")
    assert node is not None
    sent = [json.loads(c.args[0]) for c in ws.send.await_args_list]
    assert any(f.get("type") == "registered" for f in sent)


@pytest.mark.asyncio
async def test_web_register_with_bad_jwt_rejected(keys_and_db):
    server = GatewayServer(host="127.0.0.1", port=0, secret="right")
    ws = _ws()
    msg = RegisterMessage(node_id="web", platform="web", auth_token="garbage")

    result = await server._handle_register(ws, msg)

    assert result is None
    ws.close.assert_awaited()
    sent = [json.loads(c.args[0]) for c in ws.send.await_args_list]
    assert any(f.get("code") == "auth_failed" for f in sent)


@pytest.mark.asyncio
async def test_adapter_secret_path_unchanged(keys_and_db):
    server = GatewayServer(host="127.0.0.1", port=0, secret="right")
    ws = _ws()
    msg = RegisterMessage(node_id="discord-1", platform="discord", secret="right")
    result = await server._handle_register(ws, msg)
    assert result == "discord-1"
