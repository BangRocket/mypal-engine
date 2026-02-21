# Identity Service Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a standalone FastAPI identity service that owns OAuth flows and user identity, fixing broken OAuth login in the web UI.

**Architecture:** Standalone Python (FastAPI) service in `identity/` at repo root. Owns `canonical_users`, `platform_links`, `oauth_tokens` tables in the shared PostgreSQL DB. Issues JWTs that Rails verifies. Rails delegates OAuth callbacks to it. Gateway continues trusting `X-Canonical-User-Id` from Rails (unchanged).

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy, PyJWT, httpx, uvicorn. PostgreSQL (shared with gateway).

**Design doc:** `docs/plans/2026-02-21-identity-service-design.md`

---

## Task 1: Scaffold Identity Service Package

**Files:**
- Create: `identity/__init__.py`
- Create: `identity/main.py`
- Create: `identity/config.py`
- Create: `identity/db.py`
- Create: `identity/pyproject.toml`

**Step 1: Create pyproject.toml**

```toml
[tool.poetry]
name = "clara-identity"
version = "0.1.0"
description = "OAuth identity service for MyPalClara"
package-mode = false

[tool.poetry.dependencies]
python = "^3.11,<3.14"
fastapi = "^0.115.0"
uvicorn = "^0.38.0"
sqlalchemy = "^2.0"
psycopg2-binary = "^2.9.9"
pyjwt = "^2.8.0"
httpx = "^0.28.0"
python-dotenv = "^1.0.0"
pydantic = "^2.0"

[tool.poetry.group.dev.dependencies]
pytest = "^8.0"
pytest-asyncio = "^0.24.0"
ruff = "^0.8"

[build-system]
requires = ["poetry-core"]
build-backend = "poetry.core.masonry.api"

[tool.ruff]
line-length = 120
```

**Step 2: Create identity/__init__.py**

Empty file.

**Step 3: Create identity/config.py**

```python
"""Configuration for the identity service."""

from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()

# JWT
JWT_SECRET = os.environ.get("IDENTITY_JWT_SECRET", "change-me-in-production")
JWT_EXPIRE_MINUTES = int(os.environ.get("IDENTITY_JWT_EXPIRE_MINUTES", "1440"))
JWT_ALGORITHM = "HS256"

# Service-to-service auth
SERVICE_SECRET = os.environ.get("IDENTITY_SERVICE_SECRET", "")

# Database
DATABASE_URL = os.environ.get("DATABASE_URL", "")

# Server
HOST = os.environ.get("IDENTITY_HOST", "0.0.0.0")
PORT = int(os.environ.get("IDENTITY_PORT", "18791"))

# OAuth providers — each needs client_id, client_secret, redirect_uri from env
PROVIDERS: dict[str, dict] = {
    "discord": {
        "authorize_url": "https://discord.com/api/oauth2/authorize",
        "token_url": "https://discord.com/api/oauth2/token",
        "user_url": "https://discord.com/api/users/@me",
        "scope": "identify email",
        "client_id_env": "DISCORD_OAUTH_CLIENT_ID",
        "client_secret_env": "DISCORD_OAUTH_CLIENT_SECRET",
        "redirect_uri_env": "DISCORD_OAUTH_REDIRECT_URI",
    },
    "google": {
        "authorize_url": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_url": "https://oauth2.googleapis.com/token",
        "user_url": "https://www.googleapis.com/oauth2/v2/userinfo",
        "scope": "openid email profile",
        "client_id_env": "GOOGLE_OAUTH_CLIENT_ID",
        "client_secret_env": "GOOGLE_OAUTH_CLIENT_SECRET",
        "redirect_uri_env": "GOOGLE_OAUTH_REDIRECT_URI",
    },
}


def available_providers() -> list[str]:
    """Return providers that have a client_id configured."""
    return [name for name, cfg in PROVIDERS.items() if os.environ.get(cfg["client_id_env"])]
```

**Step 4: Create identity/db.py**

