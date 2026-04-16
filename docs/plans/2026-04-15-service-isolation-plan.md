# Service Isolation Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Move all Railway-deployable services into `services/<name>/` directories with independent `railway.toml` files, using a shared base Docker image for Python services.

**Architecture:** Shared base image (`services/base/Dockerfile`) contains Python 3.12, poetry deps, and the full `mypalclara/` package. Discord, Gateway, and Backup extend it with minimal Dockerfiles. Web-UI and Identity are standalone. Qdrant and FalkorDB use official images with config-only `railway.toml` files.

**Tech Stack:** Docker, Railway, Poetry, Python 3.12, Ruby/Rails, React/Vite

**Design doc:** `docs/plans/2026-04-15-service-isolation-design.md`

---

### Task 1: Create services/base/ with shared Dockerfile

**Files:**
- Create: `services/base/Dockerfile`
- Create: `services/base/railway.toml`

**Step 1: Create the base Dockerfile**

The base image consolidates the shared build steps from `Dockerfile.discord` and `Dockerfile.gateway`. Note: `clara-mcp-server/` was removed from the repo (commit `0003cdb4`), so the Rust build stage is dropped.

```dockerfile
# services/base/Dockerfile
# Shared base image for MyPalClara Python services
# Build context: repo root (not services/base/)

FROM python:3.12-slim

WORKDIR /app

# System dependencies (Node.js for MCP servers)
RUN apt-get update && apt-get install -y \
    build-essential \
    curl \
    git \
    postgresql-client \
    gnupg \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# Poetry
RUN pip install poetry

# Dependencies
COPY pyproject.toml poetry.lock* ./
RUN poetry config virtualenvs.create false \
    && poetry install --only main --no-interaction --no-ansi

# Playwright browsers
RUN playwright install chromium --with-deps

# Application code
COPY mypalclara/ ./mypalclara/
COPY personalities/ ./personalities/
COPY scripts/ ./scripts/
COPY VERSION ./

# Persistent data directories
RUN mkdir -p /data /app/clara_files /app/mcp_servers
ENV DATA_DIR=/data
ENV CLARA_FILES_DIR=/app/clara_files
ENV MCP_SERVERS_DIR=/app/mcp_servers
```

```toml
# services/base/railway.toml
# Base image — not deployed directly, built as dependency
# Build from repo root: docker build -f services/base/Dockerfile -t clara-base .

[build]
dockerfilePath = "services/base/Dockerfile"
```

**Step 2: Verify it builds**

Run from repo root:
```bash
docker build -f services/base/Dockerfile -t clara-base:latest .
```
Expected: successful build, image tagged `clara-base:latest`

**Step 3: Commit**

```bash
git add services/base/Dockerfile services/base/railway.toml
git commit -m "feat: add shared base Docker image for Python services"
```

---

### Task 2: Create services/discord/

**Files:**
- Create: `services/discord/Dockerfile`
- Create: `services/discord/railway.toml`

**Step 1: Create the Discord Dockerfile**

```dockerfile
# services/discord/Dockerfile
FROM clara-base:latest

EXPOSE 8001

CMD ["python", "-m", "mypalclara.adapters.discord"]
```

**Step 2: Create the Railway config**

```toml
# services/discord/railway.toml

[build]
dockerfilePath = "services/discord/Dockerfile"

[deploy]
healthcheckPath = "/health"
healthcheckTimeout = 300
restartPolicyType = "ON_FAILURE"
restartPolicyMaxRetries = 3
```

**Step 3: Verify it builds**

```bash
docker build -f services/discord/Dockerfile -t clara-discord:latest .
```
Expected: builds successfully from `clara-base:latest`

**Step 4: Commit**

```bash
git add services/discord/Dockerfile services/discord/railway.toml
git commit -m "feat: add services/discord with base image Dockerfile"
```

---

### Task 3: Create services/gateway/

**Files:**
- Create: `services/gateway/Dockerfile`
- Create: `services/gateway/railway.toml`

**Step 1: Create the Gateway Dockerfile**

```dockerfile
# services/gateway/Dockerfile
FROM clara-base:latest

EXPOSE 18789 18790

CMD ["python", "-m", "mypalclara.gateway"]
```

**Step 2: Create the Railway config**

```toml
# services/gateway/railway.toml

[build]
dockerfilePath = "services/gateway/Dockerfile"

[deploy]
healthcheckPath = "/health"
healthcheckTimeout = 300
restartPolicyType = "ON_FAILURE"
restartPolicyMaxRetries = 5
```

**Step 3: Verify it builds**

```bash
docker build -f services/gateway/Dockerfile -t clara-gateway:latest .
```
Expected: builds successfully

**Step 4: Commit**

```bash
git add services/gateway/Dockerfile services/gateway/railway.toml
git commit -m "feat: add services/gateway with base image Dockerfile"
```

---

### Task 4: Create services/backup/

**Files:**
- Create: `services/backup/Dockerfile`
- Create: `services/backup/railway.toml`

The backup service currently uses a standalone Dockerfile with `pip install` (not poetry). Since it now gets the full `mypalclara` package from the base image, it only needs the PostgreSQL 18 client added.

