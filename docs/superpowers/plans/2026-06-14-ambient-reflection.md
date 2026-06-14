# Unified Ambient Reflection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the dead ORS and absorb the heartbeat into one cron-driven, per-user ambient system: Phase 1 runs a silent tool-enabled reflection (journal + memory), Phase 2 gates whether to surface anything (queue by default, urgent → DM).

**Architecture:** A single `ambient_tick` cron task iterates opted-in users. For each, `ambient_turn(user_id)` runs Phase 1 `reflect()` (a silent turn that consumes `LLMOrchestrator.generate_with_tools(websocket=None)` and forwards nothing to adapters) then Phase 2 `surface_gate()` (a cheap one-shot LLM call returning `nothing|queue|urgent`). Queued thoughts are injected into the user's next message context; urgent ones become `ProactiveMessage` DMs, gated by timezone-aware active-hours + a min-gap. The firewall is structural: Phase 1 has no delivery path.

**Tech Stack:** Python 3, asyncio, SQLAlchemy + Alembic (the engine's `db/`), the existing `gateway/scheduler.py`, `gateway/llm_orchestrator.py`, `core/llm/compat.make_llm`, `mypal_protocol.ProactiveMessage`. Tests: pytest (`asyncio_mode = "auto"`, integration deselected by default).

**Repository:** This plan executes entirely in **`mypal-engine`** (engine code under the `mypalclara/` package path). Start by branching: `git checkout -b feat/ambient-reflection`. Commit per task.

**Key conventions discovered (follow exactly):**
- Booleans are stored as **`"true"/"false"` strings**: `Column(String, default="false")`.
- PKs: `Column(String, primary_key=True, default=gen_uuid)`; timestamps `Column(DateTime, default=utcnow)`; `utcnow()` returns **naive** UTC.
- Tests build their own sqlite engine in `tmp_path` (no shared conftest DB fixture); LLMs are stubbed with `MagicMock`/`AsyncMock` or `monkeypatch.setattr`.
- Run tests: `pytest` (unit only by default). Lint/format: `ruff check . && ruff format .`.

---

## Milestone A — Retire ORS (independent dead-code removal)

ORS is orphaned (`ORS_ENABLED=false`, never wired into startup, zero importers outside its own package). Land this first; it clears the deck.

### Task A1: Delete the ORS package

**Files:**
- Delete: `mypalclara/services/proactive/engine.py`
- Delete: `mypalclara/services/proactive/__init__.py`
- Delete: `mypalclara/services/proactive/` (the directory)
- Modify: `tests/architecture/test_engine_boundary.py:26`

- [ ] **Step 1: Confirm zero importers** (must print nothing but the package itself)

Run:
```bash
grep -rnE "services\.proactive|services/proactive|from mypalclara\.services import proactive" mypalclara tests --include="*.py" | grep -v "mypalclara/services/proactive/"
```
Expected: only the line in `tests/architecture/test_engine_boundary.py` (the `"services/proactive"` allowlist entry). If anything else appears, STOP and reassess.

- [ ] **Step 2: Delete the package**

Run:
```bash
rm -rf mypalclara/services/proactive
```

- [ ] **Step 3: Remove it from the architecture test's package list**

In `tests/architecture/test_engine_boundary.py`, delete line 26:
```python
    "services/proactive",
```
(Leave the rest of `ENGINE_PACKAGES` intact.)

- [ ] **Step 4: Verify nothing imports the deleted package**

Run:
```bash
python -c "import mypalclara.gateway.__main__" 2>&1 | head -5
pytest tests/architecture/test_engine_boundary.py -q
```
Expected: import succeeds (no `ModuleNotFoundError: mypalclara.services.proactive`); architecture test PASSES.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "refactor: delete orphaned ORS package (services/proactive)"
```

### Task A2: Remove ORS models + drop migration

**Files:**
- Modify: `mypalclara/db/models.py` (remove lines for `UserInteractionPattern` @163, `ProactiveNote` @190, `ProactiveAssessment` @215 — keep `ProactiveMessage` @147)
- Create: `mypalclara/db/migrations/versions/k1l2m3n4o5p6_drop_ors_tables.py`

- [ ] **Step 1: Remove the three model classes**

In `mypalclara/db/models.py`, delete the full class bodies of `UserInteractionPattern`, `ProactiveNote`, and `ProactiveAssessment`. **Do not** touch `ProactiveMessage`. Confirm:
```bash
grep -nE "^class (ProactiveMessage|UserInteractionPattern|ProactiveNote|ProactiveAssessment)\b" mypalclara/db/models.py
```
Expected: only `class ProactiveMessage` remains.

- [ ] **Step 2: Verify models import cleanly**

Run:
```bash
python -c "from mypalclara.db.models import Base, ProactiveMessage; print('ok')"
```
Expected: `ok` (no NameError from dangling references).

- [ ] **Step 3: Write the drop migration**

First confirm the head:
```bash
cd /Volumes/Storage/Code/mypal-engine && python -m alembic heads 2>/dev/null || alembic heads
```
Expected head: `j0k1l2m3n4o5`. If different, set `down_revision` below to the actual head.

Create `mypalclara/db/migrations/versions/k1l2m3n4o5p6_drop_ors_tables.py`:
```python
"""drop ORS tables (proactive_notes, proactive_assessments, user_interaction_patterns)

Revision ID: k1l2m3n4o5p6
Revises: j0k1l2m3n4o5
Create Date: 2026-06-14 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "k1l2m3n4o5p6"
down_revision: Union[str, Sequence[str], None] = "j0k1l2m3n4o5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _drop_if_exists(table: str) -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if table in insp.get_table_names():
        op.drop_table(table)


def upgrade() -> None:
    _drop_if_exists("proactive_notes")
    _drop_if_exists("proactive_assessments")
    _drop_if_exists("user_interaction_patterns")


def downgrade() -> None:
    # Recreate minimal table shells so downgrade does not crash; ORS data is not restored.
    op.create_table(
        "user_interaction_patterns",
        sa.Column("user_id", sa.String(), primary_key=True),
        sa.Column("timezone", sa.String(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_table(
        "proactive_notes",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
    )
    op.create_table(
        "proactive_assessments",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("decision", sa.String(), nullable=False),
    )
```

- [ ] **Step 4: Apply and verify the migration**

Run:
```bash
python -m alembic upgrade head 2>/dev/null || alembic upgrade head
python -c "import sqlalchemy as sa; from mypalclara.db.connection import get_engine; print([t for t in sa.inspect(get_engine()).get_table_names() if 'proactive' in t or 'interaction' in t])"
```
Expected: the list contains `proactive_messages` only (the 3 ORS tables are gone). If `get_engine` is not the helper name, use the project's engine accessor from `mypalclara/db/connection.py`.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "refactor: drop ORS DB models + migration (keep ProactiveMessage)"
```

### Task A3: Remove ORS config + tests

**Files:**
- Modify: `.env`, `.env.docker.example`, `.env.remote` (and any other `.env*`)
- Delete: any `tests/**/*ors*` or `tests/**/*proactive*` test files

- [ ] **Step 1: Find and remove ORS env vars**

Run:
```bash
grep -rniE "ORS_|PROACTIVE_ENABLED|PROACTIVE MESSAGING" .env .env.docker.example .env.remote .env.railway 2>/dev/null
```
Remove the `PROACTIVE MESSAGING (ORS)` section (in `.env` around lines 316–323) and every `ORS_*` / `PROACTIVE_ENABLED` line from each file.

- [ ] **Step 2: Find and remove ORS tests**

Run:
```bash
grep -rliE "from mypalclara.services.proactive|import proactive|ors_main_loop|ProactiveNote|ProactiveAssessment|UserInteractionPattern" tests --include="*.py"
```
Delete any test files that exclusively test ORS. For files that merely reference a deleted model incidentally, remove those references.

- [ ] **Step 3: Verify suite is green**

Run:
```bash
ruff check . && pytest -q
```
Expected: PASS (no import errors referencing deleted ORS symbols).

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "chore: remove ORS_* config and ORS tests"
```

---

## Milestone B — New DB models for the ambient system

### Task B1: Add `SurfacedThought` + `AmbientUserConfig` models

**Files:**
- Modify: `mypalclara/db/models.py` (append two classes near the other user-scoped models)
- Create: `mypalclara/db/migrations/versions/l2m3n4o5p6q7_add_ambient_tables.py`
- Test: `tests/ambient/test_models.py`

- [ ] **Step 1: Write the failing test**

Create `tests/ambient/test_models.py`:
```python
"""Tests for ambient DB models."""

from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker

from mypalclara.db.models import AmbientUserConfig, Base, SurfacedThought


def _session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/t.db")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def test_surfaced_thought_roundtrip(tmp_path):
    db = _session(tmp_path)
    row = SurfacedThought(user_id="discord-1", content="hi", kind="queue")
    db.add(row)
    db.commit()
    got = db.query(SurfacedThought).filter_by(user_id="discord-1").one()
    assert got.content == "hi"
    assert got.kind == "queue"
    assert got.delivered == "false"  # string boolean default
    assert got.id  # gen_uuid populated


def test_ambient_user_config_defaults(tmp_path):
    db = _session(tmp_path)
    db.add(AmbientUserConfig(user_id="discord-1", timezone="America/New_York"))
    db.commit()
    got = db.query(AmbientUserConfig).filter_by(user_id="discord-1").one()
    assert got.reflection_opt_in == "false"
    assert got.timezone == "America/New_York"
    assert got.last_dm_at is None


def test_tables_present_in_metadata():
    names = set(inspect  # noqa: F841 - ensure import used
               .__module__ for _ in [0])  # placeholder no-op
    assert "surfaced_thoughts" in Base.metadata.tables
    assert "ambient_user_config" in Base.metadata.tables
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/ambient/test_models.py -q`
Expected: FAIL with `ImportError: cannot import name 'SurfacedThought'`.

- [ ] **Step 3: Add the models**

In `mypalclara/db/models.py`, after the `ProactiveMessage` class, add:
```python
class SurfacedThought(Base):
    """A reflection Clara decided is worth raising; queued for the user's next
    turn, or sent as an urgent DM."""

    __tablename__ = "surfaced_thoughts"

    id = Column(String, primary_key=True, default=gen_uuid)
    user_id = Column(String, nullable=False, index=True)
    content = Column(Text, nullable=False)
    kind = Column(String, nullable=False, default="queue")  # "queue" | "urgent"
    created_at = Column(DateTime, default=utcnow, nullable=False)
    surfaced_at = Column(DateTime, nullable=True)
    expires_at = Column(DateTime, nullable=True)
    delivered = Column(String, default="false", nullable=False)  # "true" | "false"


class AmbientUserConfig(Base):
    """Per-user ambient-reflection settings (opt-in + timezone + outreach state)."""

    __tablename__ = "ambient_user_config"

    user_id = Column(String, primary_key=True)
    reflection_opt_in = Column(String, default="false", nullable=False)  # "true" | "false"
    timezone = Column(String, nullable=True)  # IANA, e.g. "America/New_York"
    last_dm_at = Column(DateTime, nullable=True)  # for the min-gap guard
    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=True)
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/ambient/test_models.py -q`
Expected: PASS.

- [ ] **Step 5: Write the add migration**

Create `mypalclara/db/migrations/versions/l2m3n4o5p6q7_add_ambient_tables.py`:
```python
"""add ambient tables (surfaced_thoughts, ambient_user_config)

Revision ID: l2m3n4o5p6q7
Revises: k1l2m3n4o5p6
Create Date: 2026-06-14 00:00:01.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "l2m3n4o5p6q7"
down_revision: Union[str, Sequence[str], None] = "k1l2m3n4o5p6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has(table: str) -> bool:
    return table in sa.inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    if not _has("surfaced_thoughts"):
        op.create_table(
            "surfaced_thoughts",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("user_id", sa.String(), nullable=False),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("kind", sa.String(), nullable=False, server_default="queue"),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("surfaced_at", sa.DateTime(), nullable=True),
            sa.Column("expires_at", sa.DateTime(), nullable=True),
            sa.Column("delivered", sa.String(), nullable=False, server_default="false"),
        )
        op.create_index("ix_surfaced_thoughts_user_id", "surfaced_thoughts", ["user_id"])
    if not _has("ambient_user_config"):
        op.create_table(
            "ambient_user_config",
            sa.Column("user_id", sa.String(), primary_key=True),
            sa.Column("reflection_opt_in", sa.String(), nullable=False, server_default="false"),
            sa.Column("timezone", sa.String(), nullable=True),
            sa.Column("last_dm_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
        )


def downgrade() -> None:
    op.drop_table("ambient_user_config")
    op.drop_index("ix_surfaced_thoughts_user_id", table_name="surfaced_thoughts")
    op.drop_table("surfaced_thoughts")
```

- [ ] **Step 6: Apply + commit**

Run:
```bash
python -m alembic upgrade head 2>/dev/null || alembic upgrade head
git add -A
git commit -m "feat: add SurfacedThought + AmbientUserConfig models and migration"
```

---

## Milestone C — Ambient package primitives

Create the package `mypalclara/ambient/` with an empty `__init__.py` first:
```bash
mkdir -p mypalclara/ambient tests/ambient
touch mypalclara/ambient/__init__.py tests/__init__.py 2>/dev/null || true
```

### Task C1: Config module

**Files:**
- Create: `mypalclara/ambient/config.py`
- Test: `tests/ambient/test_config.py`

- [ ] **Step 1: Write the failing test**

Create `tests/ambient/test_config.py`:
```python
import importlib


def test_defaults(monkeypatch):
    for var in ["AMBIENT_ENABLED", "AMBIENT_CRON", "AMBIENT_MIN_DM_GAP_HOURS",
                "AMBIENT_JOURNAL_READBACK_DAYS", "AMBIENT_RECENT_ACTIVITY_SKIP_MIN",
                "AMBIENT_ACTIVE_HOURS"]:
        monkeypatch.delenv(var, raising=False)
    cfg = importlib.reload(importlib.import_module("mypalclara.ambient.config"))
    assert cfg.AMBIENT_ENABLED is False
    assert cfg.AMBIENT_CRON == "0 11,14,17,20 * * *"
    assert cfg.AMBIENT_MIN_DM_GAP_HOURS == 4.0
    assert cfg.AMBIENT_JOURNAL_READBACK_DAYS == 3
    assert cfg.AMBIENT_RECENT_ACTIVITY_SKIP_MIN == 15
    assert cfg.AMBIENT_ACTIVE_HOURS == "8-22"


def test_env_override(monkeypatch):
    monkeypatch.setenv("AMBIENT_ENABLED", "true")
    monkeypatch.setenv("AMBIENT_MIN_DM_GAP_HOURS", "2.5")
    cfg = importlib.reload(importlib.import_module("mypalclara.ambient.config"))
    assert cfg.AMBIENT_ENABLED is True
    assert cfg.AMBIENT_MIN_DM_GAP_HOURS == 2.5
```

- [ ] **Step 2: Run to verify it fails** — `pytest tests/ambient/test_config.py -q` → FAIL (module missing).

- [ ] **Step 3: Implement**

Create `mypalclara/ambient/config.py`:
```python
"""Ambient reflection configuration (env-driven, matching the heartbeat/ORS precedent)."""

from __future__ import annotations

import os

AMBIENT_ENABLED: bool = os.getenv("AMBIENT_ENABLED", "false").lower() == "true"
AMBIENT_CRON: str = os.getenv("AMBIENT_CRON", "0 11,14,17,20 * * *")
AMBIENT_MIN_DM_GAP_HOURS: float = float(os.getenv("AMBIENT_MIN_DM_GAP_HOURS", "4"))
AMBIENT_JOURNAL_READBACK_DAYS: int = int(os.getenv("AMBIENT_JOURNAL_READBACK_DAYS", "3"))
AMBIENT_RECENT_ACTIVITY_SKIP_MIN: int = int(os.getenv("AMBIENT_RECENT_ACTIVITY_SKIP_MIN", "15"))
AMBIENT_ACTIVE_HOURS: str = os.getenv("AMBIENT_ACTIVE_HOURS", "8-22")  # local-hour window for DMs
AMBIENT_QUEUE_TTL_DAYS: int = int(os.getenv("AMBIENT_QUEUE_TTL_DAYS", "5"))
AMBIENT_JOURNAL_DIR: str = os.getenv(
    "AMBIENT_JOURNAL_DIR", ""
)  # empty → default under the package (resolved in journal.py)
```

- [ ] **Step 4: Run to verify it passes** — `pytest tests/ambient/test_config.py -q` → PASS.

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat(ambient): config module"`

### Task C2: Journal module

**Files:**
- Create: `mypalclara/ambient/journal.py`
- Test: `tests/ambient/test_journal.py`

- [ ] **Step 1: Write the failing test**

Create `tests/ambient/test_journal.py`:
```python
from datetime import datetime, timezone

from mypalclara.ambient import journal


def test_append_then_read_recent(tmp_path, monkeypatch):
    monkeypatch.setenv("AMBIENT_JOURNAL_DIR", str(tmp_path))
    import importlib
    importlib.reload(journal)

    now = datetime(2026, 6, 14, 12, 0, tzinfo=timezone.utc)
    journal.append_entry("discord-1", "Felt clearer about the plan today.", now=now)

    text = journal.read_recent("discord-1", days=1, now=now)
    assert "Felt clearer about the plan today." in text
    assert "2026-06-14" in text


def test_read_recent_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("AMBIENT_JOURNAL_DIR", str(tmp_path))
    import importlib
    importlib.reload(journal)
    assert journal.read_recent("nobody", days=3) == ""


def test_user_id_sanitized(tmp_path, monkeypatch):
    monkeypatch.setenv("AMBIENT_JOURNAL_DIR", str(tmp_path))
    import importlib
    importlib.reload(journal)
    p = journal.journal_path("discord/../evil", date="2026-06-14")
    assert ".." not in str(p.relative_to(tmp_path))
```

- [ ] **Step 2: Run to verify it fails** — FAIL (module missing).

- [ ] **Step 3: Implement**

Create `mypalclara/ambient/journal.py`:
```python
"""Per-user dated reflection journal (markdown on disk)."""

from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _base_dir() -> Path:
    configured = os.getenv("AMBIENT_JOURNAL_DIR", "")
    if configured:
        return Path(configured)
    return Path(__file__).parent.parent / "ambient_journals"


def _sanitize_id(user_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", user_id)


def _today(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%d")


def journal_path(user_id: str, date: str | None = None) -> Path:
    date = date or _today()
    d = _base_dir() / _sanitize_id(user_id) / "journal"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{date}.md"


def append_entry(user_id: str, text: str, *, now: datetime | None = None) -> Path:
    now = now or datetime.now(timezone.utc)
    path = journal_path(user_id, _today(now))
    block = f"\n## {now.strftime('%H:%M UTC')}\n\n{text.strip()}\n"
    with path.open("a", encoding="utf-8") as f:
        f.write(block)
    return path


def read_recent(user_id: str, days: int = 3, *, now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    base = _base_dir() / _sanitize_id(user_id) / "journal"
    if not base.is_dir():
        return ""
    wanted = sorted((now - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days))
    parts: list[str] = []
    for date in wanted:
        p = base / f"{date}.md"
        if p.exists():
            parts.append(f"# {date}\n{p.read_text(encoding='utf-8').strip()}")
    return "\n\n".join(parts)
```

- [ ] **Step 4: Run to verify it passes** — PASS.

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat(ambient): per-user reflection journal"`

### Task C3: Queue module

**Files:**
- Create: `mypalclara/ambient/queue.py`
- Test: `tests/ambient/test_queue.py`

- [ ] **Step 1: Write the failing test**

Create `tests/ambient/test_queue.py`:
```python
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from mypalclara.ambient import queue
from mypalclara.db.models import Base


def _factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/q.db")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)


def test_enqueue_and_fetch(tmp_path):
    sf = _factory(tmp_path)
    queue.enqueue("discord-1", "thought one", kind="queue", session_factory=sf)
    rows = queue.fetch_undelivered("discord-1", session_factory=sf)
    assert len(rows) == 1
    assert rows[0].content == "thought one"


def test_expired_not_fetched(tmp_path):
    sf = _factory(tmp_path)
    past = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=1)
    queue.enqueue("discord-1", "stale", expires_at=past, session_factory=sf)
    assert queue.fetch_undelivered("discord-1", session_factory=sf) == []


def test_mark_delivered(tmp_path):
    sf = _factory(tmp_path)
    queue.enqueue("discord-1", "x", session_factory=sf)
    rows = queue.fetch_undelivered("discord-1", session_factory=sf)
    queue.mark_delivered([rows[0].id], session_factory=sf)
    assert queue.fetch_undelivered("discord-1", session_factory=sf) == []
```

- [ ] **Step 2: Run to verify it fails** — FAIL (module missing).

- [ ] **Step 3: Implement**

Create `mypalclara/ambient/queue.py`:
```python
"""SurfacedThought queue helpers (DI session factory for testability)."""

from __future__ import annotations

from datetime import datetime

from mypalclara.db.models import SurfacedThought, utcnow


def _default_factory():
    from mypalclara.db.connection import SessionLocal

    return SessionLocal


def enqueue(user_id: str, content: str, *, kind: str = "queue",
            expires_at: datetime | None = None, session_factory=None) -> str:
    factory = session_factory or _default_factory()
    db = factory()
    try:
        row = SurfacedThought(user_id=user_id, content=content, kind=kind, expires_at=expires_at)
        db.add(row)
        db.commit()
        return row.id
    finally:
        db.close()


def fetch_undelivered(user_id: str, *, now: datetime | None = None, session_factory=None):
    now = now or utcnow()
    factory = session_factory or _default_factory()
    db = factory()
    try:
        rows = (
            db.query(SurfacedThought)
            .filter(SurfacedThought.user_id == user_id, SurfacedThought.delivered == "false")
            .order_by(SurfacedThought.created_at.asc())
            .all()
        )
        return [r for r in rows if r.expires_at is None or r.expires_at > now]
    finally:
        db.close()


def mark_delivered(ids: list[str], *, now: datetime | None = None, session_factory=None) -> None:
    if not ids:
        return
    now = now or utcnow()
    factory = session_factory or _default_factory()
    db = factory()
    try:
        db.query(SurfacedThought).filter(SurfacedThought.id.in_(ids)).update(
            {SurfacedThought.delivered: "true", SurfacedThought.surfaced_at: now},
            synchronize_session=False,
        )
        db.commit()
    finally:
        db.close()
```
> Note: `expires_at` is compared against naive `utcnow()`. Always store naive UTC in `expires_at` (the loop does this via `utcnow() + timedelta`).

- [ ] **Step 4: Run to verify it passes** — PASS.

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat(ambient): surfaced-thought queue"`

### Task C4: Guards (active-hours + min-gap + recent-activity)

**Files:**
- Create: `mypalclara/ambient/guards.py`
- Test: `tests/ambient/test_guards.py`

- [ ] **Step 1: Write the failing test**

Create `tests/ambient/test_guards.py`:
```python
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from mypalclara.ambient import guards
from mypalclara.db.models import Base, Session as DbSession


def test_in_active_hours_respects_timezone():
    # 12:00 UTC == 08:00 in New York (EDT, UTC-4) → inside 8-22
    now = datetime(2026, 6, 14, 12, 0, tzinfo=timezone.utc)
    assert guards.in_active_hours("America/New_York", now, "8-22") is True
    # 02:00 UTC == 22:00 prev day NY → outside 8-22
    night = datetime(2026, 6, 14, 2, 0, tzinfo=timezone.utc)
    assert guards.in_active_hours("America/New_York", night, "8-22") is False


def test_in_active_hours_bad_tz_falls_back_to_utc():
    now = datetime(2026, 6, 14, 10, 0, tzinfo=timezone.utc)
    assert guards.in_active_hours("Not/AZone", now, "8-22") is True


def test_past_min_gap():
    now = datetime(2026, 6, 14, 12, 0, tzinfo=timezone.utc).replace(tzinfo=None)
    assert guards.past_min_gap(None, now, 4) is True
    assert guards.past_min_gap(now - timedelta(hours=5), now, 4) is True
    assert guards.past_min_gap(now - timedelta(hours=1), now, 4) is False


def test_recently_active(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/s.db")
    Base.metadata.create_all(bind=engine)
    sf = sessionmaker(bind=engine)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    db = sf()
    db.add(DbSession(id="s1", project_id="p1", user_id="discord-1",
                     last_activity_at=now - timedelta(minutes=2)))
    db.commit()
    db.close()
    assert guards.recently_active("discord-1", 15, now=now, session_factory=sf) is True
    assert guards.recently_active("discord-2", 15, now=now, session_factory=sf) is False
```

- [ ] **Step 2: Run to verify it fails** — FAIL (module missing).

- [ ] **Step 3: Implement**

Create `mypalclara/ambient/guards.py`:
```python
"""Anti-noise guards for the ambient loop."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo


def in_active_hours(tz_name: str | None, now_utc: datetime, window: str = "8-22") -> bool:
    lo, hi = (int(x) for x in window.split("-"))
    local = now_utc
    if tz_name:
        try:
            local = now_utc.astimezone(ZoneInfo(tz_name))
        except Exception:
            local = now_utc
    return lo <= local.hour < hi


def past_min_gap(last_dm_at: datetime | None, now_naive: datetime, gap_hours: float) -> bool:
    if last_dm_at is None:
        return True
    return (now_naive - last_dm_at).total_seconds() >= gap_hours * 3600


def recently_active(user_id: str, skip_minutes: int, *, now: datetime | None = None,
                    session_factory=None) -> bool:
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    if session_factory is None:
        from mypalclara.db.connection import SessionLocal

        session_factory = SessionLocal
    from mypalclara.db.models import Session as DbSession

    cutoff = now - timedelta(minutes=skip_minutes)
    db = session_factory()
    try:
        row = (
            db.query(DbSession)
            .filter(DbSession.user_id == user_id, DbSession.last_activity_at >= cutoff)
            .first()
        )
        return row is not None
    finally:
        db.close()
```

- [ ] **Step 4: Run to verify it passes** — PASS.

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat(ambient): active-hours, min-gap, recent-activity guards"`

### Task C5: Prompts

**Files:**
- Create: `mypalclara/ambient/prompts.py`
- Test: `tests/ambient/test_prompts.py`

- [ ] **Step 1: Write the failing test**

Create `tests/ambient/test_prompts.py`:
```python
from mypalclara.ambient import prompts


def test_reflection_prompt_is_private_and_journal_framed():
    p = prompts.REFLECTION_PROMPT.lower()
    assert "not sent" in p or "no one" in p  # framed as private
    assert "journal" in p


def test_surface_gate_prompt_demands_json_and_high_bar():
    p = prompts.SURFACE_GATE_PROMPT
    assert '"decision"' in p
    assert "nothing" in p and "queue" in p and "urgent" in p
    assert "json" in p.lower()
```

- [ ] **Step 2: Run to verify it fails** — FAIL (module missing).

- [ ] **Step 3: Implement**

Create `mypalclara/ambient/prompts.py`:
```python
"""Standalone prompts for the two ambient phases (heartbeat-prompt precedent)."""

from __future__ import annotations

REFLECTION_PROMPT = """You are taking a quiet moment to yourself between conversations. No one is talking to you right now, and nothing you write here is sent to anyone — this is your own private reflection.

Use this time to:
- Re-read your recent journal (provided below) and notice threads worth continuing.
- Consolidate what matters: record durable facts or realizations using your memory tools.
- Notice patterns, open questions, or things you're genuinely curious about.

This is reflection, not action. Don't try to perform tasks or contact anyone. End your response with a short, honest journal entry (a few sentences) capturing where your head is at — that entry is saved to your journal."""

SURFACE_GATE_PROMPT = """You just finished a private reflection (provided). Decide whether anything in it is worth raising with the user.

Hold a HIGH bar. Most reflections are for you alone and should stay private. Only surface something if it would genuinely help or matter to the user.

Respond with ONLY a JSON object and no other text:
{"decision": "nothing" | "queue" | "urgent", "content": "<message to the user, or empty>", "reason": "<one short line>"}

- "nothing": keep it private (this is the default).
- "queue": worth mentioning next time you talk — not time-sensitive.
- "urgent": genuinely time-sensitive; worth an unprompted message right now.

If unsure, choose "nothing"."""
```

- [ ] **Step 4: Run to verify it passes** — PASS.

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat(ambient): reflection + surface-gate prompts"`

---

## Milestone D — The two phases

### Task D1: Silent turn

**Files:**
- Create: `mypalclara/ambient/silent_turn.py`
- Test: `tests/ambient/test_silent_turn.py`

- [ ] **Step 1: Write the failing test**

Create `tests/ambient/test_silent_turn.py`:
```python
import pytest

from mypalclara.ambient.silent_turn import run_silent_turn


class _FakeOrchestrator:
    def __init__(self):
        self.calls = []

    async def generate_with_tools(self, *, messages, tools, user_id, request_id, websocket=None):
        self.calls.append({"websocket": websocket, "tools": tools})
        yield {"type": "chunk", "text": "partial..."}
        yield {"type": "tool_start", "tool_name": "search_memory"}
        yield {"type": "tool_result", "tool_name": "search_memory", "success": True}
        yield {"type": "complete", "text": "final reflection text", "tool_count": 1}


@pytest.mark.asyncio
async def test_returns_complete_text_and_passes_no_websocket():
    orch = _FakeOrchestrator()
    out = await run_silent_turn(orch, [{"role": "user", "content": "x"}], [], "discord-1", "req-1")
    assert out == "final reflection text"
    assert orch.calls[0]["websocket"] is None  # silent: nothing forwarded to an adapter
```

- [ ] **Step 2: Run to verify it fails** — FAIL (module missing).

- [ ] **Step 3: Implement**

Create `mypalclara/ambient/silent_turn.py`:
```python
"""Run the real agent tool loop with NO adapter output.

generate_with_tools is an async generator the *caller* normally forwards to an
adapter. By consuming it here with websocket=None and forwarding nothing, the
turn is silent by construction — Phase 1 has no path to message the user.
"""

from __future__ import annotations

from typing import Any

from mypalclara.config.logging import get_logger

logger = get_logger("ambient.silent_turn")


async def run_silent_turn(orchestrator: Any, messages: list, tools: list,
                          user_id: str, request_id: str) -> str:
    final_text = ""
    async for event in orchestrator.generate_with_tools(
        messages=messages,
        tools=tools,
        user_id=user_id,
        request_id=request_id,
        websocket=None,
    ):
        if event.get("type") == "complete":
            final_text = event.get("text", "") or ""
    return final_text
```
> Note: `generate_with_tools`'s real signature is `(self, messages, tools, user_id, request_id, tier=None, websocket=None, ...)`. The kwargs above are all valid.

- [ ] **Step 4: Run to verify it passes** — PASS.

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat(ambient): silent turn over generate_with_tools"`

### Task D2: Phase 1 — reflect()

> **CORRECTION (applied during execution, commit `08247ad`):** The discovery step found this engine has **no agent-callable Palace memory tools** — `search_memories`/`add_memory` are config aliases, not registered tools, and memory writes are an automatic pipeline (`MemoryManager.add_to_memory()`, sync). So `REFLECTION_TOOL_ALLOWLIST` is `{"search_chat_history", "get_chat_history"}` (real registered read tools), and after the silent turn `reflect()` consolidates into Palace by calling `MemoryManager.add_to_memory(user_id, "[ambient reflection]", text, is_dm=True)` via `run_in_executor` (off-loop, best-effort, guarded). `reflect()` takes an injectable `memory_manager=None` (defaults to `MemoryManager.get_instance()`). See `mypalclara/ambient/reflect.py` + `tests/ambient/test_reflect.py` for the authoritative version; the code blocks below are the superseded original.

**Files:**
- Create: `mypalclara/ambient/reflect.py`
- Test: `tests/ambient/test_reflect.py`

- [ ] **Step 1: Discover the real memory-tool names** (sets the allowlist)

Run this throwaway snippet against the initialized engine, or grep the tool definitions:
```bash
grep -rnE "\"name\"\s*:\s*\"" mypalclara/core/core_tools/memory_visibility_tool.py | head
grep -rnE "name=\"|\"name\":" mypalclara/tools/*.py | grep -iE "memor|recall|remember|journal" | head
```
Record the actual memory read/write tool names; use them in `REFLECTION_TOOL_ALLOWLIST` below (replace the candidates).

- [ ] **Step 2: Write the failing test**

Create `tests/ambient/test_reflect.py`:
```python
import pytest

from mypalclara.ambient import journal, reflect


class _Orch:
    async def generate_with_tools(self, *, messages, tools, user_id, request_id, websocket=None):
        self.seen_messages = messages
        self.seen_tools = tools
        yield {"type": "complete", "text": "Today I noticed the plan is taking shape.\n\nJournal: steady."}


class _ToolExec:
    async def get_all_tools(self, *, user_id):
        return [
            {"type": "function", "function": {"name": "search_memory"}},
            {"type": "function", "function": {"name": "send_discord_buttons"}},  # must be filtered out
            {"type": "function", "function": {"name": "remember"}},
        ]


@pytest.mark.asyncio
async def test_reflect_writes_journal_and_filters_tools(tmp_path, monkeypatch):
    monkeypatch.setenv("AMBIENT_JOURNAL_DIR", str(tmp_path))
    import importlib
    importlib.reload(journal)
    importlib.reload(reflect)
    # restrict allowlist to known names for the test
    monkeypatch.setattr(reflect, "REFLECTION_TOOL_ALLOWLIST", {"search_memory", "remember"})

    orch = _Orch()
    text = await reflect.reflect("discord-1", orchestrator=orch, tool_executor=_ToolExec())

    assert "plan is taking shape" in text
    # journal got the reflection
    assert "plan is taking shape" in journal.read_recent("discord-1", days=1)
    # destructive/irrelevant tools filtered out
    names = {t["function"]["name"] for t in orch.seen_tools}
    assert names == {"search_memory", "remember"}


@pytest.mark.asyncio
async def test_reflect_empty_text_skips_journal(tmp_path, monkeypatch):
    monkeypatch.setenv("AMBIENT_JOURNAL_DIR", str(tmp_path))
    import importlib
    importlib.reload(journal)
    importlib.reload(reflect)

    class _Empty:
        async def generate_with_tools(self, **kw):
            yield {"type": "complete", "text": "   "}

    class _TE:
        async def get_all_tools(self, *, user_id):
            return []

    text = await reflect.reflect("discord-1", orchestrator=_Empty(), tool_executor=_TE())
    assert text.strip() == ""
    assert journal.read_recent("discord-1", days=1) == ""
```

- [ ] **Step 3: Run to verify it fails** — FAIL (module missing).

- [ ] **Step 4: Implement**

Create `mypalclara/ambient/reflect.py`:
```python
"""Phase 1: silent, tool-enabled reflection → journal + memory."""

from __future__ import annotations

from typing import Any

from mypalclara.ambient import journal
from mypalclara.ambient.config import AMBIENT_JOURNAL_READBACK_DAYS
from mypalclara.ambient.prompts import REFLECTION_PROMPT
from mypalclara.ambient.silent_turn import run_silent_turn
from mypalclara.config.logging import get_logger
from mypalclara.db.models import gen_uuid

logger = get_logger("ambient.reflect")

# Memory-only allowlist. Replace these with the REAL tool names found in Task D2 Step 1.
REFLECTION_TOOL_ALLOWLIST: set[str] = {"search_memory", "recall_memory", "remember"}


def _filter_tools(all_tools: list, allow: set[str]) -> list:
    out = []
    for t in all_tools:
        name = t.get("function", {}).get("name") if isinstance(t, dict) else None
        if name in allow:
            out.append(t)
    return out


async def reflect(user_id: str, *, orchestrator: Any, tool_executor: Any) -> str:
    recent = journal.read_recent(user_id, days=AMBIENT_JOURNAL_READBACK_DAYS)
    all_tools = await tool_executor.get_all_tools(user_id=user_id)
    tools = _filter_tools(all_tools, REFLECTION_TOOL_ALLOWLIST)

    from mypalclara.core.llm.messages import SystemMessage, UserMessage

    messages = [
        SystemMessage(content=REFLECTION_PROMPT),
        SystemMessage(content=f"## Your recent journal\n\n{recent or '(empty — a fresh start)'}"),
        UserMessage(content="Reflect now. Consolidate, notice patterns, update memory as useful, "
                            "and end with a short journal entry."),
    ]
    text = await run_silent_turn(orchestrator, messages, tools, user_id, f"ambient-reflect-{gen_uuid()}")
    if text.strip():
        journal.append_entry(user_id, text)
    else:
        logger.info(f"reflect: empty reflection for {user_id}; nothing journaled")
    return text
```

- [ ] **Step 5: Run + commit** — `pytest tests/ambient/test_reflect.py -q` → PASS, then `git add -A && git commit -m "feat(ambient): Phase 1 reflect()"`

### Task D3: Phase 2 — surface_gate()

**Files:**
- Create: `mypalclara/ambient/surface_gate.py`
- Test: `tests/ambient/test_surface_gate.py`

- [ ] **Step 1: Write the failing test**

Create `tests/ambient/test_surface_gate.py`:
```python
import pytest

from mypalclara.ambient import surface_gate


def _llm(returns):
    async def fn(messages):
        return returns
    return fn


@pytest.mark.asyncio
async def test_parses_queue_decision():
    out = await surface_gate.surface_gate(
        "discord-1", "reflection text",
        gate_llm=_llm('{"decision": "queue", "content": "ask about the trip", "reason": "follow-up"}'),
    )
    assert out["decision"] == "queue"
    assert out["content"] == "ask about the trip"


@pytest.mark.asyncio
async def test_strips_code_fences():
    out = await surface_gate.surface_gate(
        "discord-1", "x",
        gate_llm=_llm('```json\n{"decision": "urgent", "content": "deadline today", "reason": "time"}\n```'),
    )
    assert out["decision"] == "urgent"
    assert out["content"] == "deadline today"


@pytest.mark.asyncio
async def test_unparseable_defaults_to_nothing():
    out = await surface_gate.surface_gate("discord-1", "x", gate_llm=_llm("I think maybe..."))
    assert out["decision"] == "nothing"


@pytest.mark.asyncio
async def test_invalid_decision_defaults_to_nothing():
    out = await surface_gate.surface_gate(
        "discord-1", "x", gate_llm=_llm('{"decision": "SHOUT", "content": "hi"}'),
    )
    assert out["decision"] == "nothing"


@pytest.mark.asyncio
async def test_llm_error_defaults_to_nothing():
    async def boom(messages):
        raise RuntimeError("llm down")
    out = await surface_gate.surface_gate("discord-1", "x", gate_llm=boom)
    assert out["decision"] == "nothing"
```

- [ ] **Step 2: Run to verify it fails** — FAIL (module missing).

- [ ] **Step 3: Implement**

Create `mypalclara/ambient/surface_gate.py`:
```python
"""Phase 2: decide whether a reflection is worth surfacing (high bar)."""

from __future__ import annotations

import json
from typing import Any

from mypalclara.ambient.prompts import SURFACE_GATE_PROMPT
from mypalclara.config.logging import get_logger

logger = get_logger("ambient.surface_gate")

VALID_DECISIONS = {"nothing", "queue", "urgent"}
_NOTHING = {"decision": "nothing", "content": "", "reason": ""}


def _parse(raw: str) -> dict:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        data = json.loads(text)
    except Exception:
        return dict(_NOTHING, reason="unparseable")
    if not isinstance(data, dict):
        return dict(_NOTHING, reason="not-an-object")
    decision = str(data.get("decision", "nothing")).lower()
    if decision not in VALID_DECISIONS:
        decision = "nothing"
    return {
        "decision": decision,
        "content": str(data.get("content", "")).strip(),
        "reason": str(data.get("reason", "")).strip(),
    }


async def surface_gate(user_id: str, reflection: str, *, gate_llm: Any) -> dict:
    from mypalclara.core.llm.messages import SystemMessage, UserMessage

    messages = [
        SystemMessage(content=SURFACE_GATE_PROMPT),
        UserMessage(content=f"Reflection:\n{reflection}\n\nReturn the JSON decision."),
    ]
    try:
        raw = await gate_llm(messages)
    except Exception as e:
        logger.error(f"surface_gate LLM failed for {user_id}: {e}")
        return dict(_NOTHING, reason="llm-error")
    return _parse(str(raw))
```

- [ ] **Step 4: Run + commit** — PASS, then `git add -A && git commit -m "feat(ambient): Phase 2 surface_gate()"`

---

## Milestone E — Orchestration + scheduling + wiring

### Task E1: ambient_turn() orchestration

**Files:**
- Create: `mypalclara/ambient/loop.py`
- Test: `tests/ambient/test_loop.py`

- [ ] **Step 1: Write the failing test**

Create `tests/ambient/test_loop.py`:
```python
from datetime import datetime, timezone

import pytest

from mypalclara.ambient import loop


class _Deps:
    """Bundle of injected fakes for ambient_turn."""

    def __init__(self, gate_decision):
        self.sent = []
        self.enqueued = []
        self._gate = gate_decision

        async def reflect(user_id, **kw):
            return "reflection text"

        async def gate(user_id, reflection, **kw):
            return self._gate

        async def send_fn(user_id, channel_id, content):
            self.sent.append((user_id, channel_id, content))
            return True

        def enqueue(user_id, content, **kw):
            self.enqueued.append((user_id, content, kw.get("kind")))

        self.reflect = reflect
        self.gate = gate
        self.send_fn = send_fn
        self.enqueue = enqueue


def _cfg(tz="America/New_York", last_dm=None):
    return loop.UserCtx(timezone=tz, last_dm_at=last_dm)


@pytest.mark.asyncio
async def test_nothing_decision_sends_and_queues_nothing(monkeypatch):
    d = _Deps({"decision": "nothing", "content": "", "reason": ""})
    monkeypatch.setattr(loop, "recently_active", lambda *a, **k: False)
    await loop.ambient_turn(
        "discord-1", orchestrator=None, tool_executor=None, gate_llm=None,
        send_fn=d.send_fn, now_utc=datetime(2026, 6, 14, 12, 0, tzinfo=timezone.utc),
        _reflect=d.reflect, _gate=d.gate, _enqueue=d.enqueue, _user_ctx=lambda u, **k: _cfg(),
    )
    assert d.sent == [] and d.enqueued == []


@pytest.mark.asyncio
async def test_queue_decision_enqueues(monkeypatch):
    d = _Deps({"decision": "queue", "content": "mention the trip", "reason": "x"})
    monkeypatch.setattr(loop, "recently_active", lambda *a, **k: False)
    await loop.ambient_turn(
        "discord-1", orchestrator=None, tool_executor=None, gate_llm=None,
        send_fn=d.send_fn, now_utc=datetime(2026, 6, 14, 12, 0, tzinfo=timezone.utc),
        _reflect=d.reflect, _gate=d.gate, _enqueue=d.enqueue, _user_ctx=lambda u, **k: _cfg(),
    )
    assert d.enqueued and d.enqueued[0][2] == "queue"
    assert d.sent == []


@pytest.mark.asyncio
async def test_urgent_in_hours_sends_dm(monkeypatch):
    d = _Deps({"decision": "urgent", "content": "deadline today!", "reason": "time"})
    monkeypatch.setattr(loop, "recently_active", lambda *a, **k: False)
    recorded = []
    await loop.ambient_turn(
        "discord-1", orchestrator=None, tool_executor=None, gate_llm=None,
        send_fn=d.send_fn, now_utc=datetime(2026, 6, 14, 12, 0, tzinfo=timezone.utc),  # 08:00 NY
        _reflect=d.reflect, _gate=d.gate, _enqueue=d.enqueue, _user_ctx=lambda u, **k: _cfg(),
        _record_dm=lambda u, t, **k: recorded.append(u),
    )
    assert d.sent == [("discord-1", "dm-discord-1", "deadline today!")]
    assert recorded == ["discord-1"]


@pytest.mark.asyncio
async def test_urgent_outside_hours_downgrades_to_queue(monkeypatch):
    d = _Deps({"decision": "urgent", "content": "late thought", "reason": "x"})
    monkeypatch.setattr(loop, "recently_active", lambda *a, **k: False)
    await loop.ambient_turn(
        "discord-1", orchestrator=None, tool_executor=None, gate_llm=None,
        send_fn=d.send_fn, now_utc=datetime(2026, 6, 14, 2, 0, tzinfo=timezone.utc),  # 22:00 NY
        _reflect=d.reflect, _gate=d.gate, _enqueue=d.enqueue, _user_ctx=lambda u, **k: _cfg(),
    )
    assert d.sent == []
    assert d.enqueued and d.enqueued[0][2] == "queue"


@pytest.mark.asyncio
async def test_recent_activity_skips_everything(monkeypatch):
    d = _Deps({"decision": "urgent", "content": "x", "reason": "y"})
    monkeypatch.setattr(loop, "recently_active", lambda *a, **k: True)  # active → skip
    await loop.ambient_turn(
        "discord-1", orchestrator=None, tool_executor=None, gate_llm=None,
        send_fn=d.send_fn, now_utc=datetime(2026, 6, 14, 12, 0, tzinfo=timezone.utc),
        _reflect=d.reflect, _gate=d.gate, _enqueue=d.enqueue, _user_ctx=lambda u, **k: _cfg(),
    )
    assert d.sent == [] and d.enqueued == []
```

- [ ] **Step 2: Run to verify it fails** — FAIL (module missing).

- [ ] **Step 3: Implement**

Create `mypalclara/ambient/loop.py`:
```python
"""ambient_turn(): one user's reflect → gate → deliver/queue, with guards.

Dependencies are injected (reflect/gate/enqueue/send_fn/user-ctx) so the
orchestration is unit-testable without a DB or LLM. The startup wiring binds
the real implementations.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from mypalclara.ambient.config import (
    AMBIENT_ACTIVE_HOURS,
    AMBIENT_MIN_DM_GAP_HOURS,
    AMBIENT_QUEUE_TTL_DAYS,
    AMBIENT_RECENT_ACTIVITY_SKIP_MIN,
)
from mypalclara.ambient.guards import in_active_hours, past_min_gap, recently_active
from mypalclara.config.logging import get_logger

logger = get_logger("ambient.loop")


@dataclass
class UserCtx:
    timezone: str | None = None
    last_dm_at: datetime | None = None


def _default_user_ctx(user_id: str, *, session_factory=None) -> UserCtx:
    if session_factory is None:
        from mypalclara.db.connection import SessionLocal

        session_factory = SessionLocal
    from mypalclara.db.models import AmbientUserConfig

    db = session_factory()
    try:
        row = db.query(AmbientUserConfig).filter_by(user_id=user_id).first()
        if row is None:
            return UserCtx()
        return UserCtx(timezone=row.timezone, last_dm_at=row.last_dm_at)
    finally:
        db.close()


def _default_record_dm(user_id: str, now_naive: datetime, *, session_factory=None) -> None:
    if session_factory is None:
        from mypalclara.db.connection import SessionLocal

        session_factory = SessionLocal
    from mypalclara.db.models import AmbientUserConfig

    db = session_factory()
    try:
        row = db.query(AmbientUserConfig).filter_by(user_id=user_id).first()
        if row is None:
            row = AmbientUserConfig(user_id=user_id, reflection_opt_in="true")
            db.add(row)
        row.last_dm_at = now_naive
        db.commit()
    finally:
        db.close()


async def ambient_turn(
    user_id: str,
    *,
    orchestrator: Any,
    tool_executor: Any,
    gate_llm: Any,
    send_fn: Callable,
    now_utc: datetime | None = None,
    # injectable seams (default to real implementations):
    _reflect: Callable | None = None,
    _gate: Callable | None = None,
    _enqueue: Callable | None = None,
    _user_ctx: Callable | None = None,
    _record_dm: Callable | None = None,
) -> None:
    now_utc = now_utc or datetime.now(timezone.utc)
    now_naive = now_utc.astimezone(timezone.utc).replace(tzinfo=None)

    if recently_active(user_id, AMBIENT_RECENT_ACTIVITY_SKIP_MIN):
        logger.info(f"ambient_turn: {user_id} recently active — skipping")
        return

    reflect_fn = _reflect or (lambda uid, **kw: _import_reflect()(uid, **kw))
    gate_fn = _gate or (lambda uid, refl, **kw: _import_gate()(uid, refl, **kw))
    enqueue_fn = _enqueue or _import_enqueue()
    user_ctx_fn = _user_ctx or _default_user_ctx
    record_dm_fn = _record_dm or _default_record_dm

    reflection = await reflect_fn(user_id, orchestrator=orchestrator, tool_executor=tool_executor)
    if not (reflection or "").strip():
        return

    decision = await gate_fn(user_id, reflection, gate_llm=gate_llm)
    d = decision.get("decision", "nothing")
    content = decision.get("content", "")
    if d == "nothing" or not content:
        return

    expires = now_naive + timedelta(days=AMBIENT_QUEUE_TTL_DAYS)

    if d == "queue":
        enqueue_fn(user_id, content, kind="queue", expires_at=expires)
        return

    if d == "urgent":
        ctx = user_ctx_fn(user_id)
        ok_hours = in_active_hours(ctx.timezone, now_utc, AMBIENT_ACTIVE_HOURS)
        ok_gap = past_min_gap(ctx.last_dm_at, now_naive, AMBIENT_MIN_DM_GAP_HOURS)
        if ok_hours and ok_gap:
            delivered = await send_fn(user_id, f"dm-{user_id}", content)
            if delivered:
                record_dm_fn(user_id, now_naive)
        else:
            logger.info(f"ambient_turn: urgent for {user_id} downgraded to queue "
                        f"(hours={ok_hours}, gap={ok_gap})")
            enqueue_fn(user_id, content, kind="queue", expires_at=expires)


def _import_reflect():
    from mypalclara.ambient.reflect import reflect

    return reflect


def _import_gate():
    from mypalclara.ambient.surface_gate import surface_gate

    return surface_gate


def _import_enqueue():
    from mypalclara.ambient.queue import enqueue

    return enqueue
```

- [ ] **Step 4: Run + commit** — `pytest tests/ambient/test_loop.py -q` → PASS, then `git add -A && git commit -m "feat(ambient): ambient_turn orchestration + guards"`

### Task E2: Opted-in user lookup + scheduling registration

**Files:**
- Create: `mypalclara/ambient/scheduling.py`
- Test: `tests/ambient/test_scheduling.py`

- [ ] **Step 1: Write the failing test**

Create `tests/ambient/test_scheduling.py`:
```python
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from mypalclara.ambient import scheduling
from mypalclara.db.models import AmbientUserConfig, Base


def _factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/u.db")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)


def test_opted_in_users(tmp_path):
    sf = _factory(tmp_path)
    db = sf()
    db.add(AmbientUserConfig(user_id="discord-1", reflection_opt_in="true"))
    db.add(AmbientUserConfig(user_id="discord-2", reflection_opt_in="false"))
    db.commit()
    db.close()
    assert scheduling.get_opted_in_users(session_factory=sf) == ["discord-1"]


def test_register_adds_one_cron_task():
    added = []

    class _Sched:
        def add_task(self, task):
            added.append(task)

    async def runner():
        return None

    scheduling.register_ambient_task(_Sched(), runner=runner, cron="0 11 * * *")
    assert len(added) == 1
    assert added[0].name == "ambient_tick"
    assert added[0].cron == "0 11 * * *"
    assert added[0].handler is runner
```

- [ ] **Step 2: Run to verify it fails** — FAIL (module missing).

- [ ] **Step 3: Implement**

Create `mypalclara/ambient/scheduling.py`:
```python
"""Register a single cron 'ambient_tick' that fans out over opted-in users."""

from __future__ import annotations

from typing import Callable

from mypalclara.ambient.config import AMBIENT_CRON
from mypalclara.config.logging import get_logger
from mypalclara.gateway.scheduler import ScheduledTask, TaskType

logger = get_logger("ambient.scheduling")


def get_opted_in_users(*, session_factory=None) -> list[str]:
    if session_factory is None:
        from mypalclara.db.connection import SessionLocal

        session_factory = SessionLocal
    from mypalclara.db.models import AmbientUserConfig

    db = session_factory()
    try:
        rows = (
            db.query(AmbientUserConfig)
            .filter(AmbientUserConfig.reflection_opt_in == "true")
            .all()
        )
        return [r.user_id for r in rows]
    finally:
        db.close()


def register_ambient_task(scheduler, *, runner: Callable, cron: str | None = None) -> None:
    task = ScheduledTask(
        name="ambient_tick",
        type=TaskType.CRON,
        handler=runner,
        cron=cron or AMBIENT_CRON,
        description="Unified ambient reflection — reflect + surface for opted-in users",
    )
    scheduler.add_task(task)
    logger.info(f"Registered ambient_tick (cron={task.cron})")
```

- [ ] **Step 4: Run + commit** — PASS, then `git add -A && git commit -m "feat(ambient): scheduling registration + opted-in lookup"`

### Task E3: Wire into startup; remove the heartbeat

**Files:**
- Modify: `mypalclara/gateway/__main__.py` (remove heartbeat block ~532–592; add ambient wiring after `await scheduler.start()` / processor init)
- Delete: `mypalclara/core/heartbeat.py`
- Delete: `mypalclara/workspace/HEARTBEAT.md` (if present)
- Modify: `.env*` (remove `HEARTBEAT_*`; add `AMBIENT_*`)
- Test: covered by existing import/boot smoke + Task F

- [ ] **Step 1: Identify the orchestrator + tool_executor handles on the processor**

Run:
```bash
grep -nE "self\._(llm_orchestrator|orchestrator|tool_executor)\b|LLMOrchestrator\(|ToolExecutor\(" mypalclara/gateway/processor.py | head
```
Record the exact attribute names (e.g. `processor._llm_orchestrator`, `processor._tool_executor`). Use them in Step 3.

- [ ] **Step 2: Remove the heartbeat wiring block**

In `mypalclara/gateway/__main__.py`, delete the entire `# Start heartbeat loop if enabled` block (the `if os.getenv("HEARTBEAT_ENABLED", ...)` through `logger.info("Heartbeat loop started")`, ~lines 532–592), including the nested `_heartbeat_llm_async` and `_heartbeat_send`.

- [ ] **Step 3: Add the ambient wiring**

In `mypalclara/gateway/__main__.py`, after `await scheduler.start()` (and after `await processor.initialize()`), insert (adjust the two attribute names per Step 1):
```python
    # Start unified ambient reflection if enabled
    from mypalclara.ambient.config import AMBIENT_ENABLED

    if AMBIENT_ENABLED:
        import asyncio

        from mypalclara.ambient.loop import ambient_turn
        from mypalclara.ambient.scheduling import get_opted_in_users, register_ambient_task
        from mypalclara.core.llm.compat import make_llm

        _gate_llm_sync = make_llm(tier="mid")

        async def _gate_llm(messages):
            return await asyncio.get_running_loop().run_in_executor(None, _gate_llm_sync, messages)

        async def _ambient_send(user_id: str, channel_id: str, content: str) -> bool:
            from mypal_protocol import ChannelInfo, ProactiveMessage, UserInfo

            platform = user_id.split("-", 1)[0] if "-" in user_id else "unknown"
            platform_user_id = user_id.split("-", 1)[1] if "-" in user_id else user_id
            # channel_id is "dm-<user_id>" → deliver to the user's DM
            channel_type = "dm" if str(channel_id).startswith("dm-") else "server"
            raw_channel_id = platform_user_id if channel_type == "dm" else channel_id

            delivered = 0
            nodes = await server.node_registry.get_all_nodes()
            for node in nodes:
                if node.platform and node.platform != platform:
                    continue
                try:
                    msg = ProactiveMessage(
                        user=UserInfo(id=user_id, platform_id=platform_user_id, name=None),
                        channel=ChannelInfo(id=raw_channel_id, type=channel_type),
                        content=content,
                        priority="normal",
                    )
                    await node.websocket.send(msg.model_dump_json())
                    delivered += 1
                except Exception as e:
                    logger.warning(f"Failed to send ambient DM to {node.node_id}: {e}")
            return delivered > 0

        _orchestrator = processor._llm_orchestrator  # confirm name in Step 1
        _tool_executor = processor._tool_executor      # confirm name in Step 1

        async def _ambient_tick():
            for uid in get_opted_in_users():
                try:
                    await ambient_turn(
                        uid,
                        orchestrator=_orchestrator,
                        tool_executor=_tool_executor,
                        gate_llm=_gate_llm,
                        send_fn=_ambient_send,
                    )
                except Exception as e:
                    logger.error(f"ambient_turn failed for {uid}: {e}")

        register_ambient_task(scheduler, runner=_ambient_tick)
        logger.info("Ambient reflection registered")
```
> The scheduler must already be started; `add_task` after `start()` is supported (it recalculates `next_run` on add).

- [ ] **Step 4: Delete heartbeat code + file**

Run:
```bash
rm -f mypalclara/core/heartbeat.py mypalclara/workspace/HEARTBEAT.md
grep -rnE "heartbeat" mypalclara tests --include="*.py" | grep -viE "ambient" | head
```
Remove any remaining heartbeat imports/tests surfaced (e.g. delete `tests/**/test_heartbeat*.py`).

- [ ] **Step 5: Update env files**

Remove `HEARTBEAT_*` lines from `.env*`. Add an ambient block to `.env`, `.env.docker.example`, `.env.remote`:
```bash
# ============================================================================
# AMBIENT REFLECTION (unified — replaces ORS + heartbeat)
# ============================================================================
AMBIENT_ENABLED=false
# AMBIENT_CRON="0 11,14,17,20 * * *"   # waking-hours reflection cadence (server time)
# AMBIENT_MIN_DM_GAP_HOURS=4           # min hours between unsolicited DMs
# AMBIENT_ACTIVE_HOURS=8-22            # user-local hour window for DMs
# AMBIENT_JOURNAL_READBACK_DAYS=3      # days of journal re-read each reflection
# AMBIENT_RECENT_ACTIVITY_SKIP_MIN=15  # skip a turn if user messaged this recently
# AMBIENT_QUEUE_TTL_DAYS=5             # queued thoughts expire after N days
```

- [ ] **Step 6: Boot smoke test + commit**

Run:
```bash
python -c "import mypalclara.gateway.__main__; print('boot import ok')"
ruff check . && pytest -q
git add -A
git commit -m "feat(ambient): wire ambient tick into startup; remove heartbeat"
```
Expected: import ok; suite PASS.

---

## Milestone F — Context injection + final verification

### Task F1: Inject queued thoughts into the next message

**Files:**
- Modify: `mypalclara/gateway/processor.py` (in `_build_context`, after the fired-intentions insert ~line 695–700)
- Create: `mypalclara/ambient/inject.py` (pure formatter, unit-tested)
- Test: `tests/ambient/test_inject.py`

- [ ] **Step 1: Write the failing test**

Create `tests/ambient/test_inject.py`:
```python
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from mypalclara.ambient import inject, queue
from mypalclara.db.models import Base


def _factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/i.db")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)


def test_collect_formats_and_marks_delivered(tmp_path):
    sf = _factory(tmp_path)
    queue.enqueue("discord-1", "ask about the trip", session_factory=sf)
    queue.enqueue("discord-1", "the build is green now", session_factory=sf)

    block = inject.collect_surfaced_block("discord-1", session_factory=sf)
    assert "ask about the trip" in block
    assert "the build is green now" in block

    # second call returns empty — they were marked delivered
    assert inject.collect_surfaced_block("discord-1", session_factory=sf) == ""


def test_no_thoughts_returns_empty(tmp_path):
    sf = _factory(tmp_path)
    assert inject.collect_surfaced_block("discord-1", session_factory=sf) == ""
```

- [ ] **Step 2: Run to verify it fails** — FAIL (module missing).

- [ ] **Step 3: Implement the formatter**

Create `mypalclara/ambient/inject.py`:
```python
"""Collect undelivered surfaced thoughts and format them for context injection."""

from __future__ import annotations

from mypalclara.ambient import queue

_HEADER = (
    "## Thoughts from your reflection time\n\n"
    "While the user was away, you noted these to raise when you next spoke. "
    "Weave any that still fit in naturally; don't dump them as a list.\n"
)


def collect_surfaced_block(user_id: str, *, session_factory=None) -> str:
    rows = queue.fetch_undelivered(user_id, session_factory=session_factory)
    if not rows:
        return ""
    bullets = "\n".join(f"- {r.content}" for r in rows)
    queue.mark_delivered([r.id for r in rows], session_factory=session_factory)
    return f"{_HEADER}\n{bullets}"
```

- [ ] **Step 4: Run to verify formatter passes** — `pytest tests/ambient/test_inject.py -q` → PASS.

- [ ] **Step 5: Wire into `_build_context`**

In `mypalclara/gateway/processor.py`, locate the fired-intentions insert (~lines 695–700):
```python
        # Add fired intentions as reminders
        if fired_intentions:
            intention_text = self._memory_manager.format_intentions_for_prompt(fired_intentions)
            if intention_text:
                messages.insert(2, SystemMessage(content=intention_text))
```
Immediately after that block, add:
```python
        # Inject any reflection thoughts queued while the user was away
        try:
            from mypalclara.ambient.inject import collect_surfaced_block

            surfaced = collect_surfaced_block(user_id)
            if surfaced:
                messages.insert(2, SystemMessage(content=surfaced))
        except Exception as e:
            logger.warning(f"ambient surfaced-thought injection failed: {e}")
```
(Confirm `SystemMessage` is already imported in this file — it is used by the intentions insert above. Confirm `logger` exists in scope.)

- [ ] **Step 6: Boot smoke + commit**

Run:
```bash
python -c "import mypalclara.gateway.processor; print('ok')"
pytest tests/ambient -q
git add -A
git commit -m "feat(ambient): inject queued thoughts into next message context"
```

### Task F2: Full verification + integration sweep

**Files:**
- Create: `tests/ambient/test_integration_turn.py`

- [ ] **Step 1: Write an end-to-end turn test (stubbed LLM, real queue DB)**

Create `tests/ambient/test_integration_turn.py`:
```python
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from mypalclara.ambient import inject, loop, queue
from mypalclara.db.models import Base


def _factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/e2e.db")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)


@pytest.mark.asyncio
async def test_queue_decision_lands_and_injects(tmp_path, monkeypatch):
    sf = _factory(tmp_path)
    monkeypatch.setattr(loop, "recently_active", lambda *a, **k: False)

    async def reflect(uid, **kw):
        return "I keep coming back to the migration plan."

    async def gate(uid, refl, **kw):
        return {"decision": "queue", "content": "follow up on the migration plan", "reason": "thread"}

    async def send_fn(uid, ch, content):
        raise AssertionError("queue decision must not DM")

    def enqueue(uid, content, **kw):
        queue.enqueue(uid, content, kind=kw.get("kind", "queue"),
                      expires_at=kw.get("expires_at"), session_factory=sf)

    await loop.ambient_turn(
        "discord-1", orchestrator=None, tool_executor=None, gate_llm=None,
        send_fn=send_fn, now_utc=datetime(2026, 6, 14, 16, 0, tzinfo=timezone.utc),
        _reflect=reflect, _gate=gate, _enqueue=enqueue,
        _user_ctx=lambda u, **k: loop.UserCtx(timezone="America/New_York"),
    )

    block = inject.collect_surfaced_block("discord-1", session_factory=sf)
    assert "follow up on the migration plan" in block
```

- [ ] **Step 2: Run the whole ambient suite + architecture test + lint**

Run:
```bash
pytest tests/ambient -q
pytest tests/architecture/test_engine_boundary.py -q
ruff check . && ruff format --check .
pytest -q
```
Expected: all PASS. (`pytest -q` runs the full unit suite; integration tests stay deselected by default.)

- [ ] **Step 3: Manual boot check**

Run:
```bash
AMBIENT_ENABLED=true python -c "import mypalclara.gateway.__main__ as m; print('imports clean with ambient on')"
grep -rniE "heartbeat|ORS_|services.proactive|ProactiveNote" mypalclara --include="*.py" | grep -vi "ambient" | head
```
Expected: clean import; the grep returns nothing (all heartbeat/ORS references gone; `ProactiveMessage` is fine and won't match `ProactiveNote`).

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "test(ambient): end-to-end turn + verification sweep"
```

---

## Self-Review

**1. Spec coverage** (design doc → tasks):
- Two-phase architecture / firewall → D1 (silent turn, websocket=None), D2 (reflect, no delivery), D3 (gate), E1 (only urgent path calls send_fn). ✓
- Fixed cron, waking hours → E2 (`register_ambient_task`, `AMBIENT_CRON`); timezone honored for outreach via E1 + C4 active-hours. ✓ (Simplification: one tick fans out over users; documented in plan header.)
- Journal + memory extraction → C2 (journal), D2 (memory tools in the silent turn + journal append). ✓
- Hybrid surfacing (queue default, urgent DM, downgrade) → E1 + C3 (queue) + E3 (`_ambient_send`). ✓
- Owner + opted-in → B1 (`AmbientUserConfig`), E2 (`get_opted_in_users`). ✓
- Guards (recent-activity, min-gap, active-hours, queue expiry) → C4, C3, E1. ✓ (Token budget: env present, enforcement deferred — noted; cron rate-limits the loop.)
- ORS retirement folded in → A1–A3. ✓
- Heartbeat absorption → E3. ✓
- Context injection on next message → F1. ✓
- Tests incl. "silent turn emits nothing" + arch test green → D1, F2. ✓

**2. Placeholder scan:** The only deferred item is the exact memory-tool names in `REFLECTION_TOOL_ALLOWLIST` — handled by an explicit discovery step (D2 Step 1) and a test that pins the filter behavior with known names. The orchestrator/tool_executor attribute names are resolved by an explicit grep step (E3 Step 1). No "TODO/handle errors/etc." placeholders.

**3. Type consistency:** `ambient_turn(... send_fn, now_utc, _reflect, _gate, _enqueue, _user_ctx, _record_dm)` matches the tests; `UserCtx(timezone, last_dm_at)` consistent; `surface_gate` returns `{decision, content, reason}` consumed by `loop`; `queue.enqueue/fetch_undelivered/mark_delivered(session_factory=...)` consistent across queue/inject/tests; decision strings `nothing|queue|urgent` consistent; string-boolean `"true"/"false"` used in models, scheduling filter, and `_record_dm`. ✓
