# Discord Importer — Palace-Service-Aware Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `scripts/import_discord_chats.py` write to the Palace service (synchronous reflection, per-channel persona `agent_id`) when `USE_PALACE_SERVICE=true`, leaving the embedded path unchanged.

**Architecture:** All changes confined to `scripts/import_discord_chats.py` + new unit tests. The service write path calls `PALACE.client.reflect_session(..., mode="sync")` directly via the async bridge (the sanctioned `RemotePalace.client` escape hatch); the embedded path keeps calling `mm.reflect_on_session`. Three small pure/dispatch helpers (`resolve_channel_agent`, `import_session`, `maybe_reset`) are extracted so behavior is unit-testable without a live server.

**Tech Stack:** Python 3.12, pytest, `unittest.mock`, Poetry. Run tests with the installed Poetry venv.

**Spec:** `docs/plans/2026-05-31-discord-importer-service-aware-design.md`

---

## File Structure

- **Modify** `scripts/import_discord_chats.py`:
  - Add constant `CLARA_BOT_AGENT`.
  - Add `resolve_channel_agent(messages, default_agent)` — pick `agent_id` from a channel's dominant Clara-variant bot.
  - Add `import_session(session, *, user_id, agent_id, channel, mm, palace, use_service)` — per-session reflection dispatch (service vs embedded).
  - Extract the existing `--reset` block into `reset_embedded_memory(mm, palace)`; add `maybe_reset(do_reset, use_service, mm, palace)`.
  - Rewire `main()`: import routed `PALACE`/`USE_PALACE_SERVICE`, call `maybe_reset`, resolve per-file agent, first-session smoke-abort in service mode, call `import_session` per session.
- **Create** `tests/scripts/test_import_discord_chats.py` — unit tests for the helpers (no live server).

Poetry invocation used throughout (Poetry is not on PATH):
```
POETRY="/Users/heidornj/Library/Application Support/pypoetry/venv/bin/poetry"
```

---

## Task 1: Per-channel agent resolution

**Files:**
- Modify: `scripts/import_discord_chats.py` (add near `CLARA_BOTS`, line ~53)
- Test: `tests/scripts/test_import_discord_chats.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/scripts/test_import_discord_chats.py`:
```python
"""Unit tests for the service-aware Discord importer."""

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import import_discord_chats as importer  # noqa: E402


def _msg(author_name, *, is_clara, content="hi", ts="2025-12-20T12:00:00"):
    return {
        "content": content,
        "author_name": author_name,
        "author_id": f"id-{author_name}",
        "canonical_name": author_name,
        "is_bot": is_clara,
        "is_clara": is_clara,
        "role": "assistant" if is_clara else "user",
        "timestamp": ts,
        "message_id": "m1",
    }


def test_resolve_channel_agent_clara_dm():
    msgs = [_msg("Josh", is_clara=False), _msg("MyPalClara", is_clara=True)]
    assert importer.resolve_channel_agent(msgs, default_agent="x") == "clara"


def test_resolve_channel_agent_clarissa():
    msgs = [_msg("Josh", is_clara=False), _msg("MyPalClarissa", is_clara=True)]
    assert importer.resolve_channel_agent(msgs, default_agent="x") == "clarissa"


def test_resolve_channel_agent_dominant_wins():
    msgs = [_msg("MyPalClarissa", is_clara=True)] * 3 + [_msg("MyPalClara", is_clara=True)]
    assert importer.resolve_channel_agent(msgs, default_agent="x") == "clarissa"


def test_resolve_channel_agent_fallback_when_no_bot():
    msgs = [_msg("Josh", is_clara=False), _msg("cadacious", is_clara=False)]
    assert importer.resolve_channel_agent(msgs, default_agent="fallback") == "fallback"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `"$POETRY" run pytest tests/scripts/test_import_discord_chats.py -v`
Expected: FAIL — `AttributeError: module 'import_discord_chats' has no attribute 'resolve_channel_agent'`

- [ ] **Step 3: Write minimal implementation**

In `scripts/import_discord_chats.py`, after `CLARA_BOTS = {"MyPalClara", "MyPalClarissa"}` (line ~53):
```python
# Maps a Clara-variant bot name to the Palace agent_id its conversations
# should be attributed to (per-channel persona separation).
CLARA_BOT_AGENT = {
    "MyPalClara": "clara",
    "MyPalClarissa": "clarissa",
    "MyPalFlorence": "florence",
}


