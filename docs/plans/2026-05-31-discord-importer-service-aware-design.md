# Discord Importer — Palace-Service-Aware Design

**Date:** 2026-05-31
**Status:** Approved (design)
**Scope:** `scripts/import_discord_chats.py` only (plus its tests). No changes to `mypalclara/core/memory_manager.py`.

## Problem

`scripts/import_discord_chats.py` ingests cleaned Discord exports into Clara's
memory by calling `MemoryManager.reflect_on_session(...)` per conversation
session. It was written against the **embedded** Palace and is incompatible with
the Palace-service migration:

- It imports `from mypalclara.core.memory.config import PALACE` (the *embedded*
  singleton, which is `None` when `USE_PALACE_SERVICE=true`) and exits at
  `:238` with "Palace not initialized".
- `--reset` touches embedded-only surfaces (`mm.episode_store.client` Qdrant,
  `PALACE.vector_store`, `PALACE.graph`, `PALACE.db`) — all `None`/absent on the
  remote path.

The live bot already works in service mode because `MemoryManager.reflect_on_session`
has a remote branch (`_reflect_on_session_remote`) that calls
`PALACE.client.reflect_session(...)`. But that branch hardcodes `mode="async"`
and `self.agent_id`, neither of which fits a bulk import that wants synchronous
backpressure and per-channel persona attribution.

## Goal

Make the importer write the ~1,003 cleaned sessions (DM + JORSHTOPIA + ask-clarissa)
into the **Palace service** when `USE_PALACE_SERVICE=true`, while leaving the
embedded path byte-for-byte unchanged.

## Decisions (confirmed)

1. **Synchronous reflection.** Each session is reflected with `mode="sync"` so
   errors surface per-session and "script done" means "import done".
2. **Per-channel persona `agent_id`.** DM + JORSHTOPIA → `clara`; ask-clarissa →
   `clarissa` (derived from the dominant Clara-variant bot in each channel).
3. **`--reset` disabled in service mode** — warn and no-op; never wipe the shared
   Palace tenant from this script.
4. **Dual-mode preserved.** The service path is additive behind
   `USE_PALACE_SERVICE`; embedded behavior is unchanged.

## Design

### Mode detection & guard fix
Replace the embedded `config.PALACE` import/guard with:
```python
from mypalclara.core.memory.routed import PALACE, USE_PALACE_SERVICE
```
- Embedded (`USE_PALACE_SERVICE=false`): `PALACE` is the embedded singleton —
  current behavior, including `mm.episode_store` checks and `--reset`.
- Service (`true`): `PALACE` is a `RemotePalace` (not `None`); proceed on the
  service write path below.

### Per-channel agent_id resolution
```python
CLARA_BOT_AGENT = {"MyPalClara": "clara", "MyPalClarissa": "clarissa", "MyPalFlorence": "florence"}
```
For each chat file, count `CLARA_BOTS` authors among its messages and pick the
dominant one → channel `agent_id` (fallback: `mm.agent_id`). Log the resolved
agent per file. Embedded path keeps using `mm.agent_id`.

### Service-mode write (per session)
Instead of `mm.reflect_on_session`, call the client directly via the bridge —
the sanctioned escape hatch (`RemotePalace.client`):
```python
episodes = PALACE.bridge.submit(
    PALACE.client.reflect_session(
        messages=msg_dicts,
        user_id=user_id,
        agent_id=channel_agent_id,
        session_id=metadata["channel"],
        mode="sync",
    )
)
# sync returns list[Episode]; log len(episodes)
```
Keep the existing per-run fingerprint dedup, progress logging, and
`total_errors` accounting. Entity-alias registration (`mm.entity_resolver`)
stays as-is — client-side in both modes.

### `--reset`
In service mode: log a clear warning ("`--reset` is embedded-only; ignored in
service mode — no data deleted") and skip the entire reset block. Embedded:
unchanged.

### Startup prerequisites & smoke test
- Log routed mode and the resolved `agent_id` per file at startup.
- In service mode, treat the **first session** as the smoke test: process it
  synchronously like any other (so it is *not* re-imported), but if it raises
  (e.g. server LLM misconfigured — Palace extraction uses the *server's*
  `LLM_API_KEY`/`LLM_PROVIDER`, not mypalclara's — or a 5xx), abort the whole run
  immediately with a clear message rather than churning through the remaining
  ~1,002. Subsequent per-session errors are counted (`total_errors`) and logged,
  not fatal.
- Not a dry-run concern: `--dry-run` still only splits/prints and never writes,
  in either mode.

## Out of scope (YAGNI)
- Cross-run idempotency / server-side dedup (the import is a one-time operation;
  per-run fingerprint dedup stays).
- Async/concurrent reflection, queue monitoring.
- Modifying `MemoryManager` to expose `mode`/`agent_id` (would broaden blast
  radius onto the live bot).
- Pushing entity aliases to Palace (entity resolution remains client-side, as in
  the live bot).

## Testing
Unit tests (no live server; mock `routed.PALACE` + `USE_PALACE_SERVICE`):
1. Service branch calls `reflect_session` with `mode="sync"` and the correct
   per-channel `agent_id`.
2. `--reset` is a no-op in service mode (no destructive calls).
3. Agent resolution maps each fixture channel correctly (Clara / Clarissa /
   fallback).
4. Embedded branch still routes through `mm.reflect_on_session`.
5. Pure helpers (`split_into_sessions`, agent resolution) covered directly.

## Risk / rollback
- Isolated to one script; embedded path untouched → low risk.
- Server-side LLM must be configured for sync reflect to succeed (smoke test
  guards this).
- Rollback is reverting the single file.
