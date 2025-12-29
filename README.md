# MyPalClara

A personal AI assistant with persistent memory and tool capabilities, powered by Discord. The assistant's name is Clara.

## Features

- **Discord Interface** - Full-featured Discord bot with streaming responses and reply chains
- **Persistent Memory** - User and project memories via [mem0](https://github.com/mem0ai/mem0)
- **Code Execution** - Sandboxed Python/Bash via local Docker or remote VPS
- **Web Search** - Real-time web search via Tavily
- **File Management** - Local file storage with S3 sync support
- **GitHub/Azure DevOps** - Repository, issue, PR, and pipeline management
- **Claude Code Integration** - Delegate complex coding tasks to Claude Code agent
- **Multiple LLM Backends** - OpenRouter, NanoGPT, or custom OpenAI-compatible endpoints
- **Model Tiers** - Dynamic model selection via message prefixes (`!high`, `!mid`, `!low`)

## Quick Start

### Prerequisites

- Python 3.11+
- [Poetry](https://python-poetry.org/)
- Docker (optional, for code execution sandbox)
- Discord bot token

### Installation

```bash
# Clone and install
git clone https://github.com/BangRocket/mypalclara.git
cd mypalclara
poetry install

# Configure environment
cp .env.example .env
# Edit .env with your API keys
```

### Running

```bash
# Run Discord bot locally
poetry run python discord_bot.py

# Or with Docker
docker-compose --profile discord up
```

## Configuration

### Required Environment Variables

| Variable | Description |
|----------|-------------|
| `DISCORD_BOT_TOKEN` | Discord bot token |
| `OPENAI_API_KEY` | Required for mem0 embeddings |
| `LLM_PROVIDER` | `openrouter`, `nanogpt`, or `openai` |

### LLM Providers

**OpenRouter** (default):
```bash
LLM_PROVIDER=openrouter
OPENROUTER_API_KEY=your-key
OPENROUTER_MODEL=anthropic/claude-sonnet-4
```

**NanoGPT**:
```bash
LLM_PROVIDER=nanogpt
NANOGPT_API_KEY=your-key
NANOGPT_MODEL=moonshotai/Kimi-K2-Instruct-0905
```

**Custom OpenAI-compatible**:
```bash
LLM_PROVIDER=openai
CUSTOM_OPENAI_API_KEY=your-key
CUSTOM_OPENAI_BASE_URL=https://api.openai.com/v1
CUSTOM_OPENAI_MODEL=gpt-4o
```

### Optional Features

| Variable | Description |
|----------|-------------|
| `TAVILY_API_KEY` | Enable web search |
| `GITHUB_TOKEN` | Enable GitHub integration |
| `AZURE_DEVOPS_ORG` / `AZURE_DEVOPS_PAT` | Enable Azure DevOps integration |
| `ANTHROPIC_API_KEY` | Enable Claude Code agent |
| `ENABLE_GRAPH_MEMORY=true` | Enable relationship tracking (Neo4j/Kuzu) |

## Memory System

Clara uses mem0 for persistent memory with vector search (pgvector/Qdrant) and optional graph storage (Neo4j/Kuzu).

### Bootstrap Profile Data

```bash
# Generate memory JSON (dry run)
poetry run python -m src.bootstrap_memory

# Apply to mem0
poetry run python -m src.bootstrap_memory --apply
```

### Clear Memory

```bash
poetry run python clear_dbs.py              # With prompt
poetry run python clear_dbs.py --yes        # Skip prompt
poetry run python clear_dbs.py --user <id>  # Specific user
```

## Production Deployment

### Railway

The repo includes `railway.toml` for one-click deployment:

1. Connect your GitHub repo to Railway
2. Set environment variables in Railway dashboard
3. Deploy

### Docker Compose with PostgreSQL

```bash
# Run with PostgreSQL databases
docker-compose --profile discord --profile postgres up
```

Set these for PostgreSQL:
```bash
DATABASE_URL=postgresql://user:pass@host:5432/clara_main
MEM0_DATABASE_URL=postgresql://user:pass@host:5432/clara_vectors
```

## Development

```bash
poetry run ruff check .    # Lint
poetry run ruff format .   # Format
poetry run pytest          # Test
```

## License

MIT
