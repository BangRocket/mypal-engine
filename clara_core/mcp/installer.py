"""MCP Server Installer for installing servers from various sources.

This module provides functionality to install MCP servers from:
- npm packages (e.g., @modelcontextprotocol/server-everything)
- GitHub repositories
- Docker images
- Local paths
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from db import SessionLocal

from .client import MCPClient
from .models import MCPServer

logger = logging.getLogger(__name__)

# Directory for storing cloned repos and built servers
MCP_SERVERS_DIR = Path(os.getenv("MCP_SERVERS_DIR", ".mcp_servers"))


@dataclass
class InstallResult:
    """Result of an MCP server installation."""

    success: bool
    server: MCPServer | None = None
    error: str | None = None
    tools_discovered: int = 0


class MCPInstaller:
    """Installer for MCP servers from various sources."""

    def __init__(self) -> None:
        """Initialize the installer."""
        self._ensure_servers_dir()

    def _ensure_servers_dir(self) -> None:
        """Ensure the MCP servers directory exists."""
        MCP_SERVERS_DIR.mkdir(parents=True, exist_ok=True)

    async def install(
        self,
        source: str,
        name: str | None = None,
        env: dict[str, str] | None = None,
        installed_by: str | None = None,
    ) -> InstallResult:
        """Install an MCP server from a source.

        Auto-detects source type from the input:
        - npm package: Starts with @ or contains no / or .
        - GitHub: Contains github.com or is owner/repo format
        - Docker: Contains docker.io or other registry patterns
        - Local: Starts with / or ./ or ~ (path)

        Args:
            source: Source to install from (npm package, GitHub URL, Docker image, or path)
            name: Optional custom name for the server (auto-detected if not provided)
            env: Optional environment variables for the server
            installed_by: Optional user ID who installed this server

        Returns:
            InstallResult with success status and server info
        """
        source_type = self._detect_source_type(source)
        logger.info(f"[MCP Installer] Installing from {source_type}: {source}")

        if source_type == "npm":
            return await self._install_npm(source, name, env, installed_by)
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
            One of: "npm", "github", "docker", "local"
        """
        source = source.strip()

        # Local path
        if source.startswith(("/", "./", "~", "../")):
            return "local"

        # GitHub URL
        if "github.com" in source or re.match(r"^[a-zA-Z0-9_-]+/[a-zA-Z0-9_-]+$", source):
            return "github"

        # Docker image
        if any(
            x in source
            for x in ["docker.io", "ghcr.io", "gcr.io", "quay.io", "registry.", "amazonaws.com"]
        ):
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
        if source_type == "npm":
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
    ) -> InstallResult:
        """Install an npm MCP server.

        Uses npx to run the server without global installation.

        Args:
            package: npm package name (e.g., @modelcontextprotocol/server-everything)
            name: Optional custom name
            env: Optional environment variables
            installed_by: Optional user ID
        """
        server_name = name or self._generate_name(package, "npm")

        # Check if server already exists
        with SessionLocal() as session:
            existing = session.query(MCPServer).filter(MCPServer.name == server_name).first()
            if existing:
                return InstallResult(success=False, error=f"Server '{server_name}' already exists")

        # Check if npx is available
        npx_path = shutil.which("npx")
        if not npx_path:
            return InstallResult(
                success=False,
                error="npx not found. Please install Node.js and npm.",
            )

        # Create server configuration
        server = MCPServer(
            name=server_name,
            display_name=package.split("/")[-1],
            source_type="npm",
            source_url=package,
            transport="stdio",
            command="npx",
            installed_by=installed_by,
        )
        server.set_args(["-y", package])
        if env:
            server.set_env(env)

        # Test the connection
        logger.info(f"[MCP Installer] Testing npm server '{server_name}'...")
        test_result = await self._test_server(server)

        if not test_result["success"]:
            return InstallResult(
                success=False,
                error=f"Server test failed: {test_result.get('error', 'Unknown error')}",
            )

        # Save to database
        server.set_tools(test_result.get("tools", []))
        with SessionLocal() as session:
            session.add(server)
            session.commit()
            session.refresh(server)
            session.expunge(server)

        logger.info(f"[MCP Installer] Installed npm server '{server_name}' with {server.tool_count} tools")
        return InstallResult(
            success=True,
            server=server,
            tools_discovered=server.tool_count,
        )

    async def _install_github(
        self,
        source: str,
        name: str | None,
        env: dict[str, str] | None,
        installed_by: str | None,
    ) -> InstallResult:
        """Install an MCP server from a GitHub repository.

        Clones the repo and detects how to run it based on config files.

        Args:
            source: GitHub URL or owner/repo
            name: Optional custom name
            env: Optional environment variables
            installed_by: Optional user ID
        """
        server_name = name or self._generate_name(source, "github")

        # Check if server already exists
        with SessionLocal() as session:
            existing = session.query(MCPServer).filter(MCPServer.name == server_name).first()
            if existing:
                return InstallResult(success=False, error=f"Server '{server_name}' already exists")

        # Normalize GitHub URL
        if not source.startswith(("http://", "https://", "git@")):
            source = f"https://github.com/{source}"
        if not source.endswith(".git"):
            source = f"{source}.git"

        # Clone directory
        clone_dir = MCP_SERVERS_DIR / server_name

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

            # Save to database
            server.set_tools(test_result.get("tools", []))
            with SessionLocal() as session:
                session.add(server)
                session.commit()
                session.refresh(server)
                session.expunge(server)

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

    async def _configure_github_server(
        self,
        repo_dir: Path,
        name: str,
        source_url: str,
        env: dict[str, str] | None,
        installed_by: str | None,
    ) -> MCPServer | None:
        """Configure an MCP server from a cloned GitHub repo.

        Detects project type and sets up the appropriate run command.

        Args:
            repo_dir: Path to the cloned repository
            name: Server name
            source_url: Original GitHub URL
            env: Environment variables
            installed_by: User who installed

        Returns:
            Configured MCPServer or None if couldn't detect how to run
        """
        server = MCPServer(
            name=name,
            source_type="github",
            source_url=source_url,
            transport="stdio",
            cwd=str(repo_dir),
            installed_by=installed_by,
        )
        if env:
            server.set_env(env)

        # Check for package.json (Node.js)
        package_json = repo_dir / "package.json"
        if package_json.exists():
            try:
                with open(package_json) as f:
                    pkg = json.load(f)

                # Install dependencies
                logger.info(f"[MCP Installer] Installing npm dependencies for '{name}'...")
                subprocess.run(
                    ["npm", "install"],
                    cwd=repo_dir,
                    capture_output=True,
                    timeout=300,
                )

                # Look for main entry or start script
                main = pkg.get("main", "index.js")
                scripts = pkg.get("scripts", {})

                if "start" in scripts:
                    server.command = "npm"
                    server.set_args(["run", "start"])
                else:
                    server.command = "node"
                    server.set_args([main])

                server.display_name = pkg.get("name", name)
                return server

            except Exception as e:
                logger.warning(f"[MCP Installer] Failed to parse package.json: {e}")

        # Check for pyproject.toml (Python with poetry/uv)
        pyproject = repo_dir / "pyproject.toml"
        if pyproject.exists():
            try:
                # Try to install with uv first, fall back to pip
                uv_path = shutil.which("uv")
                if uv_path:
                    logger.info(f"[MCP Installer] Installing Python dependencies with uv for '{name}'...")
                    subprocess.run(
                        ["uv", "pip", "install", "."],
                        cwd=repo_dir,
                        capture_output=True,
                        timeout=300,
                    )
                    server.command = "uv"
                    server.set_args(["run", "python", "-m", name.replace("_", "-")])
                else:
                    logger.info(f"[MCP Installer] Installing Python dependencies with pip for '{name}'...")
                    subprocess.run(
                        ["pip", "install", "."],
                        cwd=repo_dir,
                        capture_output=True,
                        timeout=300,
                    )
                    server.command = "python"
                    server.set_args(["-m", name.replace("_", "-")])

                return server

            except Exception as e:
                logger.warning(f"[MCP Installer] Failed to setup Python project: {e}")

        # Check for setup.py (older Python)
        setup_py = repo_dir / "setup.py"
        if setup_py.exists():
            try:
                logger.info(f"[MCP Installer] Installing Python package with pip for '{name}'...")
                subprocess.run(
                    ["pip", "install", "."],
                    cwd=repo_dir,
                    capture_output=True,
                    timeout=300,
                )
                server.command = "python"
                server.set_args(["-m", name.replace("_", "-")])
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
        with SessionLocal() as session:
            existing = session.query(MCPServer).filter(MCPServer.name == server_name).first()
            if existing:
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

            server = MCPServer(
                name=server_name,
                display_name=image.split("/")[-1].split(":")[0],
                source_type="docker",
                source_url=image,
                transport="streamable-http",
                endpoint_url=f"http://localhost:{port}/mcp",
                installed_by=installed_by,
            )

            docker_config = {
                "image": image,
                "port": port,
                "auto_start": True,
            }
            if env:
                docker_config["environment"] = env

            server.set_docker_config(docker_config)
            if env:
                server.set_env(env)

            # Note: We don't test Docker servers automatically since they require
            # container management that's handled separately
            logger.warning(
                f"[MCP Installer] Docker server '{server_name}' configured but not tested. "
                "Container management is required separately."
            )

            # Save to database
            with SessionLocal() as session:
                session.add(server)
                session.commit()
                session.refresh(server)
                session.expunge(server)

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
        with SessionLocal() as session:
            existing = session.query(MCPServer).filter(MCPServer.name == server_name).first()
            if existing:
                return InstallResult(success=False, error=f"Server '{server_name}' already exists")

        server = MCPServer(
            name=server_name,
            source_type="local",
            source_url=str(local_path),
            transport="stdio",
            cwd=str(local_path),
            installed_by=installed_by,
        )
        if env:
            server.set_env(env)

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

        # Save to database
        server.set_tools(test_result.get("tools", []))
        with SessionLocal() as session:
            session.add(server)
            session.commit()
            session.refresh(server)
            session.expunge(server)

        logger.info(f"[MCP Installer] Installed local server '{server_name}' with {server.tool_count} tools")
        return InstallResult(
            success=True,
            server=server,
            tools_discovered=server.tool_count,
        )

    async def _test_server(self, server: MCPServer, timeout: float = 30.0) -> dict[str, Any]:
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

        Removes from database and cleans up any local files.

        Args:
            server_name: Name of the server to uninstall

        Returns:
            True if successful
        """
        with SessionLocal() as session:
            server = session.query(MCPServer).filter(MCPServer.name == server_name).first()
            if not server:
                logger.warning(f"[MCP Installer] Server '{server_name}' not found")
                return False

            # Cleanup local files for GitHub installs
            if server.source_type == "github":
                clone_dir = MCP_SERVERS_DIR / server_name
                if clone_dir.exists():
                    logger.info(f"[MCP Installer] Removing cloned repo: {clone_dir}")
                    shutil.rmtree(clone_dir, ignore_errors=True)

            session.delete(server)
            session.commit()

        logger.info(f"[MCP Installer] Uninstalled server '{server_name}'")
        return True

    def list_installed(self) -> list[dict[str, Any]]:
        """List all installed MCP servers.

        Returns:
            List of server info dicts
        """
        with SessionLocal() as session:
            servers = session.query(MCPServer).all()
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
                    "created_at": s.created_at.isoformat() if s.created_at else None,
                }
                for s in servers
            ]
