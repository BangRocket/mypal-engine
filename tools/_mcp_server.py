"""MCP (Model Context Protocol) server integration.

Exposes Clara's tools via the MCP protocol for use with Claude Desktop,
other MCP clients, or any MCP-compatible AI application.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ._registry import ToolRegistry

# Check for MCP availability
try:
    from mcp.server.fastmcp import FastMCP

    MCP_AVAILABLE = True
except ImportError:
    MCP_AVAILABLE = False
    FastMCP = None


class ClaraMCPServer:
    """MCP server that exposes Clara's tools.

    Uses FastMCP to provide a simple interface for exposing tools
    via the Model Context Protocol.

    Usage:
        server = ClaraMCPServer(registry)
        await server.run_stdio()  # For Claude Desktop
        # or
        await server.run_http(port=8002)  # For HTTP access
    """

    def __init__(self, registry: ToolRegistry, name: str = "clara-tools"):
        """Initialize the MCP server.

        Args:
            registry: ToolRegistry instance with registered tools
            name: Server name for MCP identification
        """
        if not MCP_AVAILABLE:
            raise ImportError(
                "MCP package not installed. Install with: pip install mcp"
            )

        self.registry = registry
        self.name = name
        self._mcp: FastMCP | None = None
        self._setup_complete = False

    def _create_server(self) -> FastMCP:
        """Create and configure the FastMCP server."""
        mcp = FastMCP(self.name)

        # Register all tools from the registry
        for tool_name in self.registry.get_tool_names():
            tool_def = self.registry.get_tool(tool_name)
            if tool_def is None:
                continue

            # Skip platform-specific tools that aren't MCP-compatible
            if tool_def.platforms and "mcp" not in tool_def.platforms:
                # Skip Discord-only tools
                if tool_def.platforms == ["discord"]:
                    continue

            self._register_tool(mcp, tool_def)

        return mcp

    def _register_tool(self, mcp: FastMCP, tool_def: Any) -> None:
        """Register a single tool with the MCP server."""
        from ._base import ToolContext

        # Create a wrapper function for MCP
        async def tool_handler(**kwargs) -> str:
            context = ToolContext(
                user_id="mcp-user",
                platform="mcp",
                extra={"mcp_server": self},
            )
            return await tool_def.handler(kwargs, context)

        # Register with FastMCP using the tool decorator pattern
        # FastMCP expects decorated functions, so we'll use add_tool
        try:
            # Try the direct add_tool method if available
            if hasattr(mcp, "add_tool"):
                mcp.add_tool(
                    tool_handler,
                    name=tool_def.name,
                    description=tool_def.description,
                )
            else:
                # Fall back to using the decorator pattern
                decorated = mcp.tool(
                    name=tool_def.name, description=tool_def.description
                )(tool_handler)
                # The decoration itself registers the tool
        except Exception as e:
            print(f"[mcp] Failed to register tool {tool_def.name}: {e}", file=sys.stderr)

    def setup(self) -> None:
        """Set up the MCP server (call before running)."""
        if self._setup_complete:
            return

        self._mcp = self._create_server()
        self._setup_complete = True
        print(f"[mcp] Server '{self.name}' ready with {len(self.registry)} tools")

    async def run_stdio(self) -> None:
        """Run the MCP server over stdio (for Claude Desktop)."""
        self.setup()
        if self._mcp is None:
            raise RuntimeError("MCP server not initialized")

        print(f"[mcp] Starting stdio server...", file=sys.stderr)
        await self._mcp.run_stdio()

    async def run_sse(self, host: str = "localhost", port: int = 8002) -> None:
        """Run the MCP server over SSE (Server-Sent Events)."""
        self.setup()
        if self._mcp is None:
            raise RuntimeError("MCP server not initialized")

        print(f"[mcp] Starting SSE server on {host}:{port}...", file=sys.stderr)
        await self._mcp.run_sse(host=host, port=port)

    def get_tool_list(self) -> list[dict[str, Any]]:
        """Get list of tools in MCP format."""
        return self.registry.get_tools(format="mcp")


def is_mcp_available() -> bool:
    """Check if MCP is available."""
    return MCP_AVAILABLE
