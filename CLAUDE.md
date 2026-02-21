# CLAUDE.md

Guidance for Claude Code working with this repository.

## Project Overview

MyPalClara is a personal AI assistant (Clara) with session management, persistent memory (Rook), and multi-platform support. Uses a gateway architecture with platform adapters for Discord, Teams, Slack, Telegram, Matrix, Signal, and WhatsApp.

## Quick Reference

```bash
# Development
poetry install                    # Install dependencies
poetry run ruff check . && poetry run ruff format .  # Lint + format

# Run gateway with all adapters
poetry run python -m mypalclara.gateway start

# Run specific adapter only
poetry run python -m mypalclara.gateway start --adapter discord

# Gateway daemon management
poetry run python -m mypalclara.gateway status
poetry run python -m mypalclara.gateway stop
poetry run python -m mypalclara.gateway restart

# Database
poetry run python scripts/migrate.py           # Run migrations
poetry run python scripts/migrate.py status    # Check status
poetry run python scripts/clear_dbs.py         # Clear memory data

# Web UI (Rails backend)
cd web-ui/backend && rails s -p 3000   # Start Rails API server

# Web UI (React frontend)
cd web-ui/frontend && npm run dev      # Start Vite dev server (port 5173)

# Docker
docker-compose --profile discord up
docker-compose --profile discord --profile postgres up

# Docker (unified web UI)
cd web-ui && docker build -t clara-web .  # Build unified image
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

### Core Structure
- `mypalclara/core/memory_manager.py` - Core orchestrator: session handling, Rook integration, prompt building with Clara's persona
- `mypalclara/core/llm/` - Unified LLM provider architecture (modular, supports OpenRouter, NanoGPT, OpenAI, Anthropic)
- `mypalclara/core/memory/` - Rook memory system (Qdrant/pgvector for vectors, OpenAI embeddings)
- `mypalclara/db/models.py` - SQLAlchemy models: Project, Session, Message, ChannelSummary
- `mypalclara/db/db.py` - Database setup (SQLite for dev, PostgreSQL for production)
- `mypalclara/core/email/` - Email monitoring and auto-response system
- `mypalclara/adapters/discord/` - Discord bot with multi-user support, reply chains, and streaming responses

### Directory Structure
| Directory | Purpose |
|-----------|---------|
| `mypalclara/` | Top-level Python package (all code lives here) |
| `mypalclara/gateway/` | WebSocket gateway for platform adapters |
| `mypalclara/adapters/` | Platform adapters (Discord, Teams, Slack, etc.) |
| `mypalclara/core/memory/` | Rook memory system (Qdrant/pgvector, embeddings) |
| `mypalclara/core/mcp/` | MCP plugin system (servers, tools, OAuth) |
| `mypalclara/core/email/` | Email monitoring and alerts |
| `mypalclara/core/core_tools/` | Tool implementations including MCP management |
| `mypalclara/db/` | Database models, migrations, connection |
| `mypalclara/sandbox/` | Code execution (Docker, Incus containers/VMs) |
| `mypalclara/tools/` | Tool loader infrastructure |
| `mypalclara/config/` | Configuration modules |
| `mypalclara/services/email/` | Email monitoring service |
| `mypalclara/services/backup/` | Automated database backup service |
| `mypalclara/services/proactive/` | Proactive messaging engine |
| `mypalclara/gateway/api/` | HTTP API endpoints (moved from web module) |
| `web-ui/backend/` | Rails API-only backend (game logic, gateway proxy) |
| `web-ui/frontend/` | React SPA (Vite, TypeScript, TailwindCSS) |

### Memory System (Rook)
- **User memories**: Persistent facts/preferences per user
- **Project memories**: Topic-specific context per project
- **Session context**: Recent 20 messages + last session snapshot
- **Session summary**: LLM-generated on timeout (30 min idle)

### Gateway System
WebSocket server for platform adapters with streaming support. Gateway now serves both WebSocket (port 18789) and HTTP API (port 18790).

```bash
poetry run python -m mypalclara.gateway --host 127.0.0.1 --port 18789
```

| Platform | Streaming | Notes |
|----------|-----------|-------|
| Discord | Yes | Message edits |
| Teams/Slack/Telegram/Matrix | Yes | 1s cooldown / rate limits |
| Signal/WhatsApp | No | APIs don't support editing |

### Web UI
Web UI uses Rails as BFF (backend-for-frontend). Rails handles game logic directly (own PostgreSQL) and proxies all other API requests to the Python gateway HTTP API (port 18790).

## Environment Variables

### Required
```bash
OPENAI_API_KEY=...          # Always required for embeddings
LLM_PROVIDER=anthropic      # openrouter, nanogpt, openai, or anthropic
```

### Chat LLM Providers

**OpenRouter** (`LLM_PROVIDER=openrouter`):
- `OPENROUTER_API_KEY` - API key
- `OPENROUTER_MODEL` - Chat model (default: anthropic/claude-sonnet-4)
- `OPENROUTER_SITE` / `OPENROUTER_TITLE` - Optional headers

**NanoGPT** (`LLM_PROVIDER=nanogpt`):
- `NANOGPT_API_KEY` - API key
- `NANOGPT_MODEL` - Chat model (default: moonshotai/Kimi-K2-Instruct-0905)

**Custom OpenAI** (`LLM_PROVIDER=openai`):
- `CUSTOM_OPENAI_API_KEY` - API key for LLM (separate from embeddings)
- `CUSTOM_OPENAI_BASE_URL` - Base URL (default: https://api.openai.com/v1)
- `CUSTOM_OPENAI_MODEL` - Chat model (default: gpt-4o)

**Anthropic** (`LLM_PROVIDER=anthropic`):
- `ANTHROPIC_API_KEY` - Anthropic API key
- `ANTHROPIC_BASE_URL` - Custom base URL for proxies like clewdr (optional)
- `ANTHROPIC_MODEL` - Chat model (default: claude-sonnet-4-5)

Uses native Anthropic SDK with native tool calling. Recommended for Claude proxies like clewdr.

**Amazon Bedrock** (`LLM_PROVIDER=bedrock`):
- `AWS_REGION` - AWS region (default: us-east-1)
- `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` - AWS credentials (or use IAM role)
- `BEDROCK_MODEL` - Bedrock model ID (default: anthropic.claude-3-5-sonnet-20241022-v2:0)
- `BEDROCK_MODEL_{HIGH,MID,LOW}` - Tier-specific model overrides

Uses Claude models via Amazon Bedrock. Requires `langchain-aws` package: `pip install langchain-aws`.
Supports IAM role authentication when running on AWS (EC2, Lambda, ECS).

**Azure OpenAI** (`LLM_PROVIDER=azure`):
- `AZURE_OPENAI_ENDPOINT` - Azure OpenAI endpoint URL (required)
- `AZURE_OPENAI_API_KEY` - Azure API key (required)
- `AZURE_DEPLOYMENT_NAME` - Deployment name in Azure (required)
- `AZURE_API_VERSION` - API version (default: 2024-02-15-preview)
- `AZURE_MODEL` - Model reference (default: gpt-4o)
- `AZURE_MODEL_{HIGH,MID,LOW}` - Tier-specific model overrides

Uses Azure OpenAI Service. Deployment names map to models configured in Azure Portal.

### Model Tiers (Discord Bot)
The Discord bot supports dynamic model selection via message prefixes:
- `!high` or `!opus` → High tier (Opus-class, most capable)
- `!mid` or `!sonnet` → Mid tier (Sonnet-class, balanced) - default
- `!low`, `!haiku`, or `!fast` → Low tier (Haiku-class, fast/cheap)

Optional tier-specific model overrides:
- `OPENROUTER_MODEL_HIGH`, `OPENROUTER_MODEL_MID`, `OPENROUTER_MODEL_LOW`
- `NANOGPT_MODEL_HIGH`, `NANOGPT_MODEL_MID`, `NANOGPT_MODEL_LOW`
- `CUSTOM_OPENAI_MODEL_HIGH`, `CUSTOM_OPENAI_MODEL_MID`, `CUSTOM_OPENAI_MODEL_LOW`
- `ANTHROPIC_MODEL_HIGH`, `ANTHROPIC_MODEL_MID`, `ANTHROPIC_MODEL_LOW`
- `MODEL_TIER` - Default tier when not specified. If not set, uses the base model (e.g., `ANTHROPIC_MODEL`). Set to "high", "mid", or "low" to enable tier-based defaults.
- `AUTO_TIER_SELECTION` - Enable automatic tier selection based on message complexity (default: false)

**Auto Tier Selection:**
When `AUTO_TIER_SELECTION=true`, Clara uses the fast/low model to classify message complexity and automatically selects the appropriate tier:
- LOW = Simple greetings, quick facts, basic questions, casual chat
- MID = Moderate tasks, explanations, summaries, most coding questions
- HIGH = Complex reasoning, long-form writing, difficult coding, multi-step analysis

The classifier considers recent conversation history (up to 4 messages) when making its decision. This prevents short replies like "yes" or "ok" from dropping to a lower tier when they're part of a complex ongoing discussion.

Example usage in Discord: `!high What is quantum entanglement?`

### Cloudflare Access (for endpoints behind cloudflared)
For custom OpenAI endpoints behind Cloudflare Access (like cloudflared tunnels):
- `CF_ACCESS_CLIENT_ID` - Cloudflare Access Service Token client ID
- `CF_ACCESS_CLIENT_SECRET` - Cloudflare Access Service Token client secret

### Rook Provider (independent from chat LLM)
Environment variables use `ROOK_*` prefix with `MEM0_*` fallback for backward compatibility.

- `ROOK_PROVIDER` - Provider for memory extraction: "openrouter" (default), "nanogpt", "openai", or "anthropic"
- `ROOK_MODEL` - Model for memory extraction (default: openai/gpt-4o-mini)
- `ROOK_API_KEY` - Optional: override the provider's default API key
- `ROOK_BASE_URL` - Optional: override the provider's default base URL

Note: For `ROOK_PROVIDER=anthropic`, uses native Anthropic SDK with `anthropic_base_url` support for proxies.

### Optional
- `USER_ID` - Single-user identifier (default: "demo-user")
- `DEFAULT_PROJECT` - Default project name (default: "Default Project")
- `SKIP_PROFILE_LOAD` - Skip initial Rook profile loading (default: true)
- `ENABLE_GRAPH_MEMORY` - Enable graph memory for relationship tracking (default: false)
- `GRAPH_STORE_PROVIDER` - Graph store provider: "falkordb" (default)
- `FALKORDB_HOST` - FalkorDB host (default: localhost)
- `FALKORDB_PORT` - FalkorDB port (default: 6379)
- `FALKORDB_PASSWORD` - FalkorDB password (optional)
- `FALKORDB_GRAPH_NAME` - Graph name (default: clara)

### PostgreSQL (Production)
For production, use managed PostgreSQL instead of SQLite/Qdrant:
- `DATABASE_URL` - PostgreSQL connection for SQLAlchemy (default: uses SQLite)
- `ROOK_DATABASE_URL` - PostgreSQL+pgvector connection for Rook vectors (default: uses Qdrant)

Example (Railway):
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

**For Claude proxies (like clewdr)**: Use `LLM_PROVIDER=anthropic` with `ANTHROPIC_BASE_URL` for native Anthropic SDK support. This uses native Claude tool calling without format conversion.

**Tool Communication Mode:**
- `TOOL_CALL_MODE` - How tools are communicated to the LLM:
  - `langchain` (default): Uses LangChain's `bind_tools()` for unified tool calling across all providers
  - `native`: Uses API-native tool calling (OpenAI/Anthropic format) directly
  - `xml`: OpenClaw-style system prompt injection (works with any LLM, no native tool support needed)

When using `TOOL_CALL_MODE=xml`:
- Tools are serialized to XML and injected into the system prompt
- Works with any LLM provider (doesn't require native tool support)
- Function calls are parsed from the LLM response text using XML tags
- Format: `<function_calls><invoke name="tool_name"><parameter name="arg">value</parameter></invoke></function_calls>`

**Unified Tool Calling (Recommended for New Code):**
The `make_llm_with_tools_unified()` function provides a standardized interface for all providers:

```python
from mypalclara.core import make_llm_with_tools_unified, ToolResponse

