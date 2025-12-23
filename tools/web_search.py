"""Web search tool using Tavily API.

Provides web search capabilities for finding current information.
Tools: web_search

Requires: TAVILY_API_KEY env var
"""

from __future__ import annotations

import os
from typing import Any

from ._base import ToolContext, ToolDef

MODULE_NAME = "web_search"
MODULE_VERSION = "1.0.0"

# Configuration
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")


def is_configured() -> bool:
    """Check if web search is configured."""
    return bool(TAVILY_API_KEY)


# --- Tool Handlers ---


async def web_search(args: dict[str, Any], ctx: ToolContext) -> str:
    """Search the web using Tavily API."""
    query = args.get("query", "")
    if not query:
        return "Error: No search query provided"

    api_key = os.getenv("TAVILY_API_KEY")
    if not api_key:
        return "Error: Web search not configured (TAVILY_API_KEY not set)"

    max_results = min(args.get("max_results", 5), 10)
    search_depth = args.get("search_depth", "basic")
    if search_depth not in ("basic", "advanced"):
        search_depth = "basic"

    try:
        import httpx

        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": api_key,
                    "query": query,
                    "search_depth": search_depth,
                    "include_answer": True,
                    "max_results": max_results,
                },
                timeout=30.0,
            )
            response.raise_for_status()
            data = response.json()

            # Format results
            results = []
            if data.get("answer"):
                results.append(f"**Summary:** {data['answer']}\n")

            for r in data.get("results", []):
                title = r.get("title", "No title")
                url = r.get("url", "")
                content = r.get("content", "")[:300]
                results.append(f"- **{title}**")
                results.append(f"  {url}")
                results.append(f"  {content}...")
                results.append("")

            return "\n".join(results) if results else "No results found"

    except Exception as e:
        return f"Search error: {str(e)}"


# --- Tool Definitions ---

TOOLS = [
    ToolDef(
        name="web_search",
        description=(
            "Search the web using Tavily API. "
            "Returns relevant search results with snippets and URLs. "
            "Use this to find current information, research topics, "
            "look up documentation, find news, etc."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query",
                },
                "max_results": {
                    "type": "integer",
                    "description": (
                        "Maximum number of results to return (default: 5, max: 10)"
                    ),
                },
                "search_depth": {
                    "type": "string",
                    "enum": ["basic", "advanced"],
                    "description": (
                        "Search depth: 'basic' for quick results, "
                        "'advanced' for more thorough search (default: basic)"
                    ),
                },
            },
            "required": ["query"],
        },
        handler=web_search,
        # No requires - available if API key is set
    ),
]


# --- Lifecycle Hooks ---


async def initialize() -> None:
    """Initialize web search module."""
    if is_configured():
        print("[web_search] Tavily API configured")
    else:
        print("[web_search] Not configured - tool will be disabled")
        # Remove the tool if not configured
        global TOOLS
        TOOLS = []


async def cleanup() -> None:
    """Cleanup on module unload."""
    pass
