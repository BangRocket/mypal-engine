"""Entity resolution — map platform IDs to human-readable names.

Resolves identifiers like "discord-271274659385835521" to "Josh".
Maintains an alias registry that persists across sessions.

Used by the knowledge graph to create meaningful entity nodes
instead of raw platform IDs.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from mypalclara.config.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Callable

logger = get_logger("entity_resolver")

# Patterns for platform-prefixed identifiers.
_PLATFORM_PREFIX_RE = re.compile(r"^(discord|teams|slack|telegram|matrix|signal|whatsapp)-(.+)$")

_NAME_EXTRACTION_PROMPT = """\
Given this conversation, identify any real names the user mentions for themselves \
or others.

Return JSON:
{
  "user_name": "Josh" or null,
  "mentioned_people": [
    {"name": "Kinsey", "relationship": "therapist"},
    {"name": "Anne", "relationship": "daughter"}
  ]
}

Only include names you're confident about. Don't guess."""

# SQL for the entity_aliases table — executed lazily on first DB write.
_CREATE_TABLE_SQL = """\
CREATE TABLE IF NOT EXISTS entity_aliases (
    id TEXT PRIMARY KEY,
    identifier TEXT NOT NULL UNIQUE,
    canonical_name TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'manual',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
)"""

_CREATE_INDEX_IDENTIFIER_SQL = "CREATE INDEX IF NOT EXISTS ix_entity_aliases_identifier ON entity_aliases (identifier)"
_CREATE_INDEX_NAME_SQL = (
    "CREATE INDEX IF NOT EXISTS ix_entity_aliases_canonical_name ON entity_aliases (canonical_name)"
)

_UPSERT_SQL_SQLITE = """\
INSERT INTO entity_aliases (id, identifier, canonical_name, source, created_at, updated_at)
VALUES (:id, :identifier, :canonical_name, :source, :created_at, :updated_at)
ON CONFLICT (identifier) DO UPDATE SET
    canonical_name = :canonical_name,
    source = :source,
    updated_at = :updated_at"""

_UPSERT_SQL_PG = """\
INSERT INTO entity_aliases (id, identifier, canonical_name, source, created_at, updated_at)
VALUES (:id, :identifier, :canonical_name, :source, :created_at, :updated_at)
ON CONFLICT (identifier) DO UPDATE SET
    canonical_name = EXCLUDED.canonical_name,
    source = EXCLUDED.source,
    updated_at = EXCLUDED.updated_at"""

_SELECT_ALL_SQL = "SELECT identifier, canonical_name, source FROM entity_aliases"


class EntityResolver:
    """Resolves platform IDs to human-readable names.

    Maintains a bidirectional mapping:
    - aliases: raw_id -> canonical_name
    - reverse: canonical_name -> set of raw_ids

    Persists to database via SQLAlchemy (entity_aliases table).
    Falls back to in-memory if no DB available.
    """

    def __init__(self, session_factory: Callable[[], Any] | None = None) -> None:
        # Forward mapping: identifier -> canonical_name
        self._aliases: dict[str, str] = {}
        # Reverse mapping: canonical_name (lowercased) -> set of identifiers
        self._reverse: dict[str, set[str]] = {}

        self._session_factory = session_factory
        self._table_ensured = False

        if session_factory is not None:
            self.load_from_db()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def resolve(self, identifier: str) -> str:
        """Return the canonical name for an identifier.

        Checks the identifier as-is first, then tries stripping any
        platform prefix (e.g. ``discord-123`` -> ``123``).

        Returns the identifier unchanged if no mapping exists.
        """
        # Fast path: exact match.
        name = self._aliases.get(identifier)
        if name is not None:
            return name

        # Try without the platform prefix.
        match = _PLATFORM_PREFIX_RE.match(identifier)
        if match:
            bare_id = match.group(2)
            name = self._aliases.get(bare_id)
            if name is not None:
                return name

        return identifier

    def register(self, identifier: str, canonical_name: str, source: str = "manual") -> None:
        """Register (or update) a mapping from *identifier* to *canonical_name*.

        Also updates the reverse index and persists to the database when
        a session factory is available.
        """
        old_name = self._aliases.get(identifier)

        # Update forward mapping.
        self._aliases[identifier] = canonical_name

        # Update reverse mapping — remove stale entry if the name changed.
        if old_name is not None and old_name.lower() != canonical_name.lower():
            old_set = self._reverse.get(old_name.lower())
            if old_set:
                old_set.discard(identifier)
                if not old_set:
                    del self._reverse[old_name.lower()]

        self._reverse.setdefault(canonical_name.lower(), set()).add(identifier)

        logger.info("Registered alias: %s -> %s (source=%s)", identifier, canonical_name, source)

        if self._session_factory is not None:
            self._save_to_db(identifier, canonical_name, source)

    def register_from_conversation(
        self,
        messages: list[dict[str, str]],
        user_id: str,
        llm_callable: Callable[[list[Any]], str] | None = None,
    ) -> dict[str, Any]:
        """Use an LLM to extract real names from conversation and register them.

        Args:
            messages: Recent conversation messages (list of dicts with ``role``
                      and ``content`` keys).
            user_id: The platform user ID to associate with any self-identified
                     name (e.g. ``"discord-271274659385835521"``).
            llm_callable: A callable that accepts a list of message dicts and
                          returns a string.  If *None*, this method is a no-op.

        Returns:
            A dict summarising what was extracted and registered::

                {"user_name": "Josh" | None,
                 "mentioned_people": [{"name": ..., "relationship": ...}, ...]}

            Returns an empty dict when no LLM is available or extraction fails.
        """
        if llm_callable is None:
            return {}

        # Build the prompt.  We feed a condensed version of the conversation
        # so the LLM has context but the prompt stays small.
        conversation_text = _format_conversation(messages)
        prompt_messages: list[dict[str, str]] = [
            {"role": "system", "content": _NAME_EXTRACTION_PROMPT},
            {"role": "user", "content": (
                "Extract names from this conversation. "
                "Respond ONLY with the JSON object, nothing else.\n\n"
                "<conversation>\n" + conversation_text + "\n</conversation>"
            )},
        ]

        try:
            raw_response = llm_callable(prompt_messages)
        except Exception:
            logger.exception("LLM call failed during name extraction")
            return {}

        extracted = _parse_llm_json(raw_response)
        if extracted is None:
            return {}

        user_name: str | None = extracted.get("user_name")
        mentioned: list[dict[str, str]] = extracted.get("mentioned_people", [])

        if user_name and isinstance(user_name, str):
            self.register(user_id, user_name, source="conversation")

        result: dict[str, Any] = {
            "user_name": user_name,
            "mentioned_people": mentioned,
        }
        return result

    def get_aliases(self, canonical_name: str) -> list[str]:
        """Return all known identifiers for *canonical_name*."""
        return sorted(self._reverse.get(canonical_name.lower(), set()))

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def load_from_db(self) -> None:
        """Load all aliases from the database into memory."""
        if self._session_factory is None:
            return

        try:
            self._ensure_table()
            session = self._session_factory()
            try:
                from sqlalchemy import text

                rows = session.execute(text(_SELECT_ALL_SQL)).fetchall()
                for identifier, canonical_name, _source in rows:
                    self._aliases[identifier] = canonical_name
                    self._reverse.setdefault(canonical_name.lower(), set()).add(identifier)

                logger.info("Loaded %d entity aliases from database", len(rows))
            finally:
                session.close()
        except Exception:
            logger.exception("Failed to load entity aliases from database — running in-memory only")

    def _save_to_db(self, identifier: str, canonical_name: str, source: str = "manual") -> None:
        """Persist a single mapping to the database."""
        if self._session_factory is None:
            return

        try:
            self._ensure_table()
            session = self._session_factory()
            try:
                from sqlalchemy import text

                now = datetime.now(timezone.utc)
                params = {
                    "id": str(uuid.uuid4()),
                    "identifier": identifier,
                    "canonical_name": canonical_name,
                    "source": source,
                    "created_at": now,
                    "updated_at": now,
                }

                # Detect dialect for upsert syntax.
                dialect = session.bind.dialect.name if session.bind else "sqlite"
                upsert_sql = _UPSERT_SQL_PG if dialect == "postgresql" else _UPSERT_SQL_SQLITE
                session.execute(text(upsert_sql), params)
                session.commit()
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()
        except Exception:
            logger.exception("Failed to persist entity alias %s -> %s", identifier, canonical_name)

    def _ensure_table(self) -> None:
        """Create the entity_aliases table if it doesn't already exist."""
        if self._table_ensured or self._session_factory is None:
            return

        session = self._session_factory()
        try:
            from sqlalchemy import text

            session.execute(text(_CREATE_TABLE_SQL))
            session.execute(text(_CREATE_INDEX_IDENTIFIER_SQL))
            session.execute(text(_CREATE_INDEX_NAME_SQL))
            session.commit()
            self._table_ensured = True
        except Exception:
            session.rollback()
            logger.exception("Failed to ensure entity_aliases table")
        finally:
            session.close()


# ------------------------------------------------------------------
# Private helpers
# ------------------------------------------------------------------


def _format_conversation(messages: list[dict[str, str]], max_messages: int = 20) -> str:
    """Collapse a message list into a compact text block for the LLM."""
    lines: list[str] = []
    for msg in messages[-max_messages:]:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        if content:
            lines.append(f"{role}: {content}")
    return "\n".join(lines)


def _parse_llm_json(raw: str) -> dict[str, Any] | None:
    """Best-effort parse of JSON from an LLM response.

    Handles responses wrapped in markdown code fences.
    """
    text = raw.strip()

    # Strip markdown code fences (```json ... ``` or ``` ... ```).
    fence_match = re.search(r"```(?:json)?\s*\n?(.*?)```", text, re.DOTALL)
    if fence_match:
        text = fence_match.group(1).strip()

    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
        logger.warning("LLM returned JSON but not an object: %s", type(parsed).__name__)
        return None
    except json.JSONDecodeError:
        logger.warning("Failed to parse LLM response as JSON: %.200s", text)
        return None
