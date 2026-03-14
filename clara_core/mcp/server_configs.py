"""MCP Server Configurations - Defines connection parameters for MCP servers.

This module contains ServerConfig definitions for all MCP servers.

Server categories:
- Host-side servers: Clara's own MCP implementations (local-files, docker-sandbox, claude-code)
- First-party servers: Official vendor implementations (tavily, github, google)

Functions:
- get_host_side_servers() - Returns Clara's own MCP servers
- get_first_party_servers() - Returns official vendor MCP servers
- get_all_servers() - Returns all enabled servers (host-side + first-party)
- get_server_config(name) - Returns config for a specific server by name
"""

import os
import sys

from .client_manager import ServerConfig, TransportType

# Environment-based feature flags for individual servers
MCP_LOCAL_FILES_ENABLED = os.getenv("MCP_LOCAL_FILES_ENABLED", "true").lower() == "true"
MCP_DOCKER_SANDBOX_ENABLED = os.getenv("MCP_DOCKER_SANDBOX_ENABLED", "true").lower() == "true"
MCP_CLAUDE_CODE_ENABLED = os.getenv("MCP_CLAUDE_CODE_ENABLED", "true").lower() == "true"
MCP_TAVILY_ENABLED = os.getenv("MCP_TAVILY_ENABLED", "true").lower() == "true"
MCP_GITHUB_ENABLED = os.getenv("MCP_GITHUB_ENABLED", "true").lower() == "true"
MCP_GOOGLE_ENABLED = os.getenv("MCP_GOOGLE_ENABLED", "false").lower() == "true"
MCP_AZURE_DEVOPS_ENABLED = os.getenv("MCP_AZURE_DEVOPS_ENABLED", "true").lower() == "true"
MCP_PLAYWRIGHT_ENABLED = os.getenv("MCP_PLAYWRIGHT_ENABLED", "true").lower() == "true"


def _get_python_executable() -> str:
    """Get the Python executable for stdio servers."""
    return sys.executable


# Server configurations for host-side MCP servers
# These run on the host machine via stdio transport

LOCAL_FILES_CONFIG = ServerConfig(
    name="local-files",
    transport=TransportType.STDIO,
    command=_get_python_executable(),
    args=["-m", "mcp_servers.local_files.server"],
)

DOCKER_SANDBOX_CONFIG = ServerConfig(
    name="docker-sandbox",
    transport=TransportType.STDIO,
    command=_get_python_executable(),
    args=["-m", "mcp_servers.docker_sandbox.server"],
)

CLAUDE_CODE_CONFIG = ServerConfig(
    name="claude-code",
    transport=TransportType.STDIO,
    command=_get_python_executable(),
    args=["-m", "mcp_servers.claude_code.server"],
)

def is_tavily_configured() -> bool:
    """Check if Tavily MCP server can be used (API key is configured)."""
    return bool(os.getenv("TAVILY_API_KEY"))


# Tavily web search - official first-party MCP server
# Requires: TAVILY_API_KEY env var (inherited from parent process)
# Docs: https://github.com/tavily-ai/tavily-mcp
# Note: No explicit env dict - subprocess inherits TAVILY_API_KEY from parent
TAVILY_CONFIG = ServerConfig(
    name="tavily",
    transport=TransportType.STDIO,
    command="npx",
    args=["-y", "tavily-mcp@latest"],
)


def _get_github_token() -> str:
    """Get GitHub token from environment.

    Supports both GITHUB_PERSONAL_ACCESS_TOKEN (official MCP server)
    and GITHUB_TOKEN (Clara's existing convention) for compatibility.
    """
    return os.getenv("GITHUB_PERSONAL_ACCESS_TOKEN") or os.getenv("GITHUB_TOKEN", "")


def is_github_configured() -> bool:
    """Check if GitHub MCP server can be used (token is configured)."""
    return bool(_get_github_token())


# GitHub - official MCP server from GitHub
# Requires: GITHUB_PERSONAL_ACCESS_TOKEN or GITHUB_TOKEN env var (PAT with appropriate scopes)
# Docs: https://github.com/github/github-mcp-server
# Default toolsets: context, repos, issues, pull_requests, users
# Enable more via GITHUB_TOOLSETS env var (e.g., "default,actions,projects")
#
# Note: Uses Docker container via stdio transport. The MCP SDK's stdio_client
# may have timing issues with Docker containers due to buffering. The server
# configuration is correct - if issues occur, consider using the HTTP remote
# server at https://api.githubcopilot.com/mcp/ instead.
GITHUB_CONFIG = ServerConfig(
    name="github",
    transport=TransportType.STDIO,
    command="docker",
    args=[
        "run",
        "-i",
        "--rm",
        "-e",
        "GITHUB_PERSONAL_ACCESS_TOKEN",
        "-e",
        "GITHUB_TOOLSETS",
        "ghcr.io/github/github-mcp-server",
    ],
    env={
        "GITHUB_PERSONAL_ACCESS_TOKEN": _get_github_token(),
        "GITHUB_TOOLSETS": os.getenv("GITHUB_TOOLSETS", ""),
    },
)


