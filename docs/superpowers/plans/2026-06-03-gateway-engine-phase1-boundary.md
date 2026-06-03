# Gateway → mypal-engine Phase 1 (In-Place Boundary) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the gateway/engine subgraph self-contained — no engine module imports a platform SDK (`discord`, …) or the client-side `mypalclara.adapters` package — while the app still boots and runs exactly as today, all in one repo.

**Architecture:** This is **Phase 1** of the two-phase extraction in [the design spec](../specs/2026-06-03-gateway-engine-extraction-design.md). Phase 1 enforces the engine↔client boundary *in place*. Phase 2 (a separate plan, written after this lands) does the `git filter-repo` lift into the `mypal-engine` repo. We lock the boundary with an executable architecture test whose allowlist of known violations shrinks to empty as tasks land — so every commit stays green.

**Tech Stack:** Python 3.11+, Poetry (`package-mode = false`, code runs from repo root on `sys.path`), pytest (`asyncio_mode = "auto"`, `testpaths = ["tests"]`), websockets, FastAPI, Pydantic v2, ruff.

> **Note on scope vs. spec:** Exhaustive grepping found the real engine→client/SDK leak set is **six** items, not the spec's "two." This plan covers all of them (4 hard module-level + 2 soft/lazy), per the approved scope decision to include the soft cleanups. The verified inventory below supersedes the spec's "two couplings" line.

## Verified leak inventory (what Phase 1 removes)

