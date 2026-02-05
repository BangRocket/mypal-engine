"""LLM configuration dataclass.

Provides a unified configuration object for all LLM providers.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    pass

# Model tier type
ModelTier = Literal["high", "mid", "low"]


@dataclass
class LLMConfig:
    """Unified configuration for LLM providers.

    Supports all Clara providers:
    - openrouter: OpenRouter API
    - nanogpt: NanoGPT API
    - openai: Custom OpenAI-compatible endpoint
    - anthropic: Native Anthropic SDK (with base_url for clewdr proxy)
    - bedrock: Amazon Bedrock (Claude models via AWS)
    - azure: Azure OpenAI Service

    Attributes:
        provider: LLM provider name
        model: Model name/identifier
        api_key: API key for authentication
        base_url: Base URL for API endpoint (optional)
        max_tokens: Maximum tokens in response
        temperature: Sampling temperature (0.0-2.0)
        tier: Model tier for tier-based selection
        extra_headers: Additional HTTP headers (e.g., Cloudflare Access)
        aws_region: AWS region for Bedrock (default: us-east-1)
        azure_deployment: Azure OpenAI deployment name
        azure_api_version: Azure OpenAI API version
    """

    provider: str
    model: str
    api_key: str | None = None
    base_url: str | None = None
    max_tokens: int = 4096
    temperature: float = 0.0
    tier: ModelTier | None = None
    extra_headers: dict[str, str] | None = None

    # Additional configuration options
    top_p: float = 1.0
    top_k: int | None = None
    response_format: dict | None = None

    # Provider-specific options
    aws_region: str | None = None  # Bedrock
    azure_deployment: str | None = None  # Azure OpenAI
    azure_api_version: str | None = None  # Azure OpenAI

    @classmethod
    def from_env(
        cls,
        provider: str | None = None,
        tier: ModelTier | None = None,
        for_tools: bool = False,
    ) -> "LLMConfig":
        """Create config from environment variables.

        Args:
            provider: Provider name. If None, uses LLM_PROVIDER env var.
            tier: Model tier. If None, uses MODEL_TIER env var if set.
            for_tools: If True, uses TOOL_* overrides and prevents low tier.

        Returns:
            LLMConfig instance configured from environment.

        Environment Variables:
            LLM_PROVIDER: Provider selection
                (openrouter, nanogpt, openai, anthropic, bedrock, azure)
            MODEL_TIER: Default tier (high, mid, low)

            OpenRouter:
                OPENROUTER_API_KEY, OPENROUTER_MODEL, OPENROUTER_MODEL_{HIGH,MID,LOW}
                OPENROUTER_SITE, OPENROUTER_TITLE

            NanoGPT:
                NANOGPT_API_KEY, NANOGPT_MODEL, NANOGPT_MODEL_{HIGH,MID,LOW}

            Custom OpenAI:
                CUSTOM_OPENAI_API_KEY, CUSTOM_OPENAI_BASE_URL
                CUSTOM_OPENAI_MODEL, CUSTOM_OPENAI_MODEL_{HIGH,MID,LOW}

            Anthropic:
                ANTHROPIC_API_KEY, ANTHROPIC_BASE_URL
                ANTHROPIC_MODEL, ANTHROPIC_MODEL_{HIGH,MID,LOW}

            Amazon Bedrock:
                AWS_REGION (default: us-east-1)
                AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY (or use IAM role)
                BEDROCK_MODEL, BEDROCK_MODEL_{HIGH,MID,LOW}

            Azure OpenAI:
                AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_KEY
                AZURE_DEPLOYMENT_NAME, AZURE_API_VERSION (default: 2024-02-15-preview)
                AZURE_MODEL, AZURE_MODEL_{HIGH,MID,LOW}

            Tool Overrides:
                TOOL_API_KEY, TOOL_BASE_URL

            Cloudflare Access:
                CF_ACCESS_CLIENT_ID, CF_ACCESS_CLIENT_SECRET
        """
        from clara_core.llm.tiers import get_base_model, get_current_tier, get_model_for_tier

        if provider is None:
            provider = os.getenv("LLM_PROVIDER", "openrouter").lower()

        # Determine effective tier
        effective_tier = tier or get_current_tier()

        # For tools, never use "low" tier - bump to base model
        if for_tools and effective_tier == "low":
            effective_tier = None

        # Get model based on tier
        if effective_tier is not None:
            model = get_model_for_tier(effective_tier, provider)
        else:
            model = get_base_model(provider)

        # Get provider-specific configuration
        if provider == "openrouter":
            api_key = os.getenv("OPENROUTER_API_KEY")
            base_url = "https://openrouter.ai/api/v1"
            site = os.getenv("OPENROUTER_SITE", "http://localhost:3000")
            title = os.getenv("OPENROUTER_TITLE", "MyPalClara")
            extra_headers = {
                "HTTP-Referer": site,
                "X-Title": title,
            }

        elif provider == "nanogpt":
            api_key = os.getenv("NANOGPT_API_KEY")
            base_url = "https://nano-gpt.com/api/v1"
            extra_headers = None

        elif provider == "openai":
            api_key = os.getenv("CUSTOM_OPENAI_API_KEY")
            base_url = os.getenv("CUSTOM_OPENAI_BASE_URL", "https://api.openai.com/v1")
            extra_headers = _get_cf_access_headers()

        elif provider == "anthropic":
            api_key = os.getenv("ANTHROPIC_API_KEY")
            base_url = os.getenv("ANTHROPIC_BASE_URL")
            extra_headers = _get_cf_access_headers()
            # Override User-Agent for proxy compatibility
            if base_url and extra_headers is None:
                extra_headers = {}
            if base_url and extra_headers is not None:
                extra_headers["User-Agent"] = "Clara/1.0"

        elif provider == "bedrock":
            # Amazon Bedrock uses AWS credentials (env vars, IAM role, or profile)
            # No API key needed - uses boto3 credential chain
            api_key = None
            base_url = None
            extra_headers = None

        elif provider == "azure":
            api_key = os.getenv("AZURE_OPENAI_API_KEY")
            base_url = os.getenv("AZURE_OPENAI_ENDPOINT")
            extra_headers = None

        else:
            raise ValueError(f"Unknown provider: {provider}")

        # Apply tool overrides if requested
        if for_tools:
            tool_api_key = os.getenv("TOOL_API_KEY")
            tool_base_url = os.getenv("TOOL_BASE_URL")
            if tool_api_key:
                api_key = tool_api_key
            if tool_base_url:
                base_url = tool_base_url

        # Provider-specific config
        aws_region = None
        azure_deployment = None
        azure_api_version = None

        if provider == "bedrock":
            aws_region = os.getenv("AWS_REGION", "us-east-1")

        elif provider == "azure":
            azure_deployment = os.getenv("AZURE_DEPLOYMENT_NAME")
            azure_api_version = os.getenv("AZURE_API_VERSION", "2024-02-15-preview")

        return cls(
            provider=provider,
            model=model,
            api_key=api_key,
            base_url=base_url,
            extra_headers=extra_headers,
            tier=effective_tier,
            aws_region=aws_region,
            azure_deployment=azure_deployment,
            azure_api_version=azure_api_version,
        )

    def with_tier(self, tier: ModelTier) -> "LLMConfig":
        """Create a new config with a different tier.

        Args:
            tier: New model tier

        Returns:
            New LLMConfig with updated tier and model
        """
        from clara_core.llm.tiers import get_model_for_tier

        return LLMConfig(
            provider=self.provider,
            model=get_model_for_tier(tier, self.provider),
            api_key=self.api_key,
            base_url=self.base_url,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            tier=tier,
            extra_headers=self.extra_headers,
            top_p=self.top_p,
            top_k=self.top_k,
            aws_region=self.aws_region,
            azure_deployment=self.azure_deployment,
            azure_api_version=self.azure_api_version,
        )


def _get_cf_access_headers() -> dict[str, str] | None:
    """Get Cloudflare Access headers if configured.

    For endpoints behind Cloudflare Access (like cloudflared tunnels),
    set these environment variables:
    - CF_ACCESS_CLIENT_ID: Service token client ID
    - CF_ACCESS_CLIENT_SECRET: Service token client secret
    """
    client_id = os.getenv("CF_ACCESS_CLIENT_ID")
    client_secret = os.getenv("CF_ACCESS_CLIENT_SECRET")
    if client_id and client_secret:
        return {
            "CF-Access-Client-Id": client_id,
            "CF-Access-Client-Secret": client_secret,
        }
    return None
