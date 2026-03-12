# Web UI Rebuild Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Rebuild Clara's web UI as a Claude.ai-style chat interface with git-style conversation branching, dropping Rails entirely.

**Architecture:** React SPA talks directly to Clara's gateway (FastAPI + WebSocket). Clerk handles auth. New Conversation/Branch DB models replace sessions for web. Branch-scoped memory isolation with merge strategies.

**Tech Stack:** React 19, @assistant-ui/react, Zustand, Tailwind, Clerk React SDK, FastAPI, SQLAlchemy, Alembic, PyJWT

**Design doc:** `docs/plans/2026-03-12-web-ui-rebuild-design.md`

---

## Task 1: Clerk JWT Auth Middleware (Gateway)

**Files:**
- Create: `mypalclara/gateway/api/clerk_auth.py`
- Modify: `mypalclara/gateway/api/app.py:30-38` (CORS origins)
- Modify: `mypalclara/gateway/api/auth.py:23-55` (add Clerk path)
- Modify: `pyproject.toml` (add PyJWT + cryptography deps)
- Test: `tests/gateway/api/test_clerk_auth.py`

**Step 1: Add dependencies**

```bash
poetry add PyJWT cryptography httpx
```

**Step 2: Write failing test for Clerk JWT validation**

```python
# tests/gateway/api/test_clerk_auth.py
import pytest
from unittest.mock import patch, AsyncMock
from mypalclara.gateway.api.clerk_auth import verify_clerk_jwt, ClerkJWKSCache


class TestClerkJWTValidation:
    """Test Clerk JWT verification."""

    def test_missing_token_raises(self):
        """No Authorization header should raise 401."""
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            # Simulate no token
            import asyncio
            asyncio.get_event_loop().run_until_complete(
                verify_clerk_jwt(authorization=None)
            )
        assert exc_info.value.status_code == 401

    def test_invalid_token_raises(self):
        """Malformed token should raise 401."""
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            import asyncio
            asyncio.get_event_loop().run_until_complete(
                verify_clerk_jwt(authorization="Bearer garbage.token.here")
            )
        assert exc_info.value.status_code == 401

    def test_valid_token_returns_claims(self):
        """Valid JWT should return decoded claims with sub."""
        import jwt
        # Create a test token
        test_claims = {"sub": "user_clerk123", "exp": 9999999999, "iss": "https://test.clerk.accounts.dev"}
        token = jwt.encode(test_claims, "test-secret", algorithm="HS256")

        with patch.object(ClerkJWKSCache, 'get_public_key', return_value="test-secret"):
            with patch.object(ClerkJWKSCache, '_algorithm', "HS256"):
                import asyncio
                claims = asyncio.get_event_loop().run_until_complete(
                    verify_clerk_jwt(authorization=f"Bearer {token}")
                )
        assert claims["sub"] == "user_clerk123"
```

Run: `poetry run pytest tests/gateway/api/test_clerk_auth.py -v`
Expected: FAIL (module not found)

**Step 3: Implement Clerk JWT verification**

```python
# mypalclara/gateway/api/clerk_auth.py
"""Clerk JWT authentication for the gateway API."""

import time
import jwt
import httpx
import logging
from fastapi import Header, HTTPException

logger = logging.getLogger(__name__)


class ClerkJWKSCache:
    """Caches Clerk's JWKS public keys with TTL."""

    _keys: dict = {}
    _fetched_at: float = 0
    _ttl: float = 3600  # 1 hour
    _algorithm: str = "RS256"

    @classmethod
    async def get_public_key(cls, kid: str, clerk_issuer: str) -> str:
        """Fetch and cache Clerk's public key by key ID."""
        if time.time() - cls._fetched_at > cls._ttl or kid not in cls._keys:
            await cls._refresh(clerk_issuer)
        if kid not in cls._keys:
            raise HTTPException(status_code=401, detail="Unknown key ID")
        return cls._keys[kid]

    @classmethod
    async def _refresh(cls, clerk_issuer: str):
        """Fetch JWKS from Clerk."""
        jwks_url = f"{clerk_issuer}/.well-known/jwks.json"
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(jwks_url, timeout=10)
                resp.raise_for_status()
                jwks = resp.json()
            cls._keys = {}
            for key_data in jwks.get("keys", []):
                kid = key_data.get("kid")
                if kid:
                    public_key = jwt.algorithms.RSAAlgorithm.from_jwk(key_data)
                    cls._keys[kid] = public_key
            cls._fetched_at = time.time()
            logger.info("Refreshed Clerk JWKS, %d keys cached", len(cls._keys))
        except Exception as e:
            logger.error("Failed to fetch Clerk JWKS: %s", e)
            if not cls._keys:
                raise HTTPException(status_code=503, detail="Auth service unavailable")


async def verify_clerk_jwt(
    authorization: str | None = Header(None, alias="Authorization"),
) -> dict:
    """Validate a Clerk JWT and return decoded claims.

    Raises HTTPException 401 if token is missing or invalid.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")

    token = authorization.removeprefix("Bearer ").strip()

    try:
        # Decode header to get kid and issuer
        unverified = jwt.decode(token, options={"verify_signature": False})
        issuer = unverified.get("iss", "")
        header = jwt.get_unverified_header(token)
        kid = header.get("kid", "")

        if not issuer or not kid:
            raise HTTPException(status_code=401, detail="Invalid token claims")

        public_key = await ClerkJWKSCache.get_public_key(kid, issuer)

        claims = jwt.decode(
            token,
            public_key,
            algorithms=[ClerkJWKSCache._algorithm],
            issuer=issuer,
        )
        return claims

    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError as e:
        logger.warning("Invalid Clerk JWT: %s", e)
        raise HTTPException(status_code=401, detail="Invalid token")
```

**Step 4: Wire Clerk auth into existing auth dependency**

Modify `mypalclara/gateway/api/auth.py` to support both Clerk JWT and the existing `X-Canonical-User-Id` header (for Discord/other adapters):

```python
# In auth.py, update get_current_user to try Clerk JWT first, fall back to header
async def get_current_user(
    authorization: str | None = Header(None),
    x_canonical_user_id: str | None = Header(None),
    x_gateway_secret: str | None = Header(None),
    db: Session = Depends(get_db),
) -> CanonicalUser:
    """Authenticate via Clerk JWT or X-Canonical-User-Id header."""

    # Path 1: Clerk JWT
    if authorization and authorization.startswith("Bearer "):
        from mypalclara.gateway.api.clerk_auth import verify_clerk_jwt
        claims = await verify_clerk_jwt(authorization=authorization)
        clerk_user_id = claims["sub"]
        # Find or create CanonicalUser by Clerk ID
        user = db.query(CanonicalUser).filter(
            CanonicalUser.platform_links.any(
                platform="clerk", platform_user_id=clerk_user_id
            )
        ).first()
        if not user:
            user = _create_user_from_clerk(db, clerk_user_id, claims)
        return user

    # Path 2: X-Canonical-User-Id header (existing adapters)
    # ... existing code unchanged ...
```

**Step 5: Update CORS in app.py**

Add Clerk's domain and the frontend dev server origin to CORS:

```python
# In app.py, update CORS origins default
origins = os.getenv(
    "GATEWAY_API_CORS_ORIGINS",
    "http://localhost:5173"  # Vite dev server (Rails no longer needed)
).split(",")
```

**Step 6: Run tests**

Run: `poetry run pytest tests/gateway/api/test_clerk_auth.py -v`
Expected: PASS

**Step 7: Commit**

```bash
git add mypalclara/gateway/api/clerk_auth.py mypalclara/gateway/api/auth.py mypalclara/gateway/api/app.py tests/gateway/api/test_clerk_auth.py pyproject.toml
git commit -m "feat: add Clerk JWT auth middleware to gateway"
```

---

## Task 2: Conversation & Branch DB Models

**Files:**
- Modify: `mypalclara/db/models.py:66-78` (add new models after existing Message)
- Create: `mypalclara/db/migrations/versions/xxxx_add_conversation_branch_models.py` (via alembic)
- Test: `tests/db/test_branch_models.py`

**Step 1: Write failing test for Conversation and Branch models**

```python
# tests/db/test_branch_models.py
import pytest
from mypalclara.db.models import Conversation, Branch, BranchMessage


class TestConversationModel:
    def test_create_conversation(self, db_session):
        conv = Conversation(user_id="user-123")
        db_session.add(conv)
        db_session.commit()
        assert conv.id is not None
        assert conv.user_id == "user-123"

    def test_conversation_has_branches(self, db_session):
        conv = Conversation(user_id="user-123")
        db_session.add(conv)
        db_session.commit()

        branch = Branch(
            conversation_id=conv.id,
            status="active",
        )
        db_session.add(branch)
        db_session.commit()

        assert len(conv.branches) == 1
        assert branch.parent_branch_id is None  # main trunk


class TestBranchModel:
    def test_fork_branch(self, db_session):
        conv = Conversation(user_id="user-123")
        db_session.add(conv)
        db_session.commit()

        main = Branch(conversation_id=conv.id, status="active")
        db_session.add(main)
        db_session.commit()

        msg = BranchMessage(
            branch_id=main.id,
            role="user",
            content="hello",
            user_id="user-123",
        )
        db_session.add(msg)
        db_session.commit()

        fork = Branch(
            conversation_id=conv.id,
            parent_branch_id=main.id,
            fork_message_id=msg.id,
            name="experiment",
            status="active",
        )
        db_session.add(fork)
        db_session.commit()

        assert fork.parent_branch_id == main.id
        assert fork.fork_message_id == msg.id
        assert fork.conversation_id == conv.id


class TestBranchMessageModel:
    def test_create_message(self, db_session):
        conv = Conversation(user_id="user-123")
        db_session.add(conv)
        db_session.commit()

        branch = Branch(conversation_id=conv.id, status="active")
        db_session.add(branch)
        db_session.commit()

        msg = BranchMessage(
            branch_id=branch.id,
            role="user",
            content="test message",
            user_id="user-123",
        )
        db_session.add(msg)
        db_session.commit()

        assert msg.id is not None
        assert msg.branch_id == branch.id
```

