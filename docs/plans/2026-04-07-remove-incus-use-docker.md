# Remove Incus, Use Docker Everywhere

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Remove all Incus dependencies and convert the per-user VM system to use Docker containers.

**Architecture:** Two systems use containerization: the ephemeral code sandbox (`mypalclara/sandbox/`) and the persistent per-user VM system (`mypalclara/core/vm_manager.py`). The sandbox already has a working Docker backend; Incus is just an alternative to delete. The VM manager is Incus-only and needs a Docker rewrite. Drop idle suspension (Docker containers are lightweight enough that stop/start is fine on-demand).

**Tech Stack:** Docker SDK for Python (`docker` package, already a dependency)

---

### Task 1: Delete Incus sandbox backend

**Files:**
- Delete: `mypalclara/sandbox/incus.py`

**Step 1: Delete the file**

```bash
rm mypalclara/sandbox/incus.py
```

**Step 2: Verify no Python import breaks**

Run: `python -c "from mypalclara.sandbox import get_sandbox_manager"` — this will fail because `manager.py` imports from `incus.py`. That's expected; we fix it in Task 2.

**Step 3: Commit**

```bash
git add -u mypalclara/sandbox/incus.py
git commit -m "chore: delete Incus sandbox backend"
```

---

### Task 2: Simplify sandbox manager to Docker-only

**Files:**
- Modify: `mypalclara/sandbox/manager.py`
- Modify: `mypalclara/sandbox/__init__.py`

**Step 1: Rewrite `manager.py`**

Remove all Incus imports, backend selection, and mode logic. The `UnifiedSandboxManager` becomes a thin wrapper around `DockerSandboxManager` (keeping backward compat for callers that use `get_sandbox_manager()`).

