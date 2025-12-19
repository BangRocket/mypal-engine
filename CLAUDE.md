# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

MyPalClara is a personal AI assistant with session management and persistent memory (via mem0). The assistant's name is Clara. It uses a FastAPI backend with SQLite storage and a Next.js frontend built with assistant-ui.

## Development Commands

### Backend (Python/FastAPI)
```bash
poetry install                    # Install dependencies
poetry run python api.py          # Run API server (port 8000)
poetry run python app.py          # Run Gradio UI (port 7860)
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
- `memory_manager.py` - Core orchestrator: session handling, mem0 integration, prompt building with Clara's persona
- `llm_backends.py` - LLM provider abstraction (OpenRouter, NanoGPT, custom OpenAI) - both streaming and non-streaming
- `mem0_config.py` - mem0 memory system configuration (Qdrant vector store, OpenAI embeddings)
- `models.py` - SQLAlchemy models: Project, Session, Message
- `db.py` - SQLite database setup

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

## Key Patterns

- Backend uses global `MemoryManager` instance initialized at startup with LLM callable
- Frontend uses assistant-ui's `RemoteThreadListAdapter` and `ThreadHistoryAdapter` for thread persistence
- All LLM backends use OpenAI-compatible API (OpenAI SDK on backend, AI SDK on frontend)
- Thread adapter uses empty `BACKEND_URL` to leverage Next.js rewrites for CORS-free backend access
