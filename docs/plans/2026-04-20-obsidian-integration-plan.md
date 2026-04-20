# Obsidian Integration Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add local (non-MCP) Obsidian integration to Clara: per-user token storage on the identity service, 16 tools covering the full obsidian-local-rest-api surface, per-user tool filtering, and a cached vault-context snapshot injected into Clara's system prompt.

**Architecture:** Identity service grows Fernet-encrypted token storage + config endpoints + UI. Clara gateway gains a new `core/obsidian/` client library, a tool module (`core_tools/obsidian_tool.py`), per-user tool filtering infrastructure (`ToolDef.availability`), and vault-snapshot caching integrated into `PromptBuilder`.

**Tech Stack:** Python 3.12, FastAPI (identity service), SQLAlchemy (models), httpx (HTTP client), cryptography.Fernet (encryption at rest), pytest (tests).

**Reference:** See `docs/plans/2026-04-20-obsidian-integration-design.md` for design rationale and full surface spec.

---

## Pre-flight

Before starting any task:

- Ensure `poetry install` succeeds from the repo root.
- Ensure identity service tests pass on main: `cd services/identity && pytest`.
- Ensure main repo tests pass: `pytest tests/` (some pre-existing failures noted in MEMORY.md are expected).
- Confirm `obsidian.shmp.app` is reachable: `curl -k https://obsidian.shmp.app/` (may 401 without token, that's fine).
- Export dev token for integration tests only, **never commit**:
  `export OBSIDIAN_DEV_TOKEN="<dev-token-from-session>"`

---

## Phase A — Identity service: schema, encryption, endpoints, UI

### Task A1: Add Fernet encryption utility

**Files:**
- Create: `services/identity/crypto.py`
- Test: `services/identity/tests/test_crypto.py`

**Step 1: Write the failing test**

```python
# services/identity/tests/test_crypto.py
import os
import pytest
from cryptography.fernet import Fernet
from identity.crypto import encrypt_secret, decrypt_secret, get_fernet

def test_encrypt_decrypt_roundtrip(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("SECRETS_ENCRYPTION_KEY", key)
    get_fernet.cache_clear()  # reset singleton

    ciphertext = encrypt_secret("hello-token")
    assert isinstance(ciphertext, bytes)
    assert ciphertext != b"hello-token"
    assert decrypt_secret(ciphertext) == "hello-token"

def test_missing_key_raises(monkeypatch):
    monkeypatch.delenv("SECRETS_ENCRYPTION_KEY", raising=False)
    get_fernet.cache_clear()
    with pytest.raises(RuntimeError, match="SECRETS_ENCRYPTION_KEY"):
        encrypt_secret("x")
```

**Step 2: Run test — expect fail with ImportError**

`cd services/identity && pytest tests/test_crypto.py -v`

**Step 3: Implement**

```python
# services/identity/crypto.py
from __future__ import annotations
import os
from functools import lru_cache
from cryptography.fernet import Fernet

@lru_cache(maxsize=1)
def get_fernet() -> Fernet:
    key = os.environ.get("SECRETS_ENCRYPTION_KEY")
    if not key:
        raise RuntimeError("SECRETS_ENCRYPTION_KEY env var is required")
    return Fernet(key.encode() if isinstance(key, str) else key)

def encrypt_secret(plaintext: str) -> bytes:
    return get_fernet().encrypt(plaintext.encode("utf-8"))

def decrypt_secret(ciphertext: bytes) -> str:
    return get_fernet().decrypt(ciphertext).decode("utf-8")
```

**Step 4: Run test — expect pass**

**Step 5: Commit**

```bash
git add services/identity/crypto.py services/identity/tests/test_crypto.py
git commit -m "feat(identity): add Fernet encryption utility for per-user secrets"
```

---

### Task A2: Add Obsidian columns to CanonicalUser model

**Files:**
- Modify: `services/identity/db.py:34-48`

**Step 1: Extend the model**

Add these four columns to `CanonicalUser` (after `is_admin`, before `created_at`):

```python
encrypted_obsidian_token = Column(LargeBinary, nullable=True)
obsidian_api_host        = Column(Text, nullable=True)
obsidian_verify_tls      = Column(Boolean, default=True, server_default="1", nullable=False)
obsidian_updated_at      = Column(DateTime, nullable=True)
```

Add `LargeBinary` to the SQLAlchemy imports at the top of the file.

**Step 2: Confirm model loads**

`cd services/identity && python -c "from identity.db import CanonicalUser; print(CanonicalUser.__table__.columns.keys())"`
Expected: includes `encrypted_obsidian_token`, `obsidian_api_host`, `obsidian_verify_tls`, `obsidian_updated_at`.

**Step 3: Commit**

```bash
git add services/identity/db.py
git commit -m "feat(identity): add Obsidian config columns to CanonicalUser"
```

---

### Task A3: Write idempotent migration helper for existing DBs

**Files:**
- Create: `services/identity/scripts/migrate_obsidian_columns.py`
- Test: `services/identity/tests/test_migration.py`

Identity service uses `Base.metadata.create_all()` which won't add columns to existing tables. This script runs ALTER TABLE idempotently.

**Step 1: Write the failing test**

```python
# services/identity/tests/test_migration.py
import pytest
from sqlalchemy import create_engine, inspect
from identity.db import Base
from identity.scripts.migrate_obsidian_columns import migrate

def test_migration_adds_columns_to_legacy_table(tmp_path):
    db_url = f"sqlite:///{tmp_path}/legacy.db"
    engine = create_engine(db_url)

    # Simulate legacy table WITHOUT obsidian columns
    engine.execute("""
        CREATE TABLE canonical_users (
            id TEXT PRIMARY KEY,
            display_name TEXT NOT NULL,
            primary_email TEXT,
            avatar_url TEXT,
            status TEXT DEFAULT 'active',
            is_admin BOOLEAN DEFAULT 0,
            created_at TIMESTAMP,
            updated_at TIMESTAMP
        )
    """)

    migrate(engine)

    cols = {c["name"] for c in inspect(engine).get_columns("canonical_users")}
    assert "encrypted_obsidian_token" in cols
    assert "obsidian_api_host" in cols
    assert "obsidian_verify_tls" in cols
    assert "obsidian_updated_at" in cols

def test_migration_is_idempotent(tmp_path):
    db_url = f"sqlite:///{tmp_path}/fresh.db"
    engine = create_engine(db_url)
    Base.metadata.create_all(engine)
    migrate(engine)  # should be a no-op
    migrate(engine)  # should still be a no-op
```

**Step 2: Run test — expect fail**

**Step 3: Implement**

```python
# services/identity/scripts/__init__.py  (empty if not present)

# services/identity/scripts/migrate_obsidian_columns.py
from __future__ import annotations
from sqlalchemy import inspect, text

NEW_COLUMNS = {
    "encrypted_obsidian_token": "BLOB",
    "obsidian_api_host":        "TEXT",
    "obsidian_verify_tls":      "BOOLEAN NOT NULL DEFAULT 1",
    "obsidian_updated_at":      "TIMESTAMP",
}

def migrate(engine) -> None:
    existing = {c["name"] for c in inspect(engine).get_columns("canonical_users")}
    with engine.begin() as conn:
        for name, decl in NEW_COLUMNS.items():
            if name not in existing:
                conn.execute(text(f"ALTER TABLE canonical_users ADD COLUMN {name} {decl}"))

def main() -> None:
    from identity.db import engine
    migrate(engine)
    print("Obsidian columns migration complete.")

if __name__ == "__main__":
    main()
```

Note: Postgres uses `BYTEA` and `BOOLEAN DEFAULT true`. Detect dialect:

```python
def _col_decl(name: str, dialect: str) -> str:
    if dialect == "postgresql":
        return {
            "encrypted_obsidian_token": "BYTEA",
            "obsidian_api_host":        "TEXT",
            "obsidian_verify_tls":      "BOOLEAN NOT NULL DEFAULT true",
            "obsidian_updated_at":      "TIMESTAMP",
        }[name]
    return NEW_COLUMNS[name]  # SQLite defaults above
```

Wire `_col_decl(name, engine.dialect.name)` into the loop.

**Step 4: Run tests — expect pass**

`cd services/identity && pytest tests/test_migration.py -v`

**Step 5: Commit**

```bash
git add services/identity/scripts/ services/identity/tests/test_migration.py
git commit -m "feat(identity): add idempotent Obsidian columns migration"
```

---

### Task A4: Wire migration into startup

**Files:**
- Modify: `services/identity/app.py` (startup hook)

**Step 1: Call the migration from startup**

Find the FastAPI startup event (search `@app.on_event("startup")` or lifespan). Add a call after `init_db()`:

```python
from identity.scripts.migrate_obsidian_columns import migrate as migrate_obsidian
from identity.db import engine as identity_engine

# inside startup:
migrate_obsidian(identity_engine)
```

**Step 2: Smoke-test**

```bash
cd services/identity
SECRETS_ENCRYPTION_KEY=$(python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())") \
  python -c "from identity.app import app; print('startup ok')"
```
Expected: no error.

**Step 3: Commit**

```bash
git add services/identity/app.py
git commit -m "feat(identity): run Obsidian migration on startup"
```

---

### Task A5: Add PUT /users/me/obsidian-config endpoint

**Files:**
- Modify: `services/identity/app.py` (add endpoint + Pydantic model)
- Test: `services/identity/tests/test_obsidian_config.py`

**Step 1: Write the failing test**

```python
# services/identity/tests/test_obsidian_config.py
import pytest
from fastapi.testclient import TestClient
from identity.app import app
from identity.db import SessionLocal, CanonicalUser
from identity.crypto import decrypt_secret

@pytest.fixture
def client(monkeypatch, tmp_path):
    # Set up encryption key for tests
    from cryptography.fernet import Fernet
    monkeypatch.setenv("SECRETS_ENCRYPTION_KEY", Fernet.generate_key().decode())
    from identity.crypto import get_fernet
    get_fernet.cache_clear()
    return TestClient(app)

def test_put_obsidian_config_stores_encrypted_token(client, authed_jwt, canonical_user_id):
    resp = client.put(
        "/users/me/obsidian-config",
        headers={"Authorization": f"Bearer {authed_jwt}"},
        json={"api_token": "secret-token-xyz", "api_host": "obsidian.shmp.app", "verify_tls": True},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"configured": True, "api_host": "obsidian.shmp.app", "verify_tls": True}

    # Verify stored encrypted (not plaintext)
    db = SessionLocal()
    user = db.query(CanonicalUser).filter_by(id=canonical_user_id).first()
    assert user.encrypted_obsidian_token != b"secret-token-xyz"
    assert decrypt_secret(user.encrypted_obsidian_token) == "secret-token-xyz"
    assert user.obsidian_api_host == "obsidian.shmp.app"
    db.close()

def test_put_obsidian_config_requires_token_field(client, authed_jwt):
    resp = client.put(
        "/users/me/obsidian-config",
        headers={"Authorization": f"Bearer {authed_jwt}"},
        json={},
    )
    assert resp.status_code == 422

def test_put_obsidian_config_requires_auth(client):
    resp = client.put("/users/me/obsidian-config", json={"api_token": "x"})
    assert resp.status_code == 401
```

(You'll need `authed_jwt` and `canonical_user_id` fixtures — check existing `tests/test_api.py` for the pattern.)

**Step 2: Run test — expect fail**

**Step 3: Implement**

Add to `services/identity/app.py`:

```python
from pydantic import BaseModel, Field
from identity.crypto import encrypt_secret
from identity.db import utcnow

class ObsidianConfigIn(BaseModel):
    api_token: str = Field(..., min_length=1)
    api_host: str | None = None
    verify_tls: bool = True

@app.put("/users/me/obsidian-config")
def put_obsidian_config(
    payload: ObsidianConfigIn,
    user: CanonicalUser = Depends(get_current_user),  # existing JWT dep
    db: Session = Depends(get_db),
):
    user.encrypted_obsidian_token = encrypt_secret(payload.api_token)
    user.obsidian_api_host = payload.api_host or "obsidian.shmp.app"
    user.obsidian_verify_tls = payload.verify_tls
    user.obsidian_updated_at = utcnow()
    db.commit()
    return {
        "configured": True,
        "api_host": user.obsidian_api_host,
        "verify_tls": user.obsidian_verify_tls,
    }
```

**Step 4: Run tests — expect pass**

**Step 5: Commit**

```bash
git add services/identity/app.py services/identity/tests/test_obsidian_config.py
git commit -m "feat(identity): add PUT /users/me/obsidian-config endpoint"
```

---

### Task A6: Add DELETE /users/me/obsidian-config endpoint

**Files:**
- Modify: `services/identity/app.py`
- Test: extend `services/identity/tests/test_obsidian_config.py`

**Step 1: Add failing test**

```python
def test_delete_obsidian_config_clears_fields(client, authed_jwt, canonical_user_id):
    # First, configure
    client.put(
        "/users/me/obsidian-config",
        headers={"Authorization": f"Bearer {authed_jwt}"},
        json={"api_token": "x"},
    )
    # Now delete
    resp = client.delete(
        "/users/me/obsidian-config",
        headers={"Authorization": f"Bearer {authed_jwt}"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"configured": False}

    db = SessionLocal()
    user = db.query(CanonicalUser).filter_by(id=canonical_user_id).first()
    assert user.encrypted_obsidian_token is None
    assert user.obsidian_api_host is None
    db.close()
```

**Step 2: Run — expect fail**

**Step 3: Implement**

```python
@app.delete("/users/me/obsidian-config")
def delete_obsidian_config(
    user: CanonicalUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    user.encrypted_obsidian_token = None
    user.obsidian_api_host = None
    user.obsidian_verify_tls = True
    user.obsidian_updated_at = utcnow()
    db.commit()
    return {"configured": False}
```

**Step 4: Run — expect pass**

**Step 5: Commit**

```bash
git add services/identity/app.py services/identity/tests/test_obsidian_config.py
git commit -m "feat(identity): add DELETE /users/me/obsidian-config endpoint"
```

---

### Task A7: Extend GET /users/me response with obsidian status

**Files:**
- Modify: `services/identity/app.py` (the existing `/users/me` handler around line 248)
- Test: extend `services/identity/tests/test_obsidian_config.py`

**Step 1: Add failing test**

```python
def test_users_me_includes_obsidian_status(client, authed_jwt):
    # Unconfigured
    resp = client.get("/users/me", headers={"Authorization": f"Bearer {authed_jwt}"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["obsidian_configured"] is False
    assert body["obsidian_api_host"] is None
    assert body["obsidian_verify_tls"] is True

    # Configure
    client.put(
        "/users/me/obsidian-config",
        headers={"Authorization": f"Bearer {authed_jwt}"},
        json={"api_token": "x", "api_host": "example.com", "verify_tls": False},
    )
    resp = client.get("/users/me", headers={"Authorization": f"Bearer {authed_jwt}"})
    body = resp.json()
    assert body["obsidian_configured"] is True
    assert body["obsidian_api_host"] == "example.com"
    assert body["obsidian_verify_tls"] is False
    assert "obsidian_token" not in body  # NEVER returned to browser
    assert "encrypted_obsidian_token" not in body
```

**Step 2: Run — expect fail**

**Step 3: Modify `/users/me` handler**

Add to the response dict:
```python
"obsidian_configured": user.encrypted_obsidian_token is not None,
"obsidian_api_host": user.obsidian_api_host,
"obsidian_verify_tls": bool(user.obsidian_verify_tls),
```

**Step 4: Run — expect pass**

**Step 5: Commit**

```bash
git add services/identity/app.py services/identity/tests/test_obsidian_config.py
git commit -m "feat(identity): expose obsidian_configured/host/verify_tls on /users/me"
```

---

### Task A8: Add service-auth GET /users/{id}/obsidian-token endpoint

**Files:**
- Modify: `services/identity/app.py`
- Test: extend `services/identity/tests/test_obsidian_config.py`

**Step 1: Add failing test**

```python
def test_service_auth_get_obsidian_token(client, canonical_user_id, service_secret, authed_jwt):
    # Configure first
    client.put(
        "/users/me/obsidian-config",
        headers={"Authorization": f"Bearer {authed_jwt}"},
        json={"api_token": "service-secret-token", "api_host": "h.example"},
    )
    resp = client.get(
        f"/users/{canonical_user_id}/obsidian-token",
        headers={"X-Service-Secret": service_secret},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body == {
        "api_token": "service-secret-token",
        "api_host": "h.example",
        "verify_tls": True,
    }

def test_service_auth_missing_token_returns_404(client, canonical_user_id, service_secret):
    resp = client.get(
        f"/users/{canonical_user_id}/obsidian-token",
        headers={"X-Service-Secret": service_secret},
    )
    assert resp.status_code == 404

def test_service_auth_requires_service_secret(client, canonical_user_id):
    resp = client.get(f"/users/{canonical_user_id}/obsidian-token")
    assert resp.status_code == 401
```

**Step 2: Run — expect fail**

**Step 3: Implement**

```python
from identity.crypto import decrypt_secret

@app.get("/users/{canonical_user_id}/obsidian-token")
def get_obsidian_token_internal(
    canonical_user_id: str,
    _: None = Depends(require_service_secret),  # existing dep
    db: Session = Depends(get_db),
):
    user = db.query(CanonicalUser).filter_by(id=canonical_user_id).first()
    if not user or user.encrypted_obsidian_token is None:
        raise HTTPException(status_code=404, detail="Obsidian not configured")
    return {
        "api_token": decrypt_secret(user.encrypted_obsidian_token),
        "api_host": user.obsidian_api_host or "obsidian.shmp.app",
        "verify_tls": bool(user.obsidian_verify_tls),
    }
```

**Step 4: Run — expect pass**

**Step 5: Commit**

```bash
git add services/identity/app.py services/identity/tests/test_obsidian_config.py
git commit -m "feat(identity): add service-auth endpoint for fetching decrypted obsidian token"
```

---

### Task A9: Add Obsidian card to identity service React SPA

**Files:**
- Modify: `services/identity/static/index.html`

**Step 1: Locate the API key section**

Look for the existing API key form UI in `index.html`. Identify where to add the new section (same level of hierarchy, likely after the API keys block).

**Step 2: Add Obsidian card markup + handlers**

Add an "Integrations" section with an Obsidian card that:
- Shows "Configured" or "Not configured" based on `/users/me`
- Has inputs: password-type `api_token`, text `api_host` (placeholder `obsidian.shmp.app`), checkbox `verify_tls` (default checked)
- "Save" button → `PUT /users/me/obsidian-config` with current inputs; on success, clear token field and show "Configured"
- "Clear" button → `DELETE /users/me/obsidian-config`; on success, show "Not configured"
- Styling consistent with the existing API key card

(Exact code depends on whether the SPA uses vanilla JS, React-via-CDN, or a bundled React — follow the existing patterns already in this file.)

**Step 3: Manual smoke test**

```bash
cd services/identity
SECRETS_ENCRYPTION_KEY=$(python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())") \
  poetry run python -m identity
```
Open `http://localhost:18791/`, log in, configure a test token, refresh — should show "Configured".

**Step 4: Commit**

```bash
git add services/identity/static/index.html
git commit -m "feat(identity): add Obsidian integration card to profile UI"
```

---

## Phase B — Obsidian client library

### Task B1: Scaffold obsidian module + exceptions

**Files:**
- Create: `mypalclara/core/obsidian/__init__.py` (empty)
- Create: `mypalclara/core/obsidian/exceptions.py`
- Test: `tests/core/obsidian/__init__.py` (empty), `tests/core/obsidian/test_exceptions.py`

**Step 1: Write the failing test**

```python
# tests/core/obsidian/test_exceptions.py
from mypalclara.core.obsidian.exceptions import (
    ObsidianError, ObsidianAuthError, ObsidianNotFoundError,
    ObsidianRateLimitError, ObsidianConnectionError, ObsidianServerError,
)

def test_exception_hierarchy():
    for cls in (ObsidianAuthError, ObsidianNotFoundError, ObsidianRateLimitError,
                ObsidianConnectionError, ObsidianServerError):
        assert issubclass(cls, ObsidianError)
```

**Step 2: Run — expect fail**

**Step 3: Implement**

```python
# mypalclara/core/obsidian/exceptions.py
class ObsidianError(Exception): pass
class ObsidianAuthError(ObsidianError): pass
class ObsidianNotFoundError(ObsidianError): pass
class ObsidianRateLimitError(ObsidianError): pass
class ObsidianConnectionError(ObsidianError): pass
class ObsidianServerError(ObsidianError): pass
```

**Step 4: Run — expect pass**

**Step 5: Commit**

```bash
git add mypalclara/core/obsidian/ tests/core/obsidian/
git commit -m "feat(obsidian): scaffold client module with typed exceptions"
```

---

### Task B2: Implement ObsidianClient with auth, vault list/get/put

**Files:**
- Create: `mypalclara/core/obsidian/client.py`
- Test: `tests/core/obsidian/test_client.py`

**Step 1: Write the failing test**

```python
# tests/core/obsidian/test_client.py
import pytest
import httpx
from mypalclara.core.obsidian.client import ObsidianClient
from mypalclara.core.obsidian.exceptions import ObsidianAuthError, ObsidianNotFoundError

@pytest.mark.asyncio
async def test_list_vault_sends_bearer_auth(httpx_mock):
    httpx_mock.add_response(
        url="https://h.example/vault/",
        json={"files": ["a.md", "b/"]},
    )
    client = ObsidianClient("h.example", "my-token")
    files = await client.list_vault()
    assert files == ["a.md", "b/"]

    request = httpx_mock.get_request()
    assert request.headers["Authorization"] == "Bearer my-token"

@pytest.mark.asyncio
async def test_get_file_returns_content(httpx_mock):
    httpx_mock.add_response(
        url="https://h.example/vault/note.md",
        text="# Hello\n\nBody.",
    )
    client = ObsidianClient("h.example", "t")
    assert await client.get_file("note.md") == "# Hello\n\nBody."

@pytest.mark.asyncio
async def test_get_file_404_raises(httpx_mock):
    httpx_mock.add_response(url="https://h.example/vault/missing.md", status_code=404)
    client = ObsidianClient("h.example", "t")
    with pytest.raises(ObsidianNotFoundError):
        await client.get_file("missing.md")

@pytest.mark.asyncio
async def test_auth_error_401(httpx_mock):
    httpx_mock.add_response(url="https://h.example/vault/", status_code=401)
    client = ObsidianClient("h.example", "bad")
    with pytest.raises(ObsidianAuthError):
        await client.list_vault()

@pytest.mark.asyncio
async def test_put_file_sends_content(httpx_mock):
    httpx_mock.add_response(url="https://h.example/vault/new.md", status_code=204)
    client = ObsidianClient("h.example", "t")
    await client.put_file("new.md", "content")
    req = httpx_mock.get_request()
    assert req.method == "PUT"
    assert req.content == b"content"
```

Install `pytest-httpx` if not present: add to dev deps.

**Step 2: Run — expect fail**

**Step 3: Implement** (start with `list_vault`, `get_file`, `put_file`):

```python
# mypalclara/core/obsidian/client.py
from __future__ import annotations
import httpx
from mypalclara.core.obsidian.exceptions import (
    ObsidianAuthError, ObsidianConnectionError, ObsidianNotFoundError,
    ObsidianRateLimitError, ObsidianServerError,
)

class ObsidianClient:
    def __init__(self, api_host: str, api_token: str, verify_tls: bool = True,
                 timeout: float = 10.0):
        self.api_host = api_host
        self.api_token = api_token
        self.verify_tls = verify_tls
        self.timeout = timeout

    @property
    def _base(self) -> str:
        host = self.api_host.rstrip("/")
        if host.startswith("http://") or host.startswith("https://"):
            return host
        return f"https://{host}"

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_token}"}

    def _raise_for_status(self, resp: httpx.Response) -> None:
        if resp.status_code < 400:
            return
        if resp.status_code in (401, 403):
            raise ObsidianAuthError(f"Auth failed: {resp.status_code}")
        if resp.status_code == 404:
            raise ObsidianNotFoundError(f"Not found: {resp.url}")
        if resp.status_code == 429:
            raise ObsidianRateLimitError("Rate limited")
        if resp.status_code >= 500:
            raise ObsidianServerError(f"Server error: {resp.status_code}")
        resp.raise_for_status()

    async def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        url = f"{self._base}{path}"
        try:
            async with httpx.AsyncClient(verify=self.verify_tls, timeout=self.timeout) as c:
                resp = await c.request(method, url, headers=self._headers, **kwargs)
        except (httpx.ConnectError, httpx.TimeoutException) as e:
            raise ObsidianConnectionError(str(e)) from e
        self._raise_for_status(resp)
        return resp

    async def list_vault(self) -> list[str]:
        resp = await self._request("GET", "/vault/")
        return resp.json().get("files", [])

    async def get_file(self, path: str) -> str:
        resp = await self._request("GET", f"/vault/{path}")
        return resp.text

    async def put_file(self, path: str, content: str) -> None:
        await self._request("PUT", f"/vault/{path}", content=content.encode("utf-8"))
```

**Step 4: Run — expect pass**

**Step 5: Commit**

```bash
git add mypalclara/core/obsidian/client.py tests/core/obsidian/test_client.py
git commit -m "feat(obsidian): implement client with list_vault/get_file/put_file + auth"
```

---

### Task B3: Add remaining vault endpoints (append, patch, delete, list_dir)

**Files:** same as B2

**Step 1: Add failing tests** for each of `append_file`, `patch_file`, `delete_file`, `list_dir`.

**Step 2: Run — expect fail**

**Step 3: Add methods to `ObsidianClient`:**

```python
async def list_dir(self, path: str) -> list[str]:
    resp = await self._request("GET", f"/vault/{path.rstrip('/')}/")
    return resp.json().get("files", [])

async def append_file(self, path: str, content: str) -> None:
    await self._request("POST", f"/vault/{path}", content=content.encode("utf-8"))

async def patch_file(self, path: str, target_type: str, target: str,
                    content: str, operation: str = "append") -> None:
    headers = {
        "Target-Type": target_type,      # "heading" | "block" | "frontmatter"
        "Target": target,
        "Operation": operation,          # "append" | "prepend" | "replace"
    }
    await self._request("PATCH", f"/vault/{path}",
                        content=content.encode("utf-8"),
                        headers={**self._headers, **headers})
```

Note: `_request` currently merges headers via kwargs — adjust so per-call headers merge cleanly with auth header.

```python
async def delete_file(self, path: str) -> None:
    await self._request("DELETE", f"/vault/{path}")
```

**Step 4: Run — expect pass**

**Step 5: Commit**

```bash
git add mypalclara/core/obsidian/client.py tests/core/obsidian/test_client.py
git commit -m "feat(obsidian): add list_dir, append, patch, delete methods"
```

---

### Task B4: Add active file + periodic notes endpoints

**Files:** same

Methods to add (with tests):

```python
async def get_active(self) -> str:
    resp = await self._request("GET", "/active/")
    return resp.text

async def put_active(self, content: str) -> None:
    await self._request("PUT", "/active/", content=content.encode("utf-8"))

async def get_periodic(self, period: str, date=None) -> str:
    # period: "daily" | "weekly" | "monthly" | "quarterly" | "yearly"
    if date:
        path = f"/periodic/{period}/{date.year}/{date.month:02d}/{date.day:02d}/"
    else:
        path = f"/periodic/{period}/"
    resp = await self._request("GET", path)
    return resp.text

async def append_periodic(self, period: str, content: str, date=None) -> None:
    if date:
        path = f"/periodic/{period}/{date.year}/{date.month:02d}/{date.day:02d}/"
    else:
        path = f"/periodic/{period}/"
    await self._request("POST", path, content=content.encode("utf-8"))
```

Commit: `feat(obsidian): add active file + periodic notes methods`

---

### Task B5: Add search endpoints (simple, DQL, JsonLogic)

**Files:** same

```python
async def search_simple(self, query: str) -> list[dict]:
    resp = await self._request("POST", "/search/simple/", json={"query": query})
    return resp.json()

async def search_dql(self, query: str) -> list[dict]:
    resp = await self._request(
        "POST", "/search/",
        content=query.encode("utf-8"),
        headers={**self._headers, "Content-Type": "application/vnd.olrapi.dataview.dql+txt"},
    )
    return resp.json()

async def search_jsonlogic(self, query: dict) -> list[dict]:
    resp = await self._request(
        "POST", "/search/",
        json=query,
        headers={**self._headers, "Content-Type": "application/vnd.olrapi.jsonlogic+json"},
    )
    return resp.json()
```

(Verify `Content-Type` strings against the obsidian-local-rest-api OpenAPI spec before implementing — if they differ, use the spec's values.)

Commit: `feat(obsidian): add search_simple/search_dql/search_jsonlogic methods`

---

### Task B6: Add tags/commands/open endpoints

**Files:** same

```python
async def list_tags(self) -> list[tuple[str, int]]:
    resp = await self._request("GET", "/tags/")
    data = resp.json()  # shape per spec; adjust
    return [(t["name"], t["count"]) for t in data]

async def list_commands(self) -> list[dict]:
    resp = await self._request("GET", "/commands/")
    return resp.json().get("commands", [])

async def execute_command(self, command_id: str) -> None:
    await self._request("POST", f"/commands/{command_id}/")

async def open_file(self, path: str) -> None:
    await self._request("POST", f"/open/{path}")
```

Commit: `feat(obsidian): add tags/commands/open methods`

---

### Task B7: Implement get_client_for_user factory with identity-service lookup

**Files:**
- Create: `mypalclara/core/obsidian/factory.py`
- Test: `tests/core/obsidian/test_factory.py`

**Step 1: Write the failing test**

```python
# tests/core/obsidian/test_factory.py
import pytest
from unittest.mock import patch, AsyncMock
from mypalclara.core.obsidian.factory import get_client_for_user, clear_client_cache

@pytest.fixture(autouse=True)
def _reset():
    clear_client_cache()
    yield
    clear_client_cache()

@pytest.mark.asyncio
async def test_returns_none_when_unconfigured(monkeypatch):
    monkeypatch.setenv("IDENTITY_SERVICE_URL", "https://id.example")
    monkeypatch.setenv("IDENTITY_SERVICE_SECRET", "s")
    with patch("httpx.AsyncClient.request", new=AsyncMock()) as mock:
        mock.return_value.status_code = 404
        client = await get_client_for_user("user-1")
        assert client is None

@pytest.mark.asyncio
async def test_returns_client_when_configured(monkeypatch):
    monkeypatch.setenv("IDENTITY_SERVICE_URL", "https://id.example")
    monkeypatch.setenv("IDENTITY_SERVICE_SECRET", "s")
    resp_json = {"api_token": "t", "api_host": "o.example", "verify_tls": True}
    with patch("httpx.AsyncClient.request", new=AsyncMock()) as mock:
        mock.return_value.status_code = 200
        mock.return_value.json = lambda: resp_json
        client = await get_client_for_user("user-1")
        assert client is not None
        assert client.api_host == "o.example"
        assert client.api_token == "t"

@pytest.mark.asyncio
async def test_caches_client_per_user():
    # Second call with same user reuses instance (within TTL)
    ...
```

**Step 2: Run — expect fail**

**Step 3: Implement**

```python
# mypalclara/core/obsidian/factory.py
from __future__ import annotations
import os
import time
import httpx
from mypalclara.core.obsidian.client import ObsidianClient

_CLIENT_TTL_SECONDS = 60
_cache: dict[str, tuple[float, ObsidianClient]] = {}

def clear_client_cache() -> None:
    _cache.clear()

async def get_client_for_user(canonical_user_id: str) -> ObsidianClient | None:
    now = time.monotonic()
    cached = _cache.get(canonical_user_id)
    if cached and now - cached[0] < _CLIENT_TTL_SECONDS:
        return cached[1]

    base = os.environ.get("IDENTITY_SERVICE_URL", "http://localhost:18791")
    secret = os.environ.get("IDENTITY_SERVICE_SECRET", "")
    url = f"{base.rstrip('/')}/users/{canonical_user_id}/obsidian-token"
    async with httpx.AsyncClient(timeout=5.0) as http:
        resp = await http.request("GET", url, headers={"X-Service-Secret": secret})
    if resp.status_code == 404:
        _cache.pop(canonical_user_id, None)
        return None
    resp.raise_for_status()
    data = resp.json()
    client = ObsidianClient(
        api_host=data["api_host"],
        api_token=data["api_token"],
        verify_tls=bool(data.get("verify_tls", True)),
    )
    _cache[canonical_user_id] = (now, client)
    return client
```

**Step 4: Run — expect pass**

**Step 5: Commit**

```bash
git add mypalclara/core/obsidian/factory.py tests/core/obsidian/test_factory.py
git commit -m "feat(obsidian): add factory that fetches credentials from identity service"
```

---

## Phase C — Per-user tool filtering infrastructure

### Task C1: Add availability field to ToolDef

**Files:**
- Modify: `mypalclara/tools/_base.py:38-68`
- Test: `tests/tools/test_tool_def_availability.py`

**Step 1: Write the failing test**

```python
import pytest
from mypalclara.tools._base import ToolDef

async def _always_true(uid): return True
async def _always_false(uid): return False

def test_tool_def_has_optional_availability_field():
    tool = ToolDef(name="t", description="d", parameters={}, handler=lambda a, c: None)
    assert tool.availability is None

def test_tool_def_accepts_availability_callable():
    tool = ToolDef(name="t", description="d", parameters={},
                   handler=lambda a, c: None, availability=_always_true)
    assert tool.availability is _always_true
```

**Step 2: Run — expect fail**

**Step 3: Add field to `ToolDef`**

After `intent: str = "read"`:

```python
availability: Callable[[str], Awaitable[bool]] | None = None
"""Optional per-user availability predicate. Receives canonical_user_id,
returns True if the tool should be exposed to this user."""
```

**Step 4: Run — expect pass**

**Step 5: Commit**

```bash
git add mypalclara/tools/_base.py tests/tools/test_tool_def_availability.py
git commit -m "feat(tools): add optional availability predicate to ToolDef"
```

---

### Task C2: Filter by availability in get_all_tools with per-request memo

**Files:**
- Modify: `mypalclara/gateway/tool_executor.py` (find `get_all_tools` ~line 406)
- Test: `tests/gateway/test_tool_executor_availability.py`

**Step 1: Write the failing test**

```python
import pytest
from mypalclara.tools._base import ToolDef
from mypalclara.gateway.tool_executor import ToolExecutor

async def _handler(a, c): return "ok"
async def _pred_alice_only(uid):
    return uid == "alice"

@pytest.mark.asyncio
async def test_availability_filter_includes_only_matching_user():
    t_everyone = ToolDef(name="free", description="d", parameters={}, handler=_handler)
    t_alice = ToolDef(name="alice_only", description="d", parameters={},
                      handler=_handler, availability=_pred_alice_only)

    executor = ToolExecutor(extra_tools=[t_everyone, t_alice])

    tools_alice = await executor.get_all_tools(user_id="alice")
    names_alice = {t["name"] if isinstance(t, dict) else t.name for t in tools_alice}
    assert "free" in names_alice and "alice_only" in names_alice

    tools_bob = await executor.get_all_tools(user_id="bob")
    names_bob = {t["name"] if isinstance(t, dict) else t.name for t in tools_bob}
    assert "free" in names_bob and "alice_only" not in names_bob

    tools_anon = await executor.get_all_tools(user_id=None)
    # When user_id not given, skip filtering (include all)
    names_anon = {t["name"] if isinstance(t, dict) else t.name for t in tools_anon}
    assert "alice_only" in names_anon

@pytest.mark.asyncio
async def test_availability_predicate_called_once_per_request(monkeypatch):
    calls = {"n": 0}
    async def pred(uid):
        calls["n"] += 1
        return True
    t1 = ToolDef(name="t1", description="d", parameters={}, handler=_handler, availability=pred)
    t2 = ToolDef(name="t2", description="d", parameters={}, handler=_handler, availability=pred)
    executor = ToolExecutor(extra_tools=[t1, t2])
    await executor.get_all_tools(user_id="alice")
    assert calls["n"] == 1  # memoized per callable
```

**Step 2: Run — expect fail**

**Step 3: Implement filtering in `get_all_tools`**

Sketch (adapt to actual `ToolExecutor` signature):

```python
async def get_all_tools(
    self,
    adapter_capabilities: list[str] | None = None,
    user_id: str | None = None,
) -> list[...]:
    tools = self._collect_all_tools(adapter_capabilities)
    if user_id is None:
        return tools

    # Memoize predicate results per unique callable
    memo: dict[int, bool] = {}
    filtered = []
    for t in tools:
        pred = getattr(t, "availability", None)
        if pred is None:
            filtered.append(t)
            continue
        key = id(pred)
        if key not in memo:
            try:
                memo[key] = await pred(user_id)
            except Exception:
                logger.warning("availability predicate failed", exc_info=True)
                memo[key] = False
        if memo[key]:
            filtered.append(t)
    return filtered
```

**Step 4: Run — expect pass**

**Step 5: Commit**

```bash
git add mypalclara/gateway/tool_executor.py tests/gateway/test_tool_executor_availability.py
git commit -m "feat(gateway): filter tools by per-user availability predicate"
```

---

### Task C3: Thread user_id through processor.py

**Files:**
- Modify: `mypalclara/gateway/processor.py` (around line 437 where `get_all_tools` is called)

**Step 1: Identify user_id source**

The gateway request already carries a user_id (used elsewhere in the processor). Find it and pass it into `self._tool_executor.get_all_tools(adapter_capabilities=..., user_id=...)`.

**Step 2: Manual smoke-test**

Run the gateway locally with a configured user — verify Obsidian tools are gated correctly (once C4+D tasks are done).

**Step 3: Commit**

```bash
git add mypalclara/gateway/processor.py
git commit -m "feat(gateway): pass user_id to tool_executor.get_all_tools for per-user filtering"
```

---

## Phase D — Prompt integration

### Task D1: Wire per-tool SYSTEM_PROMPT into build_worm_persona

**Files:**
- Modify: `mypalclara/core/security/worm_persona.py:70-89`
- Test: `tests/core/security/test_worm_persona_system_prompts.py`

**Step 1: Write the failing test**

```python
def test_build_worm_persona_includes_registered_system_prompts():
    from mypalclara.core.security.worm_persona import build_worm_persona

    result = build_worm_persona(
        personality="you are clara",
        tools=[{"name": "obsidian_search", "description": "search vault"}],
        system_prompts=[("obsidian", "When using Obsidian tools, prefer search before get.")],
    )
    assert "prefer search before get" in result
```

**Step 2: Run — expect fail**

**Step 3: Modify `build_worm_persona`**

Add optional `system_prompts: list[tuple[str, str]] | None = None` parameter. After the capability inventory block, if provided, append each prompt chunk with a small header (`## Tool-specific guidance — {module}`).

**Step 4: Run — expect pass**

**Step 5: Commit**

```bash
git add mypalclara/core/security/worm_persona.py tests/core/security/test_worm_persona_system_prompts.py
git commit -m "feat(prompt): include registered per-tool system prompts in worm persona"
```

---

### Task D2: Gather system_prompts in PromptBuilder and pass through

**Files:**
- Modify: `mypalclara/core/prompt_builder.py` (around line 156/204)

**Step 1: Locate the call to `build_worm_persona`**

Before that call, gather registered prompts:
```python
system_prompts = self._registry.get_system_prompts(
    tool_modules=[t["module"] for t in tools if t.get("module")]
)
```
(Exact API depends on the registry — adjust to what exists.)

**Step 2: Pass into `build_worm_persona`**

**Step 3: Smoke-test** — run a test that calls `build_prompt` with a tool having a registered SYSTEM_PROMPT, assert it appears in the result.

**Step 4: Commit**

```bash
git add mypalclara/core/prompt_builder.py
git commit -m "feat(prompt): gather and inject per-tool system prompts in PromptBuilder"
```

---

### Task D3: Implement VaultSnapshot + build_snapshot

**Files:**
- Create: `mypalclara/core/obsidian/snapshot.py`
- Test: `tests/core/obsidian/test_snapshot.py`

**Step 1: Write the failing test**

```python
import pytest
from unittest.mock import AsyncMock
from mypalclara.core.obsidian.snapshot import VaultSnapshot, build_snapshot

@pytest.mark.asyncio
async def test_build_snapshot_aggregates_calls():
    client = AsyncMock()
    client.api_host = "h.example"
    client.list_vault = AsyncMock(return_value=["Projects/", "Daily/", "a.md"])
    client.list_tags = AsyncMock(return_value=[("work", 42), ("clara", 17)])
    client.search_dql = AsyncMock(return_value=[{"path": "a.md"}, {"path": "b.md"}])
    client.get_periodic = AsyncMock(return_value="# 2026-04-20\n")

    snap = await build_snapshot(client)
    assert snap.host == "h.example"
    assert "Projects" in snap.top_level_folders or "Projects/" in snap.top_level_folders
    assert snap.top_tags == [("work", 42), ("clara", 17)]
    assert snap.recent_notes == ["a.md", "b.md"]
    assert snap.today_periodic is not None

@pytest.mark.asyncio
async def test_build_snapshot_degrades_on_partial_failure():
    from mypalclara.core.obsidian.exceptions import ObsidianConnectionError
    client = AsyncMock()
    client.api_host = "h.example"
    client.list_vault = AsyncMock(return_value=["a.md"])
    client.list_tags = AsyncMock(side_effect=ObsidianConnectionError())
    client.search_dql = AsyncMock(side_effect=Exception("nope"))
    client.get_periodic = AsyncMock(side_effect=Exception("nope"))

    snap = await build_snapshot(client)
    assert snap.top_tags == []
    assert snap.recent_notes == []
    assert snap.today_periodic is None
```

**Step 2: Run — expect fail**

**Step 3: Implement**

```python
# mypalclara/core/obsidian/snapshot.py
from __future__ import annotations
import asyncio
from dataclasses import dataclass, field
from datetime import datetime

@dataclass
class VaultSnapshot:
    host: str
    top_level_folders: list[str] = field(default_factory=list)
    total_note_count: int = 0
    top_tags: list[tuple[str, int]] = field(default_factory=list)
    recent_notes: list[str] = field(default_factory=list)
    today_periodic: str | None = None
    fetched_at: datetime = field(default_factory=datetime.utcnow)
    unavailable: bool = False

    def to_prompt_block(self) -> str:
        if self.unavailable:
            return "User has Obsidian configured but vault details are currently unavailable."
        folders = ", ".join(self.top_level_folders[:10]) or "(none)"
        tags = ", ".join(f"#{t} ({c})" for t, c in self.top_tags[:8]) or "(none)"
        recent = ", ".join(self.recent_notes[:5]) or "(none)"
        periodic = self.today_periodic or "none yet"
        return (
            f"**User's Obsidian vault** ({self.host}): "
            f"{self.total_note_count} notes across folders: {folders}. "
            f"Recent edits: {recent}. "
            f"Today's daily note: {periodic}. "
            f"Top tags: {tags}."
        )

async def _safe(coro, default):
    try:
        return await coro
    except Exception:
        return default

async def build_snapshot(client) -> VaultSnapshot:
    async def _folders_and_count():
        listing = await client.list_vault()
        folders = [p.rstrip("/") for p in listing if p.endswith("/")]
        # Rough count; full recursion could be added later
        count = sum(1 for p in listing if not p.endswith("/"))
        return folders, count

    listing, tags, recent_raw, periodic = await asyncio.gather(
        _safe(_folders_and_count(), ([], 0)),
        _safe(client.list_tags(), []),
        _safe(client.search_dql('TABLE file.mtime FROM "" SORT file.mtime DESC LIMIT 5'), []),
        _safe(client.get_periodic("daily"), None),
    )
    folders, count = listing
    recent = [h.get("path", "") for h in (recent_raw or []) if h.get("path")]
    return VaultSnapshot(
        host=client.api_host,
        top_level_folders=folders,
        total_note_count=count,
        top_tags=tags or [],
        recent_notes=recent,
        today_periodic=(periodic[:80] if periodic else None),
    )
```

**Step 4: Run — expect pass**

**Step 5: Commit**

```bash
git add mypalclara/core/obsidian/snapshot.py tests/core/obsidian/test_snapshot.py
git commit -m "feat(obsidian): implement VaultSnapshot + parallel build with graceful degrade"
```

---

### Task D4: Implement SnapshotCache with per-user lock and invalidation

**Files:**
- Create: `mypalclara/core/obsidian/cache.py`
- Test: `tests/core/obsidian/test_cache.py`

**Step 1: Write the failing test**

```python
import pytest
import asyncio
from unittest.mock import AsyncMock
from mypalclara.core.obsidian.cache import SnapshotCache
from mypalclara.core.obsidian.snapshot import VaultSnapshot

@pytest.mark.asyncio
async def test_cache_builds_on_miss_and_returns_cached_on_hit():
    calls = {"n": 0}
    async def builder(client):
        calls["n"] += 1
        return VaultSnapshot(host="h")
    cache = SnapshotCache(builder=builder)
    s1 = await cache.get_or_build("u1", client=object())
    s2 = await cache.get_or_build("u1", client=object())
    assert s1 is s2
    assert calls["n"] == 1

@pytest.mark.asyncio
async def test_cache_invalidation_forces_rebuild():
    async def builder(client): return VaultSnapshot(host="h")
    cache = SnapshotCache(builder=builder)
    s1 = await cache.get_or_build("u1", client=object())
    cache.invalidate("u1")
    s2 = await cache.get_or_build("u1", client=object())
    assert s1 is not s2

@pytest.mark.asyncio
async def test_concurrent_builds_serialize_via_lock():
    call_started = asyncio.Event()
    async def slow_builder(client):
        call_started.set()
        await asyncio.sleep(0.05)
        return VaultSnapshot(host="h")
    cache = SnapshotCache(builder=slow_builder)
    t1 = asyncio.create_task(cache.get_or_build("u1", client=object()))
    await call_started.wait()
    t2 = asyncio.create_task(cache.get_or_build("u1", client=object()))
    s1, s2 = await asyncio.gather(t1, t2)
    assert s1 is s2  # second call waited and got the same instance

@pytest.mark.asyncio
async def test_failure_caches_unavailable_sentinel_briefly():
    async def failing(client):
        raise RuntimeError("boom")
    cache = SnapshotCache(builder=failing, failure_ttl=0.05)
    s1 = await cache.get_or_build("u1", client=object())
    assert s1.unavailable is True
    s2 = await cache.get_or_build("u1", client=object())
    assert s2 is s1  # same sentinel within TTL
    await asyncio.sleep(0.06)
    # After TTL, builder is re-attempted — still raises, still returns sentinel,
    # but a fresh instance
    s3 = await cache.get_or_build("u1", client=object())
    assert s3.unavailable is True
```

**Step 2: Run — expect fail**

**Step 3: Implement**

```python
# mypalclara/core/obsidian/cache.py
from __future__ import annotations
import asyncio
import time
from collections.abc import Callable, Awaitable
from mypalclara.core.obsidian.snapshot import VaultSnapshot

class SnapshotCache:
    def __init__(self, builder: Callable[[object], Awaitable[VaultSnapshot]],
                 failure_ttl: float = 30.0):
        self._builder = builder
        self._failure_ttl = failure_ttl
        self._store: dict[str, tuple[float, VaultSnapshot]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock_for(self, user_id: str) -> asyncio.Lock:
        return self._locks.setdefault(user_id, asyncio.Lock())

    def invalidate(self, user_id: str) -> None:
        self._store.pop(user_id, None)

    async def get_or_build(self, user_id: str, client) -> VaultSnapshot:
        entry = self._store.get(user_id)
        if entry:
            expires_at, snap = entry
            if snap.unavailable and time.monotonic() > expires_at:
                self._store.pop(user_id, None)
            else:
                return snap
        async with self._lock_for(user_id):
            entry = self._store.get(user_id)
            if entry:
                return entry[1]
            try:
                snap = await self._builder(client)
                self._store[user_id] = (float("inf"), snap)
                return snap
            except Exception:
                sentinel = VaultSnapshot(host=getattr(client, "api_host", "?"),
                                         unavailable=True)
                self._store[user_id] = (time.monotonic() + self._failure_ttl, sentinel)
                return sentinel
```

**Step 4: Run — expect pass**

**Step 5: Commit**

```bash
git add mypalclara/core/obsidian/cache.py tests/core/obsidian/test_cache.py
git commit -m "feat(obsidian): add SnapshotCache with per-user lock and failure TTL"
```

---

### Task D5: Wire snapshot into PromptBuilder.build_prompt

**Files:**
- Modify: `mypalclara/core/prompt_builder.py`

**Step 1: Identify build_prompt signature**

Add `user_id: str | None = None` to `build_prompt` (and thread through from callers — processor.py).

**Step 2: Inject snapshot when configured**

After capability inventory, if `user_id`:
```python
from mypalclara.core.obsidian.factory import get_client_for_user
from mypalclara.core.obsidian import _snapshot_cache  # singleton defined in module
client = await get_client_for_user(user_id)
if client is not None:
    snap = await _snapshot_cache.get_or_build(user_id, client)
    prompt_sections.append("## User Context\n\n" + snap.to_prompt_block())
```

Define the singleton in `mypalclara/core/obsidian/__init__.py`:
```python
from mypalclara.core.obsidian.cache import SnapshotCache
from mypalclara.core.obsidian.snapshot import build_snapshot
_snapshot_cache = SnapshotCache(builder=build_snapshot)
```

**Step 3: Test**

```python
# tests/core/prompt_builder/test_prompt_with_vault_snapshot.py
# - mock get_client_for_user to return a client
# - mock snapshot_cache to return a known VaultSnapshot
# - assert the prompt contains "User's Obsidian vault"
```

**Step 4: Commit**

```bash
git add mypalclara/core/prompt_builder.py mypalclara/core/obsidian/__init__.py tests/core/prompt_builder/
git commit -m "feat(prompt): inject cached Obsidian vault snapshot into system prompt"
```

---

### Task D6: Thread user_id from processor.py into build_prompt

**Files:**
- Modify: `mypalclara/gateway/processor.py`

**Step 1:** Locate the `build_prompt` call (the same place as the `get_all_tools` call from C3). Pass `user_id=user_id`.

**Step 2: Smoke-test** (end-to-end once we have tools registered).

**Step 3: Commit**

```bash
git add mypalclara/gateway/processor.py
git commit -m "feat(gateway): pass user_id to PromptBuilder for per-user context injection"
```

---

## Phase E — Tool module

### Task E1: Scaffold obsidian_tool module + availability predicate

**Files:**
- Create: `mypalclara/core/core_tools/obsidian_tool.py`
- Test: `tests/core/core_tools/test_obsidian_tool.py`

**Step 1: Write the failing test**

```python
import pytest
from unittest.mock import patch, AsyncMock
from mypalclara.core.core_tools.obsidian_tool import has_obsidian_config, TOOLS

def test_module_exports_tools():
    assert len(TOOLS) == 16
    for t in TOOLS:
        assert t.name.startswith("obsidian_")
        assert t.availability is not None

@pytest.mark.asyncio
async def test_has_obsidian_config_true_when_client_returned():
    with patch("mypalclara.core.core_tools.obsidian_tool.get_client_for_user",
               new=AsyncMock(return_value=object())):
        assert await has_obsidian_config("u1") is True

@pytest.mark.asyncio
async def test_has_obsidian_config_false_when_none():
    with patch("mypalclara.core.core_tools.obsidian_tool.get_client_for_user",
               new=AsyncMock(return_value=None)):
        assert await has_obsidian_config("u1") is False
```

**Step 2: Run — expect fail**

**Step 3: Implement skeleton**

```python
# mypalclara/core/core_tools/obsidian_tool.py
from __future__ import annotations
import json
import logging
from typing import Any

from mypalclara.tools._base import ToolContext, ToolDef
from mypalclara.core.obsidian.factory import get_client_for_user
from mypalclara.core.obsidian.exceptions import (
    ObsidianError, ObsidianAuthError, ObsidianNotFoundError,
    ObsidianRateLimitError, ObsidianConnectionError,
)
from mypalclara.core.obsidian import _snapshot_cache

MODULE_NAME = "obsidian"
MODULE_VERSION = "1.0.0"
logger = logging.getLogger("clara.tools.obsidian")

SYSTEM_PROMPT = """\
You have read/write access to the user's Obsidian vault.

Principles:
- Prefer `obsidian_search` before `obsidian_get_file` when you don't know the exact path.
- For targeted edits, prefer `obsidian_patch_file` (heading/block/frontmatter) over
  `obsidian_create_or_update_file`, which overwrites the entire note.
- Periodic notes are the user's journal. `obsidian_append_to_periodic_note` with
  `period="daily"` is the right default for "add this to my journal".
- `obsidian_open_file` surfaces a note in the user's Obsidian UI — use sparingly, only
  when they explicitly want to see something.
- Write tools mutate the user's vault. Their effects are visible to the user.
- If a tool returns "Obsidian not configured", the user needs to set up integration
  via the identity service UI; do not retry.
"""

async def has_obsidian_config(canonical_user_id: str) -> bool:
    try:
        client = await get_client_for_user(canonical_user_id)
        return client is not None
    except Exception:
        logger.warning("has_obsidian_config failed", exc_info=True)
        return False

TOOLS: list[ToolDef] = []  # populated by subsequent tasks
```

Also add placeholder `TOOLS = [...]` entries or leave empty for E2+E3+E4 to fill.

**Step 4: Run — expect the 16-tool assertion to fail** (it's a forward-looking test; OK to mark as xfail for now, or split it into a later task).

Adjust the test: split into `test_module_exports_availability_predicate` (passes now) and a separate `test_all_sixteen_tools_registered` that runs in Task E5.

**Step 5: Commit**

```bash
git add mypalclara/core/core_tools/obsidian_tool.py tests/core/core_tools/test_obsidian_tool.py
git commit -m "feat(tools): scaffold obsidian tool module with availability predicate"
```

---

### Task E2: Implement read tools (7)

**Files:** same

Implement these handlers + `ToolDef` entries:

- `obsidian_list_vault`
- `obsidian_list_dir` (param: `path`)
- `obsidian_get_file` (param: `path`)
- `obsidian_get_active_file`
- `obsidian_get_periodic_note` (params: `period`, optional `date` as YYYY-MM-DD)
- `obsidian_list_tags`
- `obsidian_list_commands`

**Pattern for each handler:**

```python
async def _handle_get_file(args: dict[str, Any], ctx: ToolContext) -> str:
    client = await get_client_for_user(ctx.user_id)
    if client is None:
        return "Obsidian is not configured for this user."
    path = args.get("path", "")
    if not path:
        return "Error: 'path' is required."
    try:
        return await client.get_file(path)
    except ObsidianNotFoundError:
        return f"Note not found: {path}"
    except ObsidianAuthError:
        return "Obsidian authentication failed. Please update your API token."
    except ObsidianConnectionError as e:
        return f"Obsidian unreachable: {e}"
    except ObsidianError as e:
        return f"Obsidian error: {e}"

TOOLS.append(ToolDef(
    name="obsidian_get_file",
    description="Read the full content of a note in the user's Obsidian vault.",
    parameters={
        "type": "object",
        "properties": {"path": {"type": "string", "description": "Vault-relative path, e.g. 'Projects/foo.md'"}},
        "required": ["path"],
    },
    handler=_handle_get_file,
    availability=has_obsidian_config,
    risk_level="safe",
    intent="read",
    emoji="📄",
    detail_keys=["path"],
))
```

**Write one unit test per handler** (mock `get_client_for_user`). Cover: happy path, "not configured" path, one error-mapping path.

**Step 4: Run — expect pass**

**Step 5: Commit**

```bash
git add mypalclara/core/core_tools/obsidian_tool.py tests/core/core_tools/test_obsidian_tool.py
git commit -m "feat(tools): add 7 Obsidian read tools (vault, dir, file, active, periodic, tags, commands)"
```

---

### Task E3: Implement search tools (2)

**Files:** same

- `obsidian_search` (simple text) — param: `query`
- `obsidian_query` (structured) — params: `query_type` ("dql"|"jsonlogic"), `query` (string for dql, JSON string for jsonlogic)

Include unit tests; commit with message `feat(tools): add obsidian_search and obsidian_query tools`.

---

### Task E4: Implement write tools (5) + cache invalidation

**Files:** same

- `obsidian_create_or_update_file` — params: `path`, `content`
- `obsidian_append_to_file` — params: `path`, `content`
- `obsidian_patch_file` — params: `path`, `target_type`, `target`, `content`, `operation` (default "append")
- `obsidian_append_to_periodic_note` — params: `period`, `content`, optional `date`
- `obsidian_delete_file` — params: `path`

**Each write handler MUST call `_snapshot_cache.invalidate(ctx.user_id)` on success.**

Pattern:

```python
async def _handle_put_file(args, ctx):
    client = await get_client_for_user(ctx.user_id)
    if client is None: return "Obsidian is not configured for this user."
    try:
        await client.put_file(args["path"], args["content"])
        _snapshot_cache.invalidate(ctx.user_id)
        return f"Wrote {args['path']}"
    except ObsidianError as e:
        return f"Obsidian error: {e}"
```

Test that invalidation is called on success and NOT on failure (mock cache, assert call count).

Commit: `feat(tools): add 5 Obsidian write tools with snapshot cache invalidation`

---

### Task E5: Implement UI/command tools (2) + register module

**Files:**
- Modify: `mypalclara/core/core_tools/obsidian_tool.py`
- Modify: `mypalclara/core/core_tools/__init__.py`

**Step 1: Add the last two tools**

- `obsidian_open_file` — param: `path`
- `obsidian_execute_command` — param: `command_id`

Both are "write" intent (they cause user-visible effects).

**Step 2: Register module in `core_tools/__init__.py`**

In `register_core_tools()`, add:
```python
from mypalclara.core.core_tools import obsidian_tool
for tool in obsidian_tool.TOOLS:
    registry.register(tool)
registry.register_system_prompt(
    obsidian_tool.MODULE_NAME,
    obsidian_tool.SYSTEM_PROMPT,
)
```

**Step 3: Enable the deferred assertion**

Restore `test_all_sixteen_tools_registered` and make it pass now that all 16 are in place.

**Step 4: Smoke-test**

```bash
cd /Users/heidornj/Code/mypalclara
poetry run python -c "
from mypalclara.core.core_tools import register_core_tools
from mypalclara.tools._registry import ToolRegistry
r = ToolRegistry()
register_core_tools(r)
obs = [t.name for t in r.get_all_tools() if t.name.startswith('obsidian_')]
print(len(obs), obs)
"
```
Expected: `16 ['obsidian_list_vault', 'obsidian_list_dir', ...]`

**Step 5: Commit**

```bash
git add mypalclara/core/core_tools/obsidian_tool.py mypalclara/core/core_tools/__init__.py tests/
git commit -m "feat(tools): add obsidian_open_file/execute_command and register module"
```

---

## Phase F — End-to-end + integration

### Task F1: Marked integration test against obsidian.shmp.app

**Files:**
- Create: `tests/integration/test_obsidian_live.py`

**Step 1: Write the test**

```python
import os
import pytest
from mypalclara.core.obsidian.client import ObsidianClient

pytestmark = pytest.mark.integration

@pytest.fixture
def live_client():
    token = os.environ.get("OBSIDIAN_DEV_TOKEN")
    if not token:
        pytest.skip("OBSIDIAN_DEV_TOKEN not set")
    return ObsidianClient("obsidian.shmp.app", token, verify_tls=True)

@pytest.mark.asyncio
async def test_live_list_vault(live_client):
    files = await live_client.list_vault()
    assert isinstance(files, list)

@pytest.mark.asyncio
async def test_live_search_simple(live_client):
    results = await live_client.search_simple("clara")
    assert isinstance(results, list)

@pytest.mark.asyncio
async def test_live_periodic_roundtrip(live_client):
    marker = "Clara integration test marker"
    await live_client.append_periodic("daily", marker + "\n")
    content = await live_client.get_periodic("daily")
    assert marker in content
```

**Step 2: Ensure pytest marker configured**

In `pyproject.toml`, add `integration` to `[tool.pytest.ini_options] markers`.

**Step 3: Run only when token set**

```bash
OBSIDIAN_DEV_TOKEN=$TOKEN pytest tests/integration/test_obsidian_live.py -v -m integration
```

**Step 4: Commit**

```bash
git add tests/integration/test_obsidian_live.py pyproject.toml
git commit -m "test(obsidian): add marked integration tests against live REST API"
```

---

### Task F2: End-to-end smoke in a Discord DM

**Not a code task.** After all previous tasks ship:

1. Restart the identity service with `SECRETS_ENCRYPTION_KEY` set.
2. Log into the identity SPA; configure the Obsidian token + host `obsidian.shmp.app`.
3. Restart the gateway.
4. In a Discord DM with Clara: `"what's in my Obsidian vault?"` — expect Clara to call `obsidian_list_vault` and describe folders.
5. `"search my vault for 'clara'"` — expect `obsidian_search`.
6. `"append to today's daily note: 'smoke test marker'"` — expect `obsidian_append_to_periodic_note`, then verify it appears in Obsidian.
7. Verify the prompt block shows up in debug logs: check that Clara's system prompt includes "User's Obsidian vault (obsidian.shmp.app): ...".

Document any issues in a new file `docs/plans/2026-04-20-obsidian-integration-smoke-notes.md` and fix before marking the feature complete.

---

## Environment variables summary

Add to `.env.example`:

```bash
# Identity service
SECRETS_ENCRYPTION_KEY=           # Fernet key; generate with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`

# Gateway — fetching Obsidian creds from identity service
IDENTITY_SERVICE_URL=http://localhost:18791
IDENTITY_SERVICE_SECRET=          # same as existing, if already set

# Development / integration tests only (never commit)
# OBSIDIAN_DEV_TOKEN=<token>
```

Document `SECRETS_ENCRYPTION_KEY` in `CLAUDE.md` under "Required" environment variables for the identity service.

---

## Definition of done

- [ ] All 27 tasks committed on `main` (or merged via PR)
- [ ] Identity service unit tests green, including new `test_obsidian_config.py` and `test_crypto.py`
- [ ] Main repo unit tests green (excluding pre-existing failures documented in MEMORY.md)
- [ ] Marked integration tests pass with `OBSIDIAN_DEV_TOKEN` set
- [ ] End-to-end Discord smoke test (Task F2) successful
- [ ] `.env.example` and `CLAUDE.md` updated with new env vars
- [ ] Token never appears in logs, tracebacks, or HTTP responses outside the service-auth endpoint

---

## Notes for the implementer

- **Don't skip the TDD cycle.** The handler pattern is repetitive; the test-first discipline catches subtle bugs (wrong header names, wrong endpoint paths) before they compound.
- **Verify the obsidian-local-rest-api OpenAPI spec** for exact request/response shapes — the tests above use plausible shapes but the live API is the source of truth. Adjust response parsing if it differs.
- **Don't commit `OBSIDIAN_DEV_TOKEN`.** Export locally, use only for integration tests.
- **If Alembic gets added to the identity service later**, convert the migration script in Task A3 into a proper Alembic revision.
- **Per-user tool filtering infrastructure** (Phase C) is reusable — mention it in the PR description so future per-user tools (GitHub, Google Drive) can adopt the same pattern.
