# Per-User Persistent VMs Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Give each user a persistent Incus VM with personal workspace files and privacy-scoped memory, so Clara provides single-user depth in a multi-user service.

**Architecture:** Extend the existing Incus sandbox to support persistent VMs alongside ephemeral code-execution containers. Add `visibility` metadata to Rook memories. The gateway determines privacy scope from channel type and filters context accordingly.

**Tech Stack:** Python 3.13, SQLAlchemy (PostgreSQL/SQLite), Incus CLI, Rook (mem0 fork with Qdrant/pgvector), pytest + pytest-asyncio

**Design doc:** `docs/plans/2026-03-04-per-user-vm-design.md`

---

### Task 1: UserVM Database Model

**Files:**
- Modify: `mypalclara/db/models.py`
- Create: `tests/clara_core/test_user_vm_model.py`

**Step 1: Write the failing test**

```python
"""Tests for UserVM database model."""
from __future__ import annotations

import pytest
from mypalclara.db.models import UserVM


class TestUserVMModel:
    def test_create_user_vm(self, db_session):
        vm = UserVM(
            user_id="discord-123",
            instance_name="clara-user-discord-123",
            instance_type="container",
        )
        db_session.add(vm)
        db_session.commit()
        db_session.refresh(vm)

        assert vm.id is not None
        assert vm.user_id == "discord-123"
        assert vm.instance_name == "clara-user-discord-123"
        assert vm.instance_type == "container"
        assert vm.status == "provisioning"
        assert vm.created_at is not None
        assert vm.last_accessed_at is not None
        assert vm.suspended_at is None

    def test_user_id_unique(self, db_session):
        vm1 = UserVM(user_id="discord-123", instance_name="clara-user-discord-123", instance_type="container")
        vm2 = UserVM(user_id="discord-123", instance_name="clara-user-discord-123-2", instance_type="container")
        db_session.add(vm1)
        db_session.commit()
        db_session.add(vm2)
        with pytest.raises(Exception):  # IntegrityError
            db_session.commit()

    def test_status_values(self, db_session):
        vm = UserVM(user_id="discord-456", instance_name="clara-user-discord-456", instance_type="vm")
        db_session.add(vm)
        db_session.commit()

        vm.status = "running"
        db_session.commit()
        assert vm.status == "running"

        vm.status = "suspended"
        db_session.commit()
        assert vm.status == "suspended"
```

Note: You may need a `db_session` fixture. Check if one exists in `tests/conftest.py`. If not, create one:

```python
@pytest.fixture
def db_session():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from mypalclara.db.models import Base

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()
```

**Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/clara_core/test_user_vm_model.py -v`
Expected: FAIL with `ImportError: cannot import name 'UserVM'`

**Step 3: Write minimal implementation**

Add to `mypalclara/db/models.py` near the other model definitions:

```python
class UserVM(Base):
    """Tracks persistent per-user VM instances."""

    __tablename__ = "user_vms"

    id = Column(String, primary_key=True, default=gen_uuid)
    user_id = Column(String, nullable=False, unique=True, index=True)
    instance_name = Column(String, nullable=False)
    instance_type = Column(String, nullable=False, default="container")
    status = Column(String, nullable=False, default="provisioning")
    created_at = Column(DateTime, default=utcnow, nullable=False)
    last_accessed_at = Column(DateTime, default=utcnow, nullable=False)
    suspended_at = Column(DateTime, nullable=True)

    def __repr__(self) -> str:
        return f"<UserVM user={self.user_id} status={self.status}>"
```

**Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/clara_core/test_user_vm_model.py -v`
Expected: PASS (3 tests)

**Step 5: Commit**

```bash
git add mypalclara/db/models.py tests/clara_core/test_user_vm_model.py
git commit -m "feat: add UserVM database model for persistent per-user VMs"
```

---

### Task 2: VM Manager — Core Lifecycle

**Files:**
- Create: `mypalclara/core/vm_manager.py`
- Create: `tests/clara_core/test_vm_manager.py`

**Step 1: Write the failing tests**

