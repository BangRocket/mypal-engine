"""Anthropic LLM implementation with proxy support (clewdr, etc.).

IMPORTANT: This implementation includes anthropic_base_url support for
proxy servers like clewdr. This is a CRITICAL feature that must be preserved.
"""

import os
from typing import Dict, List, Optional

from anthropic import Anthropic

from clara_core.memory.llm.base import AnthropicConfig, LLMBase


class AnthropicLLM(LLMBase):
    """Anthropic LLM implementation with proxy support.

    Supports anthropic_base_url for connecting through proxy servers
    like clewdr, which is essential for some deployment scenarios.
    """

    def __init__(self, config: Optional[AnthropicConfig] = None):
        """Initialize Anthropic LLM.

        Args:
            config: Anthropic configuration (includes anthropic_base_url for proxy support)
        """
        super().__init__(config)

        # Set defaults
        self.config.model = self.config.model or "claude-sonnet-4-5"

        api_key = self.config.api_key or os.getenv("ANTHROPIC_API_KEY")

        # Build client kwargs
        client_kwargs = {"api_key": api_key}

        # CRITICAL: Support for anthropic_base_url (proxy servers like clewdr)
        # This custom fix enables connection through proxy servers
        if self.config.anthropic_base_url:
            client_kwargs["base_url"] = self.config.anthropic_base_url

        # Allow http_client override
        if self.config.http_client:
            client_kwargs["http_client"] = self.config.http_client

        self.client = Anthropic(**client_kwargs)

    def generate_response(
        self,
        messages: List[Dict[str, str]],
        response_format: Optional[Dict] = None,
        tools: Optional[List[Dict]] = None,
        tool_choice: Optional[str] = None,
    ) -> Dict:
        """Generate a response using Anthropic's API.

        Args:
            messages: List of message dictionaries.
            response_format: Optional response format (JSON mode support).
            tools: Optional list of tools for function calling.
            tool_choice: Optional tool choice specification.

        Returns:
            The generated response content or tool call payload.
        """
        # Extract system message if present
        system = None
        filtered_messages = []
        for msg in messages:
            if msg["role"] == "system":
                system = msg["content"]
            else:
                filtered_messages.append(msg)

        params = {
            "model": self.config.model,
            "messages": filtered_messages,
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
        }

        if system:
            params["system"] = system
        if self.config.top_p is not None:
            params["top_p"] = self.config.top_p
        if self.config.top_k is not None:
            params["top_k"] = self.config.top_k
        if tools:
            params["tools"] = tools
        if tool_choice:
            params["tool_choice"] = tool_choice

        response = self.client.messages.create(**params)

        if tools:
            tool_calls = []
            for block in response.content:
                if getattr(block, "type", None) != "tool_use":
                    continue
                tool_calls.append(
                    {
                        "name": block.name,
                        "arguments": block.input,
                    }
                )
            return {"tool_calls": tool_calls}

        # Extract text content from response
        content = ""
        for block in response.content:
            if hasattr(block, "text"):
                content += block.text

        return content
