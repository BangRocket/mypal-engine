# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

MyPalClara is a personal AI assistant with session management and persistent memory (via mem0). The assistant's name is Clara. It uses a FastAPI backend with SQLite/PostgreSQL storage and a Next.js frontend built with assistant-ui.

## Development Commands

### Backend (Python/FastAPI)
```bash
poetry install                    # Install dependencies
poetry run python api.py          # Run API server (port 8000)
poetry run python app.py          # Run Gradio UI (port 7860)
poetry run python discord_bot.py  # Run Discord bot
poetry run pytest                 # Run tests
poetry run ruff check .           # Lint
poetry run ruff format .          # Format
```

### Frontend (Next.js)
```bash
cd frontend
npm install                       # Install dependencies
npm run dev                       # Run dev server (port 3000, uses Turbopack)
npm run build                     # Production build
npm run lint                      # ESLint
npm run prettier:fix              # Format with Prettier
```

### Docker
```bash
docker-compose up                 # Run backend (port 8000) + frontend (port 3000)
```

## Architecture

### Backend Structure
- `api.py` - FastAPI server with thread management, chat, and memory endpoints
- `discord_bot.py` - Discord bot with multi-user support, reply chains, and streaming responses
- `memory_manager.py` - Core orchestrator: session handling, mem0 integration, prompt building with Clara's persona
- `llm_backends.py` - LLM provider abstraction (OpenRouter, NanoGPT, custom OpenAI) - both streaming and non-streaming
- `mem0_config.py` - mem0 memory system configuration (Qdrant/pgvector for vectors, OpenAI embeddings)
- `models.py` - SQLAlchemy models: Project, Session, Message, ChannelSummary
- `db.py` - Database setup (SQLite for dev, PostgreSQL for production)

### Frontend Structure
- `frontend/app/api/chat/route.ts` - Next.js API route that fetches context from backend, streams LLM response via AI SDK
- `frontend/lib/thread-adapter.ts` - RemoteThreadListAdapter and ThreadHistoryAdapter for assistant-ui thread management
- `frontend/components/assistant-ui/` - Chat UI components built on assistant-ui
- `frontend/app/assistant.tsx` - Main assistant component with runtime provider and adapters

### Data Flow
1. Frontend sends chat request to `/api/chat` route with messages
2. Route fetches enriched context from backend `/api/context` (includes mem0 memories, session context, Clara persona)
3. Route streams LLM response directly to frontend using AI SDK's `streamText`
4. On completion, stores messages via backend `/api/store` (triggers mem0 memory extraction)

### Memory System
- **User memories**: Persistent facts/preferences per user (stored in mem0, searched via `_fetch_mem0_context`)
- **Project memories**: Topic-specific context per project (filtered by project_id in mem0)
- **Graph memories**: Optional relationship tracking via Neo4j or Kuzu (disabled by default, enable with `ENABLE_GRAPH_MEMORY=true`)
- **Session context**: Recent 20 messages + snapshot of last 10 messages from previous session
- **Session summary**: LLM-generated summary stored when session times out
- Sessions auto-timeout after 30 minutes of inactivity (`SESSION_IDLE_MINUTES`)

### Thread Management
Backend provides full CRUD for threads via `/api/threads` endpoints:
- List, create, rename, archive, unarchive, delete threads
- Get/append messages per thread
- Generate titles via LLM (`/api/threads/{id}/generate-title`)

## Environment Variables

### Required
- `OPENAI_API_KEY` - Always required for mem0 embeddings (text-embedding-3-small)
- `LLM_PROVIDER` - Chat LLM provider: "openrouter" (default), "nanogpt", or "openai"

### Chat LLM Providers (based on LLM_PROVIDER)

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

### Mem0 Provider (independent from chat LLM)
- `MEM0_PROVIDER` - Provider for memory extraction: "openrouter" (default), "nanogpt", or "openai"
- `MEM0_MODEL` - Model for memory extraction (default: openai/gpt-4o-mini)
- `MEM0_API_KEY` - Optional: override the provider's default API key
- `MEM0_BASE_URL` - Optional: override the provider's default base URL