def _get_google_refresh_token() -> str:
    """Get Google refresh token for MCP server.

    Uses MCP_GOOGLE_REFRESH_TOKEN for single-user MCP mode.
    For multi-user support, a custom wrapper using google_token_provider is needed.
    """
    return os.getenv("MCP_GOOGLE_REFRESH_TOKEN", "")


def is_google_mcp_configured() -> bool:
    """Check if Google MCP server can be used.

    Requires:
    - GOOGLE_CLIENT_ID
    - GOOGLE_CLIENT_SECRET
    - MCP_GOOGLE_REFRESH_TOKEN (for single-user mode)

    Note: This checks for single-user static token mode.
    Clara's per-user OAuth (via google_token_provider) is a separate system.
    """
    return bool(
        os.getenv("GOOGLE_CLIENT_ID")
        and os.getenv("GOOGLE_CLIENT_SECRET")
        and _get_google_refresh_token()
    )


# Google Workspace - third-party MCP server (google-workspace-mcp-server)
# Provides: Docs, Sheets, Drive, Gmail, Calendar tools
# Requires: GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, MCP_GOOGLE_REFRESH_TOKEN
#
# LIMITATION: This server uses SINGLE-USER static token model via env vars.
# It does NOT support Clara's per-user OAuth tokens stored in PostgreSQL.
#
# For single-user deployments or testing, set MCP_GOOGLE_REFRESH_TOKEN.
# For multi-user (Clara's default), use Clara's built-in Google tools (tools/google_workspace.py)
# which support per-user OAuth via tools/google_oauth.py.
#
# A future custom wrapper (mcp_servers/google_workspace/) could bridge this gap.
# See: clara_core/mcp/google_token_provider.py for the token bridge infrastructure.
#
# Disabled by default (MCP_GOOGLE_ENABLED=false) because:
# 1. Requires manual refresh token setup
# 2. Single-user limitation conflicts with Clara's multi-user Discord bot
GOOGLE_CONFIG = ServerConfig(
    name="google",
    transport=TransportType.STDIO,
    command="npx",
    args=["-y", "google-workspace-mcp-server@latest"],
    env={
        "GOOGLE_CLIENT_ID": os.getenv("GOOGLE_CLIENT_ID", ""),
        "GOOGLE_CLIENT_SECRET": os.getenv("GOOGLE_CLIENT_SECRET", ""),
        "GOOGLE_REFRESH_TOKEN": _get_google_refresh_token(),
    },
)


def _get_azure_devops_org() -> str:
    """Get Azure DevOps organization from environment."""
    return os.getenv("AZURE_DEVOPS_ORG", "")


def _get_azure_devops_pat() -> str:
    """Get Azure DevOps PAT from environment."""
    return os.getenv("AZURE_DEVOPS_PAT", "")


def is_azure_devops_configured() -> bool:
    """Check if Azure DevOps MCP server can be used.

    Requires:
    - AZURE_DEVOPS_ORG: Organization name or URL
    - AZURE_DEVOPS_PAT: Personal Access Token with appropriate scopes
    """
    return bool(_get_azure_devops_org() and _get_azure_devops_pat())


# Azure DevOps - official Anthropic MCP server
# Provides: Work items, repos, pull requests, pipelines, wikis, and more
# Requires: AZURE_DEVOPS_ORG, AZURE_DEVOPS_PAT env vars
# Docs: https://github.com/anthropics/anthropic-mcp-servers/tree/main/azure-devops
#
# Note: This replaces Clara's legacy tools/azure_devops.py with the official MCP server
# when MCP is enabled. The official server provides similar functionality.
AZURE_DEVOPS_CONFIG = ServerConfig(
    name="azure-devops",
    transport=TransportType.STDIO,
    command="npx",
    args=["-y", "@anthropic/azure-devops-mcp@latest"],
    env={
        "AZURE_DEVOPS_ORG": _get_azure_devops_org(),
        "AZURE_DEVOPS_PAT": _get_azure_devops_pat(),
    },
)


def is_playwright_configured() -> bool:
    """Check if Playwright MCP server can be used.

    Playwright MCP server has no required credentials - it launches browsers
    locally. However, it requires npx to be available.

    Returns:
        True (always available if npx works)
    """
    # Playwright doesn't require credentials, always considered configured
    return True


