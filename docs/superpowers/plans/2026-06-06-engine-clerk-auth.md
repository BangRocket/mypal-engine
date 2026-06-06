# Engine Clerk Auth Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the Clara web app authenticate directly to the engine with a Clerk JWT — over HTTP (`Authorization: Bearer`) and over the WebSocket (browser registration) — mapping the Clerk user to a `CanonicalUser`, reusing existing identity models.

**Architecture:** A small Clerk JWT verifier (PyJWT + JWKS) feeds two entry points: (1) the existing FastAPI `get_current_user` dependency gains a Bearer-token branch alongside the existing trusted `X-Canonical-User-Id` header; (2) the gateway WebSocket `_handle_register` gains a browser path that authenticates a JWT instead of the shared secret. Clerk identities become `PlatformLink(platform="clerk", prefixed_user_id="clerk-<sub>")` rows tied to a `CanonicalUser` — no schema change. Adapter auth (shared secret) is untouched.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy, PyJWT 2.8 (RS256 via `cryptography`), pytest + FastAPI `TestClient`. This is plan #1 of the Clara app web MVP (spec: `mypal/docs/superpowers/specs/2026-06-06-clara-app-web-mvp-design.md`); the frontend plan in `mypal` depends on it.

---

## File structure

- Create `mypalclara/gateway/api/clerk_auth.py` — Clerk JWT verification + `get_or_create_clerk_user` + the `get_clerk_user` FastAPI dependency. One responsibility: turn a Clerk JWT into a `CanonicalUser`.
- Modify `mypalclara/gateway/api/auth.py` — `get_current_user` gains a Bearer branch delegating to `clerk_auth`.
- Modify `mypal_protocol/messages.py` (the vendored protocol) — `RegisterMessage` gains optional `auth_token`.
- Modify `mypalclara/gateway/server.py` — `_handle_register` gains a browser/JWT branch.
- Create `tests/gateway/api/test_clerk_auth.py` and `tests/gateway/test_ws_clerk_auth.py`.

**Config (env):** `CLERK_JWKS_URL`, `CLERK_ISSUER`. CORS already lists `http://localhost:5173`; add prod origins later via `GATEWAY_API_CORS_ORIGINS` (no code change in MVP).

---

## Task 0: Confirm crypto dependency for RS256

**Files:**
- Modify: `pyproject.toml` (only if the check fails)

- [ ] **Step 1: Check that PyJWT can do RS256**

Run: `poetry run python -c "import jwt, cryptography; from cryptography.hazmat.primitives.asymmetric import rsa; print('ok', jwt.__version__)"`
Expected: prints `ok 2.x.x`. If `ModuleNotFoundError: cryptography`, continue to Step 2; otherwise skip to Task 1.

- [ ] **Step 2: Add the crypto extra (only if Step 1 failed)**

Run: `poetry add "PyJWT[crypto]@^2.8.0"`
Expected: `cryptography` resolved into the lockfile.

- [ ] **Step 3: Commit (only if changed)**

```bash
git add pyproject.toml poetry.lock
git commit -m "build: ensure PyJWT[crypto] for RS256 Clerk token verification"
```

---

## Task 1: Clerk JWT verifier

