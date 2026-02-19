"""Clara Memory System configuration (Rook).

This module provides configuration and initialization for Clara's memory system.
The memory system is called "Rook" internally.

Environment variables use ROOK_* prefix with MEM0_* as deprecated fallback.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import load_dotenv

# Use Clara's native memory system
from clara_core.memory.core.memory import ClaraMemory

load_dotenv()

logger = logging.getLogger("clara.rook.config")


def _get_env(rook_key: str, mem0_key: str, default: str | None = None) -> str | None:
    """Get env var with ROOK_* preferred, MEM0_* as deprecated fallback."""
    rook_val = os.getenv(rook_key)
    if rook_val:
        return rook_val
    mem0_val = os.getenv(mem0_key)
    if mem0_val:
        logger.warning(f"{mem0_key} is deprecated, use {rook_key} instead")
        return mem0_val
    return default


# Rook has its own independent provider config (separate from chat LLM)
# ROOK_* preferred, MEM0_* fallback for backward compatibility
ROOK_PROVIDER = _get_env("ROOK_PROVIDER", "MEM0_PROVIDER", "openrouter").lower()
ROOK_MODEL = _get_env("ROOK_MODEL", "MEM0_MODEL", "openai/gpt-4o-mini")

# Optional overrides - if not set, uses the provider's default key/url
ROOK_API_KEY = _get_env("ROOK_API_KEY", "MEM0_API_KEY")
ROOK_BASE_URL = _get_env("ROOK_BASE_URL", "MEM0_BASE_URL")

# OpenAI API for embeddings (always required)
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# Provider defaults
PROVIDER_DEFAULTS = {
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "api_key_env": "OPENROUTER_API_KEY",
    },
    "nanogpt": {
        "base_url": "https://nano-gpt.com/api/v1",
        "api_key_env": "NANOGPT_API_KEY",
    },
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "api_key_env": "OPENAI_API_KEY",
    },
    "openai-custom": {
        "base_url": os.getenv("CUSTOM_OPENAI_BASE_URL", "https://api.openai.com/v1"),
        "api_key_env": "CUSTOM_OPENAI_API_KEY",
    },
    "anthropic": {
        "base_url": os.getenv("ANTHROPIC_BASE_URL"),  # None = default API
        "api_key_env": "ANTHROPIC_API_KEY",
    },
}

# Store memory data in a local directory
BASE_DATA_DIR = Path(os.getenv("DATA_DIR", str(Path(__file__).parent.parent.parent)))
QDRANT_DATA_DIR = BASE_DATA_DIR / "qdrant_data"

# Self-hosted Qdrant (recommended for production)
QDRANT_URL = os.getenv("QDRANT_URL")
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY")
# PostgreSQL with pgvector (legacy option, set ROOK_DATABASE_URL to use)
ROOK_DATABASE_URL = _get_env("ROOK_DATABASE_URL", "MEM0_DATABASE_URL")

# Vector store migration mode (for blue-green deployment)
# Options: primary_only, dual_write, dual_read, secondary_only
VECTOR_STORE_MODE = os.getenv("VECTOR_STORE_MODE", "primary_only")

# Only create Qdrant directory if we're using local Qdrant
if not ROOK_DATABASE_URL and not QDRANT_URL:
    QDRANT_DATA_DIR.mkdir(parents=True, exist_ok=True)

# Graph memory configuration (optional - for relationship tracking)
ENABLE_GRAPH_MEMORY = os.getenv("ENABLE_GRAPH_MEMORY", "false").lower() == "true"
GRAPH_STORE_PROVIDER = os.getenv("GRAPH_STORE_PROVIDER", "falkordb").lower()

# FalkorDB configuration
FALKORDB_HOST = os.getenv("FALKORDB_HOST", "localhost")
FALKORDB_PORT = int(os.getenv("FALKORDB_PORT", "6379"))
FALKORDB_PASSWORD = os.getenv("FALKORDB_PASSWORD") or None
FALKORDB_GRAPH_NAME = os.getenv("FALKORDB_GRAPH_NAME", "clara_memory")


def _get_graph_store_config() -> dict | None:
    """Build graph store config for relationship tracking."""
    if not ENABLE_GRAPH_MEMORY:
        return None

    if GRAPH_STORE_PROVIDER != "falkordb":
        logger.warning(f"Unknown GRAPH_STORE_PROVIDER={GRAPH_STORE_PROVIDER}, only 'falkordb' is supported")
        return None

    logger.info(f"Graph store: FalkorDB at {FALKORDB_HOST}:{FALKORDB_PORT}")
    return {
        "provider": "falkordb",
        "config": {
            "host": FALKORDB_HOST,
            "port": FALKORDB_PORT,
            "password": FALKORDB_PASSWORD,
            "graph_name": FALKORDB_GRAPH_NAME,
        },
    }


def _get_llm_config() -> dict | None:
    """Build LLM config based on ROOK_PROVIDER.

    Uses the unified provider from clara_core.llm for consistent behavior
    across all LLM operations (chat, memory, tools).
    """
    if ROOK_PROVIDER not in PROVIDER_DEFAULTS:
        logger.warning(f"Unknown ROOK_PROVIDER={ROOK_PROVIDER} - LLM disabled")
        return None

    provider_config = PROVIDER_DEFAULTS[ROOK_PROVIDER]

    # Get API key: explicit ROOK_API_KEY > provider's default key
    api_key = ROOK_API_KEY or os.getenv(provider_config["api_key_env"])
    if not api_key:
        logger.info(f"No API key found for ROOK_PROVIDER={ROOK_PROVIDER} - LLM disabled")
        return None

    # Get base URL: explicit ROOK_BASE_URL > provider's default URL
    base_url = ROOK_BASE_URL or provider_config["base_url"]

    logger.info(f"Rook LLM Provider: {ROOK_PROVIDER} (via unified)")
    logger.info(f"Rook LLM Model: {ROOK_MODEL}")
    if base_url:
        logger.info(f"Rook LLM Base URL: {base_url}")

    # Use unified provider for all backends
    # This ensures consistent behavior with clara_core.llm
    return {
        "provider": "unified",
        "config": {
            "provider": ROOK_PROVIDER,  # Actual provider (openrouter, anthropic, etc.)
            "model": ROOK_MODEL,
            "api_key": api_key,
            "base_url": base_url,
            "temperature": 0,
            "max_tokens": 8000,
        },
    }


# Get LLM config
llm_config = _get_llm_config()

# Get graph store config
graph_store_config = _get_graph_store_config()

# Collection name
ROOK_COLLECTION_NAME = _get_env("ROOK_COLLECTION_NAME", "MEM0_COLLECTION_NAME", "clara_memories")


# Embedding dimensions for text-embedding-3-small
EMBEDDING_MODEL_DIMS = 1536


def _build_vector_store_config() -> dict:
    """Build vector store configuration.

    Priority order:
    1. QDRANT_URL (self-hosted Qdrant) — recommended for all deployments
    2. ROOK_DATABASE_URL (pgvector) — legacy option, use migrate_pgvector_to_qdrant.py to migrate
    3. Local Qdrant (development default)
    """
    # 1. Self-hosted Qdrant (recommended)
    if QDRANT_URL:
        config = {
            "provider": "qdrant",
            "config": {
                "collection_name": ROOK_COLLECTION_NAME,
                "embedding_model_dims": EMBEDDING_MODEL_DIMS,
                "url": QDRANT_URL,
                "on_disk": True,  # Persist to disk
            },
        }
        if QDRANT_API_KEY:
            config["config"]["api_key"] = QDRANT_API_KEY
        logger.info(f"Vector store: Qdrant (self-hosted) at {QDRANT_URL}")
        logger.info(f"Collection: {ROOK_COLLECTION_NAME}")
        return config

    # 2. PostgreSQL with pgvector (legacy — migrate to Qdrant with scripts/migrate_pgvector_to_qdrant.py)
    if ROOK_DATABASE_URL:
        pgvector_url = ROOK_DATABASE_URL
        if pgvector_url.startswith("postgres://"):
            pgvector_url = pgvector_url.replace("postgres://", "postgresql://", 1)

        logger.info("Vector store: pgvector")
        logger.info(f"Collection: {ROOK_COLLECTION_NAME}")
        return {
            "provider": "pgvector",
            "config": {
                "connection_string": pgvector_url,
                "collection_name": ROOK_COLLECTION_NAME,
                "embedding_model_dims": EMBEDDING_MODEL_DIMS,
            },
        }

    # 3. Local Qdrant (development)
    logger.info(f"Vector store: Qdrant (local) at {QDRANT_DATA_DIR}")
    return {
        "provider": "qdrant",
        "config": {
            "collection_name": "clara_memories",
            "embedding_model_dims": EMBEDDING_MODEL_DIMS,
            "path": str(QDRANT_DATA_DIR),
        },
    }


# Build vector store config
vector_store_config = _build_vector_store_config()

# Embedding cache toggle
MEMORY_EMBEDDING_CACHE = os.getenv("MEMORY_EMBEDDING_CACHE", "true").lower() == "true"

# Build config - embeddings always use OpenAI (with optional caching)
config = {
    "vector_store": vector_store_config,
    "embedder": {
        "provider": "openai",
        "config": {
            "model": "text-embedding-3-small",
            "api_key": OPENAI_API_KEY,
        },
    },
}

# Log embedding cache status
if MEMORY_EMBEDDING_CACHE:
    redis_url = os.getenv("REDIS_URL")
    if redis_url:
        logger.info("Embedding cache: ENABLED (Redis)")
    else:
        logger.info("Embedding cache: DISABLED (no REDIS_URL)")
else:
    logger.info("Embedding cache: DISABLED (MEMORY_EMBEDDING_CACHE=false)")

# Only add LLM config if we have one
if llm_config:
    config["llm"] = llm_config

# Add graph store config if configured
if graph_store_config:
    config["graph_store"] = graph_store_config
    if llm_config:
        config["graph_store"]["llm"] = llm_config.copy()

# Debug summary
logger.info("Embeddings: OpenAI text-embedding-3-small")
if graph_store_config:
    logger.info(f"Graph memory: ENABLED ({GRAPH_STORE_PROVIDER})")
else:
    logger.info("Graph memory: DISABLED (set ENABLE_GRAPH_MEMORY=true to enable)")

# Initialize Rook (Clara's memory system singleton)
ROOK: ClaraMemory | None = None


def _init_rook() -> ClaraMemory | None:
    """Initialize Rook (Clara's memory system) synchronously."""
    if not OPENAI_API_KEY:
        logger.warning("OPENAI_API_KEY not set - Rook disabled (no embeddings)")
        return None

    try:
        memory = ClaraMemory.from_config(config)
        logger.info("Rook initialized successfully")
        return memory
    except Exception as e:
        logger.error(f"Failed to initialize Rook: {e}")
        logger.warning("App will run without memory features")
        return None


# Initialize at module load
ROOK = _init_rook()