```python
"""Database connection and models for the identity service."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    create_engine,
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker
from sqlalchemy.pool import QueuePool

from identity.config import DATABASE_URL

Base = declarative_base()


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def gen_uuid() -> str:
    return str(uuid.uuid4())


# ── Models ──────────────────────────────────────────────────────────────

class CanonicalUser(Base):
    __tablename__ = "canonical_users"

    id = Column(String, primary_key=True, default=gen_uuid)
    display_name = Column(String, nullable=False)
    primary_email = Column(String, nullable=True, unique=True)
    avatar_url = Column(String, nullable=True)
    status = Column(String, default="active", server_default="active", nullable=False)
    is_admin = Column(Boolean, default=False, server_default="0", nullable=False)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    platform_links = relationship("PlatformLink", back_populates="canonical_user")
    oauth_tokens = relationship("OAuthToken", back_populates="canonical_user")


class PlatformLink(Base):
    __tablename__ = "platform_links"

    id = Column(String, primary_key=True, default=gen_uuid)
    canonical_user_id = Column(String, ForeignKey("canonical_users.id"), nullable=False)
    platform = Column(String, nullable=False)
    platform_user_id = Column(String, nullable=False)
    prefixed_user_id = Column(String, nullable=False, unique=True)
    display_name = Column(String, nullable=True)
    linked_at = Column(DateTime, default=utcnow)
    linked_via = Column(String, nullable=True)

    __table_args__ = (Index("ix_platform_link_platform_user", "platform", "platform_user_id", unique=True),)

    canonical_user = relationship("CanonicalUser", back_populates="platform_links")


class OAuthToken(Base):
    __tablename__ = "oauth_tokens"

    id = Column(String, primary_key=True, default=gen_uuid)
    canonical_user_id = Column(String, ForeignKey("canonical_users.id"), nullable=False)
    provider = Column(String, nullable=False)
    access_token = Column(Text, nullable=False)
    refresh_token = Column(Text, nullable=True)
    expires_at = Column(DateTime, nullable=True)
    scopes = Column(Text, nullable=True)
    provider_user_id = Column(String, nullable=True)
    provider_data = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    __table_args__ = (Index("ix_oauth_token_user_provider", "canonical_user_id", "provider", unique=True),)

    canonical_user = relationship("CanonicalUser", back_populates="oauth_tokens")


# ── Engine & Session ──────────────────────────────────────────────────

_db_url = DATABASE_URL
if _db_url and _db_url.startswith("postgres://"):
    _db_url = _db_url.replace("postgres://", "postgresql://", 1)

if _db_url and _db_url.startswith("postgresql"):
    engine = create_engine(_db_url, poolclass=QueuePool, pool_size=5, max_overflow=10, pool_pre_ping=True)
else:
    engine = create_engine("sqlite:///identity.db", echo=False)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Create tables if they don't exist (idempotent)."""
    Base.metadata.create_all(bind=engine)
```

**Step 5: Create identity/main.py**

```python
"""Identity service entrypoint."""

from __future__ import annotations

import uvicorn

from identity.config import HOST, PORT
from identity.app import create_app
from identity.db import init_db

app = create_app()


def main():
    init_db()
    uvicorn.run("identity.main:app", host=HOST, port=PORT, reload=False)


if __name__ == "__main__":
    main()
```

**Step 6: Run `poetry install` in identity/ and verify it starts**

```bash
cd identity && poetry install && cd ..
```

Expected: Dependencies install successfully.

**Step 7: Commit**

```bash
git add identity/
git commit -m "feat: scaffold identity service package"
```

---

## Task 2: OAuth Flow Endpoints

**Files:**
- Create: `identity/oauth.py`
- Create: `identity/jwt_service.py`
- Create: `identity/app.py`
- Test: `identity/tests/test_oauth.py`

**Step 1: Create identity/jwt_service.py**

```python
"""JWT signing and verification."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import jwt

from identity.config import JWT_ALGORITHM, JWT_EXPIRE_MINUTES, JWT_SECRET


def encode(canonical_user_id: str, name: str = "") -> str:
    payload = {
        "sub": canonical_user_id,
        "name": name,
        "iat": datetime.now(timezone.utc),
        "exp": datetime.now(timezone.utc) + timedelta(minutes=JWT_EXPIRE_MINUTES),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode(token: str) -> dict | None:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
        return None
```

**Step 2: Create identity/oauth.py**

```python
"""OAuth flow logic — provider-agnostic."""

from __future__ import annotations

import json
import os
from urllib.parse import urlencode

import httpx

from identity.config import PROVIDERS


def authorize_url(provider: str) -> str:
    """Build the OAuth authorize URL for a provider."""
    cfg = PROVIDERS[provider]
    params = {
        "client_id": os.environ.get(cfg["client_id_env"], ""),
        "redirect_uri": os.environ.get(cfg["redirect_uri_env"], ""),
        "response_type": "code",
        "scope": cfg["scope"],
    }
    return f"{cfg['authorize_url']}?{urlencode(params)}"


async def exchange_code(provider: str, code: str) -> dict:
    """Exchange authorization code for access token."""
    cfg = PROVIDERS[provider]
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            cfg["token_url"],
            data={
                "client_id": os.environ.get(cfg["client_id_env"], ""),
                "client_secret": os.environ.get(cfg["client_secret_env"], ""),
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": os.environ.get(cfg["redirect_uri_env"], ""),
            },
            headers={"Accept": "application/json"},
        )
        return resp.json()


async def fetch_user_profile(provider: str, access_token: str) -> dict:
    """Fetch user profile from OAuth provider."""
    cfg = PROVIDERS[provider]
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            cfg["user_url"],
            headers={"Authorization": f"Bearer {access_token}"},
        )
        return resp.json()


def normalize_profile(provider: str, raw: dict) -> dict:
    """Normalize provider-specific profile into a common format.

    Returns: {platform_user_id, display_name, email, avatar_url}
    """
    if provider == "discord":
        avatar_hash = raw.get("avatar")
        avatar_url = (
            f"https://cdn.discordapp.com/avatars/{raw['id']}/{avatar_hash}.png"
            if avatar_hash
            else None
        )
        return {
            "platform_user_id": str(raw["id"]),
            "display_name": raw.get("global_name") or raw.get("username") or "User",
            "email": raw.get("email"),
            "avatar_url": avatar_url,
        }
    elif provider == "google":
        return {
            "platform_user_id": str(raw["id"]),
            "display_name": raw.get("name") or "User",
            "email": raw.get("email"),
            "avatar_url": raw.get("picture"),
        }
    else:
        # Generic fallback for future providers
        return {
            "platform_user_id": str(raw.get("id", "")),
            "display_name": raw.get("name") or raw.get("username") or "User",
            "email": raw.get("email"),
            "avatar_url": raw.get("avatar_url") or raw.get("picture"),
        }
```

