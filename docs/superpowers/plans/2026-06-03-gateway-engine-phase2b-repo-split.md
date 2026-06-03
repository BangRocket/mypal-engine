# Gateway → mypal-engine Phase 2b: Repo Split (runbook)

> Irreversible / outward-facing. Operates on a **fresh single-branch clone** so the current repo is never touched. Produces a new public GitHub repo `BangRocket/mypal-engine`.

**Goal:** Carve the engine subgraph (history-preserved) out of `mypalclara` into a standalone `mypal-engine` repo and push it to a new **public** GitHub repo, without modifying the current working repo.

**Source branch:** `visual-clara-palace-integration` (carries all Phase 1 + 2a work; `main` does NOT). Filter single-branch, then rename to `main`.

## Procedure

1. Fresh single-branch clone:
   `git clone --single-branch --branch visual-clara-palace-integration <repo> ../mypal-engine`
2. In the clone, `git filter-repo --paths-from-file <keep-list>` (keep-list below).
3. Rename branch to `main`; verify structure, size, engine imports, boundary test.
4. Create public repo `BangRocket/mypal-engine`, set origin, push `main`.
5. Report. (Trimming the OLD repo to client-only is a SEPARATE, later, confirmed step — it breaks local dev until rewired.)

## Engine keep-list (positive)

```
mypal_protocol/
mypalclara/__init__.py
mypalclara/__main__.py
mypalclara/config/
mypalclara/core/
mypalclara/db/
mypalclara/gateway/
mypalclara/sandbox/
mypalclara/tools/
mypalclara/workspace/
mypalclara/services/__init__.py
mypalclara/services/proactive/
mypalclara/services/blog/
mypalclara/services/email/
mypalclara/services/backup/
services/gateway/
services/base/
services/backup/
services/falkordb/
services/qdrant/
scripts/
docs/
personalities/
hooks/
clara_core/
mcp_servers/
tools/
tests/__init__.py
tests/architecture/
tests/gateway/
tests/core/
tests/db/
tests/protocol/
tests/services/
tests/integration/
tests/scripts/
tests/tools/
pyproject.toml
poetry.lock
README.md
LICENSE
VERSION
alembic.ini
CLAUDE.md
.env.docker.example
.gitignore
```

## Excluded (client / data / legacy)

`mypalclara/adapters/`, `mypalclara/web/`, `mypalclara/services/voice/`, `services/discord/`,
`services/web-ui/`, `services/identity/`, `teams_manifest/`, root `web-ui/`, root `identity/`,
`node_modules/`, `qdrant_data/`, `mypalclara/qdrant_data/`, `clara.db`, `e2e/`, `chats/`, `wiki/`,
`tests/adapters/`, `tests/web/`, `package.json`, `pnpm-lock.yaml`, `playwright.config.ts`.

## Verification before push

- `git -C ../mypal-engine ls-files | wc -l` and `du -sh ../mypal-engine/.git` (expect much smaller than 1.5G).
- No `mypalclara/adapters/` present; `mypal_protocol/` present.
- Engine import smoke: `PYTHONPATH=../mypal-engine python -c "import mypal_protocol; import mypalclara.gateway.server"`.
- Boundary test runs green in the new repo.
