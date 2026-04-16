# Service Isolation Design

## Goal

Restructure the repository so each Railway-deployable service lives in its own `services/<name>/` directory with its own `railway.toml`. Services that share the `mypalclara/` Python package use a shared base Docker image. This enables independent deployment, scaling, and version control per service in Railway.

## Services

| Service | Type | Directory |
|---------|------|-----------|
| Discord bot | Python (base image) | `services/discord/` |
| Gateway + Proactive | Python (base image) | `services/gateway/` |
| Backup | Python (base image) | `services/backup/` |
| Web-UI | Rails + React (standalone) | `services/web-ui/` |
| Identity | Python (standalone) | `services/identity/` |
| Qdrant | Official image | `services/qdrant/` |
| FalkorDB | Official image | `services/falkordb/` |

**Railway managed plugins** (no directory needed): PostgreSQL, Redis

**Not included** (separate machine): Voice service, Email service

**Bundled with Gateway**: Proactive service

## Base Image Pattern

### Why

Discord, Gateway, and Backup all need the `mypalclara/` Python package. Currently each has a near-identical Dockerfile that copies the full codebase. This duplicates build time and creates drift risk.

### How

1. `services/base/Dockerfile` builds the shared image:
   - Python 3.12 + system deps (build-essential, curl, git, postgresql-client, Node.js 22)
   - Poetry install of `mypalclara/` package
   - Rust MCP server binary (from multi-stage build)
   - Playwright browsers
   - Shared dirs: `/data`, `/app/clara_files`, `/app/mcp_servers`

2. Each dependent service has a minimal Dockerfile:
   ```dockerfile
   FROM clara-base:latest
   EXPOSE <port>
   CMD ["python", "-m", "mypalclara.<module>"]
   ```

3. Base image is built and pushed to a container registry (GitHub Container Registry recommended). Dependent services reference it by tag.

### Build Order

```
clara-base → discord, gateway, backup (parallel)
web-ui, identity, qdrant, falkordb (independent)
```

## Directory Structure

```
services/
  base/
    Dockerfile           # Shared Python base image
    railway.toml         # Optional: trigger base rebuild
  discord/
    Dockerfile           # FROM clara-base, CMD discord
    railway.toml
  gateway/
    Dockerfile           # FROM clara-base, CMD gateway
    railway.toml
  backup/
    Dockerfile           # FROM clara-base, CMD backup
    railway.toml
  web-ui/
    Dockerfile           # Multi-stage Rails+React (self-contained)
    railway.toml
  identity/
    Dockerfile           # Standalone Python (self-contained)
    railway.toml
  qdrant/
    railway.toml         # Official image, env config only
  falkordb/
    railway.toml         # Official image, env config only
```

Root-level files that stay:
- `mypalclara/` — core Python package (consumed by base image)
- `pyproject.toml`, `poetry.lock` — dependency management
- `docker-compose.yml` — local dev (updated to match new structure)
- `clara-mcp-server/` — Rust project (consumed by base image build)
- `personalities/`, `scripts/`, `VERSION` — consumed by base image

Root-level files removed:
- `railway.toml` — deleted (no root service)
- `Dockerfile.discord` — moved to `services/discord/Dockerfile`
- `Dockerfile.gateway` — moved to `services/gateway/Dockerfile`
- `Dockerfile.teams` — deleted (Teams not deployed to Railway)

Directories moved:
- `web-ui/` → `services/web-ui/` (contents preserved)
- `identity/` → `services/identity/` (contents preserved)
- `mypalclara/services/backup/Dockerfile` + `railway.toml` → `services/backup/`

## Railway Configuration

### Python services (base image dependents)

Each `railway.toml` uses `dockerfilePath` relative to its directory. Railway build context is the service directory, but the Dockerfile references the pre-built base image so no repo-root context is needed.

### Official image services (Qdrant, FalkorDB)

These use Railway's Docker image deployment — no Dockerfile, just `railway.toml` specifying the image and environment variables.

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

```toml
# services/falkordb/railway.toml
[build]
dockerImage = "falkordb/falkordb:latest"

[deploy]
restartPolicyType = "ON_FAILURE"
restartPolicyMaxRetries = 5
```

### Standalone services (Web-UI, Identity)

These keep their existing Dockerfiles but move into `services/`. Build context is the service directory itself.

## Migration Impact

### What changes for Railway dashboard

Each service in Railway needs its root directory pointed to `services/<name>/`. This is a Railway dashboard config change, not a code change.

### What changes for docker-compose

`docker-compose.yml` build contexts update to reference new paths:
- `dockerfile: Dockerfile.discord` → `dockerfile: services/discord/Dockerfile`
- `dockerfile: Dockerfile.gateway` → `dockerfile: services/gateway/Dockerfile`
- `context: .` + `dockerfile: mypalclara/services/backup/Dockerfile` → `context: services/backup/`

### What changes for CI/CD

Any CI that builds Dockerfiles needs path updates. Base image build should be a separate CI step that runs first.

## Decisions

- **PostgreSQL and Redis**: Railway managed plugins, no container needed
- **Voice and Email services**: Deployed on separate machine, excluded from this restructure
- **Proactive service**: Bundled with Gateway (not a separate service)
- **Teams adapter**: Not deployed to Railway, `Dockerfile.teams` deleted from root
- **Base image registry**: GitHub Container Registry (ghcr.io)
- **Backup service**: Moves to base image pattern (currently standalone with pip deps). This simplifies it since it can import from `mypalclara` directly.