**Step 3: Create identity/app.py with OAuth routes**

```python
"""FastAPI application for the identity service."""

from __future__ import annotations

import json
import logging
import os

from fastapi import Depends, FastAPI, Header, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy.orm import Session as DBSession

from identity import jwt_service, oauth
from identity.config import PROVIDERS, SERVICE_SECRET, available_providers
from identity.db import (
    CanonicalUser,
    OAuthToken,
    PlatformLink,
    SessionLocal,
    gen_uuid,
    get_db,
    utcnow,
)

logger = logging.getLogger("identity")


# ── Request/Response models ──────────────────────────────────────────

class AuthorizeRequest(BaseModel):
    provider: str

class CallbackRequest(BaseModel):
    provider: str
    code: str

class RefreshRequest(BaseModel):
    token: str

class EnsureLinkRequest(BaseModel):
    provider: str
    platform_user_id: str
    display_name: str = "User"

class UserResponse(BaseModel):
    id: str
    display_name: str
    avatar_url: str | None = None
    email: str | None = None

class LinkResponse(BaseModel):
    platform: str
    platform_user_id: str
    display_name: str | None = None
    linked_at: str | None = None


# ── Auth dependencies ────────────────────────────────────────────────

def require_service_secret(x_service_secret: str | None = Header(None)):
    """Verify the internal service secret for service-to-service calls."""
    if not SERVICE_SECRET:
        return  # No secret configured, allow all (dev mode)
    if x_service_secret != SERVICE_SECRET:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid service secret")


def get_current_user_from_jwt(
    authorization: str | None = Header(None),
    db: DBSession = Depends(get_db),
) -> CanonicalUser:
    """Extract user from Authorization: Bearer <jwt> header."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing or invalid token")

    payload = jwt_service.decode(authorization.removeprefix("Bearer ").strip())
    if not payload:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")

    user = db.query(CanonicalUser).filter(CanonicalUser.id == payload["sub"]).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    return user


# ── Helpers ──────────────────────────────────────────────────────────

def find_or_create_user(
    provider: str,
    profile: dict,
    token_data: dict,
    db: DBSession,
) -> CanonicalUser:
    """Find existing user by platform link, or create a new one.

    Also stores/updates the OAuth token.
    """
    normalized = oauth.normalize_profile(provider, profile)
    platform_user_id = normalized["platform_user_id"]
    prefixed = f"{provider}-{platform_user_id}"

    # Look up by platform link
    link = db.query(PlatformLink).filter(
        PlatformLink.platform == provider,
        PlatformLink.platform_user_id == platform_user_id,
    ).first()

    if link:
        user = link.canonical_user
        # Update display name and avatar if changed
        if normalized["display_name"] and normalized["display_name"] != user.display_name:
            user.display_name = normalized["display_name"]
        if normalized["avatar_url"] and normalized["avatar_url"] != user.avatar_url:
            user.avatar_url = normalized["avatar_url"]
        if normalized["email"] and not user.primary_email:
            user.primary_email = normalized["email"]
        user.updated_at = utcnow()
    else:
        # Create new canonical user
        user = CanonicalUser(
            id=gen_uuid(),
            display_name=normalized["display_name"],
            primary_email=normalized["email"],
            avatar_url=normalized["avatar_url"],
        )
        db.add(user)
        db.flush()  # Get the user ID

        # Create platform link
        link = PlatformLink(
            id=gen_uuid(),
            canonical_user_id=user.id,
            platform=provider,
            platform_user_id=platform_user_id,
            prefixed_user_id=prefixed,
            display_name=normalized["display_name"],
            linked_via="oauth",
        )
        db.add(link)

    # Store/update OAuth token
    existing_token = db.query(OAuthToken).filter(
        OAuthToken.canonical_user_id == user.id,
        OAuthToken.provider == provider,
    ).first()

    if existing_token:
        existing_token.access_token = token_data.get("access_token", "")
        existing_token.refresh_token = token_data.get("refresh_token")
        existing_token.provider_user_id = platform_user_id
        existing_token.provider_data = json.dumps(profile)
        existing_token.updated_at = utcnow()
    else:
        oauth_token = OAuthToken(
            id=gen_uuid(),
            canonical_user_id=user.id,
            provider=provider,
            access_token=token_data.get("access_token", ""),
            refresh_token=token_data.get("refresh_token"),
            provider_user_id=platform_user_id,
            provider_data=json.dumps(profile),
        )
        db.add(oauth_token)

    db.commit()
    db.refresh(user)
    return user


# ── App factory ──────────────────────────────────────────────────────

def create_app() -> FastAPI:
    app = FastAPI(
        title="Clara Identity Service",
        description="OAuth and user identity management",
        version="0.1.0",
        docs_url="/docs",
        redoc_url="/redoc",
    )

    cors_origins = os.getenv(
        "IDENTITY_CORS_ORIGINS", "http://localhost:3000,http://localhost:5173"
    ).split(",")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in cors_origins],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Health ───────────────────────────────────────────────────────

    @app.get("/health")
    async def health():
        return {"status": "ok", "service": "clara-identity"}

    # ── OAuth endpoints (browser-facing) ─────────────────────────────

    @app.post("/oauth/authorize")
    async def oauth_authorize(body: AuthorizeRequest):
        if body.provider not in PROVIDERS:
            raise HTTPException(status_code=400, detail=f"Unknown provider: {body.provider}")
        if body.provider not in available_providers():
            raise HTTPException(status_code=400, detail=f"Provider not configured: {body.provider}")
        url = oauth.authorize_url(body.provider)
        return {"url": url}

    @app.post("/oauth/callback")
    async def oauth_callback(body: CallbackRequest, db: DBSession = Depends(get_db)):
        if body.provider not in PROVIDERS:
            raise HTTPException(status_code=400, detail=f"Unknown provider: {body.provider}")

        # Exchange code for token
        token_data = await oauth.exchange_code(body.provider, body.code)
        if "access_token" not in token_data:
            logger.error(f"Token exchange failed for {body.provider}: {token_data}")
            raise HTTPException(status_code=422, detail="OAuth token exchange failed")

        # Fetch user profile
        profile = await oauth.fetch_user_profile(body.provider, token_data["access_token"])

        # Find or create user
        user = find_or_create_user(body.provider, profile, token_data, db)

        # Issue JWT
        token = jwt_service.encode(user.id, name=user.display_name)

        return {
            "token": token,
            "user": {
                "id": user.id,
                "display_name": user.display_name,
                "avatar_url": user.avatar_url,
                "email": user.primary_email,
            },
        }

    @app.post("/oauth/refresh")
    async def oauth_refresh(body: RefreshRequest):
        payload = jwt_service.decode(body.token)
        if not payload:
            raise HTTPException(status_code=401, detail="Invalid or expired token")
        new_token = jwt_service.encode(payload["sub"], name=payload.get("name", ""))
        return {"token": new_token}

    # ── User endpoints (JWT-authenticated) ───────────────────────────

    @app.get("/users/me")
    async def get_me(
        user: CanonicalUser = Depends(get_current_user_from_jwt),
        db: DBSession = Depends(get_db),
    ):
        links = db.query(PlatformLink).filter(PlatformLink.canonical_user_id == user.id).all()
        return {
            "id": user.id,
            "display_name": user.display_name,
            "avatar_url": user.avatar_url,
            "email": user.primary_email,
            "status": user.status,
            "is_admin": user.is_admin,
            "links": [
                {
                    "platform": l.platform,
                    "platform_user_id": l.platform_user_id,
                    "display_name": l.display_name,
                    "linked_at": l.linked_at.isoformat() if l.linked_at else None,
                }
                for l in links
            ],
        }

    # ── Internal endpoints (service secret) ──────────────────────────

    @app.get("/users/by-platform/{provider}/{platform_user_id}")
    async def get_user_by_platform(
        provider: str,
        platform_user_id: str,
        db: DBSession = Depends(get_db),
        _=Depends(require_service_secret),
    ):
        link = db.query(PlatformLink).filter(
            PlatformLink.platform == provider,
            PlatformLink.platform_user_id == platform_user_id,
        ).first()
        if not link:
            raise HTTPException(status_code=404, detail="User not found")
        user = link.canonical_user
        return {
            "id": user.id,
            "display_name": user.display_name,
            "avatar_url": user.avatar_url,
            "email": user.primary_email,
        }

    @app.post("/users/ensure-link")
    async def ensure_link(
        body: EnsureLinkRequest,
        db: DBSession = Depends(get_db),
        _=Depends(require_service_secret),
    ):
        prefixed = f"{body.provider}-{body.platform_user_id}"

        # Check if link already exists
        link = db.query(PlatformLink).filter(
            PlatformLink.platform == body.provider,
            PlatformLink.platform_user_id == body.platform_user_id,
        ).first()

        if link:
            return {"canonical_user_id": link.canonical_user_id}

        # Create new user + link
        user = CanonicalUser(
            id=gen_uuid(),
            display_name=body.display_name,
        )
        db.add(user)
        db.flush()

        link = PlatformLink(
            id=gen_uuid(),
            canonical_user_id=user.id,
            platform=body.provider,
            platform_user_id=body.platform_user_id,
            prefixed_user_id=prefixed,
            display_name=body.display_name,
            linked_via="auto",
        )
        db.add(link)
        db.commit()

        return {"canonical_user_id": user.id}

    @app.get("/auth/config")
    async def auth_config():
        """Return available providers and config for the frontend."""
        providers = available_providers()
        return {
            "dev_mode": os.environ.get("WEB_DEV_MODE") == "true",
            "providers": {p: p in providers for p in PROVIDERS},
        }

    return app
```

