"""RegisterMessage carries an optional secret; RegisteredMessage carries adapter_token."""

from mypal_protocol import RegisteredMessage, RegisterMessage


def test_register_message_accepts_secret():
    msg = RegisterMessage(node_id="discord-abc", platform="discord", secret="s3cr3t")
    assert msg.secret == "s3cr3t"


def test_register_message_secret_defaults_none():
    msg = RegisterMessage(node_id="discord-abc", platform="discord")
    assert msg.secret is None


def test_registered_message_carries_adapter_token():
    msg = RegisteredMessage(node_id="discord-abc", session_id="gw-1", adapter_token="adp-xyz")
    assert msg.adapter_token == "adp-xyz"