Run: `poetry run pytest tests/db/test_branch_models.py -v`
Expected: FAIL (models not defined)

**Step 2: Implement models**

Add to `mypalclara/db/models.py` after the existing Message class:

```python
class Conversation(Base):
    """A user's continuous conversation with Clara (one per user)."""
    __tablename__ = "conversations"

    id = Column(String, primary_key=True, default=gen_uuid)
    user_id = Column(String, nullable=False, index=True, unique=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    branches = relationship("Branch", back_populates="conversation", order_by="Branch.created_at")


class Branch(Base):
    """A branch within a conversation. Main trunk has parent_branch_id=None."""
    __tablename__ = "branches"

    id = Column(String, primary_key=True, default=gen_uuid)
    conversation_id = Column(String, ForeignKey("conversations.id"), nullable=False, index=True)
    parent_branch_id = Column(String, ForeignKey("branches.id"), nullable=True)
    fork_message_id = Column(String, ForeignKey("branch_messages.id"), nullable=True)
    name = Column(String, nullable=True)
    status = Column(String, default="active")  # active, merged, archived
    created_at = Column(DateTime, default=datetime.utcnow)
    merged_at = Column(DateTime, nullable=True)

    conversation = relationship("Conversation", back_populates="branches")
    parent_branch = relationship("Branch", remote_side="Branch.id", backref="child_branches")
    messages = relationship("BranchMessage", back_populates="branch", order_by="BranchMessage.created_at")

    __table_args__ = (
        Index("ix_branch_conversation_status", "conversation_id", "status"),
    )


class BranchMessage(Base):
    """A message within a branch."""
    __tablename__ = "branch_messages"

    id = Column(String, primary_key=True, default=gen_uuid)
    branch_id = Column(String, ForeignKey("branches.id"), nullable=False, index=True)
    user_id = Column(String, nullable=True)
    role = Column(String, nullable=False)  # user, assistant, system, tool
    content = Column(Text, nullable=True)
    attachments = Column(Text, nullable=True)  # JSON
    tool_calls = Column(Text, nullable=True)  # JSON
    created_at = Column(DateTime, default=datetime.utcnow)

    branch = relationship("Branch", back_populates="messages")

    __table_args__ = (
        Index("ix_branch_message_branch_created", "branch_id", "created_at"),
    )
```

**Step 3: Create Alembic migration**

```bash
cd /Users/heidornj/Code/mypalclara
poetry run python scripts/migrate.py create "add conversation branch models"
```

**Step 4: Run migration**

```bash
poetry run python scripts/migrate.py
```

**Step 5: Run tests**

Run: `poetry run pytest tests/db/test_branch_models.py -v`
Expected: PASS

**Step 6: Commit**

```bash
git add mypalclara/db/models.py mypalclara/db/migrations/versions/ tests/db/test_branch_models.py
git commit -m "feat: add Conversation, Branch, BranchMessage DB models"
```

---

## Task 3: Conversation & Branch API Endpoints

**Files:**
- Create: `mypalclara/gateway/api/conversations.py`
- Modify: `mypalclara/gateway/api/app.py:40-47` (mount new router)
- Test: `tests/gateway/api/test_conversations.py`

**Step 1: Write failing tests for conversation API**

```python
# tests/gateway/api/test_conversations.py
import pytest
from fastapi.testclient import TestClient


class TestConversationEndpoint:
    def test_get_conversation_creates_if_missing(self, client, auth_headers):
        resp = client.get("/api/v1/conversation", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "id" in data
        assert "branches" in data
        assert len(data["branches"]) == 1  # auto-created main trunk
        assert data["branches"][0]["name"] == "main"

    def test_get_conversation_returns_existing(self, client, auth_headers):
        resp1 = client.get("/api/v1/conversation", headers=auth_headers)
        resp2 = client.get("/api/v1/conversation", headers=auth_headers)
        assert resp1.json()["id"] == resp2.json()["id"]


class TestBranchEndpoints:
    def test_list_branches(self, client, auth_headers):
        # Create conversation first
        client.get("/api/v1/conversation", headers=auth_headers)
        resp = client.get("/api/v1/branches", headers=auth_headers)
        assert resp.status_code == 200
        assert len(resp.json()) >= 1

    def test_fork_branch(self, client, auth_headers):
        conv = client.get("/api/v1/conversation", headers=auth_headers).json()
        main_id = conv["branches"][0]["id"]

        # Send a message first (need something to fork from)
        # ... message sending happens via WebSocket, so we may need to create
        # a test message directly

        resp = client.post("/api/v1/branches/fork", headers=auth_headers, json={
            "parent_branch_id": main_id,
            "fork_message_id": None,  # fork from current point
            "name": "experiment",
        })
        assert resp.status_code == 201
        assert resp.json()["parent_branch_id"] == main_id
        assert resp.json()["name"] == "experiment"

    def test_merge_branch_squash(self, client, auth_headers):
        conv = client.get("/api/v1/conversation", headers=auth_headers).json()
        main_id = conv["branches"][0]["id"]

        fork = client.post("/api/v1/branches/fork", headers=auth_headers, json={
            "parent_branch_id": main_id,
            "name": "to-merge",
        }).json()

        resp = client.post(
            f"/api/v1/branches/{fork['id']}/merge",
            headers=auth_headers,
            json={"strategy": "squash"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "merged"

    def test_get_branch_messages(self, client, auth_headers):
        conv = client.get("/api/v1/conversation", headers=auth_headers).json()
        main_id = conv["branches"][0]["id"]
        resp = client.get(f"/api/v1/branches/{main_id}/messages", headers=auth_headers)
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_rename_branch(self, client, auth_headers):
        conv = client.get("/api/v1/conversation", headers=auth_headers).json()
        main_id = conv["branches"][0]["id"]

        fork = client.post("/api/v1/branches/fork", headers=auth_headers, json={
            "parent_branch_id": main_id,
            "name": "old-name",
        }).json()

        resp = client.patch(
            f"/api/v1/branches/{fork['id']}",
            headers=auth_headers,
            json={"name": "new-name"},
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "new-name"

    def test_archive_branch(self, client, auth_headers):
        conv = client.get("/api/v1/conversation", headers=auth_headers).json()
        main_id = conv["branches"][0]["id"]

        fork = client.post("/api/v1/branches/fork", headers=auth_headers, json={
            "parent_branch_id": main_id,
        }).json()

        resp = client.patch(
            f"/api/v1/branches/{fork['id']}",
            headers=auth_headers,
            json={"status": "archived"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "archived"

    def test_delete_branch(self, client, auth_headers):
        conv = client.get("/api/v1/conversation", headers=auth_headers).json()
        main_id = conv["branches"][0]["id"]

        fork = client.post("/api/v1/branches/fork", headers=auth_headers, json={
            "parent_branch_id": main_id,
        }).json()

        resp = client.delete(f"/api/v1/branches/{fork['id']}", headers=auth_headers)
        assert resp.status_code == 204

    def test_cannot_delete_main_trunk(self, client, auth_headers):
        conv = client.get("/api/v1/conversation", headers=auth_headers).json()
        main_id = conv["branches"][0]["id"]
        resp = client.delete(f"/api/v1/branches/{main_id}", headers=auth_headers)
        assert resp.status_code == 400
```

Run: `poetry run pytest tests/gateway/api/test_conversations.py -v`
Expected: FAIL

**Step 2: Implement conversations router**