```python
"""Tests for VM Manager — persistent per-user VM lifecycle."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from mypalclara.core.vm_manager import VMManager


class TestVMManagerProvision:
    @pytest.mark.asyncio
    async def test_provision_creates_instance(self):
        manager = VMManager()
        with patch.object(manager, "_run_incus", new_callable=AsyncMock, return_value="") as mock_run:
            result = await manager.provision("discord-123")
            assert result is True
            # Should call incus launch
            calls = [str(c) for c in mock_run.call_args_list]
            assert any("launch" in c for c in calls)

    @pytest.mark.asyncio
    async def test_provision_sets_instance_name(self):
        manager = VMManager()
        with patch.object(manager, "_run_incus", new_callable=AsyncMock, return_value=""):
            await manager.provision("discord-123")
            assert "discord-123" in manager._instances

    @pytest.mark.asyncio
    async def test_provision_already_exists_returns_true(self):
        manager = VMManager()
        manager._instances["discord-123"] = "clara-user-discord-123"
        with patch.object(manager, "_run_incus", new_callable=AsyncMock, return_value="RUNNING"):
            result = await manager.provision("discord-123")
            assert result is True


class TestVMManagerSuspendResume:
    @pytest.mark.asyncio
    async def test_suspend_pauses_instance(self):
        manager = VMManager()
        manager._instances["discord-123"] = "clara-user-discord-123"
        with patch.object(manager, "_run_incus", new_callable=AsyncMock, return_value="") as mock_run:
            await manager.suspend("discord-123")
            calls = [str(c) for c in mock_run.call_args_list]
            assert any("pause" in c for c in calls)

    @pytest.mark.asyncio
    async def test_resume_starts_instance(self):
        manager = VMManager()
        manager._instances["discord-123"] = "clara-user-discord-123"
        with patch.object(manager, "_run_incus", new_callable=AsyncMock, return_value="") as mock_run:
            await manager.resume("discord-123")
            calls = [str(c) for c in mock_run.call_args_list]
            assert any("start" in c for c in calls)

    @pytest.mark.asyncio
    async def test_suspend_unknown_user_raises(self):
        manager = VMManager()
        with pytest.raises(ValueError, match="No VM found"):
            await manager.suspend("nonexistent")


class TestVMManagerEnsure:
    @pytest.mark.asyncio
    async def test_ensure_provisions_if_not_exists(self):
        manager = VMManager()
        with patch.object(manager, "provision", new_callable=AsyncMock, return_value=True) as mock:
            await manager.ensure_vm("discord-123")
            mock.assert_called_once_with("discord-123")

    @pytest.mark.asyncio
    async def test_ensure_resumes_if_suspended(self):
        manager = VMManager()
        manager._instances["discord-123"] = "clara-user-discord-123"
        manager._statuses["discord-123"] = "suspended"
        with patch.object(manager, "resume", new_callable=AsyncMock) as mock:
            await manager.ensure_vm("discord-123")
            mock.assert_called_once_with("discord-123")

    @pytest.mark.asyncio
    async def test_ensure_noop_if_running(self):
        manager = VMManager()
        manager._instances["discord-123"] = "clara-user-discord-123"
        manager._statuses["discord-123"] = "running"
        with patch.object(manager, "provision", new_callable=AsyncMock) as mock_p:
            with patch.object(manager, "resume", new_callable=AsyncMock) as mock_r:
                await manager.ensure_vm("discord-123")
                mock_p.assert_not_called()
                mock_r.assert_not_called()


class TestVMManagerExec:
    @pytest.mark.asyncio
    async def test_exec_runs_command_in_vm(self):
        manager = VMManager()
        manager._instances["discord-123"] = "clara-user-discord-123"
        manager._statuses["discord-123"] = "running"
        with patch.object(manager, "_run_incus", new_callable=AsyncMock, return_value="hello"):
            result = await manager.exec_in_vm("discord-123", ["echo", "hello"])
            assert result == "hello"

    @pytest.mark.asyncio
    async def test_exec_ensures_vm_first(self):
        manager = VMManager()
        with patch.object(manager, "ensure_vm", new_callable=AsyncMock) as mock_ensure:
            with patch.object(manager, "_run_incus", new_callable=AsyncMock, return_value="ok"):
                manager._instances["discord-123"] = "clara-user-discord-123"
                manager._statuses["discord-123"] = "running"
                await manager.exec_in_vm("discord-123", ["ls"])
                mock_ensure.assert_called_once()


class TestVMManagerReadWriteFile:
    @pytest.mark.asyncio
    async def test_read_file_from_vm(self):
        manager = VMManager()
        manager._instances["discord-123"] = "clara-user-discord-123"
        manager._statuses["discord-123"] = "running"
        with patch.object(manager, "exec_in_vm", new_callable=AsyncMock, return_value="file content"):
            result = await manager.read_file("discord-123", "/home/clara/workspace/USER.md")
            assert result == "file content"

    @pytest.mark.asyncio
    async def test_write_file_to_vm(self):
        manager = VMManager()
        manager._instances["discord-123"] = "clara-user-discord-123"
        manager._statuses["discord-123"] = "running"
        with patch.object(manager, "exec_in_vm", new_callable=AsyncMock, return_value="") as mock_exec:
            await manager.write_file("discord-123", "/home/clara/workspace/USER.md", "new content")
            mock_exec.assert_called_once()
```

**Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/clara_core/test_vm_manager.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mypalclara.core.vm_manager'`

**Step 3: Write minimal implementation**

Create `mypalclara/core/vm_manager.py`:

```python
"""Persistent per-user VM lifecycle management.

Each user can get a persistent Incus VM (or container) that survives
across sessions. VMs are provisioned on demand, suspended on idle,
and resumed when the user returns.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, UTC

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
packages:
  - python3
  - python3-pip
  - git
  - curl
runcmd:
  - mkdir -p /home/clara/workspace /home/clara/private /home/clara/public
  - chown -R 1000:1000 /home/clara
"""


def _sanitize_user_id(user_id: str) -> str:
    """Convert user_id to a safe Incus instance name component."""
    return re.sub(r"[^a-zA-Z0-9-]", "-", user_id).strip("-").lower()


class VMManager:
    """Manages persistent per-user Incus VMs.

    Unlike the ephemeral IncusManager (for code execution), these VMs
    persist indefinitely. They are provisioned on first need, suspended
    on idle, and resumed on demand.
    """

    def __init__(self) -> None:
        self._instances: dict[str, str] = {}  # user_id -> instance_name
        self._statuses: dict[str, str] = {}  # user_id -> status
        self._lock = asyncio.Lock()

    async def _run_incus(self, *args: str) -> str:
        """Run an incus CLI command and return stdout."""
        cmd = ["incus", *args]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            error = stderr.decode().strip()
            raise RuntimeError(f"incus {args[0]} failed: {error}")
        return stdout.decode().strip()

    def _instance_name(self, user_id: str) -> str:
        """Generate instance name for a user."""
        safe_id = _sanitize_user_id(user_id)
        return f"clara-user-{safe_id}"

    async def provision(self, user_id: str) -> bool:
        """Provision a new persistent VM for a user.

        If the VM already exists, returns True without creating.
        """
        async with self._lock:
            instance_name = self._instance_name(user_id)

            # Check if already tracked
            if user_id in self._instances:
                return True

            # Check if instance exists in Incus
            try:
                output = await self._run_incus("info", instance_name)
                if output:
                    self._instances[user_id] = instance_name
                    self._statuses[user_id] = "running"
                    return True
            except RuntimeError:
                pass  # Instance doesn't exist, create it

            # Create the instance
            await self._run_incus(
                "launch", DEFAULT_IMAGE, instance_name,
                "--config", f"user.user-data={USER_VM_CLOUD_INIT}",
            )

            self._instances[user_id] = instance_name
            self._statuses[user_id] = "running"
            logger.info(f"[VM] Provisioned {instance_name} for {user_id}")
            return True

    async def suspend(self, user_id: str) -> None:
        """Suspend (pause) a user's VM."""
        if user_id not in self._instances:
            raise ValueError(f"No VM found for user {user_id}")

        instance_name = self._instances[user_id]
        await self._run_incus("pause", instance_name)
        self._statuses[user_id] = "suspended"
        logger.info(f"[VM] Suspended {instance_name}")

    async def resume(self, user_id: str) -> None:
        """Resume a suspended user VM."""
        if user_id not in self._instances:
            raise ValueError(f"No VM found for user {user_id}")

        instance_name = self._instances[user_id]
        await self._run_incus("start", instance_name)
        self._statuses[user_id] = "running"
        logger.info(f"[VM] Resumed {instance_name}")

    async def ensure_vm(self, user_id: str) -> str:
        """Ensure a user's VM is running, provisioning or resuming as needed.

        Returns the instance name.
        """
        status = self._statuses.get(user_id)

        if status == "running":
            return self._instances[user_id]

        if status == "suspended":
            await self.resume(user_id)
            return self._instances[user_id]

        # Not tracked — provision
        await self.provision(user_id)
        return self._instances[user_id]

    async def exec_in_vm(self, user_id: str, command: list[str]) -> str:
        """Execute a command inside a user's VM.

        Ensures the VM is running first.
        """
        await self.ensure_vm(user_id)
        instance_name = self._instances[user_id]
        return await self._run_incus("exec", instance_name, "--", *command)

    async def read_file(self, user_id: str, path: str) -> str:
        """Read a file from a user's VM."""
        return await self.exec_in_vm(user_id, ["cat", path])

    async def write_file(self, user_id: str, path: str, content: str) -> None:
        """Write content to a file in a user's VM."""
        # Use incus file push via stdin
        await self.exec_in_vm(
            user_id,
            ["sh", "-c", f"cat > {path} << 'CLARA_EOF'\n{content}\nCLARA_EOF"],
        )

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
```

**Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/clara_core/test_vm_manager.py -v`
Expected: PASS (12 tests)

**Step 5: Commit**

```bash
git add mypalclara/core/vm_manager.py tests/clara_core/test_vm_manager.py
git commit -m "feat: add VMManager for persistent per-user VM lifecycle"
```

---

### Task 3: VM Manager — DB Persistence

**Files:**
- Modify: `mypalclara/core/vm_manager.py`
- Modify: `tests/clara_core/test_vm_manager.py`

**Step 1: Write the failing tests**

Add to `tests/clara_core/test_vm_manager.py`:

```python
class TestVMManagerDBPersistence:
    @pytest.mark.asyncio
    async def test_provision_creates_db_record(self, db_session):
        from mypalclara.db.models import UserVM

        manager = VMManager()
        with patch.object(manager, "_run_incus", new_callable=AsyncMock, return_value=""):
            await manager.provision("discord-789")

        vm = db_session.query(UserVM).filter_by(user_id="discord-789").first()
        assert vm is not None
        assert vm.status == "running"
        assert "discord-789" in vm.instance_name

    @pytest.mark.asyncio
    async def test_suspend_updates_db(self, db_session):
        from mypalclara.db.models import UserVM

        manager = VMManager()
        with patch.object(manager, "_run_incus", new_callable=AsyncMock, return_value=""):
            await manager.provision("discord-789")
            await manager.suspend("discord-789")

        vm = db_session.query(UserVM).filter_by(user_id="discord-789").first()
        assert vm.status == "suspended"
        assert vm.suspended_at is not None

    @pytest.mark.asyncio
    async def test_load_from_db_on_init(self, db_session):
        from mypalclara.db.models import UserVM

        # Seed a VM record
        vm = UserVM(
            user_id="discord-999",
            instance_name="clara-user-discord-999",
            instance_type="container",
            status="suspended",
        )
        db_session.add(vm)
        db_session.commit()

        manager = VMManager()
        await manager.load_from_db()
        assert "discord-999" in manager._instances
        assert manager._statuses["discord-999"] == "suspended"
```

**Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/clara_core/test_vm_manager.py::TestVMManagerDBPersistence -v`
Expected: FAIL — `provision` doesn't write to DB yet, `load_from_db` doesn't exist

**Step 3: Add DB persistence to VMManager**

Add `_save_to_db()`, `_update_db_status()`, and `load_from_db()` methods. Update `provision()`, `suspend()`, and `resume()` to call them. Use the `get_session()` pattern from `mypalclara/db/connection.py`.

```python
async def load_from_db(self) -> None:
    """Load VM state from database on startup."""
    from mypalclara.db.connection import SessionLocal
    from mypalclara.db.models import UserVM

    db = SessionLocal()
    try:
        vms = db.query(UserVM).all()
        for vm in vms:
            self._instances[vm.user_id] = vm.instance_name
            self._statuses[vm.user_id] = vm.status
        logger.info(f"[VM] Loaded {len(vms)} VMs from database")
    finally:
        db.close()

def _save_to_db(self, user_id: str, instance_name: str, instance_type: str) -> None:
    """Create a new UserVM record."""
    from mypalclara.db.connection import SessionLocal
    from mypalclara.db.models import UserVM

    db = SessionLocal()
    try:
        vm = UserVM(
            user_id=user_id,
            instance_name=instance_name,
            instance_type=instance_type,
            status="running",
        )
        db.add(vm)
        db.commit()
    finally:
        db.close()

def _update_db_status(self, user_id: str, status: str) -> None:
    """Update a VM's status in the database."""
    from datetime import datetime, UTC
    from mypalclara.db.connection import SessionLocal
    from mypalclara.db.models import UserVM

    db = SessionLocal()
    try:
        vm = db.query(UserVM).filter_by(user_id=user_id).first()
        if vm:
            vm.status = status
            vm.last_accessed_at = datetime.now(UTC)
            if status == "suspended":
                vm.suspended_at = datetime.now(UTC)
            elif status == "running":
                vm.suspended_at = None
            db.commit()
    finally:
        db.close()
```

Then update `provision()` to call `self._save_to_db(user_id, instance_name, DEFAULT_INSTANCE_TYPE)`, `suspend()` to call `self._update_db_status(user_id, "suspended")`, and `resume()` to call `self._update_db_status(user_id, "running")`.

**Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/clara_core/test_vm_manager.py -v`
Expected: PASS (15 tests)

**Step 5: Commit**

```bash
git add mypalclara/core/vm_manager.py tests/clara_core/test_vm_manager.py
git commit -m "feat: add DB persistence for per-user VM state"
```

---

### Task 4: Memory Visibility Metadata

**Files:**
- Modify: `mypalclara/core/memory/core/memory.py`
- Create: `tests/clara_core/test_memory_visibility.py`

**Step 1: Write the failing tests**

```python
"""Tests for memory visibility metadata."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


class TestBuildFiltersVisibility:
    def test_default_no_visibility_filter(self):
        from mypalclara.core.memory.core.memory import _build_filters_and_metadata

        metadata, filters = _build_filters_and_metadata(user_id="discord-123")
        # No visibility filter by default — returns all memories
        assert "visibility" not in filters

    def test_visibility_filter_public_only(self):
        from mypalclara.core.memory.core.memory import _build_filters_and_metadata

        metadata, filters = _build_filters_and_metadata(
            user_id="discord-123",
            input_filters={"visibility": "public"},
        )
        assert filters.get("visibility") == "public"

    def test_visibility_in_metadata_defaults_private(self):
        from mypalclara.core.memory.core.memory import _build_filters_and_metadata

        metadata, filters = _build_filters_and_metadata(
            user_id="discord-123",
            input_metadata={"some_key": "some_value"},
        )
        # New memories should default to private visibility
        assert metadata.get("visibility") == "private"


class TestSearchWithVisibility:
    def test_search_public_only_passes_filter(self):
        """When visibility='public' is passed, search should forward it as a filter."""
        from mypalclara.core.memory.core.memory import ClaraMemory

        mem = ClaraMemory.__new__(ClaraMemory)
        mem.vector_store = MagicMock()
        mem.embedding_model = MagicMock()
        mem.embedding_model.embed.return_value = [[0.1, 0.2]]
        mem.vector_store.search.return_value = []
        mem.db = MagicMock()
        mem.db.get_all.return_value = []

        result = mem.search(
            "test query",
            user_id="discord-123",
            filters={"visibility": "public"},
        )

        # Check the vector_store.search was called with visibility in filters
        call_kwargs = mem.vector_store.search.call_args
        if call_kwargs:
            passed_filters = call_kwargs.kwargs.get("filters") or call_kwargs[1].get("filters", {})
            assert passed_filters.get("visibility") == "public"


class TestUpdateVisibility:
    def test_update_memory_visibility(self):
        """Test that we can update a memory's visibility metadata."""
        from mypalclara.core.memory.core.memory import ClaraMemory

        mem = ClaraMemory.__new__(ClaraMemory)
        mem.vector_store = MagicMock()
        mem.db = MagicMock()

        mem.update_memory_visibility("mem-123", "public")

        # Should update the vector store payload
        mem.vector_store.update_payload.assert_called_once()
        call_args = mem.vector_store.update_payload.call_args
        assert call_args[1]["payload"]["visibility"] == "public"
```

**Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/clara_core/test_memory_visibility.py -v`
Expected: FAIL — `_build_filters_and_metadata` doesn't handle `visibility`, `update_memory_visibility` doesn't exist

**Step 3: Implement visibility support**

In `mypalclara/core/memory/core/memory.py`:

1. In `_build_filters_and_metadata()`: add `visibility` as a promoted key (like `user_id`). When building metadata for new memories, default `visibility` to `"private"`.

2. Add `update_memory_visibility()` method to `ClaraMemory`:

```python
def update_memory_visibility(self, memory_id: str, visibility: str) -> None:
    """Update the visibility of a memory.

    Args:
        memory_id: ID of the memory to update
        visibility: 'public' or 'private'
    """
    if visibility not in ("public", "private"):
        raise ValueError(f"Invalid visibility: {visibility}. Must be 'public' or 'private'.")
    self.vector_store.update_payload(
        vector_id=memory_id,
        payload={"visibility": visibility},
    )
```

3. You may need to add `update_payload()` to the vector store base class and implementations (Qdrant / pgvector). Check `mypalclara/core/memory/vector/base.py` for the abstract interface.

**Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/clara_core/test_memory_visibility.py -v`
Expected: PASS (4 tests)

**Step 5: Commit**

```bash
git add mypalclara/core/memory/core/memory.py tests/clara_core/test_memory_visibility.py
git commit -m "feat: add visibility metadata to memory system (private default)"
```

---

### Task 5: Memory Visibility Tools

**Files:**
- Create: `mypalclara/core/core_tools/memory_visibility_tool.py`
- Create: `tests/clara_core/test_memory_visibility_tool.py`

**Step 1: Write the failing tests**

```python
"""Tests for memory visibility tools."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mypalclara.tools._base import ToolContext

CTX = ToolContext(user_id="discord-123")


class TestSetVisibility:
    @pytest.mark.asyncio
    async def test_set_visibility_public(self):
        from mypalclara.core.core_tools.memory_visibility_tool import _handle_set_visibility

        with patch("mypalclara.core.core_tools.memory_visibility_tool._get_memory") as mock_mem:
            mock_mem.return_value = MagicMock()
            result = await _handle_set_visibility(
                {"memory_id": "mem-123", "visibility": "public"}, CTX
            )
            assert "public" in result.lower()
            mock_mem.return_value.update_memory_visibility.assert_called_once_with("mem-123", "public")

    @pytest.mark.asyncio
    async def test_set_visibility_invalid(self):
        from mypalclara.core.core_tools.memory_visibility_tool import _handle_set_visibility

        result = await _handle_set_visibility(
            {"memory_id": "mem-123", "visibility": "secret"}, CTX
        )
        assert "error" in result.lower()

    @pytest.mark.asyncio
    async def test_set_visibility_private(self):
        from mypalclara.core.core_tools.memory_visibility_tool import _handle_set_visibility

        with patch("mypalclara.core.core_tools.memory_visibility_tool._get_memory") as mock_mem:
            mock_mem.return_value = MagicMock()
            result = await _handle_set_visibility(
                {"memory_id": "mem-123", "visibility": "private"}, CTX
            )
            assert "private" in result.lower()


class TestListPublic:
    @pytest.mark.asyncio
    async def test_list_public_memories(self):
        from mypalclara.core.core_tools.memory_visibility_tool import _handle_list_public

        mock_result = {
            "results": [
                MagicMock(memory="Likes Python", id="m1"),
                MagicMock(memory="Works at Acme", id="m2"),
            ]
        }
        with patch("mypalclara.core.core_tools.memory_visibility_tool._get_memory") as mock_mem:
            mock_mem.return_value = MagicMock()
            mock_mem.return_value.search.return_value = mock_result
            result = await _handle_list_public({}, CTX)
            assert "Likes Python" in result
            assert "Works at Acme" in result

    @pytest.mark.asyncio
    async def test_list_public_empty(self):
        from mypalclara.core.core_tools.memory_visibility_tool import _handle_list_public

        with patch("mypalclara.core.core_tools.memory_visibility_tool._get_memory") as mock_mem:
            mock_mem.return_value = MagicMock()
            mock_mem.return_value.search.return_value = {"results": []}
            result = await _handle_list_public({}, CTX)
            assert "no public" in result.lower()
```

**Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/clara_core/test_memory_visibility_tool.py -v`
Expected: FAIL with `ModuleNotFoundError`

**Step 3: Write the tool module**

Create `mypalclara/core/core_tools/memory_visibility_tool.py` following the pattern from `workspace_tool.py`:

```python
"""Memory visibility tools — manage public/private tagging on memories."""

from __future__ import annotations

from typing import Any

from mypalclara.tools._base import ToolContext, ToolDef

MODULE_NAME = "memory_visibility"
MODULE_VERSION = "1.0.0"

SYSTEM_PROMPT = """
## Memory Privacy
You can manage the visibility of user memories:
- **private** (default): Only visible in DMs and personal context
- **public**: Visible in group channels so teammates can benefit

Use memory_set_visibility to change a memory's visibility.
Use memory_list_public to show what's currently public.
Never make a memory public without the user's explicit consent.
""".strip()


def _get_memory():
    """Get the Rook memory instance."""
    from mypalclara.core.memory import get_memory
    return get_memory()


async def _handle_set_visibility(args: dict[str, Any], ctx: ToolContext) -> str:
    visibility = args.get("visibility", "")
    if visibility not in ("public", "private"):
        return f"Error: visibility must be 'public' or 'private', got '{visibility}'."

    memory_id = args.get("memory_id", "")
    if not memory_id:
        return "Error: memory_id is required."

    try:
        mem = _get_memory()
        mem.update_memory_visibility(memory_id, visibility)
        return f"Memory {memory_id} is now **{visibility}**."
    except Exception as e:
        return f"Error updating visibility: {e}"


async def _handle_list_public(args: dict[str, Any], ctx: ToolContext) -> str:
    try:
        mem = _get_memory()
        result = mem.search(
            "public memories",
            user_id=ctx.user_id,
            filters={"visibility": "public"},
            limit=50,
        )
        memories = result.get("results", [])
        if not memories:
            return "No public memories found for this user."

        lines = [f"**Public memories ({len(memories)}):**"]
        for m in memories:
            lines.append(f"- `{m.id}`: {m.memory}")
        return "\n".join(lines)
    except Exception as e:
        return f"Error listing public memories: {e}"


TOOLS = [
    ToolDef(
        name="memory_set_visibility",
        description=(
            "Set a memory's visibility to 'public' (visible in group channels) "
            "or 'private' (DMs only). Always confirm with the user before making "
            "a memory public."
        ),
        parameters={
            "type": "object",
            "properties": {
                "memory_id": {
                    "type": "string",
                    "description": "ID of the memory to update",
                },
                "visibility": {
                    "type": "string",
                    "enum": ["public", "private"],
                    "description": "New visibility level",
                },
            },
            "required": ["memory_id", "visibility"],
        },
        handler=_handle_set_visibility,
        emoji="\U0001f512",
        label="Set Visibility",
        detail_keys=["memory_id", "visibility"],
        risk_level="moderate",
        intent="write",
    ),
    ToolDef(
        name="memory_list_public",
        description="List all of the current user's public memories (visible in group channels).",
        parameters={"type": "object", "properties": {}},
        handler=_handle_list_public,
        emoji="\U0001f4cb",
        label="Public Memories",
        detail_keys=[],
        risk_level="safe",
        intent="read",
    ),
]


async def initialize() -> None:
    pass


async def cleanup() -> None:
    pass
```

**Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/clara_core/test_memory_visibility_tool.py -v`
Expected: PASS (5 tests)

**Step 5: Commit**

```bash
git add mypalclara/core/core_tools/memory_visibility_tool.py tests/clara_core/test_memory_visibility_tool.py
git commit -m "feat: add memory visibility tools (set_visibility, list_public)"
```

---

### Task 6: Privacy Scope in Gateway Processor

**Files:**
- Modify: `mypalclara/gateway/processor.py`
- Create: `tests/gateway/test_privacy_scope.py`

**Step 1: Write the failing tests**

```python
"""Tests for privacy scope determination in the gateway processor."""
from __future__ import annotations

import pytest

from mypalclara.gateway.processor import _determine_privacy_scope


class TestDeterminePrivacyScope:
    def test_dm_channel_returns_full(self):
        assert _determine_privacy_scope("dm") == "full"

    def test_server_channel_returns_public_only(self):
        assert _determine_privacy_scope("server") == "public_only"

    def test_group_channel_returns_public_only(self):
        assert _determine_privacy_scope("group") == "public_only"

    def test_unknown_defaults_to_public_only(self):
        assert _determine_privacy_scope("unknown") == "public_only"
```

**Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/gateway/test_privacy_scope.py -v`
Expected: FAIL with `ImportError: cannot import name '_determine_privacy_scope'`

**Step 3: Add the function**

Add to `mypalclara/gateway/processor.py`:

```python
def _determine_privacy_scope(channel_type: str) -> str:
    """Determine privacy scope based on channel type.

    Args:
        channel_type: 'dm', 'server', or 'group'

    Returns:
        'full' for DMs (all memories), 'public_only' for group channels
    """
    if channel_type == "dm":
        return "full"
    return "public_only"
```

Then wire it into `_build_context()` so that when `privacy_scope == "public_only"`, the memory search includes `filters={"visibility": "public"}`. This is the key integration point — find where `fetch_mem0_context` is called and pass the visibility filter through.

**Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/gateway/test_privacy_scope.py -v`
Expected: PASS (4 tests)

**Step 5: Commit**

```bash
git add mypalclara/gateway/processor.py tests/gateway/test_privacy_scope.py
git commit -m "feat: add privacy scope determination based on channel type"
```

---

### Task 7: Privacy-Filtered Memory Fetching

**Files:**
- Modify: `mypalclara/core/memory_manager.py`
- Modify: `mypalclara/gateway/processor.py`
- Create: `tests/clara_core/test_privacy_filtered_fetch.py`

**Step 1: Write the failing tests**

```python
"""Tests for privacy-filtered memory fetching."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