**Files:**
- Create: `mypalclara/gateway/api/clerk_auth.py`
- Test: `tests/gateway/api/test_clerk_auth.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/gateway/api/test_clerk_auth.py
"""Clerk JWT verification + user resolution for the web app."""

import datetime as dt

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from mypalclara.gateway.api import clerk_auth


@pytest.fixture
def rsa_keys():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    pub = key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return priv, pub


def _make_token(priv, *, sub="clerk_user_1", iss="https://clerk.test", exp_delta=3600, **extra):
    now = dt.datetime.now(dt.timezone.utc)
    payload = {"sub": sub, "iss": iss, "iat": now, "exp": now + dt.timedelta(seconds=exp_delta), **extra}
    return jwt.encode(payload, priv, algorithm="RS256")


def test_verify_valid_token_returns_claims(monkeypatch, rsa_keys):
    priv, pub = rsa_keys
    monkeypatch.setattr(clerk_auth, "_get_signing_key", lambda token: pub)
    monkeypatch.setenv("CLERK_ISSUER", "https://clerk.test")
    token = _make_token(priv, sub="clerk_abc", email="a@b.co")
    claims = clerk_auth.verify_clerk_jwt(token)
    assert claims["sub"] == "clerk_abc"
    assert claims["email"] == "a@b.co"


def test_verify_expired_token_raises(monkeypatch, rsa_keys):
    priv, pub = rsa_keys
    monkeypatch.setattr(clerk_auth, "_get_signing_key", lambda token: pub)
    monkeypatch.setenv("CLERK_ISSUER", "https://clerk.test")
    token = _make_token(priv, exp_delta=-10)
    with pytest.raises(clerk_auth.ClerkAuthError):
        clerk_auth.verify_clerk_jwt(token)


def test_verify_wrong_issuer_raises(monkeypatch, rsa_keys):
    priv, pub = rsa_keys
    monkeypatch.setattr(clerk_auth, "_get_signing_key", lambda token: pub)
    monkeypatch.setenv("CLERK_ISSUER", "https://clerk.test")
    token = _make_token(priv, iss="https://evil.test")
    with pytest.raises(clerk_auth.ClerkAuthError):
        clerk_auth.verify_clerk_jwt(token)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/gateway/api/test_clerk_auth.py -v`
Expected: FAIL — `ModuleNotFoundError: mypalclara.gateway.api.clerk_auth`.

- [ ] **Step 3: Write minimal implementation**

```python
# mypalclara/gateway/api/clerk_auth.py
"""Clerk JWT verification and Clerk → CanonicalUser resolution for the web app."""

from __future__ import annotations

import os

import jwt
from jwt import PyJWKClient

CLERK_PLATFORM = "clerk"


class ClerkAuthError(Exception):
    """Raised when a Clerk JWT is missing, malformed, expired, or untrusted."""


_jwk_client: PyJWKClient | None = None


def _get_signing_key(token: str) -> str:
    """Resolve the RS256 signing key (PEM) for a token via Clerk's JWKS.

    Cached PyJWKClient per process. Patched in tests.
    """
    global _jwk_client
    jwks_url = os.getenv("CLERK_JWKS_URL")
    if not jwks_url:
        raise ClerkAuthError("CLERK_JWKS_URL is not configured")
    if _jwk_client is None:
        _jwk_client = PyJWKClient(jwks_url, cache_keys=True)
    try:
        return _jwk_client.get_signing_key_from_jwt(token).key
    except Exception as e:  # network / unknown kid
        raise ClerkAuthError(f"could not resolve signing key: {e}") from e


def verify_clerk_jwt(token: str) -> dict:
    """Verify a Clerk-issued RS256 JWT and return its claims.

    Raises ClerkAuthError on any failure. Audience is not checked (Clerk
    session tokens carry no aud); issuer is checked when CLERK_ISSUER is set.
    """
    if not token:
        raise ClerkAuthError("empty token")
    issuer = os.getenv("CLERK_ISSUER")
    try:
        key = _get_signing_key(token)
        return jwt.decode(
            token,
            key,
            algorithms=["RS256"],
            issuer=issuer if issuer else None,
            options={"verify_aud": False, "require": ["exp", "sub"]},
        )
    except ClerkAuthError:
        raise
    except jwt.PyJWTError as e:
        raise ClerkAuthError(str(e)) from e
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/gateway/api/test_clerk_auth.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add mypalclara/gateway/api/clerk_auth.py tests/gateway/api/test_clerk_auth.py
git commit -m "feat(auth): Clerk JWT verifier (PyJWT + JWKS)"
```

---

## Task 2: Clerk → CanonicalUser resolution

**Files:**
- Modify: `mypalclara/gateway/api/clerk_auth.py`
- Test: `tests/gateway/api/test_clerk_auth.py` (add cases)

- [ ] **Step 1: Write the failing test**