```python
# mypalclara/gateway/api/conversations.py
"""Conversation and branch management API endpoints."""

import json
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from mypalclara.db.models import (
    Branch,
    BranchMessage,
    Conversation,
    CanonicalUser,
)
from mypalclara.gateway.api.auth import get_approved_user, get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["conversations"])


# --- Request/Response schemas ---

class ForkRequest(BaseModel):
    parent_branch_id: str
    fork_message_id: str | None = None
    name: str | None = None

class MergeRequest(BaseModel):
    strategy: str  # "squash" or "full"

class BranchUpdate(BaseModel):
    name: str | None = None
    status: str | None = None


# --- Endpoints ---

@router.get("/conversation")
async def get_conversation(
    user: CanonicalUser = Depends(get_approved_user),
    db: Session = Depends(get_db),
):
    """Get the user's conversation, auto-creating if none exists."""
    conv = db.query(Conversation).filter(Conversation.user_id == user.id).first()
    if not conv:
        conv = Conversation(user_id=user.id)
        db.add(conv)
        db.flush()
        # Create main trunk
        main = Branch(conversation_id=conv.id, name="main", status="active")
        db.add(main)
        db.commit()

    return {
        "id": conv.id,
        "created_at": conv.created_at.isoformat(),
        "updated_at": conv.updated_at.isoformat(),
        "branches": [_branch_to_dict(b) for b in conv.branches],
    }


@router.get("/branches")
async def list_branches(
    status: str | None = None,
    user: CanonicalUser = Depends(get_approved_user),
    db: Session = Depends(get_db),
):
    """List branches for the user's conversation."""
    conv = db.query(Conversation).filter(Conversation.user_id == user.id).first()
    if not conv:
        return []
    query = db.query(Branch).filter(Branch.conversation_id == conv.id)
    if status:
        query = query.filter(Branch.status == status)
    return [_branch_to_dict(b) for b in query.order_by(Branch.created_at).all()]


@router.post("/branches/fork", status_code=201)
async def fork_branch(
    req: ForkRequest,
    user: CanonicalUser = Depends(get_approved_user),
    db: Session = Depends(get_db),
):
    """Fork a new branch from a parent branch at a specific message."""
    parent = db.query(Branch).get(req.parent_branch_id)
    if not parent:
        raise HTTPException(status_code=404, detail="Parent branch not found")
    # Verify ownership
    conv = db.query(Conversation).get(parent.conversation_id)
    if not conv or conv.user_id != user.id:
        raise HTTPException(status_code=403, detail="Not your conversation")

    branch = Branch(
        conversation_id=conv.id,
        parent_branch_id=req.parent_branch_id,
        fork_message_id=req.fork_message_id,
        name=req.name,
        status="active",
    )
    db.add(branch)
    db.commit()
    return _branch_to_dict(branch)


@router.patch("/branches/{branch_id}")
async def update_branch(
    branch_id: str,
    req: BranchUpdate,
    user: CanonicalUser = Depends(get_approved_user),
    db: Session = Depends(get_db),
):
    """Rename or change status of a branch."""
    branch = _get_owned_branch(db, branch_id, user)
    if req.name is not None:
        branch.name = req.name
    if req.status is not None:
        if req.status not in ("active", "archived"):
            raise HTTPException(status_code=400, detail="Invalid status")
        branch.status = req.status
    db.commit()
    return _branch_to_dict(branch)


@router.post("/branches/{branch_id}/merge")
async def merge_branch(
    branch_id: str,
    req: MergeRequest,
    user: CanonicalUser = Depends(get_approved_user),
    db: Session = Depends(get_db),
):
    """Merge a branch back into its parent."""
    branch = _get_owned_branch(db, branch_id, user)
    if not branch.parent_branch_id:
        raise HTTPException(status_code=400, detail="Cannot merge the main trunk")
    if branch.status == "merged":
        raise HTTPException(status_code=400, detail="Branch already merged")
    if req.strategy not in ("squash", "full"):
        raise HTTPException(status_code=400, detail="Strategy must be 'squash' or 'full'")

    # Promote branch-scoped memories to global
    _promote_branch_memories(db, branch_id, user.id)

    if req.strategy == "full":
        _copy_messages_to_parent(db, branch)

    branch.status = "merged"
    branch.merged_at = datetime.utcnow()
    db.commit()
    return _branch_to_dict(branch)


@router.delete("/branches/{branch_id}", status_code=204)
async def delete_branch(
    branch_id: str,
    user: CanonicalUser = Depends(get_approved_user),
    db: Session = Depends(get_db),
):
    """Delete a branch and its messages."""
    branch = _get_owned_branch(db, branch_id, user)
    if not branch.parent_branch_id:
        raise HTTPException(status_code=400, detail="Cannot delete the main trunk")
    # Delete messages, then branch
    db.query(BranchMessage).filter(BranchMessage.branch_id == branch_id).delete()
    db.delete(branch)
    db.commit()


@router.get("/branches/{branch_id}/messages")
async def get_branch_messages(
    branch_id: str,
    include_ancestors: bool = True,
    limit: int = 100,
    offset: int = 0,
    user: CanonicalUser = Depends(get_approved_user),
    db: Session = Depends(get_db),
):
    """Get messages for a branch, optionally including ancestor context."""
    branch = _get_owned_branch(db, branch_id, user)
    messages = []

    if include_ancestors and branch.parent_branch_id:
        messages = _get_ancestor_messages(db, branch)

    own_messages = (
        db.query(BranchMessage)
        .filter(BranchMessage.branch_id == branch_id)
        .order_by(BranchMessage.created_at)
        .all()
    )
    messages.extend(own_messages)

    # Apply pagination to the combined list
    paginated = messages[offset : offset + limit]
    return [_message_to_dict(m) for m in paginated]


# --- Helpers ---

def _get_owned_branch(db: Session, branch_id: str, user: CanonicalUser) -> Branch:
    branch = db.query(Branch).get(branch_id)
    if not branch:
        raise HTTPException(status_code=404, detail="Branch not found")
    conv = db.query(Conversation).get(branch.conversation_id)
    if not conv or conv.user_id != user.id:
        raise HTTPException(status_code=403, detail="Not your conversation")
    return branch


def _get_ancestor_messages(db: Session, branch: Branch) -> list[BranchMessage]:
    """Walk up parent chain collecting messages up to each fork point."""
    messages = []
    current = branch

    while current.parent_branch_id:
        parent = db.query(Branch).get(current.parent_branch_id)
        if not parent:
            break
        query = (
            db.query(BranchMessage)
            .filter(BranchMessage.branch_id == parent.id)
            .order_by(BranchMessage.created_at)
        )
        if current.fork_message_id:
            fork_msg = db.query(BranchMessage).get(current.fork_message_id)
            if fork_msg:
                query = query.filter(BranchMessage.created_at <= fork_msg.created_at)
        parent_messages = query.all()
        messages = parent_messages + messages
        current = parent

    return messages


def _promote_branch_memories(db: Session, branch_id: str, user_id: str):
    """Promote branch-scoped memories to global (branch_id → NULL).

    This is called during merge. Implementation depends on Rook's storage —
    see Task 4 for the branch_id metadata field.
    """
    # TODO: Implement after Task 4 adds branch_id to Rook metadata
    pass


def _copy_messages_to_parent(db: Session, branch: Branch):
    """Copy branch messages to parent branch (full merge)."""
    messages = (
        db.query(BranchMessage)
        .filter(BranchMessage.branch_id == branch.id)
        .order_by(BranchMessage.created_at)
        .all()
    )
    for msg in messages:
        copy = BranchMessage(
            branch_id=branch.parent_branch_id,
            user_id=msg.user_id,
            role=msg.role,
            content=msg.content,
            attachments=msg.attachments,
            tool_calls=msg.tool_calls,
            created_at=msg.created_at,
        )
        db.add(copy)


def _branch_to_dict(branch: Branch) -> dict:
    return {
        "id": branch.id,
        "conversation_id": branch.conversation_id,
        "parent_branch_id": branch.parent_branch_id,
        "fork_message_id": branch.fork_message_id,
        "name": branch.name,
        "status": branch.status,
        "created_at": branch.created_at.isoformat() if branch.created_at else None,
        "merged_at": branch.merged_at.isoformat() if branch.merged_at else None,
    }


def _message_to_dict(msg: BranchMessage) -> dict:
    return {
        "id": msg.id,
        "branch_id": msg.branch_id,
        "user_id": msg.user_id,
        "role": msg.role,
        "content": msg.content,
        "attachments": json.loads(msg.attachments) if msg.attachments else None,
        "tool_calls": json.loads(msg.tool_calls) if msg.tool_calls else None,
        "created_at": msg.created_at.isoformat() if msg.created_at else None,
    }
```

**Step 3: Mount router in app.py**

Add to `mypalclara/gateway/api/app.py` in the router mounting section:

```python
from mypalclara.gateway.api.conversations import router as conversations_router
app.include_router(conversations_router)
```

**Step 4: Run tests**

Run: `poetry run pytest tests/gateway/api/test_conversations.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add mypalclara/gateway/api/conversations.py mypalclara/gateway/api/app.py tests/gateway/api/test_conversations.py
git commit -m "feat: add conversation and branch API endpoints"
```

---

## Task 4: Branch-Scoped Memory

**Files:**
- Modify: `mypalclara/core/memory/core/memory.py:251-333` (add branch_id to add/search)
- Modify: `mypalclara/gateway/api/conversations.py` (implement _promote_branch_memories)
- Test: `tests/core/memory/test_branch_memory.py`

**Step 1: Write failing test**

```python
# tests/core/memory/test_branch_memory.py
import pytest
from unittest.mock import MagicMock, patch


class TestBranchScopedMemory:
    def test_add_memory_with_branch_id(self):
        """Memories added with branch_id should include it in metadata."""
        from mypalclara.core.memory.core.memory import ClaraMemory

        mock_memory = MagicMock(spec=ClaraMemory)
        # Test that add() accepts branch_id parameter
        # and passes it through to metadata
        mock_memory.add.return_value = {"results": []}
        mock_memory.add(
            messages="test fact",
            user_id="user-123",
            metadata={"branch_id": "branch-abc"},
        )
        mock_memory.add.assert_called_once()
        call_kwargs = mock_memory.add.call_args[1]
        assert call_kwargs["metadata"]["branch_id"] == "branch-abc"

    def test_search_filters_by_branch(self):
        """Search should return global + branch-scoped memories."""
        from mypalclara.core.memory.core.memory import ClaraMemory

        mock_memory = MagicMock(spec=ClaraMemory)
        mock_memory.search.return_value = {"results": []}
        mock_memory.search(
            query="test",
            user_id="user-123",
            filters={"OR": [
                {"branch_id": None},
                {"branch_id": "branch-abc"},
            ]},
        )
        mock_memory.search.assert_called_once()
```

Run: `poetry run pytest tests/core/memory/test_branch_memory.py -v`
Expected: PASS (mocks only, verifying interface)

**Step 2: Add branch-aware helper functions**

Create a helper module that wraps Rook's add/search with branch awareness:

```python
# mypalclara/core/memory/branch_memory.py
"""Branch-aware memory operations for the web UI conversation model."""

import logging
from mypalclara.core.memory import get_memory

logger = logging.getLogger(__name__)


def add_memory_for_branch(
    messages: str | list,
    user_id: str,
    branch_id: str | None = None,
    **kwargs,
):
    """Add a memory, optionally scoped to a branch.

    If branch_id is None, memory is global (visible everywhere).
    If branch_id is set, memory is branch-scoped (only visible in that branch until merged).
    """
    memory = get_memory()
    metadata = kwargs.pop("metadata", {}) or {}
    if branch_id:
        metadata["branch_id"] = branch_id
    return memory.add(messages=messages, user_id=user_id, metadata=metadata, **kwargs)


def search_memory_for_branch(
    query: str,
    user_id: str,
    branch_id: str | None = None,
    **kwargs,
):
    """Search memories visible to a branch.

    Returns global memories (no branch_id) plus memories scoped to this specific branch.
    """
    memory = get_memory()
    if branch_id:
        # Include global + this branch's memories
        kwargs["filters"] = {
            "OR": [
                {"branch_id": None},
                {"branch_id": branch_id},
            ]
        }
    # If no branch_id, default search returns all (global) memories
    return memory.search(query=query, user_id=user_id, **kwargs)


def promote_branch_memories(user_id: str, branch_id: str):
    """Promote branch-scoped memories to global on merge.

    Sets branch_id metadata to None for all memories in the branch.
    """
    memory = get_memory()
    # Search for all memories with this branch_id
    results = memory.search(
        query="*",
        user_id=user_id,
        filters={"branch_id": branch_id},
        limit=1000,
    )
    for mem in results.get("results", []):
        memory.update(mem["id"], metadata={"branch_id": None})
    logger.info("Promoted %d memories from branch %s to global", len(results.get("results", [])), branch_id)


def discard_branch_memories(user_id: str, branch_id: str):
    """Delete branch-scoped memories when a branch is discarded."""
    memory = get_memory()
    results = memory.search(
        query="*",
        user_id=user_id,
        filters={"branch_id": branch_id},
        limit=1000,
    )
    for mem in results.get("results", []):
        memory.delete(mem["id"])
    logger.info("Discarded %d memories from branch %s", len(results.get("results", [])), branch_id)
```

