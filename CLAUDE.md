# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Clara is a personal AI assistant with session management, persistent memory (via mem0), and multi-interface support (web UI, Gradio, Telegram). It uses a FastAPI backend with SQLite storage and a Next.js frontend built with assistant-ui.

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
docker-compose up                 # Run backend + frontend separately
docker-compose -f docker-compose.combined.yml up  # Combined single-container
```

## Architecture

### Backend Structure
- `api.py` - FastAPI server with thread management, chat, and memory endpoints
- `memory_manager.py` - Core orchestrator: session handling, mem0 integration, prompt building
- `llm_backends.py` - LLM provider abstraction (OpenRouter, NanoGPT, HuggingFace)
- `mem0_config.py` - mem0 memory system configuration (Qdrant vector store, OpenAI embeddings)
- `models.py` - SQLAlchemy models: Project, Session, Message
- `db.py` - SQLite database setup
- `telegram_bot.py` - Telegram integration sharing the "General Chat" thread

### Frontend Structure
- `frontend/app/api/chat/route.ts` - Next.js API route proxying to backend with LLM streaming
- `frontend/lib/thread-adapter.ts` - Thread list and history adapters for assistant-ui
- `frontend/components/assistant-ui/` - Chat UI components built on assistant-ui

### Data Flow
1. Frontend sends chat request to `/api/chat` route
2. Route fetches enriched context from backend `/api/context` (includes mem0 memories)
3. Route streams LLM response directly to frontend
4. On completion, stores messages via backend `/api/store` (triggers mem0 memory extraction)

### Memory System
- **User memories**: Persistent facts/preferences per user (stored in mem0)
- **Project memories**: Topic-specific context per project
- **Session context**: Recent messages + summary from previous session
- Sessions auto-timeout after 30 minutes of inactivity

## Environment Variables

Required:
- `OPENROUTER_API_KEY` or `NANOGPT_API_KEY` - LLM provider
- `OPENAI_API_KEY` - For mem0 embeddings (text-embedding-3-small)

Optional:
- `LLM_PROVIDER` - "openrouter" (default), "nanogpt", or "huggingface"
- `OPENROUTER_MODEL` / `NANOGPT_MODEL` - Model selection
- `TELEGRAM_BOT_TOKEN` - Enable Telegram integration
- `TELEGRAM_ALLOWED_USERS` - Comma-separated user IDs/usernames
- `USER_ID` - Single-user identifier (default: "demo-user")
- `DATA_DIR` - Persistent data directory for Docker
- `SKIP_PROFILE_LOAD` - Skip initial mem0 profile loading (default: true)

## Key Patterns

- Backend uses global `MemoryManager` instance initialized at startup
- Frontend uses assistant-ui's `RemoteThreadListAdapter` and `ThreadHistoryAdapter`
- Telegram bot runs as daemon thread inside API server, shares "General Chat" thread
- Messages track `source` field ("web" or "telegram") for cross-platform sync
