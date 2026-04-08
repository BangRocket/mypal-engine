# Switch Embeddings to HuggingFace e5-large-v2

## Goal

Replace OpenAI text-embedding-3-small with intfloat/e5-large-v2 via HuggingFace Inference API. Removes the hard dependency on OpenAI for embeddings while keeping OpenAI as an optional provider.

## Architecture

New `HuggingFaceEmbedding` class using `huggingface_hub.InferenceClient` (already a dependency). Config-driven provider selection via factory pattern in `ClaraMemory` init. Embedding dimensions change from 1536 to 1024, requiring vector store recreation and a migration script to re-embed existing memories.

## Components

### New: `mypalclara/core/memory/embeddings/huggingface.py`
- Subclasses `EmbeddingBase`
- Uses `huggingface_hub.InferenceClient.feature_extraction()` for embeddings
- Model: `intfloat/e5-large-v2` (1024 dims)
- Requires `HF_TOKEN` env var

### Modified: `mypalclara/core/memory/config.py`
- `EMBEDDING_MODEL_DIMS` = 1024
- Default embedder config switches to `provider: "huggingface"`
- `OPENAI_API_KEY` becomes optional (only needed for voice STT)

### Modified: `mypalclara/core/memory/core/memory.py`
- Factory pattern for embedder init: pick `HuggingFaceEmbedding` or `OpenAIEmbedding` based on config provider

### New: `scripts/migrate_embeddings.py`
- Reads all memories from DB
- Recreates Qdrant collection with 1024 dims
- Re-embeds all memories with new model

### Unchanged
- `openai.py` embedder (stays as optional provider)
- `cached.py` (wraps any embedder transparently)
- All memory operations (same `embed()` interface)
- Vector store interfaces (dimensions are already parameterized)
