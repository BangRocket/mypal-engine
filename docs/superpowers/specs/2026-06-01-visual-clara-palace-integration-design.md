# Visual Clara → MyPalace Integration (via Clara Gateway)

**Date:** 2026-06-01
**Status:** Approved design — ready for implementation plan
**Repos involved:** `visual-clara` (client), `mypalclara` (gateway/server), `MyPalace` (memory service — no code changes, config only)

## Goal

Give `visual-clara` (a browser-only React visual-novel chat UI) Clara's persistent
Palace memory, so conversations there share one continuous memory with Discord Clara
and benefit from layered retrieval, reflection, and narrative synthesis — **without**
losing visual-clara's defining feature: the animated character driven by
`[pose:…]` / `[expression:…]` tags.

`visual-clara` does **not** talk to MyPalace directly. It talks to the Clara gateway,
and the gateway's existing routed memory layer (`USE_PALACE_SERVICE=true`) talks to
MyPalace. This reuses the same pipeline Discord and the Rails web-ui already use.

## Decisions (from brainstorming)

| # | Decision | Choice |
|---|----------|--------|
| 1 | Transport | **A** — repoint visual-clara's existing OpenAI-style SSE client at the gateway's `POST /v1/chat/completions` (port 18790) |
| 2 | Pose/expression layer | **(a)** — visual-clara owns the pose vocabulary and sends instructions; gateway injects them; tags stripped before storage |
| 3 | Identity / memory scope | **(a)** — single static `clara_xxx` key in visual-clara's `.env`, tied to the existing canonical user (shared memory with Discord Clara) |
| 4 | Pose-instruction injection mechanism | **(i)** — gateway honors `role:"system"` messages from the OpenAI `messages` array |

## Key facts that shaped this design

- The gateway endpoint `POST /v1/chat/completions`
  ([`mypalclara/gateway/api/chat.py`](../../../mypalclara/gateway/api/chat.py)) currently
  **ignores the client's system prompt and conversation history** — it extracts only the
  last `user` message and rebuilds the whole prompt server-side from Clara's persona +
  the server DB's history. So pose instructions must be injected deliberately, and
  localStorage history stops being authoritative.
- The bearer key resolves to a **canonical user → primary platform ID**, giving
  cross-platform memory continuity automatically (chat.py `_resolve_user`).
- **No pose/expression tag system exists server-side today.** This is the gap the design
  closes (client owns the vocabulary; server injects + strips).

## Architecture & data flow

```
visual-clara (browser, :5180)
   │  POST /v1/chat/completions  (SSE, Bearer clara_xxx)
   │  body: { model:"clara", stream:true,
   │          messages:[ {role:"system", content: POSE_INSTRUCTIONS},
   │                     {role:"user",   content: <text>} ] }
   ▼
Clara gateway HTTP API (:18790)
   │  _resolve_user(key) → canonical user
   │  build_prompt_layered(persona + Palace memory)  +  append POSE_INSTRUCTIONS system msg
   ▼
MemoryManager (routed, USE_PALACE_SERVICE=true)
   ▼
MyPalace service (:8000)  ── layered context in; episodes/memories out
   ▲
   │  LLM streams reply WITH [pose:…]/[expression:…] tags
   ▼
gateway streams OpenAI SSE back → visual-clara parses tags → sprites
   └─ gateway stores TAG-STRIPPED exchange + triggers reflection → MyPalace
```

`visual-clara` never reaches MyPalace directly. The live MyPalace link is the gateway
running in service-aware mode.

## Server changes (mypalclara) — confined to the API layer

All changes are in the gateway HTTP API; no change to `MemoryManager` or
`build_prompt_layered` signatures.

### 1. Honor + inject `system` messages — [`chat.py`](../../../mypalclara/gateway/api/chat.py)
- In `chat_completions()`, collect non-empty `role:"system"` strings from the request
  `messages` into `system_extra` (joined with `\n\n`).
- Thread `system_extra` into `_process_message` (signature gains a `system_extra: str = ""`
  param; passed through `_stream_response` / `_get_full_response`).
- After `build_prompt_layered(...)` returns `prompt_messages`, append
  `SystemMessage(content=system_extra)` when non-empty. This lands after persona + memory,
  before the user turn is converted via `messages_to_openai`.
- Trust boundary: the request is authenticated with the user's own `clara_` key; injecting
  their own system instructions is acceptable.

### 2. Strip pose tags before storage — `chat.py`
- New helper `strip_pose_tags(text: str) -> str` removing `\[(?:pose|expression):[^\]]*\]`
  (and collapsing the resulting double spaces). Lives in a small module, e.g.
  `mypalclara/gateway/api/_visual.py`, with its own unit tests.
- Apply to `full_response` **before** `store_message(..., "assistant", ...)`.
- Streamed chunks to the client are **not** stripped (the client needs the tags).
- Reflection reads the already-stripped DB copy, so it is covered with no extra change.