**Step 1: Create the Backup Dockerfile**

```dockerfile
# services/backup/Dockerfile
FROM clara-base:latest

# Backup needs PostgreSQL 18 client (base has default version)
# Upgrade to pg18 for latest pg_dump features
RUN apt-get update && apt-get install -y \
    gnupg \
    lsb-release \
    && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc | gpg --dearmor -o /usr/share/keyrings/postgresql-keyring.gpg \
    && echo "deb [signed-by=/usr/share/keyrings/postgresql-keyring.gpg] http://apt.postgresql.org/pub/repos/apt $(lsb_release -cs)-pgdg main" > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update \
    && apt-get install -y postgresql-client-18 \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /backups

EXPOSE 8080

CMD ["python", "-m", "mypalclara.services.backup", "serve"]
```

**Step 2: Create the Railway config**

```toml
# services/backup/railway.toml

[build]
dockerfilePath = "services/backup/Dockerfile"

[deploy]
healthcheckPath = "/health"
healthcheckTimeout = 30
restartPolicyType = "ON_FAILURE"
restartPolicyMaxRetries = 3
```

**Step 3: Verify it builds**

```bash
docker build -f services/backup/Dockerfile -t clara-backup:latest .
```
Expected: builds successfully

**Step 4: Commit**

```bash
git add services/backup/Dockerfile services/backup/railway.toml
git commit -m "feat: add services/backup with base image Dockerfile"
```

---

### Task 5: Move web-ui/ to services/web-ui/

**Files:**
- Move: `web-ui/` → `services/web-ui/`
- Update: `services/web-ui/railway.toml` (already exists inside web-ui/)

**Step 1: Move the directory**

```bash
git mv web-ui services/web-ui
```

**Step 2: Verify the Dockerfile and railway.toml are intact**

The `web-ui/Dockerfile` uses relative paths (`frontend/`, `backend/`) which remain correct since the entire directory moves together. No edits needed to:
- `services/web-ui/Dockerfile`
- `services/web-ui/railway.toml`

**Step 3: Verify it builds**

```bash
docker build -t clara-web-ui:latest services/web-ui/
```
Expected: builds successfully (self-contained, no repo-root deps)

**Step 4: Commit**

```bash
git add -A services/web-ui/
git commit -m "refactor: move web-ui/ to services/web-ui/"
```

---

### Task 6: Move identity/ to services/identity/

**Files:**
- Move: `identity/` → `services/identity/`

**Step 1: Move the directory**

```bash
git mv identity services/identity
```

**Step 2: Verify paths**

The identity Dockerfile uses `COPY . ./identity/` which copies the build context. Since we build from `services/identity/`, this still works. No edits needed.

**Step 3: Verify it builds**

```bash
docker build -t clara-identity:latest services/identity/
```
Expected: builds successfully

**Step 4: Commit**

```bash
git add -A services/identity/
git commit -m "refactor: move identity/ to services/identity/"
```

---

### Task 7: Create services/qdrant/

**Files:**
- Create: `services/qdrant/railway.toml`

**Step 1: Create Railway config for official Qdrant image**

```toml
# services/qdrant/railway.toml

[build]
dockerImage = "qdrant/qdrant:latest"

[deploy]
healthcheckPath = "/healthz"
healthcheckTimeout = 30
restartPolicyType = "ON_FAILURE"
restartPolicyMaxRetries = 5
```

**Step 2: Commit**

```bash
git add services/qdrant/railway.toml
git commit -m "feat: add services/qdrant with official image Railway config"
```

---

### Task 8: Create services/falkordb/

**Files:**
- Create: `services/falkordb/railway.toml`

**Step 1: Create Railway config for official FalkorDB image**

```toml
# services/falkordb/railway.toml

[build]
dockerImage = "falkordb/falkordb:latest"

[deploy]
restartPolicyType = "ON_FAILURE"
restartPolicyMaxRetries = 5
```

**Step 2: Commit**

```bash
git add services/falkordb/railway.toml
git commit -m "feat: add services/falkordb with official image Railway config"
```

---

### Task 9: Clean up old files

**Files:**
- Delete: `railway.toml` (root — was Discord bot)
- Delete: `Dockerfile.discord` (replaced by `services/discord/Dockerfile`)
- Delete: `Dockerfile.gateway` (replaced by `services/gateway/Dockerfile`)
- Delete: `Dockerfile.teams` (Teams not deployed to Railway)
- Delete: `mypalclara/services/backup/Dockerfile` (replaced by `services/backup/Dockerfile`)
- Delete: `mypalclara/services/backup/railway.toml` (replaced by `services/backup/railway.toml`)

**Step 1: Remove old files**

```bash
git rm railway.toml Dockerfile.discord Dockerfile.gateway Dockerfile.teams
git rm mypalclara/services/backup/Dockerfile mypalclara/services/backup/railway.toml
```

**Step 2: Verify no broken references**

