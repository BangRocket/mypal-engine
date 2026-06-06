"""Gateway WebSocket: browser clients register with a Clerk JWT, not the secret."""

import datetime as dt
import json
from unittest.mock import AsyncMock, MagicMock, patch

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from mypal_protocol import RegisterMessage
from mypal_protocol.messages import ChannelInfo, MessageRequest, UserInfo
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


# ---------------------------------------------------------------------------
# C1: web clients must not be able to spoof another user's identity
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_web_client_cannot_spoof_user_id(keys_and_db):
    """C1: a browser client sending msg.user.id='discord-victim' must have its
    user identity overridden to the Clerk-verified node_id before processing.

    Seam: monkeypatch server._process_request (AsyncMock) so we can inspect
    the MessageRequest that reaches it.  The router is configured to acquire
    immediately (default behaviour for an uncontested channel), so
    _handle_message_request → _process_request is the straight-line path.
    """
    priv = keys_and_db
    server = GatewayServer(host="127.0.0.1", port=0, secret="right")
    ws = _ws()

    # Register a web client (sub="abc" → node_id "clerk-abc")
    reg_msg = RegisterMessage(node_id="web-ignored", platform="web", auth_token=_token(priv, sub="abc"))
    result = await server._handle_register(ws, reg_msg)
    assert result == "clerk-abc"

    # Build a MessageRequest with a spoofed user.id.
    # Use is_mention=True so the router acquires the channel immediately (no
    # debounce) — this keeps the test synchronous and avoids async timer hacks.
    spoofed_msg = MessageRequest(
        id="msg-1",
        user=UserInfo(id="discord-victim", platform_id="victim", name="Evil"),
        channel=ChannelInfo(id="ch-1", type="server"),
        content="hello",
        metadata={"is_mention": True},
    )

    # Capture the msg that reaches _process_request
    captured = {}

    async def fake_process_request(websocket, node_id, msg):
        captured["msg"] = msg

    server._process_request = fake_process_request  # type: ignore[method-assign]

    await server._handle_message_request(ws, spoofed_msg)

    # The processed message must carry the verified identity, not the spoofed one
    processed = captured.get("msg")
    assert processed is not None, "_process_request was never called"
    assert processed.user.id == "clerk-abc", (
        f"Expected user.id 'clerk-abc', got {processed.user.id!r} — spoofed id leaked through"
    )
    assert processed.user.platform_id == "abc", (
        f"Expected platform_id 'abc', got {processed.user.platform_id!r}"
    )


@pytest.mark.asyncio
async def test_adapter_platform_user_id_not_overridden(keys_and_db):
    """C1 (negative): adapter (discord) nodes must NOT have their user.id overridden."""
    server = GatewayServer(host="127.0.0.1", port=0, secret="right")
    ws = _ws()

    # Register an adapter node (discord, not web)
    reg_msg = RegisterMessage(node_id="discord-1", platform="discord", secret="right")
    await server._handle_register(ws, reg_msg)

    # Adapter sends a message on behalf of a real discord user.
    # Use is_mention=True so the router acquires the channel immediately.
    discord_msg = MessageRequest(
        id="msg-2",
        user=UserInfo(id="discord-999", platform_id="999", name="Real User"),
        channel=ChannelInfo(id="ch-2", type="server"),
        content="hi",
        metadata={"is_mention": True},
    )

    captured = {}

    async def fake_process_request(websocket, node_id, msg):
        captured["msg"] = msg

    server._process_request = fake_process_request  # type: ignore[method-assign]

    await server._handle_message_request(ws, discord_msg)

    processed = captured.get("msg")
    assert processed is not None, "_process_request was never called"
    # Must preserve the adapter-supplied user id unchanged
    assert processed.user.id == "discord-999"
    assert processed.user.platform_id == "999"