**Step 4: Write test for find_or_create_user**

Create `identity/tests/__init__.py` (empty) and `identity/tests/test_oauth.py`:

```python
"""Tests for OAuth flow and user creation."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from identity.db import Base, CanonicalUser, PlatformLink, OAuthToken
from identity.app import find_or_create_user


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


class TestFindOrCreateUser:
    def test_creates_new_user_on_first_login(self, db):
        profile = {"id": "12345", "global_name": "Josh", "username": "josh", "avatar": "abc123", "email": "j@x.com"}
        token_data = {"access_token": "tok_123", "refresh_token": "ref_123"}

        user = find_or_create_user("discord", profile, token_data, db)

        assert user.display_name == "Josh"
        assert user.primary_email == "j@x.com"
        assert user.avatar_url == "https://cdn.discordapp.com/avatars/12345/abc123.png"

        # Platform link created
        link = db.query(PlatformLink).filter(PlatformLink.canonical_user_id == user.id).first()
        assert link is not None
        assert link.platform == "discord"
        assert link.platform_user_id == "12345"
        assert link.prefixed_user_id == "discord-12345"
        assert link.linked_via == "oauth"

        # OAuth token stored
        token = db.query(OAuthToken).filter(OAuthToken.canonical_user_id == user.id).first()
        assert token is not None
        assert token.access_token == "tok_123"
        assert token.provider == "discord"

    def test_returns_existing_user_on_repeat_login(self, db):
        profile = {"id": "12345", "global_name": "Josh", "username": "josh", "avatar": "abc123"}
        token_data = {"access_token": "tok_1"}

        user1 = find_or_create_user("discord", profile, token_data, db)

        token_data2 = {"access_token": "tok_2"}
        user2 = find_or_create_user("discord", profile, token_data2, db)

        assert user1.id == user2.id
        # Token updated
        token = db.query(OAuthToken).filter(OAuthToken.canonical_user_id == user1.id).first()
        assert token.access_token == "tok_2"

    def test_updates_display_name_on_repeat_login(self, db):
        profile = {"id": "12345", "global_name": "Josh", "username": "josh", "avatar": None}
        token_data = {"access_token": "tok_1"}
        user1 = find_or_create_user("discord", profile, token_data, db)

        profile2 = {"id": "12345", "global_name": "Joshua", "username": "josh", "avatar": None}
        user2 = find_or_create_user("discord", profile2, token_data, db)

        assert user2.id == user1.id
        assert user2.display_name == "Joshua"

    def test_google_provider(self, db):
        profile = {"id": "g-999", "name": "Josh G", "email": "j@gmail.com", "picture": "https://img.com/j.jpg"}
        token_data = {"access_token": "goog_tok"}

        user = find_or_create_user("google", profile, token_data, db)

        assert user.display_name == "Josh G"
        assert user.avatar_url == "https://img.com/j.jpg"
        link = db.query(PlatformLink).filter(PlatformLink.canonical_user_id == user.id).first()
        assert link.platform == "google"
        assert link.platform_user_id == "g-999"
```

