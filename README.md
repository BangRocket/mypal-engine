# MyPalClara

AI assistant with session management and persistent memory. The assistant's name is Clara.

## Installation

```bash
poetry install
```

## Usage

### Development

Backend:
```bash
poetry run python api.py
```

Frontend:
```bash
cd frontend
npm install
npm run dev
```

### Docker

```bash
docker-compose up
```

## Features

- Threaded chat interface built with assistant-ui
- Session-based conversations with thread management
- User memory for persistent facts and preferences (via mem0)
- Graph memory for relationship tracking (Neo4j)
- Project memory for topic-specific context
- SQLite storage via SQLAlchemy
- Multiple LLM backend support (OpenRouter, NanoGPT, OpenAI)

## Memory System

MyPalClara uses [mem0](https://github.com/mem0ai/mem0) for memory management with:

- **Vector Store (Qdrant)**: Semantic search over memories
- **Graph Store (Neo4j)**: Relationship tracking between entities

### Bootstrap Profile Data

Extract and seed memories from a user profile:

```bash
# Generate JSON files (dry run)
poetry run python -m src.bootstrap_memory

# Apply to mem0 (vector + graph)
poetry run python -m src.bootstrap_memory --apply

# Force regeneration of JSON files
poetry run python -m src.bootstrap_memory --force --apply
```

This extracts atomic memories into namespaces:
- `profile_bio` - Name, family, location, career
- `interaction_style` - Tone, boundaries, formatting preferences
- `project_seed` - Assistant operating principles
- `project_context:creative_portfolio` - Game projects, tools, aesthetics
- `restricted:sensitive` - Mental health context (flagged in metadata)

### Clear Databases

Clear all memory data (vector store + graph store):

```bash
# With confirmation prompt
poetry run python clear_dbs.py

# Skip confirmation
poetry run python clear_dbs.py --yes

# Clear specific user
poetry run python clear_dbs.py --user <user_id>
```

## Environment Variables

Copy `.env.example` to `.env` and configure:

### Required
- `OPENAI_API_KEY` - For embeddings (text-embedding-3-small)

### Chat LLM (choose one provider)
- `LLM_PROVIDER` - `openrouter`, `nanogpt`, or `openai`
- Provider-specific keys (see `.env.example`)

### Memory LLM
- `MEM0_PROVIDER` - Provider for memory extraction (default: `openai`)
- `MEM0_MODEL` - Model for extraction (default: `gpt-4o-mini`)

### Graph Store (optional)
- `GRAPH_STORE_PROVIDER` - `neo4j` or `kuzu` (embedded)
- `NEO4J_URL`, `NEO4J_USERNAME`, `NEO4J_PASSWORD` - For Neo4j
