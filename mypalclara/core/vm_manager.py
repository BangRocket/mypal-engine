"""Persistent per-user container lifecycle management.

Each user can get a persistent Docker container that survives
across sessions. Containers are provisioned on demand, stopped
when idle, and restarted when the user returns.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

try:
    import docker
    from docker.errors import NotFound as DockerNotFound

    DOCKER_AVAILABLE = True
except ImportError:
    DOCKER_AVAILABLE = False
    docker = None  # type: ignore[assignment]
    DockerNotFound = Exception  # type: ignore[assignment, misc]

logger = logging.getLogger(__name__)

# Configuration
DEFAULT_IMAGE = os.getenv("USER_VM_IMAGE", "python:3.13-slim")
VM_WORKSPACE_DIR = "/home/clara/workspace"
VM_PRIVATE_DIR = "/home/clara/private"
VM_PUBLIC_DIR = "/home/clara/public"

# Setup script run after container creation — just user + dirs, no package install
_SETUP_SCRIPT = """\
set -e
useradd -m -u 1000 -s /bin/bash clara 2>/dev/null || true
mkdir -p /home/clara/workspace /home/clara/private /home/clara/public
chown -R clara:clara /home/clara
"""


def _sanitize_user_id(user_id: str) -> str:
    """Convert user_id to a safe container name component."""
    if not user_id:
        raise ValueError("user_id cannot be empty")
    safe_id = re.sub(r"[^a-zA-Z0-9-]", "-", user_id).strip("-").lower()
    if not safe_id:
        raise ValueError(f"user_id '{user_id}' contains no valid characters")
    return safe_id


class VMManager:
    """Manages persistent per-user Docker containers.

    Args:
        session_factory: Optional callable returning a SQLAlchemy Session.
            When provided, container state is persisted to the database.
            When None, only in-memory tracking is used.
    """

    def __init__(
        self,
        session_factory: Callable[[], Session] | None = None,
    ) -> None:
        self._instances: dict[str, str] = {}  # user_id -> container_name
        self._statuses: dict[str, str] = {}  # user_id -> status
        self._lock = asyncio.Lock()
        self._session_factory = session_factory
        self._client: Any = None
        self._containers: dict[str, Any] = {}  # container_name -> container object cache

    @property
    def client(self) -> Any:
        """Lazy-load Docker client."""
        if self._client is None and DOCKER_AVAILABLE:
            self._client = docker.from_env()
        return self._client

    def _container_name(self, user_id: str) -> str:
        """Generate container name for a user."""
        safe_id = _sanitize_user_id(user_id)
        return f"clara-user-{safe_id}"

    async def provision(self, user_id: str) -> bool:
        """Provision a new persistent container for a user."""
        async with self._lock:
            container_name = self._container_name(user_id)

            if user_id in self._instances:
                return True

            # Check if container already exists in Docker
            try:
                existing = await self._docker_get(container_name)
                if existing:
                    status = existing.status
                    if status != "running":
                        loop = asyncio.get_event_loop()
                        await loop.run_in_executor(None, existing.start)
                    self._instances[user_id] = container_name
                    self._statuses[user_id] = "running"
                    return True
            except DockerNotFound:
                pass

            # Create new container
            loop = asyncio.get_event_loop()
            container = await loop.run_in_executor(
                None,
                lambda: self.client.containers.run(
                    DEFAULT_IMAGE,
                    "tail -f /dev/null",  # Keep container alive
                    name=container_name,
                    detach=True,
                    mem_limit="512m",
                    cpu_period=100000,
                    cpu_quota=100000,
                    volumes={
                        f"clara-user-{_sanitize_user_id(user_id)}-data": {
                            "bind": "/home/clara",
                            "mode": "rw",
                        }
                    },
                ),
            )
            self._containers[container_name] = container

            # Run setup
            await self._exec_direct_raw(container_name, ["sh", "-c", _SETUP_SCRIPT], as_root=True)

            self._instances[user_id] = container_name
            self._statuses[user_id] = "running"
            self._save_to_db(user_id, container_name, "container")
            logger.info(f"[VM] Provisioned {container_name} for {user_id}")

        # Seed outside the lock — exec_in_vm calls ensure_vm which acquires it
        await self._seed_workspace(user_id)
        return True

    async def _docker_get(self, container_name: str) -> Any:
        """Get a Docker container by name. Raises DockerNotFound if missing."""
        if container_name in self._containers:
            return self._containers[container_name]
        loop = asyncio.get_event_loop()
        container = await loop.run_in_executor(
            None, lambda: self.client.containers.get(container_name)
        )
        self._containers[container_name] = container
        return container

    async def _exec_direct_raw(
        self, container_name: str, command: list[str], as_root: bool = False
    ) -> str:
        """Execute in a container by name (no ensure_vm, avoids recursion)."""
        loop = asyncio.get_event_loop()
        container = await self._docker_get(container_name)
        user = "root" if as_root else "clara"
        exit_code, output = await loop.run_in_executor(
            None, lambda: container.exec_run(command, user=user)
        )
        decoded = output.decode("utf-8", errors="replace") if isinstance(output, bytes) else str(output)
        if exit_code != 0:
            raise RuntimeError(f"Command failed (exit {exit_code}): {decoded}")
        return decoded

    async def _exec_direct(self, user_id: str, command: list[str], as_root: bool = False) -> str:
        """Execute in a user's container without calling ensure_vm (avoids recursion)."""
        container_name = self._instances[user_id]
        return await self._exec_direct_raw(container_name, command, as_root=as_root)

    async def _write_direct(self, user_id: str, path: str, content: str) -> None:
        """Write a file to a container without calling ensure_vm (avoids recursion)."""
        if "CLARA_EOF" in content:
            raise ValueError("Content contains reserved delimiter 'CLARA_EOF'")
        escaped_path = path.replace("'", "'\\''")
        await self._exec_direct(
            user_id,
            ["sh", "-c", f"cat > '{escaped_path}' << 'CLARA_EOF'\n{content}\nCLARA_EOF"],
        )

    async def _seed_workspace(self, user_id: str) -> None:
        """Copy SOUL.md and IDENTITY.md from the shared workspace into a new container."""
        from pathlib import Path

        shared_ws = Path(__file__).parent.parent / "workspace"

        # Ensure directories exist
        await self._exec_direct(
            user_id,
            ["mkdir", "-p", VM_WORKSPACE_DIR, VM_PRIVATE_DIR, VM_PUBLIC_DIR],
            as_root=True,
        )
        await self._exec_direct(
            user_id,
            ["chown", "-R", "clara:clara", "/home/clara"],
            as_root=True,
        )

        for filename in ("SOUL.md", "IDENTITY.md"):
            src = shared_ws / filename
            if not src.exists():
                continue
            try:
                content = src.read_text(encoding="utf-8")
                await self._write_direct(user_id, f"{VM_WORKSPACE_DIR}/{filename}", content)
                logger.info(f"[VM] Seeded {filename} into {self._instances[user_id]}")
            except Exception as e:
                logger.warning(f"[VM] Could not seed {filename}: {e}")

    async def _ensure_seeded(self, user_id: str) -> None:
        """Check if SOUL.md/IDENTITY.md exist in the container; seed any that are missing."""
        from pathlib import Path

        shared_ws = Path(__file__).parent.parent / "workspace"

        for filename in ("SOUL.md", "IDENTITY.md"):
            src = shared_ws / filename
            if not src.exists():
                continue
            try:
                await self._exec_direct(user_id, ["test", "-f", f"{VM_WORKSPACE_DIR}/{filename}"])
            except RuntimeError:
                try:
                    await self._exec_direct(
                        user_id,
                        ["mkdir", "-p", VM_WORKSPACE_DIR],
                        as_root=True,
                    )
                    content = src.read_text(encoding="utf-8")
                    await self._write_direct(user_id, f"{VM_WORKSPACE_DIR}/{filename}", content)
                    logger.info(f"[VM] Seeded missing {filename} into {self._instances[user_id]}")
                except Exception as e:
                    logger.warning(f"[VM] Could not seed {filename}: {e}")

    async def suspend(self, user_id: str) -> None:
        """Stop a user's container."""
        if user_id not in self._instances:
            raise ValueError(f"No VM found for user {user_id}")

        container_name = self._instances[user_id]
        container = await self._docker_get(container_name)
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, container.stop)
        self._statuses[user_id] = "suspended"
        self._update_db_status(user_id, "suspended")
        logger.info(f"[VM] Stopped {container_name}")

    async def resume(self, user_id: str) -> None:
        """Restart a stopped user container."""
        if user_id not in self._instances:
            raise ValueError(f"No VM found for user {user_id}")

        container_name = self._instances[user_id]
        container = await self._docker_get(container_name)
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, container.start)
        self._statuses[user_id] = "running"
        self._update_db_status(user_id, "running")
        logger.info(f"[VM] Started {container_name}")

    # ------------------------------------------------------------------
    # DB persistence helpers
    # ------------------------------------------------------------------

    async def load_from_db(self) -> None:
        """Load container state from database on startup."""
        if self._session_factory is None:
            return
        from mypalclara.db.models import UserVM

        session = self._session_factory()
        try:
            vms = session.query(UserVM).all()
            for vm in vms:
                self._instances[vm.user_id] = vm.instance_name
                self._statuses[vm.user_id] = vm.status
            if vms:
                logger.info(f"[VM] Loaded {len(vms)} container(s) from database")
        finally:
            session.close()

    def _save_to_db(self, user_id: str, instance_name: str, instance_type: str) -> None:
        """Create or update a UserVM record after provisioning."""
        if self._session_factory is None:
            return
        from mypalclara.db.models import UserVM

        session = self._session_factory()
        try:
            existing = session.query(UserVM).filter_by(user_id=user_id).first()
            if existing:
                existing.status = "running"
                existing.instance_name = instance_name
            else:
                vm = UserVM(
                    user_id=user_id,
                    instance_name=instance_name,
                    instance_type=instance_type,
                    status="running",
                )
                session.add(vm)
            session.commit()
        except Exception:
            session.rollback()
            logger.exception(f"[VM] Failed to save DB record for {user_id}")
        finally:
            session.close()

    def _update_db_status(self, user_id: str, status: str) -> None:
        """Update a container's status in the database."""
        if self._session_factory is None:
            return
        from mypalclara.db.models import UserVM

        session = self._session_factory()
        try:
            vm = session.query(UserVM).filter_by(user_id=user_id).first()
            if vm:
                vm.status = status
                if status == "suspended":
                    from mypalclara.db.models import utcnow

                    vm.suspended_at = utcnow()
                elif status == "running":
                    vm.suspended_at = None
                session.commit()
        except Exception:
            session.rollback()
            logger.exception(f"[VM] Failed to update DB status for {user_id}")
        finally:
            session.close()

    async def _vm_exists(self, container_name: str) -> bool:
        """Check if a container actually exists in Docker."""
        try:
            await self._docker_get(container_name)
            return True
        except DockerNotFound:
            return False

    async def ensure_vm(self, user_id: str) -> str:
        """Ensure a user's container is running, provisioning or resuming as needed.

        Verifies the container actually exists in Docker — if it was deleted
        externally, clears stale state and reprovisions.
        """
        status = self._statuses.get(user_id)
        instance_name = self._instances.get(user_id)

        # If we think it's running or suspended, verify it actually exists
        if status in ("running", "suspended") and instance_name:
            if not await self._vm_exists(instance_name):
                logger.warning(f"[VM] {instance_name} not found in Docker (was {status}), reprovisioning")
                self._instances.pop(user_id, None)
                self._statuses.pop(user_id, None)
                status = None

        if status == "running":
            await self._ensure_seeded(user_id)
            return self._instances[user_id]

        if status == "suspended":
            await self.resume(user_id)
            await self._ensure_seeded(user_id)
            return self._instances[user_id]

        await self.provision(user_id)
        return self._instances.get(user_id, self._container_name(user_id))

    async def exec_in_vm(self, user_id: str, command: list[str], as_root: bool = False) -> str:
        """Execute a command inside a user's container.

        Args:
            user_id: The user whose container to execute in.
            command: Command and arguments to run.
            as_root: If True, run as root. Otherwise runs as the 'clara' user.
        """
        await self.ensure_vm(user_id)
        return await self._exec_direct(user_id, command, as_root=as_root)

    async def read_file(self, user_id: str, path: str) -> str:
        """Read a file from a user's container."""
        return await self.exec_in_vm(user_id, ["cat", path])

    async def write_file(self, user_id: str, path: str, content: str) -> None:
        """Write content to a file in a user's container."""
        if "CLARA_EOF" in content:
            raise ValueError("Content contains reserved delimiter 'CLARA_EOF'")
        escaped_path = path.replace("'", "'\\''")
        await self.exec_in_vm(
            user_id,
            [
                "sh",
                "-c",
                f"cat > '{escaped_path}' << 'CLARA_EOF'\n{content}\nCLARA_EOF",
            ],
        )

    async def read_workspace_files(self, user_id: str) -> dict[str, str]:
        """Read all .md files from a user's container workspace.

        Returns:
            Dict mapping filename to content, e.g. {"USER.md": "...", "MEMORY.md": "..."}
        """
        try:
            file_list = await self.exec_in_vm(
                user_id, ["find", VM_WORKSPACE_DIR, "-maxdepth", "1", "-name", "*.md", "-type", "f"]
            )
        except RuntimeError:
            logger.warning(f"[VM] Could not list workspace files for {user_id}")
            return {}

        files: dict[str, str] = {}
        for filepath in file_list.strip().splitlines():
            filepath = filepath.strip()
            if not filepath:
                continue
            filename = filepath.rsplit("/", 1)[-1]
            try:
                content = await self.read_file(user_id, filepath)
                files[filename] = content
            except RuntimeError:
                logger.warning(f"[VM] Could not read {filepath} for {user_id}")
        return files

    async def get_status(self, user_id: str) -> dict[str, str | None]:
        """Get the status of a user's container."""
        return {
            "user_id": user_id,
            "instance_name": self._instances.get(user_id),
            "status": self._statuses.get(user_id),
        }

    async def list_vms(self) -> list[dict[str, str | None]]:
        """List all tracked user containers."""
        return [await self.get_status(uid) for uid in self._instances]