| # | Engine file | Leak | Task |
|---|---|---|---|
| 1 | `mypalclara/core/discord/` (4 files, ~2458 lines) | module-level `import discord`; it's Discord-adapter UI misfiled in `core/` (only the Discord adapter imports it) | Task 3 — move to `adapters/discord/ui/` |
| 2 | `mypalclara/gateway/api/game.py` + `mypalclara/adapters/game/` | gateway imports `mypalclara.adapters.game.api`; game is an engine-served HTTP API mislocated under `adapters/` | Task 4 — move to `core/game/` |
| 3 | `mypalclara/services/email/monitor.py` | module-level `import discord` (and it's **dead code** — `email_monitor_loop` has no callers) | Task 5 — callback refactor |
| 4 | `mypalclara/gateway/adapter_manager.py` | module-level `from mypalclara.adapters.manifest import …` (adapter subprocess spawning) | Task 6 — externalize |
| 5 | `mypalclara/config/logging.py` | lazy `import discord` inside `DiscordLogHandler.send_embed` | Task 7 — inject renderer |
| 6 | `mypalclara/services/proactive/engine.py` | `TYPE_CHECKING` `from discord import Client` + duck-typed discord send path (**dormant** — ORS loop not wired) | Task 8 — `send_fn` callback |

Plus **Task 2** extracts the wire protocol into the top-level `mypal_protocol` package (the client↔engine contract), and **Task 9** pins the engine/client path inventory for Phase 2.

## File structure after Phase 1

```
mypal_protocol/                      # NEW top-level package (the wire contract)
  __init__.py                        # re-exports everything from messages
  messages.py                        # moved verbatim from mypalclara/gateway/protocol.py
mypalclara/
  gateway/
    protocol.py                      # thin back-compat shim → from mypal_protocol.messages import *
    adapter_manager.py               # no longer imports mypalclara.adapters; external-adapter default
    api/game.py                      # imports router from mypalclara.core.game.api
  core/
    game/                            # MOVED from mypalclara/adapters/game/
      __init__.py  engine.py  api.py
    (no more core/discord/)          # MOVED out to adapters/discord/ui/
  config/logging.py                  # DiscordLogHandler renders embeds via injected callable
  services/
    proactive/engine.py              # send_proactive_message(send_fn, …); no discord
    email/monitor.py                 # email_monitor_loop(send_alert_fn, …); no discord
  adapters/                          # CLIENT side
    discord/ui/                      # MOVED from mypalclara/core/discord/
    cli/launch_adapters.py           # NEW dev-only multi-adapter launcher (moved off the engine)
tests/architecture/
  test_engine_boundary.py            # NEW executable boundary invariant
```

---

## Task 1: Engine boundary test (the executable invariant)

Establishes the architecture test with a `KNOWN_VIOLATIONS` allowlist that exactly matches today's violations, so it passes now and **shrinks to empty** across Tasks 2–8.

**Files:**
- Create: `tests/architecture/__init__.py`
- Create: `tests/architecture/test_engine_boundary.py`

- [ ] **Step 1: Create the test package marker**

Create `tests/architecture/__init__.py` (empty file):

```python
```

- [ ] **Step 2: Write the boundary test**

Create `tests/architecture/test_engine_boundary.py`:

```python
"""Architecture boundary test for the gateway → mypal-engine extraction (Phase 1).

The engine packages in ENGINE_PACKAGES must never import a platform SDK
(discord, telethon, …) or the client-side `mypalclara.adapters` package. Known
remaining violations live in KNOWN_VIOLATIONS and shrink to empty as Phase 1
tasks land; the test fails if a NEW violation appears or if an allowlisted file
no longer violates (forcing the allowlist to stay in lockstep with the fixes).
"""

from __future__ import annotations

import ast
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
PKG_ROOT = REPO_ROOT / "mypalclara"

# Directories that travel with the standalone engine.
ENGINE_PACKAGES = [
    "core",
    "db",
    "config",
    "sandbox",
    "tools",
    "gateway",
    "services/proactive",
    "services/blog",
    "services/email",
]

FORBIDDEN_SDK_ROOTS = {
    "discord",
    "telethon",
    "telegram",
    "slack_sdk",
    "slack_bolt",
    "botbuilder",
    "signalbot",
    "whatsapp",
}
FORBIDDEN_INTERNAL_PREFIXES = ("mypalclara.adapters",)

# Files (relative to mypalclara/) still expected to violate. Shrinks to {} by Task 8.
KNOWN_VIOLATIONS = {
    "core/discord/__init__.py",
    "core/discord/commands.py",
    "core/discord/views.py",
    "core/discord/embeds.py",
    "services/email/monitor.py",
    "services/proactive/engine.py",
    "config/logging.py",
    "gateway/adapter_manager.py",
    "gateway/api/game.py",
}


def _iter_engine_files():
    for pkg in ENGINE_PACKAGES:
        base = PKG_ROOT / pkg
        if not base.exists():
            continue
        for path in base.rglob("*.py"):
            yield path


def _imported_modules(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module is not None and node.level == 0:
                modules.add(node.module)
    return modules


def _is_forbidden(module: str) -> bool:
    top = module.split(".")[0]
    if top in FORBIDDEN_SDK_ROOTS:
        return True
    return any(module == p or module.startswith(p + ".") for p in FORBIDDEN_INTERNAL_PREFIXES)


def _current_violations() -> set[str]:
    violations: set[str] = set()
    for path in _iter_engine_files():
        rel = path.relative_to(PKG_ROOT).as_posix()
        if any(_is_forbidden(m) for m in _imported_modules(path)):
            violations.add(rel)
    return violations


def test_engine_has_no_new_client_or_sdk_imports():
    violations = _current_violations()
    unexpected = violations - KNOWN_VIOLATIONS
    assert not unexpected, f"New engine boundary violations: {sorted(unexpected)}"


def test_known_violations_allowlist_is_not_stale():
    violations = _current_violations()
    stale = KNOWN_VIOLATIONS - violations
    assert not stale, (
        "These files no longer violate — remove them from KNOWN_VIOLATIONS: "
        f"{sorted(stale)}"
    )
```

- [ ] **Step 3: Run the test to verify it passes against today's tree**

Run: `poetry run pytest tests/architecture/test_engine_boundary.py -v`
Expected: PASS (both tests). `KNOWN_VIOLATIONS` exactly matches the current violation set.

> If `test_known_violations_allowlist_is_not_stale` FAILS, the allowlist doesn't match reality — adjust `KNOWN_VIOLATIONS` to exactly the reported current set before continuing.

- [ ] **Step 4: Commit**

```bash
git add tests/architecture/__init__.py tests/architecture/test_engine_boundary.py
git commit -m "test(arch): add engine import-boundary test with shrinking allowlist"
```

---

## Task 2: Extract the wire protocol into `mypal_protocol`

Move `gateway/protocol.py` (pure Pydantic, zero core/db imports) into a top-level `mypal_protocol` package, leave a back-compat shim, and re-point the adapter importers. Adds a client-side boundary check.

**Files:**
- Create: `mypal_protocol/__init__.py`, `mypal_protocol/messages.py`
- Modify: `mypalclara/gateway/protocol.py` (becomes a shim)
- Modify (re-point imports): `mypalclara/adapters/protocol.py`, `mypalclara/adapters/protocols.py`, `mypalclara/adapters/base.py`, `mypalclara/adapters/cli/gateway_client.py`, `mypalclara/adapters/discord/gateway_client.py`, `mypalclara/adapters/teams/gateway_client.py`, `mypalclara/adapters/cli/voice/manager.py`, `mypalclara/adapters/discord/voice/manager.py`
- Modify: `tests/architecture/test_engine_boundary.py` (add client-side check)

- [ ] **Step 1: Add the client-side boundary test (failing first)**

Append to `tests/architecture/test_engine_boundary.py`:

```python
# --- Client side must not import gateway internals (only mypal_protocol / HTTP API) ---

CLIENT_PACKAGES = ["adapters"]

# Files (relative to mypalclara/) still importing mypalclara.gateway.*. Shrinks to {}.
KNOWN_CLIENT_GATEWAY_IMPORTS = {
    "adapters/protocol.py",
    "adapters/protocols.py",
    "adapters/base.py",
    "adapters/cli/gateway_client.py",
    "adapters/discord/gateway_client.py",
    "adapters/teams/gateway_client.py",
    "adapters/cli/voice/manager.py",
    "adapters/discord/voice/manager.py",
}


def _client_gateway_importers() -> set[str]:
    found: set[str] = set()
    for pkg in CLIENT_PACKAGES:
        base = PKG_ROOT / pkg
        for path in base.rglob("*.py"):
            rel = path.relative_to(PKG_ROOT).as_posix()
            for module in _imported_modules(path):
                if module == "mypalclara.gateway" or module.startswith("mypalclara.gateway."):
                    found.add(rel)
                    break
    return found


def test_client_does_not_import_gateway_internals():
    importers = _client_gateway_importers()
    unexpected = importers - KNOWN_CLIENT_GATEWAY_IMPORTS
    assert not unexpected, f"Client modules importing gateway internals: {sorted(unexpected)}"
```

Run: `poetry run pytest tests/architecture/test_engine_boundary.py::test_client_does_not_import_gateway_internals -v`
Expected: PASS (importers == allowlist). This baseline shrinks as we re-point imports.

- [ ] **Step 2: Move the protocol module with history**

```bash
mkdir -p mypal_protocol
git mv mypalclara/gateway/protocol.py mypal_protocol/messages.py
```

- [ ] **Step 3: Create the package `__init__.py`**

Create `mypal_protocol/__init__.py`:

```python
"""mypal-protocol: the WebSocket wire contract shared by the engine and its clients.

Pure Pydantic message models, zero engine/db dependencies. In Phase 2 this package
is published from the mypal-engine repo and consumed by both sides.
"""

from mypal_protocol.messages import *  # noqa: F401,F403
```

- [ ] **Step 4: Recreate `gateway/protocol.py` as a back-compat shim**

Create `mypalclara/gateway/protocol.py`:

```python
"""Back-compat shim. The protocol now lives in the top-level `mypal_protocol` package.

Import from `mypal_protocol` directly in new code. This shim keeps existing
`mypalclara.gateway.protocol` imports working during the engine extraction.
"""

from mypal_protocol.messages import *  # noqa: F401,F403
```

- [ ] **Step 5: Verify the shim and package import the same symbols**

Run:
```bash
poetry run python -c "import mypal_protocol as p, mypalclara.gateway.protocol as s; assert p.MessageType is s.MessageType; assert p.ProactiveMessage is s.ProactiveMessage; print('OK', p.MessageType.REGISTER)"
```
Expected: `OK MessageType.REGISTER`

- [ ] **Step 6: Re-point adapter imports from `mypalclara.gateway.protocol` → `mypal_protocol`**

Edit each of these files, replacing `from mypalclara.gateway.protocol import (...)` with `from mypal_protocol import (...)` (keep the imported names identical):
- `mypalclara/adapters/protocol.py:6`
- `mypalclara/adapters/protocols.py:13`
- `mypalclara/adapters/base.py:24`
- `mypalclara/adapters/cli/gateway_client.py:15`
- `mypalclara/adapters/discord/gateway_client.py:15`
- `mypalclara/adapters/teams/gateway_client.py:15`
- `mypalclara/adapters/cli/voice/manager.py:15`
- `mypalclara/adapters/discord/voice/manager.py:15`

Find any stragglers:
```bash
grep -rn "mypalclara.gateway.protocol" mypalclara/adapters
```
Expected after edits: no output.

- [ ] **Step 7: Empty the client allowlist**

In `tests/architecture/test_engine_boundary.py`, set:

```python
KNOWN_CLIENT_GATEWAY_IMPORTS: set[str] = set()
```

- [ ] **Step 8: Run boundary tests + a smoke import**

Run:
```bash
poetry run pytest tests/architecture/test_engine_boundary.py -v
poetry run python -c "import mypalclara.adapters.base; import mypalclara.gateway.server; print('imports OK')"
```
Expected: all boundary tests PASS; `imports OK`.

- [ ] **Step 9: Commit**

```bash
git add mypal_protocol mypalclara/gateway/protocol.py mypalclara/adapters tests/architecture/test_engine_boundary.py
git commit -m "refactor(protocol): extract wire contract to top-level mypal_protocol package"
```

---

## Task 3: Move `core/discord/` → `adapters/discord/ui/`

The `core/discord/` package is Discord-adapter UI (slash commands, embeds, views, utils) imported only by the Discord adapter. Move it to the client side and re-point the four importers.

**Files:**
- Move: `mypalclara/core/discord/` → `mypalclara/adapters/discord/ui/`
- Modify: `mypalclara/adapters/discord/main.py:38`, `mypalclara/adapters/discord/attachment_handler.py:294`, `mypalclara/adapters/discord/gateway_client.py:409`, `mypalclara/adapters/discord/gateway_client.py:468`
- Modify: `tests/architecture/test_engine_boundary.py` (shrink allowlist)

- [ ] **Step 1: Confirm the only importers are the Discord adapter**

Run:
```bash
grep -rn "mypalclara.core.discord\|from mypalclara.core import discord" mypalclara --include="*.py" | grep -v "mypalclara/core/discord/"
```
Expected: exactly the 4 known lines, all under `mypalclara/adapters/discord/`. If anything else appears (e.g. an engine module), STOP and reassess — `resize_image_for_vision` in `utils.py` must not be used engine-side.

- [ ] **Step 2: Move the package with history**

```bash
git mv mypalclara/core/discord mypalclara/adapters/discord/ui
```

- [ ] **Step 3: Re-point the four importers**

Apply these exact replacements:
- `mypalclara/adapters/discord/main.py:38`: `from mypalclara.core.discord import setup as setup_slash_commands` → `from mypalclara.adapters.discord.ui import setup as setup_slash_commands`
- `mypalclara/adapters/discord/attachment_handler.py:294`: `from mypalclara.core.discord.utils import resize_image_for_vision` → `from mypalclara.adapters.discord.ui.utils import resize_image_for_vision`
- `mypalclara/adapters/discord/gateway_client.py:409`: `from mypalclara.core.discord.embeds import (` → `from mypalclara.adapters.discord.ui.embeds import (`
- `mypalclara/adapters/discord/gateway_client.py:468`: `from mypalclara.core.discord.views import GatewayButtonView` → `from mypalclara.adapters.discord.ui.views import GatewayButtonView`

Verify none remain:
```bash
grep -rn "mypalclara.core.discord" mypalclara --include="*.py"
```
Expected: no output.

- [ ] **Step 4: Shrink the engine allowlist**

In `tests/architecture/test_engine_boundary.py`, remove these four entries from `KNOWN_VIOLATIONS`:
`core/discord/__init__.py`, `core/discord/commands.py`, `core/discord/views.py`, `core/discord/embeds.py`.

- [ ] **Step 5: Run boundary tests + Discord adapter import smoke**

Run:
```bash
poetry run pytest tests/architecture/test_engine_boundary.py -v
poetry run python -c "import mypalclara.adapters.discord.ui as ui; assert hasattr(ui, 'setup'); print('discord ui OK')"
```
Expected: boundary tests PASS (no stale allowlist); `discord ui OK`.

- [ ] **Step 6: Commit**

```bash
git add mypalclara/core mypalclara/adapters/discord tests/architecture/test_engine_boundary.py
git commit -m "refactor(discord): move Discord UI from core/ to adapters/discord/ui (client side)"
```

---

## Task 4: Move the game backend `adapters/game/` → `core/game/`

The "game adapter" is an HTTP API the engine serves (the gateway mounts its router; web-UI calls `/api/v1/game`). It's mislocated under `adapters/`. Move it into the engine namespace and drop its manifest registration (it isn't a spawnable platform adapter).