```python
"""Unified sandbox manager for Clara.

Provides a single interface for Docker-based code execution.
"""

from __future__ import annotations

from typing import Any

from .docker import DOCKER_AVAILABLE, DockerSandboxManager, ExecutionResult


class UnifiedSandboxManager:
    """Sandbox manager using Docker containers.

    Usage:
        manager = get_sandbox_manager()
        result = await manager.execute_code("user123", "print('hello')")
    """

    def __init__(self):
        self._docker: DockerSandboxManager | None = None

    @property
    def docker(self) -> DockerSandboxManager:
        """Get Docker manager (lazy initialization)."""
        if self._docker is None:
            self._docker = DockerSandboxManager()
        return self._docker

    def is_available(self) -> bool:
        """Check if Docker is available."""
        return DOCKER_AVAILABLE and self.docker.is_available()

    async def health_check(self) -> bool:
        """Check if Docker is healthy."""
        return self.docker.is_available()

    def _get_manager(self) -> DockerSandboxManager | None:
        """Get the Docker manager if available."""
        if self.is_available():
            return self.docker
        return None

    async def execute_code(self, user_id: str, code: str, description: str = "") -> ExecutionResult:
        manager = self._get_manager()
        if not manager:
            return ExecutionResult(success=False, output="", error="No sandbox backend available")
        return await manager.execute_code(user_id, code, description)

    async def run_shell(self, user_id: str, command: str) -> ExecutionResult:
        manager = self._get_manager()
        if not manager:
            return ExecutionResult(success=False, output="", error="No sandbox backend available")
        return await manager.run_shell(user_id, command)

    async def install_package(self, user_id: str, package: str) -> ExecutionResult:
        manager = self._get_manager()
        if not manager:
            return ExecutionResult(success=False, output="", error="No sandbox backend available")
        return await manager.install_package(user_id, package)

    async def ensure_packages(self, user_id: str, packages: list[str]) -> ExecutionResult:
        manager = self._get_manager()
        if not manager:
            return ExecutionResult(success=False, output="", error="No sandbox backend available")
        if hasattr(manager, "ensure_packages"):
            return await manager.ensure_packages(user_id, packages)
        for pkg in packages:
            result = await manager.install_package(user_id, pkg)
            if not result.success:
                return result
        return ExecutionResult(success=True, output=f"All {len(packages)} packages installed")

    async def read_file(self, user_id: str, path: str) -> ExecutionResult:
        manager = self._get_manager()
        if not manager:
            return ExecutionResult(success=False, output="", error="No sandbox backend available")
        return await manager.read_file(user_id, path)

    async def write_file(self, user_id: str, path: str, content: str | bytes) -> ExecutionResult:
        manager = self._get_manager()
        if not manager:
            return ExecutionResult(success=False, output="", error="No sandbox backend available")
        return await manager.write_file(user_id, path, content)

    async def list_files(self, user_id: str, path: str = "/home/user") -> ExecutionResult:
        manager = self._get_manager()
        if not manager:
            return ExecutionResult(success=False, output="", error="No sandbox backend available")
        return await manager.list_files(user_id, path)

    async def unzip_file(self, user_id: str, path: str, destination: str | None = None) -> ExecutionResult:
        manager = self._get_manager()
        if not manager:
            return ExecutionResult(success=False, output="", error="No sandbox backend available")
        return await manager.unzip_file(user_id, path, destination)

    async def web_search(self, query: str, max_results: int = 5, search_depth: str = "basic") -> ExecutionResult:
        manager = self._get_manager()
        if not manager:
            return ExecutionResult(success=False, output="", error="No sandbox backend available")
        if hasattr(manager, "web_search"):
            return await manager.web_search(query, max_results, search_depth)
        return ExecutionResult(success=False, output="", error="Web search not available")

    async def handle_tool_call(self, user_id: str, tool_name: str, arguments: dict) -> ExecutionResult:
        manager = self._get_manager()
        if not manager:
            return ExecutionResult(success=False, output="", error="No sandbox backend available")
        return await manager.handle_tool_call(user_id, tool_name, arguments)

    async def get_sandbox(self, user_id: str) -> Any:
        manager = self._get_manager()
        if not manager:
            return None
        return await manager.get_sandbox(user_id)

    async def cleanup_all(self) -> None:
        if self._docker:
            await self._docker.cleanup_all()

    async def cleanup_idle_sessions(self) -> int:
        if self._docker:
            return await self._docker.cleanup_idle_sessions()
        return 0

    def get_stats(self) -> dict[str, Any]:
        manager = self._get_manager()
        stats = manager.get_stats() if manager else {"available": False}
        stats["active_backend"] = "docker" if manager else "none"
        return stats


_unified_manager: UnifiedSandboxManager | None = None


def get_sandbox_manager() -> UnifiedSandboxManager:
    """Get the global unified sandbox manager."""
    global _unified_manager
    if _unified_manager is None:
        _unified_manager = UnifiedSandboxManager()
    return _unified_manager


def reset_sandbox_manager() -> None:
    """Reset the global sandbox manager (for testing)."""
    global _unified_manager
    _unified_manager = None
```

**Step 2: Update `__init__.py` docstring**

Remove Incus references from the module docstring. Keep the same exports.

**Step 3: Verify import works**

Run: `python -c "from mypalclara.sandbox import get_sandbox_manager; print('OK')"`
Expected: `OK`

**Step 4: Commit**

```bash
git add mypalclara/sandbox/manager.py mypalclara/sandbox/__init__.py
git commit -m "refactor: simplify sandbox manager to Docker-only"
```

---

### Task 3: Rewrite VMManager to use Docker

**Files:**
- Modify: `mypalclara/core/vm_manager.py`

**Step 1: Rewrite vm_manager.py**

Replace `_run_incus` CLI calls with Docker SDK. Key changes:
- Use `docker.from_env()` client (lazy, like `DockerSandboxManager`)
- `provision()` → `client.containers.run(image, name=..., detach=True)` with setup commands
- `suspend()` → `container.stop()` (instead of `incus pause`)
- `resume()` → `container.start()` (instead of `incus start`)
- `exec_in_vm()` → `container.exec_run(cmd, user="1000:1000")`
- `_vm_exists()` → `client.containers.get(name)` with NotFound handling
- Drop `cloud-init` — run setup commands directly after container creation
- Drop `idle_check_loop` — not needed for Docker
- Keep: DB persistence, workspace seeding, `_sanitize_user_id`, public API surface

Docker image: `debian:12-slim` (closest to the old `images:debian/12/cloud` Incus image). Install python3, pip, git, curl on provision.

The container uses a named Docker volume (`clara-user-{safe_id}-data`) mounted at `/home/clara` for persistent workspace data across stop/start cycles.

**Step 2: Verify import works**

