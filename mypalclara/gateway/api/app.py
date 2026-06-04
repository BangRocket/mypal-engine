"""FastAPI app factory for the gateway HTTP API."""

from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from mypalclara.gateway.api.admin import router as admin_router
from mypalclara.gateway.api.backup import router as backup_router
from mypalclara.gateway.api.channels import router as channels_router
from mypalclara.gateway.api.chat import router as chat_router
from mypalclara.gateway.api.email_accounts import router as email_accounts_router
from mypalclara.gateway.api.game import router as game_router
from mypalclara.gateway.api.graph import router as graph_router
from mypalclara.gateway.api.guilds import router as guilds_router
from mypalclara.gateway.api.intentions import router as intentions_router
from mypalclara.gateway.api.mcp import router as mcp_router
from mypalclara.gateway.api.memories import router as memories_router
from mypalclara.gateway.api.memory_internal import router as memory_internal_router
from mypalclara.gateway.api.sandbox import router as sandbox_router
from mypalclara.gateway.api.sessions import router as sessions_router
from mypalclara.gateway.api.users import router as users_router


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="Clara Gateway API",
        description="HTTP API for Clara's memory, session, and user management",
        version="1.0.0",
        docs_url="/api/docs",
        redoc_url="/api/redoc",
        openapi_url="/api/openapi.json",
    )

    # CORS — allow the Rails app and any configured origins
    cors_origins = os.getenv(
        "GATEWAY_API_CORS_ORIGINS",
        "http://localhost:3000,http://localhost:5173,http://localhost:5180,"
        "http://127.0.0.1:5180,http://localhost:1420,tauri://localhost,https://tauri.localhost",
    ).split(",")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in cors_origins],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # OpenAI-compatible chat completions (for Clara desktop/web app)
    app.include_router(chat_router, prefix="/v1", tags=["chat"])

    # Mount all API routers under /api/v1/
    app.include_router(sessions_router, prefix="/api/v1/sessions", tags=["sessions"])
    app.include_router(memories_router, prefix="/api/v1/memories", tags=["memories"])
    app.include_router(graph_router, prefix="/api/v1/graph", tags=["graph"])
    app.include_router(intentions_router, prefix="/api/v1/intentions", tags=["intentions"])
    app.include_router(users_router, prefix="/api/v1/users", tags=["users"])
    app.include_router(admin_router, prefix="/api/v1/admin", tags=["admin"])
    app.include_router(game_router, prefix="/api/v1/game", tags=["game"])
    app.include_router(backup_router, prefix="/api/v1/backup", tags=["backup"])
    app.include_router(sandbox_router, prefix="/api/v1/sandbox", tags=["sandbox"])
    app.include_router(channels_router, prefix="/api/v1", tags=["channels"])
    app.include_router(guilds_router, prefix="/api/v1", tags=["guilds"])
    app.include_router(email_accounts_router, prefix="/api/v1", tags=["email-accounts"])
    app.include_router(mcp_router, prefix="/api/v1/mcp", tags=["mcp"])
    app.include_router(memory_internal_router, prefix="/api/v1/memory", tags=["memory-internal"])

    @app.get("/api/v1/health")
    async def health():
        return {"status": "ok", "service": "clara-gateway-api"}

    return app
