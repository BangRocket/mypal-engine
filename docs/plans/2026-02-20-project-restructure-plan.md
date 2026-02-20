# Project Restructure Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Consolidate all Python packages under a single `mypalclara` top-level package.

**Architecture:** Move 9 top-level Python packages into `mypalclara/`, update all import paths via bulk find-replace, delete 4 dead directories. The restructure is purely organizational — no behavior changes.

**Tech Stack:** Python, git mv, sed/grep for import replacement, pytest for verification.

**Design doc:** `docs/plans/2026-02-20-project-restructure-design.md`

---

### Task 1: Create Feature Branch and Establish Baseline

**Files:**
- None (git operations only)

**Step 1: Create feature branch**

```bash
git checkout -b restructure/consolidate-packages
```

**Step 2: Run existing tests to establish passing baseline**

Run: `poetry run pytest tests/ -x -q --timeout=30 2>&1 | tail -20`
Expected: Note which tests pass and which have pre-existing failures. Record this baseline.

**Step 3: Commit the design doc**

```bash
git add docs/plans/2026-02-20-project-restructure-design.md docs/plans/2026-02-20-project-restructure-plan.md
git commit -m "docs: add project restructure design and plan"
```

---

### Task 2: Delete Dead Directories

**Files:**
- Delete: `clara_files/` (empty)
- Delete: `gateway/` at root (empty legacy dir with empty `providers/` subdir)
- Delete: `storage/` (re-export shim, zero imports anywhere)
- Delete: `vendor/` (empty directory tree, zero tracked files)

**Step 1: Delete the directories**

```bash
rm -rf clara_files/ gateway/ vendor/
git rm -r storage/
```

Note: `clara_files/`, `gateway/`, `vendor/` have no tracked files so just `rm -rf`. `storage/` has 2 tracked files so needs `git rm`.

**Step 2: Verify nothing references them**

Run: `grep -r "from storage\b\|import storage\b" --include="*.py" . | grep -v __pycache__`
Expected: No matches (already verified: zero imports).

Run: `grep -r "from vendor\b\|import vendor\b" --include="*.py" . | grep -v __pycache__`
Expected: No matches.

**Step 3: Commit**

```bash
git add -A
git commit -m "chore: remove dead directories (clara_files, gateway, storage, vendor)"
```

---

### Task 3: Move Core Packages Into mypalclara/

This is the big structural move. Do all `git mv` operations in one batch, then commit before changing any imports.

**Files:**
- Move: `clara_core/` → `mypalclara/core/`
- Move: `db/` → `mypalclara/db/`
- Move: `config/` → `mypalclara/config/`
- Move: `tools/` → `mypalclara/tools/`
- Move: `adapters/` → `mypalclara/adapters/`
- Move: `sandbox/` → `mypalclara/sandbox/`
- Move: `email_service/` → `mypalclara/services/email/`
- Move: `backup_service/` → `mypalclara/services/backup/`
- Move: `proactive/` → `mypalclara/services/proactive/`
- Create: `mypalclara/services/__init__.py`

**Step 1: Create target directories**

```bash
mkdir -p mypalclara/services
```

**Step 2: Move all packages (git mv preserves history)**

```bash
git mv clara_core mypalclara/core
git mv db mypalclara/db
git mv config mypalclara/config
git mv tools mypalclara/tools
git mv adapters mypalclara/adapters
git mv sandbox mypalclara/sandbox
git mv email_service mypalclara/services/email
git mv backup_service mypalclara/services/backup
git mv proactive mypalclara/services/proactive
```

**Step 3: Create services __init__.py**

```python
# mypalclara/services/__init__.py
"""Clara services — email monitoring, database backup, proactive engagement."""
```

**Step 4: Commit the moves (before import changes)**

```bash
git add -A
git commit -m "refactor: move all packages under mypalclara/ namespace

Structural move only — imports not yet updated. Code will not run until
imports are fixed in subsequent commits."
```

---

### Task 4: Update All Import Paths — clara_core → mypalclara.core

**Scope:** 96 files. This is the largest change.

**Step 1: Bulk replace in all Python files**

Replace these patterns across the entire codebase (excluding .git/):
- `from clara_core` → `from mypalclara.core`
- `import clara_core` → `import mypalclara.core`

Use find + sed:
```bash
find mypalclara/ tests/ scripts/ -name '*.py' -exec sed -i '' \
  -e 's/from clara_core/from mypalclara.core/g' \
  -e 's/import clara_core/import mypalclara.core/g' \
  {} +
```

**Step 2: Verify no stale references remain**

Run: `grep -r "from clara_core\|import clara_core" --include="*.py" mypalclara/ tests/ scripts/`
Expected: Zero matches.

**Step 3: Commit**

