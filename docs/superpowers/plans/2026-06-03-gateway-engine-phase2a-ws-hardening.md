# Gateway → mypal-engine Phase 2a: WS Hardening + Protocol Packaging

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the gateway WebSocket boundary authenticated — `CLARA_GATEWAY_SECRET` becomes **mandatory** for adapter registration, each registered adapter gets a server-issued identity token — and package `mypal_protocol` as an installable distribution, all in the current repo and test-locked.

**Architecture:** Phase 2a of the extraction ([spec](../specs/2026-06-03-gateway-engine-extraction-design.md)). This is the safe, in-repo half of Phase 2; the irreversible `git filter-repo` split + deploy/CI (Phase 2b) follows separately. Today `CLARA_GATEWAY_SECRET` is read at `server.py:78` but never checked on the WS path — any client can register as any `node_id`. We add: (1) a `secret` field on `RegisterMessage`, validated server-side with the server refusing to start without a configured secret; (2) a server-issued `adapter_token` returned in `RegisteredMessage` and stored on the node; (3) adapters sending the secret from env.

**Tech Stack:** Python 3.11+, Poetry (`package-mode = false`), pytest (`asyncio_mode = "auto"`), websockets, Pydantic v2, ruff.

> **Breaking change:** after this lands, every adapter/web-UI deployment MUST set `CLARA_GATEWAY_SECRET`, and the gateway refuses to start without it. Documented in Task 6.

## Scope

**In scope (Phase 2a):** mypal_protocol packaging; mandatory shared secret on WS register; reject unauthenticated registers (error + close); server-issued per-adapter `adapter_token` (stored + returned + logged); adapters send the secret; env/docs updates.

**Out of scope (later):** per-message `adapter_token` enforcement (the websocket↔node binding already authenticates post-register messages); distinct per-adapter secrets/rotation; `wss`/TLS termination (handled at the deploy/proxy layer in Phase 2b); the repo split itself.

## File structure

```
mypal_protocol/
  pyproject.toml        # NEW — declares the installable `mypal-protocol` distribution
  py.typed              # NEW — PEP 561 typing marker
  __init__.py           # (exists)
  messages.py           # MODIFY — add secret to RegisterMessage, adapter_token to RegisteredMessage
mypalclara/
  gateway/
    server.py           # MODIFY — require secret at start(); validate on register; issue adapter_token
    session.py          # MODIFY — NodeConnection.adapter_token + register() param
  adapters/
    base.py             # MODIFY — send secret from env; store adapter_token from response
.env.docker.example     # MODIFY — secret now required
docker-compose.yml      # MODIFY — fail if secret unset
CLAUDE.md               # MODIFY — document mandatory secret
tests/gateway/
  test_ws_auth.py       # NEW — secret enforcement + adapter_token
  test_session.py       # (exists) — extended for adapter_token
mypal_protocol_tests/   # (none) — protocol tested via tests/gateway + a packaging test
tests/protocol/
  test_packaging.py     # NEW — mypal_protocol imports as a package + has metadata
```

---

## Task 1: Package `mypal_protocol` as an installable distribution

Add packaging metadata so Phase 2b can publish `mypal-protocol` from the engine repo. Does not disturb the run-from-source flow (repo root stays on `sys.path`).

**Files:**
- Create: `mypal_protocol/pyproject.toml`, `mypal_protocol/py.typed`
- Create: `tests/protocol/__init__.py`, `tests/protocol/test_packaging.py`

- [ ] **Step 1: Write the failing packaging test**

Create `tests/protocol/__init__.py` (empty) and `tests/protocol/test_packaging.py`:

```python
"""mypal_protocol must be an importable, typed, standalone package."""

import pathlib

import mypal_protocol


def test_package_exposes_core_messages():
    assert hasattr(mypal_protocol, "RegisterMessage")
    assert hasattr(mypal_protocol, "RegisteredMessage")
    assert hasattr(mypal_protocol, "MessageType")


def test_package_has_distribution_metadata():
    root = pathlib.Path(mypal_protocol.__file__).resolve().parent
    assert (root / "pyproject.toml").exists(), "mypal_protocol must declare a pyproject.toml"
    assert (root / "py.typed").exists(), "mypal_protocol must ship a py.typed marker"


def test_package_is_self_contained():
    # The wire contract must not import engine/db code.
    import ast

    src = (pathlib.Path(mypal_protocol.__file__).resolve().parent / "messages.py").read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert not node.module.startswith("mypalclara"), f"protocol imports engine code: {node.module}"
```

