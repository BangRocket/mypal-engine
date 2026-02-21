"""FastAPI app factory for the gateway HTTP API."""

from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from mypalclara.gateway.api.admin import router as admin_router
from mypalclara.gateway.api.game import router as game_router
from mypalclara.gateway.api.graph import router as graph_router
from mypalclara.gateway.api.intentions import router as intentions_router
from mypalclara.gateway.api.memories import router as memories_router
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
    cors_origins = os.getenv("GATEWAY_API_CORS_ORIGINS", "http://localhost:3000,http://localhost:5173").split(",")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in cors_origins],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Mount all API routers under /api/v1/
    app.include_router(sessions_router, prefix="/api/v1/sessions", tags=["sessions"])
    app.include_router(memories_router, prefix="/api/v1/memories", tags=["memories"])
    app.include_router(graph_router, prefix="/api/v1/graph", tags=["graph"])
    app.include_router(intentions_router, prefix="/api/v1/intentions", tags=["intentions"])
    app.include_router(users_router, prefix="/api/v1/users", tags=["users"])
    app.include_router(admin_router, prefix="/api/v1/admin", tags=["admin"])
    app.include_router(game_router, prefix="/api/v1/game", tags=["game"])

    @app.get("/api/v1/health")
    async def health():
        return {"status": "ok", "service": "clara-gateway-api"}

    return app
