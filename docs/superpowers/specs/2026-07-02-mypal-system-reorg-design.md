# MyPal System Reorganization: Finish the Decomposition

**Date:** 2026-07-02
**Status:** Approved 2026-07-02.
**Scope:** System-level organization across mypal-engine, mypalclara, mypalace, mypalclara.wiki, and the workspace root.
**Relationship to prior docs:** Companion to `mypalclara/docs/superpowers/specs/2026-06-05-mypalclara-experience-charter-design.md` (the experience charter) and `mypal-engine/docs/superpowers/specs/2026-06-03-gateway-engine-extraction-design.md` (the split design). This doc does not change either; it finishes what they started and adds the system-level layer neither covers.

## Context

The June 2026 monolith split produced the right architecture: mypal-engine is the headless intelligence (gateway WS :18789, HTTP :18790, LLM, tools, MCP, sandbox, DB), mypalclara is the experience layer (Discord/Teams/CLI adapters, voice, future unified app), and mypalace is the standalone memory service (PyPI: mypalace 0.12.0 + mypalace-client). The code boundaries are clean today: the engine has zero adapters, the client has zero engine internals, mypalace imports neither.

What remains is the unfinished periphery of that split, plus the absence of any system-level layer:

1. **Identity collision.** Both mypal-engine and mypalclara build a distribution literally named `mypalclara` at version `2026.19.2`. The engine's 67k-LOC package was never renamed. `clara_core/` (6 files) survives in the engine as dead code from an even earlier rename.
2. **Copy-pasted contract.** `mypal_protocol` is vendored in both repos and has drifted: the engine copy has an `auth_token` field on `RegisterMessage` that the client copy lacks. There is no protocol version constant, no handshake check, and neither repo declares it as a dependency; both import it off `sys.path`.
3. **Duplicated periphery.** `personalities/`, `hooks/`, `VERSION`, and the monolith-era README are identical copies in engine and client. All 10 of the client's `docs/plans/` files also exist in the engine. A stale wiki copy is committed inside the client repo alongside the real `mypalclara.wiki` repo. `docs/migrating-mypalclara.md` is byte-identical in engine and mypalace.
4. **Documentation rot.** Engine README and CLAUDE.md describe the pre-split monolith (adapters, dead run commands, directories that do not exist). The client README is ~60% engine content. Both wiki copies predate the split; `grep -ri mypal-engine` across the wiki returns zero hits. mypalace's README documents the pre-rename `palace/` layout.
5. **No system level.** The workspace root has no README, no scripts, no manifest. The engine, the most active repo, has no CI at all. Engine/client compatibility is enforced by nothing.
6. **In-flight migration left ambiguous.** The engine runs Palace in service mode (`USE_PALACE_SERVICE=true`, `mypalace-client ^0.12.0`), yet the embedded memory system (49 files, 11.8k LOC) remains, because `routed.py` documents remote gaps (remote `history()` returns `[]`; remote store paths raise). Nothing records when or how the embedded path retires.

## Goal

Make the three-aspect system legible and drift-proof:

- Every name means exactly one thing (one repo, one package, one purpose).
- Every shared artifact has exactly one editable home; consumers get it via dependency, API, or generated copy, never by hand-synced duplication.
- Every documentation surface describes the system that exists today.
- The workspace root explains and orchestrates the system.
- CI guards the boundaries so drift cannot silently return.

## Non-goals