Search for references to old paths:
```bash
grep -r "Dockerfile.discord\|Dockerfile.gateway\|Dockerfile.teams" . --include="*.yml" --include="*.yaml" --include="*.toml" --include="*.md"
```
Expected: hits in `docker-compose.yml` (fixed in Task 10) and possibly docs (fixed in Task 11).

**Step 3: Commit**

```bash
git add -A
git commit -m "chore: remove old root-level Dockerfiles and railway.toml"
```

---

### Task 10: Update docker-compose.yml

**Files:**
- Modify: `docker-compose.yml`

Update build contexts and Dockerfile paths for all services that moved.

**Step 1: Update discord-bot service**

Change:
```yaml
    build:
      context: .
      dockerfile: Dockerfile.discord
```
To:
```yaml
    build:
      context: .
      dockerfile: services/discord/Dockerfile
```

**Step 2: Update discord-adapter service**

Same change as discord-bot:
```yaml
    build:
      context: .
      dockerfile: services/discord/Dockerfile
```

**Step 3: Update gateway service**

Change:
```yaml
    build:
      context: .
      dockerfile: Dockerfile.gateway
```
To:
```yaml
    build:
      context: .
      dockerfile: services/gateway/Dockerfile
```

**Step 4: Update teams-adapter service**

Change:
```yaml
    build:
      context: .
      dockerfile: Dockerfile.teams
```
To:
```yaml
    build:
      context: .
      dockerfile: services/gateway/Dockerfile
```
Note: Teams adapter shared the same Python base pattern. Using gateway Dockerfile since it has the same deps. The CMD is overridden in docker-compose via the existing `command` or entrypoint. Actually, check — the teams-adapter in docker-compose doesn't override CMD. We need a `services/teams/Dockerfile` too, OR override the command in docker-compose:

```yaml
    build:
      context: .
      dockerfile: services/gateway/Dockerfile
    command: ["python", "-m", "mypalclara.adapters.teams"]
```

**Step 5: Update backup service**

Change:
```yaml
    build:
      context: .
      dockerfile: mypalclara/services/backup/Dockerfile
```
To:
```yaml
    build:
      context: .
      dockerfile: services/backup/Dockerfile
```

**Step 6: Verify compose config is valid**

```bash
docker compose config --quiet
```
Expected: exits 0, no errors

**Step 7: Commit**

```bash
git add docker-compose.yml
git commit -m "refactor: update docker-compose to use new services/ Dockerfile paths"
```

---

### Task 11: Update CLAUDE.md and documentation

**Files:**
- Modify: `CLAUDE.md`

**Step 1: Update directory structure table**

Update the directory structure section in CLAUDE.md to reflect:
- `services/discord/` — Discord bot Railway service
- `services/gateway/` — Gateway Railway service
- `services/backup/` — Backup Railway service
- `services/web-ui/` — Web UI (Rails + React) Railway service (was `web-ui/`)
- `services/identity/` — Identity service Railway service (was `identity/`)
- `services/qdrant/` — Qdrant vector DB Railway service
- `services/falkordb/` — FalkorDB graph DB Railway service
- `services/base/` — Shared base Docker image

Remove references to:
- `web-ui/backend/` and `web-ui/frontend/` as top-level entries (now under `services/web-ui/`)
- Old Dockerfile paths (`Dockerfile.discord`, `Dockerfile.gateway`)

**Step 2: Update Docker commands**

Replace:
```bash
docker-compose --profile discord up
```
With the same (docker-compose commands don't change — only internal paths do).

Update any reference to `web-ui/` paths:
```bash
cd web-ui/backend && rails s -p 3000    # old
cd services/web-ui/backend && rails s -p 3000  # new

cd web-ui/frontend && npm run dev    # old
cd services/web-ui/frontend && npm run dev  # new
```

**Step 3: Add base image build instructions**

Add to Quick Reference:
```bash
# Build base image (required before building Python services)
docker build -f services/base/Dockerfile -t clara-base:latest .

# Build individual services
docker build -f services/discord/Dockerfile -t clara-discord:latest .
docker build -f services/gateway/Dockerfile -t clara-gateway:latest .
docker build -f services/backup/Dockerfile -t clara-backup:latest .
```

**Step 4: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: update CLAUDE.md for services/ directory restructure"
```

---

### Task 12: Verify full build chain

**Step 1: Build base image**

```bash
docker build -f services/base/Dockerfile -t clara-base:latest .
```
Expected: SUCCESS

**Step 2: Build all dependent services**

```bash
docker build -f services/discord/Dockerfile -t clara-discord:latest .
docker build -f services/gateway/Dockerfile -t clara-gateway:latest .
docker build -f services/backup/Dockerfile -t clara-backup:latest .
```
Expected: all SUCCESS

**Step 3: Build standalone services**

```bash
docker build -t clara-web-ui:latest services/web-ui/
docker build -t clara-identity:latest services/identity/
```
Expected: all SUCCESS

**Step 4: Validate docker-compose**

```bash
docker compose config --quiet
```
Expected: exits 0

**Step 5: Commit if any fixes were needed**

```bash
git add -A
git commit -m "fix: resolve build issues from service isolation"
```