**Files:**
- Move: `mypalclara/adapters/game/` → `mypalclara/core/game/`
- Modify: `mypalclara/core/game/__init__.py` (drop adapter/manifest registration), `mypalclara/core/game/api.py` (re-point internal import)
- Modify: `mypalclara/gateway/api/game.py:3`
- Modify: `mypalclara/adapters/manifest.py:307` (remove `"mypalclara.adapters.game"` from the auto-import list)
- Modify: `tests/architecture/test_engine_boundary.py` (shrink allowlist)

- [ ] **Step 1: Confirm `game/engine.py` has no platform-SDK / adapters imports**

Run:
```bash
grep -nE "^(import|from)" mypalclara/adapters/game/engine.py | grep -E "discord|mypalclara.adapters"
```
Expected: no output (it imports only stdlib, fastapi, pydantic, and engine-side modules).

- [ ] **Step 2: Move the package with history**

```bash
git mv mypalclara/adapters/game mypalclara/core/game
```

- [ ] **Step 3: Re-point the API's internal import**

In `mypalclara/core/game/api.py`, change:
```python
from mypalclara.adapters.game.engine import (
```
to:
```python
from mypalclara.core.game.engine import (
```

- [ ] **Step 4: Strip the manifest registration from the package init**

Replace the entire contents of `mypalclara/core/game/__init__.py` with (removes the `mypalclara.adapters.manifest` import and the `GameAdapter` marker, which only existed for adapter discovery):

