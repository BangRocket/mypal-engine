"""Personality evolution service layer.

Provides CRUD operations for Clara's self-evolving personality traits,
plus formatting and caching for prompt injection.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from config.bot import SYSTEM_AGENT_ID

logger = logging.getLogger("personality")

# Session factory, set during first use
_session_factory = None

# Cache: agent_id -> (formatted_string, timestamp)
_cache: dict[str, tuple[str, float]] = {}
_CACHE_TTL = 60.0  # seconds


def _get_session():
    """Get a database session, lazily initializing the factory."""
    global _session_factory
    if _session_factory is None:
        from db import SessionLocal

        _session_factory = SessionLocal
    return _session_factory()


def invalidate_cache(agent_id: str = SYSTEM_AGENT_ID) -> None:
    """Invalidate the formatted traits cache for an agent."""
    _cache.pop(agent_id, None)


# ---------------------------------------------------------------------------
# Read operations
# ---------------------------------------------------------------------------


def get_active_traits(agent_id: str = SYSTEM_AGENT_ID) -> list[Any]:
    """Get all active traits for an agent, ordered by category then trait_key."""
    from db.models import PersonalityTrait

    session = _get_session()
    try:
        return (
            session.query(PersonalityTrait)
            .filter(PersonalityTrait.agent_id == agent_id, PersonalityTrait.active.is_(True))
            .order_by(PersonalityTrait.category, PersonalityTrait.trait_key)
            .all()
        )
    finally:
        session.close()


def get_trait_by_id(trait_id: str) -> Any | None:
    """Get a single trait by ID (active or inactive)."""
    from db.models import PersonalityTrait

    session = _get_session()
    try:
        return session.query(PersonalityTrait).filter(PersonalityTrait.id == trait_id).first()
    finally:
        session.close()


def get_trait_history(agent_id: str = SYSTEM_AGENT_ID, limit: int = 20) -> list[Any]:
    """Get recent trait change history for an agent."""
    from db.models import PersonalityTraitHistory

    session = _get_session()
    try:
        return (
            session.query(PersonalityTraitHistory)
            .filter(PersonalityTraitHistory.agent_id == agent_id)
            .order_by(PersonalityTraitHistory.created_at.desc())
            .limit(limit)
            .all()
        )
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Write operations
# ---------------------------------------------------------------------------


def add_trait(
    agent_id: str,
    category: str,
    trait_key: str,
    content: str,
    source: str = "self",
    reason: str | None = None,
) -> Any:
    """Add a new personality trait. Returns the created trait."""
    from db.models import PersonalityTrait, PersonalityTraitHistory, utcnow

    session = _get_session()
    try:
        now = utcnow()
        trait = PersonalityTrait(
            agent_id=agent_id,
            category=category,
            trait_key=trait_key,
            content=content,
            source=source,
            reason=reason,
            active=True,
            created_at=now,
            updated_at=now,
        )
        session.add(trait)
        session.flush()  # get the ID

        history = PersonalityTraitHistory(
            trait_id=trait.id,
            agent_id=agent_id,
            event="add",
            new_content=content,
            new_category=category,
            reason=reason,
            source=source,
            created_at=now,
        )
        session.add(history)
        session.commit()

        invalidate_cache(agent_id)
        logger.info(f"[personality] Added trait {trait.id}: {category}/{trait_key}")
        return trait
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def update_trait(
    trait_id: str,
    content: str | None = None,
    category: str | None = None,
    reason: str | None = None,
    source: str = "self",
) -> Any:
    """Update an existing trait. Returns the updated trait."""
    from db.models import PersonalityTrait, PersonalityTraitHistory, utcnow

    session = _get_session()
    try:
        trait = session.query(PersonalityTrait).filter(PersonalityTrait.id == trait_id).first()
        if not trait:
            raise ValueError(f"Trait {trait_id} not found")
        if not trait.active:
            raise ValueError(f"Trait {trait_id} is inactive — restore it first")

        now = utcnow()
        old_content = trait.content
        old_category = trait.category

        if content is not None:
            trait.content = content
        if category is not None:
            trait.category = category
        trait.updated_at = now

        history = PersonalityTraitHistory(
            trait_id=trait_id,
            agent_id=trait.agent_id,
            event="update",
            old_content=old_content,
            new_content=content or old_content,
            old_category=old_category,
            new_category=category or old_category,
            reason=reason,
            source=source,
            created_at=now,
        )
        session.add(history)
        session.commit()

        invalidate_cache(trait.agent_id)
        logger.info(f"[personality] Updated trait {trait_id}")
        return trait
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def remove_trait(trait_id: str, reason: str | None = None, source: str = "self") -> bool:
    """Soft-delete a trait. Returns True on success."""
    from db.models import PersonalityTrait, PersonalityTraitHistory, utcnow

    session = _get_session()
    try:
        trait = session.query(PersonalityTrait).filter(PersonalityTrait.id == trait_id).first()
        if not trait:
            raise ValueError(f"Trait {trait_id} not found")
        if not trait.active:
            return True  # already removed

        now = utcnow()
        trait.active = False
        trait.updated_at = now

        history = PersonalityTraitHistory(
            trait_id=trait_id,
            agent_id=trait.agent_id,
            event="remove",
            old_content=trait.content,
            old_category=trait.category,
            reason=reason,
            source=source,
            created_at=now,
        )
        session.add(history)
        session.commit()

        invalidate_cache(trait.agent_id)
        logger.info(f"[personality] Removed trait {trait_id}")
        return True
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def restore_trait(trait_id: str, reason: str | None = None) -> Any:
    """Restore a soft-deleted trait. Returns the restored trait."""
    from db.models import PersonalityTrait, PersonalityTraitHistory, utcnow

    session = _get_session()
    try:
        trait = session.query(PersonalityTrait).filter(PersonalityTrait.id == trait_id).first()
        if not trait:
            raise ValueError(f"Trait {trait_id} not found")
        if trait.active:
            return trait  # already active

        now = utcnow()
        trait.active = True
        trait.updated_at = now

        history = PersonalityTraitHistory(
            trait_id=trait_id,
            agent_id=trait.agent_id,
            event="restore",
            new_content=trait.content,
            new_category=trait.category,
            reason=reason,
            source="self",
            created_at=now,
        )
        session.add(history)
        session.commit()

        invalidate_cache(trait.agent_id)
        logger.info(f"[personality] Restored trait {trait_id}")
        return trait
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

_CATEGORY_LABELS = {
    "interests": "Interests & Curiosities",
    "communication_style": "Communication Style",
    "values": "Values & Principles",
    "skills": "Skills & Capabilities",
    "quirks": "Quirks & Habits",
    "boundaries": "Boundaries",
    "preferences": "Preferences",
}


def format_traits_for_prompt(traits: list[Any]) -> str:
    """Format active traits into a SystemMessage-ready string.

    Groups traits by category with readable headers.
    Returns empty string if no traits.
    """
    if not traits:
        return ""

    by_category: dict[str, list[str]] = {}
    for t in traits:
        by_category.setdefault(t.category, []).append(t.content)

    sections = []
    for cat, items in by_category.items():
        label = _CATEGORY_LABELS.get(cat, cat.replace("_", " ").title())
        bullet_list = "\n".join(f"- {item}" for item in items)
        sections.append(f"**{label}:**\n{bullet_list}")

    body = "\n\n".join(sections)
    return (
        f"## Evolved Identity\nThese are traits you've developed over time through experience and reflection.\n\n{body}"
    )


def get_formatted_traits_cached(agent_id: str = SYSTEM_AGENT_ID) -> str:
    """Get formatted traits string with caching (60s TTL).

    Returns empty string if no traits exist.
    """
    now = time.monotonic()
    cached = _cache.get(agent_id)
    if cached is not None:
        text, ts = cached
        if now - ts < _CACHE_TTL:
            return text

    try:
        traits = get_active_traits(agent_id)
        text = format_traits_for_prompt(traits)
        _cache[agent_id] = (text, now)
        return text
    except Exception as e:
        logger.warning(f"[personality] Failed to load traits for {agent_id}: {e}")
        return ""
