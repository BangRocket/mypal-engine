"""Unified LLM implementation using the new clara_core.llm providers.

This module bridges the memory system's LLMBase interface with the
unified LLM provider architecture in clara_core.llm.

Benefits:
- Consistent LLM behavior across chat and memory systems
- Single configuration point for providers
- Automatic format conversion handled by unified providers
"""

from __future__ import annotations

from typing import Any

from clara_core.memory.llm.base import BaseLlmConfig, LLMBase


class UnifiedLLMConfig(BaseLlmConfig):
    """Configuration for UnifiedLLM.

    Maps memory system config to unified LLMConfig.

    Attributes:
        provider: LLM provider (openrouter, nanogpt, openai, anthropic)
        model: Model name
        api_key: API key
        base_url: Base URL (openai_base_url or anthropic_base_url)
    """

    def __init__(
        self,
        provider: str = "openrouter",
        model: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        openai_base_url: str | None = None,  # Alias for OpenAI-compatible
        anthropic_base_url: str | None = None,  # Alias for Anthropic
        temperature: float = 0.0,
        max_tokens: int = 8000,
        **kwargs,
    ):
        super().__init__(
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            api_key=api_key,
            **kwargs,
        )
        self.provider = provider
        # Resolve base_url from aliases
        self.base_url = base_url or openai_base_url or anthropic_base_url


class UnifiedLLM(LLMBase):
    """Memory system LLM using unified clara_core.llm providers.

    This class adapts the new LLMProvider interface to the memory
    system's LLMBase interface, ensuring consistent behavior across
    all LLM operations.
    """

    def __init__(self, config: UnifiedLLMConfig | None = None):
        """Initialize with unified providers.

        Args:
            config: Configuration with provider, model, api_key, base_url
        """
        super().__init__(config)

        if not isinstance(self.config, UnifiedLLMConfig):
            # Convert base config to unified config
            self.config = UnifiedLLMConfig(
                model=self.config.model,
                api_key=self.config.api_key,
                base_url=self.config.base_url,
                temperature=self.config.temperature,
                max_tokens=self.config.max_tokens,
            )

        # Import here to avoid circular imports
        from clara_core.llm.config import LLMConfig
        from clara_core.llm.providers.registry import get_provider

        # Build LLMConfig from memory config
        self._llm_config = LLMConfig(
            provider=self.config.provider,
            model=self.config.model or self._get_default_model(),
            api_key=self.config.api_key,
            base_url=self.config.base_url,
            max_tokens=self.config.max_tokens,
            temperature=self.config.temperature,
            extra_headers=self._get_extra_headers(),
        )

        # Get the unified provider (LangChain-based)
        self._provider = get_provider("langchain")

    def _get_default_model(self) -> str:
        """Get default model for provider."""
        defaults = {
            "openrouter": "openai/gpt-4o-mini",
            "nanogpt": "openai/gpt-4o-mini",
            "openai": "gpt-4o-mini",
            "anthropic": "claude-sonnet-4-5",
        }
        return defaults.get(self.config.provider, "gpt-4o-mini")

    def _get_extra_headers(self) -> dict[str, str] | None:
        """Get extra headers for provider."""
        import os

        if self.config.provider == "openrouter":
            return {
                "HTTP-Referer": os.getenv("OPENROUTER_SITE", "http://localhost:3000"),
                "X-Title": os.getenv("OPENROUTER_TITLE", "MyPalClara"),
            }

        # Cloudflare Access headers
        cf_id = os.getenv("CF_ACCESS_CLIENT_ID")
        cf_secret = os.getenv("CF_ACCESS_CLIENT_SECRET")
        if cf_id and cf_secret:
            headers = {
                "CF-Access-Client-Id": cf_id,
                "CF-Access-Client-Secret": cf_secret,
            }
            if self.config.base_url and self.config.provider == "anthropic":
                headers["User-Agent"] = "Clara/1.0"
            return headers

        return None

    def generate_response(
        self,
        messages: list[dict[str, str]],
        response_format: dict | None = None,
        tools: list[dict] | None = None,
        tool_choice: str | None = None,
    ) -> Any:
        """Generate a response using unified providers.

        Args:
            messages: List of message dictionaries.
            response_format: Optional response format (not widely supported).
            tools: Optional list of tools for function calling.
            tool_choice: Optional tool choice specification.

        Returns:
            String content for text responses, or {"tool_calls": [...]} for tools.
        """
        if tools:
            # Use tool calling
            response = self._provider.complete_with_tools(messages, tools, self._llm_config)

            if response.has_tool_calls:
                return {"tool_calls": [{"name": tc.name, "arguments": tc.arguments} for tc in response.tool_calls]}

            return response.content or ""

        # Simple completion
        return self._provider.complete(messages, self._llm_config)
