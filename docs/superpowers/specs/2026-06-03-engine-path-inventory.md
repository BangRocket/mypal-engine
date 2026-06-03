# Engine ↔ Client Path Inventory (Phase 2 filter-repo spec)

Locked after Phase 1; the engine import-boundary test (`tests/architecture/test_engine_boundary.py`) is green and an independent scan reports zero engine violations.

## Engine set → moves to `mypal-engine`
- `mypal_protocol/`            (shared wire contract; published from mypal-engine)
- `mypalclara/gateway/`
- `mypalclara/core/`           (includes `core/game/`)
- `mypalclara/db/`
- `mypalclara/config/`
- `mypalclara/sandbox/`
- `mypalclara/tools/`
- `mypalclara/services/proactive/`
- `mypalclara/services/blog/`
- `mypalclara/services/email/`
- `mypalclara/services/backup/`  (DB/infra sidecar — engine owns the DB)
- `services/gateway/`, `services/base/`   (Dockerfiles / base image)
- `mypalclara/db/migrations/`   (Alembic, incl. head 506b1c1496b6)

## Client set → stays in `mypalclara`
- `mypalclara/adapters/`        (Discord incl. `adapters/discord/ui/`, Teams, CLI, …)
- `mypalclara/adapters/cli/launch_adapters.py`  (dev launcher)
- `services/web-ui/`            (Rails + React; HTTP-API client)
- per-adapter deploy configs under `services/`

## Shared
- `mypal_protocol/`  — consumed by both sides (published from mypal-engine)

## Invariant (enforced by test)
- No engine module imports a platform SDK or `mypalclara.adapters`.
- No client module imports `mypalclara.gateway.*` (only `mypal_protocol`).

## Notes carried into Phase 2
- The Discord log-channel handler (`DiscordLogHandler` in `config/logging.py`) is
  discord-free but currently has no caller; if re-activated, the Discord adapter must
  pass an `embed_renderer` to `init_discord_logging`.
- The ORS/proactive loop and email monitor are wired to deliver over the gateway WS
  (`send_fn` / `send_alert_fn`), but the ORS loop itself is not yet started by the
  gateway.
- Pre-existing, env-driven import quirk (out of scope): `services/blog/scheduled.py`
  parses `BLOG_CATEGORIES` as ints at import time; a non-numeric `.env` value raises.