### Optional
- `USER_ID` - Single-user identifier (default: "demo-user")
- `DEFAULT_PROJECT` - Default project name (default: "Default Project")
- `BACKEND_URL` - Backend URL for frontend (default: http://localhost:8000)
- `SKIP_PROFILE_LOAD` - Skip initial mem0 profile loading (default: true)
- `ENABLE_GRAPH_MEMORY` - Enable graph memory for relationship tracking (default: false)
- `GRAPH_STORE_PROVIDER` - Graph store provider: "neo4j" (default) or "kuzu" (embedded)
- `NEO4J_URL`, `NEO4J_USERNAME`, `NEO4J_PASSWORD` - Neo4j connection (when using neo4j provider)

### PostgreSQL (Production)
For production, use managed PostgreSQL instead of SQLite/Qdrant:
- `DATABASE_URL` - PostgreSQL connection for SQLAlchemy (default: uses SQLite)
- `MEM0_DATABASE_URL` - PostgreSQL+pgvector connection for mem0 vectors (default: uses Qdrant)

Example (Railway):
```bash
DATABASE_URL=postgresql://user:pass@host:5432/clara_main
MEM0_DATABASE_URL=postgresql://user:pass@host:5432/clara_vectors
```

To migrate existing data:
```bash
poetry run python scripts/migrate_to_postgres.py --all
```

### Discord Bot
- `DISCORD_BOT_TOKEN` - Discord bot token (required for Discord integration)
- `DISCORD_CLIENT_ID` - Client ID for invite link generation
- `DISCORD_ALLOWED_CHANNELS` - Comma-separated channel IDs to restrict bot (optional)
- `DISCORD_ALLOWED_ROLES` - Comma-separated role IDs for access control (optional)
- `DISCORD_MAX_MESSAGES` - Max messages in conversation chain (default: 25)
- `DISCORD_SUMMARY_AGE_MINUTES` - Messages older than this are summarized (default: 30)
- `DISCORD_CHANNEL_HISTORY_LIMIT` - Max messages to fetch from channel (default: 50)
- `DISCORD_MONITOR_PORT` - Monitor dashboard port (default: 8001)
- `DISCORD_MONITOR_ENABLED` - Enable monitor dashboard (default: true)

### Docker Code Execution (Discord Bot)
Tool calling requires Docker and a tool-capable LLM:
- `DOCKER_SANDBOX_IMAGE` - Docker image for sandbox (default: python:3.12-slim)
- `DOCKER_SANDBOX_TIMEOUT` - Container idle timeout in seconds (default: 900)
- `DOCKER_SANDBOX_MEMORY` - Memory limit per container (default: 512m)
- `DOCKER_SANDBOX_CPU` - CPU limit per container (default: 1.0)
- `TAVILY_API_KEY` - Tavily API key for web search (optional but recommended)

### Tool Calling LLM
By default, tool calling uses the **same endpoint and model as your main chat LLM**. This means if you're using a custom endpoint (like clewdr), tool calls go through it too.

Optional overrides:
- `TOOL_API_KEY` - Override API key for tool calls
- `TOOL_BASE_URL` - Override base URL for tool calls
- `TOOL_MODEL` - Override model for tool calls
- `TOOL_FORMAT` - Tool definition format: `openai` (default) or `claude`

**For Claude proxies (like clewdr)**: Set `TOOL_FORMAT=claude` to convert tool definitions to Claude's format.

To enable Docker sandbox + web search:
```bash
# Install Docker and start the daemon
docker --version  # Verify Docker is installed

# Set web search API key (optional)
export TAVILY_API_KEY="your-tavily-key"
```

### Local File Storage (Discord Bot)
Clara can save files locally that persist across sessions:
- `CLARA_FILES_DIR` - Directory for local file storage (default: ./clara_files)
- `CLARA_MAX_FILE_SIZE` - Max file size in bytes (default: 50MB)

Files are organized per-user. Discord attachments are automatically saved locally.

**Local File Tools** (always available, even without Docker):
- `save_to_local` - Save content to local storage
- `list_local_files` - List saved files
- `read_local_file` - Read a saved file
- `delete_local_file` - Delete a saved file
- `download_from_sandbox` - Copy Docker sandbox file to local storage
- `upload_to_sandbox` - Upload local file to Docker sandbox
- `send_local_file` - Send a saved file to Discord chat

## Key Patterns

- Backend uses global `MemoryManager` instance initialized at startup with LLM callable
- Frontend uses assistant-ui's `RemoteThreadListAdapter` and `ThreadHistoryAdapter` for thread persistence
- All LLM backends use OpenAI-compatible API (OpenAI SDK on backend, AI SDK on frontend)
- Thread adapter uses empty `BACKEND_URL` to leverage Next.js rewrites for CORS-free backend access

## Railway Deployment

### Discord Bot on Railway

1. **Create PostgreSQL databases** (two instances):
   - `clara-main` - For SQLAlchemy data (sessions, messages, etc.)
   - `clara-vectors` - For mem0 vectors (enable pgvector extension)

2. **Enable pgvector** on the vectors database:
   ```sql
   CREATE EXTENSION IF NOT EXISTS vector;
   ```

3. **Create Discord bot service**:
   - Connect your GitHub repo to Railway
   - Railway auto-detects `railway.toml` and uses `Dockerfile.discord`

4. **Set environment variables** in Railway dashboard:
   ```
   # Required
   DISCORD_BOT_TOKEN=your-bot-token
   OPENAI_API_KEY=sk-proj-...
   DATABASE_URL=${{Postgres.DATABASE_URL}}  # Railway variable reference
   MEM0_DATABASE_URL=${{PostgresVectors.DATABASE_URL}}

   # LLM Provider
   LLM_PROVIDER=openrouter
   OPENROUTER_API_KEY=sk-or-...
   OPENROUTER_MODEL=anthropic/claude-sonnet-4

   # Mem0 Provider
   MEM0_PROVIDER=openrouter
   MEM0_MODEL=openai/gpt-4o-mini

   # Optional
   TAVILY_API_KEY=tvly-...  # For web search
   ENABLE_GRAPH_MEMORY=false
   ```

5. **Migrate existing data** (if any):
   ```bash
   # Set env vars locally pointing to Railway PostgreSQL
   export DATABASE_URL=postgresql://...
   export MEM0_DATABASE_URL=postgresql://...
   poetry run python scripts/migrate_to_postgres.py --all
   ```

### Files for Railway

| File | Purpose |
|------|---------|
| `Dockerfile.discord` | Discord bot container |
| `railway.toml` | Railway service configuration |
| `Dockerfile` | Backend API container (for separate API service) |

### Limitations on Railway

**Docker Sandbox**: The Docker code execution sandbox (`execute_python`, `run_shell`, etc.) will NOT work on Railway because Docker-in-Docker is not supported. The bot will still function for:
- Chat conversations with memory
- Web search (if `TAVILY_API_KEY` is set)
- Local file storage tools

For full sandbox support, self-host on a VPS with Docker installed.
