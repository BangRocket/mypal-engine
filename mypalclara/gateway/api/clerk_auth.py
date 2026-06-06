"""Clerk JWT verification and Clerk → CanonicalUser resolution for the web app."""

from __future__ import annotations

import os

import jwt
from jwt import PyJWKClient
from sqlalchemy.orm import Session as DBSession

from mypalclara.db.connection import SessionLocal  # noqa: F401  (patched in tests)
from mypalclara.db.models import CanonicalUser, PlatformLink

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