class TestFetchWithPrivacyScope:
    def test_full_scope_no_visibility_filter(self):
        """In DMs, all memories are returned (no visibility filter)."""
        from mypalclara.core.memory_manager import MemoryManager

        mm = MemoryManager.__new__(MemoryManager)
        mm._memory_retriever = MagicMock()
        mm._memory_retriever.fetch_mem0_context.return_value = (["mem1"], [], [])

        user_mems, _, _ = mm.fetch_mem0_context(
            "discord-123", "proj-1", "hello", privacy_scope="full"
        )

        call_kwargs = mm._memory_retriever.fetch_mem0_context.call_args
        # Should NOT pass visibility filter
        filters = call_kwargs.kwargs.get("extra_filters", {})
        assert "visibility" not in filters

    def test_public_only_scope_adds_visibility_filter(self):
        """In group channels, only public memories are returned."""
        from mypalclara.core.memory_manager import MemoryManager

        mm = MemoryManager.__new__(MemoryManager)
        mm._memory_retriever = MagicMock()
        mm._memory_retriever.fetch_mem0_context.return_value = (["mem1"], [], [])

        mm.fetch_mem0_context(
            "discord-123", "proj-1", "hello", privacy_scope="public_only"
        )

        call_kwargs = mm._memory_retriever.fetch_mem0_context.call_args
        filters = call_kwargs.kwargs.get("extra_filters", {})
        assert filters.get("visibility") == "public"
```

**Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/clara_core/test_privacy_filtered_fetch.py -v`
Expected: FAIL — `fetch_mem0_context` doesn't accept `privacy_scope`

**Step 3: Add privacy_scope parameter**

Add `privacy_scope: str = "full"` parameter to `MemoryManager.fetch_mem0_context()`. When `privacy_scope == "public_only"`, pass `extra_filters={"visibility": "public"}` to the memory retriever. This filter gets forwarded to `_build_filters_and_metadata()` which passes it to the vector store.

Also update the gateway processor's `_build_context()` to pass `privacy_scope` through.

**Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/clara_core/test_privacy_filtered_fetch.py -v`
Expected: PASS (2 tests)

**Step 5: Commit**

```bash
git add mypalclara/core/memory_manager.py mypalclara/gateway/processor.py tests/clara_core/test_privacy_filtered_fetch.py
git commit -m "feat: pass privacy scope through memory fetching pipeline"
```

---

### Task 8: Per-User Workspace Loading from VM

**Files:**
- Modify: `mypalclara/core/prompt_builder.py`
- Create: `tests/clara_core/test_per_user_workspace.py`

**Step 1: Write the failing tests**

```python
"""Tests for per-user workspace loading from VM."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mypalclara.core.prompt_builder import PromptBuilder


class TestPerUserWorkspace:
    @patch.object(PromptBuilder, "_load_workspace_persona", return_value="You are Clara.")
    def test_build_prompt_with_full_scope_includes_user_workspace(self, _mock):
        pb = PromptBuilder(agent_id="test")
        # Mock the per-user workspace loader
        pb._user_workspace_cache = {"discord-123": {"USER.md": "Name: Alice\nTimezone: EST"}}

        messages = pb.build_prompt(
            user_mems=[],
            proj_mems=[],
            thread_summary=None,
            recent_msgs=[],
            user_message="hello",
            privacy_scope="full",
            user_id="discord-123",
        )
        all_content = " ".join(m.content for m in messages)
        assert "Name: Alice" in all_content

    @patch.object(PromptBuilder, "_load_workspace_persona", return_value="You are Clara.")
    def test_build_prompt_public_only_excludes_user_workspace(self, _mock):
        pb = PromptBuilder(agent_id="test")
        pb._user_workspace_cache = {"discord-123": {"USER.md": "Name: Alice\nTimezone: EST"}}

        messages = pb.build_prompt(
            user_mems=[],
            proj_mems=[],
            thread_summary=None,
            recent_msgs=[],
            user_message="hello",
            privacy_scope="public_only",
            user_id="discord-123",
        )
        all_content = " ".join(m.content for m in messages)
        assert "Name: Alice" not in all_content
```

**Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/clara_core/test_per_user_workspace.py -v`
Expected: FAIL — `build_prompt` doesn't accept `privacy_scope` or `user_id`

**Step 3: Add per-user workspace support**

Add `privacy_scope: str = "full"` and `user_id: str | None = None` parameters to `build_prompt()`. When `privacy_scope == "full"` and `user_id` is set, load per-user workspace files from `_user_workspace_cache` (or fetch from VM) and include them in the system prompt after the global persona.

Add `_user_workspace_cache: dict[str, dict[str, str]]` to `PromptBuilder.__init__()`.

Add `async def load_user_workspace(self, user_id: str, vm_manager) -> None` method that reads workspace files from the VM and populates the cache.

**Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/clara_core/test_per_user_workspace.py -v`
Expected: PASS (2 tests)

**Step 5: Commit**

```bash
git add mypalclara/core/prompt_builder.py tests/clara_core/test_per_user_workspace.py
git commit -m "feat: add per-user workspace loading with privacy scope"
```

---

### Task 9: Workspace Tool Scoping to User VM

**Files:**
- Modify: `mypalclara/core/core_tools/workspace_tool.py`
- Modify: `tests/clara_core/test_workspace_tool.py`

**Step 1: Write the failing tests**

Add to `tests/clara_core/test_workspace_tool.py`:

```python
class TestWorkspaceUserScoping:
    @pytest.mark.asyncio
    async def test_read_from_user_vm_workspace(self, tmp_path, monkeypatch):
        """When user has a VM, workspace_read reads from VM workspace."""
        import mypalclara.core.core_tools.workspace_tool as mod
        from mypalclara.tools._base import ToolContext

        # Simulate a user VM workspace
        user_ws = tmp_path / "vm_workspace"
        user_ws.mkdir()
        (user_ws / "USER.md").write_text("Name: Alice")

        # Mock the VM workspace resolver
        monkeypatch.setattr(mod, "_resolve_workspace_dir", lambda ctx: user_ws)

        ctx = ToolContext(user_id="discord-123")
        result = await mod._handle_workspace_read({"filename": "USER.md"}, ctx)
        assert "Name: Alice" in result

    @pytest.mark.asyncio
    async def test_write_to_user_vm_workspace(self, tmp_path, monkeypatch):
        """When user has a VM, workspace_write writes to VM workspace."""
        import mypalclara.core.core_tools.workspace_tool as mod
        from mypalclara.tools._base import ToolContext

        user_ws = tmp_path / "vm_workspace"
        user_ws.mkdir()
        (user_ws / "USER.md").write_text("original")
        monkeypatch.setattr(mod, "_resolve_workspace_dir", lambda ctx: user_ws)

        ctx = ToolContext(user_id="discord-123")
        result = await mod._handle_workspace_write(
            {"filename": "USER.md", "content": "Name: Bob"}, ctx
        )
        assert "Updated" in result
        assert (user_ws / "USER.md").read_text() == "Name: Bob"

    @pytest.mark.asyncio
    async def test_global_files_always_from_shared(self, tmp_path, monkeypatch):
        """SOUL.md and IDENTITY.md always come from the shared workspace."""
        import mypalclara.core.core_tools.workspace_tool as mod
        from mypalclara.tools._base import ToolContext

        # Even with a user VM workspace, SOUL.md reads from shared
        shared_ws = tmp_path / "shared"
        shared_ws.mkdir()
        (shared_ws / "SOUL.md").write_text("Global soul content")
        monkeypatch.setattr(mod, "WORKSPACE_DIR", shared_ws)

        user_ws = tmp_path / "vm_workspace"
        user_ws.mkdir()
        monkeypatch.setattr(mod, "_resolve_workspace_dir", lambda ctx: user_ws)

        ctx = ToolContext(user_id="discord-123")
        result = await mod._handle_workspace_read({"filename": "SOUL.md"}, ctx)
        assert "Global soul content" in result
```

**Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/clara_core/test_workspace_tool.py::TestWorkspaceUserScoping -v`
Expected: FAIL — `_resolve_workspace_dir` doesn't exist

**Step 3: Add workspace dir resolution**

Add `_resolve_workspace_dir(ctx: ToolContext) -> Path` to `workspace_tool.py`. This returns the user's VM workspace path if the user has a VM, otherwise the shared workspace. For global files (SOUL.md, IDENTITY.md), always use shared workspace regardless.

Update all handlers (`_handle_workspace_read`, `_handle_workspace_write`, `_handle_workspace_create`, `_handle_workspace_list`) to use `_resolve_workspace_dir(ctx)` instead of the global `WORKSPACE_DIR` constant. For read-only files, always use `WORKSPACE_DIR`.

**Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/clara_core/test_workspace_tool.py -v`
Expected: PASS (all existing + 3 new tests)

**Step 5: Commit**

```bash
git add mypalclara/core/core_tools/workspace_tool.py tests/clara_core/test_workspace_tool.py
git commit -m "feat: scope workspace tools to user VM when available"
```

---

### Task 10: SOUL.md Privacy Instructions

**Files:**
- Modify: `mypalclara/workspace/SOUL.md`

**Step 1: Read the current SOUL.md**

Run: `cat mypalclara/workspace/SOUL.md`

**Step 2: Add privacy section**

Append to SOUL.md:

```markdown

## Privacy

Users have both public and private information. Respect the boundary:

- **In DMs:** You have full access to the user's private memories, workspace, and files.
- **In group channels:** Only reference a user's public memories. Never reveal private details, personal files, or workspace content.
- **Default:** Everything a user tells you is private unless they explicitly ask you to make it public.
- **Asking:** You may suggest making something public if it would benefit the team, but never do it without the user's explicit consent.
```

**Step 3: Commit**

```bash
git add mypalclara/workspace/SOUL.md
git commit -m "feat: add privacy instructions to SOUL.md"
```

---

### Task 11: Wire VM Manager into Gateway Startup

**Files:**
- Modify: `mypalclara/gateway/__main__.py`
- Modify: `mypalclara/gateway/processor.py`

**Step 1: Add VM manager initialization**

In `_async_run_gateway()`, after MCP initialization and before adapter startup:

```python
# Initialize VM manager
from mypalclara.core.vm_manager import VMManager

vm_manager = VMManager()
await vm_manager.load_from_db()
logger.info(f"VM manager ready ({len(vm_manager._instances)} user VMs loaded)")

# Pass to processor
processor.set_vm_manager(vm_manager)
```

**Step 2: Add `set_vm_manager` to MessageProcessor**

```python
def set_vm_manager(self, vm_manager) -> None:
    """Set the VM manager for per-user VM access."""
    self._vm_manager = vm_manager
```

**Step 3: Wire privacy scope into message processing**

In the processor's `process()` or `_build_context()` method, determine privacy scope:

```python
privacy_scope = _determine_privacy_scope(request.channel.type)
```

Pass it through to `fetch_mem0_context()` and `build_prompt()`.

**Step 4: Test manually**

Run: `poetry run python -m mypalclara`
Expected: Gateway starts, logs "VM manager ready (0 user VMs loaded)"

**Step 5: Commit**

```bash
git add mypalclara/gateway/__main__.py mypalclara/gateway/processor.py
git commit -m "feat: wire VM manager and privacy scope into gateway"
```

---

### Task 12: Register New Core Tools

**Files:**
- Modify: `mypalclara/core/core_tools/__init__.py`

**Step 1: Check how existing tool modules are registered**

Read `mypalclara/core/core_tools/__init__.py` to see the pattern for loading tool modules.

**Step 2: Add memory_visibility to the tool registry**

Follow the same pattern used for `workspace_tool`, `files_tool`, etc.

**Step 3: Verify tools are discovered**

Run: `poetry run python -c "from mypalclara.core.core_tools import memory_visibility_tool; print(len(memory_visibility_tool.TOOLS), 'tools')""`
Expected: `2 tools`

**Step 4: Commit**

```bash
git add mypalclara/core/core_tools/__init__.py
git commit -m "feat: register memory visibility tools in core tools"
```

---

### Task 13: Integration Test — Full Flow

**Files:**
- Create: `tests/integration/test_per_user_vm_integration.py`

**Step 1: Write integration tests**

```python
"""Integration tests for per-user VM + privacy scoped memory."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mypalclara.gateway.processor import _determine_privacy_scope


