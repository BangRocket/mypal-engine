"""Episode memory — verbatim conversation chunks with rich metadata.

Episodes are the primary storage unit in Clara's memory system.
They capture meaningful conversation exchanges (not every message)
with metadata about participants, topics, emotional tone, and significance.

Stored in a dedicated Qdrant collection (clara_episodes).
"""

from __future__ import annotations

import json
import logging
import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    OrderBy,
    PayloadSchemaType,
    PointStruct,
    Range,
    VectorParams,
)

from mypalclara.core.memory.config import EMBEDDING_MODEL_DIMS

logger = logging.getLogger("clara.memory.episodes")

# Collection name for episodes (separate from clara_memories)
EPISODES_COLLECTION = "clara_episodes"


@dataclass
class Episode:
    """A meaningful conversation exchange captured from a session."""

    id: str
    content: str  # Verbatim conversation text (multi-turn)
    summary: str  # One-line LLM summary for L1 display
    user_id: str
    agent_id: str = "clara"
    participants: list[str] = field(default_factory=list)
    topics: list[str] = field(default_factory=list)
    emotional_tone: str = "neutral"
    significance: float = 0.5
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    session_id: str | None = None
    message_count: int = 0

    def to_payload(self) -> dict:
        """Convert episode to a Qdrant-compatible payload dict."""
        return {
            "id": self.id,
            "content": self.content,
            "summary": self.summary,
            "user_id": self.user_id,
            "agent_id": self.agent_id,
            "participants": self.participants,
            "topics": self.topics,
            "emotional_tone": self.emotional_tone,
            "significance": self.significance,
            "timestamp": self.timestamp.isoformat(),
            "session_id": self.session_id,
            "message_count": self.message_count,
        }

    @classmethod
    def from_payload(cls, payload: dict) -> Episode:
        """Reconstruct an Episode from a Qdrant payload dict."""
        ts = payload.get("timestamp")
        if isinstance(ts, str):
            # Handle both timezone-aware and naive ISO timestamps
            ts = datetime.fromisoformat(ts)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=UTC)
        elif ts is None:
            ts = datetime.now(UTC)

        return cls(
            id=payload["id"],
            content=payload["content"],
            summary=payload["summary"],
            user_id=payload["user_id"],
            agent_id=payload.get("agent_id", "clara"),
            participants=payload.get("participants", []),
            topics=payload.get("topics", []),
            emotional_tone=payload.get("emotional_tone", "neutral"),
            significance=payload.get("significance", 0.5),
            timestamp=ts,
            session_id=payload.get("session_id"),
            message_count=payload.get("message_count", 0),
        )


