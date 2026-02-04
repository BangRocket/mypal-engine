# Migration Guide: main → 2026.05.15

This document covers migrating from the main branch (2026.05.9) to version 2026.05.15.

## Breaking Changes

### ORS (Organic Response System) Removed

The proactive messaging system has been completely removed. If you were using ORS:

**Removed files:**
- `organic_response_system.py`
- `proactive_engine.py`
- `docs/ors-context-sensitivity-spec.md`
- `clara-mcp-server/src/tools/ors_notes.rs`

**Removed environment variables:**
- `ORS_ENABLED` / `PROACTIVE_ENABLED`
- `ORS_BASE_INTERVAL_MINUTES`
- `ORS_MIN_SPEAK_GAP_HOURS`
- `ORS_ACTIVE_DAYS`
- `ORS_NOTE_DECAY_DAYS`
- `ORS_IDLE_TIMEOUT_MINUTES`

**Removed database tables:**
- `proactive_messages`
- `proactive_assessments`
- `proactive_notes`

**Migration:** No action required. ORS tables will remain but are unused.

### Database Migration System

Alembic is now used for database migrations instead of auto-creation.

**New dependency:** `alembic ^1.18.1`

**New files:**
- `scripts/migrate.py` - Migration CLI
- `alembic.ini` - Alembic configuration
- `db/migrations/` - Migration scripts

**Migration commands:**
```bash
# Check migration status
poetry run python scripts/migrate.py status

# Run pending migrations
poetry run python scripts/migrate.py upgrade

# Create new migration
poetry run python scripts/migrate.py create "description"
```

**Note:** Migrations run automatically on startup via `run_alembic_migrations()`.

---

## New Features

### Gateway Architecture (In Development)

A WebSocket gateway for platform adapters. Currently opt-in and not required for normal operation.

**New files:**
- `gateway/` - Full gateway implementation
- `adapters/` - Platform adapter base classes

**New environment variables:**
```bash
CLARA_GATEWAY_HOST=127.0.0.1
CLARA_GATEWAY_PORT=18789
CLARA_GATEWAY_SECRET=optional-shared-secret
```

**Usage:**
```bash
# Run gateway (separate from Discord bot)
poetry run python -m gateway
```

### Hooks System

Event-driven automation for the gateway.

**New files:**
- `gateway/events.py` - Event system
- `gateway/hooks.py` - Hook management
- `hooks/hooks.yaml.example` - Example configuration

**Configuration:** Create `hooks/hooks.yaml`:
```yaml
hooks:
  - name: log-startup
    event: gateway:startup
    command: echo "Gateway started"

  - name: notify-errors
    event: tool:error
    command: curl -X POST https://webhook.example.com -d "${CLARA_EVENT_DATA}"
```

**Event types:**
- `gateway:startup`, `gateway:shutdown`
- `adapter:connected`, `adapter:disconnected`
- `session:start`, `session:end`, `session:timeout`
- `message:received`, `message:sent`, `message:cancelled`
- `tool:start`, `tool:end`, `tool:error`
- `scheduler:task_run`, `scheduler:task_error`

**New environment variables:**
```bash
CLARA_HOOKS_DIR=./hooks
```

### Scheduler System

Task scheduling with cron, interval, and one-shot support.

**New files:**
- `gateway/scheduler.py` - Scheduler implementation
- `scheduler.yaml.example` - Example configuration

**Configuration:** Create `scheduler.yaml`:
```yaml
tasks:
  - name: cleanup-sessions
    type: interval
    interval: 3600
    command: poetry run python -m scripts.cleanup_sessions

  - name: daily-backup
    type: cron
    cron: "0 3 * * *"
    command: ./scripts/backup.sh
```

**Task types:**
- `one_shot` - Run once (with optional delay)
- `interval` - Run every N seconds
- `cron` - Standard 5-field cron expression

**New environment variables:**
```bash
CLARA_SCHEDULER_DIR=.
```

### Incus Sandbox Support

Alternative to Docker for code execution with optional VM isolation.

**New files:**
- `sandbox/incus.py` - Incus sandbox manager

**New environment variables:**
```bash
SANDBOX_MODE=incus          # or incus-vm for VM isolation
INCUS_SANDBOX_IMAGE=images:debian/12/cloud
INCUS_SANDBOX_TYPE=container  # or vm
INCUS_SANDBOX_TIMEOUT=900
INCUS_SANDBOX_MEMORY=512MiB
INCUS_SANDBOX_CPU=1
INCUS_REMOTE=local
```

**Mode selection (`SANDBOX_MODE`):**
| Mode | Description |
|------|-------------|
| `docker` | Local Docker containers |
| `incus` | Incus containers (faster startup) |
| `incus-vm` | Incus VMs (stronger isolation) |
| `auto` | Auto-select (incus → docker) |

**Prerequisites:** Incus must be installed and configured on the host.

---

## Dependency Changes

### Added
- `alembic ^1.18.1` - Database migrations
- `websockets ^15.0` - Gateway WebSocket server
- `pytest-asyncio ^0.24.0` - Async test support (dev)

### Updated
Run `poetry install` to update dependencies.

---

## Docker Changes

### Discord Image
- Added `adapters/` directory to image
- Added default `personality.txt` for mount compatibility

### Rust MCP Server
- Updated to Rust nightly for edition2024 support
- Removed ORS notes tools

---

## Migration Steps

1. **Update dependencies:**
   ```bash
   poetry install
   ```

2. **Run database migrations:**
   ```bash
   poetry run python scripts/migrate.py upgrade
   ```

3. **Remove deprecated environment variables** (if set):
   - All `ORS_*` / `PROACTIVE_*` variables

4. **(Optional) Configure new features:**
   - Copy `hooks/hooks.yaml.example` to `hooks/hooks.yaml`
   - Copy `scheduler.yaml.example` to `scheduler.yaml`
   - Set `SANDBOX_MODE=incus` if using Incus

5. **Rebuild Docker images** (if using Docker):
   ```bash
   docker-compose build
   ```

---

## Rollback

To rollback to main:
```bash
git checkout main
poetry install
```

Note: Database schema changes are backward compatible. No rollback migration needed.