Run: `poetry run pytest tests/protocol/test_packaging.py -v`
Expected: FAIL — `test_package_has_distribution_metadata` fails (no pyproject.toml / py.typed yet).

- [ ] **Step 2: Add the py.typed marker**

Create `mypal_protocol/py.typed` (empty file):

```python
```

- [ ] **Step 3: Add the distribution pyproject**

Create `mypal_protocol/pyproject.toml`:

```toml
[project]
name = "mypal-protocol"
version = "0.1.0"
description = "Wire-protocol message models shared between mypal-engine and its clients."
requires-python = ">=3.11,<3.14"
license = { text = "PolyForm-Noncommercial-1.0.0" }
dependencies = [
    "pydantic>=2,<3",
]

[project.urls]
Homepage = "https://github.com/BangRocket/mypalclara"

[tool.setuptools]
packages = ["mypal_protocol"]
package-dir = { mypal_protocol = "." }

[tool.setuptools.package-data]
mypal_protocol = ["py.typed"]

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"
```

- [ ] **Step 4: Run the packaging test**

Run: `poetry run pytest tests/protocol/test_packaging.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Confirm the root app still runs from source**

Run: `poetry run python -c "import mypal_protocol; import mypalclara.gateway.server; print('imports OK')"`
Expected: `imports OK`

- [ ] **Step 6: Commit**

```bash
git add mypal_protocol/pyproject.toml mypal_protocol/py.typed tests/protocol
git commit -m "build(protocol): package mypal_protocol as an installable distribution"
```

---

## Task 2: Add `secret` to RegisterMessage and `adapter_token` to RegisteredMessage

**Files:**
- Modify: `mypal_protocol/messages.py`
- Create: `tests/protocol/test_auth_fields.py`

- [ ] **Step 1: Write the failing model test**

Create `tests/protocol/test_auth_fields.py`:

```python
"""RegisterMessage carries an optional secret; RegisteredMessage carries adapter_token."""

from mypal_protocol import RegisteredMessage, RegisterMessage


def test_register_message_accepts_secret():
    msg = RegisterMessage(node_id="discord-abc", platform="discord", secret="s3cr3t")
    assert msg.secret == "s3cr3t"


def test_register_message_secret_defaults_none():
    msg = RegisterMessage(node_id="discord-abc", platform="discord")
    assert msg.secret is None


def test_registered_message_carries_adapter_token():
    msg = RegisteredMessage(node_id="discord-abc", session_id="gw-1", adapter_token="adp-xyz")
    assert msg.adapter_token == "adp-xyz"
```

Run: `poetry run pytest tests/protocol/test_auth_fields.py -v`
Expected: FAIL — `secret` and `adapter_token` fields don't exist.

- [ ] **Step 2: Add the fields**

In `mypal_protocol/messages.py`, in `RegisterMessage` (after the `metadata` field), add:

```python
    secret: str | None = Field(
        default=None,
        description="Shared gateway secret for authentication (CLARA_GATEWAY_SECRET)",
    )
```

In `RegisteredMessage` (after `server_time`), add:

```python
    adapter_token: str | None = Field(
        default=None,
        description="Server-issued per-connection identity token",
    )
```

- [ ] **Step 3: Run the test**

Run: `poetry run pytest tests/protocol/test_auth_fields.py -v`
Expected: PASS (3 tests).

- [ ] **Step 4: Commit**

```bash
git add mypal_protocol/messages.py tests/protocol/test_auth_fields.py
git commit -m "feat(protocol): add register secret + server-issued adapter_token fields"
```

---

## Task 3: NodeConnection stores `adapter_token`

**Files:**
- Modify: `mypalclara/gateway/session.py`
- Modify: `tests/gateway/test_session.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/gateway/test_session.py` (inside the existing test class that has `node_registry`/`mock_websocket` fixtures — match the existing fixture names):

```python
    @pytest.mark.asyncio
    async def test_register_stores_adapter_token(self, node_registry, mock_websocket):
        """Should persist a server-issued adapter_token on the node."""
        session_id, _ = await node_registry.register(
            websocket=mock_websocket,
            node_id="node-tok",
            platform="discord",
            adapter_token="adp-deadbeef",
        )
        node = await node_registry.get_node("node-tok")
        assert node is not None
        assert node.adapter_token == "adp-deadbeef"
