"""Token counting utilities for prompt budget management."""

from __future__ import annotations

import tiktoken

# cl100k_base is a reasonable approximation for both OpenAI and Claude tokenizers
_encoder = tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str) -> int:
    """Count tokens in a text string."""
    return len(_encoder.encode(text))


def count_message_tokens(messages: list) -> int:
    """Count tokens across a list of LLM messages.

    Each message costs its content tokens plus ~4 tokens for role overhead.
    """
    total = 0
    for msg in messages:
        total += count_tokens(msg.content) + 4
    return total


# Context window sizes by model family
CONTEXT_WINDOWS: dict[str, int] = {
    "claude": 200_000,
    "gpt-4o": 128_000,
    "gpt-4": 128_000,
    "default": 128_000,
}


def get_context_window(model_name: str) -> int:
    """Get context window size for a model."""
    model_lower = model_name.lower()
    for key, size in CONTEXT_WINDOWS.items():
        if key in model_lower:
            return size
    return CONTEXT_WINDOWS["default"]
