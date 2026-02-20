"""OpenAI embeddings implementation."""

import os
import warnings
from typing import Literal, Optional

# Pre-import OpenAI resources to avoid deadlock when used in concurrent contexts.
# The OpenAI client lazy-loads these modules, which can cause import deadlocks
# when multiple threads try to import simultaneously.
import openai.resources.embeddings  # noqa: F401
from openai import OpenAI

from clara_core.memory.embeddings.base import BaseEmbedderConfig, EmbeddingBase


class OpenAIEmbedding(EmbeddingBase):
    """OpenAI embeddings implementation."""

    def __init__(self, config: Optional[BaseEmbedderConfig] = None):
        """Initialize OpenAI embedding.

        Args:
            config: Embedding configuration
        """
        super().__init__(config)

        self.config.model = self.config.model or "text-embedding-3-small"
        self.config.embedding_dims = self.config.embedding_dims or 1536

        api_key = self.config.api_key or os.getenv("OPENAI_API_KEY")
        base_url = (
            self.config.openai_base_url
            or os.getenv("OPENAI_API_BASE")
            or os.getenv("OPENAI_BASE_URL")
            or "https://api.openai.com/v1"
        )

        if os.environ.get("OPENAI_API_BASE"):
            warnings.warn(
                "The environment variable 'OPENAI_API_BASE' is deprecated. " "Please use 'OPENAI_BASE_URL' instead.",
                DeprecationWarning,
            )

        self.client = OpenAI(api_key=api_key, base_url=base_url)

        # Force eager initialization of embeddings to avoid import deadlock
        # when used in ThreadPoolExecutor
        _ = self.client.embeddings

    def embed(self, text, memory_action: Optional[Literal["add", "search", "update"]] = None):
        """Get the embedding for the given text using OpenAI.

        Args:
            text: The text to embed.
            memory_action: The type of embedding to use (add, search, or update).

        Returns:
            list: The embedding vector.
        """
        if not text or not text.strip():
            return [0.0] * (self.config.embedding_dims or 1536)

        text = text.replace("\n", " ")
        # Normalize case for embeddings so "OpenBC" and "openbc" produce
        # identical vectors. Stored memory text preserves original case —
        # only the embedding vector is affected.
        text = text.lower()
        return (
            self.client.embeddings.create(input=[text], model=self.config.model, dimensions=self.config.embedding_dims)
            .data[0]
            .embedding
        )