```

Run: `poetry run pytest tests/gateway/test_session.py -k adapter_token -v`
Expected: FAIL — `register()` has no `adapter_token` param / `NodeConnection` has no such field.

- [ ] **Step 2: Add the field and param**

In `mypalclara/gateway/session.py`, add to the `NodeConnection` dataclass (after `metadata`):

```python
    adapter_token: str | None = None
```

In `NodeRegistry.register`, add `adapter_token: str | None = None` to the signature (after `metadata`), and set it when constructing the `NodeConnection`. Find where the `NodeConnection(...)` is built in `register()` and add `adapter_token=adapter_token` to its kwargs.

- [ ] **Step 3: Run the test**

Run: `poetry run pytest tests/gateway/test_session.py -k adapter_token -v`
Expected: PASS.

- [ ] **Step 4: Run the full session suite (no regressions)**

Run: `poetry run pytest tests/gateway/test_session.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add mypalclara/gateway/session.py tests/gateway/test_session.py
git commit -m "feat(gateway): track server-issued adapter_token on NodeConnection"
```

---

## Task 4: Server requires the secret and authenticates registration

**Files:**
- Modify: `mypalclara/gateway/server.py`
- Create: `tests/gateway/test_ws_auth.py`

- [ ] **Step 1: Write the failing auth tests**

Create `tests/gateway/test_ws_auth.py`:

```python
"""Gateway WebSocket registration must require a matching CLARA_GATEWAY_SECRET."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from mypalclara.gateway.server import GatewayServer
from mypal_protocol import RegisterMessage


def _ws():
    ws = MagicMock()
    ws.send = AsyncMock()
    ws.close = AsyncMock()
    return ws


@pytest.mark.asyncio
async def test_start_without_secret_raises(monkeypatch):
    monkeypatch.delenv("CLARA_GATEWAY_SECRET", raising=False)
    server = GatewayServer(host="127.0.0.1", port=0, secret=None)
    with pytest.raises(RuntimeError, match="CLARA_GATEWAY_SECRET"):
        await server.start()


@pytest.mark.asyncio
async def test_register_rejected_without_matching_secret():
    server = GatewayServer(host="127.0.0.1", port=0, secret="right")
    ws = _ws()
    msg = RegisterMessage(node_id="discord-1", platform="discord", secret="wrong")

    result = await server._handle_register(ws, msg)

    assert result is None
    ws.close.assert_awaited()  # connection closed on auth failure
    node = await server.node_registry.get_node("discord-1")
    assert node is None  # not registered
    # an error frame was sent
    sent = [json.loads(c.args[0]) for c in ws.send.await_args_list]
    assert any(f.get("code") == "auth_failed" for f in sent)


@pytest.mark.asyncio
async def test_register_succeeds_and_issues_token():
    server = GatewayServer(host="127.0.0.1", port=0, secret="right")
    ws = _ws()
    msg = RegisterMessage(node_id="discord-1", platform="discord", secret="right")

    result = await server._handle_register(ws, msg)

    assert result == "discord-1"
    node = await server.node_registry.get_node("discord-1")
    assert node is not None
    assert node.adapter_token and node.adapter_token.startswith("adp-")
    sent = [json.loads(c.args[0]) for c in ws.send.await_args_list]
    registered = [f for f in sent if f.get("type") == "registered"]
    assert registered and registered[0]["adapter_token"] == node.adapter_token
```

Run: `poetry run pytest tests/gateway/test_ws_auth.py -v`
Expected: FAIL — server doesn't require/validate the secret yet.

- [ ] **Step 2: Require the secret at server start**

In `mypalclara/gateway/server.py`, at the top of `start()` (before `serve(...)`), add:

```python
        if not self.secret:
            raise RuntimeError(
                "CLARA_GATEWAY_SECRET is required to start the gateway. "
                "Set it in the environment (and configure adapters with the same value)."
            )
```

- [ ] **Step 3: Validate the secret and issue a token in `_handle_register`**

Replace the body of `_handle_register` with:

```python
    async def _handle_register(
        self,
        websocket: WebSocketServerProtocol,
        msg: RegisterMessage,
    ) -> str | None:
        """Handle adapter registration (requires a matching shared secret)."""
        if not self.secret or msg.secret != self.secret:
            logger.warning(f"Rejected registration from {msg.node_id} ({msg.platform}): bad secret")
            await self._send_error(
                websocket, None, "auth_failed", "Invalid or missing gateway secret", recoverable=False
            )
            await websocket.close(code=1008, reason="auth_failed")
            return None

        adapter_token = f"adp-{uuid.uuid4().hex[:16]}"
        session_id, is_reconnection = await self.node_registry.register(
            websocket=websocket,
            node_id=msg.node_id,
            platform=msg.platform,
            capabilities=msg.capabilities,
            metadata=msg.metadata,
            adapter_token=adapter_token,
        )

        response = RegisteredMessage(
            node_id=msg.node_id,
            session_id=session_id,
            adapter_token=adapter_token,
        )
        await self._send(websocket, response)

        action = "reconnected" if is_reconnection else "registered"
        logger.info(f"Node {msg.node_id} ({msg.platform}) {action} [token {adapter_token[:10]}…]")

        return msg.node_id
```

Confirm `uuid` is imported at the top of `server.py` (it is used by `session.py`; if `server.py` lacks `import uuid`, add it with the other stdlib imports).

- [ ] **Step 4: Run the auth tests**

Run: `poetry run pytest tests/gateway/test_ws_auth.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Lint + full gateway suite**

Run:
```bash
poetry run ruff check mypalclara/gateway/server.py tests/gateway/test_ws_auth.py
poetry run pytest tests/gateway -v
```
Expected: ruff clean; all gateway tests PASS.

- [ ] **Step 6: Commit**

```bash
git add mypalclara/gateway/server.py tests/gateway/test_ws_auth.py
git commit -m "feat(gateway): require CLARA_GATEWAY_SECRET on WS register; issue adapter_token"
```

---

## Task 5: Adapters send the secret and store the issued token

**Files:**
- Modify: `mypalclara/adapters/base.py`
- Create: `tests/adapters/test_register_secret.py`

- [ ] **Step 1: Write the failing test (isolating the register-message builder)**

Create `tests/adapters/test_register_secret.py`:

```python
"""Adapters include CLARA_GATEWAY_SECRET in the register message."""

import pytest

from mypalclara.adapters.base import BaseAdapter


class _Probe(BaseAdapter):
    """Minimal concrete adapter for testing the register builder."""

    platform = "probe"

    async def start(self):  # pragma: no cover - not exercised
        pass

    async def send_response(self, *a, **k):  # pragma: no cover
        pass


def test_build_register_includes_secret(monkeypatch):
    monkeypatch.setenv("CLARA_GATEWAY_SECRET", "shared-xyz")
    adapter = _Probe()
    msg = adapter._build_register_message()
    assert msg.secret == "shared-xyz"
    assert msg.platform == "probe"
    assert msg.node_id
```

> If `BaseAdapter`'s constructor requires arguments, match its real signature in `_Probe()` — inspect `mypalclara/adapters/base.py` for `__init__` and pass the minimal required values. If `BaseAdapter` defines abstract methods beyond those above, stub them in `_Probe` too.

Run: `poetry run pytest tests/adapters/test_register_secret.py -v`
Expected: FAIL — `_build_register_message` does not exist.

> Create `tests/adapters/__init__.py` (empty) if missing.

- [ ] **Step 2: Extract a register-message builder and use the secret**

In `mypalclara/adapters/base.py`, add a method on `BaseAdapter`:

```python
    def _build_register_message(self) -> "RegisterMessage":
        """Build the registration message, including the shared gateway secret."""
        return RegisterMessage(
            node_id=self.node_id,
            platform=self.platform,
            capabilities=self.capabilities,
            secret=os.getenv("CLARA_GATEWAY_SECRET", ""),
        )
```

In `connect()`, replace the inline `RegisterMessage(...)` construction with:

```python
        register = self._build_register_message()
        await self._ws.send(register.model_dump_json())
```

And after a successful `REGISTERED` response, store the issued token alongside `session_id`:

```python
            self.session_id = data.get("session_id")
            self.adapter_token = data.get("adapter_token")
```

Add `self.adapter_token: str | None = None` to `BaseAdapter.__init__` (near where `self.session_id` is initialized). Confirm `os` is imported in `base.py` (it is — used for `gateway_url`).

- [ ] **Step 3: Run the test**

Run: `poetry run pytest tests/adapters/test_register_secret.py -v`
Expected: PASS.

- [ ] **Step 4: Lint + import smoke**

Run:
```bash
poetry run ruff check mypalclara/adapters/base.py tests/adapters/test_register_secret.py
poetry run python -c "import mypalclara.adapters.base; print('adapter base OK')"
```
Expected: ruff clean; `adapter base OK`.

- [ ] **Step 5: Commit**

```bash
git add mypalclara/adapters/base.py tests/adapters/test_register_secret.py
git commit -m "feat(adapters): send CLARA_GATEWAY_SECRET on register; store adapter_token"
```

---

## Task 6: Make the secret mandatory in config + docs

**Files:**
- Modify: `.env.docker.example`
- Modify: `docker-compose.yml`
- Modify: `CLAUDE.md`

- [ ] **Step 1: Update the env example**

In `.env.docker.example`, change the gateway secret line from optional to required, e.g. replace:

```bash
# CLARA_GATEWAY_SECRET=...           # Optional shared secret for auth
```
with:
```bash
CLARA_GATEWAY_SECRET=change-me-to-a-long-random-string   # REQUIRED: shared secret; adapters must match. Generate: openssl rand -hex 32
```

- [ ] **Step 2: Make compose fail if unset**

In `docker-compose.yml`, change the gateway env line:

```yaml
      - CLARA_GATEWAY_SECRET=${CLARA_GATEWAY_SECRET:-}
```
to:
```yaml
      - CLARA_GATEWAY_SECRET=${CLARA_GATEWAY_SECRET:?CLARA_GATEWAY_SECRET must be set (shared with all adapters)}
```

Apply the same `CLARA_GATEWAY_SECRET=${CLARA_GATEWAY_SECRET:?...}` to any adapter services in `docker-compose.yml` that connect to the gateway (search the file for adapter service blocks and add the env line if missing).

- [ ] **Step 3: Update CLAUDE.md**

In `CLAUDE.md`, in the Gateway env section, change `CLARA_GATEWAY_SECRET=...          # Optional auth secret` to:

```bash
CLARA_GATEWAY_SECRET=...          # REQUIRED: shared secret for WS registration; all adapters must set the same value
```

- [ ] **Step 4: Verify compose still parses**

Run: `docker compose config >/dev/null && echo "compose OK"` (or `docker-compose config`).
Expected: with `CLARA_GATEWAY_SECRET` exported it prints `compose OK`; with it unset it errors with the guard message (that's the intended behavior).

> If Docker is unavailable in this environment, skip the runtime check and instead `grep -n "CLARA_GATEWAY_SECRET" docker-compose.yml` to confirm the `:?` guard is present.

- [ ] **Step 5: Commit**

```bash
git add .env.docker.example docker-compose.yml CLAUDE.md
git commit -m "docs(gateway): make CLARA_GATEWAY_SECRET required in env, compose, and docs"
```

---

## Final verification (run before declaring Phase 2a done)

```bash
poetry run ruff check .  # (pre-existing unrelated I001 in db/migrations is acceptable)
poetry run pytest tests/protocol tests/gateway tests/adapters/test_register_secret.py tests/architecture -v
poetry run python -c "import mypalclara.gateway.__main__; print('engine entrypoint OK')"
```
Expected: the listed tests PASS; engine entrypoint imports clean. The architecture boundary tests still pass (no new engine→client/SDK imports introduced).

## Phase 2b handoff (separate, confirmed step)

After 2a lands: `git filter-repo` split using `docs/superpowers/specs/2026-06-03-engine-path-inventory.md` into a new local `mypal-engine` repo **and** a new GitHub repo (name/visibility to be provided at that time); publish `mypal-protocol` from mypal-engine; split deploy/CI; `wss`/TLS at the proxy layer. This step rewrites history and creates external repos — confirm before running.
