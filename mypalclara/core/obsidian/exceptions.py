"""Typed exceptions raised by the Obsidian client.

Callers should catch ObsidianError for the umbrella case, or a specific
subclass to distinguish auth/not-found/rate-limit/connection/server failures.
"""

from __future__ import annotations


class ObsidianError(Exception):
    """Base exception for all Obsidian client failures."""


class ObsidianAuthError(ObsidianError):
    """Raised when the Obsidian REST API returns 401 or 403."""


class ObsidianNotFoundError(ObsidianError):
    """Raised when the Obsidian REST API returns 404."""


class ObsidianRateLimitError(ObsidianError):
    """Raised when the Obsidian REST API returns 429."""


class ObsidianConnectionError(ObsidianError):
    """Raised on network/transport failures (connect error, timeout)."""


class ObsidianServerError(ObsidianError):
    """Raised when the Obsidian REST API returns a 5xx response."""
