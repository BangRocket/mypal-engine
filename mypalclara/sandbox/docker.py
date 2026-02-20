"""
Docker-based code execution for Clara.

Provides sandboxed code execution via local Docker containers.
Drop-in replacement for e2b_tools.py with identical interface.

Usage:
    from docker_tools import DockerSandboxManager, DOCKER_TOOLS

    manager = DockerSandboxManager()
    result = await manager.execute_code(user_id, "print('Hello!')")

Environment variables:
    DOCKER_SANDBOX_IMAGE - Base image (default: python:3.12-slim)
    DOCKER_SANDBOX_TIMEOUT - Idle timeout in seconds (default: 900)
    DOCKER_SANDBOX_MEMORY - Memory limit (default: 512m)
    DOCKER_SANDBOX_CPU - CPU limit (default: 1.0)
    GIT_USER_NAME - Git user.name for commits (default: Clara Bot)
    GIT_USER_EMAIL - Git user.email for commits (default: clara@bot.local)
"""

from __future__ import annotations

import asyncio
import io
import os
import tarfile
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

# Docker imports - optional dependency
try:
    import docker
    from docker.models.containers import Container

    DOCKER_AVAILABLE = True
except ImportError:
    DOCKER_AVAILABLE = False
    docker = None
    Container = None

# Configuration
DOCKER_IMAGE = os.getenv("DOCKER_SANDBOX_IMAGE", "python:3.12-slim")
DOCKER_TIMEOUT = int(os.getenv("DOCKER_SANDBOX_TIMEOUT", "900"))
DOCKER_MEMORY = os.getenv("DOCKER_SANDBOX_MEMORY", "512m")
DOCKER_CPU = float(os.getenv("DOCKER_SANDBOX_CPU", "1.0"))
SANDBOX_IDLE_TIMEOUT = DOCKER_TIMEOUT
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GIT_USER_NAME = os.getenv("GIT_USER_NAME", "Clara Bot")
GIT_USER_EMAIL = os.getenv("GIT_USER_EMAIL", "clara@bot.local")


# Tool definitions for OpenAI-compatible APIs
DOCKER_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "execute_python",
            "description": (
                "Execute Python code in a secure Docker sandbox. "
                "The sandbox has internet access and can install packages with pip. "
                "Code execution is stateful - variables persist across calls. "
                "Use this for: calculations, data analysis, file generation, "
                "web requests, package installation, and any Python code."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": (
                            "The Python code to execute. Can be multi-line. "
                            "Use print() to output results. "
                            "Variables persist across executions."
                        ),
                    },
                    "description": {
                        "type": "string",
                        "description": ("Brief description of what this code does " "(for logging/display purposes)"),
                    },
                },
                "required": ["code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "install_package",
            "description": (
                "Install a Python package using pip in the sandbox. "
                "Use this before importing non-standard-library packages."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "package": {
                        "type": "string",
                        "description": (
                            "The package name to install (e.g., 'requests', "
                            "'pandas', 'numpy'). Can include version specifiers."
                        ),
                    },
                },
                "required": ["package"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": (
                "Read the contents of a file from the sandbox filesystem. "
                "Useful for checking generated files or reading uploaded content."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": ("The file path to read (e.g., '/home/user/output.txt')"),
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": (
                "Write content to a file in the sandbox filesystem. "
                "Useful for creating files that can be executed or downloaded."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "The file path to write to",
                    },
                    "content": {
                        "type": "string",
                        "description": "The content to write to the file",
                    },
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": (
                "List files and directories in a path within the sandbox. "
                "Useful for exploring the filesystem or checking generated files."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": ("The directory path to list (default: '/home/user')"),
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_shell",
            "description": ("Run a shell command in the sandbox. " "Useful for system operations, git, curl, etc."),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The shell command to execute",
                    },
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "unzip_file",
            "description": (
                "Extract a zip archive in the sandbox. "
                "Supports .zip, .tar, .tar.gz, .tgz, .tar.bz2 formats. "
                "Useful after downloading or receiving compressed files."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": ("Path to the archive file to extract " "(e.g., '/home/user/archive.zip')"),
                    },
                    "destination": {
                        "type": "string",
                        "description": ("Directory to extract to (default: same directory as archive)"),
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "Search the web using Tavily API. "
                "Returns relevant search results with snippets and URLs. "
                "Use this to find current information, research topics, "
                "look up documentation, find news, etc."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": ("Maximum number of results to return (default: 5, max: 10)"),
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
        },
    },
]


