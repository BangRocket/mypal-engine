"""Gateway WebSocket registration must require a matching CLARA_GATEWAY_SECRET."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from mypal_protocol import RegisterMessage
from mypalclara.gateway.server import GatewayServer


def _ws():
    ws = MagicMock()
    ws.send = AsyncMock()
    ws.close = AsyncMock()
    return ws


@pytest.mark.asyncio
async def test_start_without_secret_raises(monkeypatch):
    monkeypatch.delenv("CLARA_GATEWAY_SECRET", raising=False)
    server = GatewayServer(host="127.0.0.1", port=0, secret=None)
    with pytest.raises(RuntimeError, match="CLARA_GATEWAY_SECRET"):
        await server.start()


@pytest.mark.asyncio
async def test_register_rejected_without_matching_secret():
    server = GatewayServer(host="127.0.0.1", port=0, secret="right")
    ws = _ws()
    msg = RegisterMessage(node_id="discord-1", platform="discord", secret="wrong")

    result = await server._handle_register(ws, msg)

    assert result is None
    ws.close.assert_awaited()  # connection closed on auth failure
    node = await server.node_registry.get_node("discord-1")
    assert node is None  # not registered
    # an error frame was sent
    sent = [json.loads(c.args[0]) for c in ws.send.await_args_list]
    assert any(f.get("code") == "auth_failed" for f in sent)


@pytest.mark.asyncio
async def test_register_succeeds_and_issues_token():
    server = GatewayServer(host="127.0.0.1", port=0, secret="right")
    ws = _ws()
    msg = RegisterMessage(node_id="discord-1", platform="discord", secret="right")

    result = await server._handle_register(ws, msg)

    assert result == "discord-1"
    node = await server.node_registry.get_node("discord-1")
    assert node is not None
    assert node.adapter_token and node.adapter_token.startswith("adp-")
    sent = [json.loads(c.args[0]) for c in ws.send.await_args_list]
    registered = [f for f in sent if f.get("type") == "registered"]
    assert registered and registered[0]["adapter_token"] == node.adapter_token