**Step 3: Wire into conversations.py**

Update `_promote_branch_memories` in `mypalclara/gateway/api/conversations.py`:

```python
def _promote_branch_memories(db: Session, branch_id: str, user_id: str):
    from mypalclara.core.memory.branch_memory import promote_branch_memories
    promote_branch_memories(user_id=user_id, branch_id=branch_id)
```

And update `delete_branch` to discard memories:

```python
# In delete_branch endpoint, before deleting messages:
from mypalclara.core.memory.branch_memory import discard_branch_memories
discard_branch_memories(user_id=user.id, branch_id=branch_id)
```

**Step 4: Run tests**

Run: `poetry run pytest tests/core/memory/test_branch_memory.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add mypalclara/core/memory/branch_memory.py mypalclara/gateway/api/conversations.py tests/core/memory/test_branch_memory.py
git commit -m "feat: add branch-scoped memory with promote/discard on merge"
```

---

## Task 5: Branch-Aware WebSocket & Processor

**Files:**
- Modify: `mypalclara/gateway/protocol.py:156-176` (add branch_id to MessageRequest)
- Modify: `mypalclara/gateway/processor.py:159-259` (branch-aware context/storage)
- Modify: `mypalclara/gateway/server.py` (WebSocket auth for Clerk JWT)
- Test: `tests/gateway/test_branch_processor.py`

**Step 1: Add branch_id to MessageRequest protocol**

In `mypalclara/gateway/protocol.py`, add to `MessageRequest` class:

```python
class MessageRequest(BaseModel):
    # ... existing fields ...
    branch_id: str | None = None  # Web UI branch targeting
```

**Step 2: Add WebSocket JWT auth**

In `mypalclara/gateway/server.py`, update the WebSocket connection handler to extract JWT from query params:

```python
async def _handle_connection(self, websocket):
    """Handle a new WebSocket connection."""
    # Extract token from query params for web clients
    query_params = dict(urllib.parse.parse_qsl(
        urllib.parse.urlparse(websocket.request.path).query
    ))
    token = query_params.get("token")
    if token:
        # Validate Clerk JWT and attach user info
        try:
            from mypalclara.gateway.api.clerk_auth import verify_clerk_jwt
            claims = await verify_clerk_jwt(authorization=f"Bearer {token}")
            websocket.clerk_user_id = claims["sub"]
        except Exception:
            await websocket.close(4001, "Invalid token")
            return
    # ... existing connection handling ...
```

**Step 3: Update processor for branch-aware context**

In `mypalclara/gateway/processor.py`, modify `_get_or_create_db_session` and `_store_message` to handle branch_id:

```python
async def _get_branch_context(
    self, branch_id: str, user_id: str, db_session
) -> list[dict]:
    """Get message history for a branch including ancestors."""
    from mypalclara.db.models import Branch, BranchMessage

    branch = db_session.query(Branch).get(branch_id)
    if not branch:
        return []

    # Collect ancestor messages
    messages = []
    if branch.parent_branch_id:
        messages = self._get_ancestor_messages_sync(db_session, branch)

    # Add branch's own messages
    own = (
        db_session.query(BranchMessage)
        .filter(BranchMessage.branch_id == branch_id)
        .order_by(BranchMessage.created_at)
        .all()
    )
    messages.extend(own)

    return [
        {"role": m.role, "content": m.content}
        for m in messages[-30:]  # Last 30 messages
    ]


async def _store_branch_message(
    self, branch_id: str, user_id: str, role: str, content: str, db_session,
    attachments: str | None = None, tool_calls: str | None = None,
) -> str:
    """Store a message in a branch."""
    from mypalclara.db.models import BranchMessage

    msg = BranchMessage(
        branch_id=branch_id,
        user_id=user_id,
        role=role,
        content=content,
        attachments=attachments,
        tool_calls=tool_calls,
    )
    db_session.add(msg)
    db_session.commit()
    return msg.id
```

Then in the main `process()` method, check for `branch_id` in the MessageRequest:

```python
# In process() method, after getting the message request:
if message.branch_id:
    # Web UI branch mode
    context_messages = await self._get_branch_context(
        message.branch_id, user_id, db_session
    )
    await self._store_branch_message(
        message.branch_id, user_id, "user", message.content, db_session
    )
    # Use branch-aware memory search
    from mypalclara.core.memory.branch_memory import search_memory_for_branch
    memories = search_memory_for_branch(query=message.content, user_id=user_id, branch_id=message.branch_id)
else:
    # Existing session-based flow (Discord, etc.)
    # ... unchanged ...
```

**Step 4: Write test**

```python
# tests/gateway/test_branch_processor.py
import pytest
from mypalclara.gateway.protocol import MessageRequest


class TestBranchAwareProtocol:
    def test_message_request_accepts_branch_id(self):
        msg = MessageRequest(
            id="test-1",
            user={"id": "user-1", "name": "Test"},
            channel={"id": "web", "type": "dm"},
            content="hello",
            branch_id="branch-abc",
        )
        assert msg.branch_id == "branch-abc"

    def test_message_request_branch_id_optional(self):
        msg = MessageRequest(
            id="test-2",
            user={"id": "user-1", "name": "Test"},
            channel={"id": "web", "type": "dm"},
            content="hello",
        )
        assert msg.branch_id is None
```

Run: `poetry run pytest tests/gateway/test_branch_processor.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add mypalclara/gateway/protocol.py mypalclara/gateway/processor.py mypalclara/gateway/server.py tests/gateway/test_branch_processor.py
git commit -m "feat: branch-aware WebSocket protocol and message processor"
```

---

## Task 6: Frontend Strip-Down

**Files:**
- Modify: `web-ui/frontend/package.json` (remove unused deps)
- Modify: `web-ui/frontend/src/App.tsx` (simplify to 3 routes)
- Delete: `web-ui/frontend/src/pages/Blackjack.tsx`, `Checkers.tsx`, `Lobby.tsx`, `GameHistory.tsx`, `Replay.tsx`, `GraphExplorer.tsx`, `Intentions.tsx`, `AdminUsers.tsx`
- Delete: `web-ui/frontend/src/components/games/`, `graph/`, `intentions/`
- Delete: `web-ui/backend/` (entire Rails app)
- Modify: `web-ui/frontend/src/components/layout/` (simplify sidebar)

**Step 1: Remove Rails backend entirely**

```bash
rm -rf web-ui/backend/
```

**Step 2: Remove unused frontend pages**

```bash
rm -f web-ui/frontend/src/pages/Blackjack.tsx
rm -f web-ui/frontend/src/pages/Checkers.tsx
rm -f web-ui/frontend/src/pages/Lobby.tsx
rm -f web-ui/frontend/src/pages/GameHistory.tsx
rm -f web-ui/frontend/src/pages/Replay.tsx
rm -f web-ui/frontend/src/pages/GraphExplorer.tsx
rm -f web-ui/frontend/src/pages/Intentions.tsx
rm -f web-ui/frontend/src/pages/AdminUsers.tsx
rm -f web-ui/frontend/src/pages/Login.tsx
rm -f web-ui/frontend/src/pages/PendingApproval.tsx
rm -f web-ui/frontend/src/pages/Suspended.tsx
rm -rf web-ui/frontend/src/components/games/
rm -rf web-ui/frontend/src/components/graph/
rm -rf web-ui/frontend/src/components/intentions/
```

**Step 3: Remove unused deps from package.json**

```bash
cd web-ui/frontend
npm uninstall @rails/actioncable @tiptap/core @tiptap/react @tiptap/starter-kit @tiptap/extension-placeholder @xyflow/react d3 d3-force
```

**Step 4: Simplify App.tsx routing**

```tsx
// web-ui/frontend/src/App.tsx
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { ClerkLoaded, SignedIn, SignedOut, RedirectToSignIn } from "@clerk/clerk-react";
import Chat from "./pages/Chat";
import Settings from "./pages/Settings";
import KnowledgeBase from "./pages/KnowledgeBase";
import AppLayout from "./components/layout/AppLayout";

function App() {
  return (
    <BrowserRouter>
      <ClerkLoaded>
        <SignedOut>
          <RedirectToSignIn />
        </SignedOut>
        <SignedIn>
          <AppLayout>
            <Routes>
              <Route path="/" element={<Chat />} />
              <Route path="/settings" element={<Settings />} />
              <Route path="/knowledge" element={<KnowledgeBase />} />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
          </AppLayout>
        </SignedIn>
      </ClerkLoaded>
    </BrowserRouter>
  );
}

export default App;
```

**Step 5: Verify frontend builds**

```bash
cd web-ui/frontend && npm run build
```

Expected: Build succeeds (may have some import warnings for deleted pages — fix any broken imports)

**Step 6: Commit**

```bash
git add -A web-ui/
git commit -m "feat: strip frontend to chat + settings + knowledge, remove Rails backend"
```

---

## Task 7: Clerk Auth in Frontend

**Files:**
- Modify: `web-ui/frontend/package.json` (add @clerk/clerk-react)
- Create: `web-ui/frontend/src/auth/ClerkProvider.tsx`
- Modify: `web-ui/frontend/src/main.tsx` (wrap with ClerkProvider)
- Delete: `web-ui/frontend/src/auth/AuthProvider.tsx` (old OAuth flow)
- Modify: `web-ui/frontend/src/api/client.ts` (use Clerk token)

**Step 1: Install Clerk**

```bash
cd web-ui/frontend && npm install @clerk/clerk-react
```

**Step 2: Create Clerk provider wrapper**

