# MyPal Reorg Plan A: Ground Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the risk-free foundation of the system reorg: mypalace hygiene fixes, a thin workspace meta-repo at the root, CI for mypal-engine, and the Phase-C tracking issues.

**Architecture:** Four independent tasks across three locations (mypalace repo, workspace root, mypal-engine repo). No runtime code changes anywhere; everything is docs, glue files, CI config, and issue filing. This is Plan A of four; Plans B (engine rename), C (protocol single-sourcing), and D (dedupe + docs truth) follow after A lands.

**Tech Stack:** git, just, GitHub Actions, Poetry (engine), pytest, gh CLI.

**Spec:** `mypal-engine/docs/superpowers/specs/2026-07-02-mypal-system-reorg-design.md` (workstreams WS5, WS6a, WS7, WS8).

## Global Constraints

- All paths below are relative to `/Volumes/Storage/Code/mypalsystem` unless absolute.
- `mypalace/docker-compose.yml` has an uncommitted user modification. Never stage it: always `git add` explicit paths, never `git add -A` or `git add .`.
- Commit messages use conventional prefixes. In mypal-engine, `docs:`, `chore:`, `ci:` do NOT bump the CalVer version (the `.githooks` bump hook keys off prefixes); use those prefixes for everything in this plan.
- No pushes to GitHub except at the explicitly marked steps in Task 3 and Task 4, and confirm with the user immediately before each push.
- No runtime behavior changes: no env var renames, no code edits outside the files listed per task.
- Worktree isolation is deliberately skipped for this plan: every task is docs/config-only and lands on `main` of its repo. (Plan B, the package rename, WILL use a worktree.)

---

### Task 1: mypalace hygiene sweep (WS7)

**Files:**
- Delete (filesystem only, both untracked): `mypalace/palace/`, `mypalace/mypalace/cli/`
- Modify: `mypalace/README.md` (three stale-path fixes + one typo)
- Modify: `mypalace/docs/migrating-mypalclara.md` (staleness banner)
- Modify: `mypalace/docs/deployment.md` (version string)
- Create: `mypalace/CLAUDE.md`

**Interfaces:**
- Consumes: nothing.
- Produces: nothing later tasks depend on; standalone cleanup.

- [ ] **Step 1: Remove the two empty leftover directories**

```bash
cd /Volumes/Storage/Code/mypalsystem/mypalace
rm -rf mypalace/cli/__pycache__ && rmdir mypalace/cli
rmdir palace
git status --porcelain
```