```python
"""Game backend for Clara's Game Room.

An HTTP-served engine module (not a platform adapter): the gateway mounts
`api.router` at /api/v1/game and the web-UI calls it. Lives in the engine.

Module structure:
- engine.py: Core game logic, models, and LLM integration
- api.py: FastAPI router with /move endpoint
"""

from mypalclara.core.game.api import router
from mypalclara.core.game.engine import (
    GameMoveRequest,
    GameMoveResponse,
    get_clara_move,
)

__all__ = [
    "GameMoveRequest",
    "GameMoveResponse",
    "get_clara_move",
    "router",
]
```

- [ ] **Step 5: Re-point the gateway router import**

In `mypalclara/gateway/api/game.py:3`, change:
```python
from mypalclara.adapters.game.api import router
```
to:
```python
from mypalclara.core.game.api import router
```

- [ ] **Step 6: Remove `game` from the adapter manifest auto-import list**

In `mypalclara/adapters/manifest.py` around line 307, delete the list element `"mypalclara.adapters.game",`. Verify no references remain:
```bash
grep -rn "adapters.game\|adapters/game" mypalclara --include="*.py"
```
Expected: no output.

- [ ] **Step 7: Shrink the engine allowlist**

In `tests/architecture/test_engine_boundary.py`, remove `"gateway/api/game.py"` from `KNOWN_VIOLATIONS`.

- [ ] **Step 8: Run boundary tests + game router import smoke**

Run:
```bash
poetry run pytest tests/architecture/test_engine_boundary.py -v
poetry run python -c "from mypalclara.gateway.api.game import router; from mypalclara.core.game import get_clara_move; print('game OK', router is not None)"
```
Expected: boundary tests PASS; `game OK True`.

- [ ] **Step 9: Commit**

```bash
git add mypalclara/core/game mypalclara/gateway/api/game.py mypalclara/adapters/manifest.py tests/architecture/test_engine_boundary.py
git commit -m "refactor(game): relocate game HTTP backend from adapters/ to core/game (engine side)"
```

---

## Task 5: De-discord the email monitor (callback refactor)

`email_monitor_loop` is dead code (no callers) that imports `discord` and builds a `discord.Embed`. Refactor it to a transport-agnostic loop driven by its own interval and an injected `send_alert_fn`, formatting the alert as plain markdown. No platform SDK.

**Files:**
- Modify: `mypalclara/services/email/monitor.py`
- Create: `tests/services/email/__init__.py`, `tests/services/email/test_alert_formatting.py`
- Modify: `tests/architecture/test_engine_boundary.py` (shrink allowlist)

- [ ] **Step 1: Write the failing test for discord-free alert formatting**

Create `tests/services/email/__init__.py` (empty) and `tests/services/email/test_alert_formatting.py`:

```python
"""The email monitor must format alerts as plain markdown, with no discord import."""

import ast
import pathlib

import mypalclara.services.email.monitor as monitor


def test_monitor_module_does_not_import_discord():
    src = pathlib.Path(monitor.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert "discord" not in imported


def test_format_email_alert_renders_markdown():
    text = monitor.format_email_alert(
        subject="Server down",
        from_addr="ops@example.com",
        account_email="me@example.com",
        rule_name="Urgent ops",
        importance="urgent",
        snippet="Disk at 99%",
    )
    assert "Server down" in text
    assert "ops@example.com" in text
    assert "Urgent ops" in text
    assert "URGENT" in text.upper()
```

Run: `poetry run pytest tests/services/email/test_alert_formatting.py -v`
Expected: FAIL — `test_monitor_module_does_not_import_discord` fails (discord imported) and `format_email_alert` does not exist.

- [ ] **Step 2: Remove the discord import and add a markdown formatter**

In `mypalclara/services/email/monitor.py`:

Delete line 13 (`import discord`) and the `TYPE_CHECKING` block at lines 23–24 (`from discord import Client`). Remove the now-unused `TYPE_CHECKING` import on line 11 if present (change `from typing import TYPE_CHECKING` accordingly; if `TYPE_CHECKING` is its only use, delete the line).

Add this module-level function (place it just above `send_email_alert`):

```python
IMPORTANCE_EMOJI = {
    "urgent": "🚨",
    "high": "⚠️",
    "normal": "📬",
    "low": "📭",
}


def format_email_alert(
    subject: str,
    from_addr: str,
    account_email: str,
    rule_name: str,
    importance: str,
    snippet: str | None = None,
) -> str:
    """Render an email alert as plain Discord-flavored markdown (no SDK objects)."""
    emoji = IMPORTANCE_EMOJI.get(importance, "📬")
    lines = [
        f"{emoji} **{subject[:100]}**",
        f"**From:** {from_addr[:100]}",
        f"**Rule:** {rule_name}  •  **Account:** {account_email}",
        f"**Importance:** {importance.upper()}",
    ]
    if snippet:
        lines.append(f"> {snippet[:200]}")
    return "\n".join(lines)
```

- [ ] **Step 3: Replace the discord send path with the injected callback**

Replace the `email_monitor_loop`, `check_account`, `process_message`, and `send_email_alert` signatures so they thread an injected `send_alert_fn` instead of a `discord.Client`. Apply these edits:

`email_monitor_loop` (lines ~50–86) — replace the signature and the client-driven gating:

```python
async def email_monitor_loop(
    send_alert_fn: "Callable[[str, str, str], Awaitable[str | None]]",
    poll_seconds: int = 60,
) -> None:
    """Poll email accounts and deliver alerts via send_alert_fn.

    Args:
        send_alert_fn: async (user_id, channel_id, content) -> message_id | None
        poll_seconds: base sleep between scheduler passes
    """
    logger.info("Email monitoring loop starting...")
    await asyncio.sleep(10)  # let startup settle

    while True:
        try:
            now = datetime.now(UTC).replace(tzinfo=None)
            accounts = get_accounts_to_check(now)
            for account in accounts:
                try:
                    await check_account(account, send_alert_fn)
                except Exception as e:
                    logger.error(f"Error checking account {account.email_address}: {e}")
                    await handle_account_error(account, str(e))
                await asyncio.sleep(1)
            sleep_seconds = calculate_next_sleep()
            await asyncio.sleep(min(poll_seconds, max(10, sleep_seconds)))
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"Email monitor loop error: {e}")
            await asyncio.sleep(60)
```