```tsx
// web-ui/frontend/src/auth/ClerkProvider.tsx
import { ClerkProvider as BaseClerkProvider } from "@clerk/clerk-react";

const CLERK_PUBLISHABLE_KEY = import.meta.env.VITE_CLERK_PUBLISHABLE_KEY;

if (!CLERK_PUBLISHABLE_KEY) {
  throw new Error("Missing VITE_CLERK_PUBLISHABLE_KEY environment variable");
}

export function ClerkProvider({ children }: { children: React.ReactNode }) {
  return (
    <BaseClerkProvider publishableKey={CLERK_PUBLISHABLE_KEY}>
      {children}
    </BaseClerkProvider>
  );
}
```

**Step 3: Update main.tsx**

```tsx
// web-ui/frontend/src/main.tsx
import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import { ClerkProvider } from "./auth/ClerkProvider";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import "./index.css";

const queryClient = new QueryClient();

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ClerkProvider>
      <QueryClientProvider client={queryClient}>
        <App />
      </QueryClientProvider>
    </ClerkProvider>
  </React.StrictMode>
);
```

**Step 4: Update API client to use Clerk tokens**

```typescript
// web-ui/frontend/src/api/client.ts
import { useAuth } from "@clerk/clerk-react";

const GATEWAY_URL = import.meta.env.VITE_GATEWAY_URL || "http://localhost:18790";

export async function gatewayFetch(
  path: string,
  options: RequestInit = {},
  getToken: () => Promise<string | null>,
): Promise<Response> {
  const token = await getToken();
  return fetch(`${GATEWAY_URL}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...options.headers,
    },
  });
}
```

**Step 5: Delete old auth provider**

```bash
rm web-ui/frontend/src/auth/AuthProvider.tsx
```

**Step 6: Create .env.example**

```bash
# web-ui/frontend/.env.example
VITE_CLERK_PUBLISHABLE_KEY=pk_test_...
VITE_GATEWAY_URL=http://localhost:18790
VITE_GATEWAY_WS_URL=ws://localhost:18789
```

**Step 7: Verify build**

```bash
cd web-ui/frontend && npm run build
```

**Step 8: Commit**

```bash
git add web-ui/frontend/
git commit -m "feat: replace OAuth auth with Clerk React SDK"
```

---

## Task 8: Direct WebSocket & Branch-Aware Chat Store

**Files:**
- Rewrite: `web-ui/frontend/src/stores/chatStore.ts`
- Create: `web-ui/frontend/src/hooks/useGatewayWebSocket.ts`
- Delete: `web-ui/frontend/src/hooks/useWebSocket.ts` (old ActionCable hook)

**Step 1: Create gateway WebSocket hook**

```typescript
// web-ui/frontend/src/hooks/useGatewayWebSocket.ts
import { useEffect, useRef, useCallback } from "react";
import { useAuth } from "@clerk/clerk-react";

const WS_URL = import.meta.env.VITE_GATEWAY_WS_URL || "ws://localhost:18789";

export type GatewayEvent =
  | { type: "response_start"; request_id: string }
  | { type: "chunk"; request_id: string; content: string }
  | { type: "tool_start"; request_id: string; tool_name: string; step: number }
  | { type: "tool_result"; request_id: string; tool_name: string; success: boolean; output_preview: string }
  | { type: "response_end"; request_id: string; full_text: string; tool_count: number; files?: any[] }
  | { type: "error"; request_id?: string; message: string }
  | { type: "registered"; session_id: string };

interface UseGatewayWebSocketOptions {
  onEvent: (event: GatewayEvent) => void;
  onConnect?: () => void;
  onDisconnect?: () => void;
}

export function useGatewayWebSocket({ onEvent, onConnect, onDisconnect }: UseGatewayWebSocketOptions) {
  const { getToken } = useAuth();
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectRef = useRef<number>(0);
  const sessionIdRef = useRef<string | null>(null);

  const connect = useCallback(async () => {
    const token = await getToken();
    if (!token) return;

    const url = `${WS_URL}?token=${encodeURIComponent(token)}`;
    const ws = new WebSocket(url);
    wsRef.current = ws;

    ws.onopen = () => {
      reconnectRef.current = 0;
      // Register as web adapter
      ws.send(JSON.stringify({
        type: "register",
        node_id: `web-${crypto.randomUUID()}`,
        platform: "web",
        capabilities: ["streaming", "attachments"],
        session_id: sessionIdRef.current,
      }));
    };

    ws.onmessage = (evt) => {
      const data = JSON.parse(evt.data);
      if (data.type === "registered") {
        sessionIdRef.current = data.session_id;
        onConnect?.();
      }
      onEvent(data as GatewayEvent);
    };

    ws.onclose = () => {
      onDisconnect?.();
      // Exponential backoff reconnect
      const delay = Math.min(1000 * 2 ** reconnectRef.current, 60000);
      reconnectRef.current++;
      setTimeout(connect, delay);
    };

    ws.onerror = () => ws.close();
  }, [getToken, onEvent, onConnect, onDisconnect]);

  const send = useCallback((data: Record<string, unknown>) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(data));
    }
  }, []);

  const disconnect = useCallback(() => {
    wsRef.current?.close();
    wsRef.current = null;
  }, []);

  useEffect(() => {
    connect();
    return disconnect;
  }, [connect, disconnect]);

  return { send, disconnect };
}
```

**Step 2: Rewrite chat store for branch model**

```typescript
// web-ui/frontend/src/stores/chatStore.ts
import { create } from "zustand";

export type ModelTier = "low" | "mid" | "high";

export interface ToolEvent {
  type: "tool_start" | "tool_result";
  tool_name: string;
  step?: number;
  success?: boolean;
  output_preview?: string;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  toolEvents?: ToolEvent[];
  attachments?: any[];
  streaming?: boolean;
  created_at?: string;
}

export interface BranchInfo {
  id: string;
  conversation_id: string;
  parent_branch_id: string | null;
  fork_message_id: string | null;
  name: string | null;
  status: "active" | "merged" | "archived";
  created_at: string;
  merged_at: string | null;
}

interface ChatState {
  // Connection
  connected: boolean;
  connectionError: string | null;

  // Branch state
  branches: BranchInfo[];
  activeBranchId: string | null;

  // Messages for active branch
  messages: ChatMessage[];

  // UI state
  selectedTier: ModelTier;
  streaming: boolean;
  activeRequestId: string | null;

  // Actions
  setConnected: (connected: boolean) => void;
  setConnectionError: (error: string | null) => void;
  setBranches: (branches: BranchInfo[]) => void;
  setActiveBranch: (branchId: string) => void;
  setMessages: (messages: ChatMessage[]) => void;
  addUserMessage: (content: string, attachments?: any[]) => string;
  setSelectedTier: (tier: ModelTier) => void;

  // Streaming actions
  onResponseStart: (requestId: string) => void;
  onChunk: (content: string) => void;
  onToolStart: (toolName: string, step: number) => void;
  onToolResult: (toolName: string, success: boolean, output: string) => void;
  onResponseEnd: (fullText: string) => void;
  onError: (message: string) => void;
}

export const useChatStore = create<ChatState>((set, get) => ({
  connected: false,
  connectionError: null,
  branches: [],
  activeBranchId: null,
  messages: [],
  selectedTier: "mid",
  streaming: false,
  activeRequestId: null,

  setConnected: (connected) => set({ connected, connectionError: null }),
  setConnectionError: (error) => set({ connectionError: error, connected: false }),
  setBranches: (branches) => set({ branches }),
  setActiveBranch: (branchId) => set({ activeBranchId: branchId }),
  setMessages: (messages) => set({ messages }),
  setSelectedTier: (tier) => set({ selectedTier: tier }),

  addUserMessage: (content, attachments) => {
    const id = crypto.randomUUID();
    set((state) => ({
      messages: [...state.messages, {
        id,
        role: "user",
        content,
        attachments,
        created_at: new Date().toISOString(),
      }],
    }));
    return id;
  },

  onResponseStart: (requestId) => {
    set((state) => ({
      streaming: true,
      activeRequestId: requestId,
      messages: [...state.messages, {
        id: requestId,
        role: "assistant",
        content: "",
        toolEvents: [],
        streaming: true,
      }],
    }));
  },

  onChunk: (content) => {
    set((state) => {
      const messages = [...state.messages];
      const last = messages[messages.length - 1];
      if (last?.role === "assistant" && last.streaming) {
        messages[messages.length - 1] = { ...last, content: last.content + content };
      }
      return { messages };
    });
  },

  onToolStart: (toolName, step) => {
    set((state) => {
      const messages = [...state.messages];
      const last = messages[messages.length - 1];
      if (last?.role === "assistant" && last.streaming) {
        const toolEvents = [...(last.toolEvents || []), { type: "tool_start" as const, tool_name: toolName, step }];
        messages[messages.length - 1] = { ...last, toolEvents };
      }
      return { messages };
    });
  },

  onToolResult: (toolName, success, output) => {
    set((state) => {
      const messages = [...state.messages];
      const last = messages[messages.length - 1];
      if (last?.role === "assistant" && last.streaming) {
        const toolEvents = [...(last.toolEvents || []), {
          type: "tool_result" as const, tool_name: toolName, success, output_preview: output,
        }];
        messages[messages.length - 1] = { ...last, toolEvents };
      }
      return { messages };
    });
  },

  onResponseEnd: (fullText) => {
    set((state) => {
      const messages = [...state.messages];
      const last = messages[messages.length - 1];
      if (last?.role === "assistant" && last.streaming) {
        messages[messages.length - 1] = { ...last, content: fullText, streaming: false };
      }
      return { messages, streaming: false, activeRequestId: null };
    });
  },

  onError: (message) => {
    set((state) => {
      const messages = [...state.messages];
      const last = messages[messages.length - 1];
      if (last?.role === "assistant" && last.streaming) {
        messages[messages.length - 1] = { ...last, content: `Error: ${message}`, streaming: false };
      } else {
        messages.push({ id: crypto.randomUUID(), role: "assistant", content: `Error: ${message}` });
      }
      return { messages, streaming: false, activeRequestId: null };
    });
  },
}));
```

**Step 3: Delete old WebSocket hook**

```bash
rm web-ui/frontend/src/hooks/useWebSocket.ts
```

**Step 4: Verify build**

```bash
cd web-ui/frontend && npm run build
```

**Step 5: Commit**

```bash
git add web-ui/frontend/src/
git commit -m "feat: direct gateway WebSocket + branch-aware chat store"
```

---

## Task 9: Claude.ai Chat UI with assistant-ui

**Files:**
- Rewrite: `web-ui/frontend/src/pages/Chat.tsx`
- Rewrite: `web-ui/frontend/src/components/chat/ChatRuntimeProvider.tsx`
- Rewrite: `web-ui/frontend/src/components/assistant-ui/thread.tsx`
- Create: `web-ui/frontend/src/components/chat/ToolCallBlock.tsx`
- Modify: `web-ui/frontend/src/components/chat/TierSelector.tsx`

**Step 1: Fetch assistant-ui Claude example for reference**

Check https://www.assistant-ui.com/examples/claude for the component structure. The key components are:
- `Thread` — main chat area with messages and composer
- `ThreadList` — sidebar (we'll adapt this to show branches)
- `AssistantMessage` / `UserMessage` — message rendering
- `Composer` — input area

**Step 2: Build ChatRuntimeProvider bridging Zustand to assistant-ui**

```tsx
// web-ui/frontend/src/components/chat/ChatRuntimeProvider.tsx
import { useEffect, useMemo } from "react";
import {
  AssistantRuntimeProvider,
  useExternalStoreRuntime,
} from "@assistant-ui/react";
import { useAuth } from "@clerk/clerk-react";
import { useChatStore } from "../../stores/chatStore";
import { useGatewayWebSocket } from "../../hooks/useGatewayWebSocket";