def resolve_channel_agent(messages: list[dict], default_agent: str) -> str:
    """Pick the agent_id for a channel from its dominant Clara-variant bot.

    Counts messages authored by Clara-variant bots (is_clara=True) and maps the
    most frequent author via CLARA_BOT_AGENT. Falls back to default_agent when
    the channel has no Clara-variant bot or the bot is unmapped.
    """
    from collections import Counter

    counts = Counter(m["author_name"] for m in messages if m.get("is_clara"))
    if not counts:
        return default_agent
    dominant = counts.most_common(1)[0][0]
    return CLARA_BOT_AGENT.get(dominant, default_agent)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `"$POETRY" run pytest tests/scripts/test_import_discord_chats.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add scripts/import_discord_chats.py tests/scripts/test_import_discord_chats.py
git commit -m "feat(import): per-channel persona agent_id resolution"
```

---

## Task 2: Per-session reflection dispatch (`import_session`)

**Files:**
- Modify: `scripts/import_discord_chats.py` (add after `format_session_for_reflection`, line ~184)
- Test: `tests/scripts/test_import_discord_chats.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/scripts/test_import_discord_chats.py`:
```python
from unittest.mock import MagicMock  # noqa: E402


def _session():
    return [_msg("Josh", is_clara=False, content="hello"),
            _msg("MyPalClara", is_clara=True, content="hi Josh")]


def test_import_session_service_uses_sync_and_agent():
    session = _session()
    palace = MagicMock()
    palace.bridge.submit.return_value = ["ep1", "ep2"]   # stand-in episodes
    mm = MagicMock()

    result = importer.import_session(
        session, user_id="discord-1", agent_id="clarissa",
        channel="ask-clarissa", mm=mm, palace=palace, use_service=True,
    )

    # client.reflect_session called once with mode=sync + the per-channel agent
    palace.client.reflect_session.assert_called_once_with(
        messages=importer.format_session_for_reflection(session),
        user_id="discord-1",
        agent_id="clarissa",
        session_id="ask-clarissa",
        mode="sync",
    )
    # the coroutine was dispatched on the bridge
    palace.bridge.submit.assert_called_once()
    assert result == {"episodes": ["ep1", "ep2"]}
    # embedded path must NOT be used in service mode
    mm.reflect_on_session.assert_not_called()


def test_import_session_embedded_uses_manager():
    session = _session()
    mm = MagicMock()
    mm.reflect_on_session.return_value = {"episodes": [1], "entities": [], "self_notes": []}
    palace = MagicMock()

    result = importer.import_session(
        session, user_id="discord-1", agent_id="clara",
        channel="MyPalClara", mm=mm, palace=palace, use_service=False,
    )

    mm.reflect_on_session.assert_called_once_with(
        importer.format_session_for_reflection(session),
        "discord-1",
        session_id="MyPalClara",
    )
    assert result == {"episodes": [1], "entities": [], "self_notes": []}
    palace.client.reflect_session.assert_not_called()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `"$POETRY" run pytest tests/scripts/test_import_discord_chats.py -k import_session -v`
Expected: FAIL — `AttributeError: module 'import_discord_chats' has no attribute 'import_session'`

- [ ] **Step 3: Write minimal implementation**

In `scripts/import_discord_chats.py`, after `format_session_for_reflection` (line ~184):
```python
def import_session(
    session: list[dict],
    *,
    user_id: str,
    agent_id: str,
    channel: str,
    mm,
    palace,
    use_service: bool,
) -> dict:
    """Reflect one session into memory; return a result dict for logging.

    Service mode: synchronous server-side reflection via the Palace client,
    attributed to agent_id. Returns {"episodes": [...]} (the stored episodes).
    Embedded mode: delegates to MemoryManager.reflect_on_session (unchanged),
    returning its reflection dict.
    """
    msg_dicts = format_session_for_reflection(session)
    if use_service:
        episodes = palace.bridge.submit(
            palace.client.reflect_session(
                messages=msg_dicts,
                user_id=user_id,
                agent_id=agent_id,
                session_id=channel,
                mode="sync",
            )
        )
        return {"episodes": episodes or []}
    return mm.reflect_on_session(msg_dicts, user_id, session_id=channel) or {}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `"$POETRY" run pytest tests/scripts/test_import_discord_chats.py -k import_session -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add scripts/import_discord_chats.py tests/scripts/test_import_discord_chats.py
git commit -m "feat(import): import_session dispatch (sync service vs embedded)"
```