**Step 5: Run tests**

```bash
cd identity && poetry run pytest tests/test_oauth.py -v
```

Expected: All 4 tests PASS.

**Step 6: Commit**

```bash
git add identity/
git commit -m "feat: identity service OAuth flow and user creation"
```

---

## Task 3: Write test for JWT service

**Files:**
- Create: `identity/tests/test_jwt.py`

**Step 1: Write tests**

```python
"""Tests for JWT encoding/decoding."""

import time

import pytest

from identity import jwt_service
from identity.config import JWT_SECRET


class TestJwtService:
    def test_encode_decode_roundtrip(self):
        token = jwt_service.encode("user-123", name="Josh")
        payload = jwt_service.decode(token)

        assert payload is not None
        assert payload["sub"] == "user-123"
        assert payload["name"] == "Josh"
        assert "iat" in payload
        assert "exp" in payload

    def test_decode_invalid_token(self):
        assert jwt_service.decode("garbage") is None

    def test_decode_tampered_token(self):
        token = jwt_service.encode("user-123")
        # Flip a character in the signature
        tampered = token[:-1] + ("A" if token[-1] != "A" else "B")
        assert jwt_service.decode(tampered) is None
```

**Step 2: Run tests**

```bash
cd identity && poetry run pytest tests/test_jwt.py -v
```

Expected: All 3 tests PASS.

**Step 3: Commit**

```bash
git add identity/tests/test_jwt.py
git commit -m "test: JWT service encode/decode tests"
```

---

## Task 4: Write test for API endpoints

**Files:**
- Create: `identity/tests/test_api.py`

**Step 1: Write tests using FastAPI TestClient**

