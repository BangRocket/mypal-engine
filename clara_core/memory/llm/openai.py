"""OpenAI LLM implementation."""

import json
import os
from typing import Dict, List, Optional

from openai import OpenAI

# Pre-import OpenAI resources to avoid deadlock when used in concurrent contexts.
# The OpenAI client lazy-loads these modules, which can cause import deadlocks
# when multiple threads try to import simultaneously.
import openai.resources.chat  # noqa: F401
import openai.resources.embeddings  # noqa: F401

from clara_core.memory.llm.base import LLMBase, OpenAIConfig


class OpenAILLM(LLMBase):
    """OpenAI LLM implementation (works with OpenAI-compatible endpoints)."""

    def __init__(self, config: Optional[OpenAIConfig] = None):
        """Initialize OpenAI LLM.

        Args:
            config: OpenAI configuration
        """
        super().__init__(config)

        # Set defaults
        self.config.model = self.config.model or "gpt-4o-mini"

        api_key = self.config.api_key or os.getenv("OPENAI_API_KEY")
        base_url = self.config.base_url or os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1"

        # Allow http_client override
        client_kwargs = {"api_key": api_key, "base_url": base_url}
        if self.config.http_client:
            client_kwargs["http_client"] = self.config.http_client

        self.client = OpenAI(**client_kwargs)

        # Force eager initialization of chat.completions to avoid import deadlock
        # when used in ThreadPoolExecutor
        _ = self.client.chat

    def generate_response(
        self,
        messages: List[Dict[str, str]],
        response_format: Optional[Dict] = None,
        tools: Optional[List[Dict]] = None,
        tool_choice: Optional[str] = None,
    ) -> Dict:
        """Generate a response using OpenAI's API.

        Args:
            messages: List of message dictionaries.
            response_format: Optional response format.
            tools: Optional list of tools for function calling.
            tool_choice: Optional tool choice specification.

        Returns:
            The generated response content or tool call payload.
        """
        params = {
            "model": self.config.model,
            "messages": messages,
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
            "top_p": self.config.top_p,
        }

        if response_format:
            params["response_format"] = response_format
        if tools:
            params["tools"] = tools
        if tool_choice:
            params["tool_choice"] = tool_choice

        response = self.client.chat.completions.create(**params)
        message = response.choices[0].message

        if tools:
            tool_calls = []
            for call in message.tool_calls or []:
                arguments = call.function.arguments
                try:
                    arguments = json.loads(arguments) if isinstance(arguments, str) else arguments
                except json.JSONDecodeError:
                    # Leave arguments as raw string if parsing fails
                    pass
                tool_calls.append(
                    {
                        "name": call.function.name,
                        "arguments": arguments,
                    }
                )
            return {"tool_calls": tool_calls}

        return message.content