Add the typing imports at the top of the file (with the other stdlib imports):

```python
from collections.abc import Awaitable, Callable
```

`check_account` (line ~126): change the parameter `client: Client` to `send_alert_fn` and pass it through to `process_message`. In its body replace `await process_message(account, msg, rules, client)` with `await process_message(account, msg, rules, send_alert_fn)`.

`process_message` (line ~193): change `client: Client` to `send_alert_fn`, and replace the send block (lines ~219–224) with:

```python
    # Deliver the alert via the injected sender
    content = format_email_alert(
        subject=msg.subject,
        from_addr=msg.from_addr,
        account_email=account.email_address,
        rule_name=match.rule_name,
        importance=match.importance,
        snippet=msg.snippet,
    )
    message_id = await send_alert_fn(account.user_id, account.alert_channel_id, content)

    if message_id:
        record_alert(account, msg, match, message_id)
```

Delete the entire `send_email_alert` function (lines ~260–319) — its discord embed logic is replaced by `format_email_alert` + `send_alert_fn`.

- [ ] **Step 4: Run the tests**

Run: `poetry run pytest tests/services/email/test_alert_formatting.py -v`
Expected: PASS (no discord import; formatter renders markdown).

- [ ] **Step 5: Wire the loop into the gateway (optional activation, discord-free)**

In `mypalclara/gateway/__main__.py`, directly after the heartbeat wiring block (after line ~591), add an email monitor task that reuses the same WS routing pattern as `_heartbeat_send`:

```python
    # Start email monitoring loop if enabled (routes alerts over the gateway WS)
    email_task = None
    from mypalclara.services.email.monitor import is_email_monitoring_enabled

    if is_email_monitoring_enabled():
        from mypalclara.services.email.monitor import email_monitor_loop

        async def _email_send(user_id: str, channel_id: str, content: str) -> str | None:
            """Route an email alert to the adapter that owns the user, over WS.

            Returns a truthy sentinel when delivered to >=1 node so the caller
            records the alert (dedup keys on account_id+email_uid, not on the id).
            """
            from mypal_protocol import ChannelInfo, ProactiveMessage, UserInfo

            platform = user_id.split("-", 1)[0] if "-" in user_id else "unknown"
            platform_user_id = user_id.split("-", 1)[1] if "-" in user_id else user_id
            raw_channel_id = channel_id.split("-", 1)[1] if "-" in channel_id else channel_id
            channel_type = "dm" if str(channel_id).startswith("dm-") else "server"

            delivered = 0
            nodes = await server.node_registry.get_all_nodes()
            for node in nodes:
                if node.platform and node.platform != platform:
                    continue
                try:
                    msg = ProactiveMessage(
                        user=UserInfo(id=user_id, platform_id=platform_user_id, name=None),
                        channel=ChannelInfo(id=raw_channel_id, type=channel_type),
                        content=content,
                        priority="high",
                    )
                    await node.websocket.send(msg.model_dump_json())
                    delivered += 1
                except Exception as e:
                    logger.warning(f"Failed to send email alert to {node.node_id}: {e}")
            return "ws" if delivered else None

        email_task = asyncio.create_task(email_monitor_loop(_email_send))
        logger.info("Email monitoring loop started")
```

> Find the shutdown section (where `heartbeat_task` is cancelled) and cancel `email_task` the same way if it is not None. Match the existing cancellation pattern in that function.

- [ ] **Step 6: Shrink the engine allowlist**

In `tests/architecture/test_engine_boundary.py`, remove `"services/email/monitor.py"` from `KNOWN_VIOLATIONS`.

- [ ] **Step 7: Run boundary + email tests + gateway import smoke**

Run:
```bash
poetry run pytest tests/architecture/test_engine_boundary.py tests/services/email -v
poetry run python -c "import mypalclara.gateway.__main__; import mypalclara.services.email.monitor; print('gateway+email import OK')"
```
Expected: all PASS; `gateway+email import OK`.

- [ ] **Step 8: Commit**

```bash
git add mypalclara/services/email/monitor.py mypalclara/gateway/__main__.py tests/services/email tests/architecture/test_engine_boundary.py
git commit -m "refactor(email): decouple monitor from discord; route alerts over gateway WS"
```

---

## Task 6: Externalize adapter spawning

The engine must not import `mypalclara.adapters`. Make `adapter_manager.py` discover adapters from YAML config only (no manifest import), default the gateway to external adapters, and move the dev-only multi-adapter launcher to the client side.

**Files:**
- Modify: `mypalclara/gateway/adapter_manager.py`
- Create: `mypalclara/adapters/cli/launch_adapters.py`
- Modify: `mypalclara/gateway/__main__.py` (default to no in-process spawning)
- Modify: `tests/architecture/test_engine_boundary.py` (shrink allowlist)
- Create: `tests/gateway/test_adapter_manager_no_manifest.py`

- [ ] **Step 1: Write the failing test**

Create `tests/gateway/test_adapter_manager_no_manifest.py`:

```python
"""adapter_manager must not import mypalclara.adapters (engine standalone)."""

import ast
import pathlib

import mypalclara.gateway.adapter_manager as am


def test_adapter_manager_does_not_import_adapters_package():
    src = pathlib.Path(am.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
        elif isinstance(node, ast.Import):
            modules.update(a.name for a in node.names)
    offenders = {m for m in modules if m == "mypalclara.adapters" or m.startswith("mypalclara.adapters.")}
    assert not offenders, f"adapter_manager imports adapters: {offenders}"


def test_adapter_manager_loads_yaml_config(tmp_path):
    cfg = tmp_path / "adapters.yaml"
    cfg.write_text(
        "adapters:\n"
        "  discord:\n"
        "    enabled: true\n"
        "    module: mypalclara.adapters.discord\n"
    )
    mgr = am.AdapterManager(config_path=cfg)
    configs = mgr.load_config()
    assert "discord" in configs
    assert configs["discord"].module == "mypalclara.adapters.discord"
```