@dataclass
class NarrativeArc:
    """A narrative arc synthesized from multiple episodes over time."""

    id: str
    title: str                    # "The job search", "Sleep and mental health"
    summary: str                  # 2-3 sentences describing trajectory
    status: str = "active"        # active, resolved, dormant
    user_id: str = ""
    agent_id: str = "clara"
    key_episode_ids: list[str] = field(default_factory=list)
    emotional_trajectory: str = ""  # "started anxious, becoming more determined"
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_payload(self) -> dict:
        return {
            "id": self.id,
            "type": "narrative_arc",
            "title": self.title,
            "summary": self.summary,
            "status": self.status,
            "user_id": self.user_id,
            "agent_id": self.agent_id,
            "key_episode_ids": self.key_episode_ids,
            "emotional_trajectory": self.emotional_trajectory,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

    @classmethod
    def from_payload(cls, payload: dict) -> "NarrativeArc":
        created = payload.get("created_at")
        if isinstance(created, str):
            created = datetime.fromisoformat(created)
            if created.tzinfo is None:
                created = created.replace(tzinfo=UTC)
        elif created is None:
            created = datetime.now(UTC)

        updated = payload.get("updated_at")
        if isinstance(updated, str):
            updated = datetime.fromisoformat(updated)
            if updated.tzinfo is None:
                updated = updated.replace(tzinfo=UTC)
        elif updated is None:
            updated = datetime.now(UTC)

        return cls(
            id=payload["id"],
            title=payload.get("title", ""),
            summary=payload.get("summary", ""),
            status=payload.get("status", "active"),
            user_id=payload.get("user_id", ""),
            agent_id=payload.get("agent_id", "clara"),
            key_episode_ids=payload.get("key_episode_ids", []),
            emotional_trajectory=payload.get("emotional_trajectory", ""),
            created_at=created,
            updated_at=updated,
        )


class EpisodeStore:
    """Storage and retrieval for conversation episodes in Qdrant.

    Uses a dedicated collection (clara_episodes) separate from the main
    memory store (clara_memories).
    """

    def __init__(self, embedding_model, qdrant_config: dict):
        """Initialize the episode store.

        Args:
            embedding_model: An embedder instance with an ``embed(text, memory_action)``
                method (e.g. HuggingFaceEmbedding, OpenAIEmbedding, or CachedEmbedding).
            qdrant_config: Connection config dict. Accepted keys mirror the main
                Qdrant vector store: ``url``, ``api_key``, ``path`` (for local),
                ``host``/``port``.
        """
        self.embedding_model = embedding_model
        self.dims = EMBEDDING_MODEL_DIMS

        # Build Qdrant client — mirrors logic in vector/qdrant.py
        params: dict = {}
        self.is_local = False

        if qdrant_config.get("url"):
            params["url"] = qdrant_config["url"]
        if qdrant_config.get("api_key"):
            params["api_key"] = qdrant_config["api_key"]
        if qdrant_config.get("host") and qdrant_config.get("port"):
            params["host"] = qdrant_config["host"]
            params["port"] = qdrant_config["port"]

        if not params:
            # Fall back to local path-based Qdrant
            path = qdrant_config.get("path", str(os.getenv("QDRANT_DATA_DIR", "qdrant_data")))
            params["path"] = path
            self.is_local = True

        timeout = int(os.getenv("QDRANT_TIMEOUT", "15"))
        self.client = QdrantClient(**params, timeout=timeout)

        self._ensure_collection()

    def _ensure_collection(self) -> None:
        """Create the episodes collection if it doesn't exist."""
        existing = self.client.get_collections()
        for col in existing.collections:
            if col.name == EPISODES_COLLECTION:
                logger.debug(f"Collection {EPISODES_COLLECTION} already exists.")
                self._ensure_indexes()
                return

        self.client.create_collection(
            collection_name=EPISODES_COLLECTION,
            vectors_config=VectorParams(
                size=self.dims,
                distance=Distance.COSINE,
                on_disk=not self.is_local,
            ),
        )
        logger.info(f"Created collection {EPISODES_COLLECTION} (dims={self.dims})")
        self._ensure_indexes()

    def _ensure_indexes(self) -> None:
        """Create payload indexes for fast filtering."""
        if self.is_local:
            logger.debug("Skipping payload index creation for local Qdrant")
            return

        index_fields = {
            "user_id": PayloadSchemaType.KEYWORD,
            "agent_id": PayloadSchemaType.KEYWORD,
            "emotional_tone": PayloadSchemaType.KEYWORD,
            "significance": PayloadSchemaType.FLOAT,
            "timestamp": PayloadSchemaType.DATETIME,
        }

        for field_name, schema_type in index_fields.items():
            try:
                self.client.create_payload_index(
                    collection_name=EPISODES_COLLECTION,
                    field_name=field_name,
                    field_schema=schema_type,
                )
                logger.debug(f"Created index for {field_name} in {EPISODES_COLLECTION}")
            except Exception as e:
                # Index may already exist — that's fine
                logger.debug(f"Index for {field_name} may already exist: {e}")

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def store(self, episode: Episode) -> str:
        """Embed and store an episode in Qdrant.

        Args:
            episode: The Episode to store.

        Returns:
            The episode ID.
        """
        vector = self.embedding_model.embed(episode.content, memory_action="add")
        point = PointStruct(
            id=episode.id,
            vector=vector,
            payload=episode.to_payload(),
        )
        self.client.upsert(collection_name=EPISODES_COLLECTION, points=[point])
        logger.info(
            f"Stored episode {episode.id} (sig={episode.significance:.2f}, "
            f"tone={episode.emotional_tone}, topics={episode.topics})"
        )
        return episode.id

    # ------------------------------------------------------------------
    # Read — semantic search
    # ------------------------------------------------------------------

    def search(
        self,
        query: str,
        user_id: str,
        limit: int = 5,
        min_significance: float = 0.0,
    ) -> list[Episode]:
        """Semantic search over episodes.

        Args:
            query: Natural-language search query.
            user_id: Scope results to this user.
            limit: Maximum number of episodes to return.
            min_significance: Only return episodes at or above this significance.

        Returns:
            List of matching Episodes ordered by relevance.
        """
        vector = self.embedding_model.embed(query, memory_action="search")

        conditions = [
            FieldCondition(key="user_id", match=MatchValue(value=user_id)),
        ]
        if min_significance > 0.0:
            conditions.append(
                FieldCondition(key="significance", range=Range(gte=min_significance)),
            )

        try:
            hits = self.client.query_points(
                collection_name=EPISODES_COLLECTION,
                query=vector,
                query_filter=Filter(must=conditions),
                limit=limit,
                with_payload=True,
            )
            return [Episode.from_payload(hit.payload) for hit in hits.points]
        except Exception as e:
            logger.error(f"Episode search failed: {e}")
            return []

    # ------------------------------------------------------------------
    # Read — recency
    # ------------------------------------------------------------------

    def get_recent(self, user_id: str, limit: int = 5) -> list[Episode]:
        """Get the most recent episodes for a user.

        Args:
            user_id: The user to fetch episodes for.
            limit: Maximum number of episodes.

        Returns:
            List of Episodes ordered newest-first.
        """
        try:
            results, _next_page = self.client.scroll(
                collection_name=EPISODES_COLLECTION,
                scroll_filter=Filter(
                    must=[FieldCondition(key="user_id", match=MatchValue(value=user_id))],
                ),
                limit=limit,
                order_by=OrderBy(key="timestamp", direction="desc"),
                with_payload=True,
                with_vectors=False,
            )
            return [Episode.from_payload(point.payload) for point in results]
        except Exception as e:
            # OrderBy requires payload index on timestamp — fall back to
            # fetching all and sorting client-side if the index doesn't exist
            # (e.g. local Qdrant without index support).
            logger.debug(f"OrderBy scroll failed ({e}), falling back to client-side sort")
            return self._get_recent_fallback(user_id, limit)

    def _get_recent_fallback(self, user_id: str, limit: int) -> list[Episode]:
        """Fallback for get_recent when server-side ordering isn't available."""
        try:
            results, _ = self.client.scroll(
                collection_name=EPISODES_COLLECTION,
                scroll_filter=Filter(
                    must=[FieldCondition(key="user_id", match=MatchValue(value=user_id))],
                ),
                limit=200,  # Fetch a reasonable batch to sort locally
                with_payload=True,
                with_vectors=False,
            )
            episodes = [Episode.from_payload(point.payload) for point in results]
            episodes.sort(key=lambda ep: ep.timestamp, reverse=True)
            return episodes[:limit]
        except Exception as e:
            logger.error(f"Episode get_recent fallback failed: {e}")
            return []

    # ------------------------------------------------------------------
    # Read — topic filter
    # ------------------------------------------------------------------

    def get_by_topic(self, topic: str, user_id: str, limit: int = 5) -> list[Episode]:
        """Search episodes by topic tag.

        Uses semantic search with the topic as query, filtered by user_id,
        so that related topics (e.g. "career" vs "job search") also surface.

        Args:
            topic: Topic string to search for.
            user_id: Scope results to this user.
            limit: Maximum number of episodes.

        Returns:
            List of matching Episodes ordered by relevance.
        """
        # Semantic search scoped to the user gives better recall than
        # exact payload matching on the topics list.
        return self.search(query=topic, user_id=user_id, limit=limit)

    # ------------------------------------------------------------------
    # Narrative arcs
    # ------------------------------------------------------------------

    def store_arc(self, arc: "NarrativeArc") -> str:
        """Store a narrative arc in the episodes collection.

        Arcs are stored alongside episodes with type=narrative_arc in metadata.
        """
        embedding = self.embedding_model.embed(
            f"{arc.title}: {arc.summary}", "add"
        )
        point = PointStruct(
            id=arc.id,
            vector=embedding,
            payload=arc.to_payload(),
        )
        self.client.upsert(
            collection_name=EPISODES_COLLECTION,
            points=[point],
        )
        logger.info(f"Stored arc '{arc.title}' ({arc.id})")
        return arc.id

    def get_active_arcs(self, user_id: str, limit: int = 10) -> list["NarrativeArc"]:
        """Get active narrative arcs for a user."""
        try:
            results = self.client.scroll(
                collection_name=EPISODES_COLLECTION,
                scroll_filter=Filter(
                    must=[
                        FieldCondition(key="user_id", match=MatchValue(value=user_id)),
                        FieldCondition(key="type", match=MatchValue(value="narrative_arc")),
                        FieldCondition(key="status", match=MatchValue(value="active")),
                    ]
                ),
                limit=limit,
            )
            points = results[0] if results else []
            return [NarrativeArc.from_payload(p.payload) for p in points]
        except Exception as e:
            logger.warning(f"Failed to get active arcs: {e}")
            return []


# ---------------------------------------------------------------------------
# Episode extraction from conversations
# ---------------------------------------------------------------------------

_EXTRACTION_PROMPT = """\
You are analyzing a conversation to identify meaningful exchanges worth remembering.

For each meaningful exchange, provide:
- start_index and end_index (which messages form this exchange, 0-indexed, inclusive)
- summary: one sentence capturing the essence
- topics: list of topic tags
- emotional_tone: the dominant emotional quality (vulnerable, playful, frustrated, \
reflective, neutral, warm, anxious, determined, etc.)
- significance: 0.0-1.0 how meaningful this exchange is
  - 0.1-0.3: casual chat, greetings, quick Q&A
  - 0.4-0.6: useful information exchange, task discussion
  - 0.7-0.9: emotionally meaningful, important decisions, deep discussions
  - 1.0: life-changing moments

Return a JSON array of exchanges. Not every message needs to be in an exchange \
— skip trivial filler. If the conversation contains nothing meaningful, return an \
empty array [].

Conversation:
{conversation}

Respond ONLY with a JSON array (no commentary, no markdown fences)."""


def _strip_code_fences(text: str) -> str:
    """Remove markdown code fences wrapping JSON output."""
    text = text.strip()
    # ```json ... ``` or ``` ... ```
    match = re.match(r"^```(?:json)?\s*\n?(.*?)\n?\s*```$", text, re.DOTALL)
    if match:
        return match.group(1).strip()
    return text


def _format_conversation(messages: list[dict]) -> str:
    """Format messages into a readable conversation string for the LLM."""
    lines: list[str] = []
    for i, msg in enumerate(messages):
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        ts = msg.get("timestamp", "")
        ts_str = f" [{ts}]" if ts else ""
        lines.append(f"[{i}] {role}{ts_str}: {content}")
    return "\n".join(lines)


def _build_episode_content(messages: list[dict], start: int, end: int) -> str:
    """Extract verbatim content for an episode from the message range."""
    parts: list[str] = []
    for msg in messages[start : end + 1]:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        parts.append(f"{role}: {content}")
    return "\n".join(parts)


def _infer_participants(messages: list[dict], start: int, end: int) -> list[str]:
    """Derive participant names from the message slice."""
    participants: set[str] = set()
    for msg in messages[start : end + 1]:
        role = msg.get("role", "unknown")
        name = msg.get("name")
        if name:
            participants.add(name)
        elif role == "user":
            participants.add("User")
        elif role == "assistant":
            participants.add("Clara")
        else:
            participants.add(role.capitalize())
    return sorted(participants)


def _parse_timestamp(messages: list[dict], start: int) -> datetime:
    """Try to derive a timestamp from the first message in the exchange."""
    msg = messages[start] if start < len(messages) else {}
    ts = msg.get("timestamp")
    if ts is None:
        return datetime.now(UTC)
    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            return ts.replace(tzinfo=UTC)
        return ts
    if isinstance(ts, (int, float)):
        return datetime.fromtimestamp(ts, tz=UTC)
    if isinstance(ts, str):
        try:
            dt = datetime.fromisoformat(ts)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            return dt
        except ValueError:
            pass
    return datetime.now(UTC)


def extract_episodes(
    messages: list[dict],
    user_id: str,
    session_id: str | None = None,
    llm_callable=None,
) -> list[Episode]:
    """Extract meaningful episodes from a conversation.

    Args:
        messages: List of message dicts with at least ``role`` and ``content``.
            Optional keys: ``timestamp``, ``name``.
        user_id: The user this conversation belongs to.
        session_id: Optional session identifier.
        llm_callable: A callable ``(messages: list[dict]) -> str`` that calls
            an LLM and returns the text response. If ``None``, no extraction
            is performed and an empty list is returned.

    Returns:
        List of Episode objects ready to store.
    """
    if not messages or llm_callable is None:
        return []

    conversation_text = _format_conversation(messages)
    prompt = _EXTRACTION_PROMPT.format(conversation=conversation_text)

    try:
        llm_response = llm_callable([{"role": "user", "content": prompt}])
    except Exception as e:
        logger.error(f"LLM call for episode extraction failed: {e}")
        return []

    # Parse response
    raw = _strip_code_fences(llm_response)
    try:
        exchanges = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning(f"Failed to parse episode extraction JSON: {raw[:200]}")
        return []

    if not isinstance(exchanges, list):
        logger.warning(f"Expected JSON array from extraction, got {type(exchanges).__name__}")
        return []

    episodes: list[Episode] = []
    for ex in exchanges:
        try:
            start = int(ex["start_index"])
            end = int(ex["end_index"])
        except (KeyError, TypeError, ValueError):
            logger.debug(f"Skipping exchange with invalid indices: {ex}")
            continue

        # Clamp indices to message bounds
        start = max(0, start)
        end = min(len(messages) - 1, end)
        if start > end:
            continue

        content = _build_episode_content(messages, start, end)
        summary = ex.get("summary", content[:120])
        topics = ex.get("topics", [])
        if isinstance(topics, str):
            topics = [topics]
        emotional_tone = ex.get("emotional_tone", "neutral")
        try:
            significance = float(ex.get("significance", 0.5))
            significance = max(0.0, min(1.0, significance))
        except (TypeError, ValueError):
            significance = 0.5

        episode = Episode(
            id=str(uuid.uuid4()),
            content=content,
            summary=summary,
            user_id=user_id,
            agent_id="clara",
            participants=_infer_participants(messages, start, end),
            topics=topics,
            emotional_tone=emotional_tone,
            significance=significance,
            timestamp=_parse_timestamp(messages, start),
            session_id=session_id,
            message_count=end - start + 1,
        )
        episodes.append(episode)

    logger.info(f"Extracted {len(episodes)} episodes from {len(messages)} messages")
    return episodes