Run: `python -c "from mypalclara.core.vm_manager import VMManager; print('OK')"`

**Step 3: Commit**

```bash
git add mypalclara/core/vm_manager.py
git commit -m "feat: rewrite VMManager to use Docker instead of Incus"
```

---

### Task 4: Update VMManager tests

**Files:**
- Modify: `tests/clara_core/test_vm_manager.py`

**Step 1: Update tests to mock Docker SDK instead of `_run_incus`**

All existing tests mock `_run_incus`. Update them to mock the Docker client and container objects instead. The test structure stays the same — same behaviors tested, different mock targets.

Key mock patterns:
- Mock `docker.from_env()` returning a mock client
- Mock `client.containers.run()` returning a mock container
- Mock `client.containers.get()` for existence checks
- Mock `container.exec_run()` returning `(exit_code, output_bytes)`
- Mock `container.stop()` / `container.start()` / `container.remove()`

**Step 2: Run tests**

Run: `poetry run pytest tests/clara_core/test_vm_manager.py -v`
Expected: All tests pass.

**Step 3: Commit**

```bash
git add tests/clara_core/test_vm_manager.py
git commit -m "test: update VMManager tests for Docker backend"
```

---

### Task 5: Update gateway startup (remove idle loop)

**Files:**
- Modify: `mypalclara/gateway/__main__.py` (lines ~480-490)

**Step 1: Remove `idle_check_loop` task creation**

Change the VM init block to remove the `asyncio.create_task(vm_manager.idle_check_loop())` line. The rest stays the same — `VMManager` init, `load_from_db`, `set_vm_manager`.

**Step 2: Commit**

```bash
git add mypalclara/gateway/__main__.py
git commit -m "chore: remove idle VM check loop (not needed for Docker)"
```

---

### Task 6: Update Discord commands

**Files:**
- Modify: `mypalclara/core/discord/commands.py` (line ~1201)

**Step 1: Remove Incus choices from sandbox mode command**

Change the choices from `["docker", "incus", "incus-vm", "auto"]` to just `["docker"]`. Since there's only one option now, consider whether to keep the command at all — but for now just limit the choices so the command still works.

**Step 2: Commit**

```bash
git add mypalclara/core/discord/commands.py
git commit -m "chore: remove Incus options from sandbox mode command"
```

---

### Task 7: Update DB model comment and workspace tool docstring

**Files:**
- Modify: `mypalclara/db/models.py` (line ~367)
- Modify: `mypalclara/core/core_tools/workspace_tool.py` (lines 10, 73)

**Step 1: Update `sandbox_mode` column comment**

Change `# docker, incus, incus-vm, auto` to `# docker`

**Step 2: Update workspace_tool.py references**

- Line 10: change "VM manager (incus exec)" to "VM manager (Docker)"
- Line 73: change "personal VM (Incus container)" to "personal Docker container"

**Step 3: Commit**

```bash
git add mypalclara/db/models.py mypalclara/core/core_tools/workspace_tool.py
git commit -m "docs: update Incus references to Docker in models and workspace tool"
```

---

### Task 8: Update CLAUDE.md

**Files:**
- Modify: `CLAUDE.md`

**Step 1: Remove Incus env vars and references**

- Remove `SANDBOX_MODE` documentation (or note it's always Docker)
- Remove `INCUS_*` env vars
- Change `USER_VM_INSTANCE_TYPE` docs — remove "container"/"vm" distinction
- Update sandbox section to say Docker-only
- Remove "Incus" from any feature tables or architecture descriptions

**Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: update CLAUDE.md to remove Incus references"
```

---

### Task 9: Run full test suite and verify

**Step 1: Run tests**

```bash
poetry run pytest tests/clara_core/test_vm_manager.py tests/clara_core/test_workspace_tool.py tests/clara_core/test_per_user_workspace.py tests/integration/test_per_user_vm_integration.py -v
```

Expected: All tests pass.

**Step 2: Run import check**

```bash
python -c "
from mypalclara.sandbox import get_sandbox_manager
from mypalclara.core.vm_manager import VMManager
print('All imports OK')
"
```

**Step 3: Run linter**

```bash
poetry run ruff check mypalclara/sandbox/ mypalclara/core/vm_manager.py mypalclara/core/core_tools/workspace_tool.py
```

Expected: Clean.