- No feature work. The unified app (charter #2), games-to-engine (#3), Rails BFF retirement (#4), and Tauri (#5) remain their own chartered sub-projects; this reorg clears ground for them.
- No behavior change to the running assistant. Env var names (`CLARA_GATEWAY_*`, `PALACE_*`, etc.), ports, DB schemas, and Qdrant collection names (`clara_episodes`, `clara_memories`) are untouched.
- No deletion of the embedded Palace code yet (see Workstream 8).
- No monorepo. The peer-repo model from the charter stands.

## Target end-state

```
/Volumes/Storage/Code/mypalsystem/     ← thin meta-repo (glue files only)
├── README.md                          system map: aspects, ports, data flow, setup order
├── CLAUDE.md                          cross-repo guidance for agent sessions at root
├── justfile                           status / pull / test / dev across repos
├── mypal.code-workspace               VS Code multi-root
├── .gitignore                         ignores the four child repos
│
├── mypal-engine/                      pkg: mypal_engine  (renamed from mypalclara)
│   ├── mypal_engine/                  ambient, config, core, db, gateway, sandbox, services, tools, workspace
│   ├── mypal_protocol/                CANONICAL copy; published to PyPI as mypal-protocol
│   ├── personalities/                 removed (canonical home is mypalclara, see WS3)
│   ├── clara_core/                    deleted (dead)
│   └── .github/workflows/ci.yml       lint + tests (new; engine currently has none)
│
├── mypalclara/                        pkg: mypalclara  (experience layer, name unchanged)
│   ├── mypalclara/                    adapters, client_common, config, services/voice
│   ├── personas/                      renamed from personalities/; canonical home
│   ├── mypal_protocol/                deleted; replaced by dependency on mypal-protocol
│   ├── wiki/                          deleted (stale copy; canonical is mypalclara.wiki)
│   └── hooks/                         deleted (engine subsystem)
│
├── mypalace/                          unchanged structurally (already healthy)
│   ├── palace/                        deleted (empty leftover)
│   └── mypalace/cli/                  deleted (empty leftover)
│
└── mypalclara.wiki/                   rewritten as the system-facing docs map
```

## Workstreams

Each workstream is independently landable and separately revertible. Sequencing rationale is in the next section.

### WS1: Engine identity (mypal-engine)

Rename the engine's Python package `mypalclara` → `mypal_engine` and the Poetry project name to `mypal-engine`, so that "mypalclara" refers to exactly one thing in the system.

- Mechanical rename of the package dir plus all imports (261 py files, tests, scripts, `services/*/Dockerfile` run commands, alembic env).
- Delete `clara_core/` entirely; the live MCP code is `core/mcp/` inside the main package and nothing imports `clara_core`.
- Remove the three dead console scripts (`clara-discord`, `clara-teams`, `clara-cli`) that point at modules living in the client repo; keep `clara-gateway` retargeted to `mypal_engine.gateway.__main__:main`.
- Fix the `.gitignore` contradiction: drop the `mcp_servers/` ignore line (the first-party servers are tracked; the runtime clone dir `.mcp_servers/` is already separately ignored).
- Blast-radius notes: `package-mode = false` means nothing pip-installs the engine, so the rename is repo-internal. Env vars keep their `CLARA_*` names (identity of the product is Clara; identity of the package is the engine). Implementation must grep for pickle/module-path persistence before the rename lands (SQLAlchemy and Qdrant store no Python paths; anything pickling objects by module path would break and must be checked).
- Verification: full pytest (82 test files), `grep -r "import mypalclara\|from mypalclara"` returns zero hits engine-side, gateway boots, one adapter (CLI) registers against it.

### WS2: One contract (mypal_protocol)

Single-source the wire contract in the engine repo and turn compatibility from a hope into a check.

- Canonical home: `mypal-engine/mypal_protocol/`. The engine copy is already ahead (the `auth_token` field), the protocol evolves with the engine, and the package's own docstring says "published from the mypal-engine repo and consumed by both sides."
- Add `PROTOCOL_VERSION` (start at `"2"`) to the package and to `RegisterMessage`. During transition the engine accepts a missing version with a warning; enforcement (reject on mismatch) turns on after both sides ship.
- Publish `mypal-protocol` to PyPI via a tag-triggered workflow in the engine repo, cloned from mypalace's `release.yml` (PyPI trusted publishing is already set up as a pattern there). Version starts at 0.2.0.
- Client repo: delete the vendored `mypal_protocol/`, declare `mypal-protocol = "^0.2"` as a real dependency. Until the first PyPI publish lands, the client may pin a git dependency; after publish, switch to the PyPI pin.
- Move the canonical protocol round-trip tests engine-side with the package; the client keeps only an import-and-registration smoke test.
- TypeScript bindings remain charter item #1; this workstream only establishes the single Python source and the version handshake that #1 builds on.

### WS3: One home per shared artifact

Apply one rule everywhere: every shared artifact has exactly one editable home.

| Artifact | Canonical home | Consumers get it via | Action |
|---|---|---|---|
| `mypal_protocol` | engine repo | PyPI dependency | WS2 |
| Personas (`clara.md`, `clarissa.md`, `flo.md`) | mypalclara `personas/` (per charter) | engine: to be traced at implementation (existing config mechanism or a small `GET /api/v1/personas/{name}`); voice server reads the local canonical copy | rename `personalities/` → `personas/` in mypalclara; delete the engine copy once its actual reads are traced and rewired |
| `hooks/` example | engine repo | n/a (engine subsystem) | delete from client |
| Wiki content | mypalclara.wiki repo | GitHub wiki | delete `mypalclara/wiki/` |
| `docs/plans/` history | the repo where the work happened | n/a | delete the 10 duplicated engine-era plans from the client; engine keeps the archive |
| `migrating-mypalclara.md` | mypalace | link | engine keeps a one-line pointer, deletes its byte-identical copy |
| `VERSION` / CalVer | per-repo, independent | n/a | stop pretending lockstep; each repo bumps on its own cadence (they already do in practice) |

The personas row carries the one genuine design tension: the charter places persona definitions in mypalclara while stating "engine owns shared state." Resolution here: the *files* live in mypalclara (they are experience content), and the implementation plan's first task is to trace what the engine actually reads from its `personalities/` dir and rewire that one consumer (config path or API). No silent duplicate editing in any outcome.

### WS4: Documentation truth

Rewrite every surface to describe the post-split system, and give each repo the same orientation header.

- **Engine README + CLAUDE.md:** rewritten engine-first (run the gateway, connect adapters from the client repo, Palace service config, Railway deploy). All adapter/web-ui/Teams-setup content moves out or dies; the Teams Azure walkthrough belongs in the client repo, which owns the Teams adapter.
- **Client README:** cut the ~60% engine content; keep adapters, voice, web-ui (until charter #4 retires it), Teams manifest. CLAUDE.md is already accurate.
- **mypalace:** fix the README Architecture section (`palace/` → `mypalace/`), refresh the two version-stale docs (`migrating-mypalclara.md` says 0.6.0, `deployment.md` says 0.11.1, repo is at 0.12.0), add a CLAUDE.md (the only repo missing one).
- **Wiki (mypalclara.wiki):** rewrite as the system-facing map: Home = the three aspects, ports, message flow, setup order; Architecture = post-split diagram; subsystem pages either updated or replaced with pointers to the owning repo's docs. Principle going forward: docs live where the code lives; the wiki is the map, not a mirror.
- **Standard header:** every repo README opens with the same short "The MyPal System" block: three bullets and links to the sibling repos and the wiki.
- **Archive hygiene:** each repo's `docs/` gets a README index separating living reference docs from historical plans/specs, so the 55-file engine docs tree stops reading as current documentation.

### WS5: Workspace layer at the root

Create a thin meta-repo at `/Volumes/Storage/Code/mypalsystem` containing only glue (the four child repos are gitignored):

- `README.md`: the system map. What each aspect is, who talks to whom (adapters → engine WS :18789 / HTTP :18790; engine → mypalace :8000; engine → Qdrant/FalkorDB/Postgres), and the day-one setup order (palace, then engine, then adapters).
- `CLAUDE.md`: cross-repo context for agent sessions launched at the root: the naming map (repo → package → published name), boundary rules, and a "which repo do I change for X" table.
- `justfile`: `just status` (git status -sb across repos), `just pull`, `just test` (each repo's suite), `just dev` (compose up palace + engine + one adapter).
- `mypal.code-workspace` for VS Code multi-root.
- Local-only by default; pushing it to GitHub as a public map is optional and deferred.

### WS6: CI and boundary guards

- **Engine CI (new):** `.github/workflows/ci.yml` running ruff + pytest on push/PR. The most active repo currently has zero CI; this is the single highest-leverage guard in the whole plan.
- **Client boundary test (repair):** `tests/architecture/test_engine_boundary.py` currently iterates engine dirs that no longer exist and silently passes. Rewrite it to assert the inverse: (1) no file in the client package imports engine internals (`mypal_engine.*`), and (2) the engine-era dirs (`core/`, `db/`, `gateway/`, `sandbox/`...) do not reappear in the client package.
- **Protocol guard:** with WS2 done, drift prevention is structural (one source). The client's dependency pin plus the runtime version handshake replace any need for hash-diff checks.
- **Engine boundary test (existing, engine-side):** the engine already has its own `tests/architecture/test_engine_boundary.py` (no engine package may import a platform SDK; zero known violations). Keep it and make sure the new CI runs it, so the guard finally executes somewhere other than a developer's laptop.

### WS7: mypalace touch-ups

Small and independent: delete the empty `palace/` and `mypalace/cli/` leftover dirs, plus the README/doc fixes listed in WS4. No structural change; the repo is the system's healthiest.

### WS8: Palace end-state (scheduled, not executed)

Do not delete the embedded memory system in this reorg. Instead, make the migration state explicit:

- Engine README documents `USE_PALACE_SERVICE` as the supported mode and the embedded path as transitional.
- File engine issues for "Phase C: collapse `core/memory/` to a thin client" listing the known remote gaps (remote `history()` returns `[]`; remote store paths raise), and mypalace issues for closing those gaps.
- The 11.8k LOC comes out in its own future project once the gaps close and a soak period passes.

## Sequencing

1. **WS7** (mypalace hygiene): independent, minutes of work.
2. **WS5** (workspace layer): independent; immediately improves navigation and gives agent sessions a root context.
3. **WS6 engine CI**: lands *before* the rename so WS1 merges against a green pipeline.
4. **WS1** (engine rename): the biggest single change, protected by the new CI.
5. **WS2** (protocol single-sourcing): after WS1 so the published package is born with final names.
6. **WS3 + WS4** (dedupe + docs): after WS1/WS2 so docs describe final names; the wiki rewrite goes last.
7. **WS6 remainder** (client boundary test repair) can land any time; **WS8** items are filed, not built.

## Risk handling

- **The running assistant:** WS1 and WS2 are the only workstreams that touch runtime code. Both deploy engine-first; the protocol handshake is warn-only until both sides ship, so old adapters keep working throughout. Every workstream is a separate PR with a one-commit revert path.
- **Railway:** engine service Dockerfiles (`services/{gateway,backup,base}/`) reference package paths and get updated inside the WS1 change; deploy only after CI is green.
- **Pickle/module-path persistence:** checked explicitly before the WS1 rename lands (grep for `pickle`, `importlib` by dotted path, and any DB columns storing module paths).
- **Voice server persona read:** WS3 rewires `services/voice/server.py`'s read of `personalities/clara.md` to the renamed `personas/` path in the same change that renames the dir; the voice server lives in the same repo as the canonical copy, so no cross-repo fetch is needed for it.

## Verification

- Engine: full pytest; zero `mypalclara` imports remain; gateway boots; CLI adapter registers end-to-end; Railway build succeeds from the updated Dockerfiles.
- Client: pytest; repaired architecture tests fail-loud on regression; adapters run against a local engine.
- Protocol: round-trip tests live engine-side; client resolves `mypal-protocol` from the registry; handshake logs the version on register.
- Docs: every README's run commands are executed once as written before merge (the current engine README's commands are dead; that class of rot is the target).
- Workspace: `just status`, `just test`, `just dev` work from the root on a fresh terminal.

## Success criteria

1. `grep -r mypalclara` inside mypal-engine matches only historical docs.
2. Exactly one editable copy of the protocol, personas, hooks example, and wiki content exists across the workspace.
3. All three code repos have green CI; the engine has CI at all.
4. A newcomer (or an agent session at the root) can answer "which repo does X live in" from the root README alone.
5. Protocol compatibility is checked at adapter registration, not assumed.

## Decisions made (revisit only if you disagree)

- **Multi-repo stands; no monorepo.** Matches the charter's peer-client model and mypalace's independent PyPI/Docker release cadence.
- **Protocol canonical home is the engine repo, distributed via PyPI.** The engine copy is already ahead, and mypalace proves the publishing pattern works for this project.
- **Personas' canonical home is mypalclara (`personas/`),** per the charter's target structure; the engine's read path gets traced and rewired rather than keeping a second editable copy.
- **The wiki survives as the system map** rather than being retired into per-repo docs; it is the only shared, linkable docs surface the project has.
- **The workspace meta-repo is local-only for now**; publishing it is a later call.
- **Engine package name is `mypal_engine`** (matches the repo name); the product/persona name Clara stays in env vars, DB names, and prose.