class TestPrivacyScopeIntegration:
    def test_dm_gets_full_scope(self):
        assert _determine_privacy_scope("dm") == "full"

    def test_server_gets_public_only(self):
        assert _determine_privacy_scope("server") == "public_only"


class TestMemoryVisibilityToolIntegration:
    @pytest.mark.asyncio
    async def test_set_and_list_visibility(self):
        from mypalclara.core.core_tools.memory_visibility_tool import (
            _handle_list_public,
            _handle_set_visibility,
        )
        from mypalclara.tools._base import ToolContext

        ctx = ToolContext(user_id="discord-123")

        mock_mem = MagicMock()
        mock_mem.search.return_value = {
            "results": [MagicMock(memory="Likes Python", id="m1")]
        }

        with patch("mypalclara.core.core_tools.memory_visibility_tool._get_memory", return_value=mock_mem):
            # Set visibility
            result = await _handle_set_visibility(
                {"memory_id": "m1", "visibility": "public"}, ctx
            )
            assert "public" in result

            # List public
            result = await _handle_list_public({}, ctx)
            assert "Likes Python" in result


class TestWorkspaceScopingIntegration:
    @pytest.mark.asyncio
    async def test_workspace_read_respects_readonly_in_vm(self, tmp_path, monkeypatch):
        """SOUL.md is always read-only even when workspace is scoped to VM."""
        import mypalclara.core.core_tools.workspace_tool as mod
        from mypalclara.core.core_tools.workspace_tool import _handle_workspace_write
        from mypalclara.tools._base import ToolContext

        monkeypatch.setattr(mod, "WORKSPACE_DIR", tmp_path)
        ctx = ToolContext(user_id="discord-123")

        result = await _handle_workspace_write(
            {"filename": "SOUL.md", "content": "hacked"}, ctx
        )
        assert "read-only" in result
```

**Step 2: Run integration tests**

Run: `poetry run pytest tests/integration/test_per_user_vm_integration.py -v`
Expected: PASS

**Step 3: Run full test suite**

Run: `poetry run pytest tests/ -q --ignore=tests/clara_core/test_intentions.py --ignore=tests/clara_core/test_llm.py`
Expected: All tests pass (excluding known pre-existing failures)

**Step 4: Commit**

```bash
git add tests/integration/test_per_user_vm_integration.py
git commit -m "test: add integration tests for per-user VM and privacy scoping"
```

---

### Task 14: Update CLAUDE.md Documentation

**Files:**
- Modify: `CLAUDE.md`

**Step 1: Add per-user VM section**

Add under the "Optional Features" section:

```markdown
### Per-User VMs
```bash
USER_VM_ENABLED=false             # Enable persistent per-user VMs
USER_VM_IDLE_TIMEOUT=1800         # Seconds before suspend (default: 1800 = 30 min)
USER_VM_INSTANCE_TYPE=container   # 'container' or 'vm'
USER_VM_IMAGE=images:debian/12/cloud  # Incus image for user VMs
```

Each user can get a persistent Incus VM with personal workspace files and filesystem access. VMs are provisioned on demand, suspended on idle, and resumed when the user returns.

**Privacy model:** Memories default to `private`. Users can mark memories as `public` for group channel visibility. Clara respects the boundary automatically based on channel type (DM = full access, group = public only).
```

**Step 2: Lint and format**

Run: `poetry run ruff check . && poetry run ruff format .`

**Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: add per-user VM configuration to CLAUDE.md"
```

---

## Summary

| Task | Component | Dependencies |
|------|-----------|-------------|
| 1 | UserVM DB model | None |
| 2 | VM Manager — core lifecycle | Task 1 |
| 3 | VM Manager — DB persistence | Tasks 1, 2 |
| 4 | Memory visibility metadata | None |
| 5 | Memory visibility tools | Task 4 |
| 6 | Privacy scope in processor | None |
| 7 | Privacy-filtered memory fetch | Tasks 4, 6 |
| 8 | Per-user workspace loading | Tasks 2, 3 |
| 9 | Workspace tool scoping to VM | Tasks 2, 8 |
| 10 | SOUL.md privacy instructions | None |
| 11 | Gateway startup wiring | Tasks 2, 3, 6, 7 |
| 12 | Register new core tools | Task 5 |
| 13 | Integration tests | All above |
| 14 | CLAUDE.md documentation | All above |

Tasks 1, 4, 6, 10 can be done in parallel (no dependencies). Tasks 2-3 and 5 can proceed after their respective prerequisites. Tasks 11-14 are integration/wiring that tie everything together.
