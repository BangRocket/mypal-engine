"""Model tier management for LLM providers.

Provides tier-based model selection for different capability/cost tradeoffs:
- high: Most capable, expensive (Opus-class)
- mid: Balanced capability/cost (Sonnet-class) - default
- low: Fast, cheap, good for simple tasks (Haiku-class)
"""

from __future__ import annotations

import os
from enum import Enum
from typing import Literal, Union


class ModelTier(str, Enum):
    """Model tier enum for capability/cost tradeoffs.

    Inherits from str so it can be used directly as a string value.
    Usage: ModelTier.LOW == "low" and ModelTier.LOW.value == "low"
    """

    HIGH = "high"
    MID = "mid"
    LOW = "low"

    def __str__(self) -> str:
        return self.value


# Type alias for string literals (for type hints)
ModelTierLiteral = Literal["high", "mid", "low"]

# Union type for accepting both enum and string
ModelTierType = Union[ModelTier, ModelTierLiteral]

# Default tier
DEFAULT_TIER: ModelTierType = ModelTier.MID

# Default models per provider per tier
DEFAULT_MODELS = {
    "openrouter": {
        "high": "anthropic/claude-opus-4",
        "mid": "anthropic/claude-sonnet-4",
        "low": "anthropic/claude-haiku",
    },
    "nanogpt": {
        "high": "anthropic/claude-opus-4",
        "mid": "moonshotai/Kimi-K2-Instruct-0905",
        "low": "openai/gpt-4o-mini",
    },
    "openai": {
        "high": "claude-opus-4",
        "mid": "gpt-4o",
        "low": "gpt-4o-mini",
    },
    "anthropic": {
        "high": "claude-opus-4-5",
        "mid": "claude-sonnet-4-5",
        "low": "claude-haiku-4-5",
    },
    "bedrock": {
        # Amazon Bedrock model IDs for Claude
        "high": "anthropic.claude-3-5-sonnet-20241022-v2:0",
        "mid": "anthropic.claude-3-5-sonnet-20241022-v2:0",
        "low": "anthropic.claude-3-5-haiku-20241022-v1:0",
    },
    "azure": {
        # Azure OpenAI uses deployment names, not model names!
        # These are reference model names only - they won't work without configuration.
        # Users MUST set AZURE_DEPLOYMENT_NAME and optionally AZURE_MODEL_{HIGH,MID,LOW}
        # to match their Azure Portal deployments.
        "high": "gpt-4o",
        "mid": "gpt-4o",
        "low": "gpt-4o-mini",
    },
}


def get_model_for_tier(tier: ModelTierType, provider: str | None = None) -> str:
    """Get the model name for a specific tier and provider.

    Checks environment variables first, then falls back to defaults.

    Environment variables (by provider):
        OpenRouter: OPENROUTER_MODEL_{HIGH,MID,LOW}
        NanoGPT: NANOGPT_MODEL_{HIGH,MID,LOW}
        OpenAI: CUSTOM_OPENAI_MODEL_{HIGH,MID,LOW}
        Anthropic: ANTHROPIC_MODEL_{HIGH,MID,LOW}

    For backwards compatibility:
        - If tier-specific env var is not set, falls back to the base model env var
        - e.g., OPENROUTER_MODEL is used as the default for OPENROUTER_MODEL_MID

    Args:
        tier: The model tier ("high", "mid", "low")
        provider: The LLM provider. If None, uses LLM_PROVIDER env var.

    Returns:
        The model name to use.
    """
    if provider is None:
        provider = os.getenv("LLM_PROVIDER", "openrouter").lower()

    tier_upper = tier.upper()

    # Check for tier-specific environment variable
    if provider == "openrouter":
        tier_model = os.getenv(f"OPENROUTER_MODEL_{tier_upper}")
        if tier_model:
            return tier_model
        # Fall back to base model for mid tier, or defaults
        if tier == "mid":
            return os.getenv("OPENROUTER_MODEL", DEFAULT_MODELS["openrouter"]["mid"])
        return DEFAULT_MODELS["openrouter"].get(tier, DEFAULT_MODELS["openrouter"]["mid"])

    elif provider == "nanogpt":
        tier_model = os.getenv(f"NANOGPT_MODEL_{tier_upper}")
        if tier_model:
            return tier_model
        if tier == "mid":
            return os.getenv("NANOGPT_MODEL", DEFAULT_MODELS["nanogpt"]["mid"])
        return DEFAULT_MODELS["nanogpt"].get(tier, DEFAULT_MODELS["nanogpt"]["mid"])

    elif provider == "openai":
        tier_model = os.getenv(f"CUSTOM_OPENAI_MODEL_{tier_upper}")
        if tier_model:
            return tier_model
        if tier == "mid":
            return os.getenv("CUSTOM_OPENAI_MODEL", DEFAULT_MODELS["openai"]["mid"])
        return DEFAULT_MODELS["openai"].get(tier, DEFAULT_MODELS["openai"]["mid"])

    elif provider == "anthropic":
        tier_model = os.getenv(f"ANTHROPIC_MODEL_{tier_upper}")
        if tier_model:
            return tier_model
        if tier == "mid":
            return os.getenv("ANTHROPIC_MODEL", DEFAULT_MODELS["anthropic"]["mid"])
        return DEFAULT_MODELS["anthropic"].get(tier, DEFAULT_MODELS["anthropic"]["mid"])

    elif provider == "bedrock":
        tier_model = os.getenv(f"BEDROCK_MODEL_{tier_upper}")
        if tier_model:
            return tier_model
        if tier == "mid":
            return os.getenv("BEDROCK_MODEL", DEFAULT_MODELS["bedrock"]["mid"])
        return DEFAULT_MODELS["bedrock"].get(tier, DEFAULT_MODELS["bedrock"]["mid"])

    elif provider == "azure":
        tier_model = os.getenv(f"AZURE_MODEL_{tier_upper}")
        if tier_model:
            return tier_model
        if tier == "mid":
            return os.getenv("AZURE_MODEL", DEFAULT_MODELS["azure"]["mid"])
        return DEFAULT_MODELS["azure"].get(tier, DEFAULT_MODELS["azure"]["mid"])

    else:
        raise ValueError(f"Unknown provider: {provider}")