Expected: both `rmdir` calls succeed (they are empty apart from that pycache); `git status --porcelain` shows only ` M docker-compose.yml` (the user's pre-existing local change; leave it).

- [ ] **Step 2: Fix the README architecture tree (palace/ → mypalace/)**

In `mypalace/README.md`, replace the Architecture code block that begins with `palace/` (starts near line 550) with:

```
mypalace/
├── main.py              FastAPI app + lifespan (tables, Qdrant collection, gRPC)
├── config.py            Pydantic Settings (.env aware)
├── models.py            SQLModel tables
├── database.py          Async SQLAlchemy engine + session factory
├── embeddings.py        EmbeddingProvider protocol + HF and OpenAI impls
├── vector.py            Async Qdrant wrapper (ensure/upsert/query/delete)
├── llm.py               Async chat-completion client (OpenAI-compatible + Anthropic)
├── memory_service.py    CRUD + semantic search; lazy embedder
├── session_service.py   Session + message lifecycle
├── context_service.py   Memory search + recent messages → prompt context
├── api/                 REST routes (memories, sessions, context, admin, ...)
├── grpc/                optional gRPC transport (mirrors the REST surface)
├── auth/  workers/  retrieval/  dynamics/  intentions/  graph/  cache/  events/
│                        subsystem packages added across phases 2-14
└── observability/       Prometheus metrics, OTel traces, structlog
```

Before committing, verify each listed entry exists: `ls mypalace/main.py mypalace/config.py mypalace/models.py mypalace/database.py mypalace/embeddings.py mypalace/vector.py mypalace/llm.py mypalace/memory_service.py mypalace/session_service.py mypalace/context_service.py` and `ls -d mypalace/api mypalace/grpc mypalace/auth mypalace/workers mypalace/observability`. Adjust the tree to reality if any line is wrong; the tree must not name a file that does not exist.

- [ ] **Step 3: Fix the two legacy-notes paths and the uvicorn typo**

Three surgical edits in `mypalace/README.md`:
1. `palace/llm.py` → `mypalace/llm.py` (in "Legacy slice notes")
2. `palace/dynamics/fsrs.py` → `mypalace/dynamics/fsrs.py` (same section)
3. `uvicorn mymypalace.main:app` → `uvicorn mypalace.main:app` (typo in the gRPC section)

- [ ] **Step 4: Banner the version-stale docs**

At the very top of `mypalace/docs/migrating-mypalclara.md` (immediately after the H1 title line), insert:

```markdown
> **Version note (2026-07-02):** this guide was written and tested against mypalace **0.6.0**. The current release is **0.12.0**; the API surface is a strict superset, so the steps still apply, but check `CHANGELOG.md` for auth, tenancy, and schema-mode changes introduced since (notably per-tenant Postgres schemas in 0.12.0).
```

In `mypalace/docs/deployment.md`, replace the version reference `0.11.1` with `0.12.0` (single occurrence in the intro line; verify with `grep -n "0.11.1" docs/deployment.md` that no other occurrence needs judgment).

- [ ] **Step 5: Create mypalace/CLAUDE.md**

```markdown
# CLAUDE.md

Guidance for Claude Code working with this repository.

## Project Overview

MyPalace is a standalone memory service for AI assistants (FastAPI + optional gRPC). Two packages ship from this one repo:

- `mypalace` (root `pyproject.toml`): the server. REST `/v1/*`, optional gRPC on `PALACE_GRPC_PORT`, Postgres-backed workers, admin web UI at `/admin/*`.
- `mypalace-client` (`mypalace_client/pyproject.toml`): async HTTP/gRPC client + the `mypalace-admin` operator CLI. Versioned in lockstep with the server; bump both pyprojects together.

Fully standalone: imports nothing from mypal-engine or mypalclara. mypal-engine consumes this service through `mypalace-client` when `USE_PALACE_SERVICE=true`.

## Quick Reference

```bash
python3.12 -m venv .venv && .venv/bin/pip install -e ".[dev]"   # install (Python 3.12+)
.venv/bin/python -m pytest                    # fast mock suite, no services needed
.venv/bin/python -m pytest -m integration     # live suite (testcontainers: postgres+qdrant)
.venv/bin/uvicorn mypalace.main:app --reload --port 8000
.venv/bin/alembic upgrade head                # DB URL from PALACE_DATABASE_URL
docker compose up -d postgres qdrant          # dev backends
```

## Architecture

- `mypalace/`: server source (~15k LOC). Top-level service modules (`memory_service.py`, `tenancy.py`, ...) plus subpackages `api/`, `grpc/`, `auth/`, `workers/`, `retrieval/`, `dynamics/` (FSRS), `intentions/`, `graph/`, `cache/`, `observability/`.
- `mypalace_client/`: self-contained client package (own pyproject, lock, tests, README).
- `alembic/`: migrations; fresh installs auto-stamp on first boot.
- `apps/admin-ui/`: Vite + React admin console, built into the server image, served at `/admin/*`.
- `proto/mypalace.proto`: gRPC schema; stub-regen instructions live in README.

## Conventions

- Multi-tenancy: every user-data row carries `tenant_id`; per-tenant Postgres schemas are mandatory as of v0.12.0.
- Auth: `X-Palace-Key` header; scopes `read`/`write`/`admin` are explicit and non-inheriting.
- Releases: tag `vX.Y.Z` → `.github/workflows/release.yml` tests, builds both packages, publishes to PyPI (TestPyPI for `-rc`/`-beta` tags), pushes the Docker image, cuts a GitHub release from `CHANGELOG.md`. Update `CHANGELOG.md` and both version strings before tagging.
```

- [ ] **Step 6: Verify no stale palace/ layout references remain**

```bash
cd /Volumes/Storage/Code/mypalsystem/mypalace
grep -n "palace/main.py\|palace/llm.py\|palace/dynamics\|mymypalace" README.md docs/*.md | grep -v "mypalace/"
```

Expected: no output.

- [ ] **Step 7: Commit (explicit paths only)**

```bash
cd /Volumes/Storage/Code/mypalsystem/mypalace
git add README.md CLAUDE.md docs/migrating-mypalclara.md docs/deployment.md
git commit -m "docs: fix stale palace/ paths, banner version-pinned guides, add CLAUDE.md"
git status --porcelain
```

Expected: commit succeeds; status shows only ` M docker-compose.yml` remaining.

---

### Task 2: Workspace meta-repo at the root (WS5)

**Files:**
- Create: `/Volumes/Storage/Code/mypalsystem/.gitignore`
- Create: `/Volumes/Storage/Code/mypalsystem/README.md`
- Create: `/Volumes/Storage/Code/mypalsystem/CLAUDE.md`
- Create: `/Volumes/Storage/Code/mypalsystem/justfile`
- Create: `/Volumes/Storage/Code/mypalsystem/mypal.code-workspace`

**Interfaces:**
- Consumes: nothing.
- Produces: `just status`, `just pull`, `just test`, `just dev` recipes that later plans use for verification; the root CLAUDE.md that agent sessions rely on. The justfile contains one engine command string (`-m mypalclara.gateway`) that Plan B updates during the rename.

- [ ] **Step 1: Ensure `just` is installed**

```bash
command -v just || brew install just
just --version
```

Expected: a version string (any version).

- [ ] **Step 2: Initialize the meta-repo and .gitignore**

```bash
cd /Volumes/Storage/Code/mypalsystem
git init -b main
```

Create `.gitignore`:

```gitignore
# The four child repos are independent; this meta-repo tracks glue only.
mypal-engine/
mypalclara/
mypalclara.wiki/
mypalace/
.DS_Store
```

- [ ] **Step 3: Create README.md (the system map)**

```markdown
# The MyPal System

A personal AI assistant ("Clara") built as three cooperating projects plus a wiki. This directory is a thin workspace: each subdirectory is its own independent git repo; this meta-repo tracks only the glue files.

| Repo | What it is | Runs as |
|---|---|---|
| `mypal-engine/` | The intelligence: gateway, LLM orchestration, memory manager, tools, MCP, sandbox, DB | WebSocket `:18789` + HTTP API `:18790` |
| `mypalclara/` | The experience layer: Discord / Teams / CLI adapters, voice server, future unified app | client processes connecting to the engine |
| `mypalace/` | Standalone memory service (PyPI: `mypalace`, `mypalace-client`) | HTTP `:8000`, optional gRPC `:50051` |
| `mypalclara.wiki/` | GitHub wiki: the system-facing docs map | docs only |

## How the pieces talk

```
Discord / Teams / CLI / Voice   (mypalclara adapters)
        │   WebSocket :18789 + HTTP :18790   (shared CLARA_GATEWAY_SECRET)
        ▼
   mypal-engine ────────────────► mypalace :8000
        │      X-Palace-Key, USE_PALACE_SERVICE=true
        └──► Postgres / Qdrant / FalkorDB / Redis, LLM providers, MCP servers
```

Wire contract: `mypal_protocol` (currently vendored in both engine and client; single-sourcing is reorg workstream WS2).

## Day-one setup order

1. **mypalace**: bring up backends and the API (see its README Quick start).
2. **mypal-engine**: configure `.env`, then `poetry run python -m mypalclara.gateway start`.
3. **mypalclara**: run an adapter, e.g. `CLARA_GATEWAY_SECRET=... poetry run python -m mypalclara.adapters.discord`.

## Workspace commands

Requires [`just`](https://github.com/casey/just): `just status`, `just pull`, `just test`, `just dev`.

## Design docs

- System reorg: `mypal-engine/docs/superpowers/specs/2026-07-02-mypal-system-reorg-design.md`
- Experience charter: `mypalclara/docs/superpowers/specs/2026-06-05-mypalclara-experience-charter-design.md`
```

- [ ] **Step 4: Create CLAUDE.md (cross-repo agent guidance)**

```markdown
# CLAUDE.md: MyPal workspace root

This directory is a thin meta-repo. The four subdirectories are independent git repos: branch and commit inside them, never here (this repo tracks only the glue files).

## Naming map (read this first)

| Repo | Python package | Published name | Purpose |
|---|---|---|---|
| mypal-engine | `mypalclara` (rename to `mypal_engine` is reorg WS1) | not published | headless engine |
| mypalclara | `mypalclara` | not published | adapters + voice (experience layer) |
| mypalace | `mypalace`, `mypalace_client` | PyPI `mypalace`, `mypalace-client` | memory service |
| engine AND client | `mypal_protocol` | vendored copies, unpublished (reorg WS2) | wire contract |

Yes, two different packages are currently both named `mypalclara`: the engine's (67k LOC of engine code) and the client's (adapters). Check which repo you are in before editing anything.

## Which repo do I change?

| Change | Repo |
|---|---|
| Prompting, memory behavior, LLM providers, tools, MCP, sandbox, gateway/API | mypal-engine |
| Discord / Teams / CLI behavior, voice, web UI | mypalclara |
| Memory service endpoints, tenancy, FSRS, workers, admin UI | mypalace |
| Wire messages between adapters and engine | `mypal_protocol`: edit the ENGINE copy and mirror the exact change to the client copy in the same sitting (never edit only one). WS2 removes this footgun. |
| Personas (`clara.md`, `clarissa.md`, `flo.md`) | duplicated in engine + client today; edit BOTH copies identically until WS3 lands |
| User-facing system docs | mypalclara.wiki |

## Boundary rules

- Adapters never import engine internals; the client reaches the engine only via `mypal_protocol` (WebSocket) and `EngineApiClient` (HTTP). Architecture tests guard this in both repos.
- The engine never imports platform SDKs (discord, botbuilder, ...). Guarded by `mypal-engine/tests/architecture/test_engine_boundary.py`.
- mypalace imports nothing from the other repos.

## Reorg state

Active design: `mypal-engine/docs/superpowers/specs/2026-07-02-mypal-system-reorg-design.md`. Plans land in order A (ground layer) → B (engine rename) → C (protocol) → D (dedupe + docs). Until C and D land, the vendored-copy warnings above apply.
```

- [ ] **Step 5: Create the justfile**

```just
# MyPal workspace commands. Requires `just` (brew install just).

repos := "mypal-engine mypalace mypalclara mypalclara.wiki"

# git status across all repos
status:
    #!/usr/bin/env bash
    for r in {{repos}}; do echo "=== $r ==="; git -C "$r" status -sb; echo; done

# fast-forward pull across all repos
pull:
    #!/usr/bin/env bash
    for r in {{repos}}; do echo "=== $r ==="; git -C "$r" pull --ff-only; echo; done

# run every repo's test suite
test: test-palace test-engine test-clara

test-engine:
    cd mypal-engine && poetry run pytest -q

test-clara:
    cd mypalclara && poetry run pytest -q tests/

test-palace:
    #!/usr/bin/env bash
    cd mypalace
    if [ -x .venv/bin/python ]; then .venv/bin/python -m pytest -q; else python3 -m pytest -q; fi

# start palace dev backends; prints the run commands for the services
dev:
    cd mypalace && docker compose up -d postgres qdrant
    @echo "palace API : cd mypalace && .venv/bin/uvicorn mypalace.main:app --port 8000"
    @echo "engine     : cd mypal-engine && poetry run python -m mypalclara.gateway start"
    # NOTE: the engine module path becomes mypal_engine.gateway after reorg WS1 (Plan B updates this line)
```

- [ ] **Step 6: Create mypal.code-workspace**

```json
{
  "folders": [
    { "path": "mypal-engine" },
    { "path": "mypalclara" },
    { "path": "mypalace" },
    { "path": "mypalclara.wiki" },
    { "path": ".", "name": "workspace-root" }
  ],
  "settings": {}
}
```

- [ ] **Step 7: Verify the recipes work**

```bash
cd /Volumes/Storage/Code/mypalsystem
just status
```

Expected: four `=== repo ===` blocks each showing a branch line (`## main...` or `## master...`). Then:

```bash
just test-palace
```

Expected: the mypalace mock suite runs and passes (README documents it as ~2s, no services required). If no venv and no system pytest exists, record the failure output and continue; wiring a palace venv is not this plan's job, and `just test-palace` failing for a missing interpreter is acceptable to note in the commit message.

Do NOT run `just test-engine` / `just test-clara` here; Task 3 Step 1 establishes the engine baseline deliberately, and the client suite is exercised in Plan C.

- [ ] **Step 8: Commit the meta-repo**

```bash
cd /Volumes/Storage/Code/mypalsystem
git add .gitignore README.md CLAUDE.md justfile mypal.code-workspace
git commit -m "chore: workspace meta-repo (system map, cross-repo CLAUDE.md, justfile, VS Code workspace)"
git status --porcelain
```

Expected: clean status (child repos ignored).

---

### Task 3: Engine CI (WS6a)

**Files:**
- Create: `mypal-engine/.github/workflows/ci.yml`

**Interfaces:**
- Consumes: nothing.
- Produces: a required-green pipeline that Plan B (the rename) merges against. Later plans assume `poetry run pytest` and `poetry run ruff check .` are the canonical local equivalents.

- [ ] **Step 1: Establish the local baseline (this decides the workflow content)**

```bash
cd /Volumes/Storage/Code/mypalsystem/mypal-engine
poetry install --no-interaction --sync 2>&1 | tail -3
poetry run ruff check . 2>&1 | tail -5
poetry run ruff format --check . 2>&1 | tail -3
poetry run pytest -q 2>&1 | tail -15
```

Record all four outcomes. Decision table:
- ruff check green + pytest green: use the full workflow below as written.
- `ruff format --check` red: drop that single line from the workflow (do not reformat the repo in this task; note it as a Plan D cleanup).
- pytest red: STOP. Report the failures to the user before proceeding; do not merge a red CI and do not silence tests to force green. (Likely cause to check first: tests reading `.env`; retry as `env -u DISCORD_BOT_TOKEN poetry run pytest -q` only if the failure output implicates real credentials, and if that is the cause, the fix belongs in the workflow `env:` block, not in the tests.)

- [ ] **Step 2: Write .github/workflows/ci.yml**

```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:

jobs:
  test:
    runs-on: ubuntu-latest
    timeout-minutes: 25
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - name: Install poetry
        run: pipx install poetry
      - name: Cache poetry
        uses: actions/cache@v4
        with:
          path: ~/.cache/pypoetry
          key: poetry-${{ runner.os }}-${{ hashFiles('poetry.lock') }}
      - name: Install dependencies
        run: poetry install --no-interaction
      - name: Lint
        run: poetry run ruff check .
      - name: Format check
        run: poetry run ruff format --check .
      - name: Tests
        run: poetry run pytest -q
```

(Adjusted per the Step 1 decision table.)

- [ ] **Step 3: Commit on a branch and push to exercise the workflow**

```bash
cd /Volumes/Storage/Code/mypalsystem/mypal-engine
git checkout -b ci-bootstrap
git add .github/workflows/ci.yml
git commit -m "ci: add lint + test workflow (first CI for this repo)"
```

**CONFIRM WITH THE USER BEFORE PUSHING**, then:

```bash
git push -u origin ci-bootstrap
gh pr create -R BangRocket/mypal-engine --title "ci: add lint + test workflow" --body "First CI for the engine repo. Ground-layer task from the 2026-07-02 system reorg design (WS6a). Runs ruff + pytest on push/PR.

🤖 Generated with [Claude Code](https://claude.com/claude-code)"
gh pr checks --repo BangRocket/mypal-engine --watch --fail-fast
```

Expected: the workflow run completes green. If it fails on something CI-only (missing system dep, env var), fix forward on the branch: each fix is a new commit, re-watch. Env-var-shaped failures get an `env:` block entry on the Tests step (e.g. `SKIP_PROFILE_LOAD: "true"`) with values from CLAUDE.md defaults, never real secrets.

- [ ] **Step 4: Merge once green**

```bash
gh pr merge --repo BangRocket/mypal-engine --merge --delete-branch
git checkout main && git pull --ff-only
```

Expected: `main` now contains the workflow; the repo shows a green check on GitHub. Note: local `main` was 1 commit behind `origin/main` when this plan was written; the `--ff-only` pull is expected to fast-forward over that as well. If it refuses because local main has unpushed commits (the reorg spec commit from earlier today), use `git pull --rebase` instead and verify `git log --oneline -3` shows both the spec commit and the merged CI commit.

---

### Task 4: File the Phase-C tracking issues (WS8)

**Files:** none (GitHub issues only).

**Interfaces:**
- Consumes: nothing.
- Produces: two issue URLs recorded back into the design doc by Plan D's docs pass.

- [ ] **Step 1: File the engine issue**

**CONFIRM WITH THE USER BEFORE CREATING** (outward-facing), then:

```bash
gh issue create -R BangRocket/mypal-engine \
  --title "Phase C: collapse core/memory to a thin Palace client" \
  --body "$(cat <<'EOF'
## Context

The engine runs Palace in service mode (`USE_PALACE_SERVICE=true`, `mypalace-client ^0.12.0` via `core/memory/routed.py`), but the embedded memory system (`core/memory/`, ~49 files / 11.8k LOC) remains fully present as the fallback path.

## Blockers (documented in routed.py)

- Remote `history()` returns `[]` (no parity endpoint used yet)
- Remote store paths raise instead of delegating

Tracked service-side in the paired MyPalace issue ("Remote-parity gaps blocking Phase C").

## Exit criteria

1. Both gaps closed in mypalace + mypalace-client, pinned versions bumped here
2. Two weeks of service-mode soak with no fallback to the embedded path
3. Then: delete the embedded `core/memory/` implementation, collapse `MemoryManager` to the thin client wrapper (the "Phase C" already described in routed.py), and sweep `scripts/` for embedded-only migration tooling

From the 2026-07-02 system reorg design (WS8).
EOF
)"
```

Expected: prints the new issue URL. Record it.

- [ ] **Step 2: File the mypalace issue**

```bash
gh issue create -R BangRocket/MyPalace \
  --title "Remote-parity gaps blocking mypal-engine Phase C" \
  --body "$(cat <<'EOF'
mypal-engine runs against this service with `USE_PALACE_SERVICE=true`, but two client-visible gaps keep its embedded fallback alive (see the paired mypal-engine issue "Phase C: collapse core/memory to a thin Palace client"):

1. **History parity:** the engine-side router's `history()` returns `[]` in remote mode; expose/confirm the endpoint + client method it should call.
2. **Store-path operations:** router store paths raise in remote mode; enumerate which store operations lack a remote equivalent and add them (or document the intended remote pattern).

Definition of done: mypalace-client offers both surfaces, the engine router routes them, and the engine issue's soak clock can start.
EOF
)"
```

Expected: prints the new issue URL. Record both URLs in the task notes for Plan D.

---

## Verification (whole plan)

1. `cd /Volumes/Storage/Code/mypalsystem && just status`: four repos listed; mypalace shows only its pre-existing `docker-compose.yml` modification; engine and root are clean.
2. `grep -rn "mymypalace\|palace/main.py" mypalace/README.md`: no output.
3. GitHub: mypal-engine shows a green Actions run on main; two issues exist (one per repo).
4. `test -f mypalace/CLAUDE.md && test -f README.md && test -f justfile`: all present.

## Explicitly out of scope for Plan A

- Any edit inside `mypal-engine/mypalclara/` or `mypalclara/mypalclara/` (Plans B-D)
- The wiki (Plan D)
- Protocol packaging (Plan C)
- Deleting anything tracked by git other than nothing: Plan A deletes zero tracked files