```bash
git add -A
git commit -m "refactor: update clara_core → mypalclara.core imports"
```

---

### Task 5: Update All Import Paths — config → mypalclara.config

**Scope:** 48 files.

**Step 1: Bulk replace**

```bash
find mypalclara/ tests/ scripts/ -name '*.py' -exec sed -i '' \
  -e 's/from config\./from mypalclara.config./g' \
  -e 's/from config import/from mypalclara.config import/g' \
  -e 's/^import config\b/import mypalclara.config/g' \
  {} +
```

IMPORTANT: Use `from config\.` (with dot) and `from config import` patterns to avoid matching things like `from configparser` or `from mypalclara.core.config`.

**Step 2: Verify no stale references**

Run: `grep -rn "^from config\b\|^import config\b" --include="*.py" mypalclara/ tests/ scripts/ | grep -v "mypalclara.config"`
Expected: Zero matches.

**Step 3: Commit**

```bash
git add -A
git commit -m "refactor: update config → mypalclara.config imports"
```

---

### Task 6: Update All Import Paths — db → mypalclara.db

**Scope:** 42 files (includes ~18 dynamic/lazy imports inside functions).

**Step 1: Bulk replace**

```bash
find mypalclara/ tests/ scripts/ -name '*.py' -exec sed -i '' \
  -e 's/from db\./from mypalclara.db./g' \
  -e 's/from db import/from mypalclara.db import/g' \
  -e 's/^import db\b/import mypalclara.db/g' \
  {} +
```

IMPORTANT: Must also catch dynamic imports inside functions (e.g., `        from db.connection import SessionLocal`). The sed patterns above handle these because they match `from db.` at any indentation level.

**Step 2: Verify no stale references**

Run: `grep -rn "from db\.\|from db import\|^import db\b" --include="*.py" mypalclara/ tests/ scripts/ | grep -v "mypalclara.db" | grep -v "from dbc\|from dbm"`
Expected: Zero matches (after filtering out false positives like `dbm`, `dbc`).

**Step 3: Commit**

```bash
git add -A
git commit -m "refactor: update db → mypalclara.db imports"
```

---

### Task 7: Update All Import Paths — adapters → mypalclara.adapters

**Scope:** 32 files.

**Step 1: Bulk replace**

```bash
find mypalclara/ tests/ scripts/ -name '*.py' -exec sed -i '' \
  -e 's/from adapters\./from mypalclara.adapters./g' \
  -e 's/from adapters import/from mypalclara.adapters import/g' \
  -e 's/^import adapters\b/import mypalclara.adapters/g' \
  {} +
```

**Step 2: Verify**

Run: `grep -rn "^from adapters\b\|^import adapters\b" --include="*.py" mypalclara/ tests/ scripts/ | grep -v "mypalclara.adapters"`
Expected: Zero matches.

**Step 3: Commit**

```bash
git add -A
git commit -m "refactor: update adapters → mypalclara.adapters imports"
```

---

### Task 8: Update All Import Paths — tools → mypalclara.tools

**Scope:** 11 files.

**Step 1: Bulk replace**

```bash
find mypalclara/ tests/ scripts/ -name '*.py' -exec sed -i '' \
  -e 's/from tools\./from mypalclara.tools./g' \
  -e 's/from tools import/from mypalclara.tools import/g' \
  -e 's/^import tools\b/import mypalclara.tools/g' \
  {} +
```

CAUTION: `from tools._base import` should become `from mypalclara.tools._base import`. The pattern handles this since it matches `from tools.`.

**Step 2: Verify**

Run: `grep -rn "^from tools\b\|^import tools\b" --include="*.py" mypalclara/ tests/ scripts/ | grep -v "mypalclara.tools" | grep -v "core_tools"`
Expected: Zero matches.

**Step 3: Commit**

```bash
git add -A
git commit -m "refactor: update tools → mypalclara.tools imports"
```

---

### Task 9: Update All Import Paths — Remaining Packages

**Scope:** sandbox (2 files), email_service (6 files), backup_service (4 files), proactive (1 file).

**Step 1: Sandbox**

```bash
find mypalclara/ tests/ scripts/ -name '*.py' -exec sed -i '' \
  -e 's/from sandbox\./from mypalclara.sandbox./g' \
  -e 's/from sandbox import/from mypalclara.sandbox import/g' \
  -e 's/^import sandbox\b/import mypalclara.sandbox/g' \
  {} +
```

**Step 2: Email service**

```bash
find mypalclara/ tests/ scripts/ -name '*.py' -exec sed -i '' \
  -e 's/from email_service\./from mypalclara.services.email./g' \
  -e 's/from email_service import/from mypalclara.services.email import/g' \
  -e 's/^import email_service\b/import mypalclara.services.email/g' \
  {} +
```

