"""Cross-platform user identity resolution.

Consolidates CanonicalUser / PlatformLink queries that were previously
duplicated across the gateway processor and web API modules.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.orm import Session as OrmSession

logger = logging.getLogger("db.user_identity")


def resolve_all_user_ids(prefixed_user_id: str, db: "OrmSession | None" = None) -> list[str]:
    """Resolve a prefixed user_id to ALL linked prefixed user_ids via PlatformLink.

    Falls back to ``[prefixed_user_id]`` when no PlatformLink exists.

    Args:
        prefixed_user_id: e.g. ``"discord-123"``
        db: Optional DB session. One is created (and closed) if not provided.

    Returns:
        List of all prefixed user_ids for this canonical user.
    """
    from mypalclara.db import SessionLocal
    from mypalclara.db.models import PlatformLink

    close_db = False
    if db is None:
        db = SessionLocal()
        close_db = True

    try:
        link = db.query(PlatformLink).filter(PlatformLink.prefixed_user_id == prefixed_user_id).first()
        if not link:
            return [prefixed_user_id]

        all_links = (
            db.query(PlatformLink.prefixed_user_id)
            .filter(PlatformLink.canonical_user_id == link.canonical_user_id)
            .all()
        )
        return [row[0] for row in all_links]
    except Exception as e:
        logger.debug(f"resolve_all_user_ids failed for {prefixed_user_id}: {e}")
        return [prefixed_user_id]
    finally:
        if close_db:
            db.close()


def resolve_all_user_ids_for_canonical(canonical_user_id: str, db: "OrmSession") -> list[str]:
    """Resolve a CanonicalUser.id to all linked prefixed user_ids.

    Used by web API endpoints that already have the canonical user.

    Args:
        canonical_user_id: UUID of the CanonicalUser
        db: Active DB session

    Returns:
        List of prefixed user_ids (e.g. ``["discord-123", "teams-456"]``).
    """
    from mypalclara.db.models import PlatformLink

    links = db.query(PlatformLink).filter(PlatformLink.canonical_user_id == canonical_user_id).all()
    return [link.prefixed_user_id for link in links]


def ensure_platform_link(
    prefixed_user_id: str,
    display_name: str | None = None,
    db: "OrmSession | None" = None,
) -> None:
    """Auto-create a CanonicalUser + PlatformLink if none exists for this prefixed_user_id.

    Idempotent — no-op if the link already exists.

    Args:
        prefixed_user_id: e.g. ``"discord-123"``
        display_name: Optional display name for the new CanonicalUser
        db: Optional DB session. One is created (and closed) if not provided.
    """
    from mypalclara.db import SessionLocal
    from mypalclara.db.models import CanonicalUser, PlatformLink

    close_db = False
    if db is None:
        db = SessionLocal()
        close_db = True

    try:
        existing = db.query(PlatformLink).filter(PlatformLink.prefixed_user_id == prefixed_user_id).first()
        if existing:
            return

        # Parse platform and raw id from prefixed format
        if "-" in prefixed_user_id:
            platform, platform_user_id = prefixed_user_id.split("-", 1)
        else:
            platform = "unknown"
            platform_user_id = prefixed_user_id

        name = display_name or prefixed_user_id

        canonical = CanonicalUser(display_name=name)
        db.add(canonical)
        db.flush()  # get canonical.id

        link = PlatformLink(
            canonical_user_id=canonical.id,
            platform=platform,
            platform_user_id=platform_user_id,
            prefixed_user_id=prefixed_user_id,
            display_name=name,
            linked_via="auto",
        )
        db.add(link)
        db.commit()

        logger.info(f"Auto-created CanonicalUser + PlatformLink for {prefixed_user_id}")
    except Exception as e:
        db.rollback()
        logger.warning(f"ensure_platform_link failed for {prefixed_user_id}: {e}")
    finally:
        if close_db:
            db.close()