```python
"""Tests for identity service API endpoints."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from identity.app import create_app
from identity.db import Base, CanonicalUser, PlatformLink, gen_uuid, get_db, utcnow
from identity import jwt_service


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


@pytest.fixture
def client(db_session):
    app = create_app()

    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app)


@pytest.fixture
def user_with_link(db_session):
    """Create a canonical user with a discord platform link."""
    user = CanonicalUser(
        id=gen_uuid(),
        display_name="Test User",
        primary_email="test@example.com",
        avatar_url="https://example.com/avatar.png",
    )
    db_session.add(user)
    db_session.flush()

    link = PlatformLink(
        id=gen_uuid(),
        canonical_user_id=user.id,
        platform="discord",
        platform_user_id="disc-12345",
        prefixed_user_id="discord-disc-12345",
        display_name="Test User",
        linked_via="oauth",
    )
    db_session.add(link)
    db_session.commit()
    db_session.refresh(user)
    return user


class TestHealth:
    def test_health(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


class TestUsersMe:
    def test_returns_user_with_links(self, client, user_with_link):
        token = jwt_service.encode(user_with_link.id, name=user_with_link.display_name)
        resp = client.get("/users/me", headers={"Authorization": f"Bearer {token}"})

        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == user_with_link.id
        assert data["display_name"] == "Test User"
        assert len(data["links"]) == 1
        assert data["links"][0]["platform"] == "discord"

    def test_rejects_missing_token(self, client):
        resp = client.get("/users/me")
        assert resp.status_code == 401

    def test_rejects_invalid_token(self, client):
        resp = client.get("/users/me", headers={"Authorization": "Bearer garbage"})
        assert resp.status_code == 401


class TestUserByPlatform:
    def test_finds_user_by_platform(self, client, user_with_link):
        resp = client.get("/users/by-platform/discord/disc-12345")
        assert resp.status_code == 200
        assert resp.json()["id"] == user_with_link.id

    def test_returns_404_for_unknown(self, client):
        resp = client.get("/users/by-platform/discord/nonexistent")
        assert resp.status_code == 404


class TestEnsureLink:
    def test_creates_new_user(self, client, db_session):
        resp = client.post("/users/ensure-link", json={
            "provider": "discord",
            "platform_user_id": "new-user-999",
            "display_name": "New Guy",
        })
        assert resp.status_code == 200
        cuid = resp.json()["canonical_user_id"]
        assert cuid is not None

        # Verify in DB
        user = db_session.query(CanonicalUser).filter(CanonicalUser.id == cuid).first()
        assert user.display_name == "New Guy"

    def test_idempotent(self, client, db_session):
        body = {"provider": "discord", "platform_user_id": "idem-123", "display_name": "Same"}
        resp1 = client.post("/users/ensure-link", json=body)
        resp2 = client.post("/users/ensure-link", json=body)
        assert resp1.json()["canonical_user_id"] == resp2.json()["canonical_user_id"]

        # Only one user created
        count = db_session.query(CanonicalUser).count()
        assert count == 1


class TestAuthConfig:
    def test_returns_config(self, client):
        resp = client.get("/auth/config")
        assert resp.status_code == 200
        data = resp.json()
        assert "providers" in data
        assert "dev_mode" in data
```

**Step 2: Run tests**

```bash
cd identity && poetry run pytest tests/test_api.py -v
```

Expected: All tests PASS.

**Step 3: Commit**

```bash
git add identity/tests/
git commit -m "test: identity service API endpoint tests"
```

---

## Task 5: Dockerfile and Railway Config

**Files:**
- Create: `identity/Dockerfile`
- Create: `identity/railway.toml`

**Step 1: Create identity/Dockerfile**

```dockerfile
FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

RUN pip install poetry

COPY pyproject.toml poetry.lock* ./
RUN poetry config virtualenvs.create false && \
    poetry install --only main --no-interaction --no-ansi

COPY identity/ ./identity/

RUN groupadd --system --gid 1000 app && \
    useradd app --uid 1000 --gid 1000 --create-home --shell /bin/bash
USER app:app

EXPOSE 18791

CMD ["python", "-m", "identity.main"]
```

**Step 2: Create identity/railway.toml**

```toml
[build]
dockerfilePath = "Dockerfile"

[deploy]
healthcheckPath = "/health"
healthcheckTimeout = 120
restartPolicyType = "ON_FAILURE"
restartPolicyMaxRetries = 5
```

**Step 3: Commit**

```bash
git add identity/Dockerfile identity/railway.toml
git commit -m "feat: identity service Dockerfile and Railway config"
```

---

## Task 6: Integrate Rails with Identity Service

**Files:**
- Create: `web-ui/backend/app/services/identity_proxy.rb`
- Modify: `web-ui/backend/app/controllers/auth_controller.rb`
- Modify: `web-ui/backend/app/services/jwt_service.rb` (verify-only)

**Step 1: Create identity_proxy.rb**

```ruby
require "net/http"
require "json"
require "uri"

class IdentityProxy
  TIMEOUT = 10

  def self.post(path, body: nil)
    base_url = ENV.fetch("IDENTITY_SERVICE_URL", "http://127.0.0.1:18791")
    uri = URI("#{base_url}#{path}")

    http = Net::HTTP.new(uri.host, uri.port)
    http.use_ssl = uri.scheme == "https"
    http.open_timeout = TIMEOUT
    http.read_timeout = TIMEOUT

    request = Net::HTTP::Post.new(uri)
    request["Content-Type"] = "application/json"

    service_secret = ENV["IDENTITY_SERVICE_SECRET"]
    request["X-Service-Secret"] = service_secret if service_secret.present?

    request.body = body.to_json if body.present?

    response = http.request(request)
    parsed = JSON.parse(response.body)

    { status: response.code.to_i, body: parsed }
  rescue StandardError => e
    Rails.logger.error("IdentityProxy error: #{e.class} - #{e.message}")
    { status: 502, body: { "error" => "Identity service unavailable: #{e.message}" } }
  end

  def self.get(path, headers: {})
    base_url = ENV.fetch("IDENTITY_SERVICE_URL", "http://127.0.0.1:18791")
    uri = URI("#{base_url}#{path}")

    http = Net::HTTP.new(uri.host, uri.port)
    http.use_ssl = uri.scheme == "https"
    http.open_timeout = TIMEOUT
    http.read_timeout = TIMEOUT

    request = Net::HTTP::Get.new(uri)
    request["Content-Type"] = "application/json"
    headers.each { |k, v| request[k] = v }

    service_secret = ENV["IDENTITY_SERVICE_SECRET"]
    request["X-Service-Secret"] = service_secret if service_secret.present?

    response = http.request(request)
    parsed = JSON.parse(response.body)

    { status: response.code.to_i, body: parsed }
  rescue StandardError => e
    Rails.logger.error("IdentityProxy error: #{e.class} - #{e.message}")
    { status: 502, body: { "error" => "Identity service unavailable: #{e.message}" } }
  end
end
```

