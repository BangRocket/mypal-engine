"""Clara Memory System configuration.

This module provides configuration and initialization for Clara's memory system,
replacing the vendored mem0 configuration.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import load_dotenv

# Use Clara's native memory system
from clara_core.memory.core.memory import ClaraMemory

load_dotenv()

logger = logging.getLogger("clara.memory.config")

# Mem0 has its own independent provider config (separate from chat LLM)
MEM0_PROVIDER = os.getenv("MEM0_PROVIDER", "openrouter").lower()
MEM0_MODEL = os.getenv("MEM0_MODEL", "openai/gpt-4o-mini")

# Optional overrides - if not set, uses the provider's default key/url
MEM0_API_KEY = os.getenv("MEM0_API_KEY")
MEM0_BASE_URL = os.getenv("MEM0_BASE_URL")

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

# IMPORTANT: mem0 auto-detects these env vars and overrides our config!
# We must save and clear them before initialization, then restore after.
_saved_env_vars = {}
_env_vars_to_clear = [
    "OPENROUTER_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "MEM0_API_KEY",
]


def _clear_mem0_env_vars():
    """Clear env vars that might cause auto-detection issues."""
    for var in _env_vars_to_clear:
        if var in os.environ:
            _saved_env_vars[var] = os.environ.pop(var)
            logger.debug(f"Temporarily cleared {var} to prevent auto-detection")


def _restore_env_vars():
    """Restore cleared env vars after initialization."""
    for var, value in _saved_env_vars.items():
        os.environ[var] = value
        logger.debug(f"Restored {var}")


# Store memory data in a local directory
BASE_DATA_DIR = Path(os.getenv("DATA_DIR", str(Path(__file__).parent.parent.parent)))
QDRANT_DATA_DIR = BASE_DATA_DIR / "qdrant_data"

# PostgreSQL with pgvector for production (optional)
MEM0_DATABASE_URL = os.getenv("MEM0_DATABASE_URL")

# Self-hosted Qdrant (takes priority over pgvector and local Qdrant)
QDRANT_URL = os.getenv("QDRANT_URL")
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY")

# Vector store migration mode (for blue-green deployment)
# Options: primary_only, dual_write, dual_read, secondary_only
VECTOR_STORE_MODE = os.getenv("VECTOR_STORE_MODE", "primary_only")

# Only create Qdrant directory if we're using local Qdrant
if not MEM0_DATABASE_URL and not QDRANT_URL:
    QDRANT_DATA_DIR.mkdir(parents=True, exist_ok=True)

# Graph memory configuration (optional - for relationship tracking)
ENABLE_GRAPH_MEMORY = os.getenv("ENABLE_GRAPH_MEMORY", "false").lower() == "true"
GRAPH_STORE_PROVIDER = os.getenv("GRAPH_STORE_PROVIDER", "neo4j").lower()

# Neo4j configuration
NEO4J_URL = os.getenv("NEO4J_URL")
NEO4J_USERNAME = os.getenv("NEO4J_USERNAME", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")

# Kuzu configuration
KUZU_DATA_DIR = BASE_DATA_DIR / "kuzu_data"
if GRAPH_STORE_PROVIDER == "kuzu":
    KUZU_DATA_DIR.mkdir(parents=True, exist_ok=True)


def _get_graph_store_config() -> dict | None:
    """Build graph store config for relationship tracking."""
    if not ENABLE_GRAPH_MEMORY:
        return None

    if GRAPH_STORE_PROVIDER == "neo4j":
        if not NEO4J_URL or not NEO4J_PASSWORD:
            logger.warning("Neo4j configured but NEO4J_URL or NEO4J_PASSWORD not set")
            return None

        logger.info(f"Graph store: Neo4j at {NEO4J_URL}")
        return {
            "provider": "neo4j",
            "config": {
                "url": NEO4J_URL,
                "username": NEO4J_USERNAME,
                "password": NEO4J_PASSWORD,
            },
        }

    elif GRAPH_STORE_PROVIDER == "kuzu":
        logger.info(f"Graph store: Kuzu (embedded) at {KUZU_DATA_DIR}")
        return {
            "provider": "kuzu",
            "config": {
                "db_path": str(KUZU_DATA_DIR),
            },
        }

    else:
        logger.warning(f"Unknown GRAPH_STORE_PROVIDER={GRAPH_STORE_PROVIDER}")
        return None


def _get_llm_config() -> dict | None:
    """Build LLM config based on MEM0_PROVIDER."""
    if MEM0_PROVIDER not in PROVIDER_DEFAULTS:
        logger.warning(f"Unknown MEM0_PROVIDER={MEM0_PROVIDER} - LLM disabled")
        return None

    provider_config = PROVIDER_DEFAULTS[MEM0_PROVIDER]

    # Get API key: explicit MEM0_API_KEY > provider's default key
    api_key = MEM0_API_KEY or os.getenv(provider_config["api_key_env"])
    if not api_key:
        logger.info(f"No API key found for MEM0_PROVIDER={MEM0_PROVIDER} - LLM disabled")
        return None

    # Get base URL: explicit MEM0_BASE_URL > provider's default URL
    base_url = MEM0_BASE_URL or provider_config["base_url"]

    logger.info(f"Memory LLM Provider: {MEM0_PROVIDER}")
    logger.info(f"Memory LLM Model: {MEM0_MODEL}")
    if base_url:
        logger.info(f"Memory LLM Base URL: {base_url}")

    # Anthropic uses native SDK with anthropic_base_url
    if MEM0_PROVIDER == "anthropic":
        return {
            "provider": "anthropic",
            "config": {
                "model": MEM0_MODEL,
                "api_key": api_key,
                "anthropic_base_url": base_url,  # CRITICAL: Proxy support for clewdr
                "temperature": 0,
                "max_tokens": 8000,
            },
        }

    # All other providers use OpenAI-compatible endpoints
    return {
        "provider": "openai",
        "config": {
            "model": MEM0_MODEL,
            "api_key": api_key,
            "openai_base_url": base_url,
            "temperature": 0,
            "max_tokens": 8000,
        },
    }


# Get LLM config
llm_config = _get_llm_config()

# Get graph store config
graph_store_config = _get_graph_store_config()

# Collection name
MEM0_COLLECTION_NAME = os.getenv("MEM0_COLLECTION_NAME", "clara_memories")


def _build_vector_store_config() -> dict:
    """Build vector store configuration.

    Priority order:
    1. QDRANT_URL (self-hosted Qdrant) - NEW, recommended for production
    2. MEM0_DATABASE_URL (pgvector) - legacy production option
    3. Local Qdrant (development)
    """
    # 1. Self-hosted Qdrant (preferred for production)
    if QDRANT_URL:
        config = {
            "provider": "qdrant",
            "config": {
                "collection_name": MEM0_COLLECTION_NAME,
                "url": QDRANT_URL,
                "on_disk": True,  # Persist to disk
            },
        }
        if QDRANT_API_KEY:
            config["config"]["api_key"] = QDRANT_API_KEY
        logger.info(f"Vector store: Qdrant (self-hosted) at {QDRANT_URL}")
        logger.info(f"Collection: {MEM0_COLLECTION_NAME}")
        return config

    # 2. PostgreSQL with pgvector (legacy production)
    if MEM0_DATABASE_URL:
        pgvector_url = MEM0_DATABASE_URL
        if pgvector_url.startswith("postgres://"):
            pgvector_url = pgvector_url.replace("postgres://", "postgresql://", 1)

        logger.info("Vector store: pgvector")
        logger.info(f"Collection: {MEM0_COLLECTION_NAME}")
        return {
            "provider": "pgvector",
            "config": {
                "connection_string": pgvector_url,
                "collection_name": MEM0_COLLECTION_NAME,
            },
        }

    # 3. Local Qdrant (development)
    logger.info(f"Vector store: Qdrant (local) at {QDRANT_DATA_DIR}")
    return {
        "provider": "qdrant",
        "config": {
            "collection_name": "clara_memories",
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

# Initialize Clara Memory (singleton)
MEM0: ClaraMemory | None = None


def _init_mem0() -> ClaraMemory | None:
    """Initialize Clara Memory synchronously."""
    if not OPENAI_API_KEY:
        logger.warning("OPENAI_API_KEY not set - memory disabled (no embeddings)")
        return None

    try:
        _clear_mem0_env_vars()
        memory = ClaraMemory.from_config(config)
        logger.info("Clara Memory initialized successfully")
        return memory
    except Exception as e:
        logger.error(f"Failed to initialize Clara Memory: {e}")
        logger.warning("App will run without memory features")
        return None
    finally:
        _restore_env_vars()


# Initialize at module load
MEM0 = _init_mem0()


# Backward compatibility aliases
Memory = ClaraMemory