# Playwright - official Anthropic MCP server for browser automation
# Provides: Browser control, screenshots, navigation, form filling, etc.
# Requires: npx (included with Node.js)
# Docs: https://github.com/anthropics/anthropic-mcp-servers/tree/main/playwright
#
# Note: This enables browser automation capabilities similar to Clara's legacy
# tools/playwright_browser.py but via the standardized MCP protocol.
#
# The Playwright MCP server launches a headless browser on demand and provides
# tools for web interaction, screenshots, and page content extraction.
PLAYWRIGHT_CONFIG = ServerConfig(
    name="playwright",
    transport=TransportType.STDIO,
    command="npx",
    args=["-y", "@anthropic/playwright-mcp@latest"],
)


def get_host_side_servers() -> list[ServerConfig]:
    """Get configurations for enabled host-side MCP servers.

    Host-side servers are Clara's own MCP server implementations that
    run as Python subprocesses. They wrap existing Clara functionality.

    Respects environment variables:
    - MCP_LOCAL_FILES_ENABLED (default: true)
    - MCP_DOCKER_SANDBOX_ENABLED (default: true)
    - MCP_CLAUDE_CODE_ENABLED (default: true)

    Returns:
        List of ServerConfig for enabled host-side servers
    """
    servers = []

    if MCP_LOCAL_FILES_ENABLED:
        servers.append(LOCAL_FILES_CONFIG)

    if MCP_DOCKER_SANDBOX_ENABLED:
        servers.append(DOCKER_SANDBOX_CONFIG)

    if MCP_CLAUDE_CODE_ENABLED:
        servers.append(CLAUDE_CODE_CONFIG)

    return servers


def get_first_party_servers() -> list[ServerConfig]:
    """Get configurations for enabled first-party MCP servers.

    First-party servers are official vendor MCP implementations:
    - Tavily: Web search (requires TAVILY_API_KEY)
    - GitHub: Repository management (requires GITHUB_TOKEN)
    - Google: Workspace apps (requires GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET)
    - Azure DevOps: Work items, repos, pipelines (requires AZURE_DEVOPS_ORG, AZURE_DEVOPS_PAT)
    - Playwright: Browser automation (no credentials required)

    Respects environment variables:
    - MCP_TAVILY_ENABLED (default: true)
    - MCP_GITHUB_ENABLED (default: true)
    - MCP_GOOGLE_ENABLED (default: false)
    - MCP_AZURE_DEVOPS_ENABLED (default: true)
    - MCP_PLAYWRIGHT_ENABLED (default: true)

    Returns:
        List of ServerConfig for enabled and configured first-party servers
    """
    servers = []

    # Tavily - requires API key
    if MCP_TAVILY_ENABLED and is_tavily_configured():
        servers.append(TAVILY_CONFIG)

    # GitHub - requires Docker and a configured token
    if MCP_GITHUB_ENABLED and is_github_configured():
        servers.append(GITHUB_CONFIG)

    # Google - requires static refresh token (single-user mode)
    if MCP_GOOGLE_ENABLED and is_google_mcp_configured():
        servers.append(GOOGLE_CONFIG)

    # Azure DevOps - requires org and PAT
    if MCP_AZURE_DEVOPS_ENABLED and is_azure_devops_configured():
        servers.append(AZURE_DEVOPS_CONFIG)

    # Playwright - always available (no credentials required)
    if MCP_PLAYWRIGHT_ENABLED and is_playwright_configured():
        servers.append(PLAYWRIGHT_CONFIG)

    return servers


def get_all_servers() -> list[ServerConfig]:
    """Get configurations for all enabled MCP servers (host-side + first-party).

    Combines:
    - Host-side servers: local-files, docker-sandbox, claude-code
    - First-party servers: tavily, github, google

    Returns:
        Combined list of all enabled server configurations
    """
    return get_host_side_servers() + get_first_party_servers()


def get_server_config(name: str) -> ServerConfig | None:
    """Get configuration for a specific server by name.

    Args:
        name: Server name (e.g., "local-files", "docker-sandbox", "claude-code",
              "tavily", "github", "google", "azure-devops", "playwright")

    Returns:
        ServerConfig if found, None otherwise
    """
    configs = {
        "local-files": LOCAL_FILES_CONFIG,
        "docker-sandbox": DOCKER_SANDBOX_CONFIG,
        "claude-code": CLAUDE_CODE_CONFIG,
        "tavily": TAVILY_CONFIG,
        "github": GITHUB_CONFIG,
        "google": GOOGLE_CONFIG,
        "azure-devops": AZURE_DEVOPS_CONFIG,
        "playwright": PLAYWRIGHT_CONFIG,
    }
    return configs.get(name)


# Aliases for backward compatibility and clarity
HOST_SIDE_SERVERS = get_host_side_servers
FIRST_PARTY_SERVERS = get_first_party_servers
ALL_SERVERS = get_all_servers
