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
