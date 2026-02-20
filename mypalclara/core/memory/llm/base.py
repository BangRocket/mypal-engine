"""Base classes for LLM implementations."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional


class BaseLlmConfig:
    """Base configuration for LLMs."""

    def __init__(
        self,
        model: Optional[str] = None,
        temperature: float = 0.0,
        max_tokens: int = 3000,
        top_p: float = 1.0,
        top_k: Optional[int] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        enable_vision: bool = False,
        vision_details: Optional[str] = "auto",
        response_format: Optional[Dict] = None,
    ):
        """Initialize LLM configuration.

        Args:
            model: Model name
            temperature: Sampling temperature
            max_tokens: Maximum tokens in response
            top_p: Top-p sampling parameter
            top_k: Top-k sampling parameter
            api_key: API key
            base_url: Base URL for API
            enable_vision: Enable vision/image processing
            vision_details: Vision detail level
            response_format: Response format configuration
        """
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.top_p = top_p
        self.top_k = top_k
        self.api_key = api_key
        self.base_url = base_url
        self.enable_vision = enable_vision
        self.vision_details = vision_details
        self.response_format = response_format

    def get(self, key, default=None):
        """Get a config attribute by key."""
        return getattr(self, key, default)


class LLMBase(ABC):
    """Base class for all LLM implementations."""

    def __init__(self, config: Optional[BaseLlmConfig] = None):
        """Initialize LLM.

        Args:
            config: LLM configuration
        """
        self.config = config or BaseLlmConfig()

    @abstractmethod
    def generate_response(
        self,
        messages: list[dict[str, str]] | list[Any],
        response_format: dict | None = None,
        tools: list[dict] | None = None,
        tool_choice: str | None = None,
    ) -> str:
        """Generate a response based on the given messages.

        Args:
            messages: List of message dicts or typed Message objects.
            response_format: Optional response format (e.g., {"type": "json_object"}).
            tools: Optional list of tools for function calling.
            tool_choice: Optional tool choice specification.

        Returns:
            The generated response as a string.
        """
        pass
