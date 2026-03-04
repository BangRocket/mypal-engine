"""Persistent per-user VM lifecycle management.

Each user can get a persistent Incus VM (or container) that survives
across sessions. VMs are provisioned on demand, suspended on idle,
and resumed when the user returns.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# Default Incus image for user VMs
DEFAULT_IMAGE = "images:debian/12/cloud"
DEFAULT_INSTANCE_TYPE = "container"
VM_WORKSPACE_DIR = "/home/clara/workspace"
VM_PRIVATE_DIR = "/home/clara/private"
VM_PUBLIC_DIR = "/home/clara/public"

# Cloud-init for user VMs
USER_VM_CLOUD_INIT = """\
#cloud-config
users:
  - name: clara
    uid: 1000
    shell: /bin/bash
    sudo: ALL=(ALL) NOPASSWD:ALL
    home: /home/clara
packages:
  - python3
  - python3-pip
  - git
  - curl
runcmd:
  - mkdir -p /home/clara/workspace /home/clara/private /home/clara/public
  - chown -R clara:clara /home/clara
"""


def _sanitize_user_id(user_id: str) -> str:
    """Convert user_id to a safe Incus instance name component."""
    if not user_id:
        raise ValueError("user_id cannot be empty")
    safe_id = re.sub(r"[^a-zA-Z0-9-]", "-", user_id).strip("-").lower()
    if not safe_id:
        raise ValueError(f"user_id '{user_id}' contains no valid characters")
    return safe_id


class VMManager:
    """Manages persistent per-user Incus VMs.

    Args:
        session_factory: Optional callable returning a SQLAlchemy Session.
            When provided, VM state is persisted to the database.
            When None, only in-memory tracking is used.
    """

    def __init__(
        self,
        session_factory: Callable[[], Session] | None = None,
    ) -> None:
        self._instances: dict[str, str] = {}  # user_id -> instance_name
        self._statuses: dict[str, str] = {}  # user_id -> status
        self._lock = asyncio.Lock()
        self._session_factory = session_factory

    async def _run_incus(self, *args: str, timeout: float = 60.0) -> str:
        """Run an incus CLI command and return stdout."""
        cmd = ["incus", *args]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            raise RuntimeError(f"incus {args[0]} timed out after {timeout}s")
        if proc.returncode != 0:
            error = stderr.decode().strip()
            raise RuntimeError(f"incus {args[0]} failed: {error}")
        return stdout.decode().strip()

    def _instance_name(self, user_id: str) -> str:
        """Generate instance name for a user."""
        safe_id = _sanitize_user_id(user_id)
        return f"clara-user-{safe_id}"

    async def provision(self, user_id: str) -> bool:
        """Provision a new persistent VM for a user."""
        async with self._lock:
            instance_name = self._instance_name(user_id)

            if user_id in self._instances:
                return True

            try:
                output = await self._run_incus("info", instance_name)
                if output:
                    self._instances[user_id] = instance_name
                    self._statuses[user_id] = "running"
                    return True
            except RuntimeError:
                pass

            await self._run_incus(
                "launch",
                DEFAULT_IMAGE,
                instance_name,
                "--config",
                f"user.user-data={USER_VM_CLOUD_INIT}",
            )

            self._instances[user_id] = instance_name
            self._statuses[user_id] = "running"
            self._save_to_db(user_id, instance_name, DEFAULT_INSTANCE_TYPE)

            # Seed workspace with shared SOUL.md and IDENTITY.md
            await self._seed_workspace(user_id)

            logger.info(f"[VM] Provisioned {instance_name} for {user_id}")
            return True

    async def _seed_workspace(self, user_id: str) -> None:
        """Copy SOUL.md and IDENTITY.md from the shared workspace into a new VM."""
        from pathlib import Path

        shared_ws = Path(__file__).parent.parent / "workspace"

        for filename in ("SOUL.md", "IDENTITY.md"):
            src = shared_ws / filename
            if not src.exists():
                continue
            try:
                content = src.read_text(encoding="utf-8")
                await self.write_file(user_id, f"{VM_WORKSPACE_DIR}/{filename}", content)
                logger.info(f"[VM] Seeded {filename} into {self._instances[user_id]}")
            except Exception as e:
                logger.warning(f"[VM] Could not seed {filename}: {e}")

    async def suspend(self, user_id: str) -> None:
        """Suspend (pause) a user's VM."""
        if user_id not in self._instances:
            raise ValueError(f"No VM found for user {user_id}")

        instance_name = self._instances[user_id]
        await self._run_incus("pause", instance_name)
        self._statuses[user_id] = "suspended"
        self._update_db_status(user_id, "suspended")
        logger.info(f"[VM] Suspended {instance_name}")

    async def resume(self, user_id: str) -> None:
        """Resume a suspended user VM."""
        if user_id not in self._instances:
            raise ValueError(f"No VM found for user {user_id}")

        instance_name = self._instances[user_id]
        await self._run_incus("start", instance_name)
        self._statuses[user_id] = "running"
        self._update_db_status(user_id, "running")
        logger.info(f"[VM] Resumed {instance_name}")

    # ------------------------------------------------------------------
    # DB persistence helpers
    # ------------------------------------------------------------------

    async def load_from_db(self) -> None:
        """Load VM state from database on startup."""
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
                logger.info(f"[VM] Loaded {len(vms)} VM(s) from database")
        finally:
            # Only close if we own the session (i.e. factory creates new ones)
            # In tests the caller owns the session, but closing is safe since
            # sessionmaker sessions can be re-used after close in SQLite.
            pass

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

    def _update_db_status(self, user_id: str, status: str) -> None:
        """Update a VM's status in the database."""
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

    async def ensure_vm(self, user_id: str) -> str:
        """Ensure a user's VM is running, provisioning or resuming as needed."""
        status = self._statuses.get(user_id)

        if status == "running":
            return self._instances[user_id]

        if status == "suspended":
            await self.resume(user_id)
            return self._instances[user_id]

        await self.provision(user_id)
        return self._instances.get(user_id, self._instance_name(user_id))

    async def exec_in_vm(self, user_id: str, command: list[str], as_root: bool = False) -> str:
        """Execute a command inside a user's VM.

        Args:
            user_id: The user whose VM to execute in.
            command: Command and arguments to run.
            as_root: If True, run as root. Otherwise runs as the 'clara' user.
        """
        await self.ensure_vm(user_id)
        instance_name = self._instances[user_id]
        if as_root:
            return await self._run_incus("exec", instance_name, "--", *command)
        return await self._run_incus(
            "exec", instance_name, "--user", "1000", "--group", "1000", "--", *command
        )

    async def read_file(self, user_id: str, path: str) -> str:
        """Read a file from a user's VM."""
        return await self.exec_in_vm(user_id, ["cat", path])

    async def write_file(self, user_id: str, path: str, content: str) -> None:
        """Write content to a file in a user's VM."""
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
        """Read all .md files from a user's VM workspace.

        Returns:
            Dict mapping filename to content, e.g. {"USER.md": "...", "MEMORY.md": "..."}
        """
        try:
            # List .md files in workspace
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
        """Get the status of a user's VM."""
        return {
            "user_id": user_id,
            "instance_name": self._instances.get(user_id),
            "status": self._statuses.get(user_id),
        }

    async def list_vms(self) -> list[dict[str, str | None]]:
        """List all tracked user VMs."""
        return [await self.get_status(uid) for uid in self._instances]