**Step 2: Modify auth_controller.rb**

Replace the entire file with:

```ruby
class AuthController < ApplicationController
  skip_before_action :authenticate_user!, only: [:auth_config, :dev_login, :login, :callback, :logout]

  def auth_config
    result = IdentityProxy.get("/auth/config")
    render json: result[:body], status: result[:status]
  end

  def dev_login
    unless ENV["WEB_DEV_MODE"] == "true"
      return render json: { error: "Dev mode disabled" }, status: :forbidden
    end

    user = User.find_or_create_by(canonical_user_id: "00000000-0000-0000-0000-000000000dev") do |u|
      u.display_name = ENV.fetch("WEB_DEV_USER_NAME", "Dev User")
    end

    token = JwtService.encode(user.canonical_user_id, name: user.display_name)
    set_auth_cookie(token)
    render json: { token: token, user: user_response(user) }
  end

  def login
    result = IdentityProxy.post("/oauth/authorize", body: { provider: params[:provider] })
    render json: result[:body], status: result[:status]
  end

  def callback
    provider = params[:provider]
    code = params[:code]

    unless provider.present? && code.present?
      return render json: { error: "Invalid callback" }, status: :bad_request
    end

    result = IdentityProxy.post("/oauth/callback", body: { provider: provider, code: code })

    unless result[:status] == 200
      if request.xhr? || request.content_type&.include?("json")
        return render json: result[:body], status: result[:status]
      else
        return redirect_to "/login?error=oauth_failed", allow_other_host: false
      end
    end

    identity_user = result[:body]
    jwt = identity_user["token"]

    # Find or create local Rails user
    user = User.find_or_create_by!(canonical_user_id: identity_user.dig("user", "id")) do |u|
      u.display_name = identity_user.dig("user", "display_name") || "User"
      u.avatar_url = identity_user.dig("user", "avatar_url")
    end

    # Update display name and avatar if changed
    changed = false
    new_name = identity_user.dig("user", "display_name")
    if new_name.present? && new_name != user.display_name
      user.display_name = new_name
      changed = true
    end
    new_avatar = identity_user.dig("user", "avatar_url")
    if new_avatar.present? && new_avatar != user.avatar_url
      user.avatar_url = new_avatar
      changed = true
    end
    user.save! if changed

    set_auth_cookie(jwt)

    if request.xhr? || request.content_type&.include?("json")
      render json: { token: jwt, user: user_response(user) }
    else
      redirect_to "/", allow_other_host: false
    end
  end

  def logout
    cookies.delete(:access_token)
    render json: { ok: true }
  end

  def me
    render json: user_response(current_user)
  end

  def link
    provider = params[:provider]
    code = params[:code]

    # Exchange code via identity service
    result = IdentityProxy.post("/oauth/callback", body: { provider: provider, code: code })
    unless result[:status] == 200
      return render json: result[:body], status: result[:status]
    end

    render json: { ok: true }
  end

  def unlink
    # TODO: add unlink endpoint to identity service
    render json: { ok: true }
  end

  private

  def set_auth_cookie(token)
    cookies[:access_token] = {
      value: token,
      httponly: true,
      secure: Rails.env.production?,
      same_site: :lax,
      expires: JwtService::EXPIRE_MINUTES.minutes.from_now
    }
  end

  def user_response(user)
    {
      id: user.canonical_user_id,
      display_name: user.display_name,
      avatar_url: user.avatar_url
    }
  end
end
```

**Step 3: Update JwtService to use shared IDENTITY_JWT_SECRET**

Replace `web-ui/backend/app/services/jwt_service.rb`:

```ruby
class JwtService
  SECRET = ENV.fetch("IDENTITY_JWT_SECRET", ENV.fetch("WEB_SECRET_KEY", "change-me-in-production"))
  EXPIRE_MINUTES = ENV.fetch("WEB_JWT_EXPIRE_MINUTES", "1440").to_i

  def self.encode(canonical_user_id, extra_claims = {})
    payload = {
      sub: canonical_user_id,
      exp: EXPIRE_MINUTES.minutes.from_now.to_i,
      iat: Time.now.to_i
    }.merge(extra_claims)
    JWT.encode(payload, SECRET, "HS256")
  end

  def self.decode(token)
    JWT.decode(token, SECRET, true, algorithm: "HS256").first
  rescue JWT::DecodeError, JWT::ExpiredSignature, JWT::VerificationError
    nil
  end
end
```