```python
# append to tests/gateway/api/test_clerk_auth.py
from mypalclara.db.connection import SessionLocal
from mypalclara.db.models import Base, CanonicalUser, PlatformLink


@pytest.fixture
def db(monkeypatch, tmp_path):
    # Isolated SQLite db for this test module
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(f"sqlite:///{tmp_path}/t.db")
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)
    monkeypatch.setattr("mypalclara.gateway.api.clerk_auth.SessionLocal", TestSession, raising=False)
    s = TestSession()
    yield s
    s.close()


def test_get_or_create_creates_then_reuses(db):
    u1 = clerk_auth.get_or_create_clerk_user(db, sub="clerk_x", email="x@y.co", name="X", avatar=None)
    assert u1.id
    link = db.query(PlatformLink).filter_by(prefixed_user_id="clerk-clerk_x").one()
    assert link.platform == "clerk" and link.canonical_user_id == u1.id
    u2 = clerk_auth.get_or_create_clerk_user(db, sub="clerk_x", email="x@y.co", name="X", avatar=None)
    assert u2.id == u1.id
    assert db.query(CanonicalUser).count() == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/gateway/api/test_clerk_auth.py::test_get_or_create_creates_then_reuses -v`
Expected: FAIL — `AttributeError: module ... has no attribute 'get_or_create_clerk_user'`.

- [ ] **Step 3: Write minimal implementation**

```python
# add to mypalclara/gateway/api/clerk_auth.py
from sqlalchemy.orm import Session as DBSession

from mypalclara.db.connection import SessionLocal  # noqa: F401  (patched in tests)
from mypalclara.db.models import CanonicalUser, PlatformLink


def get_or_create_clerk_user(
    db: DBSession, *, sub: str, email: str | None, name: str | None, avatar: str | None
) -> CanonicalUser:
    """Return the CanonicalUser for a Clerk sub, creating it (and its
    PlatformLink) on first sight. The prefixed_user_id is ``clerk-<sub>``.
    """
    prefixed = f"clerk-{sub}"
    link = db.query(PlatformLink).filter(PlatformLink.prefixed_user_id == prefixed).first()
    if link:
        return db.query(CanonicalUser).filter(CanonicalUser.id == link.canonical_user_id).one()

    user = CanonicalUser(display_name=name or email or prefixed, primary_email=email, avatar_url=avatar)
    db.add(user)
    db.flush()  # assign user.id
    db.add(
        PlatformLink(
            canonical_user_id=user.id,
            platform=CLERK_PLATFORM,
            platform_user_id=sub,
            prefixed_user_id=prefixed,
            display_name=name,
            linked_via="clerk",
        )
    )
    db.commit()
    return user
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/gateway/api/test_clerk_auth.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add mypalclara/gateway/api/clerk_auth.py tests/gateway/api/test_clerk_auth.py
git commit -m "feat(auth): map Clerk sub to CanonicalUser via PlatformLink"
```

---

## Task 3: `get_clerk_user` FastAPI dependency

**Files:**
- Modify: `mypalclara/gateway/api/clerk_auth.py`
- Test: `tests/gateway/api/test_clerk_auth.py` (add cases)

- [ ] **Step 1: Write the failing test**

```python
# append to tests/gateway/api/test_clerk_auth.py
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient


def test_get_clerk_user_dependency(monkeypatch, rsa_keys, db):
    priv, pub = rsa_keys
    monkeypatch.setattr(clerk_auth, "_get_signing_key", lambda token: pub)
    monkeypatch.setenv("CLERK_ISSUER", "https://clerk.test")
    monkeypatch.setattr(clerk_auth, "get_db", lambda: iter([db]))

    app = FastAPI()

    @app.get("/me")
    async def me(user=Depends(clerk_auth.get_clerk_user)):
        return {"id": user.id, "name": user.display_name}

    client = TestClient(app)
    token = _make_token(priv, sub="clerk_dep", name="Dep", email="d@e.co")

    assert client.get("/me").status_code == 401  # no header
    r = client.get("/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200 and r.json()["name"] == "Dep"
    assert client.get("/me", headers={"Authorization": "Bearer garbage"}).status_code == 401
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/gateway/api/test_clerk_auth.py::test_get_clerk_user_dependency -v`
Expected: FAIL — `AttributeError: ... 'get_clerk_user'` / `'get_db'`.

- [ ] **Step 3: Write minimal implementation**

