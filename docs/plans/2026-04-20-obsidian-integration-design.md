# Obsidian Integration Design

**Date:** 2026-04-20
**Status:** Approved

## Problem

Clara has no access to the user's Obsidian vault. The user already runs the [obsidian-local-rest-api](https://github.com/coddingtonbear/obsidian-local-rest-api) plugin (exposed at `obsidian.shmp.app`) and wants Clara to read, search, and write notes as a first-class capability — not as an external MCP server but as a local, integrated tool surface with per-user configuration and per-user prompt context.

Reference surface: [mcp-obsidian](https://github.com/MarkusPfundstein/mcp-obsidian) exposes 7 tools over the same REST API. This design takes a superset.

## Solution

Three coordinated additions:

1. **Identity service** grows per-user Obsidian configuration (token + host + TLS verify flag), stored encrypted at rest, with a service-auth endpoint that returns decrypted credentials to the gateway.
2. **Clara gateway** gains a new `core_tools/obsidian_tool.py` (16 tools covering the full REST API surface), a shared HTTP client, and a vault-snapshot cache.
3. **Gateway infrastructure** gains two previously-missing capabilities this feature requires: per-user tool filtering (threading `user_id` through `get_all_tools()`) and per-tool `SYSTEM_PROMPT` inclusion in prompt construction. Both are reusable by future per-user integrations (Google, GitHub).

Obsidian tools are **only** exposed to users who have configured a token. Prompt context includes a live, cached snapshot of the vault (top folders, tag counts, recent edits, today's daily note) that invalidates on any Clara-initiated write.

## Scope

**In scope:**

- New fields on `CanonicalUser` in the identity service: encrypted token, api host, verify-TLS flag
- Three identity-service endpoints: `PUT/DELETE /users/me/obsidian-config`, `GET /users/{id}/obsidian-token` (service-auth), plus three extra fields on `GET /users/me`
- Identity-service UI: new "Integrations" section with Obsidian card (token input, host input, TLS checkbox)
- Fernet encryption at rest for the token, key loaded from `SECRETS_ENCRYPTION_KEY`
- Obsidian HTTP client wrapper under `mypalclara/core/obsidian/`
- 16 tools in `mypalclara/core/core_tools/obsidian_tool.py` covering vault CRUD, periodic notes, search (simple + DQL + JsonLogic), tags, commands, active file, open-in-UI
- Per-user tool filtering: `ToolDef.availability` predicate, `user_id` param on `get_all_tools()`, per-request cache of availability decisions
- Wiring for per-tool `SYSTEM_PROMPT` (infra exists but is unused; this feature enables it)
- Vault snapshot (`VaultSnapshot` dataclass, in-memory cache, invalidation on write) injected into Clara's system prompt
- Unit tests + one marked integration test against `obsidian.shmp.app`

**Out of scope:**

- Bidirectional sync or file watching
- Obsidian plugin installation / vault provisioning
- Multi-vault per user
- OAuth for Obsidian (the REST API uses static bearer tokens by design)
- Fernet key rotation
- Using Obsidian as a memory backend (Clara's Palace stays where it is)

## Architecture

```
┌─────────────────────┐       ┌──────────────────────────┐       ┌─────────────────────┐
│  Identity Service   │       │    Clara Gateway         │       │   obsidian.shmp.app │
│  (FastAPI :18791)   │       │  (processor + tools)     │       │  (Local REST API)   │
├─────────────────────┤       ├──────────────────────────┤       ├─────────────────────┤
│  CanonicalUser      │       │  on each request:        │       │  Bearer <token>     │
│   + obsidian_token  │◄──────┤  1. GET /users/me        │       │  /vault, /search,   │
│   + obsidian_host   │ GET   │     → token, host, tls   │       │  /periodic, /tags,  │
│   + obsidian_verify │       │  2. if configured:       │       │  /commands, /open,  │
│                     │       │     - include obsidian_* ├──────►│  /active            │
│  PUT /users/me/     │       │       tools              │ HTTPS │                     │
│    obsidian-config  │       │     - fetch snapshot     │       │                     │
│                     │       │       (cached, invalid-  │       │                     │
│  UI: React SPA      │       │        ated on write)    │       │                     │
│   + Obsidian card   │       │     - inject into prompt │       │                     │
└─────────────────────┘       │  3. run LLM loop         │       │                     │
                              │  4. on obsidian_* write: │       │                     │
                              │     invalidate snapshot  │       │                     │
                              └──────────────────────────┘       └─────────────────────┘
```

## Identity service changes

### Schema (`services/identity/db.py`)

Extend `CanonicalUser` with:

```python
encrypted_obsidian_token = Column(LargeBinary, nullable=True)
obsidian_api_host        = Column(Text, nullable=True)   # e.g. "obsidian.shmp.app"
obsidian_verify_tls      = Column(Boolean, nullable=False, server_default="true")
obsidian_updated_at      = Column(DateTime, nullable=True)
```

Reasoning: not reusing the `OAuthToken` table — that's designed for OAuth2 flows with refresh tokens, whereas Obsidian uses a static bearer token with no refresh semantics.

Alembic migration adds the four columns.

### Endpoints (`services/identity/app.py`)

Browser-facing (JWT auth):

```
PUT /users/me/obsidian-config
  Body: { api_token: str, api_host?: str, verify_tls?: bool }
  Returns: { configured: true, api_host, verify_tls }
  Side effect: encrypts api_token with Fernet and stores

DELETE /users/me/obsidian-config
  Returns: { configured: false }
  Side effect: nulls all three columns

GET /users/me                       # existing endpoint, extended response
  Returns: { ..., obsidian_configured: bool,
                  obsidian_api_host: str | None,
                  obsidian_verify_tls: bool }
```

Internal (service-auth, `X-Service-Secret`):

```
GET /users/{canonical_user_id}/obsidian-token
  Returns: { api_token: str, api_host: str, verify_tls: bool } or 404
```

The browser-facing endpoints **never** return the token. The service-auth endpoint is the only way to read the decrypted value, and only the gateway calls it.

### UI (`services/identity/static/index.html`)

Add an "Integrations" section below API Keys. Obsidian card:

- Password-type input for API token
- Text input for API host (placeholder: `obsidian.shmp.app`)
- Checkbox: "Verify TLS certificate" (default on)
- Save and Clear buttons
- Status indicator: "Configured" / "Not configured"

Styling matches existing API key form. Save button calls `PUT /users/me/obsidian-config`; Clear calls `DELETE`. The token field renders empty after save (same pattern as API keys — server never returns it).

### Encryption

- `SECRETS_ENCRYPTION_KEY` env var: URL-safe base64 Fernet key, 32 bytes
- Loaded once at app startup
- App startup fails fast if any `encrypted_obsidian_token` rows are non-null and the key is missing/invalid (prevents silent mis-config after key loss)
- Key rotation is future work — design space: re-encrypt on next user write, or batch job

## Obsidian HTTP client (`mypalclara/core/obsidian/`)

New module. Three files.

### `client.py`

```python
class ObsidianClient:
    def __init__(self, api_host: str, api_token: str, verify_tls: bool = True,
                 timeout: float = 10.0): ...

    # Vault files
    async def list_vault() -> list[str]                               # GET  /vault/
    async def list_dir(path: str) -> list[str]                        # GET  /vault/{path}/
    async def get_file(path: str) -> str                              # GET  /vault/{path}
    async def put_file(path: str, content: str) -> None               # PUT  /vault/{path}
    async def append_file(path: str, content: str) -> None            # POST /vault/{path}
    async def patch_file(path, target_type, target, content,
                         operation="append") -> None                  # PATCH /vault/{path}
    async def delete_file(path: str) -> None                          # DELETE /vault/{path}

    # Active file
    async def get_active() -> tuple[str, str]                         # GET  /active/
    async def put_active(content: str) -> None                        # PUT  /active/

    # Periodic notes
    async def get_periodic(period: str, date: date | None = None) -> str
    async def append_periodic(period: str, content: str,
                              date: date | None = None) -> None

    # Search
    async def search_simple(query: str) -> list[SearchHit]            # POST /search/simple/
    async def search_dql(query: str) -> list[dict]                    # POST /search/ (DQL)
    async def search_jsonlogic(query: dict) -> list[dict]             # POST /search/ (JsonLogic)

    # Tags & commands
    async def list_tags() -> list[tuple[str, int]]                    # GET  /tags/
    async def list_commands() -> list[dict]                           # GET  /commands/
    async def execute_command(command_id: str) -> None                # POST /commands/{id}/
    async def open_file(path: str) -> None                            # POST /open/{path}
```

Uses `httpx.AsyncClient` with bearer auth header and `verify=self.verify_tls`. Typed exceptions: `ObsidianAuthError` (401/403), `ObsidianNotFoundError` (404), `ObsidianRateLimitError` (429), `ObsidianConnectionError` (network/timeout), `ObsidianServerError` (5xx).

### `factory.py`

```python
async def get_client_for_user(canonical_user_id: str) -> ObsidianClient | None:
    """Fetch credentials from identity service, return client or None if unconfigured."""
```

Calls `GET {IDENTITY_SERVICE_URL}/users/{id}/obsidian-token` with `X-Service-Secret`. Returns `None` on 404 (unconfigured). Caches `ObsidianClient` instances per-user for ~60s to avoid re-fetching creds on every tool call within a multi-tool LLM turn.

### `exceptions.py`

Typed exception hierarchy as above.

## Tool module (`mypalclara/core/core_tools/obsidian_tool.py`)

Standard `TOOLS: list[ToolDef]` pattern. Module-level `SYSTEM_PROMPT` with usage guidance.

### The 16 tools

**Read (7):**

| Name | Purpose |
|------|---------|
| `obsidian_list_vault` | List files/dirs at vault root |
| `obsidian_list_dir` | List files/dirs at a path |
| `obsidian_get_file` | Read a note's full content |
| `obsidian_get_active_file` | Read the currently-open note |
| `obsidian_get_periodic_note` | Read today's (or a specific date's) daily/weekly/monthly note |
| `obsidian_list_tags` | List all tags with usage counts |
| `obsidian_list_commands` | List available Obsidian commands |

**Search (2):**

| Name | Purpose |
|------|---------|
| `obsidian_search` | Full-text search (simple) |
| `obsidian_query` | DQL or JsonLogic structured query (single tool with `query_type` arg) |

**Write (5):**

| Name | Purpose |
|------|---------|
| `obsidian_create_or_update_file` | PUT — create or replace a note |
| `obsidian_append_to_file` | POST — append content |
| `obsidian_patch_file` | PATCH — insert relative to heading, block, or frontmatter |
| `obsidian_append_to_periodic_note` | Append to today's daily/weekly/etc. note |
| `obsidian_delete_file` | Delete a note |

**UI / commands (2):**

| Name | Purpose |
|------|---------|
| `obsidian_open_file` | Open a note in the Obsidian UI (user-visible) |
| `obsidian_execute_command` | Run an Obsidian command by ID |

### Handler pattern

```python
async def _handle_list_vault(args: dict, ctx: ToolContext) -> str:
    client = await get_client_for_user(ctx.user_id)
    if client is None:
        return "Obsidian is not configured for this user."
    try:
        files = await client.list_vault()
        return json.dumps(files)
    except ObsidianAuthError:
        return "Obsidian authentication failed. User should update their token."
    except ObsidianConnectionError as e:
        return f"Obsidian unreachable: {e}"
```

Write-tool handlers call `snapshot_cache.invalidate(ctx.user_id)` on success.

### Availability predicate

Module exports:

```python
async def has_obsidian_config(user_id: str) -> bool: ...
```

…cached per-request (see next section). Every `ToolDef` in this module sets `availability=has_obsidian_config`.

### `SYSTEM_PROMPT`

~150 words describing when and how to use the tools: prefer `search` before `get` when looking for something; use `patch` for targeted edits rather than `create_or_update` which replaces; periodic notes are Josh's journal; `open_file` is user-visible so use it sparingly; write tools mutate the vault and their output is shown to Josh. Registered via `registry.register_system_prompt("obsidian", SYSTEM_PROMPT)`.

## Per-user tool filtering (infrastructure)

**New capability the codebase doesn't have.** This feature is the forcing function.

### Changes

- `ToolDef` (in `mypalclara/tools/_base.py`) grows `availability: Callable[[str], Awaitable[bool]] | None = None`.
- `tool_executor.get_all_tools()` gains `user_id: str | None = None`. When provided:
  - For each tool with a non-None `availability`, await the predicate
  - Predicates with the same identity (same callable) share a single await via a per-request memo
  - Tools whose predicate returns False are dropped from the result
- `gateway/processor.py` passes `user_id` (derived from the incoming request's canonical user) into `get_all_tools()`.

### Performance

One identity-service HTTP call per user per request, not per tool. `has_obsidian_config(user_id)` hits `GET /users/me`-equivalent and caches the result in a `contextvars.ContextVar` for the lifetime of the request.

## Vault snapshot + cache (`mypalclara/core/obsidian/snapshot.py`)

### `VaultSnapshot` dataclass

```python
@dataclass
class VaultSnapshot:
    host: str
    top_level_folders: list[str]          # from list_vault()
    total_note_count: int                  # walk of list_vault recursion, capped
    top_tags: list[tuple[str, int]]        # top 10 from list_tags()
    recent_notes: list[str]                # 5 most recently modified paths
    today_periodic: str | None             # today's daily note title or None
    fetched_at: datetime
```

### `build_snapshot(client) -> VaultSnapshot`

Issues ~4 REST calls in parallel via `asyncio.gather`:

1. `list_vault()` → top-level folders + count approximation
2. `list_tags()` → top 10 by usage
3. Recent notes: prefer `search_dql("TABLE file.mtime FROM \"\" SORT file.mtime DESC LIMIT 5")` if DQL is enabled; fallback to empty list
4. `get_periodic("daily")` title only — swallow 404

Total timeout: 5 seconds. If any call fails, degrade that field (empty list / None) rather than failing the whole snapshot.

### `SnapshotCache`

```python
class SnapshotCache:
    _cache: dict[str, VaultSnapshot]           # canonical_user_id -> snapshot
    _locks: dict[str, asyncio.Lock]            # per-user build lock

    async def get_or_build(user_id: str,
                           client: ObsidianClient) -> VaultSnapshot | None: ...
    def invalidate(user_id: str) -> None: ...
```

- No TTL. Cache lives until explicitly invalidated or process restart.
- Per-user lock prevents thundering herd when two concurrent LLM turns arrive for the same user with an empty cache.
- On build failure, cache a sentinel "unavailable" marker with a short TTL (30 seconds) to avoid hammering Obsidian when it's down.

### Invalidation

- Obsidian write tools call `snapshot_cache.invalidate(ctx.user_id)` in their handler after a successful call.
- `DELETE /users/me/obsidian-config` triggers invalidation via an identity-service → gateway webhook (stretch goal — MVP just accepts staleness until next gateway restart).
- Gateway process restart clears everything.

### Prompt rendering

```
**Josh's Obsidian vault** (obsidian.shmp.app):
1,247 notes across folders: Projects/, Daily/, Reference/, Inbox/.
Top tags: #work (412), #clara (187), #ideas (143), …
Recent edits: Projects/mypalclara.md, Daily/2026-04-20.md, Inbox/random.md, …
Today's daily note: "2026-04-20" (exists).
```

Budget: ~200 tokens. Rendered by `VaultSnapshot.to_prompt_block()`. If the snapshot is the "unavailable" sentinel, the block reads: "Josh has Obsidian configured but vault details are currently unavailable."

## Prompt builder integration

### Changes to `mypalclara/core/prompt_builder.py`

- `PromptBuilder.build_prompt()` gains `user_id: str | None = None` param (thread it through from `processor.py`).
- After persona + capability inventory, if `user_id` and `has_obsidian_config(user_id)`:
  - Fetch snapshot via `snapshot_cache.get_or_build(user_id, client)` (returns cached in ~1ms for warm cache)
  - Append `snapshot.to_prompt_block()` under a "User Context" section

### Changes to `mypalclara/core/security/worm_persona.py`

- `build_worm_persona(personality, tools, system_prompts=None)` gains `system_prompts` param.
- When provided, each registered per-tool `SYSTEM_PROMPT` is rendered after the capability inventory.
- `PromptBuilder` gathers `registry.get_system_prompts(tool_modules=enabled_modules)` and passes in.

This wires up the existing `registry.register_system_prompt()` mechanism that was previously unused. ~15 LOC change.

## Error handling

| Failure | Behavior |
|---------|----------|
| Identity service unreachable when fetching token | Availability predicate returns False → tools silently absent. Log warning. |
| Token decrypt fails (key mismatch / corrupted) | Service returns 500 to gateway; availability returns False. Log critical. |
| Obsidian API 401/403 | Tool returns user-facing "Obsidian auth failed, please update your token". Snapshot fetch returns unavailable sentinel. |
| Obsidian API 404 on specific file | Tool returns "Note not found: {path}". |
| Obsidian API 429 | One retry with 1s backoff; on second failure, return "Obsidian rate-limited, try again shortly". |
| Obsidian API 5xx | Return "Obsidian server error: {code}". |
| Network timeout (10s) | Return "Obsidian timed out". |
| Snapshot build timeout (5s total) | Inject the "unavailable" sentinel; 30s cool-off before retry. |
| Cache invalidation during concurrent read | `asyncio.Lock` per user serializes rebuilds; readers wait. |

Errors never block prompt construction. The cache's failure-sentinel behavior guarantees bounded degradation.

## Security

- **Token at rest:** Fernet-encrypted. `SECRETS_ENCRYPTION_KEY` env var. Key loss = unrecoverable encrypted tokens; documented in identity-service README.
- **Token in transit (identity service → gateway):** HTTPS + `X-Service-Secret` header (existing pattern).
- **Token in transit (gateway → obsidian.shmp.app):** HTTPS with TLS verification on by default. Per-user opt-out (`verify_tls=false`) for localhost self-signed certs.
- **Token in logs:** Never. The client redacts Authorization headers in debug logs. Tests verify redaction.
- **Token in responses:** Browser-facing endpoints never return the token. Only the service-auth endpoint does, and it requires `X-Service-Secret`.
- **Tool authorization:** The availability predicate is the gate — Clara only sees tools for users who have configured a token. No cross-user access (canonical_user_id is scoped per request).
- **DQL/JsonLogic injection:** The `obsidian_query` tool passes user-constructed queries to Obsidian, but Obsidian itself sandboxes them (read-only over the vault). No SQL-injection class of risk.
- **Dev token handling:** The dev token Josh provided for testing against `obsidian.shmp.app` is stored in `OBSIDIAN_DEV_TOKEN` env var locally, never committed, and used only by the marked integration test.

## Testing

### Unit tests (`tests/core/obsidian/`)

- `test_client.py` — all client methods against a mocked `httpx.AsyncClient`. Covers success paths, each typed exception, TLS verify flag, header correctness, URL construction.
- `test_factory.py` — `get_client_for_user` with mocked identity service. Covers configured / unconfigured / service-down.
- `test_snapshot.py` — `build_snapshot` with mocked client. Covers all four REST calls succeed, partial degrade (one call fails), full degrade (all fail → unavailable sentinel).
- `test_cache.py` — invalidation, concurrent `get_or_build` with lock, TTL on failure sentinel.
- `test_tool_handlers.py` — each of the 16 tool handlers with a mocked client. Verify write handlers call `snapshot_cache.invalidate`.

### Unit tests (`tests/tools/`)

- `test_availability_filtering.py` — `get_all_tools(user_id=...)` filters by `ToolDef.availability` predicate; per-request memoization doesn't re-call predicates.

### Unit tests (`tests/services/identity/`)

- `test_obsidian_config_endpoints.py` — PUT stores encrypted token, DELETE clears, GET /users/me includes three new fields but never the token, service-auth endpoint returns decrypted value.
- `test_encryption.py` — Fernet round-trip; startup fails when key is missing and tokens exist.

### Integration test (`tests/integration/test_obsidian_live.py`)

- Marked `@pytest.mark.integration`; skipped by default.
- Runs only when `OBSIDIAN_DEV_TOKEN` env var is set.
- Exercises: `list_vault`, `search_simple("test")`, `get_periodic("daily")`, `append_periodic("daily", "Clara test")`, `delete` of the test-created content.

### Prompt integration test

- `test_prompt_with_vault_snapshot.py` — full `build_prompt(user_id=...)` with a mocked snapshot cache. Verifies the vault block is injected when configured, absent when not.

## Rollout

1. Identity service: schema + endpoints + UI, deployed first. Existing users unaffected (all new columns nullable / default).
2. Gateway: HTTP client + factory + snapshot + cache, no tools yet. Dead code, compiles and tests.
3. Gateway: tool module + per-user filtering + prompt integration, shipped together.
4. Josh configures his token via the identity SPA.
5. Smoke test in a Discord DM: `"what's in my vault?"` → Clara lists folders.

## Open questions (deferred, not blocking)

- Fernet key rotation strategy when needed.
- Whether to expose an MCP-compatible surface later so external MCP clients can reuse the same token storage.
- Whether Obsidian should eventually become a read-only memory source for the Palace (separate design).
