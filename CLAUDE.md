# CLAUDE.md

Guidance for Claude Code working with this repository.

## Project Overview

MyPalClara is a personal AI assistant (Clara) with session management, persistent memory (Rook), and multi-platform support. Primary interface is Discord, with gateway support for Teams, Slack, Telegram, Matrix, Signal, and WhatsApp.

## Quick Reference

```bash
# Development
poetry install                    # Install dependencies
poetry run python discord_bot.py  # Run Discord bot
poetry run ruff check . && poetry run ruff format .  # Lint + format

# Daemon mode (Unix)
poetry run python discord_bot.py --daemon
poetry run python discord_bot.py --status
poetry run python discord_bot.py --stop

# Docker
docker-compose --profile discord up
docker-compose --profile discord --profile postgres up

# Database
poetry run python scripts/migrate.py           # Run migrations
poetry run python scripts/migrate.py status    # Check status
poetry run python clear_dbs.py                 # Clear memory data
```

## Versioning

CalVer: `YYYY.WW.N` (Year.Week.Build). Auto-bumped via git hook for significant commits.

**Bumps version:** `feat:`, `fix:`, `perf:`, `breaking:`
**No bump:** `chore:`, `docs:`, `style:`, `refactor:`, `test:`, `ci:`, `build:`
**Override:** `[bump]` forces bump, `[skip-version]` skips bump

```bash
git config core.hooksPath .githooks  # Enable hooks (run once)
```

## Architecture

### Core Files
| File | Purpose |
|------|---------|
| `discord_bot.py` | Discord bot with streaming, reply chains, image support |
| `memory_manager.py` | Session handling, Rook integration, prompt building |
| `llm_backends.py` | LLM abstraction (OpenRouter, NanoGPT, OpenAI, Anthropic) |
| `models.py` | SQLAlchemy models (Project, Session, Message, etc.) |
| `db.py` | Database setup (SQLite dev, PostgreSQL production) |

### Subsystems
| Directory | Purpose |
|-----------|---------|
| `clara_core/memory/` | Rook memory system (Qdrant/pgvector, embeddings) |
| `clara_core/mcp/` | MCP plugin system (servers, tools, OAuth) |
| `clara_core/email/` | Email monitoring and alerts |
| `clara_core/core_tools/` | Tool implementations including MCP management |
| `gateway/` | WebSocket gateway for platform adapters |
| `adapters/` | Platform adapters (Discord, Teams, Slack, etc.) |
| `sandbox/` | Code execution (Docker, Incus containers/VMs) |
| `storage/` | Local file storage |

### Memory System (Rook)
- **User memories**: Persistent facts/preferences per user
- **Project memories**: Topic-specific context per project
- **Session context**: Recent 20 messages + last session snapshot
- **Session summary**: LLM-generated on timeout (30 min idle)

### Gateway System
WebSocket server for platform adapters with streaming support.

```bash
poetry run python -m gateway --host 127.0.0.1 --port 18789
```

| Platform | Streaming | Notes |
|----------|-----------|-------|
| Discord | Yes | Message edits |
| Teams/Slack/Telegram/Matrix | Yes | 1s cooldown / rate limits |
| Signal/WhatsApp | No | APIs don't support editing |

## Environment Variables

### Required
```bash
OPENAI_API_KEY=...          # Always required for embeddings
LLM_PROVIDER=anthropic      # openrouter, nanogpt, openai, or anthropic
```

### Chat LLM Providers

**Anthropic** (recommended for Claude proxies):
```bash
ANTHROPIC_API_KEY=...
ANTHROPIC_BASE_URL=...      # Optional: for proxies like clewdr
ANTHROPIC_MODEL=claude-sonnet-4-5
```

**OpenRouter**:
```bash
OPENROUTER_API_KEY=...
OPENROUTER_MODEL=anthropic/claude-sonnet-4
```

**Custom OpenAI**:
```bash
CUSTOM_OPENAI_API_KEY=...
CUSTOM_OPENAI_BASE_URL=https://api.openai.com/v1
CUSTOM_OPENAI_MODEL=gpt-4o
```

### Model Tiers
Discord supports `!high`, `!mid`, `!low` prefixes for model selection. Configure with:
```bash
ANTHROPIC_MODEL_HIGH=claude-opus-4-5
ANTHROPIC_MODEL_MID=claude-sonnet-4-5
ANTHROPIC_MODEL_LOW=claude-haiku-4-5
MODEL_TIER=mid                    # Default tier
AUTO_TIER_SELECTION=false         # Auto-select based on complexity
```

### Database
```bash
DATABASE_URL=postgresql://...     # Main database (default: SQLite)
ROOK_DATABASE_URL=postgresql://...  # Vector store (default: Qdrant)
```

### Discord Bot
```bash
DISCORD_BOT_TOKEN=...
DISCORD_ALLOWED_SERVERS=...       # Comma-separated server IDs
DISCORD_ALLOWED_CHANNELS=...      # Comma-separated channel IDs
DISCORD_MAX_MESSAGES=25           # Max conversation chain length
DISCORD_STOP_PHRASES="clara stop,stop clara,nevermind"
```

### Rook Memory Provider
```bash
ROOK_PROVIDER=openrouter          # Memory extraction LLM
ROOK_MODEL=openai/gpt-4o-mini
```

### Sandbox
```bash
SANDBOX_MODE=auto                 # docker, incus, incus-vm, or auto
DOCKER_SANDBOX_IMAGE=python:3.12-slim
DOCKER_SANDBOX_TIMEOUT=900
```

### MCP Plugins
```bash
MCP_SERVERS_DIR=.mcp_servers
SMITHERY_API_KEY=...              # For Smithery registry access
```

### Gateway
```bash
CLARA_GATEWAY_HOST=127.0.0.1
CLARA_GATEWAY_PORT=18789
CLARA_GATEWAY_SECRET=...          # Optional auth secret
```

### Optional Features
```bash
ENABLE_GRAPH_MEMORY=false         # Neo4j/Kuzu relationship tracking
GITHUB_TOKEN=...                  # GitHub integration
EMAIL_MONITORING_ENABLED=false    # Email alerts
TAVILY_API_KEY=...                # Web search in sandbox
```

## Key Patterns

- Global `MemoryManager` instance initialized at bot startup
- `LLM_PROVIDER=anthropic` uses native SDK with native tool calling
- Sandbox auto-selects Docker or Incus based on availability
- MCP tools use namespaced names: `{server}__{tool}`

## Production Deployment

```bash
# PostgreSQL setup
DATABASE_URL=postgresql://user:pass@host:5432/clara_main
ROOK_DATABASE_URL=postgresql://user:pass@host:5432/clara_vectors

# Enable pgvector
CREATE EXTENSION IF NOT EXISTS vector;

# Migrate existing data
poetry run python scripts/migrate_to_postgres.py --all
```

### Backup Service
Automated PostgreSQL backups to S3 (Wasabi). Located in `backup_service/`.

```bash
S3_BUCKET=clara-backups
S3_ENDPOINT_URL=https://s3.wasabisys.com
S3_ACCESS_KEY=...
S3_SECRET_KEY=...
```
