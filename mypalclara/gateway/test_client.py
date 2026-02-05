#!/usr/bin/env python3
"""Test client for the Clara Gateway.

Usage:
    poetry run python gateway/test_client.py
    poetry run python gateway/test_client.py --url ws://localhost:18789

Sends a test message and prints the streaming response.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import websockets

from mypalclara.gateway.protocol import (
    ChannelInfo,
    MessageRequest,
    MessageType,
    RegisterMessage,
    UserInfo,
)


async def main(url: str, message: str) -> None:
    """Connect to gateway and send a test message.

    Args:
        url: WebSocket URL to connect to
        message: Message content to send
    """
    node_id = f"test-client-{uuid.uuid4().hex[:8]}"

    print(f"Connecting to {url}...")

    async with websockets.connect(url) as ws:
        # Register
        register = RegisterMessage(
            node_id=node_id,
            platform="test",
            capabilities=["streaming"],
        )
        await ws.send(register.model_dump_json())

        # Wait for registration response
        response = await ws.recv()
        data = json.loads(response)
        print(f"Registered: {data}")

        # Send test message
        request = MessageRequest(
            id=f"msg-{uuid.uuid4().hex[:8]}",
            user=UserInfo(
                id="test-user-1",
                platform_id="1",
                name="TestUser",
                display_name="Test User",
            ),
            channel=ChannelInfo(
                id="test-channel-1",
                type="dm",
                name="test-channel",
            ),
            content=message,
        )
        print(f"\nSending: {message}")
        await ws.send(request.model_dump_json())

        # Receive streaming response
        print("\nReceiving response:")
        print("-" * 40)

        while True:
            try:
                response = await asyncio.wait_for(ws.recv(), timeout=30.0)
                data = json.loads(response)
                msg_type = data.get("type")

                if msg_type == MessageType.RESPONSE_START:
                    print(f"[START] Response ID: {data.get('id')}")

                elif msg_type == MessageType.RESPONSE_CHUNK:
                    # Print chunk without newline for streaming effect
                    print(data.get("chunk", ""), end="", flush=True)

                elif msg_type == MessageType.TOOL_START:
                    print(f"\n[TOOL] {data.get('emoji', '⚙️')} {data.get('tool_name')}...")

                elif msg_type == MessageType.TOOL_RESULT:
                    status = "✓" if data.get("success") else "✗"
                    print(f"[TOOL] {status} {data.get('tool_name')}")

                elif msg_type == MessageType.RESPONSE_END:
                    print("\n" + "-" * 40)
                    print(f"[END] Total length: {len(data.get('full_text', ''))} chars")
                    break

                elif msg_type == MessageType.ERROR:
                    print(f"\n[ERROR] {data.get('code')}: {data.get('message')}")
                    break

                elif msg_type == MessageType.STATUS:
                    print(f"[STATUS] Queue position: {data.get('queue_length')}")

                else:
                    print(f"[{msg_type}] {data}")

            except asyncio.TimeoutError:
                print("\n[TIMEOUT] No response received")
                break

    print("\nDisconnected")


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description="Clara Gateway Test Client")

    parser.add_argument(
        "--url",
        default=os.getenv("CLARA_GATEWAY_URL", "ws://127.0.0.1:18789"),
        help="Gateway WebSocket URL",
    )
    parser.add_argument(
        "message",
        nargs="?",
        default="Hello Clara! This is a test message from the gateway test client.",
        help="Message to send",
    )

    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()

    try:
        asyncio.run(main(args.url, args.message))
    except KeyboardInterrupt:
        print("\nCancelled")
    except ConnectionRefusedError:
        print("Connection refused. Is the gateway running?")
        print("Start it with: poetry run python -m gateway")
        sys.exit(1)