**Step 4: Delete OauthService (no longer needed)**

```bash
rm web-ui/backend/app/services/oauth_service.rb
```

**Step 5: Run Rails tests if any exist, verify app boots**

```bash
cd web-ui/backend && bundle exec rails runner "puts 'OK'"
```

Expected: `OK` — no load errors.

**Step 6: Commit**

```bash
git add web-ui/backend/app/services/identity_proxy.rb web-ui/backend/app/controllers/auth_controller.rb web-ui/backend/app/services/jwt_service.rb
git rm web-ui/backend/app/services/oauth_service.rb
git commit -m "feat: integrate Rails with identity service for OAuth"
```

---

## Task 7: Update Gateway to Delegate Identity Writes

**Files:**
- Modify: `mypalclara/db/user_identity.py` — add identity service client
- Modify: `mypalclara/gateway/api/users.py` — no change needed (read-only, still works)

**Step 1: Add identity service client to user_identity.py**

Add this function at the end of `mypalclara/db/user_identity.py`:

```python
def ensure_platform_link_via_service(
    prefixed_user_id: str,
    display_name: str | None = None,
) -> str | None:
    """Create user+link via the identity service (preferred path).

    Falls back to local ensure_platform_link() if identity service
    is unavailable.

    Returns the canonical_user_id, or None on failure.
    """
    import httpx
    import os

    identity_url = os.getenv("IDENTITY_SERVICE_URL")
    if not identity_url:
        # No identity service configured — use local path
        ensure_platform_link(prefixed_user_id, display_name)
        return None

    # Parse prefixed_user_id
    parts = prefixed_user_id.split("-", 1)
    if len(parts) != 2:
        logger.warning(f"Invalid prefixed_user_id format: {prefixed_user_id}")
        ensure_platform_link(prefixed_user_id, display_name)
        return None

    provider, platform_user_id = parts

    try:
        headers = {"Content-Type": "application/json"}
        service_secret = os.getenv("IDENTITY_SERVICE_SECRET")
        if service_secret:
            headers["X-Service-Secret"] = service_secret

        resp = httpx.post(
            f"{identity_url}/users/ensure-link",
            json={
                "provider": provider,
                "platform_user_id": platform_user_id,
                "display_name": display_name or "User",
            },
            headers=headers,
            timeout=10,
        )
        if resp.status_code == 200:
            return resp.json().get("canonical_user_id")
        else:
            logger.warning(f"Identity service returned {resp.status_code}: {resp.text}")
    except Exception as e:
        logger.warning(f"Identity service unavailable ({e}), falling back to local")

    # Fallback
    ensure_platform_link(prefixed_user_id, display_name)
    return None
```

**Step 2: Commit**

```bash
git add mypalclara/db/user_identity.py
git commit -m "feat: gateway delegates identity writes to identity service"
```

---

## Task 8: Add __main__.py and verify end-to-end

**Files:**
- Create: `identity/__main__.py`

**Step 1: Create identity/__main__.py**

```python
"""Allow running with `python -m identity`."""

from identity.main import main

main()
```

**Step 2: Verify identity service starts locally**

```bash
cd identity && poetry run python -m identity
```

Expected: Uvicorn starts on port 18791, `/health` responds `{"status": "ok"}`.

**Step 3: Verify all identity tests pass**

```bash
cd identity && poetry run pytest tests/ -v
```

Expected: All tests PASS.

**Step 4: Verify Rails still boots**

```bash
cd web-ui/backend && bundle exec rails runner "puts 'OK'"
```

Expected: `OK`.

**Step 5: Commit**

```bash
git add identity/__main__.py
git commit -m "feat: identity service __main__.py for python -m identity"
```

---

## Task 9: Final verification and cleanup

**Step 1: Run ruff on identity code**

```bash
cd identity && poetry run ruff check . && poetry run ruff format .
```

Fix any issues.

**Step 2: Run all identity tests one final time**

```bash
cd identity && poetry run pytest tests/ -v
```

Expected: All PASS.

**Step 3: Run existing gateway tests to verify no regressions**

```bash
poetry run pytest tests/ -v -x --timeout=30 -k "not TestDirectAnthropicProvider and not test_exact_overlap and not TestUnifiedToolCalling"
```

Expected: No new failures (pre-existing failures from MEMORY.md are acceptable).

**Step 4: Commit any lint fixes**

```bash
git add -A && git commit -m "style: lint identity service code"
```

---

## Summary

| Task | What | Files |
|------|------|-------|
| 1 | Scaffold package | `identity/{__init__,main,config,db,pyproject.toml}` |
| 2 | OAuth + user creation | `identity/{oauth,jwt_service,app}.py`, test |
| 3 | JWT tests | `identity/tests/test_jwt.py` |
| 4 | API endpoint tests | `identity/tests/test_api.py` |
| 5 | Dockerfile + Railway | `identity/{Dockerfile,railway.toml}` |
| 6 | Rails integration | `identity_proxy.rb`, auth_controller, jwt_service, delete oauth_service |
| 7 | Gateway delegation | `mypalclara/db/user_identity.py` |
| 8 | __main__.py + e2e verify | `identity/__main__.py` |
| 9 | Lint + final verification | — |