**Step 3: Backup service**

```bash
find mypalclara/ tests/ scripts/ -name '*.py' -exec sed -i '' \
  -e 's/from backup_service\./from mypalclara.services.backup./g' \
  -e 's/from backup_service import/from mypalclara.services.backup import/g' \
  -e 's/^import backup_service\b/import mypalclara.services.backup/g' \
  {} +
```

**Step 4: Proactive**

```bash
find mypalclara/ tests/ scripts/ -name '*.py' -exec sed -i '' \
  -e 's/from proactive\./from mypalclara.services.proactive./g' \
  -e 's/from proactive import/from mypalclara.services.proactive import/g' \
  -e 's/^import proactive\b/import mypalclara.services.proactive/g' \
  {} +
```

**Step 5: Verify all remaining packages**

Run: `grep -rn "^from sandbox\b\|^from email_service\b\|^from backup_service\b\|^from proactive\b" --include="*.py" mypalclara/ tests/ scripts/ | grep -v mypalclara`
Expected: Zero matches.

**Step 6: Commit**

```bash
git add -A
git commit -m "refactor: update sandbox, email_service, backup_service, proactive imports"
```

---

### Task 10: Update pyproject.toml and Add Entry Points

**Files:**
- Modify: `pyproject.toml`
- Create: `mypalclara/__main__.py`

**Step 1: Update pyproject.toml entry points**

Change lines 100-104 from:
```toml
clara-gateway = "gateway.__main__:main"
clara-discord = "adapters.discord.main:run"
clara-teams = "adapters.teams.main:run"
clara-cli = "adapters.cli.main:run"
clara-web = "mypalclara.web.__main__:main"
```

To:
```toml
clara-gateway = "mypalclara.gateway.__main__:main"
clara-discord = "mypalclara.adapters.discord.main:run"
clara-teams = "mypalclara.adapters.teams.main:run"
clara-cli = "mypalclara.adapters.cli.main:run"
clara-web = "mypalclara.web.__main__:main"
```

**Step 2: Update ruff per-file-ignores paths**

Change:
```toml
"tools/*.py" = ["E501"]
"sandbox/*.py" = ["E501"]
```

To:
```toml
"mypalclara/tools/*.py" = ["E501"]
"mypalclara/sandbox/*.py" = ["E501"]
```

**Step 3: Create mypalclara/__main__.py**

```python
"""Default entry point — starts the gateway server."""
from mypalclara.gateway.__main__ import main

if __name__ == "__main__":
    main()
```

**Step 4: Commit**

```bash
git add pyproject.toml mypalclara/__main__.py
git commit -m "refactor: update pyproject.toml entry points and add default __main__"
```

---

### Task 11: Update Dockerfiles

**Files:**
- Modify: `Dockerfile.gateway`
- Modify: `Dockerfile.discord`
- Modify: `Dockerfile.web`
- Modify: `docker-compose.yml` (if backup_service path references exist)

**Step 1: Update all Dockerfiles**

All three Dockerfiles currently COPY individual directories:
```dockerfile
COPY clara_core/ ./clara_core/
COPY config/ ./config/
COPY db/ ./db/
COPY sandbox/ ./sandbox/
COPY storage/ ./storage/
COPY tools/ ./tools/
COPY mypalclara/ ./mypalclara/
COPY adapters/ ./adapters/
COPY email_service/ ./email_service/
COPY vendor/ ./vendor/
```

Replace with single COPY (everything is under mypalclara/ now):
```dockerfile
COPY mypalclara/ ./mypalclara/
```

Also update any CMD/ENTRYPOINT that references old module paths.

**Step 2: Update docker-compose.yml**

Check for any `backup_service/Dockerfile` or path references and update.

**Step 3: Commit**

```bash
git add Dockerfile.* docker-compose.yml
git commit -m "refactor: simplify Dockerfiles for new package structure"
```

---

### Task 12: Update Test Structure

**Files:**
- Modify: `tests/` — update imports in all test files
- Modify: `pyproject.toml` — testpaths stays `["tests"]`

**Step 1: Update test imports**

The sed commands in Tasks 4-9 should have already caught test files. Verify:

Run: `grep -rn "from clara_core\|from config\.\|from db\.\|from tools\.\|from adapters\.\|from sandbox\.\|from email_service\|from backup_service\|from proactive" --include="*.py" tests/ | grep -v mypalclara`
Expected: Zero stale imports.

**Step 2: Run the test suite**

Run: `poetry run pytest tests/ -x -q --timeout=30 2>&1 | tail -30`
Expected: Same pass/fail pattern as the baseline from Task 1. Any NEW failures indicate a missed import.

