# Gateway → Standalone Engine Extraction — Design

- **Date:** 2026-06-03
- **Status:** Approved design — pending implementation plan
- **Author:** Codesmith (with Claude)

## Context

Today the "gateway" lives inside the `mypalclara` monorepo package. It is already a
separately-deployable *process* (its own Dockerfile, ports 18789 WebSocket + 18790 HTTP API,
and a clean network boundary to adapters), but it is **not** separate *code*: it imports heavily
from `mypalclara.core` (MemoryManager, LLM orchestration, tools), `mypalclara.db` (10+ models,
the connection), and `mypalclara.config`.

The gateway is in fact Clara's **runtime brain** — the `MessageProcessor → LLMOrchestrator →
ToolExecutor` pipeline lives inside it, and it pulls memory, LLM, db, and tools in as libraries.
So when it is extracted, the runtime travels *with* it.

## Goal

Make the gateway a **standalone hub ("the engine")** that owns the runtime *and* the database.
Everything else — adapters, web-UI, and what remains of `mypalclara` — connects to it over its
network interfaces. This inverts today's dependency arrow: instead of the gateway being a leaf
of `mypalclara`, `mypalclara` becomes a client of the engine.

Delivered in two phases:

- **Phase 1 — in-place boundary:** enforce a clean engine ↔ client seam while everything still
  lives in one repo and stays runnable. Mergeable on its own.
- **Phase 2 — extraction:** mechanically lift the now-self-contained engine subgraph into its
  own repo, preserving history.

### Non-goals

- No move to a pure network microservice for the runtime (memory/LLM/db stay *in-process within
  the engine* — they are not split behind RPC).
- No granular shared-library split (`clara-core` / `clara-db` as independently-versioned
  packages). Rejected in favor of "engine owns everything it needs."
- No rewrite of adapter or web-UI behavior beyond the connection boundary.

## Decisions (locked)

1. **Method:** clean the boundary in place first, then a near-mechanical `git filter-repo` lift.
2. **Shape:** the engine is the hub; everyone else connects to it. The runtime + autonomous
   services + database travel with the engine.
3. **Data ownership:** the engine owns the database **exclusively**. External clients (adapters,
   web-UI) reach persisted data only through the engine's WebSocket / HTTP API — never direct DB.
4. **Names:**
   - **mypal-engine** — the standalone hub (runtime + DB + autonomous services).
   - **mypalclara** — trimmed to the client side (adapters, web-UI, backup-infra).
   - **mypal-protocol** — the shared Pydantic wire package, **living inside mypal-engine** for now
     and depended on by both sides.
5. **Security:** `CLARA_GATEWAY_SECRET` becomes **mandatory** for WebSocket registration; the WS
   boundary is hardened (per-adapter identity, `wss`/TLS support) because it is now *the* security
   surface of the hub.

## Target architecture

Three artifacts after the split:

| Artifact | Contains | Connects to engine via |
|---|---|---|
| **mypal-engine** (new standalone hub) | `gateway/` (WS :18789 + HTTP API :18790, processor, orchestrator, tool_executor, scheduler, hooks, events), `core/` (memory, llm, core_tools, mcp, subagent, personality, heartbeat, vm_manager, security, context_compactor, tool_guard, tool_result_guard, email), `db/` (models, connection, migrations — **owns the DB**), `config/`, `sandbox/`, `tools/`, `services/proactive`, `services/blog`, `services/email` (engine-side autonomous services), plus `services/base` (clara-base image). Depends on `mypal-protocol`. | — (it *is* the hub) |
| **mypalclara** (trimmed client side) | `adapters/` (Discord, Teams, Slack, Telegram, Matrix, Signal, WhatsApp, CLI), `services/web-ui/` (Rails + React), and the **dev-only adapter launcher** moved here. Depends on `mypal-protocol`. | WebSocket (adapters) + HTTP API (web-UI) |
| **mypal-protocol** (shared package, inside mypal-engine) | The ~569 lines of pure-Pydantic wire models from today's `gateway/protocol.py` (zero core/db imports). | imported by **both** sides as the wire contract |

### The seam, as a testable invariant

- Nothing in **mypal-engine** imports `mypalclara.adapters` or any platform SDK (`discord`, etc.).
- Nothing on the **client side** imports engine internals — only `mypal_protocol` (adapters) or
  the HTTP API (web-UI).
- The database has exactly **one** owner: the engine.

### Engine vs client path inventory (pinned in Phase 1, step 5)

**Engine set** (moves to mypal-engine):
`mypalclara/gateway/` (minus `protocol.py`), `mypalclara/core/` (all), `mypalclara/db/`,
`mypalclara/config/`, `mypalclara/sandbox/`, `mypalclara/tools/`,
`mypalclara/services/proactive/`, `mypalclara/services/blog/`, `mypalclara/services/email/`,
`services/gateway/` (Dockerfile/Railway), `services/base/` (clara-base).

**Client set** (stays in mypalclara):
`mypalclara/adapters/`, `services/web-ui/`, the per-adapter deploy configs
(`services/discord/`, etc.), and the dev-only adapter launcher.

**Backup placement:** the backup service (`mypalclara/services/backup/`, `services/backup/`)
uses direct DB access (`SessionLocal` / `pg_dump`). To honor "engine owns the DB exclusively,"
backup is treated as an **engine-side DB/infra sidecar** and travels with mypal-engine. *(This
refines the earlier verbal placement on the client side.)*

> The exact path list is finalized in Phase 1 step 5 and locked by the boundary test before any
> file moves in Phase 2.

## Phase 1 — in-place boundary (one repo, still fully runnable)

Ordered so each step is independently verifiable. Only steps 2 and 3 change runtime behavior.

1. **Extract the wire contract → `mypal-protocol`.**
   Move `mypalclara/gateway/protocol.py` into a `mypal-protocol` package (in-repo path/workspace
   dependency for now). Re-point importers — `gateway/*` and the 8 adapter modules
   (`adapters/base.py`, `adapters/protocol.py`, `adapters/protocols.py`, and the `cli`/`discord`/
   `teams` gateway_clients + voice managers) — to import from `mypal_protocol`. Keep a thin
   `gateway/protocol.py` re-export shim during transition. No logic change.

2. **Cut engine → adapters (adapter spawning).**
   `gateway/adapter_manager.py` imports `mypalclara.adapters.manifest` to discover and
   `subprocess.Popen` adapters. Make **external adapters the default**: the engine runs with no
   in-process adapter spawning; adapters launch as their own processes and connect over WebSocket.
   Move the convenience launcher to the **client side** so the engine package never imports
   `mypalclara.adapters`.

3. **Cut engine → platform SDK (email alerts).**
   The email monitor (`services/email/monitor.py`) imports `discord` and uses the live client to
   send alerts. Reroute alert delivery through the existing **gateway → adapter routing**
   (a `ProactiveMessage`/send over WebSocket to whichever adapter owns the channel), so the engine
   never imports `discord` or any platform SDK.

4. **Lock the seam with a test.**
   Add an import-boundary test that fails if: any engine module imports `mypalclara.adapters` or a
   platform SDK; or any client-side module imports engine internals (anything other than
   `mypal_protocol` / the HTTP API). This is the executable definition of "boundary done" and
   guards Phase 2.

5. **Pin the file inventory.**
   Produce the explicit engine-set vs client-set path list and verify the engine set has no inbound
   edges from the client set except via `mypal_protocol`. This list becomes the `git filter-repo`
   path spec for Phase 2.

After Phase 1, `mypalclara` boots and runs exactly as today, but the engine subgraph is provably
self-contained.

## Phase 2 — extraction + deployment

### The lift (mechanical, history-preserving)

- `git filter-repo` carves the **engine path set** into the new **mypal-engine** repo, preserving
  commit history for those paths. Recent in-flight gateway work (CORS, client system-message
  injection, pose-tag stripping) rides along since it lives in `gateway/`.
- **mypalclara** is trimmed: engine paths removed; `adapters/` + `services/web-ui/` + dev adapter
  launcher remain. It gains `mypal-protocol` as a dependency (pulled from mypal-engine).
- **mypal-protocol** is published from inside mypal-engine and consumed by both repos. (Revisit a
  dedicated repo only if a third consumer or independent versioning need appears.)

### Deployment & config split

- **mypal-engine owns:** the gateway Dockerfile (now the primary image), `clara-base`, Alembic
  migrations (including the pending `506b1c1496b6` merge head), backup, and all data-layer env
  (`DATABASE_URL`, `PALACE_*`, Qdrant/FalkorDB, LLM provider keys).
- **mypalclara clients own:** per-adapter Dockerfiles, the web-UI image, and connection env only
  (`CLARA_GATEWAY_URL`, `CLARA_GATEWAY_API_URL`, the shared secret).
- `docker-compose.yml` splits accordingly; adapters/web-UI reference the engine by network
  address, exactly as today.

### WebSocket hardening (security surface of the hub)

- Make `CLARA_GATEWAY_SECRET` **required** for registration (today optional).
- Give each adapter a distinct identity/token.
- Support `wss`/TLS so the hub boundary is authenticated and encrypted rather than open on the LAN.

## Testing & verification

- The Phase 1 boundary test runs in **both** repos' CI.
- Smoke path: engine boots → an adapter connects and round-trips a message → web-UI proxies an API
  call → an email alert routes over the WS.
- Existing test suites pass in each repo independently.

## Risks & mitigations

- **Hidden cross-imports surfacing late** → the Phase 1 boundary test runs *before* any files move.
- **Shared `clara-base` image** → engine keeps it; clients reuse or slim their own.
- **mypal-protocol in-repo → published transition** → kept as a path dependency until Phase 2
  flips it; the package is pure data models, so the move is low-risk.
- **Mandatory secret rollout** → every adapter/web-UI deployment must set `CLARA_GATEWAY_SECRET`;
  document in the env templates and call out as a breaking deploy change.

## Open items

- Final engine/client path list (produced and locked in Phase 1, step 5).
- Whether `mypal-protocol` ever warrants its own repo (deferred; inside mypal-engine for now).
