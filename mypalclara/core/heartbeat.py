"""OpenClaw-inspired heartbeat system.

Periodically wakes Clara to check if anything needs attention using
workspace-driven HEARTBEAT.md instructions.
"""

from __future__ import annotations

import os

ACK_TOKEN = "HEARTBEAT_OK"
DEFAULT_INTERVAL_MINUTES = 30
DEFAULT_ACK_MAX_CHARS = 300


def is_ack(response: str, max_chars: int = DEFAULT_ACK_MAX_CHARS) -> bool:
    """Check if a heartbeat response is an acknowledgement (nothing to report).

    Returns True if the response is effectively HEARTBEAT_OK with minimal
    surrounding text (under max_chars after stripping the token).
    """
    text = response.strip()
    if not text:
        return False

    if text == ACK_TOKEN:
        return True

    # Strip HEARTBEAT_OK from start or end, check remaining length
    remaining = text
    if remaining.startswith(ACK_TOKEN):
        remaining = remaining[len(ACK_TOKEN) :].strip()
    elif remaining.endswith(ACK_TOKEN):
        remaining = remaining[: -len(ACK_TOKEN)].strip()
    else:
        return False

    return len(remaining) <= max_chars
