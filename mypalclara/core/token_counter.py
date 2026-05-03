"""Token counting utilities for prompt budget management."""

from __future__ import annotations

import tiktoken

# cl100k_base is a reasonable approximation for both OpenAI and Claude tokenizers
_encoder = tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str) -> int:
    """Count tokens in a text string."""
    if not text:
        return 0
    return len(_encoder.encode(text))


def count_message_tokens(messages: list) -> int:
    """Count tokens across a list of LLM messages.

    Each message costs its content tokens plus ~4 tokens for role overhead.
    Handles messages with None content (e.g. tool call assistant messages).
    """
    total = 0
    for msg in messages:
        content = getattr(msg, "content", None) or ""
        total += count_tokens(content) + 4
    return total


# Context window sizes by model family.
# IMPORTANT: order matters — the first key whose substring matches the model
# name wins. Place 1M-window aliases (opus-4-6, opus-4-7, sonnet-4-6) BEFORE
# the generic "claude" fallback or they'll get the smaller 200K window.
CONTEXT_WINDOWS: dict[str, int] = {
    "opus-4-7": 1_000_000,
    "opus-4-6": 1_000_000,
    "sonnet-4-6": 1_000_000,
    "claude": 200_000,  # fallback for older Claude (4.5 and earlier)
    "gpt-4o": 128_000,
    "gpt-4": 128_000,
    "default": 128_000,
}


def get_context_window(model_name: str) -> int:
    """Get context window size for a model.

    Pass the actual model string (e.g. ``claude-opus-4-7``) — passing a
    bare family name like ``"claude"`` will collapse 1M-context models
    down to the 200K fallback.
    """
    model_lower = model_name.lower()
    for key, size in CONTEXT_WINDOWS.items():
        if key in model_lower:
            return size
    return CONTEXT_WINDOWS["default"]