Run: `poetry run pytest tests/gateway/test_adapter_manager_no_manifest.py -v`
Expected: FAIL — `test_adapter_manager_does_not_import_adapters_package` fails (manifest import present).

> If `tests/gateway/__init__.py` does not exist, create it as an empty file first.

- [ ] **Step 2: Remove the manifest import and its uses**

In `mypalclara/gateway/adapter_manager.py`:

Delete line 23: `from mypalclara.adapters.manifest import get_adapter, list_adapters`.

Delete the three manifest-dependent methods entirely: `discover_from_manifest` (lines ~212–238), `check_adapter_env` (lines ~240–259), and `get_adapter_manifest` (lines ~261–285).

In `start_adapter`, remove the env precheck that called `check_adapter_env` (lines ~357–362) — the adapter subprocess validates its own env on startup. Delete this block:

```python
        # Check required environment variables
        env_ok, missing = self.check_adapter_env(name)
        if not env_ok:
            logger.error(f"Adapter {name} missing required environment variables: {', '.join(missing)}")
            ap.state = AdapterState.FAILED
            return False
```

In `get_status`, remove the manifest enrichment (lines ~510–519) — delete this block:

```python
            # Include manifest info if available
            manifest_info = self.get_adapter_manifest(name)
            if manifest_info:
                adapter_status["manifest"] = manifest_info
                # Check env vars
                env_ok, missing = self.check_adapter_env(name)
                adapter_status["env_configured"] = env_ok
                if not env_ok:
                    adapter_status["missing_env"] = missing
```

- [ ] **Step 3: Run the adapter_manager test**

Run: `poetry run pytest tests/gateway/test_adapter_manager_no_manifest.py -v`
Expected: PASS (no adapters import; YAML config still loads).

- [ ] **Step 4: Default the gateway to external adapters**

In `mypalclara/gateway/__main__.py`, find where the adapter manager is started (around line 525, guarded by `if adapter_names is None or adapter_names:`). Change the default so that with no explicit `--adapter` selection the gateway runs **without** spawning adapters in-process. Replace that guard so in-process spawning happens only when adapters are explicitly named:

```python
    # Standalone-engine default: do NOT spawn adapters in-process. Adapters are
    # external WebSocket clients. In-process spawning is a dev convenience, opted
    # into by explicitly naming adapters (e.g. `--adapter discord`).
    adapter_manager = None
    if adapter_names:
        from mypalclara.gateway.adapter_manager import get_adapter_manager

        adapter_manager = get_adapter_manager()
        await adapter_manager.start(adapter_names)
```

> If the existing code references `_start_adapter_directly` / `mypalclara.adapters` anywhere in `__main__.py`, remove those engine-side spawn paths. Confirm with: `grep -n "mypalclara.adapters" mypalclara/gateway/__main__.py` → expected: no output.

- [ ] **Step 5: Add the client-side dev launcher**

Create `mypalclara/adapters/cli/launch_adapters.py` (lives on the client side, so it may import `mypalclara.adapters.manifest`):

```python
"""Dev-only launcher: start multiple adapters as local subprocesses.

This is a developer convenience that used to live inside the gateway. The
standalone engine no longer spawns adapters; run this alongside the engine to
bring up adapters locally. Production runs each adapter as its own service.
"""

from __future__ import annotations

import subprocess
import sys

from mypalclara.adapters.manifest import list_adapters


def main(argv: list[str] | None = None) -> None:
    names = (argv or sys.argv[1:]) or list_adapters()
    procs: list[subprocess.Popen] = []
    for name in names:
        cmd = [sys.executable, "-m", f"mypalclara.adapters.{name}"]
        print(f"[launch] starting {name}: {' '.join(cmd)}")
        procs.append(subprocess.Popen(cmd))
    try:
        for p in procs:
            p.wait()
    except KeyboardInterrupt:
        for p in procs:
            p.terminate()


if __name__ == "__main__":
    main()
```

- [ ] **Step 6: Shrink the engine allowlist**

In `tests/architecture/test_engine_boundary.py`, remove `"gateway/adapter_manager.py"` from `KNOWN_VIOLATIONS`.

- [ ] **Step 7: Run boundary + gateway tests + import smoke**

Run:
```bash
poetry run pytest tests/architecture/test_engine_boundary.py tests/gateway/test_adapter_manager_no_manifest.py -v
poetry run python -c "import mypalclara.gateway.__main__; import mypalclara.gateway.adapter_manager; print('gateway OK')"
```
Expected: all PASS; `gateway OK`.

- [ ] **Step 8: Commit**

```bash
git add mypalclara/gateway/adapter_manager.py mypalclara/gateway/__main__.py mypalclara/adapters/cli/launch_adapters.py tests/gateway tests/architecture/test_engine_boundary.py
git commit -m "refactor(gateway): externalize adapter spawning; engine no longer imports adapters"
```

---

## Task 7: Discord-free logging sink (inject the embed renderer)

`DiscordLogHandler.send_embed` is the only literal `import discord` left in `config/logging.py`. Move the `discord.Embed` construction out of the engine: the handler queues a plain embed-descriptor dict and delegates rendering to an injected callable that the Discord adapter provides.

**Files:**
- Modify: `mypalclara/config/logging.py`
- Modify: the caller of `init_discord_logging` (find it — see Step 1)
- Modify: `tests/architecture/test_engine_boundary.py` (shrink allowlist)
- Create: `tests/core/test_logging_no_discord.py`

- [ ] **Step 1: Locate the `init_discord_logging` caller**

Run:
```bash
grep -rn "init_discord_logging" mypalclara --include="*.py" | grep -v "def init_discord_logging"
```
Note the caller file/line (expected under `mypalclara/adapters/discord/`). You will update it in Step 5.

- [ ] **Step 2: Write the failing test**

Create `tests/core/test_logging_no_discord.py`:

```python
"""config/logging.py must contain no `import discord` (engine is SDK-free)."""

import ast
import pathlib

import mypalclara.config.logging as clog


def test_logging_module_has_no_discord_import():
    src = pathlib.Path(clog.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert all(a.name.split(".")[0] != "discord" for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            assert node.module.split(".")[0] != "discord"
```