```python
# add to mypalclara/gateway/api/clerk_auth.py
from fastapi import Depends, Header, HTTPException, status


def get_db():
    """Yield a DB session (patched in tests)."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_clerk_user(
    authorization: str | None = Header(None),
    db: DBSession = Depends(get_db),
) -> CanonicalUser:
    """Resolve the CanonicalUser from a Clerk ``Authorization: Bearer`` token."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token")
    token = authorization.split(" ", 1)[1].strip()
    try:
        claims = verify_clerk_jwt(token)
    except ClerkAuthError as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=f"Invalid token: {e}") from e
    return get_or_create_clerk_user(
        db,
        sub=claims["sub"],
        email=claims.get("email"),
        name=claims.get("name") or claims.get("first_name"),
        avatar=claims.get("image_url") or claims.get("picture"),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/gateway/api/test_clerk_auth.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add mypalclara/gateway/api/clerk_auth.py tests/gateway/api/test_clerk_auth.py
git commit -m "feat(auth): get_clerk_user FastAPI dependency"
```

---

## Task 4: `get_current_user` accepts Clerk Bearer OR trusted header

**Files:**
- Modify: `mypalclara/gateway/api/auth.py`
- Test: `tests/gateway/api/test_clerk_auth.py` (add case)

- [ ] **Step 1: Write the failing test**

```python
# append to tests/gateway/api/test_clerk_auth.py
def test_get_current_user_prefers_bearer(monkeypatch, rsa_keys, db):
    from mypalclara.gateway.api import auth as auth_mod

    priv, pub = rsa_keys
    monkeypatch.setattr(clerk_auth, "_get_signing_key", lambda token: pub)
    monkeypatch.setenv("CLERK_ISSUER", "https://clerk.test")
    monkeypatch.setattr(clerk_auth, "get_db", lambda: iter([db]))
    monkeypatch.setattr(auth_mod, "get_db", lambda: iter([db]))

    app = FastAPI()

    @app.get("/who")
    async def who(user=Depends(auth_mod.get_current_user)):
        return {"id": user.id}

    client = TestClient(app)
    token = _make_token(priv, sub="clerk_cur", name="Cur")
    r = client.get("/who", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/gateway/api/test_clerk_auth.py::test_get_current_user_prefers_bearer -v`
Expected: FAIL — 401 (current `get_current_user` ignores `Authorization`, demands `X-Canonical-User-Id`).

- [ ] **Step 3: Write minimal implementation**

In `mypalclara/gateway/api/auth.py`, change the signature of `get_current_user` to also accept `authorization` and branch to Clerk when a Bearer token is present. Replace the existing `get_current_user` body's start:

```python
def get_current_user(
    x_canonical_user_id: str | None = Header(None),
    x_gateway_secret: str | None = Header(None),
    authorization: str | None = Header(None),
    db: DBSession = Depends(get_db),
) -> CanonicalUser:
    """Resolve the current user.

    Web app: ``Authorization: Bearer <clerk-jwt>`` (validated against Clerk).
    Adapters/Rails: trusted ``X-Canonical-User-Id`` header (+ optional secret).
    """
    if authorization and authorization.lower().startswith("bearer "):
        from mypalclara.gateway.api.clerk_auth import (
            ClerkAuthError,
            get_or_create_clerk_user,
            verify_clerk_jwt,
        )

        token = authorization.split(" ", 1)[1].strip()
        try:
            claims = verify_clerk_jwt(token)
        except ClerkAuthError as e:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail=f"Invalid token: {e}") from e
        return get_or_create_clerk_user(
            db,
            sub=claims["sub"],
            email=claims.get("email"),
            name=claims.get("name") or claims.get("first_name"),
            avatar=claims.get("image_url") or claims.get("picture"),
        )

    # --- existing trusted-header path below (unchanged) ---
```