**Step 3: Fix any test failures**

If new failures appear, check the error message — it will be an `ImportError` or `ModuleNotFoundError` pointing to the exact stale import path. Fix individually.

**Step 4: Commit**

```bash
git add -A
git commit -m "fix: resolve any remaining test import issues"
```

---

### Task 13: Update Documentation

**Files:**
- Modify: `CLAUDE.md`
- Modify: `README.md`
- Modify: `wiki/*.md` (import paths, run commands)
- Modify: `docs/plans/2026-02-20-project-restructure-design.md` (mark as completed)

**Step 1: Update CLAUDE.md**

Update all references to old package paths:
- `clara_core/` → `mypalclara/core/`
- `adapters/` → `mypalclara/adapters/`
- `config/` → `mypalclara/config/`
- `db/` → `mypalclara/db/`
- `tools/` → `mypalclara/tools/`
- `sandbox/` → `mypalclara/sandbox/`
- `email_service/` → `mypalclara/services/email/`
- `backup_service/` → `mypalclara/services/backup/`
- `proactive/` → `mypalclara/services/proactive/`
- `vendor/` → (removed, delete references)
- `storage/` → (removed, delete references)
- Entry point commands updated

Also update the directory structure table and quick reference commands.

**Step 2: Update README.md**

Same path updates as CLAUDE.md.

**Step 3: Update wiki/**

Run: `grep -rn "clara_core\|adapters/\|config/\b\|^db/\|tools/\|sandbox/\|email_service\|backup_service\|proactive/" wiki/`

Update all references.

**Step 4: Commit**

```bash
git add CLAUDE.md README.md wiki/ docs/
git commit -m "docs: update all documentation for new package structure"
```

---

### Task 14: Final Verification and Cleanup

**Step 1: Run ruff**

```bash
poetry run ruff check mypalclara/ tests/ scripts/ && poetry run ruff format mypalclara/ tests/ scripts/
```

Expected: Clean (or only pre-existing warnings).

**Step 2: Run full test suite**

```bash
poetry run pytest tests/ -v --timeout=30 2>&1 | tail -40
```

Expected: Same results as baseline. No new failures.

**Step 3: Verify clean directory structure**

```bash
ls -1d */
```

Expected:
```
docs/
e2e/
hooks/
mypalclara/
personalities/
scripts/
teams_manifest/
tests/
web-ui/
wiki/
```

No more `clara_core/`, `adapters/`, `config/`, `db/`, `tools/`, `sandbox/`, `storage/`, `vendor/`, `clara_files/`, `gateway/`, `email_service/`, `backup_service/`, `proactive/`.

**Step 4: Verify entry point works**

```bash
poetry run python -c "import mypalclara; print('OK')"
poetry run python -c "from mypalclara.core import MemoryManager; print('OK')"
poetry run python -c "from mypalclara.config.logging import get_logger; print('OK')"
poetry run python -c "from mypalclara.db import SessionLocal; print('OK')"
```

Expected: All print "OK".

**Step 5: Final commit if any cleanup needed**

```bash
git add -A
git commit -m "chore: final cleanup after restructure"
```

---

## Import Change Summary

| Old path | New path | File count |
|----------|----------|------------|
| `clara_core.*` | `mypalclara.core.*` | ~96 |
| `config.*` | `mypalclara.config.*` | ~48 |
| `db.*` | `mypalclara.db.*` | ~42 |
| `adapters.*` | `mypalclara.adapters.*` | ~32 |
| `tools.*` | `mypalclara.tools.*` | ~11 |
| `sandbox.*` | `mypalclara.sandbox.*` | ~2 |
| `email_service.*` | `mypalclara.services.email.*` | ~6 |
| `backup_service.*` | `mypalclara.services.backup.*` | ~4 |
| `proactive.*` | `mypalclara.services.proactive.*` | ~1 |
| **Total** | | **~242 files** |

## Deleted Directories

| Directory | Reason |
|-----------|--------|
| `clara_files/` | Empty, unused |
| `gateway/` (root) | Empty legacy dir |
| `storage/` | Zero imports, re-export shim |
| `vendor/` | Empty directory tree, zero tracked files |

## Risk Notes

- **Biggest risk:** sed patterns matching too broadly (e.g., `from config` matching `from configparser`). Mitigated by using `from config\.` and `from config import` patterns.
- **Dynamic imports:** ~18 `db` imports are inside functions. The sed patterns match at any indentation, so these should be caught.
- **String references:** Dockerfiles, docker-compose, pyproject.toml all need manual updates (Task 10, 11).
- **Alembic migrations:** `db/migrations/env.py` → `mypalclara/db/migrations/env.py`. The alembic config in this file may reference `db.models` — needs updating.
