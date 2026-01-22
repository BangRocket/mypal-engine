"""MCP Server Installer for installing servers from various sources.

This module provides functionality to install MCP servers from:
- npm packages (e.g., @modelcontextprotocol/server-everything)
- Smithery registry (e.g., @anthropic/mcp-server-fetch)
- GitHub repositories
- Docker images
- Local paths

Configs are stored as JSON files in .mcp_servers/{name}/config.json by default.
Set MCP_USE_DATABASE=true to use database storage instead.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import aiohttp

from .client import MCPClient
from .models import (
    MCP_SERVERS_DIR,
    MCPServerConfig,
    delete_server_config,
    get_server_dir,
    list_server_configs,
    load_server_config,
    save_server_config,
)

logger = logging.getLogger(__name__)

# Smithery API configuration
SMITHERY_REGISTRY_URL = "https://registry.smithery.ai/servers"
SMITHERY_API_TOKEN = os.getenv("SMITHERY_API_TOKEN", "")

# Toggle for database vs JSON storage (JSON is default)
USE_DATABASE = os.getenv("MCP_USE_DATABASE", "").lower() in ("true", "1", "yes")


@dataclass
class SmitheryServer:
    """A server from the Smithery registry."""

    qualified_name: str
    display_name: str
    description: str
    icon_url: str | None = None
    homepage: str | None = None
    verified: bool = False
    use_count: int = 0
    created_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "qualified_name": self.qualified_name,
            "display_name": self.display_name,
            "description": self.description,
            "icon_url": self.icon_url,
            "homepage": self.homepage,
            "verified": self.verified,
            "use_count": self.use_count,
            "created_at": self.created_at,
        }


@dataclass
class SmitherySearchResult:
    """Result of a Smithery search."""

    servers: list[SmitheryServer] = field(default_factory=list)
    total: int = 0
    page: int = 1
    page_size: int = 10
    error: str | None = None


@dataclass
class InstallResult:
    """Result of an MCP server installation."""

    success: bool
    server: MCPServerConfig | None = None
    error: str | None = None
    tools_discovered: int = 0


class SmitheryClient:
    """Client for interacting with the Smithery registry."""

    def __init__(self, api_token: str | None = None) -> None:
        """Initialize the Smithery client.

        Args:
            api_token: Optional API token for authenticated requests.
                      Falls back to SMITHERY_API_TOKEN env var.
        """
        self.api_token = api_token or SMITHERY_API_TOKEN

    async def search(
        self,
        query: str,
        page: int = 1,
        page_size: int = 10,
    ) -> SmitherySearchResult:
        """Search the Smithery registry for MCP servers.

        Args:
            query: Search query (semantic search)
            page: Page number (1-indexed)
            page_size: Results per page (max 50)

        Returns:
            SmitherySearchResult with matching servers
        """
        params = {
            "q": query,
            "page": page,
            "pageSize": min(page_size, 50),
        }

        headers = {"Accept": "application/json"}
        if self.api_token:
            headers["Authorization"] = f"Bearer {self.api_token}"

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    SMITHERY_REGISTRY_URL,
                    params=params,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        return SmitherySearchResult(error=f"Smithery API error ({response.status}): {error_text[:200]}")

                    data = await response.json()

                    servers = []
                    for s in data.get("servers", []):
                        servers.append(
                            SmitheryServer(
                                qualified_name=s.get("qualifiedName", ""),
                                display_name=s.get("displayName", s.get("qualifiedName", "")),
                                description=s.get("description", ""),
                                icon_url=s.get("iconUrl"),
                                homepage=s.get("homepage"),
                                verified=s.get("verified", False),
                                use_count=s.get("useCount", 0),
                                created_at=s.get("createdAt"),
                            )
                        )

                    pagination = data.get("pagination", {})
                    return SmitherySearchResult(
                        servers=servers,
                        total=pagination.get("totalCount", len(servers)),
                        page=pagination.get("currentPage", page),
                        page_size=pagination.get("pageSize", page_size),
                    )

        except asyncio.TimeoutError:
            return SmitherySearchResult(error="Smithery API request timed out")
        except Exception as e:
            return SmitherySearchResult(error=f"Smithery API error: {e}")

    async def get_server(self, qualified_name: str) -> SmitheryServer | None:
        """Get a specific server by its qualified name.

        Args:
            qualified_name: The server's qualified name (e.g., "@anthropic/mcp-server-fetch")

        Returns:
            SmitheryServer if found, None otherwise
        """
        # Search for the exact name
        result = await self.search(qualified_name, page_size=50)
        if result.error:
            logger.warning(f"[Smithery] Search error: {result.error}")
            return None

        # Find exact match
        for server in result.servers:
            if server.qualified_name == qualified_name:
                return server

        return None


class MCPInstaller:
    """Installer for MCP servers from various sources."""

    def __init__(self) -> None:
        """Initialize the installer."""
        self._ensure_servers_dir()

    def _ensure_servers_dir(self) -> None:
        """Ensure the MCP servers directory exists."""
        MCP_SERVERS_DIR.mkdir(parents=True, exist_ok=True)

    def _server_exists(self, name: str) -> bool:
        """Check if a server with this name already exists."""
        if USE_DATABASE:
            return self._server_exists_in_db(name)
        return load_server_config(name) is not None

    def _server_exists_in_db(self, name: str) -> bool:
        """Check if server exists in database."""
        try:
            from db import SessionLocal

            with SessionLocal() as session:
                result = session.execute("SELECT 1 FROM mcp_servers WHERE name = :name", {"name": name}).first()
                return result is not None
        except Exception:
            return load_server_config(name) is not None

    def _save_server(self, server: MCPServerConfig) -> bool:
        """Save server config to storage."""
        if USE_DATABASE:
            return self._save_server_to_db(server)
        return save_server_config(server)

    def _save_server_to_db(self, server: MCPServerConfig) -> bool:
        """Save server to database."""
        try:
            from db import SessionLocal

            with SessionLocal() as session:
                session.execute(
                    """
                    INSERT INTO mcp_servers (name, source_type, display_name, source_url,
                        transport, command, args, cwd, env, endpoint_url, docker_config,
                        enabled, status, last_error, tool_count, tools_json, installed_by)
                    VALUES (:name, :source_type, :display_name, :source_url,
                        :transport, :command, :args, :cwd, :env, :endpoint_url, :docker_config,
                        :enabled, :status, :last_error, :tool_count, :tools_json, :installed_by)
                    ON CONFLICT (name) DO UPDATE SET
                        source_type = :source_type, display_name = :display_name,
                        source_url = :source_url, transport = :transport, command = :command,
                        args = :args, cwd = :cwd, env = :env, endpoint_url = :endpoint_url,
                        docker_config = :docker_config, enabled = :enabled, status = :status,
                        last_error = :last_error, tool_count = :tool_count, tools_json = :tools_json,
                        installed_by = :installed_by, updated_at = CURRENT_TIMESTAMP
                    """,
                    {
                        "name": server.name,
                        "source_type": server.source_type,
                        "display_name": server.display_name,
                        "source_url": server.source_url,
                        "transport": server.transport,
                        "command": server.command,
                        "args": json.dumps(server.args) if server.args else None,
                        "cwd": server.cwd,
                        "env": json.dumps(server.env) if server.env else None,
                        "endpoint_url": server.endpoint_url,
                        "docker_config": json.dumps(server.docker_config) if server.docker_config else None,
                        "enabled": server.enabled,
                        "status": server.status,
                        "last_error": server.last_error,
                        "tool_count": server.tool_count,
                        "tools_json": json.dumps(server.tools) if server.tools else None,
                        "installed_by": server.installed_by,
                    },
                )
                session.commit()
                return True
        except Exception as e:
            logger.warning(f"[MCP Installer] Database save failed, using JSON: {e}")
            return save_server_config(server)

    def _delete_server(self, name: str) -> bool:
        """Delete server config from storage."""
        if USE_DATABASE:
            return self._delete_server_from_db(name)
        return delete_server_config(name)

    def _delete_server_from_db(self, name: str) -> bool:
        """Delete server from database."""
        try:
            from db import SessionLocal

            with SessionLocal() as session:
                session.execute("DELETE FROM mcp_servers WHERE name = :name", {"name": name})
                session.commit()
                return True
        except Exception as e:
            logger.warning(f"[MCP Installer] Database delete failed: {e}")
            return delete_server_config(name)

    def _list_servers(self) -> list[MCPServerConfig]:
        """List all servers from storage."""
        if USE_DATABASE:
            return self._list_servers_from_db()
        return list_server_configs()

    def _list_servers_from_db(self) -> list[MCPServerConfig]:
        """List servers from database."""
        try:
            from db import SessionLocal

            with SessionLocal() as session:
                result = session.execute(
                    "SELECT name, source_type, display_name, source_url, transport, "
                    "command, args, cwd, env, endpoint_url, docker_config, enabled, "
                    "status, last_error, tool_count, tools_json, installed_by, created_at "
                    "FROM mcp_servers"
                )

                configs = []
                for row in result:
                    # Parse JSON fields
                    args = row[6]
                    if isinstance(args, str):
                        try:
                            args = json.loads(args)
                        except Exception:
                            args = []

                    env = row[8]
                    if isinstance(env, str):
                        try:
                            env = json.loads(env)
                        except Exception:
                            env = {}

                    docker_config = row[10]
                    if isinstance(docker_config, str):
                        try:
                            docker_config = json.loads(docker_config)
                        except Exception:
                            docker_config = {}

                    tools = row[15]
                    if isinstance(tools, str):
                        try:
                            tools = json.loads(tools)
                        except Exception:
                            tools = []

                    config = MCPServerConfig(
                        name=row[0],
                        source_type=row[1],
                        display_name=row[2],
                        source_url=row[3],
                        transport=row[4] or "stdio",
                        command=row[5],
                        args=args or [],
                        cwd=row[7],
                        env=env or {},
                        endpoint_url=row[9],
                        docker_config=docker_config or {},
                        enabled=row[11],
                        status=row[12] or "stopped",
                        last_error=row[13],
                        tool_count=row[14] or 0,
                        tools=tools or [],
                        installed_by=row[16],
                        created_at=row[17].isoformat() if row[17] else None,
                    )
                    configs.append(config)

                return configs
        except Exception as e:
            logger.warning(f"[MCP Installer] Database list failed: {e}")
            return list_server_configs()

    async def install(
        self,
        source: str,
        name: str | None = None,
        env: dict[str, str] | None = None,
        installed_by: str | None = None,
        args: list[str] | None = None,
    ) -> InstallResult:
        """Install an MCP server from a source.

        Auto-detects source type from the input:
        - Smithery: Prefix with "smithery:" (e.g., smithery:e2b, smithery:@anthropic/mcp-server-fetch)
        - npm package: Starts with @ or contains no / or .
        - GitHub: Contains github.com or is owner/repo format
        - Docker: Contains docker.io or other registry patterns
        - Local: Starts with / or ./ or ~ (path)

        Args:
            source: Source to install from (npm package, GitHub URL, Docker image, or path)
            name: Optional custom name for the server (auto-detected if not provided)
            env: Optional environment variables for the server
            installed_by: Optional user ID who installed this server
            args: Optional extra command-line arguments for the server

        Returns:
            InstallResult with success status and server info
        """
        source_type = self._detect_source_type(source)
        logger.info(f"[MCP Installer] Installing from {source_type}: {source}")

        if source_type == "smithery":
            # Remove smithery: prefix
            smithery_name = source[9:] if source.startswith("smithery:") else source
            return await self._install_smithery(smithery_name, name, env, installed_by, args)
        elif source_type == "npm":
            return await self._install_npm(source, name, env, installed_by, args)
        elif source_type == "github":
            return await self._install_github(source, name, env, installed_by)
        elif source_type == "docker":
            return await self._install_docker(source, name, env, installed_by)
        elif source_type == "local":
            return await self._install_local(source, name, env, installed_by)
        else:
            return InstallResult(success=False, error=f"Unknown source type: {source_type}")

    def _detect_source_type(self, source: str) -> str:
        """Detect the type of source.

        Args:
            source: Source string

        Returns:
            One of: "smithery", "npm", "github", "docker", "local"
        """
        source = source.strip()

        # Smithery explicit prefix
        if source.startswith("smithery:"):
            return "smithery"

        # Local path
        if source.startswith(("/", "./", "~", "../")):
            return "local"

        # GitHub URL
        if "github.com" in source or re.match(r"^[a-zA-Z0-9_-]+/[a-zA-Z0-9_-]+$", source):
            return "github"

        # Docker image
        if any(x in source for x in ["docker.io", "ghcr.io", "gcr.io", "quay.io", "registry.", "amazonaws.com"]):
            return "docker"

        # npm package (starts with @ or looks like a package name)
        if source.startswith("@") or re.match(r"^[a-zA-Z0-9_-]+$", source):
            return "npm"

        # Default to npm for unknown patterns that look like package names
        if "/" not in source and "." not in source:
            return "npm"

        return "unknown"

    def _generate_name(self, source: str, source_type: str) -> str:
        """Generate a server name from the source.

        Args:
            source: Source string
            source_type: Type of source

        Returns:
            Generated name
        """
        if source_type == "smithery":
            # @anthropic/mcp-server-fetch -> fetch
            # e2b -> e2b
            name = source.split("/")[-1]
            # Remove common prefixes
            for prefix in ["mcp-server-", "mcp-", "server-"]:
                if name.startswith(prefix):
                    name = name[len(prefix) :]
                    break
            return name.replace("-", "_")

        elif source_type == "npm":
            # @scope/mcp-server-name -> name
            # mcp-server-name -> name
            name = source.split("/")[-1]
            # Remove common prefixes
            for prefix in ["mcp-server-", "mcp-", "server-"]:
                if name.startswith(prefix):
                    name = name[len(prefix) :]
                    break
            return name.replace("-", "_")

        elif source_type == "github":
            # github.com/owner/repo or owner/repo -> repo
            parts = source.rstrip("/").split("/")
            name = parts[-1]
            # Remove .git suffix
            if name.endswith(".git"):
                name = name[:-4]
            return name.replace("-", "_")

        elif source_type == "docker":
            # registry/image:tag -> image
            name = source.split("/")[-1]
            name = name.split(":")[0]
            return name.replace("-", "_")

        elif source_type == "local":
            # /path/to/server -> server
            path = Path(source).expanduser()
            return path.name.replace("-", "_")

        return "mcp_server"

    async def _install_npm(
        self,
        package: str,
        name: str | None,
        env: dict[str, str] | None,
        installed_by: str | None,
        extra_args: list[str] | None = None,
    ) -> InstallResult:
        """Install an npm MCP server.

        Uses npx to run the server without global installation.

        Args:
            package: npm package name (e.g., @modelcontextprotocol/server-everything)
            name: Optional custom name
            env: Optional environment variables
            installed_by: Optional user ID
            extra_args: Optional extra arguments to pass after the package name
        """
        server_name = name or self._generate_name(package, "npm")

        # Check if server already exists
        if self._server_exists(server_name):
            return InstallResult(success=False, error=f"Server '{server_name}' already exists")

        # Check if npx is available
        npx_path = shutil.which("npx")
        if not npx_path:
            return InstallResult(
                success=False,
                error="npx not found. Please install Node.js and npm.",
            )

        # Build args: npx -y -q <package> [extra_args...]
        # -q (--quiet) suppresses npm script output that pollutes JSON-RPC stream
        server_args = ["-y", "-q", package]
        if extra_args:
            server_args.extend(extra_args)

        # Create server configuration
        server = MCPServerConfig(
            name=server_name,
            source_type="npm",
            display_name=package.split("/")[-1],
            source_url=package,
            transport="stdio",
            command="npx",
            args=server_args,
            env=env or {},
            installed_by=installed_by,
        )

        # Test the connection
        logger.info(f"[MCP Installer] Testing npm server '{server_name}'...")
        test_result = await self._test_server(server)

        if not test_result["success"]:
            return InstallResult(
                success=False,
                error=f"Server test failed: {test_result.get('error', 'Unknown error')}",
            )

        # Save config
        server.set_tools(test_result.get("tools", []))
        self._save_server(server)

        logger.info(f"[MCP Installer] Installed npm server '{server_name}' with {server.tool_count} tools")
        return InstallResult(
            success=True,
            server=server,
            tools_discovered=server.tool_count,
        )

    async def _install_smithery(
        self,
        package: str,
        name: str | None,
        env: dict[str, str] | None,
        installed_by: str | None,
        extra_args: list[str] | None = None,
    ) -> InstallResult:
        """Install an MCP server from Smithery registry.

        Looks up the server in Smithery and installs it (typically via npm).

        Args:
            package: Smithery package name (e.g., e2b, @anthropic/mcp-server-fetch)
            name: Optional custom name
            env: Optional environment variables
            installed_by: Optional user ID
            extra_args: Optional extra arguments
        """
        server_name = name or self._generate_name(package, "smithery")

        # Check if server already exists
        if self._server_exists(server_name):
            return InstallResult(success=False, error=f"Server '{server_name}' already exists")

        # Check if npx is available
        npx_path = shutil.which("npx")
        if not npx_path:
            return InstallResult(
                success=False,
                error="npx not found. Please install Node.js and npm.",
            )

        # Look up the server in Smithery registry
        smithery = SmitheryClient()
        smithery_server = await smithery.get_server(package)

        display_name = package.split("/")[-1]
        if smithery_server:
            display_name = smithery_server.display_name or display_name
            logger.info(f"[MCP Installer] Found Smithery server: {smithery_server.display_name}")

        # Try using Smithery CLI run command
        # npx -y @smithery/cli run <package> -- [args]
        server_args = ["-y", "-q", "@smithery/cli@latest", "run", package, "--"]
        if extra_args:
            server_args.extend(extra_args)

        # Create server configuration
        server = MCPServerConfig(
            name=server_name,
            source_type="smithery",
            display_name=display_name,
            source_url=f"smithery:{package}",
            transport="stdio",
            command="npx",
            args=server_args,
            env=env or {},
            installed_by=installed_by,
        )

        # Test the connection
        logger.info(f"[MCP Installer] Testing Smithery server '{server_name}'...")
        test_result = await self._test_server(server)

        if not test_result["success"]:
            # Try fallback: install directly via npm if it looks like an npm package
            logger.info("[MCP Installer] Smithery CLI approach failed, trying direct npm...")

            # Build npm args
            npm_args = ["-y", "-q", package]
            if extra_args:
                npm_args.extend(extra_args)

            server.args = npm_args
            server.source_type = "smithery"  # Keep as smithery for tracking

            test_result = await self._test_server(server)

            if not test_result["success"]:
                return InstallResult(
                    success=False,
                    error=f"Server test failed: {test_result.get('error', 'Unknown error')}",
                )

        # Save config
        server.set_tools(test_result.get("tools", []))
        self._save_server(server)

        logger.info(f"[MCP Installer] Installed Smithery server '{server_name}' with {server.tool_count} tools")
        return InstallResult(
            success=True,
            server=server,
            tools_discovered=server.tool_count,
        )

    def _extract_github_owner_repo(self, source: str) -> tuple[str, str] | None:
        """Extract owner/repo from a GitHub URL or shorthand.

        Args:
            source: GitHub URL (https://github.com/owner/repo) or shorthand (owner/repo)

        Returns:
            Tuple of (owner, repo) or None if couldn't parse
        """
        # Remove .git suffix if present
        source = source.rstrip("/")
        if source.endswith(".git"):
            source = source[:-4]

        # Handle full URLs
        if "github.com" in source:
            # https://github.com/owner/repo or git@github.com:owner/repo
            if "github.com/" in source:
                parts = source.split("github.com/")[-1].split("/")
            elif "github.com:" in source:
                parts = source.split("github.com:")[-1].split("/")
            else:
                return None

            if len(parts) >= 2:
                return (parts[0], parts[1])

        # Handle shorthand (owner/repo)
        elif "/" in source and source.count("/") == 1:
            parts = source.split("/")
            return (parts[0], parts[1])

        return None

    async def _install_github(
        self,
        source: str,
        name: str | None,
        env: dict[str, str] | None,
        installed_by: str | None,
    ) -> InstallResult:
        """Install an MCP server from a GitHub repository.

        First tries to install via npx (for npm packages), then falls back
        to cloning and building locally.

        Args:
            source: GitHub URL or owner/repo
            name: Optional custom name
            env: Optional environment variables
            installed_by: Optional user ID
        """
        server_name = name or self._generate_name(source, "github")

        # Check if server already exists
        if self._server_exists(server_name):
            return InstallResult(success=False, error=f"Server '{server_name}' already exists")

        # Extract owner/repo for npx github: shorthand
        owner_repo = self._extract_github_owner_repo(source)

        # Try npx with github: shorthand first (for npm packages)
        if owner_repo:
            npx_path = shutil.which("npx")
            if npx_path:
                github_shorthand = f"github:{owner_repo[0]}/{owner_repo[1]}"
                logger.info(f"[MCP Installer] Trying npx {github_shorthand}...")

                # Create server configuration for npx approach
                # -q (--quiet) suppresses npm script output that pollutes JSON-RPC stream
                server = MCPServerConfig(
                    name=server_name,
                    source_type="github",
                    display_name=owner_repo[1],
                    source_url=f"https://github.com/{owner_repo[0]}/{owner_repo[1]}",
                    transport="stdio",
                    command="npx",
                    args=["-y", "-q", github_shorthand],
                    env=env or {},
                    installed_by=installed_by,
                )

                # Test the connection
                test_result = await self._test_server(server)

                if test_result["success"]:
                    # npx approach worked!
                    server.set_tools(test_result.get("tools", []))
                    self._save_server(server)

                    logger.info(
                        f"[MCP Installer] Installed GitHub server '{server_name}' via npx "
                        f"with {server.tool_count} tools"
                    )
                    return InstallResult(
                        success=True,
                        server=server,
                        tools_discovered=server.tool_count,
                    )
                else:
                    logger.info(
                        f"[MCP Installer] npx approach failed ({test_result.get('error')}), "
                        "trying clone-and-build..."
                    )

        # Fall back to clone-and-build approach
        # Normalize GitHub URL
        if not source.startswith(("http://", "https://", "git@")):
            source = f"https://github.com/{source}"
        if not source.endswith(".git"):
            source = f"{source}.git"

        # Clone directory
        clone_dir = get_server_dir(server_name)

        try:
            # Clone the repository
            logger.info(f"[MCP Installer] Cloning {source}...")
            result = subprocess.run(
                ["git", "clone", "--depth", "1", source, str(clone_dir)],
                capture_output=True,
                text=True,
                timeout=120,
            )

            if result.returncode != 0:
                return InstallResult(
                    success=False,
                    error=f"Git clone failed: {result.stderr}",
                )

            # Detect project type and build
            server = await self._configure_github_server(clone_dir, server_name, source, env, installed_by)

            if not server:
                # Cleanup on failure
                shutil.rmtree(clone_dir, ignore_errors=True)
                return InstallResult(
                    success=False,
                    error="Could not detect how to run this MCP server. "
                    "Expected package.json (Node.js) or pyproject.toml/setup.py (Python).",
                )

            # Test the connection
            logger.info(f"[MCP Installer] Testing GitHub server '{server_name}'...")
            test_result = await self._test_server(server)

            if not test_result["success"]:
                shutil.rmtree(clone_dir, ignore_errors=True)
                return InstallResult(
                    success=False,
                    error=f"Server test failed: {test_result.get('error', 'Unknown error')}",
                )

            # Save config
            server.set_tools(test_result.get("tools", []))
            self._save_server(server)

            logger.info(f"[MCP Installer] Installed GitHub server '{server_name}' with {server.tool_count} tools")
            return InstallResult(
                success=True,
                server=server,
                tools_discovered=server.tool_count,
            )

        except subprocess.TimeoutExpired:
            shutil.rmtree(clone_dir, ignore_errors=True)
            return InstallResult(success=False, error="Git clone timed out")
        except Exception as e:
            shutil.rmtree(clone_dir, ignore_errors=True)
            return InstallResult(success=False, error=str(e))

    def _find_monorepo_package(self, repo_dir: Path) -> Path | None:
        """Find the actual MCP server package in a monorepo.

        Checks for common monorepo patterns and tries to locate the MCP server package.

        Args:
            repo_dir: Path to the cloned repository

        Returns:
            Path to the package directory, or None if not a monorepo
        """
        # Check if this is a monorepo
        is_monorepo = False
        workspace_dirs = []

        # Check for pnpm workspace
        pnpm_workspace = repo_dir / "pnpm-workspace.yaml"
        if pnpm_workspace.exists():
            is_monorepo = True
            workspace_dirs = ["packages", "apps"]

        # Check for npm/yarn workspaces in package.json
        package_json = repo_dir / "package.json"
        if package_json.exists():
            try:
                with open(package_json) as f:
                    pkg = json.load(f)
                workspaces = pkg.get("workspaces", [])
                if workspaces:
                    is_monorepo = True
                    # Parse workspace patterns like "packages/*"
                    for ws in workspaces:
                        if isinstance(ws, str):
                            ws_dir = ws.replace("/*", "").replace("/**", "")
                            if ws_dir and not ws_dir.startswith("!"):
                                workspace_dirs.append(ws_dir)
            except Exception:
                pass

        # Check for lerna
        lerna_json = repo_dir / "lerna.json"
        if lerna_json.exists():
            is_monorepo = True
            workspace_dirs.extend(["packages", "apps"])

        if not is_monorepo:
            return None

        # Deduplicate and add common patterns
        workspace_dirs = list(set(workspace_dirs + ["packages", "apps"]))

        # Look for the MCP server package
        for ws_dir in workspace_dirs:
            ws_path = repo_dir / ws_dir
            if not ws_path.exists():
                continue

            # Check each subdirectory for a package.json with MCP-related content
            for pkg_dir in ws_path.iterdir():
                if not pkg_dir.is_dir():
                    continue

                pkg_json = pkg_dir / "package.json"
                if pkg_json.exists():
                    try:
                        with open(pkg_json) as f:
                            pkg = json.load(f)

                        # Check if this looks like an MCP server
                        pkg_name = pkg.get("name", "").lower()
                        pkg_desc = pkg.get("description", "").lower()
                        pkg_keywords = [k.lower() for k in pkg.get("keywords", [])]
                        deps = list(pkg.get("dependencies", {}).keys())
                        deps += list(pkg.get("devDependencies", {}).keys())

                        # Heuristics for MCP server detection
                        is_mcp = (
                            "mcp" in pkg_name
                            or "mcp" in pkg_desc
                            or "mcp" in pkg_keywords
                            or "@modelcontextprotocol/sdk" in deps
                            or "mcp-server" in pkg_name
                        )

                        # Also check for JS/TS subdir specifically for e2b-style repos
                        if pkg_dir.name in ("js", "javascript", "typescript", "node"):
                            is_mcp = True

                        if is_mcp:
                            logger.info(f"[MCP Installer] Found MCP package in monorepo: {pkg_dir}")
                            return pkg_dir

                    except Exception:
                        continue

        return None

    def _parse_smithery_yaml(self, repo_dir: Path) -> dict[str, Any] | None:
        """Parse smithery.yaml for server configuration hints.

        Args:
            repo_dir: Path to the repository

        Returns:
            Dict with startCommand info, or None if not found
        """
        smithery_yaml = repo_dir / "smithery.yaml"
        if not smithery_yaml.exists():
            return None

        try:
            import yaml

            with open(smithery_yaml) as f:
                config = yaml.safe_load(f)

            start_command = config.get("startCommand", {})
            if start_command:
                logger.info("[MCP Installer] Found smithery.yaml with startCommand")
                return start_command

        except ImportError:
            logger.debug("[MCP Installer] PyYAML not installed, skipping smithery.yaml")
        except Exception as e:
            logger.warning(f"[MCP Installer] Failed to parse smithery.yaml: {e}")

        return None

    async def _configure_github_server(
        self,
        repo_dir: Path,
        name: str,
        source_url: str,
        env: dict[str, str] | None,
        installed_by: str | None,
    ) -> MCPServerConfig | None:
        """Configure an MCP server from a cloned GitHub repo.

        Detects project type and sets up the appropriate run command.
        Handles monorepos by finding the actual MCP package.
        Runs build steps for TypeScript projects.

        Args:
            repo_dir: Path to the cloned repository
            name: Server name
            source_url: Original GitHub URL
            env: Environment variables
            installed_by: User who installed

        Returns:
            Configured MCPServerConfig or None if couldn't detect how to run
        """
        # Check for monorepo and find actual package
        monorepo_pkg = self._find_monorepo_package(repo_dir)
        if monorepo_pkg:
            logger.info(f"[MCP Installer] Detected monorepo, using package at: {monorepo_pkg}")
            working_dir = monorepo_pkg
        else:
            working_dir = repo_dir

        server = MCPServerConfig(
            name=name,
            source_type="github",
            source_url=source_url,
            transport="stdio",
            cwd=str(working_dir),
            env=env or {},
            installed_by=installed_by,
        )

        # Check for smithery.yaml hints first
        smithery_config = self._parse_smithery_yaml(repo_dir) or self._parse_smithery_yaml(working_dir)

        # Check for package.json (Node.js)
        package_json = working_dir / "package.json"
        if package_json.exists():
            try:
                with open(package_json) as f:
                    pkg = json.load(f)

                scripts = pkg.get("scripts", {})

                # Detect package manager
                pkg_manager = "npm"
                if (working_dir / "pnpm-lock.yaml").exists() or (repo_dir / "pnpm-lock.yaml").exists():
                    pkg_manager = "pnpm"
                elif (working_dir / "yarn.lock").exists() or (repo_dir / "yarn.lock").exists():
                    pkg_manager = "yarn"

                # Install dependencies
                logger.info(f"[MCP Installer] Installing dependencies with {pkg_manager} for '{name}'...")
                install_result = subprocess.run(
                    [pkg_manager, "install"],
                    cwd=working_dir,
                    capture_output=True,
                    timeout=300,
                )
                if install_result.returncode != 0:
                    logger.warning(
                        f"[MCP Installer] {pkg_manager} install had issues: {install_result.stderr.decode()[:200]}"
                    )

                # Run build if present (needed for TypeScript projects)
                if "build" in scripts:
                    logger.info(f"[MCP Installer] Running build script for '{name}'...")
                    build_result = subprocess.run(
                        [pkg_manager, "run", "build"],
                        cwd=working_dir,
                        capture_output=True,
                        timeout=300,
                    )
                    if build_result.returncode != 0:
                        logger.warning(f"[MCP Installer] Build had issues: {build_result.stderr.decode()[:200]}")

                # Determine how to start the server
                # Priority: smithery.yaml > start script > bin > main

                if smithery_config:
                    # Use smithery.yaml hints
                    cmd = smithery_config.get("command", "node")
                    args = smithery_config.get("args", [])
                    if isinstance(args, str):
                        args = args.split()
                    server.command = cmd
                    server.args = args
                    # Handle env vars from smithery config
                    config_schema = smithery_config.get("configSchema", {})
                    # Note: We don't auto-populate env vars, user must provide them
                    logger.info(f"[MCP Installer] Using smithery.yaml: {cmd} {' '.join(args)}")

                elif "start" in scripts:
                    server.command = pkg_manager
                    silent_flag = "--silent" if pkg_manager == "npm" else ""
                    server.args = ["run", silent_flag, "start"] if silent_flag else ["run", "start"]
                    server.args = [a for a in server.args if a]  # Remove empty strings

                else:
                    # Find the entry point
                    main = pkg.get("main", "")
                    bin_entry = pkg.get("bin")

                    # Get bin entry (could be string or dict)
                    if isinstance(bin_entry, str):
                        main = bin_entry
                    elif isinstance(bin_entry, dict):
                        # Use the first bin entry
                        main = next(iter(bin_entry.values()), main)

                    # Default fallbacks
                    if not main:
                        # Check common locations
                        for candidate in ["dist/index.js", "build/index.js", "lib/index.js", "index.js"]:
                            if (working_dir / candidate).exists():
                                main = candidate
                                break

                    if not main:
                        main = "index.js"

                    # Verify the entry point exists
                    entry_path = working_dir / main
                    if not entry_path.exists():
                        logger.warning(f"[MCP Installer] Entry point not found: {entry_path}")
                        # Try common build outputs
                        for candidate in ["dist/index.js", "build/index.js", "lib/index.js"]:
                            if (working_dir / candidate).exists():
                                main = candidate
                                logger.info(f"[MCP Installer] Found alternative entry: {candidate}")
                                break

                    server.command = "node"
                    server.args = [main]

                server.display_name = pkg.get("name", name)
                return server

            except Exception as e:
                logger.warning(f"[MCP Installer] Failed to configure Node.js project: {e}")

        # Check for pyproject.toml (Python with poetry/uv)
        pyproject = working_dir / "pyproject.toml"
        if pyproject.exists():
            try:
                # Try to install with uv first, fall back to pip
                uv_path = shutil.which("uv")
                if uv_path:
                    logger.info(f"[MCP Installer] Installing Python dependencies with uv for '{name}'...")
                    subprocess.run(
                        ["uv", "pip", "install", "."],
                        cwd=working_dir,
                        capture_output=True,
                        timeout=300,
                    )
                    server.command = "uv"
                    server.args = ["run", "python", "-m", name.replace("_", "-")]
                else:
                    logger.info(f"[MCP Installer] Installing Python dependencies with pip for '{name}'...")
                    subprocess.run(
                        ["pip", "install", "."],
                        cwd=working_dir,
                        capture_output=True,
                        timeout=300,
                    )
                    server.command = "python"
                    server.args = ["-m", name.replace("_", "-")]

                return server

            except Exception as e:
                logger.warning(f"[MCP Installer] Failed to setup Python project: {e}")

        # Check for setup.py (older Python)
        setup_py = working_dir / "setup.py"
        if setup_py.exists():
            try:
                logger.info(f"[MCP Installer] Installing Python package with pip for '{name}'...")
                subprocess.run(
                    ["pip", "install", "."],
                    cwd=working_dir,
                    capture_output=True,
                    timeout=300,
                )
                server.command = "python"
                server.args = ["-m", name.replace("_", "-")]
                return server

            except Exception as e:
                logger.warning(f"[MCP Installer] Failed to setup Python project: {e}")

        return None

    async def _install_docker(
        self,
        image: str,
        name: str | None,
        env: dict[str, str] | None,
        installed_by: str | None,
    ) -> InstallResult:
        """Install an MCP server from a Docker image.

        Pulls the image and configures HTTP transport.

        Args:
            image: Docker image name
            name: Optional custom name
            env: Optional environment variables
            installed_by: Optional user ID
        """
        server_name = name or self._generate_name(image, "docker")

        # Check if server already exists
        if self._server_exists(server_name):
            return InstallResult(success=False, error=f"Server '{server_name}' already exists")

        # Check if Docker is available
        try:
            import docker

            client = docker.from_env()
        except Exception as e:
            return InstallResult(
                success=False,
                error=f"Docker not available: {e}",
            )

        try:
            # Pull the image
            logger.info(f"[MCP Installer] Pulling Docker image '{image}'...")
            client.images.pull(image)

            # For Docker, we'll use HTTP transport
            # The container needs to expose an MCP endpoint
            port = 8765  # Default MCP port

            docker_config = {
                "image": image,
                "port": port,
                "auto_start": True,
            }
            if env:
                docker_config["environment"] = env

            server = MCPServerConfig(
                name=server_name,
                source_type="docker",
                display_name=image.split("/")[-1].split(":")[0],
                source_url=image,
                transport="streamable-http",
                endpoint_url=f"http://localhost:{port}/mcp",
                docker_config=docker_config,
                env=env or {},
                installed_by=installed_by,
            )

            # Note: We don't test Docker servers automatically since they require
            # container management that's handled separately
            logger.warning(
                f"[MCP Installer] Docker server '{server_name}' configured but not tested. "
                "Container management is required separately."
            )

            # Save config
            self._save_server(server)

            return InstallResult(
                success=True,
                server=server,
                tools_discovered=0,  # Will be discovered when container starts
            )

        except Exception as e:
            return InstallResult(success=False, error=str(e))

    async def _install_local(
        self,
        path: str,
        name: str | None,
        env: dict[str, str] | None,
        installed_by: str | None,
    ) -> InstallResult:
        """Install an MCP server from a local path.

        Args:
            path: Path to the MCP server directory
            name: Optional custom name
            env: Optional environment variables
            installed_by: Optional user ID
        """
        local_path = Path(path).expanduser().resolve()
        server_name = name or self._generate_name(path, "local")

        if not local_path.exists():
            return InstallResult(success=False, error=f"Path does not exist: {local_path}")

        # Check if server already exists
        if self._server_exists(server_name):
            return InstallResult(success=False, error=f"Server '{server_name}' already exists")

        server = MCPServerConfig(
            name=server_name,
            source_type="local",
            source_url=str(local_path),
            transport="stdio",
            cwd=str(local_path),
            env=env or {},
            installed_by=installed_by,
        )

        # Try to detect how to run it
        configured = await self._configure_github_server(local_path, server_name, str(local_path), env, installed_by)

        if configured:
            server = configured
            server.source_type = "local"

        # Test the connection
        logger.info(f"[MCP Installer] Testing local server '{server_name}'...")
        test_result = await self._test_server(server)

        if not test_result["success"]:
            return InstallResult(
                success=False,
                error=f"Server test failed: {test_result.get('error', 'Unknown error')}",
            )

        # Save config
        server.set_tools(test_result.get("tools", []))
        self._save_server(server)

        logger.info(f"[MCP Installer] Installed local server '{server_name}' with {server.tool_count} tools")
        return InstallResult(
            success=True,
            server=server,
            tools_discovered=server.tool_count,
        )

    async def _test_server(self, server: MCPServerConfig, timeout: float = 30.0) -> dict[str, Any]:
        """Test an MCP server connection.

        Args:
            server: Server configuration to test
            timeout: Connection timeout in seconds

        Returns:
            Dict with 'success', 'tools', and optionally 'error'
        """
        client = MCPClient(server)

        try:
            # Try to connect with a timeout
            connected = await asyncio.wait_for(client.connect(), timeout=timeout)

            if not connected:
                return {
                    "success": False,
                    "error": client.state.last_error or "Connection failed",
                }

            tools = [t.to_dict() for t in client.get_tools()]
            await client.disconnect()

            return {
                "success": True,
                "tools": tools,
            }

        except asyncio.TimeoutError:
            return {
                "success": False,
                "error": f"Connection timed out after {timeout}s",
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
            }
        finally:
            await client.disconnect()

    async def uninstall(self, server_name: str) -> bool:
        """Uninstall an MCP server.

        Removes config and cleans up any local files.

        Args:
            server_name: Name of the server to uninstall

        Returns:
            True if successful
        """
        # Load config to check source type
        if USE_DATABASE:
            servers = self._list_servers_from_db()
            server = next((s for s in servers if s.name == server_name), None)
        else:
            server = load_server_config(server_name)

        if not server:
            logger.warning(f"[MCP Installer] Server '{server_name}' not found")
            return False

        # Cleanup local files for GitHub installs
        server_dir = get_server_dir(server_name)
        if server_dir.exists():
            logger.info(f"[MCP Installer] Removing server directory: {server_dir}")
            shutil.rmtree(server_dir, ignore_errors=True)

        # Delete config
        self._delete_server(server_name)

        logger.info(f"[MCP Installer] Uninstalled server '{server_name}'")
        return True

    def list_installed(self) -> list[dict[str, Any]]:
        """List all installed MCP servers.

        Returns:
            List of server info dicts
        """
        servers = self._list_servers()
        return [
            {
                "name": s.name,
                "display_name": s.display_name,
                "source_type": s.source_type,
                "source_url": s.source_url,
                "transport": s.transport,
                "enabled": s.enabled,
                "status": s.status,
                "tool_count": s.tool_count,
                "installed_by": s.installed_by,
                "created_at": s.created_at,
            }
            for s in servers
        ]