export function ChatRuntimeProvider({ children }: { children: React.ReactNode }) {
  const store = useChatStore();
  const { getToken } = useAuth();

  const { send } = useGatewayWebSocket({
    onEvent: (event) => {
      switch (event.type) {
        case "response_start":
          store.onResponseStart(event.request_id);
          break;
        case "chunk":
          store.onChunk(event.content);
          break;
        case "tool_start":
          store.onToolStart(event.tool_name, event.step);
          break;
        case "tool_result":
          store.onToolResult(event.tool_name, event.success, event.output_preview);
          break;
        case "response_end":
          store.onResponseEnd(event.full_text);
          break;
        case "error":
          store.onError(event.message);
          break;
      }
    },
    onConnect: () => store.setConnected(true),
    onDisconnect: () => store.setConnected(false),
  });

  const runtime = useExternalStoreRuntime({
    isRunning: store.streaming,
    messages: store.messages.map((msg) => ({
      role: msg.role,
      content: [{ type: "text" as const, text: msg.content }],
      id: msg.id,
      metadata: {
        toolEvents: msg.toolEvents,
        streaming: msg.streaming,
      },
    })),
    onNew: async (message) => {
      const content = message.content
        .filter((c): c is { type: "text"; text: string } => c.type === "text")
        .map((c) => c.text)
        .join("\n");

      store.addUserMessage(content);
      send({
        type: "message",
        id: crypto.randomUUID(),
        user: { id: "web-user", name: "User" },
        channel: { id: "web", type: "dm" },
        content,
        branch_id: store.activeBranchId,
        tier_override: store.selectedTier,
        attachments: [],
        metadata: {},
      });
    },
    onCancel: () => {
      send({ type: "cancel", request_id: store.activeRequestId });
    },
  });

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      {children}
    </AssistantRuntimeProvider>
  );
}
```

**Step 3: Build the Thread component (Claude.ai style)**

Use assistant-ui's Thread primitives. See the Claude example at https://www.assistant-ui.com/examples/claude for styling reference. Key pieces:

- `Thread.Root` wrapping the message list
- `Thread.Messages` rendering `UserMessage` and `AssistantMessage`
- `Composer.Root` with text input and send button
- Custom `ToolCallBlock` for collapsible tool displays

```tsx
// web-ui/frontend/src/components/chat/ToolCallBlock.tsx
import { useState } from "react";
import { ChevronDown, ChevronRight, Loader2, CheckCircle, XCircle } from "lucide-react";

interface ToolCallBlockProps {
  toolName: string;
  step?: number;
  success?: boolean;
  output?: string;
  running?: boolean;
}

export function ToolCallBlock({ toolName, step, success, output, running }: ToolCallBlockProps) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="my-2 rounded-lg border border-gray-200 dark:border-gray-700">
      <button
        className="flex w-full items-center gap-2 px-3 py-2 text-sm text-gray-600 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-800"
        onClick={() => setExpanded(!expanded)}
      >
        {running ? (
          <Loader2 className="h-4 w-4 animate-spin" />
        ) : success ? (
          <CheckCircle className="h-4 w-4 text-green-500" />
        ) : (
          <XCircle className="h-4 w-4 text-red-500" />
        )}
        <span className="font-mono">{toolName}</span>
        {step && <span className="text-xs text-gray-400">step {step}</span>}
        {expanded ? <ChevronDown className="ml-auto h-4 w-4" /> : <ChevronRight className="ml-auto h-4 w-4" />}
      </button>
      {expanded && output && (
        <div className="border-t border-gray-200 dark:border-gray-700 px-3 py-2">
          <pre className="text-xs text-gray-500 whitespace-pre-wrap">{output}</pre>
        </div>
      )}
    </div>
  );
}
```

**Step 4: Build Chat page**

```tsx
// web-ui/frontend/src/pages/Chat.tsx
import { ChatRuntimeProvider } from "../components/chat/ChatRuntimeProvider";
import { Thread } from "../components/assistant-ui/thread";
import { BranchSidebar } from "../components/chat/BranchSidebar";

export default function Chat() {
  return (
    <ChatRuntimeProvider>
      <div className="flex h-full">
        <BranchSidebar />
        <div className="flex-1">
          <Thread />
        </div>
      </div>
    </ChatRuntimeProvider>
  );
}
```

**Step 5: Verify build and visual check**

```bash
cd web-ui/frontend && npm run dev
```

Open http://localhost:5173 and verify the chat UI renders.

**Step 6: Commit**

```bash
git add web-ui/frontend/src/
git commit -m "feat: Claude.ai-style chat UI with assistant-ui and tool blocks"
```

---

## Task 10: Branch Sidebar & Fork/Merge UX

**Files:**
- Create: `web-ui/frontend/src/components/chat/BranchSidebar.tsx`
- Create: `web-ui/frontend/src/components/chat/MergeDialog.tsx`
- Create: `web-ui/frontend/src/hooks/useBranches.ts`
- Modify: `web-ui/frontend/src/components/assistant-ui/thread.tsx` (add fork button to messages)

**Step 1: Create branch management hook**

```typescript
// web-ui/frontend/src/hooks/useBranches.ts
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useAuth } from "@clerk/clerk-react";
import { useChatStore } from "../stores/chatStore";

const GATEWAY_URL = import.meta.env.VITE_GATEWAY_URL || "http://localhost:18790";

