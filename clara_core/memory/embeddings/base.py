"""Base classes for embeddings."""

from abc import ABC, abstractmethod
from typing import Dict, Literal, Optional, Union

import httpx


class BaseEmbedderConfig(ABC):
    """Configuration for embeddings.

    Simplified version containing only OpenAI-relevant parameters.
    """

    def __init__(
        self,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        embedding_dims: Optional[int] = None,
        openai_base_url: Optional[str] = None,
        http_client_proxies: Optional[Union[Dict, str]] = None,
    ):
        """Initialize embedder configuration.

        Args:
            model: Embedding model to use, defaults to None
            api_key: API key to be used, defaults to None
            embedding_dims: Number of dimensions in the embedding, defaults to None
            openai_base_url: OpenAI base URL to use, defaults to None
            http_client_proxies: Proxy settings for HTTP client, defaults to None
        """
        self.model = model
        self.api_key = api_key
        self.openai_base_url = openai_base_url
        self.embedding_dims = embedding_dims
        self.http_client = httpx.Client(proxies=http_client_proxies) if http_client_proxies else None


class EmbeddingBase(ABC):
    """Base class for all embeddings implementations."""

    def __init__(self, config: Optional[BaseEmbedderConfig] = None):
        """Initialize embedding class.

        Args:
            config: Embedding configuration, defaults to None
        """
        if config is None:
            self.config = BaseEmbedderConfig()
        else:
            self.config = config

    @abstractmethod
    def embed(self, text, memory_action: Optional[Literal["add", "search", "update"]]):
        """Get the embedding for the given text.

        Args:
            text: The text to embed.
            memory_action: The type of embedding to use (add, search, or update).

        Returns:
            list: The embedding vector.
        """
        pass
