"""HuggingFace embeddings via Inference API."""

import logging
import os
from typing import Literal, Optional

from mypalclara.core.memory.embeddings.base import BaseEmbedderConfig, EmbeddingBase

logger = logging.getLogger("clara.memory.embeddings.huggingface")

# Default model
DEFAULT_MODEL = "BAAI/bge-large-en-v1.5"
DEFAULT_DIMS = 1024


class HuggingFaceEmbedding(EmbeddingBase):
    """HuggingFace embeddings via Inference API.

    Uses huggingface_hub InferenceClient for remote embedding generation.
    Supports e5-family models which require "query: " / "passage: " prefixes.
    """

    def __init__(self, config: Optional[BaseEmbedderConfig] = None):
        super().__init__(config)

        self.config.model = self.config.model or DEFAULT_MODEL
        self.config.embedding_dims = self.config.embedding_dims or DEFAULT_DIMS

        token = self.config.api_key or os.getenv("HF_TOKEN") or os.getenv("HUGGINGFACE_API_KEY")

        from huggingface_hub import InferenceClient

        self._client = InferenceClient(
            provider="hf-inference",
            model=self.config.model,
            token=token,
        )
        model_lower = (self.config.model or "").lower()
        self._is_e5 = "e5" in model_lower
        self._is_bge = "bge" in model_lower

    def _prefix_text(self, text: str, memory_action: Optional[str]) -> str:
        """Add model-specific prefix based on memory action.

        e5 models: "query: " for search, "passage: " for documents.
        BGE models: "Represent this sentence: " for documents (search has no prefix).
        Other models: no prefix.
        """
        if self._is_e5:
            if memory_action == "search":
                return f"query: {text}"
            return f"passage: {text}"
        if self._is_bge:
            if memory_action == "search":
                return f"Represent this sentence for searching relevant passages: {text}"
            return text
        return text

    def embed(
        self,
        text: str,
        memory_action: Optional[Literal["add", "search", "update"]] = None,
    ) -> list[float]:
        """Get embedding via HuggingFace Inference API.

        Args:
            text: The text to embed.
            memory_action: "add", "search", or "update" — controls e5 prefix.

        Returns:
            Embedding vector as list of floats.
        """
        dims = self.config.embedding_dims or DEFAULT_DIMS

        if not text or not text.strip():
            return [0.0] * dims

        text = text.replace("\n", " ").lower()
        text = self._prefix_text(text, memory_action)

        result = self._client.feature_extraction(text)

        # Result may be nested (token-level) or flat (sentence-level).
        # Handle both: if 2D, mean-pool across tokens.
        if hasattr(result, "shape"):
            # numpy array
            if result.ndim == 2:
                vec = result.mean(axis=0).tolist()
            elif result.ndim == 1:
                vec = result.tolist()
            else:
                # 3D (batch, seq, hidden) — take first item, mean-pool
                vec = result[0].mean(axis=0).tolist()
        elif isinstance(result, list):
            if result and isinstance(result[0], list):
                # Nested list — mean-pool
                import statistics

                vec = [statistics.mean(col) for col in zip(*result)]
            else:
                vec = result
        else:
            vec = list(result)

        return vec[:dims]