Keep the rest of the original function (secret check, `X-Canonical-User-Id` lookup) exactly as-is after this block.

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/gateway/api/test_clerk_auth.py tests/gateway/api/test_auth_gateway_secret.py -v`
Expected: PASS (all). The existing header path still works.

- [ ] **Step 5: Commit**

```bash
git add mypalclara/gateway/api/auth.py tests/gateway/api/test_clerk_auth.py
git commit -m "feat(auth): accept Clerk Bearer in get_current_user (header path preserved)"
```

---

## Task 5: WebSocket browser/JWT registration

**Files:**
- Modify: `mypal_protocol/messages.py` (add `auth_token` to `RegisterMessage`)
- Modify: `mypalclara/gateway/server.py` (`_handle_register`)
- Test: `tests/gateway/test_ws_clerk_auth.py`

- [ ] **Step 1: Add the optional protocol field**

In `mypal_protocol/messages.py`, find `class RegisterMessage` and add a field (place it next to `secret`):

```python
    auth_token: str | None = None  # Clerk JWT for browser ("web") clients; adapters use `secret`
```

Run: `poetry run python -c "from mypal_protocol import RegisterMessage; print(RegisterMessage(node_id='n', platform='web', auth_token='t').auth_token)"`
Expected: prints `t`.

- [ ] **Step 2: Write the failing test**

```python
# tests/gateway/test_ws_clerk_auth.py
"""Gateway WebSocket: browser clients register with a Clerk JWT, not the secret."""

import datetime as dt
import json
from unittest.mock import AsyncMock, MagicMock

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from mypal_protocol import RegisterMessage
from mypalclara.gateway.api import clerk_auth
from mypalclara.gateway.server import GatewayServer


def _ws():
    ws = MagicMock()
    ws.send = AsyncMock()
    ws.close = AsyncMock()
    return ws


@pytest.fixture
def keys_and_db(monkeypatch, tmp_path):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    pub = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from mypalclara.db.models import Base

    engine = create_engine(f"sqlite:///{tmp_path}/ws.db")
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)
    monkeypatch.setattr(clerk_auth, "_get_signing_key", lambda token: pub)
    monkeypatch.setattr(clerk_auth, "SessionLocal", TestSession, raising=False)
    monkeypatch.setenv("CLERK_ISSUER", "https://clerk.test")
    return priv


def _token(priv, sub="clerk_ws"):
    now = dt.datetime.now(dt.timezone.utc)
    return jwt.encode(
        {"sub": sub, "iss": "https://clerk.test", "iat": now, "exp": now + dt.timedelta(hours=1)},
        priv,
        algorithm="RS256",
    )


@pytest.mark.asyncio
async def test_web_register_with_valid_jwt_succeeds(keys_and_db):
    priv = keys_and_db
    server = GatewayServer(host="127.0.0.1", port=0, secret="right")
    ws = _ws()
    msg = RegisterMessage(node_id="web-ignored", platform="web", auth_token=_token(priv, sub="abc"))

    result = await server._handle_register(ws, msg)

    assert result == "clerk-abc"  # node id derived from the Clerk user
    node = await server.node_registry.get_node("clerk-abc")
    assert node is not None
    sent = [json.loads(c.args[0]) for c in ws.send.await_args_list]
    assert any(f.get("type") == "registered" for f in sent)


@pytest.mark.asyncio
async def test_web_register_with_bad_jwt_rejected(keys_and_db):
    server = GatewayServer(host="127.0.0.1", port=0, secret="right")
    ws = _ws()
    msg = RegisterMessage(node_id="web", platform="web", auth_token="garbage")

    result = await server._handle_register(ws, msg)

    assert result is None
    ws.close.assert_awaited()
    sent = [json.loads(c.args[0]) for c in ws.send.await_args_list]
    assert any(f.get("code") == "auth_failed" for f in sent)


@pytest.mark.asyncio
async def test_adapter_secret_path_unchanged(keys_and_db):
    server = GatewayServer(host="127.0.0.1", port=0, secret="right")
    ws = _ws()
    msg = RegisterMessage(node_id="discord-1", platform="discord", secret="right")
    result = await server._handle_register(ws, msg)
    assert result == "discord-1"