### 3. CORS — [`app.py`](../../../mypalclara/gateway/api/app.py)
- The existing `GATEWAY_API_CORS_ORIGINS` env var already drives the allowed list;
  extend its **default** to include `http://localhost:5180` and `http://127.0.0.1:5180`
  (visual-clara's dev port). No new var needed.

## Client changes (visual-clara)

### 1. New transport — `src/api/clara.ts`
- Mirrors [`kimi.ts`](../../../../visual-clara/visual-clara-app/src/api/kimi.ts)'s OpenAI
  SSE parsing, but targets the gateway and adds `Authorization: Bearer ${VITE_CLARA_API_KEY}`.
- `streamClaraChat({ userMessage, poseInstructions, onChunk, signal })`.
- Request body: `{ model: VITE_CLARA_MODEL ?? "clara", stream: true,
  messages: [ {role:"system", content: poseInstructions}, {role:"user", content: userMessage} ] }`.
- Env: `VITE_CLARA_API_URL` (e.g. `http://localhost:18790/v1`), `VITE_CLARA_API_KEY`,
  `VITE_CLARA_MODEL`.

### 2. Pose instructions — `src/lib/poseInstructions.ts`
- The trimmed tag-vocabulary block (28 expressions, 3 poses, aliases, exact tag format),
  extracted from today's `customPrompt` with the **persona** parts removed (persona is now
  Clara's job server-side). Single source of truth for what visual-clara sends.

### 3. `src/stores/chatStore.ts`
- Call `streamClaraChat` instead of `streamKimiChat`.
- Send only the system (pose) message + the latest user message — **do not** send full
  history (server owns it).
- localStorage becomes a **display cache only**; Clara's authoritative memory is server-side.

### 4. `src/components/ChatPanel.tsx`
- Repurpose the custom-prompt editor to edit the **pose-instruction block** (persona edits
  no longer take effect server-side). Persisted in localStorage as before.

### 5. `src/hooks/usePoseParser.ts`
- Unchanged — tags arrive in the stream exactly as before.

### 6. Backend switch
- `VITE_BACKEND` (`clara` default, `kimi` fallback) so Kimi-direct stays available for
  debugging/A-B. Keep existing `kimi.ts` in place behind this switch.

## Config / ops / prerequisites

1. **Gateway in service-aware mode** (the live MyPalace link): HTTP API on 18790 with
   `USE_PALACE_SERVICE=true`, `PALACE_SERVICE_URL=http://localhost:8000` (or
   `http://palace:8000` in Docker), `PALACE_SERVICE_API_KEY=pk_live_...`.
2. **Identity (refined during planning).** The local DB has **no `api_keys` table** — it is
   an identity-service feature, so `_resolve_user` falls back to the request body's `user`
   field. The runnable dev path therefore sets `user` = your platform-prefixed id (e.g.
   `discord-271274659385835521`) for shared memory with Discord Clara. The
   `Authorization: Bearer clara_xxx` key is still sent when configured and takes precedence,
   but is optional and only resolves once the identity service / `api_keys` table exists.
3. **visual-clara `.env`:** `VITE_BACKEND=clara`, `VITE_CLARA_API_URL`, `VITE_CLARA_MODEL=clara`,
   `VITE_CLARA_USER_ID` (shared-identity dev path), and optional `VITE_CLARA_API_KEY`.
4. **Gateway env:** `GATEWAY_API_CORS_ORIGINS` default extended to include visual-clara's origin.

## Error handling

- Gateway unreachable / 401 / 5xx → clear in-panel error, preserve the user's input, set a
  neutral pose. `VITE_BACKEND=kimi` is the manual fallback.
- SSE non-JSON lines and `[DONE]` ignored (existing parser behavior).
- Empty user message guarded client-side (existing behavior).

## Testing strategy (TDD)

**Server (pytest):**
- Unit-test `strip_pose_tags`: removes single/multiple/aliased tags, preserves surrounding
  text, collapses leftover whitespace.
- Endpoint test with a stubbed LLM stream + in-memory DB asserting: (a) the injected
  `system_extra` content reaches the built prompt, (b) the **stored** assistant message has
  no pose tags, (c) the **streamed** chunks retain tags.

**Client (vitest):**
- `streamClaraChat` builds the correct request: URL, `Authorization` header, `model`, and
  `messages` (system + user).
- Pose parser extracts `[pose]`/`[expression]` from a simulated gateway SSE stream and
  strips them from display text.

**Manual verify:**
- Run gateway (service-aware) + MyPalace + visual-clara. Confirm: (1) reply renders with
  pose/expression, (2) a follow-up recalls earlier context (memory works end-to-end),
  (3) MyPalace shows a stored episode after the reflection threshold.

## Out of scope (YAGNI)

- Multi-user login / per-user key minting (Q3 option c).
- WebSocket transport / native gateway protocol (Q1 option C) and tool-event streaming.
- Server-side "visual mode" persona coupling (Q2 option b).
- Migrating visual-clara's localStorage history into Palace (server is authoritative going
  forward; old local history is left as-is).
```