---

## Task 3: Reset extraction + service-mode guard (`maybe_reset`)

**Files:**
- Modify: `scripts/import_discord_chats.py` (extract reset block at lines ~253-299 into a function; add `maybe_reset`)
- Test: `tests/scripts/test_import_discord_chats.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/scripts/test_import_discord_chats.py`:
```python
def test_maybe_reset_noop_when_not_requested():
    mm, palace = MagicMock(), MagicMock()
    assert importer.maybe_reset(False, False, mm, palace) is False


def test_maybe_reset_skips_in_service_mode():
    mm, palace = MagicMock(), MagicMock()
    # Even with do_reset=True, service mode must not touch any store.
    assert importer.maybe_reset(True, True, mm, palace) is False
    palace.delete_all.assert_not_called()
    mm.episode_store.assert_not_called()


def test_maybe_reset_runs_embedded(monkeypatch):
    called = {}
    monkeypatch.setattr(importer, "reset_embedded_memory",
                        lambda mm, palace: called.setdefault("ran", True))
    assert importer.maybe_reset(True, False, MagicMock(), MagicMock()) is True
    assert called.get("ran") is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `"$POETRY" run pytest tests/scripts/test_import_discord_chats.py -k maybe_reset -v`
Expected: FAIL — `AttributeError: ... has no attribute 'maybe_reset'`

- [ ] **Step 3: Write minimal implementation**

In `scripts/import_discord_chats.py`: cut the body of the current `if args.reset:` block (lines ~254-299, the four try/except clearing episodes, semantic memories, graph, memory history, plus the surrounding log lines) and paste it into a new module-level function. Add `maybe_reset` below it:
```python
def reset_embedded_memory(mm, palace) -> None:
    """Clear all embedded memory data (episodes, semantic memories, graph,
    history). Embedded-only — uses Qdrant/graph/db handles that are None on
    the remote path. Body moved verbatim from the old main() --reset block.
    """
    logger.warning("Resetting ALL memory data...")
    # Clear episodes collection
    try:
        from mypalclara.core.memory.episodes import EPISODES_COLLECTION

        ep_client = mm.episode_store.client
        existing = [c.name for c in ep_client.get_collections().collections]
        if EPISODES_COLLECTION in existing:
            ep_client.delete_collection(EPISODES_COLLECTION)
            logger.info(f"  Deleted {EPISODES_COLLECTION} collection")
        mm.episode_store._ensure_collection()
        logger.info(f"  Recreated {EPISODES_COLLECTION} collection")
    except Exception as e:
        logger.warning(f"  Failed to reset episodes: {e}")

    # Clear semantic memories collection
    try:
        vs = palace.vector_store
        if hasattr(vs, "delete_col"):
            vs.delete_col()
        from mypalclara.core.memory.config import EMBEDDING_MODEL_DIMS

        if hasattr(vs, "create_col"):
            vs.create_col(vector_size=EMBEDDING_MODEL_DIMS, distance="Cosine")
        logger.info("  Reset semantic memories collection")
    except Exception as e:
        logger.warning(f"  Failed to reset semantic memories: {e}")

    # Clear graph
    try:
        if hasattr(palace, "graph") and palace.graph is not None:
            palace.graph.reset()
            palace.graph._create_indexes()
            logger.info("  Reset graph")
    except Exception as e:
        logger.warning(f"  Failed to reset graph: {e}")

    # Clear memory history
    try:
        if hasattr(palace, "db"):
            palace.db.reset()
            logger.info("  Reset memory history")
    except Exception as e:
        logger.warning(f"  Failed to reset memory history: {e}")

    logger.info("Reset complete")