def get_base_model(provider: str | None = None) -> str:
    """Get the base model for a provider (without tier suffix).

    Returns the model from the base env var (e.g., CUSTOM_OPENAI_MODEL)
    rather than a tier-specific one (e.g., CUSTOM_OPENAI_MODEL_LOW).

    Args:
        provider: The LLM provider. If None, uses LLM_PROVIDER env var.

    Returns:
        The base model name.
    """
    if provider is None:
        provider = os.getenv("LLM_PROVIDER", "openrouter").lower()

    if provider == "openrouter":
        return os.getenv("OPENROUTER_MODEL", DEFAULT_MODELS["openrouter"]["mid"])
    elif provider == "nanogpt":
        return os.getenv("NANOGPT_MODEL", DEFAULT_MODELS["nanogpt"]["mid"])
    elif provider == "openai":
        return os.getenv("CUSTOM_OPENAI_MODEL", DEFAULT_MODELS["openai"]["mid"])
    elif provider == "anthropic":
        return os.getenv("ANTHROPIC_MODEL", DEFAULT_MODELS["anthropic"]["mid"])
    elif provider == "bedrock":
        return os.getenv("BEDROCK_MODEL", DEFAULT_MODELS["bedrock"]["mid"])
    elif provider == "azure":
        return os.getenv("AZURE_MODEL", DEFAULT_MODELS["azure"]["mid"])
    else:
        raise ValueError(f"Unknown provider: {provider}")


def get_current_tier() -> ModelTierType | None:
    """Get the current default tier from environment.

    Returns None if MODEL_TIER is not explicitly set, allowing callers
    to fall back to the base model instead of assuming "mid" tier.
    """
    tier = os.getenv("MODEL_TIER", "").lower()
    if tier in ("high", "mid", "low"):
        return tier  # type: ignore
    return None


def get_tier_info() -> dict:
    """Get information about configured tiers for current provider."""
    provider = os.getenv("LLM_PROVIDER", "openrouter").lower()
    current_tier = get_current_tier()
    return {
        "provider": provider,
        "current_tier": current_tier,
        "default_model": get_base_model(provider),
        "using_tiers": current_tier is not None,
        "models": {
            "high": get_model_for_tier("high", provider),
            "mid": get_model_for_tier("mid", provider),
            "low": get_model_for_tier("low", provider),
        },
    }


def get_tool_model(tier: ModelTierType | None = None) -> str:
    """Get the model to use for tool calling.

    Tool calls never use "low" tier - they use the base model at minimum.
    This ensures tools always have sufficient capability (never haiku).

    Args:
        tier: Optional tier override. If None, uses MODEL_TIER env var if set,
              otherwise uses base model. "low" tier is bumped to use the base model.
    """
    provider = os.getenv("LLM_PROVIDER", "openrouter").lower()

    # Never use "low" tier for tools - use base model instead
    if tier == "low":
        return get_base_model(provider)

    # For other tiers (high, mid, None), use tier-based selection
    effective_tier = tier or get_current_tier()

    # If no tier specified and MODEL_TIER not set, use base model
    if effective_tier is None:
        return get_base_model(provider)

    return get_model_for_tier(effective_tier, provider)