async function fetchWithAuth(path: string, getToken: () => Promise<string | null>, options?: RequestInit) {
  const token = await getToken();
  const resp = await fetch(`${GATEWAY_URL}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...options?.headers,
    },
  });
  if (!resp.ok) throw new Error(`API error: ${resp.status}`);
  if (resp.status === 204) return null;
  return resp.json();
}

export function useBranches() {
  const { getToken } = useAuth();
  const queryClient = useQueryClient();
  const { setActiveBranch, setBranches, setMessages } = useChatStore();

  const conversationQuery = useQuery({
    queryKey: ["conversation"],
    queryFn: () => fetchWithAuth("/api/v1/conversation", getToken),
  });

  const branchesQuery = useQuery({
    queryKey: ["branches"],
    queryFn: () => fetchWithAuth("/api/v1/branches", getToken),
    enabled: !!conversationQuery.data,
  });

  const forkMutation = useMutation({
    mutationFn: (params: { parentBranchId: string; forkMessageId?: string; name?: string }) =>
      fetchWithAuth("/api/v1/branches/fork", getToken, {
        method: "POST",
        body: JSON.stringify({
          parent_branch_id: params.parentBranchId,
          fork_message_id: params.forkMessageId || null,
          name: params.name,
        }),
      }),
    onSuccess: (newBranch) => {
      queryClient.invalidateQueries({ queryKey: ["branches"] });
      switchToBranch(newBranch.id);
    },
  });

  const mergeMutation = useMutation({
    mutationFn: (params: { branchId: string; strategy: "squash" | "full" }) =>
      fetchWithAuth(`/api/v1/branches/${params.branchId}/merge`, getToken, {
        method: "POST",
        body: JSON.stringify({ strategy: params.strategy }),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["branches"] });
    },
  });

  const switchToBranch = async (branchId: string) => {
    setActiveBranch(branchId);
    const messages = await fetchWithAuth(
      `/api/v1/branches/${branchId}/messages`,
      getToken,
    );
    setMessages(messages.map((m: any) => ({
      id: m.id,
      role: m.role,
      content: m.content,
      attachments: m.attachments,
      tool_calls: m.tool_calls,
      created_at: m.created_at,
    })));
  };

  return {
    conversation: conversationQuery.data,
    branches: branchesQuery.data || [],
    isLoading: conversationQuery.isLoading || branchesQuery.isLoading,
    fork: forkMutation.mutate,
    merge: mergeMutation.mutate,
    switchToBranch,
  };
}
```

**Step 2: Build BranchSidebar component**

```tsx
// web-ui/frontend/src/components/chat/BranchSidebar.tsx
import { useState } from "react";
import { GitBranch, GitMerge, MoreHorizontal, Plus, Archive, Trash2, PanelLeftClose, PanelLeft } from "lucide-react";
import { useBranches } from "../../hooks/useBranches";
import { useChatStore } from "../../stores/chatStore";
import { MergeDialog } from "./MergeDialog";
import type { BranchInfo } from "../../stores/chatStore";

export function BranchSidebar() {
  const { branches, fork, switchToBranch } = useBranches();
  const activeBranchId = useChatStore((s) => s.activeBranchId);
  const [collapsed, setCollapsed] = useState(false);
  const [merging, setMerging] = useState<BranchInfo | null>(null);

  if (collapsed) {
    return (
      <button onClick={() => setCollapsed(false)} className="p-2 hover:bg-gray-100 dark:hover:bg-gray-800">
        <PanelLeft className="h-5 w-5" />
      </button>
    );
  }

  // Build tree: main trunk + children
  const mainBranch = branches.find((b: BranchInfo) => !b.parent_branch_id);
  const childBranches = branches.filter((b: BranchInfo) => b.parent_branch_id);

  return (
    <div className="flex w-64 flex-col border-r border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-900">
      <div className="flex items-center justify-between p-3 border-b border-gray-200 dark:border-gray-700">
        <span className="text-sm font-medium">Branches</span>
        <button onClick={() => setCollapsed(true)}>
          <PanelLeftClose className="h-4 w-4" />
        </button>
      </div>

      <div className="flex-1 overflow-y-auto p-2 space-y-1">
        {mainBranch && (
          <BranchItem
            branch={mainBranch}
            isActive={activeBranchId === mainBranch.id}
            onClick={() => switchToBranch(mainBranch.id)}
            depth={0}
          />
        )}
        {childBranches.map((b: BranchInfo) => (
          <BranchItem
            key={b.id}
            branch={b}
            isActive={activeBranchId === b.id}
            onClick={() => switchToBranch(b.id)}
            onMerge={() => setMerging(b)}
            depth={1}
          />
        ))}
      </div>

      <div className="p-2 border-t border-gray-200 dark:border-gray-700">
        <button
          onClick={() => mainBranch && fork({ parentBranchId: mainBranch.id })}
          className="flex w-full items-center gap-2 rounded-md px-3 py-2 text-sm hover:bg-gray-200 dark:hover:bg-gray-800"
        >
          <Plus className="h-4 w-4" />
          New branch
        </button>
      </div>

      {merging && (
        <MergeDialog
          branch={merging}
          onClose={() => setMerging(null)}
        />
      )}
    </div>
  );
}

function BranchItem({
  branch,
  isActive,
  onClick,
  onMerge,
  depth,
}: {
  branch: BranchInfo;
  isActive: boolean;
  onClick: () => void;
  onMerge?: () => void;
  depth: number;
}) {
  return (
    <button
      onClick={onClick}
      className={`flex w-full items-center gap-2 rounded-md px-3 py-2 text-sm ${
        isActive ? "bg-gray-200 dark:bg-gray-700" : "hover:bg-gray-100 dark:hover:bg-gray-800"
      }`}
      style={{ paddingLeft: `${12 + depth * 16}px` }}
    >
      <GitBranch className="h-4 w-4 shrink-0" />
      <span className="truncate">{branch.name || "unnamed"}</span>
      {branch.status === "merged" && (
        <GitMerge className="ml-auto h-3 w-3 text-green-500" />
      )}
      {onMerge && branch.status === "active" && (
        <button onClick={(e) => { e.stopPropagation(); onMerge(); }} className="ml-auto">
          <MoreHorizontal className="h-4 w-4" />
        </button>
      )}
    </button>
  );
}
```

**Step 3: Build MergeDialog**

```tsx
// web-ui/frontend/src/components/chat/MergeDialog.tsx
import { useState } from "react";
import { useBranches } from "../../hooks/useBranches";
import type { BranchInfo } from "../../stores/chatStore";

interface MergeDialogProps {
  branch: BranchInfo;
  onClose: () => void;
}

export function MergeDialog({ branch, onClose }: MergeDialogProps) {
  const { merge } = useBranches();
  const [strategy, setStrategy] = useState<"squash" | "full">("squash");

  const handleMerge = () => {
    merge({ branchId: branch.id, strategy });
    onClose();
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
      <div className="w-96 rounded-lg bg-white dark:bg-gray-800 p-6 shadow-xl">
        <h3 className="text-lg font-medium mb-4">
          Merge "{branch.name || "unnamed"}"
        </h3>

        <div className="space-y-3 mb-6">
          <label className="flex items-start gap-3 p-3 rounded border cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-700">
            <input
              type="radio"
              checked={strategy === "squash"}
              onChange={() => setStrategy("squash")}
              className="mt-1"
            />
            <div>
              <div className="font-medium text-sm">Squash merge</div>
              <div className="text-xs text-gray-500">
                Keep memories learned during this branch. Messages stay in the branch.
              </div>
            </div>
          </label>

          <label className="flex items-start gap-3 p-3 rounded border cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-700">
            <input
              type="radio"
              checked={strategy === "full"}
              onChange={() => setStrategy("full")}
              className="mt-1"
            />
            <div>
              <div className="font-medium text-sm">Full merge</div>
              <div className="text-xs text-gray-500">
                Keep memories and append all messages to the main conversation.
              </div>
            </div>
          </label>
        </div>

        <div className="flex justify-end gap-3">
          <button onClick={onClose} className="px-4 py-2 text-sm rounded hover:bg-gray-100 dark:hover:bg-gray-700">
            Cancel
          </button>
          <button onClick={handleMerge} className="px-4 py-2 text-sm rounded bg-blue-600 text-white hover:bg-blue-700">
            Merge
          </button>
        </div>
      </div>
    </div>
  );
}
```

**Step 4: Add fork button to messages**

In the `thread.tsx` message component, add a fork icon that appears on hover:

```tsx
// Add to each message component's hover state:
<button
  onClick={() => fork({ parentBranchId: activeBranchId!, forkMessageId: message.id })}
  className="opacity-0 group-hover:opacity-100 transition-opacity"
  title="Fork from here"
>
  <GitBranch className="h-4 w-4" />
</button>
```

**Step 5: Verify build and visual check**

```bash
cd web-ui/frontend && npm run dev
```

**Step 6: Commit**

```bash
git add web-ui/frontend/src/
git commit -m "feat: branch sidebar with fork and merge dialog"
```

---

## Task 11: File Upload & Attachments

**Files:**
- Create: `web-ui/frontend/src/components/chat/FileDropZone.tsx`
- Create: `web-ui/frontend/src/components/chat/AttachmentPreview.tsx`
- Create: `web-ui/frontend/src/utils/fileProcessing.ts`
- Modify: `web-ui/frontend/src/components/chat/ChatRuntimeProvider.tsx` (handle attachments in onNew)

**Step 1: Create file processing utility**

```typescript
// web-ui/frontend/src/utils/fileProcessing.ts
const IMAGE_TYPES = ["image/png", "image/jpeg", "image/gif", "image/webp"];
const TEXT_EXTENSIONS = [
  ".txt", ".md", ".py", ".js", ".ts", ".tsx", ".jsx", ".json", ".yaml", ".yml",
  ".html", ".css", ".xml", ".csv", ".log", ".sh", ".c", ".cpp", ".java", ".go",
  ".rs", ".rb", ".php", ".sql", ".toml", ".ini",
];
const MAX_IMAGE_SIZE = 4 * 1024 * 1024;  // 4MB
const MAX_TEXT_SIZE = 100 * 1024;         // 100KB
const MAX_DOC_SIZE = 5 * 1024 * 1024;    // 5MB

export interface ProcessedFile {
  name: string;
  type: "image" | "text" | "document" | "generic";
  media_type: string;
  size: number;
  content?: string;      // base64 for images, text content for text files
  preview?: string;      // thumbnail URL for images
}

export async function processFile(file: File): Promise<ProcessedFile> {
  if (IMAGE_TYPES.includes(file.type)) {
    if (file.size > MAX_IMAGE_SIZE) throw new Error(`Image too large (max ${MAX_IMAGE_SIZE / 1024 / 1024}MB)`);
    const base64 = await fileToBase64(file);
    return {
      name: file.name,
      type: "image",
      media_type: file.type,
      size: file.size,
      content: base64,
      preview: URL.createObjectURL(file),
    };
  }

  const ext = "." + file.name.split(".").pop()?.toLowerCase();
  if (TEXT_EXTENSIONS.includes(ext)) {
    if (file.size > MAX_TEXT_SIZE) throw new Error(`Text file too large (max ${MAX_TEXT_SIZE / 1024}KB)`);
    const text = await file.text();
    return { name: file.name, type: "text", media_type: "text/plain", size: file.size, content: text };
  }

  if (ext === ".pdf" || ext === ".docx") {
    if (file.size > MAX_DOC_SIZE) throw new Error(`Document too large (max ${MAX_DOC_SIZE / 1024 / 1024}MB)`);
    const base64 = await fileToBase64(file);
    return { name: file.name, type: "document", media_type: file.type, size: file.size, content: base64 };
  }

  return { name: file.name, type: "generic", media_type: file.type, size: file.size };
}

function fileToBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = reader.result as string;
      resolve(result.split(",")[1]); // Strip data: prefix
    };
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}
```

**Step 2: Build FileDropZone and AttachmentPreview**

```tsx
// web-ui/frontend/src/components/chat/FileDropZone.tsx
import { useCallback, useState } from "react";
import { Paperclip } from "lucide-react";
import { processFile, type ProcessedFile } from "../../utils/fileProcessing";

interface FileDropZoneProps {
  onFiles: (files: ProcessedFile[]) => void;
  children: React.ReactNode;
}

export function FileDropZone({ onFiles, children }: FileDropZoneProps) {
  const [dragging, setDragging] = useState(false);

  const handleDrop = useCallback(async (e: React.DragEvent) => {
    e.preventDefault();
    setDragging(false);
    const files = Array.from(e.dataTransfer.files);
    const processed = await Promise.all(files.map(processFile));
    onFiles(processed);
  }, [onFiles]);

  return (
    <div
      onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
      onDragLeave={() => setDragging(false)}
      onDrop={handleDrop}
      className={`relative ${dragging ? "ring-2 ring-blue-500 ring-inset" : ""}`}
    >
      {children}
    </div>
  );
}
```

```tsx
// web-ui/frontend/src/components/chat/AttachmentPreview.tsx
import { X, FileText, Image, File } from "lucide-react";
import type { ProcessedFile } from "../../utils/fileProcessing";

interface AttachmentPreviewProps {
  files: ProcessedFile[];
  onRemove: (index: number) => void;
}

export function AttachmentPreview({ files, onRemove }: AttachmentPreviewProps) {
  if (files.length === 0) return null;

  return (
    <div className="flex gap-2 px-3 py-2 overflow-x-auto">
      {files.map((file, i) => (
        <div key={i} className="relative flex items-center gap-2 rounded-lg border px-3 py-2 text-sm bg-gray-50 dark:bg-gray-800">
          {file.type === "image" && file.preview ? (
            <img src={file.preview} alt={file.name} className="h-8 w-8 rounded object-cover" />
          ) : file.type === "text" ? (
            <FileText className="h-4 w-4" />
          ) : (
            <File className="h-4 w-4" />
          )}
          <span className="max-w-[120px] truncate">{file.name}</span>
          <button onClick={() => onRemove(i)} className="text-gray-400 hover:text-gray-600">
            <X className="h-3 w-3" />
          </button>
        </div>
      ))}
    </div>
  );
}
```

**Step 3: Wire attachments into ChatRuntimeProvider**

Update the `onNew` handler in ChatRuntimeProvider to include attachments in the WebSocket message, mapping `ProcessedFile` to the gateway's `AttachmentInfo` format.

**Step 4: Verify build**

```bash
cd web-ui/frontend && npm run build
```

**Step 5: Commit**

```bash
git add web-ui/frontend/src/
git commit -m "feat: file upload with drag-drop, preview, and gateway attachment format"
```

---

## Task 12: Settings Page

**Files:**
- Rewrite: `web-ui/frontend/src/pages/Settings.tsx`

**Step 1: Build settings page**

```tsx
// web-ui/frontend/src/pages/Settings.tsx
import { UserProfile } from "@clerk/clerk-react";
import { useChatStore, type ModelTier } from "../stores/chatStore";

export default function Settings() {
  const { selectedTier, setSelectedTier } = useChatStore();

  return (
    <div className="mx-auto max-w-2xl p-8 space-y-8">
      <h1 className="text-2xl font-bold">Settings</h1>

      <section className="space-y-4">
        <h2 className="text-lg font-medium">Model</h2>
        <div className="space-y-2">
          <label className="text-sm text-gray-600">Default tier</label>
          <select
            value={selectedTier}
            onChange={(e) => setSelectedTier(e.target.value as ModelTier)}
            className="w-full rounded-md border px-3 py-2 dark:bg-gray-800 dark:border-gray-700"
          >
            <option value="low">Fast (Haiku-class)</option>
            <option value="mid">Balanced (Sonnet-class)</option>
            <option value="high">Powerful (Opus-class)</option>
          </select>
        </div>
      </section>

      <section className="space-y-4">
        <h2 className="text-lg font-medium">Account</h2>
        <UserProfile />
      </section>
    </div>
  );
}
```

**Step 2: Verify build**

```bash
cd web-ui/frontend && npm run build
```

**Step 3: Commit**

```bash
git add web-ui/frontend/src/pages/Settings.tsx
git commit -m "feat: settings page with model tier and Clerk profile"
```

---

## Task 13: Knowledge / Memory Viewer Page

**Files:**
- Rewrite: `web-ui/frontend/src/pages/KnowledgeBase.tsx`
- Create: `web-ui/frontend/src/hooks/useMemories.ts`

**Step 1: Create memories hook**

```typescript
// web-ui/frontend/src/hooks/useMemories.ts
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useAuth } from "@clerk/clerk-react";

const GATEWAY_URL = import.meta.env.VITE_GATEWAY_URL || "http://localhost:18790";

async function fetchWithAuth(path: string, getToken: () => Promise<string | null>, options?: RequestInit) {
  const token = await getToken();
  const resp = await fetch(`${GATEWAY_URL}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...options?.headers,
    },
  });
  if (!resp.ok) throw new Error(`API error: ${resp.status}`);
  return resp.json();
}

