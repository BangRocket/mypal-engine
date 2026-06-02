"""Helpers for the visual-clara client integration.

- strip_pose_tags: remove [pose:…]/[expression:…] tags before persisting a
  response, so Palace memory is not polluted with presentation directives.
  Streamed chunks to the client keep the tags; only the stored copy is cleaned.
- collect_system_extra: gather client-supplied system messages from an
  OpenAI-style request so they can be injected after Clara's persona.
"""

from __future__ import annotations

import re

_POSE_TAG_RE = re.compile(r"\[(?:pose|expression):[^\]]*\]", re.IGNORECASE)


def strip_pose_tags(text: str) -> str:
    """Remove pose/expression tags and tidy the whitespace they leave behind."""
    if not text:
        return text
    cleaned = _POSE_TAG_RE.sub("", text)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)  # collapse internal runs
    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)  # trim trailing spaces on a line
    cleaned = re.sub(r"\n[ \t]+", "\n", cleaned)  # trim leading spaces on a line
    return cleaned.strip()


def collect_system_extra(messages: list[dict]) -> str:
    """Join non-empty string `system` message contents with blank lines."""
    parts: list[str] = []
    for msg in messages or []:
        if msg.get("role") == "system":
            content = msg.get("content")
            if isinstance(content, str) and content.strip():
                parts.append(content.strip())
    return "\n\n".join(parts)