```

- [ ] **Step 3: Run test to verify it fails**

Run: `poetry run pytest tests/gateway/test_ws_clerk_auth.py -v`
Expected: FAIL — web register currently hits the secret check and is rejected (`result is None`, node `clerk-abc` absent).

- [ ] **Step 4: Write minimal implementation**

In `mypalclara/gateway/server.py`, at the top of `_handle_register`, add the browser branch before the secret check:

```python
        # Browser clients (the web app) authenticate with a Clerk JWT, not the
        # shared secret. They register under a clerk-<sub> node id.
        if msg.platform == "web" or msg.auth_token:
            from mypalclara.gateway.api.clerk_auth import (
                ClerkAuthError,
                get_or_create_clerk_user,
                verify_clerk_jwt,
            )

            try:
                claims = verify_clerk_jwt(msg.auth_token or "")
            except ClerkAuthError as e:
                logger.warning(f"Rejected web registration: {e}")
                await self._send_error(websocket, None, "auth_failed", "Invalid Clerk token", recoverable=False)
                await websocket.close(code=1008, reason="auth_failed")
                return None

            db = clerk_auth.SessionLocal()
            try:
                user = get_or_create_clerk_user(
                    db,
                    sub=claims["sub"],
                    email=claims.get("email"),
                    name=claims.get("name") or claims.get("first_name"),
                    avatar=claims.get("image_url") or claims.get("picture"),
                )
                node_id = f"clerk-{claims['sub']}"
            finally:
                db.close()

            adapter_token = f"web-{uuid.uuid4().hex[:16]}"
            session_id, is_reconnection = await self.node_registry.register(
                websocket=websocket,
                node_id=node_id,
                platform="web",
                capabilities=msg.capabilities,
                metadata={**(msg.metadata or {}), "canonical_user_id": user.id},
                adapter_token=adapter_token,
            )
            await self._send(websocket, RegisteredMessage(node_id=node_id, session_id=session_id, adapter_token=adapter_token))
            logger.info(f"Web client {node_id} registered [token {adapter_token[:10]}…]")
            return node_id

        # --- existing adapter secret path below (unchanged) ---
```

Add `from mypalclara.gateway.api import clerk_auth` to the imports at the top of `server.py` if not present.

- [ ] **Step 5: Run test to verify it passes**

Run: `poetry run pytest tests/gateway/test_ws_clerk_auth.py tests/gateway/test_ws_auth.py -v`
Expected: PASS (all — new web cases pass, existing secret cases still pass).

- [ ] **Step 6: Commit**

```bash
git add mypal_protocol/messages.py mypalclara/gateway/server.py tests/gateway/test_ws_clerk_auth.py
git commit -m "feat(gateway): WebSocket browser registration via Clerk JWT"
```

---

## Task 6: Full suite + config docs

**Files:**
- Modify: `.env.example` (or the engine's documented env list)

- [ ] **Step 1: Run the full gateway + api suite**

Run: `poetry run pytest tests/gateway/ -q`
Expected: PASS, no regressions.

- [ ] **Step 2: Document the new env vars**

Add to the engine's `.env.example` (create the lines if absent):

```bash
# Clerk auth for the web app (mypal)
CLERK_JWKS_URL=https://<your-clerk-subdomain>.clerk.accounts.dev/.well-known/jwks.json
CLERK_ISSUER=https://<your-clerk-subdomain>.clerk.accounts.dev
# CORS already lists http://localhost:5173; add prod app origins to GATEWAY_API_CORS_ORIGINS
```

- [ ] **Step 3: Commit**

```bash
git add .env.example
git commit -m "docs(auth): document Clerk env vars for the web app"
```

---

## Self-review notes

- **Spec coverage:** Clerk JWT validation (Task 1), `sub`→CanonicalUser (Task 2), HTTP Bearer dependency (Tasks 3–4), WS browser auth (Task 5), CORS/config (Task 6). The adapter shared-secret path is preserved (Tasks 4–5 keep the existing branches).
- **Persona open item** (from the spec) is a frontend/protocol concern, handled in the frontend plan — not here.
- **No schema migration** needed: Clerk users reuse `CanonicalUser` + `PlatformLink(platform="clerk")`.
- **Type consistency:** `verify_clerk_jwt`, `get_or_create_clerk_user`, `get_clerk_user`, `_get_signing_key`, `ClerkAuthError`, `CLERK_PLATFORM` are used consistently across tasks and tests.
