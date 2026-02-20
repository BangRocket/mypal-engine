"""Memory management for Clara platform.

Provides the MemoryManager singleton facade that delegates to:
- SessionManager: thread/message persistence
- MemoryRetriever: Rook context fetching and caching
- MemoryWriter: memory extraction and storage
- MemoryDynamicsManager: FSRS scoring and promotion
- MemoryIngestionManager: smart ingest and supersession
- PromptBuilder: prompt construction with persona and context
- IntentionManager: intention setting and checking
"""

from __future__ import annotations

import os
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, ClassVar

from mypalclara.config.logging import get_logger
from mypalclara.core.llm.messages import Message

# Module loggers
logger = get_logger("rook")
thread_logger = get_logger("thread")
memory_logger = get_logger("memory")

# Timezone for message timestamps (defaults to America/New_York)
DEFAULT_TIMEZONE = os.getenv("DEFAULT_TIMEZONE", "America/New_York")

if TYPE_CHECKING:
    from sqlalchemy.orm import Session as OrmSession

    from mypalclara.db.models import MemoryDynamics, Message, Session

# Configuration constants
CONTEXT_MESSAGE_COUNT = 30  # Direct conversation history
CHANNEL_CONTEXT_COUNT = 50  # Channel-wide context (all users)
SUMMARY_INTERVAL = 10
MAX_SEARCH_QUERY_CHARS = 6000
MAX_KEY_MEMORIES = 15  # Key memories always included in every request
MAX_MEMORIES_PER_TYPE = 35  # Limit per type (50 total - 15 reserved for key memories)
MAX_GRAPH_RELATIONS = 20  # Limit graph relations to avoid prompt bloat

# FSRS scoring weights
FSRS_SEMANTIC_WEIGHT = 0.6  # Semantic similarity is primary signal
FSRS_DYNAMICS_WEIGHT = 0.4  # FSRS retrievability modulates ranking

# Smart ingestion thresholds
SMART_INGEST_SKIP_THRESHOLD = 0.95  # Nearly identical — skip
SMART_INGEST_UPDATE_THRESHOLD = 0.75  # Similar enough to update
SMART_INGEST_SUPERSEDE_THRESHOLD = 0.6  # Similar topic, may contradict

# Context slicing
MEMORY_CONTEXT_SLICE = 4  # Recent messages for memory extraction context
THREAD_SUMMARY_MAX_MESSAGES = 30  # Messages included in thread summary

# Access log pruning
MEMORY_ACCESS_LOG_RETENTION_DAYS = 90
PRUNE_CHECK_FREQUENCY = 100  # Check every N promote_memory calls


def _format_message_timestamp(dt: datetime | None) -> str:
    """Format a message timestamp for display in conversation history.

    Returns short time format like "10:43 PM" in the configured timezone.
    """
    if dt is None:
        return ""

    try:
        from zoneinfo import ZoneInfo

        tz = ZoneInfo(DEFAULT_TIMEZONE)
        # Convert to local timezone if UTC
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        local_dt = dt.astimezone(tz)
        return local_dt.strftime("%-I:%M %p")
    except Exception:
        # Fallback to UTC
        if dt.tzinfo is None:
            return dt.strftime("%H:%M UTC")
        return dt.strftime("%-I:%M %p")


