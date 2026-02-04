"""Base classes for LLM implementations."""

from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Union

import httpx


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
        http_client_proxies: Optional[Union[Dict, str]] = None,
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
            http_client_proxies: Proxy settings
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
        self.http_client = httpx.Client(proxies=http_client_proxies) if http_client_proxies else None
        self.response_format = response_format

    def get(self, key, default=None):
        """Get a config attribute by key."""
        return getattr(self, key, default)


class OpenAIConfig(BaseLlmConfig):
    """Configuration for OpenAI LLM."""

    def __init__(
        self,
        model: str = "gpt-4o-mini",
        temperature: float = 0.0,
        max_tokens: int = 3000,
        top_p: float = 1.0,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        openai_base_url: Optional[str] = None,  # Alias for base_url
        enable_vision: bool = False,
        vision_details: Optional[str] = "auto",
        http_client_proxies: Optional[Union[Dict, str]] = None,
        response_format: Optional[Dict] = None,
        **kwargs,  # Accept extra kwargs
    ):
        super().__init__(
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            top_p=top_p,
            api_key=api_key,
            base_url=base_url or openai_base_url,
            enable_vision=enable_vision,
            vision_details=vision_details,
            http_client_proxies=http_client_proxies,
            response_format=response_format,
        )


class AnthropicConfig(BaseLlmConfig):
    """Configuration for Anthropic LLM.

    IMPORTANT: Includes anthropic_base_url support for proxy servers like clewdr.
    """

    def __init__(
        self,
        model: str = "claude-sonnet-4-5",
        temperature: float = 0.0,
        max_tokens: int = 4096,
        top_p: float = 1.0,
        top_k: Optional[int] = None,
        api_key: Optional[str] = None,
        anthropic_base_url: Optional[str] = None,  # CRITICAL: Proxy support for clewdr
        enable_vision: bool = False,
        vision_details: Optional[str] = "auto",
        http_client_proxies: Optional[Union[Dict, str]] = None,
        **kwargs,  # Accept extra kwargs
    ):
        super().__init__(
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            top_p=top_p,
            top_k=top_k,
            api_key=api_key,
            enable_vision=enable_vision,
            vision_details=vision_details,
            http_client_proxies=http_client_proxies,
        )
        # CRITICAL: Custom attribute for proxy support (clewdr, etc.)
        self.anthropic_base_url = anthropic_base_url


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
        messages: List[Dict[str, str]],
        response_format: Optional[Dict] = None,
        tools: Optional[List[Dict]] = None,
        tool_choice: Optional[str] = None,
    ) -> str:
        """Generate a response based on the given messages.

        Args:
            messages: List of message dictionaries.
            response_format: Optional response format (e.g., {"type": "json_object"}).
            tools: Optional list of tools for function calling.
            tool_choice: Optional tool choice specification.

        Returns:
            The generated response as a string.
        """
        pass