Run: `poetry run pytest tests/core/test_logging_no_discord.py -v`
Expected: FAIL (the lazy `import discord` in `send_embed`).

> Create `tests/core/__init__.py` (empty) if it does not already exist.

- [ ] **Step 3: Add an injectable embed renderer to the handler**

In `mypalclara/config/logging.py`, in `DiscordLogHandler.__init__` (lines 264–271) add a renderer slot:

```python
        self._embed_renderer = None  # set by set_bot(); (descriptor: dict) -> Any
```

Change `set_bot` (lines 273–278) to accept an optional renderer:

```python
    def set_bot(self, bot, channel_id: int, loop, embed_renderer=None):
        """Set the Discord bot client and start the background task.

        Args:
            embed_renderer: optional callable(descriptor: dict) -> platform embed.
                Provided by the Discord adapter so this engine module never imports discord.
        """
        self._bot = bot
        self._channel_id = channel_id
        self._loop = loop
        self._embed_renderer = embed_renderer
        self._task = loop.create_task(self._worker())
```

- [ ] **Step 4: Queue descriptors instead of building `discord.Embed`**

Replace the body of `send_embed` (lines 388–442) so it queues a plain descriptor — no `import discord`:

```python
    async def send_embed(
        self,
        title: str,
        description: str | None = None,
        color: int | None = None,
        fields: list[dict] | None = None,
        footer: str | None = None,
        tag: str | None = None,
    ):
        """Queue a rich embed (as a plain descriptor) for the log channel."""
        if not self._bot or not self._channel_id:
            return
        if color is None:
            color = self.TAG_EMBED_COLORS.get(tag, 0x5865F2)
        descriptor = {
            "title": title,
            "description": description,
            "color": color,
            "fields": fields or [],
            "footer": footer,
        }
        try:
            self._queue.put_nowait({"embed_data": descriptor})
        except Exception:
            pass  # Drop if queue full
```

Update `_send_items` (lines 308–329) to render via the injected callable, replacing the `if item.get("embed"):` branch:

```python
            for item in items:
                try:
                    if item.get("embed_data") is not None:
                        if self._embed_renderer is None:
                            # No renderer (non-discord deploy): fall back to title text.
                            await channel.send(item["embed_data"].get("title", ""))
                        else:
                            embed = self._embed_renderer(item["embed_data"])
                            await channel.send(embed=embed)
                    else:
                        msg = item.get("message", "")
                        if len(msg) > 1990:
                            msg = msg[:1990] + "..."
                        await channel.send(msg)
                except Exception as e:
                    print(f"[logging] Failed to send to Discord: {e}", file=sys.stderr)
```

- [ ] **Step 5: Provide the renderer from the Discord adapter**

At the `init_discord_logging` caller found in Step 1, pass an `embed_renderer` into the call chain. `init_discord_logging(bot, channel_id, loop)` (logging.py:529) calls `_discord_handler.set_bot(bot, channel_id, loop)` at line 553 — update that to forward a renderer the adapter supplies. Simplest: add an optional param to `init_discord_logging` and define the renderer in the Discord adapter:

In `mypalclara/config/logging.py`, change the `init_discord_logging` signature and its `set_bot` call:

```python
def init_discord_logging(bot, channel_id: int, loop, embed_renderer=None) -> "DiscordLogHandler | None":
    ...
    _discord_handler.set_bot(bot, channel_id, loop, embed_renderer=embed_renderer)
```

At the adapter caller, pass a renderer that builds the real embed (this file is client-side and may import discord):

```python
    import discord
    from datetime import datetime, timezone

    def _render_log_embed(d: dict) -> discord.Embed:
        embed = discord.Embed(
            title=d.get("title"),
            description=d.get("description"),
            color=discord.Color(d.get("color", 0x5865F2)),
            timestamp=datetime.now(timezone.utc),
        )
        for f in d.get("fields", []):
            embed.add_field(name=f.get("name", ""), value=f.get("value", ""), inline=f.get("inline", True))
        if d.get("footer"):
            embed.set_footer(text=d["footer"])
        return embed

    init_discord_logging(bot, channel_id, loop, embed_renderer=_render_log_embed)
```

- [ ] **Step 6: Shrink the engine allowlist**

In `tests/architecture/test_engine_boundary.py`, remove `"config/logging.py"` from `KNOWN_VIOLATIONS`.

- [ ] **Step 7: Run tests + import smoke**

Run:
```bash
poetry run pytest tests/core/test_logging_no_discord.py tests/architecture/test_engine_boundary.py -v
poetry run python -c "import mypalclara.config.logging; print('logging OK')"
```
Expected: all PASS; `logging OK`.

- [ ] **Step 8: Commit**

```bash
git add mypalclara/config/logging.py mypalclara/adapters tests/core tests/architecture/test_engine_boundary.py
git commit -m "refactor(logging): inject embed renderer so engine logging never imports discord"
```

---

## Task 8: Proactive send path → `send_fn` callback

`services/proactive/engine.py` has a `TYPE_CHECKING` discord import and a `send_proactive_message(client, …)` path that branches on discord channel formats. Refactor to an injected `send_fn`, matching heartbeat/email. The path is dormant and its callers are already arg-inconsistent, so this is low-risk.

**Files:**
- Modify: `mypalclara/services/proactive/engine.py`
- Modify: `mypalclara/services/blog/scheduled.py` (caller)
- Modify: `tests/architecture/test_engine_boundary.py` (shrink allowlist — last entry)

- [ ] **Step 1: Remove the TYPE_CHECKING discord import**

In `mypalclara/services/proactive/engine.py`, delete the block at lines 39–40:

```python
if TYPE_CHECKING:
    from discord import Client
```

If `TYPE_CHECKING` becomes unused, remove it from the `typing` import line as well. Confirm:
```bash
grep -n "discord\|TYPE_CHECKING" mypalclara/services/proactive/engine.py
```
Expected: no `discord` references remain (TYPE_CHECKING only if still used elsewhere).

- [ ] **Step 2: Refactor `send_proactive_message` to take `send_fn`**

Replace the `send_proactive_message` definition (lines ~1667–1716) with a transport-agnostic version that delegates delivery:

```python
async def send_proactive_message(
    send_fn: "Callable[[str | None, str, str], Awaitable[bool]]",
    user_id: str | None,
    channel_id: str,
    message: str,
    purpose: str = "",
) -> bool:
    """Deliver a proactive message via send_fn and record it.

    Args:
        send_fn: async (user_id, channel_id, message) -> delivered: bool
            Provided by the gateway; routes over the adapter WebSocket.
    """
    try:
        delivered = await send_fn(user_id, channel_id, message)
        if not delivered:
            logger.warning(f"Proactive delivery failed for channel {channel_id}")
            return False

        with SessionLocal() as session:
            record = ProactiveMessage(
                user_id=user_id,
                channel_id=channel_id,
                message=message,
                priority="normal",
                reason=purpose,
            )
            session.add(record)
            session.commit()

        if user_id:
            _persist_proactive_to_history(user_id, channel_id, message)

        logger.info(f"Sent proactive message to {user_id}: {message[:50]}...")
        return True
    except Exception as e:
        logger.error(f"Failed to send proactive message: {e}")
        return False
```

Add to the typing imports at the top of the file:

```python
from collections.abc import Awaitable, Callable
```

- [ ] **Step 3: Update the internal caller in `process_user`**

At the internal call site (line ~1795), `process_user` must receive and forward a `send_fn`. Add a `send_fn` parameter to `process_user`'s signature and change the call from `client=client` to `send_fn=send_fn`:

```python
        success = await send_proactive_message(
            send_fn=send_fn,
            user_id=user_id,
            channel_id=context.last_interaction_channel,
            message=decision.message,
            purpose=decision.message_purpose or decision.reasoning,
        )
```

> Thread `send_fn` from `process_user`'s caller (the ORS loop entry, currently unwired) the same way; since the loop is not yet started by the gateway, no gateway change is required in this task.

- [ ] **Step 4: Update the blog announce caller**

In `mypalclara/services/blog/scheduled.py`, `_announce` calls `send_proactive_message` without a sender. Since the blog announce path is dormant and has no WS context here, make it a no-op-safe log instead of a broken call. Replace the `try` body of `_announce` (lines ~62–80) with:

```python
        link = post.get("wordpress", {}).get("link", "")
        title = post.get("title", "New post")
        topic = post.get("topic", "")
        message = (
            f"I just published a new blog post: **{title}**\n\n"
            f"{topic}\n\n"
            f"Read it here: {link}"
        )
        # Delivery is wired when the proactive/ORS loop is activated in the gateway.
        # Until then, log the announcement rather than calling an unbound sender.
        logger.info(f"Blog announcement ready for {BLOG_ANNOUNCE_CHANNEL}: {message[:80]}...")
```

Remove the now-unused import line inside `_announce`: `from mypalclara.services.proactive.engine import send_proactive_message`.

- [ ] **Step 5: Empty the engine allowlist**

In `tests/architecture/test_engine_boundary.py`, remove `"services/proactive/engine.py"` from `KNOWN_VIOLATIONS`. It should now be:

```python
KNOWN_VIOLATIONS: set[str] = set()
```

- [ ] **Step 6: Run boundary tests + import smoke**

Run:
```bash
poetry run pytest tests/architecture/test_engine_boundary.py -v
poetry run python -c "import mypalclara.services.proactive.engine; import mypalclara.services.blog.scheduled; print('proactive+blog OK')"
```
Expected: all PASS — `KNOWN_VIOLATIONS` is empty and the engine has zero platform-SDK / adapters imports; `proactive+blog OK`.

- [ ] **Step 7: Commit**

```bash
git add mypalclara/services/proactive/engine.py mypalclara/services/blog/scheduled.py tests/architecture/test_engine_boundary.py
git commit -m "refactor(proactive): inject send_fn; remove discord from engine send path"
```

---

## Task 9: Pin the engine/client path inventory (Phase 2 input)

Produce the authoritative engine-set vs client-set path list that becomes the `git filter-repo` spec for Phase 2, derived from the now-green boundary test.

**Files:**
- Create: `docs/superpowers/specs/2026-06-03-engine-path-inventory.md`

- [ ] **Step 1: Verify the full suite is green and the engine is SDK/adapters-free**

Run:
```bash
poetry run pytest tests/architecture -v
poetry run python - <<'PY'
import ast, pathlib
root = pathlib.Path("mypalclara")
bad = []
for pkg in ["core","db","config","sandbox","tools","gateway","services/proactive","services/blog","services/email"]:
    for p in (root/pkg).rglob("*.py"):
        t = ast.parse(p.read_text(encoding="utf-8"))
        mods=set()
        for n in ast.walk(t):
            if isinstance(n, ast.Import): mods|={a.name for a in n.names}
            elif isinstance(n, ast.ImportFrom) and n.module and n.level==0: mods.add(n.module)
        if any(m.split(".")[0]=="discord" or m.startswith("mypalclara.adapters") for m in mods):
            bad.append(str(p))
print("VIOLATIONS:", bad)
PY
```
Expected: architecture tests PASS; `VIOLATIONS: []`.

- [ ] **Step 2: Write the inventory doc**

Create `docs/superpowers/specs/2026-06-03-engine-path-inventory.md`:

```markdown
# Engine ↔ Client Path Inventory (Phase 2 filter-repo spec)

Locked after Phase 1; the engine import-boundary test (`tests/architecture/test_engine_boundary.py`) is green.

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
```

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/specs/2026-06-03-engine-path-inventory.md
git commit -m "docs(spec): pin engine/client path inventory for Phase 2 extraction"
```

---

## Phase 2 handoff (separate plan)

After this plan lands, write the Phase 2 plan covering: `git filter-repo` split using the inventory above, publishing `mypal_protocol` from `mypal-engine`, deployment/CI split, and the mandatory-`CLARA_GATEWAY_SECRET` WS hardening (`wss`/TLS, per-adapter identity). Phase 2 is mechanical because Phase 1 made the engine subgraph self-contained and test-locked.

## Final verification (run before declaring Phase 1 done)

```bash
poetry run ruff check . && poetry run ruff format --check .
poetry run pytest tests/architecture tests/gateway tests/services/email tests/core -v
poetry run python -c "import mypalclara.gateway.__main__; print('engine entrypoint imports clean')"
```
Expected: ruff clean; all listed tests PASS; entrypoint imports without pulling discord/adapters.