def maybe_reset(do_reset: bool, use_service: bool, mm, palace) -> bool:
    """Run embedded reset if requested; no-op (with warning) in service mode.

    Returns True only when an embedded reset actually ran.
    """
    if not do_reset:
        return False
    if use_service:
        logger.warning(
            "--reset is embedded-only; ignored in service mode "
            "(USE_PALACE_SERVICE=true). No data deleted."
        )
        return False
    reset_embedded_memory(mm, palace)
    return True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `"$POETRY" run pytest tests/scripts/test_import_discord_chats.py -k maybe_reset -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add scripts/import_discord_chats.py tests/scripts/test_import_discord_chats.py
git commit -m "refactor(import): extract reset_embedded_memory + maybe_reset guard"
```

---

## Task 4: Wire `main()` for routed mode

**Files:**
- Modify: `scripts/import_discord_chats.py` `main()` (lines ~227-417)

This task has no new unit test (it's integration wiring; the behavior is covered by Tasks 1–3). Verify via the existing `--help`/`--dry-run` smoke and the full test suite.

- [ ] **Step 1: Swap the Palace import + guard**

Replace (line ~236):
```python
    from mypalclara.core.memory.config import PALACE

    if PALACE is None:
        logger.error("Palace not initialized")
        sys.exit(1)
```
with:
```python
    from mypalclara.core.memory.routed import PALACE, USE_PALACE_SERVICE

    if PALACE is None:
        logger.error("Palace not initialized")
        sys.exit(1)
    logger.info(
        "Memory mode: %s",
        "REMOTE Palace service" if USE_PALACE_SERVICE else "embedded",
    )
```

- [ ] **Step 2: Replace the inline reset block with `maybe_reset`**

Replace the entire `if args.reset:` block (now the body lives in `reset_embedded_memory`) with:
```python
    maybe_reset(args.reset, USE_PALACE_SERVICE, mm, PALACE)
```
Keep the `if not mm.episode_store:` guard as-is — in service mode `mm.episode_store` is a `RemoteEpisodeStore` (truthy); in embedded it's the `EpisodeStore`.

- [ ] **Step 3: Initialize the smoke-abort flag before the file loop**

Immediately before `for chat_path in chat_files:` (line ~327), add:
```python
    smoke_ok = False  # service mode: first successful remote reflect flips this
```

- [ ] **Step 4: Resolve per-file agent after loading messages**

After `metadata, messages = load_chat_with_decisions(chat_path)` (line ~332) and its log lines, add:
```python
        channel_agent = resolve_channel_agent(messages, default_agent=mm.agent_id)
        logger.info(f"  Agent: {channel_agent}")
```

- [ ] **Step 5: Replace the per-session reflect call with `import_session` + smoke-abort**

Replace the per-session `try/except` body (lines ~385-402, the `msg_dicts = ...` / `result = mm.reflect_on_session(...)` / logging / except) with:
```python
            try:
                result = import_session(
                    session,
                    user_id=user_id,
                    agent_id=channel_agent,
                    channel=metadata["channel"],
                    mm=mm,
                    palace=PALACE,
                    use_service=USE_PALACE_SERVICE,
                )
                ep_count = len(result.get("episodes", []))
                ent_count = len(result.get("entities", []))
                note_count = len(result.get("self_notes", []))
                total_episodes += ep_count
                logger.info(f"    -> {ep_count} episodes, {ent_count} entities, {note_count} self-notes")
                if USE_PALACE_SERVICE:
                    smoke_ok = True
            except Exception as e:
                if USE_PALACE_SERVICE and not smoke_ok:
                    logger.error(
                        f"    -> First remote reflection FAILED — aborting before "
                        f"churning the rest. Check the Palace server's LLM config "
                        f"(LLM_API_KEY/LLM_PROVIDER) and that it is reachable. Error: {e}"
                    )
                    sys.exit(1)
                logger.error(f"    -> Failed: {e}")
                total_errors += 1

            total_sessions += 1
```
(Delete the now-duplicate `total_sessions += 1` that previously followed the block, so it is only incremented once.)

- [ ] **Step 6: Run the full importer test file + lint**

Run:
```bash
"$POETRY" run pytest tests/scripts/test_import_discord_chats.py -v
"$POETRY" run ruff check scripts/import_discord_chats.py && "$POETRY" run ruff format scripts/import_discord_chats.py
```
Expected: all importer unit tests PASS; ruff clean.

- [ ] **Step 7: Embedded-mode dry-run still works (regression)**

Run:
```bash
USE_PALACE_SERVICE=false "$POETRY" run python scripts/import_discord_chats.py --dry-run 2>&1 | tail -20
```
Expected: same session-split output as before the change (581 / 260 / 162 sessions), no traceback.

- [ ] **Step 8: Commit**

```bash
git add scripts/import_discord_chats.py
git commit -m "feat(import): route reflection through Palace service in main()"
```

---

## Task 5: Live verification (service mode)

**Files:** none (runtime verification against the running MyPalace stack)

Prerequisites: MyPalace stack up (`palace-api` on :8000, auth disabled), and mypalclara `.env` has `USE_PALACE_SERVICE=true` + `PALACE_SERVICE_URL=http://localhost:8000`. The Palace server's own `LLM_API_KEY`/`LLM_PROVIDER` must be configured for server-side extraction.

- [ ] **Step 1: Service-mode dry-run (no writes, exercises routed import)**

Run:
```bash
"$POETRY" run python scripts/import_discord_chats.py --dry-run 2>&1 | tail -25
```
Expected: logs "Memory mode: REMOTE Palace service", an "Agent:" line per file (clara / clara / clarissa), session counts, no traceback, no writes.

- [ ] **Step 2: Single-file smoke (real write, smallest channel)**

Run the JORSHTOPIA file (Clara) live:
```bash
"$POETRY" run python scripts/import_discord_chats.py \
  --file "chats/JORSHTOPIA - clara [1451983877244715241].json" 2>&1 | tail -40
```
Expected: first session logs `-> N episodes ...` with N≥0 and no abort; subsequent sessions proceed. If it aborts on session 1, fix the Palace server LLM config before continuing.

- [ ] **Step 3: Confirm episodes landed in Palace (agent=clara)**

Run:
```bash
curl -s "http://127.0.0.1:8000/v1/users/discord-271274659385835521/episodes?limit=3" \
  | python3 -m json.tool | head -40
```
Expected: HTTP 200 with episode objects attributed to agent_id `clara` (route per `openapi.json`; adjust the user_id/path if the deployment differs).

- [ ] **Step 4: Full import (all three files)**

Run (this is the long one — synchronous server-side LLM extraction across ~1,003 sessions):
```bash
"$POETRY" run python scripts/import_discord_chats.py 2>&1 | tee /tmp/discord-import.log | tail -30
```
Expected: final line `Import complete: <N> episodes from <M> sessions (<E> errors)`. Review `/tmp/discord-import.log` for the per-channel agent lines and any per-session errors.

- [ ] **Step 5: Update memory + (optional) commit any tweaks**

If verification surfaced fixes, commit them. Note the completed import in project memory ([[local-palace-service-stack]] / [[palace-emotional-topic-services]]).

---

## Self-Review Notes

- **Spec coverage:** guard fix → Task 4.1; per-channel agent → Task 1 + 4.4; sync service write → Task 2 + 4.5; `--reset` disabled → Task 3 + 4.2; dual-mode preserved → embedded branches untouched (Task 2/3) + regression Task 4.7; first-session smoke-abort → Task 4.5; testing → Tasks 1–3 + 4.6.
- **Type consistency:** `import_session(...)` signature identical in Task 2 and Task 4.5; `maybe_reset(do_reset, use_service, mm, palace)` and `reset_embedded_memory(mm, palace)` identical in Task 3 and Task 4.2; `resolve_channel_agent(messages, default_agent)` identical in Task 1 and Task 4.4.
- **No placeholders:** all steps carry concrete code/commands.
