# Visual Clara → MyPalace Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Route `visual-clara` through the Clara gateway's OpenAI-compatible `/v1/chat/completions` endpoint so it shares Clara's Palace memory, while preserving the `[pose:…]`/`[expression:…]` visual layer.

**Architecture:** `visual-clara` (browser) calls the gateway endpoint over SSE; the gateway builds Clara's persona + Palace memory server-side, appends client-supplied pose instructions (sent as `system` messages), streams the reply with pose tags, and stores a tag-stripped copy. The gateway's existing routed memory layer (`USE_PALACE_SERVICE=true`) is the live link to MyPalace — `visual-clara` never touches MyPalace directly.

**Tech Stack:** Python/FastAPI + pytest (server, `mypalclara` repo); React/TypeScript/Zustand + Vitest (client, `visual-clara` repo).

**Two repos, two branches:**
- Server changes → `mypalclara`, branch `visual-clara-palace-integration` (already created; the spec is committed here).
- Client changes → `visual-clara`, create branch `palace-integration` at the start of Phase B.

**Spec:** `docs/superpowers/specs/2026-06-01-visual-clara-palace-integration-design.md`

**Identity refinement discovered during planning:** mypalclara's local DB has **no `api_keys` table** (it is an identity-service feature). `_resolve_user` therefore falls back to the request body's `user` field. The runnable dev path sets identity via `user` = your Discord-prefixed id (e.g. `discord-271274659385835521`) for shared memory with Discord Clara. The `Authorization: Bearer clara_xxx` key is still sent when configured and takes precedence, but is optional and only resolves when the identity service / `api_keys` table exists.

---

## Phase A — Server (mypalclara repo)

All paths below are relative to `/Volumes/Storage/Code/mypalclara`. Confirm you are on branch `visual-clara-palace-integration` (`git branch --show-current`).

### Task 1: Pose-tag + system-message helpers (`_visual.py`)

**Files:**
- Create: `mypalclara/gateway/api/_visual.py`
- Test: `tests/gateway/api/test_visual.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/gateway/api/test_visual.py`:

```python
"""Unit tests for visual-clara helpers: pose-tag stripping + system message collection."""

from mypalclara.gateway.api._visual import collect_system_extra, strip_pose_tags


def test_strip_pose_tags_removes_expression_tag():
    assert strip_pose_tags("Hi [expression:happy] there") == "Hi there"


def test_strip_pose_tags_removes_pose_and_multiple_tags():
    assert strip_pose_tags("[pose:left]Hello [expression:surprised] world") == "Hello world"


def test_strip_pose_tags_no_tags_unchanged():
    assert strip_pose_tags("plain text, nothing to strip") == "plain text, nothing to strip"


def test_strip_pose_tags_preserves_newlines_and_trims_line_edges():
    assert strip_pose_tags("line1 [pose:center]\nline2") == "line1\nline2"


def test_strip_pose_tags_case_insensitive():
    assert strip_pose_tags("Yo [EXPRESSION:Happy] you") == "Yo you"


def test_strip_pose_tags_empty():
    assert strip_pose_tags("") == ""


def test_collect_system_extra_joins_system_messages():
    msgs = [
        {"role": "system", "content": "A"},
        {"role": "user", "content": "hi"},
        {"role": "system", "content": "B"},
    ]
    assert collect_system_extra(msgs) == "A\n\nB"


def test_collect_system_extra_ignores_empty_and_nonstring():
    msgs = [
        {"role": "system", "content": "  "},
        {"role": "system", "content": 123},
        {"role": "system", "content": "X"},
    ]
    assert collect_system_extra(msgs) == "X"


def test_collect_system_extra_empty_list():
    assert collect_system_extra([]) == ""
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `poetry run pytest tests/gateway/api/test_visual.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mypalclara.gateway.api._visual'`

- [ ] **Step 3: Write the implementation**

Create `mypalclara/gateway/api/_visual.py`:

