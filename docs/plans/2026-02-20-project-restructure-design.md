# Project Restructure Design

**Date:** 2026-02-20
**Goal:** Consolidate all Python packages under a single `mypalclara` top-level package.

## Problem

23 top-level directories, 11 of which are independent Python packages at root level (`clara_core`, `adapters`, `config`, `db`, `tools`, `sandbox`, `storage`, `proactive`, `email_service`, `backup_service`, `gateway`). Inconsistent naming, broken entry points, empty legacy directories.

## Target Layout

```
repo root/
├── mypalclara/                     ← SINGLE top-level Python package
│   ├── __init__.py                 ← public API re-exports
│   ├── __main__.py                 ← delegates to gateway (default entry point)
│   ├── core/                       ← was clara_core/
│   │   ├── __init__.py             ← same re-exports as old clara_core/__init__.py
│   │   ├── llm/
│   │   ├── memory/
│   │   ├── mcp/
│   │   ├── core_tools/
│   │   ├── discord/
│   │   ├── email/
│   │   ├── plugins/
│   │   ├── security/
│   │   ├── services/
│   │   ├── memory_manager.py
│   │   ├── prompt_builder.py
│   │   └── ... (all existing clara_core files)
│   ├── db/                         ← was db/
│   ├── config/                     ← was config/
│   ├── gateway/                    ← already here (no move needed)
│   ├── web/                        ← already here (no move needed)
│   ├── adapters/                   ← was adapters/
│   ├── tools/                      ← was tools/ (framework/loader)
│   ├── sandbox/                    ← was sandbox/
│   ├── services/                   ← NEW consolidation
│   │   ├── email/                  ← was email_service/
│   │   ├── backup/                 ← was backup_service/
│   │   └── proactive/              ← was proactive/
│   └── vendor/                     ← was vendor/
│
├── personalities/                  ← stays (data files)
├── scripts/                        ← stays (admin utilities)
├── tests/                          ← stays (mirrors mypalclara/ structure)
├── docs/                           ← stays
├── wiki/                           ← stays
├── web-ui/                         ← stays (React frontend)
├── hooks/                          ← stays (config examples)
├── e2e/                            ← stays (e2e tests)
└── teams_manifest/                 ← stays (Teams manifest)
```

## Deleted

| Directory | Reason |
|-----------|--------|
| `clara_files/` | Empty, unused |
| `gateway/` (root) | Empty legacy dir (just empty `providers/` subdir). Entry point was broken. |
| `storage/` | 2-file re-export shim. Callers updated to import from new location. |

## Entry Points

All consistent under `mypalclara.*`:

| Command | Module |
|---------|--------|
| `python -m mypalclara` | Delegates to `mypalclara.gateway` |
| `python -m mypalclara.gateway` | Gateway server (unchanged) |
| `python -m mypalclara.web` | Web UI (unchanged) |
| `python -m mypalclara.adapters.discord` | Discord adapter |
| `python -m mypalclara.adapters.cli` | CLI adapter |
| `python -m mypalclara.adapters.teams` | Teams adapter |
| `python -m mypalclara.services.backup` | Backup service |

pyproject.toml scripts:
```toml
[tool.poetry.scripts]
clara-gateway = "mypalclara.gateway.__main__:main"
clara-discord = "mypalclara.adapters.discord.main:run"
clara-teams   = "mypalclara.adapters.teams.main:run"
clara-cli     = "mypalclara.adapters.cli.main:run"
clara-web     = "mypalclara.web.__main__:main"
```

## Import Path Changes

Every import across the codebase changes. The mapping:

| Old import | New import |
|-----------|------------|
| `clara_core.*` | `mypalclara.core.*` |
| `config.*` | `mypalclara.config.*` |
| `db.*` | `mypalclara.db.*` |
| `tools.*` | `mypalclara.tools.*` |
| `adapters.*` | `mypalclara.adapters.*` |
| `sandbox.*` | `mypalclara.sandbox.*` |
| `storage.*` | `mypalclara.core.core_tools.files_tool` (direct) |
| `proactive.*` | `mypalclara.services.proactive.*` |
| `email_service.*` | `mypalclara.services.email.*` |
| `backup_service.*` | `mypalclara.services.backup.*` |
| `vendor.*` | `mypalclara.vendor.*` |
| `gateway.*` (broken) | `mypalclara.gateway.*` |

## mypalclara/__main__.py

```python
"""Default entry point — starts the gateway server."""
from mypalclara.gateway.__main__ import main

if __name__ == "__main__":
    main()
```

## Risk Mitigation

- Work on a feature branch (`restructure/consolidate-packages`)
- Automated find-and-replace for import paths, verified with grep
- Run full test suite after each major move
- Keep `config/rook.py` re-export if anything outside the package references it
- Update CLAUDE.md, README, all docs after moves

## What Does NOT Change

- All business logic, algorithms, and behavior
- Database schemas and migrations
- API contracts (WebSocket protocol, REST endpoints)
- Configuration (env vars, personality files)
- `web-ui/` frontend code
- `vendor/mem0/` internals (only import path prefix changes)
