"""MCP Server Installer for installing servers from various sources.

This module provides functionality to install MCP servers from:
- npm packages (e.g., @modelcontextprotocol/server-everything)
- Smithery registry (local stdio or hosted HTTP)
- GitHub repositories
- Docker images
- Local paths

Local servers are saved to .mcp_servers/local/{name}/
Remote servers are saved to .mcp_servers/remote/{name}/
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

from .local_server import LocalServerProcess, MCPTool
from .models import (
    MCP_SERVERS_DIR,
    LocalServerConfig,
    RemoteServerConfig,
    delete_local_server_config,
    delete_remote_server_config,
    get_local_server_dir,
    get_local_servers_dir,
    list_local_server_configs,
    list_remote_server_configs,
    load_local_server_config,
    load_remote_server_config,
    save_local_server_config,
    save_remote_server_config,
)

logger = logging.getLogger(__name__)

# Smithery API configuration
SMITHERY_REGISTRY_URL = "https://registry.smithery.ai/servers"
SMITHERY_API_TOKEN = os.getenv("SMITHERY_API_TOKEN", "")
if not SMITHERY_API_TOKEN:
    SMITHERY_API_TOKEN = os.getenv("SMITHERY_API_KEY", "")


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
    server_type: str = "local"  # "local" or "remote"
    local_config: LocalServerConfig | None = None
    remote_config: RemoteServerConfig | None = None
    error: str | None = None
    tools_discovered: int = 0

    # Legacy compatibility
    @property
    def server(self) -> LocalServerConfig | None:
        return self.local_config


class SmitheryClient:
    """Client for interacting with the Smithery registry."""

    def __init__(self, api_token: str | None = None) -> None:
        self.api_token = api_token or SMITHERY_API_TOKEN

    async def search(
        self,
        query: str,
        page: int = 1,
        page_size: int = 10,
    ) -> SmitherySearchResult:
        """Search the Smithery registry for MCP servers."""
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
                        return SmitherySearchResult(
                            error=f"Smithery API error ({response.status}): {error_text[:200]}"
                        )

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
        """Get a specific server by its qualified name."""
        result = await self.search(qualified_name, page_size=50)
        if result.error:
            logger.warning(f"[Smithery] Search error: {result.error}")
            return None

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
        """Ensure the MCP servers directory structure exists."""
        get_local_servers_dir().mkdir(parents=True, exist_ok=True)

    def _local_server_exists(self, name: str) -> bool:
        """Check if a local server with this name exists."""
        return load_local_server_config(name) is not None

    def _remote_server_exists(self, name: str) -> bool:
        """Check if a remote server with this name exists."""
        return load_remote_server_config(name) is not None

    def _server_exists(self, name: str) -> bool:
        """Check if any server with this name exists."""
        return self._local_server_exists(name) or self._remote_server_exists(name)

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
        - Smithery local: Prefix with "smithery:" (e.g., smithery:e2b)
        - Smithery hosted: Prefix with "smithery-hosted:" (HTTP transport)
        - npm package: Starts with @ or contains no / or .
        - GitHub: Contains github.com or is owner/repo format
        - Docker: Contains docker.io or other registry patterns
        - Local: Starts with / or ./ or ~ (path)

        Args:
            source: Source to install from
            name: Optional custom name for the server
            env: Optional environment variables
            installed_by: Optional user ID who installed this
            args: Optional extra command-line arguments

        Returns:
            InstallResult with success status and server info
        """
        source_type = self._detect_source_type(source)
        logger.info(f"[MCP Installer] Installing from {source_type}: {source}")

        if source_type == "smithery-hosted":
            smithery_name = source[16:] if source.startswith("smithery-hosted:") else source
            return await self._install_smithery_hosted(smithery_name, name, env, installed_by)
        elif source_type == "smithery":
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
        """Detect the type of source."""
        source = source.strip()

        if source.startswith("smithery-hosted:"):
            return "smithery-hosted"

        if source.startswith("smithery:"):
            return "smithery"

        if source.startswith(("/", "./", "~", "../")):
            return "local"

        if "github.com" in source or re.match(r"^[a-zA-Z0-9_-]+/[a-zA-Z0-9_-]+$", source):
            return "github"

        if any(
            x in source
            for x in ["docker.io", "ghcr.io", "gcr.io", "quay.io", "registry.", "amazonaws.com"]
        ):
            return "docker"

        if source.startswith("@") or re.match(r"^[a-zA-Z0-9_-]+$", source):
            return "npm"

        if "/" not in source and "." not in source:
            return "npm"

        return "unknown"

    def _generate_name(self, source: str, source_type: str) -> str:
        """Generate a server name from the source."""
        if source_type in ("smithery", "smithery-hosted"):
            name = source.split("/")[-1]
            for prefix in ["mcp-server-", "mcp-", "server-"]:
                if name.startswith(prefix):
                    name = name[len(prefix) :]
                    break
            return name.replace("-", "_")

        elif source_type == "npm":
            name = source.split("/")[-1]
            for prefix in ["mcp-server-", "mcp-", "server-"]:
                if name.startswith(prefix):
                    name = name[len(prefix) :]
                    break
            return name.replace("-", "_")

        elif source_type == "github":
            parts = source.rstrip("/").split("/")
            name = parts[-1]
            if name.endswith(".git"):
                name = name[:-4]
            return name.replace("-", "_")

        elif source_type == "docker":
            name = source.split("/")[-1]
            name = name.split(":")[0]
            return name.replace("-", "_")

        elif source_type == "local":
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
        """Install an npm MCP server."""
        server_name = name or self._generate_name(package, "npm")

        if self._server_exists(server_name):
            return InstallResult(success=False, error=f"Server '{server_name}' already exists")

        npx_path = shutil.which("npx")
        if not npx_path:
            return InstallResult(
                success=False, error="npx not found. Please install Node.js and npm."
            )

        server_args = ["-y", "-q", package]
        if extra_args:
            server_args.extend(extra_args)

        server = LocalServerConfig(
            name=server_name,
            command="npx",
            args=server_args,
            source_type="npm",
            display_name=package.split("/")[-1],
            source_url=package,
            env=env or {},
            installed_by=installed_by,
        )

        logger.info(f"[MCP Installer] Testing npm server '{server_name}'...")
        test_result = await self._test_local_server(server)

        if not test_result["success"]:
            return InstallResult(
                success=False,
                error=f"Server test failed: {test_result.get('error', 'Unknown error')}",
            )

        server.set_tools(test_result.get("tools", []))
        save_local_server_config(server)

        logger.info(f"[MCP Installer] Installed npm server '{server_name}' with {server.tool_count} tools")
        return InstallResult(
            success=True,
            server_type="local",
            local_config=server,
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
        """Install a local Smithery MCP server (stdio transport)."""
        server_name = name or self._generate_name(package, "smithery")

        if self._server_exists(server_name):
            return InstallResult(success=False, error=f"Server '{server_name}' already exists")

        npx_path = shutil.which("npx")
        if not npx_path:
            return InstallResult(
                success=False, error="npx not found. Please install Node.js and npm."
            )

        smithery = SmitheryClient()
        smithery_server = await smithery.get_server(package)

        display_name = package.split("/")[-1]
        if smithery_server:
            display_name = smithery_server.display_name or display_name
            logger.info(f"[MCP Installer] Found Smithery server: {smithery_server.display_name}")

        server_args = ["-y", "-q", "@smithery/cli@latest", "run", package, "--"]
        if extra_args:
            server_args.extend(extra_args)

        server_env = dict(env or {})
        if SMITHERY_API_TOKEN:
            server_env["SMITHERY_API_KEY"] = SMITHERY_API_TOKEN

        server = LocalServerConfig(
            name=server_name,
            command="npx",
            args=server_args,
            source_type="smithery",
            display_name=display_name,
            source_url=f"smithery:{package}",
            env=server_env,
            installed_by=installed_by,
        )

        logger.info(f"[MCP Installer] Testing Smithery server '{server_name}'...")
        test_result = await self._test_local_server(server)

        if not test_result["success"]:
            # Try fallback: direct npm
            logger.info("[MCP Installer] Smithery CLI failed, trying direct npm...")
            npm_args = ["-y", "-q", package]
            if extra_args:
                npm_args.extend(extra_args)

            server.args = npm_args
            test_result = await self._test_local_server(server)

            if not test_result["success"]:
                return InstallResult(
                    success=False,
                    error=f"Server test failed: {test_result.get('error', 'Unknown error')}",
                )

        server.set_tools(test_result.get("tools", []))
        save_local_server_config(server)

        logger.info(f"[MCP Installer] Installed Smithery server '{server_name}' with {server.tool_count} tools")
        return InstallResult(
            success=True,
            server_type="local",
            local_config=server,
            tools_discovered=server.tool_count,
        )

    async def _install_smithery_hosted(
        self,
        package: str,
        name: str | None,
        env: dict[str, str] | None,
        installed_by: str | None,
    ) -> InstallResult:
        """Install a hosted Smithery MCP server (HTTP transport)."""
        from .oauth import get_smithery_server_url

        server_name = name or self._generate_name(package, "smithery")

        if self._server_exists(server_name):
            return InstallResult(success=False, error=f"Server '{server_name}' already exists")

        server_url = get_smithery_server_url(package)

        smithery = SmitheryClient()
        smithery_server = await smithery.get_server(package)

        display_name = package.split("/")[-1]
        if smithery_server:
            display_name = smithery_server.display_name or display_name
            logger.info(f"[MCP Installer] Found Smithery hosted server: {display_name}")

        # Build headers from env
        headers = {}
        access_token = (env or {}).get("SMITHERY_ACCESS_TOKEN")
        if access_token:
            headers["Authorization"] = f"Bearer {access_token}"

        server = RemoteServerConfig(
            name=server_name,
            server_url=server_url,
            headers=headers,
            source_type="smithery-hosted",
            display_name=display_name,
            source_url=f"smithery-hosted:{package}",
            oauth_required=not bool(access_token),
            oauth_server_url=server_url,
            installed_by=installed_by,
        )

        if access_token:
            logger.info(f"[MCP Installer] Testing hosted server '{server_name}' with token...")
            test_result = await self._test_remote_server(server)

            if test_result["success"]:
                server.set_tools(test_result.get("tools", []))
                save_remote_server_config(server)

                logger.info(
                    f"[MCP Installer] Installed hosted server '{server_name}' "
                    f"with {server.tool_count} tools"
                )
                return InstallResult(
                    success=True,
                    server_type="remote",
                    remote_config=server,
                    tools_discovered=server.tool_count,
                )
            else:
                logger.warning(f"[MCP Installer] Connection test failed: {test_result.get('error')}")

        # No token or test failed - set up for OAuth
        server.status = "pending_auth"
        server.last_error = "OAuth authentication required. Use mcp_oauth_start to begin authorization."
        save_remote_server_config(server)

        logger.info(
            f"[MCP Installer] Installed hosted server '{server_name}' (pending OAuth). "
            "User must complete OAuth flow to connect."
        )

        return InstallResult(
            success=True,
            server_type="remote",
            remote_config=server,
            tools_discovered=0,
            error="OAuth authentication required. Use mcp_oauth_start to begin the authorization flow.",
        )

    def _extract_github_owner_repo(self, source: str) -> tuple[str, str] | None:
        """Extract owner/repo from a GitHub URL or shorthand."""
        source = source.rstrip("/")
        if source.endswith(".git"):
            source = source[:-4]

        if "github.com" in source:
            if "github.com/" in source:
                parts = source.split("github.com/")[-1].split("/")
            elif "github.com:" in source:
                parts = source.split("github.com:")[-1].split("/")
            else:
                return None

            if len(parts) >= 2:
                return (parts[0], parts[1])

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
        """Install an MCP server from a GitHub repository."""
        server_name = name or self._generate_name(source, "github")

        if self._server_exists(server_name):
            return InstallResult(success=False, error=f"Server '{server_name}' already exists")

        owner_repo = self._extract_github_owner_repo(source)

        if owner_repo:
            npx_path = shutil.which("npx")
            if npx_path:
                github_shorthand = f"github:{owner_repo[0]}/{owner_repo[1]}"
                logger.info(f"[MCP Installer] Trying npx {github_shorthand}...")

                server = LocalServerConfig(
                    name=server_name,
                    command="npx",
                    args=["-y", "-q", github_shorthand],
                    source_type="github",
                    display_name=owner_repo[1],
                    source_url=f"https://github.com/{owner_repo[0]}/{owner_repo[1]}",
                    env=env or {},
                    installed_by=installed_by,
                )

                test_result = await self._test_local_server(server)

                if test_result["success"]:
                    server.set_tools(test_result.get("tools", []))
                    save_local_server_config(server)

                    logger.info(
                        f"[MCP Installer] Installed GitHub server '{server_name}' via npx "
                        f"with {server.tool_count} tools"
                    )
                    return InstallResult(
                        success=True,
                        server_type="local",
                        local_config=server,
                        tools_discovered=server.tool_count,
                    )
                else:
                    logger.info(
                        f"[MCP Installer] npx approach failed ({test_result.get('error')}), "
                        "trying clone-and-build..."
                    )

        # Fall back to clone-and-build
        if not source.startswith(("http://", "https://", "git@")):
            source = f"https://github.com/{source}"
        if not source.endswith(".git"):
            source = f"{source}.git"

        clone_dir = get_local_server_dir(server_name)

        try:
            logger.info(f"[MCP Installer] Cloning {source}...")
            result = subprocess.run(
                ["git", "clone", "--depth", "1", source, str(clone_dir)],
                capture_output=True,
                text=True,
                timeout=120,
            )

            if result.returncode != 0:
                return InstallResult(success=False, error=f"Git clone failed: {result.stderr}")

            server = await self._configure_github_server(
                clone_dir, server_name, source, env, installed_by
            )

            if not server:
                shutil.rmtree(clone_dir, ignore_errors=True)
                return InstallResult(
                    success=False,
                    error="Could not detect how to run this MCP server. "
                    "Expected package.json (Node.js) or pyproject.toml/setup.py (Python).",
                )

            logger.info(f"[MCP Installer] Testing GitHub server '{server_name}'...")
            test_result = await self._test_local_server(server)

            if not test_result["success"]:
                shutil.rmtree(clone_dir, ignore_errors=True)
                return InstallResult(
                    success=False,
                    error=f"Server test failed: {test_result.get('error', 'Unknown error')}",
                )

            server.set_tools(test_result.get("tools", []))
            save_local_server_config(server)

            logger.info(f"[MCP Installer] Installed GitHub server '{server_name}' with {server.tool_count} tools")
            return InstallResult(
                success=True,
                server_type="local",
                local_config=server,
                tools_discovered=server.tool_count,
            )

        except subprocess.TimeoutExpired:
            shutil.rmtree(clone_dir, ignore_errors=True)
            return InstallResult(success=False, error="Git clone timed out")
        except Exception as e:
            shutil.rmtree(clone_dir, ignore_errors=True)
            return InstallResult(success=False, error=str(e))

    def _find_monorepo_package(self, repo_dir: Path) -> Path | None:
        """Find the actual MCP server package in a monorepo."""
        is_monorepo = False
        workspace_dirs = []

        pnpm_workspace = repo_dir / "pnpm-workspace.yaml"
        if pnpm_workspace.exists():
            is_monorepo = True
            workspace_dirs = ["packages", "apps"]

        package_json = repo_dir / "package.json"
        if package_json.exists():
            try:
                with open(package_json) as f:
                    pkg = json.load(f)
                workspaces = pkg.get("workspaces", [])
                if workspaces:
                    is_monorepo = True
                    for ws in workspaces:
                        if isinstance(ws, str):
                            ws_dir = ws.replace("/*", "").replace("/**", "")
                            if ws_dir and not ws_dir.startswith("!"):
                                workspace_dirs.append(ws_dir)
            except Exception:
                pass

        lerna_json = repo_dir / "lerna.json"
        if lerna_json.exists():
            is_monorepo = True
            workspace_dirs.extend(["packages", "apps"])

        if not is_monorepo:
            return None

        workspace_dirs = list(set(workspace_dirs + ["packages", "apps"]))

        for ws_dir in workspace_dirs:
            ws_path = repo_dir / ws_dir
            if not ws_path.exists():
                continue

            for pkg_dir in ws_path.iterdir():
                if not pkg_dir.is_dir():
                    continue

                pkg_json = pkg_dir / "package.json"
                if pkg_json.exists():
                    try:
                        with open(pkg_json) as f:
                            pkg = json.load(f)

                        pkg_name = pkg.get("name", "").lower()
                        pkg_desc = pkg.get("description", "").lower()
                        pkg_keywords = [k.lower() for k in pkg.get("keywords", [])]
                        deps = list(pkg.get("dependencies", {}).keys())
                        deps += list(pkg.get("devDependencies", {}).keys())

                        is_mcp = (
                            "mcp" in pkg_name
                            or "mcp" in pkg_desc
                            or "mcp" in pkg_keywords
                            or "@modelcontextprotocol/sdk" in deps
                            or "mcp-server" in pkg_name
                        )

                        if pkg_dir.name in ("js", "javascript", "typescript", "node"):
                            is_mcp = True

                        if is_mcp:
                            logger.info(f"[MCP Installer] Found MCP package in monorepo: {pkg_dir}")
                            return pkg_dir

                    except Exception:
                        continue

        return None

    def _parse_smithery_yaml(self, repo_dir: Path) -> dict[str, Any] | None:
        """Parse smithery.yaml for server configuration hints."""
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
    ) -> LocalServerConfig | None:
        """Configure an MCP server from a cloned GitHub repo."""
        monorepo_pkg = self._find_monorepo_package(repo_dir)
        if monorepo_pkg:
            logger.info(f"[MCP Installer] Detected monorepo, using package at: {monorepo_pkg}")
            working_dir = monorepo_pkg
        else:
            working_dir = repo_dir

        server = LocalServerConfig(
            name=name,
            command="",
            source_type="github",
            source_url=source_url,
            cwd=str(working_dir),
            env=env or {},
            installed_by=installed_by,
        )

        smithery_config = self._parse_smithery_yaml(repo_dir) or self._parse_smithery_yaml(
            working_dir
        )

        package_json = working_dir / "package.json"
        if package_json.exists():
            try:
                with open(package_json) as f:
                    pkg = json.load(f)

                scripts = pkg.get("scripts", {})

                pkg_manager = "npm"
                if (working_dir / "pnpm-lock.yaml").exists() or (repo_dir / "pnpm-lock.yaml").exists():
                    pkg_manager = "pnpm"
                elif (working_dir / "yarn.lock").exists() or (repo_dir / "yarn.lock").exists():
                    pkg_manager = "yarn"

                logger.info(f"[MCP Installer] Installing dependencies with {pkg_manager}...")
                subprocess.run(
                    [pkg_manager, "install"],
                    cwd=working_dir,
                    capture_output=True,
                    timeout=300,
                )

                if "build" in scripts:
                    logger.info("[MCP Installer] Running build script...")
                    subprocess.run(
                        [pkg_manager, "run", "build"],
                        cwd=working_dir,
                        capture_output=True,
                        timeout=300,
                    )

                if smithery_config:
                    cmd = smithery_config.get("command", "node")
                    args = smithery_config.get("args", [])
                    if isinstance(args, str):
                        args = args.split()
                    server.command = cmd
                    server.args = args
                    logger.info(f"[MCP Installer] Using smithery.yaml: {cmd} {' '.join(args)}")

                elif "start" in scripts:
                    server.command = pkg_manager
                    silent_flag = "--silent" if pkg_manager == "npm" else ""
                    server.args = ["run", silent_flag, "start"] if silent_flag else ["run", "start"]
                    server.args = [a for a in server.args if a]

                else:
                    main = pkg.get("main", "")
                    bin_entry = pkg.get("bin")

                    if isinstance(bin_entry, str):
                        main = bin_entry
                    elif isinstance(bin_entry, dict):
                        main = next(iter(bin_entry.values()), main)

                    if not main:
                        for candidate in ["dist/index.js", "build/index.js", "lib/index.js", "index.js"]:
                            if (working_dir / candidate).exists():
                                main = candidate
                                break

                    if not main:
                        main = "index.js"

                    entry_path = working_dir / main
                    if not entry_path.exists():
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

        pyproject = working_dir / "pyproject.toml"
        if pyproject.exists():
            try:
                uv_path = shutil.which("uv")
                if uv_path:
                    logger.info("[MCP Installer] Installing Python dependencies with uv...")
                    subprocess.run(
                        ["uv", "pip", "install", "."],
                        cwd=working_dir,
                        capture_output=True,
                        timeout=300,
                    )
                    server.command = "uv"
                    server.args = ["run", "python", "-m", name.replace("_", "-")]
                else:
                    logger.info("[MCP Installer] Installing Python dependencies with pip...")
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

        setup_py = working_dir / "setup.py"
        if setup_py.exists():
            try:
                logger.info("[MCP Installer] Installing Python package with pip...")
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
        """Install an MCP server from a Docker image."""
        server_name = name or self._generate_name(image, "docker")

        if self._server_exists(server_name):
            return InstallResult(success=False, error=f"Server '{server_name}' already exists")

        try:
            import docker

            client = docker.from_env()
        except Exception as e:
            return InstallResult(success=False, error=f"Docker not available: {e}")

        try:
            logger.info(f"[MCP Installer] Pulling Docker image '{image}'...")
            client.images.pull(image)

            port = 8765

            server = RemoteServerConfig(
                name=server_name,
                server_url=f"http://localhost:{port}/mcp",
                headers={},
                source_type="docker",
                display_name=image.split("/")[-1].split(":")[0],
                source_url=image,
                installed_by=installed_by,
            )

            logger.warning(
                f"[MCP Installer] Docker server '{server_name}' configured but not tested. "
                "Container management is required separately."
            )

            save_remote_server_config(server)

            return InstallResult(
                success=True,
                server_type="remote",
                remote_config=server,
                tools_discovered=0,
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
        """Install an MCP server from a local path."""
        local_path = Path(path).expanduser().resolve()
        server_name = name or self._generate_name(path, "local")

        if not local_path.exists():
            return InstallResult(success=False, error=f"Path does not exist: {local_path}")

        if self._server_exists(server_name):
            return InstallResult(success=False, error=f"Server '{server_name}' already exists")

        server = LocalServerConfig(
            name=server_name,
            command="",
            source_type="local",
            source_url=str(local_path),
            cwd=str(local_path),
            env=env or {},
            installed_by=installed_by,
        )

        configured = await self._configure_github_server(
            local_path, server_name, str(local_path), env, installed_by
        )

        if configured:
            server = configured
            server.source_type = "local"

        logger.info(f"[MCP Installer] Testing local server '{server_name}'...")
        test_result = await self._test_local_server(server)

        if not test_result["success"]:
            return InstallResult(
                success=False,
                error=f"Server test failed: {test_result.get('error', 'Unknown error')}",
            )

        server.set_tools(test_result.get("tools", []))
        save_local_server_config(server)

        logger.info(f"[MCP Installer] Installed local server '{server_name}' with {server.tool_count} tools")
        return InstallResult(
            success=True,
            server_type="local",
            local_config=server,
            tools_discovered=server.tool_count,
        )

    async def _test_local_server(
        self, server: LocalServerConfig, timeout: float = 30.0
    ) -> dict[str, Any]:
        """Test a local MCP server connection."""
        process = LocalServerProcess(server)

        try:
            connected = await asyncio.wait_for(process.start(), timeout=timeout)

            if not connected:
                return {
                    "success": False,
                    "error": process.state.last_error or "Connection failed",
                }

            tools = [t.to_dict() for t in process.get_tools()]
            await process.stop()

            return {"success": True, "tools": tools}

        except asyncio.TimeoutError:
            return {"success": False, "error": f"Connection timed out after {timeout}s"}
        except Exception as e:
            return {"success": False, "error": str(e)}
        finally:
            await process.stop()

    async def _test_remote_server(
        self, server: RemoteServerConfig, timeout: float = 30.0
    ) -> dict[str, Any]:
        """Test a remote MCP server connection."""
        from .remote_server import RemoteServerConnection

        connection = RemoteServerConnection(server)

        try:
            connected = await asyncio.wait_for(connection.connect(), timeout=timeout)

            if not connected:
                return {
                    "success": False,
                    "error": connection.state.last_error or "Connection failed",
                }

            tools = [t.to_dict() for t in connection.get_tools()]
            await connection.disconnect()

            return {"success": True, "tools": tools}

        except asyncio.TimeoutError:
            return {"success": False, "error": f"Connection timed out after {timeout}s"}
        except Exception as e:
            return {"success": False, "error": str(e)}
        finally:
            await connection.disconnect()

    async def uninstall(self, server_name: str) -> bool:
        """Uninstall an MCP server."""
        # Check local servers
        local_config = load_local_server_config(server_name)
        if local_config:
            server_dir = get_local_server_dir(server_name)
            if server_dir.exists():
                logger.info(f"[MCP Installer] Removing local server directory: {server_dir}")
                shutil.rmtree(server_dir, ignore_errors=True)
            delete_local_server_config(server_name)
            logger.info(f"[MCP Installer] Uninstalled local server '{server_name}'")
            return True

        # Check remote servers
        remote_config = load_remote_server_config(server_name)
        if remote_config:
            delete_remote_server_config(server_name)
            logger.info(f"[MCP Installer] Uninstalled remote server '{server_name}'")
            return True

        logger.warning(f"[MCP Installer] Server '{server_name}' not found")
        return False

    def list_installed(self) -> list[dict[str, Any]]:
        """List all installed MCP servers."""
        servers = []

        # List local servers
        for config in list_local_server_configs():
            servers.append(
                {
                    "name": config.name,
                    "type": "local",
                    "display_name": config.display_name,
                    "source_type": config.source_type,
                    "source_url": config.source_url,
                    "command": config.command,
                    "enabled": config.enabled,
                    "status": config.status,
                    "tool_count": config.tool_count,
                    "hot_reload": config.hot_reload,
                    "installed_by": config.installed_by,
                    "created_at": config.created_at,
                }
            )

        # List remote servers
        for config in list_remote_server_configs():
            servers.append(
                {
                    "name": config.name,
                    "type": "remote",
                    "display_name": config.display_name,
                    "source_type": config.source_type,
                    "source_url": config.source_url,
                    "server_url": config.server_url,
                    "enabled": config.enabled,
                    "status": config.status,
                    "tool_count": config.tool_count,
                    "oauth_required": config.oauth_required,
                    "installed_by": config.installed_by,
                    "created_at": config.created_at,
                }
            )

        return servers