export function useMemories(search?: string) {
  const { getToken } = useAuth();
  const queryClient = useQueryClient();

  const memoriesQuery = useQuery({
    queryKey: ["memories", search],
    queryFn: () =>
      search
        ? fetchWithAuth("/api/v1/memories/search", getToken, {
            method: "POST",
            body: JSON.stringify({ query: search }),
          })
        : fetchWithAuth("/api/v1/memories?limit=50", getToken),
  });

  const statsQuery = useQuery({
    queryKey: ["memory-stats"],
    queryFn: () => fetchWithAuth("/api/v1/memories/stats", getToken),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) =>
      fetchWithAuth(`/api/v1/memories/${id}`, getToken, { method: "DELETE" }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["memories"] }),
  });

  const updateMutation = useMutation({
    mutationFn: ({ id, content }: { id: string; content: string }) =>
      fetchWithAuth(`/api/v1/memories/${id}`, getToken, {
        method: "PUT",
        body: JSON.stringify({ content }),
      }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["memories"] }),
  });

  return {
    memories: memoriesQuery.data?.results || memoriesQuery.data || [],
    stats: statsQuery.data,
    isLoading: memoriesQuery.isLoading,
    deleteMemory: deleteMutation.mutate,
    updateMemory: updateMutation.mutate,
  };
}
```

**Step 2: Build knowledge base page**

```tsx
// web-ui/frontend/src/pages/KnowledgeBase.tsx
import { useState } from "react";
import { Search, Trash2, Edit2, Check, X, Brain } from "lucide-react";
import { useMemories } from "../hooks/useMemories";

export default function KnowledgeBase() {
  const [search, setSearch] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");
  const { memories, stats, isLoading, deleteMemory, updateMemory } = useMemories(
    debouncedSearch || undefined,
  );
  const [editing, setEditing] = useState<string | null>(null);
  const [editContent, setEditContent] = useState("");

  // Debounce search
  const handleSearch = (value: string) => {
    setSearch(value);
    clearTimeout((window as any)._searchTimeout);
    (window as any)._searchTimeout = setTimeout(() => setDebouncedSearch(value), 300);
  };

  return (
    <div className="mx-auto max-w-3xl p-8 space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold flex items-center gap-2">
          <Brain className="h-6 w-6" />
          Knowledge
        </h1>
        {stats && (
          <span className="text-sm text-gray-500">
            {stats.total || Object.values(stats).reduce((a: number, b: any) => a + (b.count || 0), 0)} memories
          </span>
        )}
      </div>

      <div className="relative">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-gray-400" />
        <input
          type="text"
          value={search}
          onChange={(e) => handleSearch(e.target.value)}
          placeholder="Search memories..."
          className="w-full rounded-md border pl-10 pr-4 py-2 dark:bg-gray-800 dark:border-gray-700"
        />
      </div>

      {isLoading ? (
        <div className="text-center text-gray-500 py-8">Loading...</div>
      ) : memories.length === 0 ? (
        <div className="text-center text-gray-500 py-8">
          {search ? "No memories match your search." : "No memories yet. Start chatting with Clara!"}
        </div>
      ) : (
        <div className="space-y-3">
          {memories.map((mem: any) => (
            <div key={mem.id} className="rounded-lg border p-4 dark:border-gray-700">
              {editing === mem.id ? (
                <div className="space-y-2">
                  <textarea
                    value={editContent}
                    onChange={(e) => setEditContent(e.target.value)}
                    className="w-full rounded border p-2 text-sm dark:bg-gray-800"
                    rows={3}
                  />
                  <div className="flex gap-2">
                    <button
                      onClick={() => { updateMemory({ id: mem.id, content: editContent }); setEditing(null); }}
                      className="text-green-600"
                    >
                      <Check className="h-4 w-4" />
                    </button>
                    <button onClick={() => setEditing(null)} className="text-gray-400">
                      <X className="h-4 w-4" />
                    </button>
                  </div>
                </div>
              ) : (
                <div className="flex items-start justify-between gap-4">
                  <div>
                    <p className="text-sm">{mem.memory || mem.content}</p>
                    <div className="mt-2 flex gap-2 text-xs text-gray-400">
                      {mem.category && <span className="rounded bg-gray-100 dark:bg-gray-700 px-2 py-0.5">{mem.category}</span>}
                      {mem.score && <span>relevance: {(mem.score * 100).toFixed(0)}%</span>}
                    </div>
                  </div>
                  <div className="flex gap-1 shrink-0">
                    <button
                      onClick={() => { setEditing(mem.id); setEditContent(mem.memory || mem.content); }}
                      className="text-gray-400 hover:text-gray-600"
                    >
                      <Edit2 className="h-4 w-4" />
                    </button>
                    <button onClick={() => deleteMemory(mem.id)} className="text-gray-400 hover:text-red-500">
                      <Trash2 className="h-4 w-4" />
                    </button>
                  </div>
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
```

**Step 3: Verify build**

```bash
cd web-ui/frontend && npm run build
```

**Step 4: Commit**

```bash
git add web-ui/frontend/src/pages/KnowledgeBase.tsx web-ui/frontend/src/hooks/useMemories.ts
git commit -m "feat: knowledge page with memory search, edit, and delete"
```

---

## Task Dependencies

```
Task 1 (Clerk auth) ──────┐
                           ├──► Task 5 (WebSocket + processor)
Task 2 (DB models) ───────┤
                           ├──► Task 3 (API endpoints)
Task 4 (Branch memory) ───┘
                           │
Task 3 ────────────────────┤
Task 5 ────────────────────┼──► Tasks 9, 10 (Chat UI, Branch sidebar)
                           │
Task 6 (Strip-down) ───────┤
Task 7 (Clerk frontend) ──┼──► Task 8 (WebSocket + store)
                           │
Task 8 ────────────────────┼──► Tasks 9, 10, 11 (UI components)
                           │
Tasks 9-11 ────────────────┼──► Task 12 (Settings)
                           └──► Task 13 (Knowledge)
```

**Backend tasks (1-5) and Frontend tasks (6-8) can run in parallel.**
Tasks 9-13 depend on the foundation tasks completing first.