```python
"""Helpers for the visual-clara client integration.

- strip_pose_tags: remove [pose:…]/[expression:…] tags before persisting a
  response, so Palace memory is not polluted with presentation directives.
  Streamed chunks to the client keep the tags; only the stored copy is cleaned.
- collect_system_extra: gather client-supplied system messages from an
  OpenAI-style request so they can be injected after Clara's persona.
"""

from __future__ import annotations

import re

_POSE_TAG_RE = re.compile(r"\[(?:pose|expression):[^\]]*\]", re.IGNORECASE)


def strip_pose_tags(text: str) -> str:
    """Remove pose/expression tags and tidy the whitespace they leave behind."""
    if not text:
        return text
    cleaned = _POSE_TAG_RE.sub("", text)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)   # collapse internal runs
    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)   # trim trailing spaces on a line
    cleaned = re.sub(r"\n[ \t]+", "\n", cleaned)   # trim leading spaces on a line
    return cleaned.strip()


def collect_system_extra(messages: list[dict]) -> str:
    """Join non-empty string `system` message contents with blank lines."""
    parts: list[str] = []
    for msg in messages or []:
        if msg.get("role") == "system":
            content = msg.get("content")
            if isinstance(content, str) and content.strip():
                parts.append(content.strip())
    return "\n\n".join(parts)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `poetry run pytest tests/gateway/api/test_visual.py -v`
Expected: PASS (9 passed)

- [ ] **Step 5: Lint**

Run: `poetry run ruff check mypalclara/gateway/api/_visual.py tests/gateway/api/test_visual.py && poetry run ruff format mypalclara/gateway/api/_visual.py tests/gateway/api/test_visual.py`
Expected: no errors

- [ ] **Step 6: Commit**

```bash
git add mypalclara/gateway/api/_visual.py tests/gateway/api/test_visual.py
git commit -m "feat(gateway): add pose-tag strip + system-message helpers for visual-clara"
```

---

### Task 2: Inject system messages + strip tags in the chat endpoint

**Files:**
- Modify: `mypalclara/gateway/api/chat.py`

This task wires the Task 1 helpers into the request path. The behavior is covered by Task 1's unit tests plus the Phase C manual verify; no new automated test is added here (a full endpoint test would require stubbing the DB, LLM, and Palace — high cost, low marginal value over the helper tests).

- [ ] **Step 1: Import the helpers**

In `mypalclara/gateway/api/chat.py`, add this import near the top (after the existing `from mypalclara.gateway.api.auth import get_db` line, around line 21):

```python
from mypalclara.gateway.api._visual import collect_system_extra, strip_pose_tags
```

- [ ] **Step 2: Collect system messages in `chat_completions` and pass them down**

In `chat_completions`, immediately after the line `user_id = await _resolve_user(request, body.get("user"))` (around line 157), add:

```python
    system_extra = collect_system_extra(messages)