@dataclass
class ContainerSession:
    """Tracks a user's container session."""

    container: Any  # Container instance
    user_id: str
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    last_used: datetime = field(default_factory=lambda: datetime.now(UTC))
    execution_count: int = 0
    # Package tracking for token efficiency
    base_packages: set[str] = field(default_factory=set)  # Pre-installed
    installed_packages: set[str] = field(default_factory=set)  # All available


@dataclass
class ExecutionResult:
    """Result of code execution."""

    success: bool
    output: str
    error: str | None = None
    files: list[dict] = field(default_factory=list)
    execution_time: float = 0.0


class DockerSandboxManager:
    """Manages Docker container sessions for users."""

    def __init__(self):
        self.sessions: dict[str, ContainerSession] = {}
        self._lock = asyncio.Lock()
        self._cleanup_task: asyncio.Task | None = None
        self._client = None

    @property
    def client(self):
        """Lazy-load Docker client."""
        if self._client is None and DOCKER_AVAILABLE:
            try:
                self._client = docker.from_env()
            except Exception as e:
                print(f"[docker] Failed to connect to Docker: {e}")
        return self._client

    def is_available(self) -> bool:
        """Check if Docker is available."""
        if not DOCKER_AVAILABLE:
            return False
        try:
            self.client.ping()
            return True
        except Exception:
            return False

    def _container_name(self, user_id: str) -> str:
        """Generate container name for a user."""
        # Sanitize user_id for container name
        safe_id = "".join(c if c.isalnum() else "-" for c in user_id)
        return f"clara-sandbox-{safe_id}"

    async def get_sandbox(self, user_id: str) -> Container | None:
        """Get or create a container for a user."""
        if not self.is_available():
            return None

        async with self._lock:
            # Check for existing session
            if user_id in self.sessions:
                session = self.sessions[user_id]
                container = session.container

                # Check if container is still running
                try:
                    container.reload()
                    if container.status == "running":
                        session.last_used = datetime.now(UTC)
                        return container
                    else:
                        print(f"[docker] Container stopped for {user_id}, recreating")
                        del self.sessions[user_id]
                except Exception as e:
                    print(f"[docker] Container check failed for {user_id}: {e}")
                    del self.sessions[user_id]

            # Create new container
            try:
                container_name = self._container_name(user_id)

                # Remove any existing stopped container with same name
                try:
                    old = self.client.containers.get(container_name)
                    old.remove(force=True)
                except docker.errors.NotFound:
                    pass

                # Build environment variables for sandbox
                sandbox_env = {}
                if GITHUB_TOKEN:
                    sandbox_env["GITHUB_TOKEN"] = GITHUB_TOKEN
                    sandbox_env["GH_TOKEN"] = GITHUB_TOKEN  # For gh CLI

                loop = asyncio.get_event_loop()
                container = await loop.run_in_executor(
                    None,
                    lambda: self.client.containers.run(
                        DOCKER_IMAGE,
                        "tail -f /dev/null",  # Keep container alive
                        name=container_name,
                        detach=True,
                        mem_limit=DOCKER_MEMORY,
                        cpu_period=100000,
                        cpu_quota=int(DOCKER_CPU * 100000),
                        working_dir="/home/user",
                        network_mode="bridge",  # Internet access
                        environment=sandbox_env if sandbox_env else None,
                    ),
                )

                # Create /home/user directory
                await loop.run_in_executor(
                    None,
                    lambda: container.exec_run("mkdir -p /home/user", user="root"),
                )

                # Install git (not included in python:3.12-slim)
                await loop.run_in_executor(
                    None,
                    lambda: container.exec_run(
                        "apt-get update && apt-get install -y git && rm -rf /var/lib/apt/lists/*",
                        user="root",
                    ),
                )

                # Configure git with user info and GitHub token for authentication
                git_config_cmds = [
                    f"git config --global user.name '{GIT_USER_NAME}'",
                    f"git config --global user.email '{GIT_USER_EMAIL}'",
                    "git config --global init.defaultBranch main",
                ]
                if GITHUB_TOKEN:
                    git_config_cmds.extend(
                        [
                            "git config --global credential.helper store",
                            f'echo "https://x-access-token:{GITHUB_TOKEN}@github.com" > ~/.git-credentials',
                        ]
                    )
                for cmd in git_config_cmds:
                    await loop.run_in_executor(
                        None,
                        lambda c=cmd: container.exec_run(["sh", "-c", c], user="root"),
                    )

                session = ContainerSession(
                    container=container,
                    user_id=user_id,
                )
                self.sessions[user_id] = session
                print(f"[docker] Created container for {user_id}: {container.short_id}")
                # Initialize package tracking (async, but don't block)
                asyncio.create_task(self._init_package_tracking(session))
                return container

            except Exception as e:
                print(f"[docker] Failed to create container for {user_id}: {e}")
                return None

    async def _invalidate_sandbox(self, user_id: str):
        """Remove a stale container from cache."""
        async with self._lock:
            if user_id in self.sessions:
                print(f"[docker] Invalidating container for {user_id}")
                try:
                    self.sessions[user_id].container.remove(force=True)
                except Exception:
                    pass
                del self.sessions[user_id]

    async def _init_package_tracking(self, session: ContainerSession) -> None:
        """Initialize package tracking for a session.

        Detects pre-installed packages to avoid redundant pip install calls.
        """
        try:
            loop = asyncio.get_event_loop()
            exit_code, output = await loop.run_in_executor(
                None,
                lambda: session.container.exec_run(
                    "pip list --format=freeze",
                    demux=True,
                ),
            )

            if exit_code == 0 and output[0]:
                stdout = output[0].decode("utf-8")
                # Parse package names (format: package==version)
                packages = set()
                for line in stdout.strip().split("\n"):
                    if "==" in line:
                        pkg_name = line.split("==")[0].lower().strip()
                        packages.add(pkg_name)

                session.base_packages = packages.copy()
                session.installed_packages = packages.copy()
                print(f"[docker] Tracked {len(packages)} pre-installed packages " f"for {session.user_id}")
        except Exception as e:
            print(f"[docker] Failed to init package tracking: {e}")

    async def execute_code(self, user_id: str, code: str, description: str = "") -> ExecutionResult:
        """Execute Python code in a user's container."""
        start_time = datetime.now(UTC)

        container = await self.get_sandbox(user_id)
        if not container:
            return ExecutionResult(
                success=False,
                output="",
                error="Docker sandbox not available. Is Docker running?",
            )

        try:
            loop = asyncio.get_event_loop()

            # Write code to a temp file in container
            script_path = "/tmp/script.py"
            await self._write_to_container(container, script_path, code)

            # Execute the script
            exit_code, output = await loop.run_in_executor(
                None,
                lambda: container.exec_run(
                    f"python {script_path}",
                    workdir="/home/user",
                    demux=True,
                ),
            )

            # Update session stats
            if user_id in self.sessions:
                self.sessions[user_id].execution_count += 1
                self.sessions[user_id].last_used = datetime.now(UTC)

            elapsed = (datetime.now(UTC) - start_time).total_seconds()

            # Parse output (demux returns (stdout, stderr))
            stdout = output[0].decode("utf-8") if output[0] else ""
            stderr = output[1].decode("utf-8") if output[1] else ""

            if exit_code != 0:
                return ExecutionResult(
                    success=False,
                    output=stdout,
                    error=stderr or f"Exit code: {exit_code}",
                    execution_time=elapsed,
                )

            return ExecutionResult(
                success=True,
                output=stdout or "(no output)",
                execution_time=elapsed,
            )

        except Exception as e:
            elapsed = (datetime.now(UTC) - start_time).total_seconds()
            return ExecutionResult(
                success=False,
                output="",
                error=str(e),
                execution_time=elapsed,
            )

    async def _write_to_container(self, container: Container, path: str, content: str | bytes):
        """Write content to a file in the container using tar."""
        loop = asyncio.get_event_loop()

        # Ensure content is bytes
        if isinstance(content, str):
            content = content.encode("utf-8")

        # Create a tar archive in memory
        tar_buffer = io.BytesIO()
        with tarfile.open(fileobj=tar_buffer, mode="w") as tar:
            # Get filename from path
            filename = os.path.basename(path)
            file_data = io.BytesIO(content)
            tarinfo = tarfile.TarInfo(name=filename)
            tarinfo.size = len(content)
            tar.addfile(tarinfo, file_data)

        tar_buffer.seek(0)

        # Get directory path
        dir_path = os.path.dirname(path) or "/"

        # Put the archive into the container
        await loop.run_in_executor(
            None,
            lambda: container.put_archive(dir_path, tar_buffer.getvalue()),
        )

    async def install_package(self, user_id: str, package: str) -> ExecutionResult:
        """Install a pip package in a user's container.

        Uses session-level package tracking to avoid redundant installs.
        """
        # Normalize package name (handle extras like requests[socks])
        pkg_lower = package.lower().split("[")[0].split(">=")[0].split("==")[0].strip()

        # Check if already tracked
        session = self.sessions.get(user_id)
        if session and pkg_lower in session.installed_packages:
            return ExecutionResult(
                success=True,
                output=f"Package '{package}' is already installed",
                execution_time=0.0,
            )

        # Actually install (quietly to save tokens)
        result = await self.run_shell(user_id, f"pip install -q {package}")

        # Track on success
        if result.success and session:
            session.installed_packages.add(pkg_lower)

        return result

    async def ensure_packages(self, user_id: str, packages: list[str]) -> ExecutionResult:
        """Ensure multiple packages are installed (batch install).

        Only installs packages that aren't already tracked.
        """
        session = self.sessions.get(user_id)
        tracked = session.installed_packages if session else set()

        # Filter to only missing packages
        missing = []
        for pkg in packages:
            pkg_lower = pkg.lower().split("[")[0].split(">=")[0].split("==")[0].strip()
            if pkg_lower not in tracked:
                missing.append(pkg)

        if not missing:
            return ExecutionResult(
                success=True,
                output=f"All {len(packages)} packages already installed",
                execution_time=0.0,
            )

        # Install only what's needed
        result = await self.run_shell(user_id, f"pip install -q {' '.join(missing)}")

        # Track on success
        if result.success and session:
            for pkg in missing:
                pkg_name = pkg.lower().split("[")[0]
                pkg_name = pkg_name.split(">=")[0].split("==")[0].strip()
                session.installed_packages.add(pkg_name)
            result = ExecutionResult(
                success=True,
                output=f"Installed {len(missing)} packages: {', '.join(missing)}",
                execution_time=result.execution_time,
            )

        return result

    async def read_file(self, user_id: str, path: str) -> ExecutionResult:
        """Read a file from a user's container."""
        container = await self.get_sandbox(user_id)
        if not container:
            return ExecutionResult(success=False, output="", error="Sandbox not available")

        try:
            loop = asyncio.get_event_loop()
            exit_code, output = await loop.run_in_executor(
                None,
                lambda: container.exec_run(f"cat '{path}'", demux=True),
            )

            stdout = output[0].decode("utf-8") if output[0] else ""
            stderr = output[1].decode("utf-8") if output[1] else ""

            if exit_code != 0:
                return ExecutionResult(
                    success=False,
                    output="",
                    error=stderr or f"File not found: {path}",
                )

            return ExecutionResult(success=True, output=stdout)

        except Exception as e:
            return ExecutionResult(success=False, output="", error=str(e))

    async def write_file(self, user_id: str, path: str, content: str | bytes) -> ExecutionResult:
        """Write a file to a user's container."""
        container = await self.get_sandbox(user_id)
        if not container:
            return ExecutionResult(success=False, output="", error="Sandbox not available")

        try:
            # Ensure directory exists
            dir_path = os.path.dirname(path)
            if dir_path:
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(
                    None,
                    lambda: container.exec_run(f"mkdir -p '{dir_path}'"),
                )

            await self._write_to_container(container, path, content)
            return ExecutionResult(success=True, output=f"File written to {path}")

        except Exception as e:
            return ExecutionResult(success=False, output="", error=str(e))

    async def list_files(self, user_id: str, path: str = "/home/user") -> ExecutionResult:
        """List files in a directory in a user's container."""
        container = await self.get_sandbox(user_id)
        if not container:
            return ExecutionResult(success=False, output="", error="Sandbox not available")

        try:
            loop = asyncio.get_event_loop()
            exit_code, output = await loop.run_in_executor(
                None,
                lambda: container.exec_run(f"ls -la '{path}'", demux=True),
            )

            stdout = output[0].decode("utf-8") if output[0] else ""
            stderr = output[1].decode("utf-8") if output[1] else ""

            if exit_code != 0:
                return ExecutionResult(
                    success=False,
                    output="",
                    error=stderr or f"Directory not found: {path}",
                )

            return ExecutionResult(success=True, output=stdout or "(empty directory)")

        except Exception as e:
            return ExecutionResult(success=False, output="", error=str(e))

    async def run_shell(self, user_id: str, command: str) -> ExecutionResult:
        """Run a shell command in a user's container."""
        container = await self.get_sandbox(user_id)
        if not container:
            return ExecutionResult(success=False, output="", error="Sandbox not available")

        try:
            loop = asyncio.get_event_loop()
            exit_code, output = await loop.run_in_executor(
                None,
                lambda: container.exec_run(
                    f"sh -c '{command}'",
                    workdir="/home/user",
                    demux=True,
                ),
            )

            stdout = output[0].decode("utf-8") if output[0] else ""
            stderr = output[1].decode("utf-8") if output[1] else ""

            combined = stdout
            if stderr:
                combined += f"\n[stderr]: {stderr}"

            if exit_code != 0:
                return ExecutionResult(
                    success=False,
                    output=combined,
                    error=f"Exit code: {exit_code}",
                )

            return ExecutionResult(success=True, output=combined or "(no output)")

        except Exception as e:
            return ExecutionResult(success=False, output="", error=str(e))

    async def unzip_file(self, user_id: str, path: str, destination: str | None = None) -> ExecutionResult:
        """Extract an archive in a user's container."""
        # Determine destination directory
        if not destination:
            destination = os.path.dirname(path) or "/home/user"

        # Build extraction command based on file extension
        path_lower = path.lower()
        if path_lower.endswith(".zip"):
            cmd = f"unzip -o '{path}' -d '{destination}'"
        elif path_lower.endswith(".tar.gz") or path_lower.endswith(".tgz"):
            cmd = f"tar -xzf '{path}' -C '{destination}'"
        elif path_lower.endswith(".tar.bz2"):
            cmd = f"tar -xjf '{path}' -C '{destination}'"
        elif path_lower.endswith(".tar"):
            cmd = f"tar -xf '{path}' -C '{destination}'"
        elif path_lower.endswith(".gz"):
            cmd = f"gunzip -k '{path}'"
        else:
            # Try unzip then tar
            cmd = f"unzip -o '{path}' -d '{destination}' 2>/dev/null || " f"tar -xf '{path}' -C '{destination}'"

        # Create destination and extract
        result = await self.run_shell(user_id, f"mkdir -p '{destination}' && {cmd}")

        if result.success:
            # List extracted files
            ls_result = await self.run_shell(user_id, f"ls -la '{destination}'")
            result.output += f"\n\nExtracted to {destination}:\n{ls_result.output}"

        return result

    async def web_search(self, query: str, max_results: int = 5, search_depth: str = "basic") -> ExecutionResult:
        """Search the web using Tavily API."""
        if not TAVILY_API_KEY:
            return ExecutionResult(
                success=False,
                output="",
                error="TAVILY_API_KEY not set. Web search unavailable.",
            )

        try:
            import httpx

            max_results = min(max(1, max_results), 10)

            async with httpx.AsyncClient() as client:
                response = await client.post(
                    "https://api.tavily.com/search",
                    json={
                        "api_key": TAVILY_API_KEY,
                        "query": query,
                        "max_results": max_results,
                        "search_depth": search_depth,
                        "include_answer": True,
                    },
                    timeout=30.0,
                )
                response.raise_for_status()
                data = response.json()

            # Format results
            output_parts = []

            if data.get("answer"):
                output_parts.append(f"**Summary:** {data['answer']}\n")

            output_parts.append("**Search Results:**\n")

            for i, result in enumerate(data.get("results", []), 1):
                title = result.get("title", "Untitled")
                url = result.get("url", "")
                snippet = result.get("content", "")[:300]
                output_parts.append(f"{i}. **{title}**\n   {url}\n   {snippet}\n")

            return ExecutionResult(
                success=True,
                output="\n".join(output_parts) if output_parts else "No results found.",
            )

        except Exception as e:
            return ExecutionResult(
                success=False,
                output="",
                error=f"Web search failed: {str(e)}",
            )

    async def handle_tool_call(self, user_id: str, tool_name: str, arguments: dict) -> ExecutionResult:
        """Handle a tool call from the LLM."""
        print(f"[docker] handle_tool_call: {tool_name} with args: {arguments}")

        try:
            if tool_name == "execute_python":
                code = arguments.get("code") or arguments.get("python_code") or arguments.get("script")
                if not code:
                    return ExecutionResult(
                        success=False,
                        output="",
                        error=f"Missing 'code' argument. Received: {list(arguments.keys())}",
                    )
                return await self.execute_code(
                    user_id,
                    code,
                    arguments.get("description", ""),
                )

            elif tool_name == "install_package":
                package = arguments.get("package") or arguments.get("name")
                if not package:
                    return ExecutionResult(
                        success=False,
                        output="",
                        error=f"Missing 'package' argument. Received: {list(arguments.keys())}",
                    )
                return await self.install_package(user_id, package)

            elif tool_name == "read_file":
                path = arguments.get("path") or arguments.get("file_path") or arguments.get("filename")
                if not path:
                    return ExecutionResult(
                        success=False,
                        output="",
                        error=f"Missing 'path' argument. Received: {list(arguments.keys())}",
                    )
                return await self.read_file(user_id, path)

            elif tool_name == "write_file":
                path = arguments.get("path") or arguments.get("file_path") or arguments.get("filename")
                content = arguments.get("content") or arguments.get("data") or arguments.get("text")
                if not path or content is None:
                    return ExecutionResult(
                        success=False,
                        output="",
                        error=f"Missing 'path' or 'content'. Received: {list(arguments.keys())}",
                    )
                return await self.write_file(user_id, path, content)

            elif tool_name == "list_files":
                return await self.list_files(
                    user_id,
                    arguments.get("path") or arguments.get("directory") or "/home/user",
                )

            elif tool_name == "run_shell":
                command = arguments.get("command") or arguments.get("cmd")
                if not command:
                    return ExecutionResult(
                        success=False,
                        output="",
                        error=f"Missing 'command' argument. Received: {list(arguments.keys())}",
                    )
                return await self.run_shell(user_id, command)

            elif tool_name == "unzip_file":
                path = arguments.get("path") or arguments.get("file_path") or arguments.get("archive")
                if not path:
                    return ExecutionResult(
                        success=False,
                        output="",
                        error=f"Missing 'path' argument. Received: {list(arguments.keys())}",
                    )
                return await self.unzip_file(
                    user_id,
                    path,
                    arguments.get("destination") or arguments.get("dest"),
                )

            elif tool_name == "web_search":
                query = arguments.get("query") or arguments.get("q") or arguments.get("search")
                if not query:
                    return ExecutionResult(
                        success=False,
                        output="",
                        error=f"Missing 'query' argument. Received: {list(arguments.keys())}",
                    )
                return await self.web_search(
                    query,
                    arguments.get("max_results", 5),
                    arguments.get("search_depth", "basic"),
                )

            else:
                return ExecutionResult(
                    success=False,
                    output="",
                    error=f"Unknown tool: {tool_name}",
                )

        except KeyError as e:
            return ExecutionResult(
                success=False,
                output="",
                error=f"Missing required argument {e}. Available: {list(arguments.keys())}",
            )

    async def cleanup_idle_sessions(self):
        """Clean up containers that have been idle too long."""
        async with self._lock:
            now = datetime.now(UTC)
            idle_threshold = timedelta(seconds=SANDBOX_IDLE_TIMEOUT)

            to_remove = []
            for user_id, session in self.sessions.items():
                if now - session.last_used > idle_threshold:
                    to_remove.append(user_id)

            for user_id in to_remove:
                session = self.sessions.pop(user_id)
                try:
                    session.container.stop(timeout=5)
                    session.container.remove()
                    print(f"[docker] Cleaned up idle container for {user_id}")
                except Exception as e:
                    print(f"[docker] Error cleaning up container for {user_id}: {e}")

    async def cleanup_all(self):
        """Clean up all container sessions."""
        async with self._lock:
            for user_id, session in list(self.sessions.items()):
                try:
                    session.container.stop(timeout=5)
                    session.container.remove()
                    print(f"[docker] Cleaned up container for {user_id}")
                except Exception as e:
                    print(f"[docker] Error cleaning up container for {user_id}: {e}")
            self.sessions.clear()

    def get_stats(self) -> dict:
        """Get sandbox manager statistics."""
        return {
            "available": self.is_available(),
            "active_sessions": len(self.sessions),
            "sessions": {
                user_id: {
                    "container_id": session.container.short_id if hasattr(session.container, "short_id") else "unknown",
                    "created_at": session.created_at.isoformat(),
                    "last_used": session.last_used.isoformat(),
                    "execution_count": session.execution_count,
                }
                for user_id, session in self.sessions.items()
            },
        }


# Global singleton instance
_sandbox_manager: DockerSandboxManager | None = None


def get_sandbox_manager() -> DockerSandboxManager:
    """Get the global sandbox manager instance."""
    global _sandbox_manager
    if _sandbox_manager is None:
        _sandbox_manager = DockerSandboxManager()
    return _sandbox_manager


def format_tool_result(result: ExecutionResult, tool_name: str) -> str:
    """Format an execution result for display."""
    if result.success:
        output = result.output or "(no output)"
        timing = f" [{result.execution_time:.2f}s]" if result.execution_time else ""
        return f"**{tool_name}** succeeded{timing}:\n```\n{output}\n```"
    else:
        error = result.error or "Unknown error"
        return f"**{tool_name}** failed:\n```\n{error}\n```"
