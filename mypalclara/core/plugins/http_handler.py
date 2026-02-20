"""HTTP tool invocation handler.

This module provides FastAPI routes for HTTP-based tool invocation.
Can be mounted in an existing FastAPI app or run standalone.

Usage with existing app:
    from clara_core.plugins.http_handler import create_tool_router
    app.include_router(create_tool_router(), prefix="/api/tools")

Standalone usage:
    from clara_core.plugins.http_handler import create_app
    import uvicorn
    uvicorn.run(create_app(), host="0.0.0.0", port=8000)
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class ToolInvocationRequest(BaseModel):
    """Request body for tool invocation."""

    arguments: dict[str, Any] = Field(default_factory=dict, description="Tool arguments")
    user_id: str | None = Field(default=None, description="User ID for context")
    platform: str = Field(default="api", description="Platform identifier")
    session_key: str | None = Field(default=None, description="Session key")


class ToolInvocationResponse(BaseModel):
    """Response from tool invocation."""

    success: bool = Field(description="Whether the tool executed successfully")
    result: str | None = Field(default=None, description="Tool execution result")
    error: str | None = Field(default=None, description="Error message if failed")
    tool_name: str = Field(description="Canonical name of the tool that was invoked")


class ToolDefinitionResponse(BaseModel):
    """Tool definition for API response."""

    name: str
    description: str
    parameters: dict[str, Any]
    platforms: list[str] | None = None
    requires: list[str] = Field(default_factory=list)


class ToolListResponse(BaseModel):
    """Response listing available tools."""

    tools: list[ToolDefinitionResponse]
    total: int


class GroupListResponse(BaseModel):
    """Response listing tool groups."""

    groups: dict[str, list[str]]


def create_tool_router(prefix: str = ""):
    """Create a FastAPI router for tool operations.

    Args:
        prefix: URL prefix for routes (usually empty as prefix is set at include_router)

    Returns:
        FastAPI APIRouter with tool routes
    """
    try:
        from fastapi import APIRouter, HTTPException, Query
    except ImportError:
        raise ImportError("FastAPI is required for HTTP tool handler. " "Install it with: pip install fastapi")

    router = APIRouter(tags=["tools"])

    @router.get("/", response_model=ToolListResponse)
    async def list_tools(
        platform: str | None = Query(None, description="Filter by platform"),
        group: str | None = Query(None, description="Filter by tool group"),
    ) -> ToolListResponse:
        """List all available tools.

        Optional filters:
        - platform: Only show tools available on this platform
        - group: Only show tools in this group (e.g., "group:memory")
        """
        from . import get_policy_engine, get_registry

        registry = get_registry()
        policy_engine = get_policy_engine()

        # Get all tools
        all_tools = registry.get_tools(platform=platform, format="raw")

        # Filter by group if specified
        if group:
            group_tools = policy_engine.get_tools_in_group(group)
            all_tools = [t for t in all_tools if t.name in group_tools]

        tools = [
            ToolDefinitionResponse(
                name=t.name,
                description=t.description,
                parameters=t.parameters,
                platforms=t.platforms,
                requires=t.requires,
            )
            for t in all_tools
        ]

        return ToolListResponse(tools=tools, total=len(tools))

    @router.get("/groups", response_model=GroupListResponse)
    async def list_groups() -> GroupListResponse:
        """List all tool groups and their members."""
        from . import get_policy_engine

        policy_engine = get_policy_engine()
        return GroupListResponse(groups=policy_engine.get_groups())

    @router.get("/{tool_name}", response_model=ToolDefinitionResponse)
    async def get_tool(tool_name: str) -> ToolDefinitionResponse:
        """Get a specific tool's definition.

        Supports alias resolution - if tool_name is an alias,
        returns the canonical tool definition.
        """
        from . import get_registry

        registry = get_registry()
        tool = registry.get_tool(tool_name)

        if not tool:
            raise HTTPException(
                status_code=404,
                detail=f"Tool '{tool_name}' not found",
            )

        return ToolDefinitionResponse(
            name=tool.name,
            description=tool.description,
            parameters=tool.parameters,
            platforms=tool.platforms,
            requires=tool.requires,
        )

    @router.post("/{tool_name}", response_model=ToolInvocationResponse)
    async def invoke_tool(
        tool_name: str,
        request: ToolInvocationRequest,
    ) -> ToolInvocationResponse:
        """Invoke a tool by name.

        The tool_name can be a canonical name or an alias.
        The request body should contain the arguments for the tool.

        Example:
            POST /api/tools/web_search
            {"arguments": {"query": "Claude AI"}}
        """
        from tools._base import ToolContext

        from . import get_registry, resolve_tool_name

        registry = get_registry()

        # Resolve alias to canonical name
        canonical_name = resolve_tool_name(tool_name)

        # Check tool exists
        tool = registry.get_tool(canonical_name)
        if not tool:
            raise HTTPException(
                status_code=404,
                detail=f"Tool '{tool_name}' not found",
            )

        # Create context
        context = ToolContext(
            user_id=request.user_id or "api-user",
            platform=request.platform,
            extra={
                "session_key": request.session_key,
            },
        )

        try:
            # Execute tool through registry (includes policy check)
            result = await registry.execute(canonical_name, request.arguments, context)

            # Check if result indicates an error
            if result.startswith("Error:"):
                return ToolInvocationResponse(
                    success=False,
                    result=None,
                    error=result,
                    tool_name=canonical_name,
                )

            return ToolInvocationResponse(
                success=True,
                result=result,
                error=None,
                tool_name=canonical_name,
            )

        except Exception as e:
            logger.error(f"Tool invocation failed: {e}", exc_info=True)
            return ToolInvocationResponse(
                success=False,
                result=None,
                error=str(e),
                tool_name=canonical_name,
            )

    return router


def create_app(
    title: str = "Clara Tool API",
    version: str = "1.0.0",
):
    """Create a standalone FastAPI app for tool invocation.

    Args:
        title: API title
        version: API version

    Returns:
        FastAPI application
    """
    try:
        from fastapi import FastAPI
    except ImportError:
        raise ImportError("FastAPI is required for HTTP tool handler. " "Install it with: pip install fastapi")

    app = FastAPI(
        title=title,
        version=version,
        description="HTTP API for invoking Clara tools",
    )

    # Include tool routes
    app.include_router(create_tool_router(), prefix="/api/tools")

    @app.get("/health")
    async def health_check():
        """Health check endpoint."""
        from . import get_registry

        registry = get_registry()
        return {
            "status": "healthy",
            "tools_count": len(registry),
            "plugins_count": len(registry.plugins),
        }

    return app


# For convenience, expose a function to run standalone
def run_standalone(host: str = "127.0.0.1", port: int = 8001):
    """Run the tool API as a standalone server.

    Args:
        host: Bind address
        port: Port to listen on
    """
    try:
        import uvicorn
    except ImportError:
        raise ImportError("Uvicorn is required to run standalone. " "Install it with: pip install uvicorn")

    app = create_app()
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    run_standalone()