```

Then update the two call sites in `chat_completions` to forward `system_extra`:

Replace:
```python
        return StreamingResponse(
            _stream_response(user_message, user_id, model, completion_id, messages),
```
with:
```python
        return StreamingResponse(
            _stream_response(user_message, user_id, model, completion_id, messages, system_extra),
```

Replace:
```python
        full_text = await _get_full_response(user_message, user_id, messages)
```
with:
```python
        full_text = await _get_full_response(user_message, user_id, messages, system_extra)
```

- [ ] **Step 3: Thread `system_extra` through `_stream_response` and `_get_full_response`**

Change the signature of `_stream_response` from:
```python
async def _stream_response(
    user_message: str,
    user_id: str,
    model: str,
    completion_id: str,
    messages: list[dict],
):
```
to:
```python
async def _stream_response(
    user_message: str,
    user_id: str,
    model: str,
    completion_id: str,
    messages: list[dict],
    system_extra: str = "",
):
```
and inside it change `async for chunk_text in _process_message(user_message, user_id, messages):` to:
```python
    async for chunk_text in _process_message(user_message, user_id, messages, system_extra):
```

Change the signature of `_get_full_response` from:
```python
async def _get_full_response(
    user_message: str,
    user_id: str,
    messages: list[dict],
) -> str:
```
to:
```python
async def _get_full_response(
    user_message: str,
    user_id: str,
    messages: list[dict],
    system_extra: str = "",
) -> str:
```
and inside it change `async for chunk in _process_message(user_message, user_id, messages):` to:
```python
    async for chunk in _process_message(user_message, user_id, messages, system_extra):
```

- [ ] **Step 4: Accept `system_extra` in `_process_message` and inject it into the prompt**

Change the signature of `_process_message` from:
```python
async def _process_message(
    user_message: str,
    user_id: str,
    messages: list[dict],
):
```
to:
```python
async def _process_message(
    user_message: str,
    user_id: str,
    messages: list[dict],
    system_extra: str = "",
):
```

Then, right after the `build_prompt_layered` try/except block (immediately before the line `# Convert to dict format for LLM`), add:

```python
    # Append client-supplied system instructions (e.g. visual-clara pose tags)
    # after the persona + memory layers, before the user turn is converted.
    if system_extra:
        from mypalclara.core.llm.messages import SystemMessage

        prompt_messages.append(SystemMessage(content=system_extra))
```

- [ ] **Step 5: Strip pose tags before storing the assistant message**

In `_process_message`, find the storage block:
```python
                mm._session_manager.store_message(store_db, db_session.id, user_id, "user", user_message)
                mm._session_manager.store_message(store_db, db_session.id, user_id, "assistant", full_response)
```
Replace the assistant line so the stored copy is cleaned (the user line is unchanged):
```python
                mm._session_manager.store_message(store_db, db_session.id, user_id, "user", user_message)
                mm._session_manager.store_message(
                    store_db, db_session.id, user_id, "assistant", strip_pose_tags(full_response)
                )
```

Reflection reads from the DB (the already-stripped copy), so it needs no change.

- [ ] **Step 6: Verify the file imports and existing gateway tests still pass**

Run: `poetry run python -c "import mypalclara.gateway.api.chat"`
Expected: no output (imports cleanly)

Run: `poetry run pytest tests/gateway -q`
Expected: PASS (no regressions)

- [ ] **Step 7: Lint**

Run: `poetry run ruff check mypalclara/gateway/api/chat.py && poetry run ruff format mypalclara/gateway/api/chat.py`
Expected: no errors

- [ ] **Step 8: Commit**

```bash
git add mypalclara/gateway/api/chat.py
git commit -m "feat(gateway): inject client system messages + strip pose tags on store"
```

---

### Task 3: Allow visual-clara's origin through CORS

**Files:**
- Modify: `mypalclara/gateway/api/app.py:32-35`

- [ ] **Step 1: Extend the default CORS origin list**

In `mypalclara/gateway/api/app.py`, replace:
```python
    cors_origins = os.getenv(
        "GATEWAY_API_CORS_ORIGINS",
        "http://localhost:3000,http://localhost:5173,http://localhost:1420,tauri://localhost,https://tauri.localhost"
    ).split(",")
```
with:
```python
    cors_origins = os.getenv(
        "GATEWAY_API_CORS_ORIGINS",
        "http://localhost:3000,http://localhost:5173,http://localhost:5180,"
        "http://127.0.0.1:5180,http://localhost:1420,tauri://localhost,https://tauri.localhost"
    ).split(",")
```

- [ ] **Step 2: Verify it imports**

Run: `poetry run python -c "from mypalclara.gateway.api.app import create_app; create_app()"`
Expected: no output (app builds)

- [ ] **Step 3: Lint**

Run: `poetry run ruff check mypalclara/gateway/api/app.py && poetry run ruff format mypalclara/gateway/api/app.py`
Expected: no errors

- [ ] **Step 4: Commit**

```bash
git add mypalclara/gateway/api/app.py
git commit -m "feat(gateway): allow visual-clara origin (port 5180) through CORS"
```

---

## Phase B — Client (visual-clara repo)

All paths below are relative to `/Volumes/Storage/Code/visual-clara/visual-clara-app`. Run client commands from that directory.

### Task 4: Create the client branch and add Vitest

**Files:**
- Modify: `package.json`

- [ ] **Step 1: Create the branch**

```bash
cd /Volumes/Storage/Code/visual-clara && git checkout -b palace-integration
```

- [ ] **Step 2: Add Vitest**

```bash
cd /Volumes/Storage/Code/visual-clara/visual-clara-app && pnpm add -D vitest
```

- [ ] **Step 3: Add the test script**

In `package.json`, change the `scripts` block from:
```json
  "scripts": {
    "dev": "vite",
    "build": "tsc && vite build",
    "preview": "vite preview"
  },
```
to:
```json
  "scripts": {
    "dev": "vite",
    "build": "tsc && vite build",
    "preview": "vite preview",
    "test": "vitest run"
  },
```

- [ ] **Step 4: Verify Vitest runs (no tests yet)**

Run: `pnpm test`
Expected: Vitest reports "No test files found" and exits 0 (or non-zero with that message — either is fine; it confirms vitest is installed and reads the vite config for the `@` alias).

- [ ] **Step 5: Commit**

```bash
cd /Volumes/Storage/Code/visual-clara && git add visual-clara-app/package.json visual-clara-app/pnpm-lock.yaml && git commit -m "chore: add vitest test runner"
```

---

### Task 5: Pose-instruction constant + pose-parser test

**Files:**
- Create: `src/lib/poseInstructions.ts`
- Create: `src/hooks/usePoseParser.test.ts`

- [ ] **Step 1: Write the failing pose-parser test**

Create `src/hooks/usePoseParser.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { parsePose } from "@/hooks/usePoseParser";

describe("parsePose", () => {
  it("extracts pose + expression and strips both tags", () => {
    const r = parsePose("Hi there [expression:happy][pose:left]");
    expect(r.expression).toBe("happy");
    expect(r.pose).toBe("left");
    expect(r.text).toBe("Hi there");
  });

  it("maps known aliases", () => {
    const r = parsePose("wow [expression:surprised]");
    expect(r.expression).toBe("surprise");
  });

  it("returns null pose/expression when no tags present", () => {
    const r = parsePose("just text");
    expect(r.pose).toBeNull();
    expect(r.expression).toBeNull();
    expect(r.text).toBe("just text");
  });
});
```

- [ ] **Step 2: Run it to verify it passes**

Run: `pnpm test`
Expected: PASS (3 passed). `parsePose` already exists, so this is a characterization test that locks in the behavior the gateway integration relies on.

- [ ] **Step 3: Create the pose-instruction constant**

Create `src/lib/poseInstructions.ts` (this is the "Visual State" block lifted out of the old `customPrompt`, with the persona text removed — persona is now Clara's job server-side):

```ts
/**
 * Pose/expression instructions sent to the Clara gateway as a `system` message.
 * Clara's persona + memory come from the server; this block only teaches her the
 * visual vocabulary this client can render. Tags are parsed and stripped client-side.
 */
export const DEFAULT_POSE_INSTRUCTIONS = `## Visual State
You can control your pose and expression using special tags:
- [pose:center] [pose:left] [pose:right] — change which way you're facing
- [expression:happy] [expression:sad] [expression:angry] [expression:neutral] [expression:blush] [expression:confident] [expression:confused] [expression:crying] [expression:disgusted] [expression:embarrassed] [expression:exhausted] [expression:fright] [expression:furious] [expression:kiss] [expression:mock] [expression:nauseating] [expression:psychotic] [expression:scared] [expression:serious] [expression:sleepy] [expression:smirk] [expression:sobbing] [expression:soulless] [expression:stoic] [expression:surprise] [expression:terror] [expression:tired] [expression:unease]

Use these tags naturally to match your emotional state. They will be stripped from the displayed message.`;
```

- [ ] **Step 4: Verify the constant compiles**

Run: `pnpm exec tsc --noEmit`
Expected: no errors

- [ ] **Step 5: Commit**

```bash
cd /Volumes/Storage/Code/visual-clara && git add visual-clara-app/src/lib/poseInstructions.ts visual-clara-app/src/hooks/usePoseParser.test.ts && git commit -m "feat: pose-instruction constant + pose-parser characterization test"
```

---

### Task 6: Clara gateway transport (`clara.ts`)

**Files:**
- Create: `src/api/clara.ts`
- Create: `src/api/clara.test.ts`

- [ ] **Step 1: Write the failing transport test**

Create `src/api/clara.test.ts`:

```ts
import { afterEach, describe, expect, it, vi } from "vitest";
import { streamClaraChat } from "@/api/clara";

afterEach(() => {
  vi.restoreAllMocks();
});

function sseResponse(body: string) {
  const encoder = new TextEncoder();
  return {
    ok: true,
    body: new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(encoder.encode(body));
        controller.close();
      },
    }),
  } as unknown as Response;
}

describe("streamClaraChat", () => {
  it("posts to the gateway with the system+user messages and streams chunks (tags retained)", async () => {
    let captured: { url: string; init: RequestInit } | null = null;
    const sse =
      'data: {"choices":[{"delta":{"content":"Hi [expression:happy]"}}]}\n\n' +
      'data: {"choices":[{"delta":{"content":" there"}}]}\n\n' +
      "data: [DONE]\n\n";

    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init: RequestInit) => {
        captured = { url, init };
        return sseResponse(sse);
      })
    );

    const chunks: string[] = [];
    let done = "";
    await streamClaraChat(
      [
        { role: "system", content: "POSE" },
        { role: "user", content: "hello" },
      ],
      {
        onStart: () => {},
        onChunk: (delta) => chunks.push(delta),
        onDone: (full) => {
          done = full;
        },
        onError: (e) => {
          throw new Error(e);
        },
      }
    );

    expect(captured).not.toBeNull();
    expect(captured!.url).toContain("/chat/completions");
    const headers = captured!.init.headers as Record<string, string>;
    expect(headers.Authorization === undefined || headers.Authorization.startsWith("Bearer ")).toBe(true);
    const body = JSON.parse(captured!.init.body as string);
    expect(body.stream).toBe(true);
    expect(body.messages[0]).toEqual({ role: "system", content: "POSE" });
    expect(body.messages[1]).toEqual({ role: "user", content: "hello" });
    // tags must survive the stream so the client can parse them
    expect(done).toBe("Hi [expression:happy] there");
    expect(chunks.join("")).toContain("[expression:happy]");
  });

  it("reports an error when the response is not ok", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({ ok: false, status: 500, json: async () => ({}) }) as unknown as Response)
    );
    let err = "";
    await streamClaraChat([{ role: "user", content: "x" }], {
      onStart: () => {},
      onChunk: () => {},
      onDone: () => {},
      onError: (e) => {
        err = e;
      },
    });
    expect(err).toContain("500");
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pnpm test`
Expected: FAIL — cannot resolve `@/api/clara` (module does not exist yet).

- [ ] **Step 3: Write the transport**

Create `src/api/clara.ts`:

```ts
import type { KimiMessage, KimiStreamCallbacks } from "@/api/kimi";

const CLARA_BASE_URL = import.meta.env.VITE_CLARA_API_URL || "http://localhost:18790/v1";
const CLARA_API_KEY = import.meta.env.VITE_CLARA_API_KEY || "";
const CLARA_USER_ID = import.meta.env.VITE_CLARA_USER_ID || "";

/**
 * Stream a chat completion from the Clara gateway (OpenAI-compatible SSE).
 * Mirrors streamKimiChat's interface so the store can switch transports freely.
 * Identity: the gateway prefers the Bearer key when valid; otherwise it uses the
 * `user` field (set to your platform-prefixed id for shared memory).
 */
export async function streamClaraChat(
  messages: KimiMessage[],
  callbacks: KimiStreamCallbacks,
  signal?: AbortSignal
) {
  const url = `${CLARA_BASE_URL}/chat/completions`;
  const model = import.meta.env.VITE_CLARA_MODEL || "clara";

  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (CLARA_API_KEY) {
    headers.Authorization = `Bearer ${CLARA_API_KEY}`;
  }

  const body: Record<string, unknown> = { model, messages, stream: true };
  if (CLARA_USER_ID) {
    body.user = CLARA_USER_ID;
  }

  try {
    const res = await fetch(url, {
      method: "POST",
      headers,
      body: JSON.stringify(body),
      signal,
    });

    if (!res.ok) {
      const errBody = await res.json().catch(() => ({}));
      const msg = errBody.error?.message || `Clara gateway error ${res.status}`;
      callbacks.onError(msg);
      return;
    }

    const reader = res.body?.getReader();
    if (!reader) {
      callbacks.onError("No response body");
      return;
    }

    callbacks.onStart();

    const decoder = new TextDecoder("utf-8");
    let buffer = "";
    let fullText = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() || "";

      for (const line of lines) {
        const trimmed = line.trim();
        if (!trimmed || !trimmed.startsWith("data: ")) continue;
        const jsonStr = trimmed.slice(6);
        if (jsonStr === "[DONE]") continue;

        try {
          const data = JSON.parse(jsonStr);
          const delta = data.choices?.[0]?.delta?.content;
          if (typeof delta === "string") {
            fullText += delta;
            callbacks.onChunk(delta, fullText);
          }
        } catch {
          // ignore malformed JSON
        }
      }
    }

    callbacks.onDone(fullText);
  } catch (err: unknown) {
    if (err instanceof Error && err.name === "AbortError") {
      return;
    }
    callbacks.onError(err instanceof Error ? err.message : String(err));
  }
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `pnpm test`
Expected: PASS (all `clara.test.ts` + `usePoseParser.test.ts` tests green)

- [ ] **Step 5: Typecheck**

Run: `pnpm exec tsc --noEmit`
Expected: no errors

- [ ] **Step 6: Commit**

```bash
cd /Volumes/Storage/Code/visual-clara && git add visual-clara-app/src/api/clara.ts visual-clara-app/src/api/clara.test.ts && git commit -m "feat: Clara gateway SSE transport"
```

---

### Task 7: Wire the store to the Clara transport

**Files:**
- Modify: `src/stores/chatStore.ts`

This replaces the Kimi-only path with a backend switch (`VITE_BACKEND`, default `clara`), renames the persona-prompt field to a pose-instruction field, and sends only the system (pose) message + the latest user message on the Clara path (the server owns history).

- [ ] **Step 1: Replace the file contents**

Replace the entire contents of `src/stores/chatStore.ts` with:

```ts
import { create } from "zustand";
import { parsePose, type Pose, type Expression } from "@/hooks/usePoseParser";
import { streamKimiChat, type KimiMessage, type KimiStreamCallbacks } from "@/api/kimi";
import { streamClaraChat } from "@/api/clara";
import { DEFAULT_POSE_INSTRUCTIONS } from "@/lib/poseInstructions";

export interface StreamMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  displayContent: string;
  streaming: boolean;
}

export type ModelTier = "low" | "mid" | "high";

interface ChatStore {
  messages: StreamMessage[];
  connected: boolean;
  streaming: boolean;
  activeRequestId: string | null;
  selectedTier: ModelTier;
  currentPose: Pose;
  currentExpression: Expression;
  targetExpression: Expression;
  isTalking: boolean;
  poseInstructions: string;
  abortController?: AbortController;

  connect: () => void;
  disconnect: () => void;
  sendMessage: (content: string, tier?: ModelTier) => void;
  cancel: () => void;
  clearConversation: () => void;
  setTier: (tier: ModelTier) => void;
  setPoseInstructions: (instructions: string) => void;
}

let msgCounter = 0;
const CONVERSATION_STORAGE_KEY = "visual_clara_conversation";
const POSE_INSTRUCTIONS_STORAGE_KEY = "visual_clara_pose_instructions";

function loadStoredMessages(): StreamMessage[] {
  try {
    const raw = localStorage.getItem(CONVERSATION_STORAGE_KEY);
    if (!raw) return [];

    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];

    return parsed
      .filter((msg): msg is StreamMessage => {
        return (
          msg &&
          (msg.role === "user" || msg.role === "assistant") &&
          typeof msg.id === "string" &&
          typeof msg.content === "string" &&
          typeof msg.displayContent === "string"
        );
      })
      .map((msg) => ({ ...msg, streaming: false }));
  } catch {
    return [];
  }
}

function saveMessages(messages: StreamMessage[]) {
  const stableMessages = messages.map((msg) => ({ ...msg, streaming: false }));
  localStorage.setItem(CONVERSATION_STORAGE_KEY, JSON.stringify(stableMessages));
}

export const useChatStore = create<ChatStore>((set, get) => {
  const storedMessages = loadStoredMessages();
  const lastCount = storedMessages
    .map((msg) => Number(msg.id.split("-")[1]))
    .filter((value) => Number.isFinite(value))
    .reduce((max, value) => Math.max(max, value), 0);
  msgCounter = Math.max(msgCounter, lastCount);

  return {
    messages: storedMessages,
    connected: false,
    streaming: false,
    activeRequestId: null,
    selectedTier: "mid",
    currentPose: "center",
    currentExpression: "neutral",
    targetExpression: "neutral",
    isTalking: false,
    poseInstructions:
      localStorage.getItem(POSE_INSTRUCTIONS_STORAGE_KEY) || DEFAULT_POSE_INSTRUCTIONS,

    connect: () => {
      set({ connected: true });
    },

    disconnect: () => {
      const ctrl = get().abortController;
      if (ctrl) {
        ctrl.abort();
      }
      set({ connected: false, streaming: false, isTalking: false });
    },

    sendMessage: (content: string, _tier?: ModelTier) => {
      const { messages, poseInstructions } = get();

      const userMsg: StreamMessage = {
        id: `u-${++msgCounter}`,
        role: "user",
        content,
        displayContent: content,
        streaming: false,
      };
      const nextMessages = [...messages, userMsg];
      saveMessages(nextMessages);
      set({ messages: nextMessages });

      const backend = import.meta.env.VITE_BACKEND || "clara";
      const sys = poseInstructions.trim();

      const callbacks: KimiStreamCallbacks = {
        onStart: () => {
          const assistantMsg: StreamMessage = {
            id: `a-${++msgCounter}`,
            role: "assistant",
            content: "",
            displayContent: "",
            streaming: true,
          };
          set((s) => ({
            messages: [...s.messages, assistantMsg],
            isTalking: false,
          }));
        },
        onChunk: (_delta, accumulated) => {
          const currentMessages = get().messages;
          const last = currentMessages[currentMessages.length - 1];
          if (last?.role === "assistant" && last.streaming) {
            const parsed = parsePose(accumulated);
            const hasVisibleText = parsed.text.trim().length > 0;
            const updated = [
              ...currentMessages.slice(0, -1),
              { ...last, content: accumulated, displayContent: parsed.text },
            ];
            saveMessages(updated);
            set((s) => ({
              messages: updated,
              currentPose: parsed.pose ?? s.currentPose,
              targetExpression: parsed.expression ?? s.targetExpression,
              isTalking: hasVisibleText,
            }));
          }
        },
        onDone: (fullText) => {
          const currentMessages = get().messages;
          const last = currentMessages[currentMessages.length - 1];
          if (last?.role === "assistant") {
            const parsed = parsePose(fullText);
            const updated = [
              ...currentMessages.slice(0, -1),
              { ...last, content: fullText, displayContent: parsed.text, streaming: false },
            ];
            saveMessages(updated);
            set((s) => ({
              messages: updated,
              streaming: false,
              isTalking: false,
              activeRequestId: null,
              currentPose: parsed.pose ?? s.currentPose,
              targetExpression: parsed.expression ?? s.targetExpression,
            }));
          }
        },
        onError: (err) => {
          const currentMessages = get().messages;
          const errorMsg: StreamMessage = {
            id: `e-${++msgCounter}`,
            role: "assistant",
            content: `Error: ${err}`,
            displayContent: `Error: ${err}`,
            streaming: false,
          };
          const updated = [...currentMessages, errorMsg];
          saveMessages(updated);
          set(() => ({
            messages: updated,
            streaming: false,
            isTalking: false,
            activeRequestId: null,
            currentPose: "center",
            targetExpression: "neutral",
          }));
        },
      };

      const abortController = new AbortController();
      console.info("[Visual Clara State] thinking started", { transport: backend });
      set({
        streaming: true,
        activeRequestId: `${backend}-${Date.now()}`,
        abortController,
        targetExpression: "neutral",
      });

      if (backend === "kimi") {
        // Kimi-direct fallback: send the full local history (no server memory).
        const msgs: KimiMessage[] = [];
        if (sys) msgs.push({ role: "system", content: sys });
        for (const msg of messages) {
          msgs.push({ role: msg.role, content: msg.content });
        }
        msgs.push({ role: "user", content });
        streamKimiChat(msgs, callbacks, abortController.signal);
      } else {
        // Clara gateway: server owns history + persona + Palace memory.
        const msgs: KimiMessage[] = [];
        if (sys) msgs.push({ role: "system", content: sys });
        msgs.push({ role: "user", content });
        streamClaraChat(msgs, callbacks, abortController.signal);
      }
    },

    cancel: () => {
      const ctrl = get().abortController;
      ctrl?.abort();
      set((state) => {
        const last = state.messages[state.messages.length - 1];
        const nextMessages =
          last?.role === "assistant" && last.streaming
            ? [...state.messages.slice(0, -1), { ...last, streaming: false }]
            : state.messages;
        saveMessages(nextMessages);
        return {
          streaming: false,
          isTalking: false,
          activeRequestId: null,
          messages: nextMessages,
        };
      });
    },

    clearConversation: () => {
      localStorage.removeItem(CONVERSATION_STORAGE_KEY);
      set({ messages: [] });
    },

    setTier: (tier: ModelTier) => set({ selectedTier: tier }),
    setPoseInstructions: (instructions: string) => {
      localStorage.setItem(POSE_INSTRUCTIONS_STORAGE_KEY, instructions);
      set({ poseInstructions: instructions });
    },
  };
});
```

- [ ] **Step 2: Typecheck**

Run: `pnpm exec tsc --noEmit`
Expected: FAIL — `ChatPanel.tsx` still references `customPrompt`/`setCustomPrompt`. That is fixed in Task 8. (Do not commit yet.)

- [ ] **Step 3: Run unit tests**

Run: `pnpm test`
Expected: PASS (store changes do not affect the existing tests)

---

### Task 8: Repurpose the prompt editor in ChatPanel

**Files:**
- Modify: `src/components/ChatPanel.tsx:14-15,123,125-127`

- [ ] **Step 1: Update the store hooks**

In `src/components/ChatPanel.tsx`, replace:
```tsx
  const customPrompt = useChatStore((s) => s.customPrompt);
  const setCustomPrompt = useChatStore((s) => s.setCustomPrompt);
```
with:
```tsx
  const poseInstructions = useChatStore((s) => s.poseInstructions);
  const setPoseInstructions = useChatStore((s) => s.setPoseInstructions);
```

- [ ] **Step 2: Update the label**

Replace:
```tsx
          <div style={{ fontSize: 10, color: "#9ca3af", marginBottom: 4 }}>System prompt</div>
```
with:
```tsx
          <div style={{ fontSize: 10, color: "#9ca3af", marginBottom: 4 }}>Visual / pose instructions</div>
```

- [ ] **Step 3: Update the textarea binding**

Replace:
```tsx
            value={customPrompt}
            onChange={(e) => setCustomPrompt(e.target.value)}
            placeholder="Enter a custom system prompt..."
```
with:
```tsx
            value={poseInstructions}
            onChange={(e) => setPoseInstructions(e.target.value)}
            placeholder="Pose/expression instructions sent to Clara..."
```

- [ ] **Step 4: Typecheck**

Run: `pnpm exec tsc --noEmit`
Expected: no errors (Task 7 + Task 8 together resolve the rename)

- [ ] **Step 5: Build to confirm**

Run: `pnpm build`
Expected: build succeeds

- [ ] **Step 6: Commit**

```bash
cd /Volumes/Storage/Code/visual-clara && git add visual-clara-app/src/stores/chatStore.ts visual-clara-app/src/components/ChatPanel.tsx && git commit -m "feat: route chat through Clara gateway with pose-instruction editor"
```

---

### Task 9: Document the client env vars

**Files:**
- Modify: `.env.example`

- [ ] **Step 1: Append the Clara gateway vars**

Append to `src/../.env.example` (i.e. `visual-clara-app/.env.example`), after the existing Kimi block:

```bash

# Backend selection: "clara" (gateway, default) or "kimi" (direct, no memory)
VITE_BACKEND=clara

# Clara gateway (OpenAI-compatible). Routes through Clara's memory + persona.
VITE_CLARA_API_URL=http://localhost:18790/v1
VITE_CLARA_MODEL=clara
# Identity for shared memory with Discord Clara. Set to your platform-prefixed id,
# e.g. discord-271274659385835521. Used as the request `user` field.
VITE_CLARA_USER_ID=
# Optional bearer key (only resolves when the identity service / api_keys table exists).
VITE_CLARA_API_KEY=
```

- [ ] **Step 2: Commit**

```bash
cd /Volumes/Storage/Code/visual-clara && git add visual-clara-app/.env.example && git commit -m "docs: document Clara gateway env vars"
```

---

## Phase C — Config & end-to-end verification

### Task 10: Run the full stack and verify memory + visuals

No code changes — this confirms the whole chain works. Do not check the boxes until each command's output matches.

- [ ] **Step 1: Start MyPalace**

```bash
cd /Volumes/Storage/Code/MyPalace && docker-compose up -d
```
Verify: `curl -s http://localhost:8000/health` returns a healthy status.

- [ ] **Step 2: Start the Clara gateway in service-aware mode**

From `/Volumes/Storage/Code/mypalclara`, with these env vars set (in your `.env` or shell):
```bash
USE_PALACE_SERVICE=true
PALACE_SERVICE_URL=http://localhost:8000
PALACE_SERVICE_API_KEY=pk_live_...    # a key minted from MyPalace admin (or unset if PALACE_AUTH_DISABLED=true on MyPalace)
```
Run: `poetry run python -m mypalclara.gateway start`
Verify: `curl -s http://localhost:18790/api/v1/health` returns `{"status":"ok","service":"clara-gateway-api"}`, and the startup log shows `Routed memory: REMOTE Palace at http://localhost:8000`.

- [ ] **Step 3: Configure and start visual-clara**

In `/Volumes/Storage/Code/visual-clara/visual-clara-app/.env`, set:
```bash
VITE_BACKEND=clara
VITE_CLARA_API_URL=http://localhost:18790/v1
VITE_CLARA_MODEL=clara
VITE_CLARA_USER_ID=discord-<your-discord-id>
```
Run: `cd /Volumes/Storage/Code/visual-clara/visual-clara-app && pnpm dev`
Open: http://localhost:5180

- [ ] **Step 4: Verify visual rendering**

Send a message likely to trigger emotion (e.g. "Clara, tell me something that excites you"). Confirm the character sprite changes pose/expression and the displayed text has no `[pose:…]`/`[expression:…]` tags.

- [ ] **Step 5: Verify memory continuity**

Tell Clara a specific fact ("My favorite color is teal"), then in a follow-up ask "what's my favorite color?". Confirm she recalls it — proving the request reached Palace context. If you used a `VITE_CLARA_USER_ID` matching your Discord identity, also confirm she can reference something previously discussed on Discord.

- [ ] **Step 6: Verify storage is tag-stripped + episodes land in MyPalace**

After at least `REFLECTION_THRESHOLD` (default 5) exchanges, confirm an episode/memory was written to MyPalace:
```bash
cd /Volumes/Storage/Code/MyPalace && pip install -q "mypalace-client[cli]" 2>/dev/null; MYPALACE_URL=http://localhost:8000 MYPALACE_ADMIN_KEY=pk_live_... mypalace-admin stats <tenant-or-default>
```
(or query the MyPalace Postgres `messages`/episodes directly). Confirm stored assistant text contains **no** pose tags.

- [ ] **Step 7: Record the result**

If all checks pass, the integration is complete. Note any deviations.

---

## Self-Review

**Spec coverage:**
- Honor system messages → Tasks 1, 2. ✓
- Inject after persona/memory → Task 2 Step 4. ✓
- Strip pose tags before storage → Tasks 1, 2 Step 5. ✓
- CORS for port 5180 → Task 3 (using the real `GATEWAY_API_CORS_ORIGINS` var). ✓
- `clara.ts` transport (Bearer + identity) → Task 6. ✓
- `poseInstructions.ts` → Task 5. ✓
- chatStore wired, minimal payload, localStorage as display cache → Task 7. ✓
- ChatPanel editor repurposed → Task 8. ✓
- `VITE_BACKEND` switch → Tasks 7, 9. ✓
- usePoseParser unchanged (characterized by a test) → Task 5. ✓
- Error handling (neutral pose on error) → Task 7 onError. ✓
- Tests: strip_pose_tags + collect_system_extra (server), clara.ts request + pose parser (client) → Tasks 1, 5, 6. ✓
- Manual verify (visuals, memory, stored-tag-stripping) → Task 10. ✓
- Config/prereqs (service-aware gateway, identity) → Task 10 + plan header refinement. ✓

**Deviation from spec (intentional, documented in header):** the `clara_` key mint is replaced by the request `user` field as the dev default, because the local DB has no `api_keys` table. Same outcome (shared identity), runnable without the identity service.

**Placeholder scan:** no TBD/TODO; every code step has complete code; every command has expected output.

**Type/name consistency:** `poseInstructions`/`setPoseInstructions` and `POSE_INSTRUCTIONS_STORAGE_KEY` used consistently across Tasks 5/7/8; `DEFAULT_POSE_INSTRUCTIONS` defined in Task 5 and imported in Task 7; `streamClaraChat(messages, callbacks, signal)` signature matches `streamKimiChat`; `collect_system_extra`/`strip_pose_tags` names match across Tasks 1/2; `system_extra` threaded consistently through `_stream_response`/`_get_full_response`/`_process_message`.