class MemoryManager:
    """Central orchestrator for Clara's memory system.

    Thin facade that delegates to specialized sub-managers:
    - SessionManager: thread/message persistence
    - MemoryRetriever: Rook context fetching and caching
    - MemoryWriter: memory extraction and storage
    - MemoryDynamicsManager: FSRS scoring and promotion
    - MemoryIngestionManager: smart ingest and supersession
    - PromptBuilder: prompt construction with persona and context
    - IntentionManager: intention setting and checking

    Entity Scoping:
    - user_id: The human user (e.g., "discord-271274659385835521")
    - agent_id: The bot persona (e.g., "clara", "flo")

    This is a singleton - use MemoryManager.get_instance() after initialization.
    """

    _instance: ClassVar["MemoryManager | None"] = None

    def __init__(
        self,
        llm_callable: Callable[[list[dict]], str],
        agent_id: str = "clara",
        on_memory_event: Callable[[str, dict[str, Any]], None] | None = None,
    ):
        """Initialize MemoryManager.

        Args:
            llm_callable: Function that takes messages and returns LLM response
            agent_id: Bot persona identifier for entity-scoped memory (default: "clara")
            on_memory_event: Optional callback for memory events (retrieval, extraction).
                Called with (event_type, data) where event_type is "memory_retrieved"
                or "memory_extracted" and data contains event-specific information.
        """
        from mypalclara.core.intention_manager import IntentionManager
        from mypalclara.core.memory_dynamics_manager import MemoryDynamicsManager
        from mypalclara.core.memory_ingestion import MemoryIngestionManager
        from mypalclara.core.memory_retriever import MemoryRetriever
        from mypalclara.core.memory_writer import MemoryWriter
        from mypalclara.core.prompt_builder import PromptBuilder
        from mypalclara.core.session_manager import SessionManager

        self.llm = llm_callable
        self.agent_id = agent_id
        self._on_memory_event = on_memory_event

        # Sub-managers
        self._session_manager = SessionManager(llm_callable=llm_callable)
        self._dynamics_manager = MemoryDynamicsManager()
        self._intention_manager = IntentionManager(agent_id=agent_id)
        self._ingestion_manager = MemoryIngestionManager(agent_id=agent_id, dynamics_manager=self._dynamics_manager)
        self._memory_retriever = MemoryRetriever(
            agent_id=agent_id,
            on_memory_event=on_memory_event,
            dynamics_manager=self._dynamics_manager,
        )
        self._prompt_builder = PromptBuilder(agent_id=agent_id, llm_callable=llm_callable)
        self._memory_writer = MemoryWriter(
            agent_id=agent_id,
            on_memory_event=on_memory_event,
            ingestion_manager=self._ingestion_manager,
            dynamics_manager=self._dynamics_manager,
            on_memories_changed=self._memory_retriever._invalidate_memory_cache,
        )

    @classmethod
    def get_instance(cls) -> "MemoryManager":
        """Get the singleton MemoryManager instance.

        Raises:
            RuntimeError: If not initialized
        """
        if cls._instance is None:
            raise RuntimeError("MemoryManager not initialized. Call MemoryManager.initialize() first.")
        return cls._instance

    @classmethod
    def initialize(
        cls,
        llm_callable: Callable[[list[dict]], str],
        agent_id: str | None = None,
        on_memory_event: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> "MemoryManager":
        """Initialize the singleton MemoryManager.

        Args:
            llm_callable: Function that takes messages and returns LLM response
            agent_id: Bot persona identifier. If None, uses BOT_NAME env var or "clara".
            on_memory_event: Optional callback for memory events (retrieval, extraction).

        Returns:
            The initialized MemoryManager instance
        """
        if cls._instance is None:
            # Get agent_id from BOT_NAME env var if not provided
            if agent_id is None:
                agent_id = os.getenv("BOT_NAME", "clara").lower()
            cls._instance = cls(
                llm_callable=llm_callable,
                agent_id=agent_id,
                on_memory_event=on_memory_event,
            )
            memory_logger.info(f"MemoryManager initialized (agent_id={agent_id})")
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Reset the singleton (for testing)."""
        cls._instance = None

    # ---------- User ID Normalization ----------

    def normalize_user_id(self, user_id: str, platform: str = "api") -> str:
        """Normalize a user ID to unified format: {platform}-{id}."""
        known_platforms = ["api", "discord", "slack", "telegram"]
        for p in known_platforms:
            if user_id.startswith(f"{p}-"):
                return user_id
        return f"{platform}-{user_id}"

    def parse_user_id(self, unified_user_id: str) -> tuple[str, str]:
        """Parse a unified user ID into (platform, original_id)."""
        if "-" in unified_user_id:
            parts = unified_user_id.split("-", 1)
            return parts[0], parts[1]
        return "unknown", unified_user_id

    # ---------- Session management (delegates to SessionManager) ----------

    def get_or_create_session(
        self,
        db: "OrmSession",
        user_id: str,
        context_id: str = "default",
        project_id: str | None = None,
        title: str | None = None,
    ) -> "Session":
        """Get or create a session with platform-agnostic context."""
        return self._session_manager.get_or_create_session(db, user_id, context_id, project_id, title)

    def get_thread(self, db: "OrmSession", thread_id: str) -> "Session | None":
        """Get a thread by ID."""
        return self._session_manager.get_thread(db, thread_id)

    def get_recent_messages(self, db: "OrmSession", thread_id: str) -> list["Message"]:
        """Get recent messages from a thread."""
        return self._session_manager.get_recent_messages(db, thread_id)

    def get_message_count(self, db: "OrmSession", thread_id: str) -> int:
        """Get total message count for a thread."""
        return self._session_manager.get_message_count(db, thread_id)

    def store_message(
        self,
        db: "OrmSession",
        thread_id: str,
        user_id: str,
        role: str,
        content: str,
    ) -> "Message":
        """Store a message in a thread."""
        return self._session_manager.store_message(db, thread_id, user_id, role, content)

    def should_update_summary(self, db: "OrmSession", thread_id: str) -> bool:
        """Check if thread summary should be updated."""
        return self._session_manager.should_update_summary(db, thread_id)

    def update_thread_summary(self, db: "OrmSession", thread: "Session") -> str:
        """Generate/update summary for a thread."""
        return self._session_manager.update_thread_summary(db, thread)

    # ---------- Memory retrieval (delegates to MemoryRetriever) ----------

    def fetch_mem0_context(
        self,
        user_id: str,
        project_id: str,
        user_message: str,
        participants: list[dict] | None = None,
        is_dm: bool = False,
    ) -> tuple[list[str], list[str], list[dict]]:
        """Fetch relevant memories from mem0 using parallel fetches."""
        return self._memory_retriever.fetch_mem0_context(
            user_id,
            project_id,
            user_message,
            participants,
            is_dm,
        )

    # ---------- Memory writing (delegates to MemoryWriter) ----------

    def add_to_mem0(
        self,
        user_id: str,
        project_id: str,
        recent_msgs: list["Message"],
        user_message: str,
        assistant_reply: str,
        participants: list[dict] | None = None,
        is_dm: bool = False,
    ) -> None:
        """Send conversation slice to mem0 for memory extraction."""
        self._memory_writer.add_to_mem0(
            user_id,
            project_id,
            recent_msgs,
            user_message,
            assistant_reply,
            participants,
            is_dm,
        )

    def add_to_memory(
        self,
        user_id: str,
        user_message: str,
        assistant_reply: str,
        is_dm: bool = False,
    ) -> None:
        """Simplified method to add a conversation exchange to memory."""
        self._memory_writer.add_to_memory(user_id, user_message, assistant_reply, is_dm)

    # ---------- Prompt building (delegates to PromptBuilder) ----------

    def fetch_emotional_context(
        self,
        user_id: str,
        limit: int = 3,
        max_age_days: int = 7,
    ) -> list[dict]:
        """Fetch recent emotional context memories for session warmth."""
        return self._prompt_builder.fetch_emotional_context(user_id, limit, max_age_days)

    def build_prompt(
        self,
        user_mems: list[str],
        proj_mems: list[str],
        thread_summary: str | None,
        recent_msgs: list["Message"],
        user_message: str,
        emotional_context: list[dict] | None = None,
        recurring_topics: list[dict] | None = None,
        graph_relations: list[dict] | None = None,
        tools: list[dict] | None = None,
        channel_context: list | None = None,
        model_name: str = "claude",
    ) -> list[Message]:
        """Build the full prompt for the LLM."""
        return self._prompt_builder.build_prompt(
            user_mems,
            proj_mems,
            thread_summary,
            recent_msgs,
            user_message,
            emotional_context=emotional_context,
            recurring_topics=recurring_topics,
            graph_relations=graph_relations,
            tools=tools,
            channel_context=channel_context,
            model_name=model_name,
        )

    def fetch_topic_recurrence(
        self,
        user_id: str,
        lookback_days: int = 14,
        min_mentions: int = 2,
    ) -> list[dict]:
        """Fetch recurring topic patterns for a user."""
        return self._prompt_builder.fetch_topic_recurrence(user_id, lookback_days, min_mentions)

    # ---------- FSRS-6 Memory Dynamics (delegates to MemoryDynamicsManager) ----------

    def get_memory_dynamics(self, memory_id: str, user_id: str) -> "MemoryDynamics | None":
        """Get FSRS dynamics for a memory."""
        return self._dynamics_manager.get_memory_dynamics(memory_id, user_id)

    def ensure_memory_dynamics(self, memory_id: str, user_id: str, is_key: bool = False) -> "MemoryDynamics":
        """Ensure FSRS dynamics exist for a memory, creating if needed."""
        return self._dynamics_manager.ensure_memory_dynamics(memory_id, user_id, is_key)

    def promote_memory(
        self,
        memory_id: str,
        user_id: str,
        grade: int = 3,
        signal_type: str = "used_in_response",
    ) -> None:
        """Mark memory as successfully recalled, updating FSRS state."""
        self._dynamics_manager.promote_memory(memory_id, user_id, grade, signal_type)

    def prune_old_access_logs(
        self,
        db: "OrmSession",
        retention_days: int = MEMORY_ACCESS_LOG_RETENTION_DAYS,
    ) -> int:
        """Delete MemoryAccessLog records older than retention period."""
        return self._dynamics_manager.prune_old_access_logs(db, retention_days)

    def demote_memory(self, memory_id: str, user_id: str, reason: str = "user_correction") -> None:
        """Mark memory as incorrect/outdated, decreasing stability."""
        self._dynamics_manager.demote_memory(memory_id, user_id, reason)

    def calculate_memory_score(self, memory_id: str, user_id: str, semantic_score: float) -> float:
        """Calculate composite score for memory ranking."""
        return self._dynamics_manager.calculate_memory_score(memory_id, user_id, semantic_score)

    def get_last_retrieved_memory_ids(self, user_id: str) -> list[str]:
        """Get memory IDs from the last retrieval for a user."""
        return self._dynamics_manager.get_last_retrieved_memory_ids(user_id)

    def _rank_results_with_fsrs_batch(self, results: list[dict], user_id: str) -> list[dict]:
        """Re-rank search results using batched FSRS lookups."""
        return self._dynamics_manager.rank_results_with_fsrs_batch(results, user_id)

    # ---------- Prediction Error Gating (delegates to MemoryIngestionManager) ----------

    def _validate_ingested_memories(self, mem_results: list[dict], user_id: str) -> list[dict]:
        """Validate newly ingested memories against existing ones."""
        return self._ingestion_manager.validate_ingested_memories(mem_results, user_id)

    def smart_ingest(
        self,
        content: str,
        user_id: str,
        metadata: dict | None = None,
        exclude_ids: list[str] | None = None,
    ) -> tuple[str, str | None]:
        """Intelligently decide how to handle new information."""
        return self._ingestion_manager.smart_ingest(content, user_id, metadata, exclude_ids)

    def _record_supersession(
        self,
        old_memory_id: str,
        new_memory_id: str,
        user_id: str,
        reason: str = "contradiction",
    ) -> None:
        """Record a supersession relationship and demote the old memory."""
        self._ingestion_manager._record_supersession(old_memory_id, new_memory_id, user_id, reason)

    def supersede_memory(
        self,
        old_memory_id: str,
        new_content: str,
        user_id: str,
        reason: str = "contradiction",
        metadata: dict | None = None,
    ) -> str | None:
        """Replace an old memory with new information."""
        return self._ingestion_manager.supersede_memory(old_memory_id, new_content, user_id, reason, metadata)

    # ---------- Intentions (delegates to IntentionManager) ----------

    def set_intention(
        self,
        user_id: str,
        content: str,
        trigger_conditions: dict,
        expires_at: datetime | None = None,
        source_memory_id: str | None = None,
    ) -> str:
        """Create a new intention/reminder for future surfacing."""
        return self._intention_manager.set_intention(
            user_id,
            content,
            trigger_conditions,
            expires_at,
            source_memory_id,
        )

    def check_intentions(self, user_id: str, message: str, context: dict | None = None) -> list[dict]:
        """Check if any intentions should fire for the given context."""
        return self._intention_manager.check_intentions(user_id, message, context)

    def format_intentions_for_prompt(self, fired_intentions: list[dict]) -> str:
        """Format fired intentions for the system prompt."""
        return self._intention_manager.format_intentions_for_prompt(fired_intentions)