# Create unified tool-calling LLM
llm = make_llm_with_tools_unified(tools, tier="mid")

# Call returns standardized ToolResponse
response: ToolResponse = llm(messages)

if response.has_tool_calls:
    for call in response.tool_calls:
        print(f"Tool: {call.name}, Args: {call.arguments}")
else:
    print(response.content)

# Convert to OpenAI dict format if needed
openai_dict = response.to_openai_dict()
```

Benefits:
- Works with all providers (OpenRouter, NanoGPT, OpenAI, Anthropic)
- Returns standardized `ToolResponse` object (not provider-specific types)
- No provider-specific branching in calling code
- Handles format conversion internally

To enable Docker sandbox + web search:
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
CLARA_GATEWAY_API_PORT=18790          # Gateway HTTP API port (default: 18790)
CLARA_GATEWAY_API_URL=http://127.0.0.1:18790  # Gateway HTTP API URL (for Rails proxy)
```

### Optional Features
```bash
ENABLE_GRAPH_MEMORY=false         # FalkorDB relationship tracking
GITHUB_TOKEN=...                  # GitHub integration
EMAIL_MONITORING_ENABLED=false    # Email alerts
TAVILY_API_KEY=...                # Web search in sandbox
```

## Key Patterns

- Discord bot uses global `MemoryManager` instance initialized at startup with LLM callable
- **Unified LLM architecture** in `mypalclara/core/llm/`:
  - `LLMConfig` dataclass for configuration (supports tiers, env loading)
  - `LLMProvider` abstract interface with `LangChainProvider`, `DirectAnthropicProvider`, `DirectOpenAIProvider`
  - `ProviderRegistry` for provider caching and factory pattern
  - `ToolCall`/`ToolResponse` dataclasses for standardized tool handling
  - Backward compatibility via `make_llm()`, `make_llm_streaming()`, etc.
- `LLM_PROVIDER=anthropic` uses native Anthropic SDK with native tool calling (recommended for clewdr)
- Sandbox system auto-selects between Docker and Incus based on availability
- Rook (Clara's memory system) is in `mypalclara/core/memory/`

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
Automated PostgreSQL backups to S3 (Wasabi). Located in `mypalclara/services/backup/`.

```bash
S3_BUCKET=clara-backups
S3_ENDPOINT_URL=https://s3.wasabisys.com
S3_ACCESS_KEY=...
S3_SECRET_KEY=...
```
