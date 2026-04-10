"""Memory management for Clara platform.

Provides the MemoryManager singleton facade that delegates to:
- SessionManager: thread/message persistence
- MemoryRetriever: Palace context fetching and caching
- MemoryWriter: memory extraction and storage
- MemoryDynamicsManager: FSRS scoring and promotion
- MemoryIngestionManager: smart ingest and supersession
- PromptBuilder: prompt construction with persona and context
- IntentionManager: intention setting and checking
"""

from __future__ import annotations

import os
from collections.abc import Callable
from datetime import datetime
from typing import TYPE_CHECKING, Any, ClassVar

from mypalclara.config.logging import get_logger
from mypalclara.core.llm.messages import Message
from mypalclara.core.memory.config import (
    CHANNEL_CONTEXT_COUNT,
    CONTEXT_MESSAGE_COUNT,
    FSRS_DYNAMICS_WEIGHT,
    FSRS_SEMANTIC_WEIGHT,
    MAX_GRAPH_RELATIONS,
    MAX_KEY_MEMORIES,
    MAX_MEMORIES_PER_TYPE,
    MAX_SEARCH_QUERY_CHARS,
    MEMORY_ACCESS_LOG_RETENTION_DAYS,
    MEMORY_CONTEXT_SLICE,
    PRUNE_CHECK_FREQUENCY,
    SMART_INGEST_SKIP_THRESHOLD,
    SMART_INGEST_SUPERSEDE_THRESHOLD,
    SMART_INGEST_UPDATE_THRESHOLD,
    SUMMARY_INTERVAL,
    THREAD_SUMMARY_MAX_MESSAGES,
    _format_message_timestamp,
)

# Module loggers
logger = get_logger("palace")
thread_logger = get_logger("thread")
memory_logger = get_logger("memory")

if TYPE_CHECKING:
    from sqlalchemy.orm import Session as OrmSession

    from mypalclara.db.models import MemoryDynamics, Message, Session


class MemoryManager:
    """Central orchestrator for Clara's memory system.

    Thin facade that delegates to specialized sub-managers:
    - SessionManager: thread/message persistence
    - MemoryRetriever: Palace context fetching and caching
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
        from mypalclara.core.memory.dynamics.manager import MemoryDynamicsManager
        from mypalclara.core.memory.ingestion import MemoryIngestionManager
        from mypalclara.core.memory.intentions import IntentionManager
        from mypalclara.core.memory.retrieval import MemoryRetriever
        from mypalclara.core.memory.session import SessionManager
        from mypalclara.core.memory.writer import MemoryWriter
        from mypalclara.core.prompt_builder import PromptBuilder

        self.llm = llm_callable
        self.agent_id = agent_id
        self._on_memory_event = on_memory_event

        # Sub-managers (existing)
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

        # New subsystems (episodic memory, entity resolution, layered retrieval)
        self._episode_store = None  # Lazy — initialized on first use
        self._entity_resolver = None  # Lazy
        self._layered_retrieval = None  # Lazy

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

    def fetch_context(
        self,
        user_id: str,
        project_id: str,
        user_message: str,
        participants: list[dict] | None = None,
        is_dm: bool = False,
        privacy_scope: str = "full",
    ) -> tuple[list[str], list[str], list[dict]]:
        """Fetch relevant memories from Palace using parallel fetches.

        Args:
            user_id: The user making the request
            project_id: Project context
            user_message: The message to search for relevant memories
            participants: List of participant dicts for conversation members
            is_dm: Whether this is a DM conversation
            privacy_scope: 'full' for DMs (all memories), 'public_only' for
                group channels (only memories with visibility='public')
        """
        return self._memory_retriever.fetch_context(
            user_id,
            project_id,
            user_message,
            participants=participants,
            is_dm=is_dm,
            privacy_scope=privacy_scope,
        )

    # ---------- Memory writing (delegates to MemoryWriter) ----------

    def add_to_palace(
        self,
        user_id: str,
        project_id: str,
        recent_msgs: list["Message"],
        user_message: str,
        assistant_reply: str,
        participants: list[dict] | None = None,
        is_dm: bool = False,
    ) -> None:
        """Send conversation slice to Palace for memory extraction."""
        self._memory_writer.add_to_palace(
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
        """Simplified method to add a conversation exchange to Palace memory."""
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
        privacy_scope: str = "full",
        user_id: str | None = None,
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
            privacy_scope=privacy_scope,
            user_id=user_id,
        )

    def build_prompt_layered(
        self,
        user_id: str,
        user_message: str,
        recent_msgs: list,
        channel_context: list | None = None,
        tools: list[dict] | None = None,
        model_name: str = "claude",
        privacy_scope: str = "full",
    ) -> list:
        """Build prompt using layered retrieval (episodes, graph, memories).

        Replaces the old build_prompt with user_mems/proj_mems split.
        """
        return self._prompt_builder.build_prompt_layered(
            user_id=user_id,
            user_message=user_message,
            recent_msgs=recent_msgs,
            memory_manager=self,
            channel_context=channel_context,
            tools=tools,
            model_name=model_name,
            privacy_scope=privacy_scope,
        )

    async def load_user_workspace(self, user_id: str, vm_manager: object) -> None:
        """Load per-user workspace files from a VM into the prompt builder cache."""
        await self._prompt_builder.load_user_workspace(user_id, vm_manager)

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

    # ---------- Episode Store ----------

    @property
    def episode_store(self):
        """Lazy-initialized episode store."""
        if self._episode_store is None:
            try:
                from mypalclara.core.memory.config import PALACE
                from mypalclara.core.memory.episodes import EpisodeStore

                if PALACE is not None:
                    # Build qdrant config from env (same source as main memory store)
                    qdrant_config = {}
                    qdrant_url = os.getenv("QDRANT_URL")
                    if qdrant_url:
                        qdrant_config["url"] = qdrant_url
                        api_key = os.getenv("QDRANT_API_KEY")
                        if api_key:
                            qdrant_config["api_key"] = api_key

                    self._episode_store = EpisodeStore(
                        embedding_model=PALACE.embedding_model,
                        qdrant_config=qdrant_config,
                    )
                    memory_logger.info("Episode store initialized")
            except Exception as e:
                memory_logger.warning(f"Episode store unavailable: {e}")
        return self._episode_store

    @property
    def entity_resolver(self):
        """Lazy-initialized entity resolver."""
        if self._entity_resolver is None:
            try:
                from mypalclara.core.memory.entity_resolver import EntityResolver

                self._entity_resolver = EntityResolver()
                memory_logger.info("Entity resolver initialized")
            except Exception as e:
                memory_logger.warning(f"Entity resolver unavailable: {e}")
        return self._entity_resolver

    @property
    def layered_retrieval(self):
        """Lazy-initialized layered retrieval."""
        if self._layered_retrieval is None:
            try:
                from mypalclara.core.memory.retrieval_layers import LayeredRetrieval

                self._layered_retrieval = LayeredRetrieval()
                memory_logger.info("Layered retrieval initialized")
            except Exception as e:
                memory_logger.warning(f"Layered retrieval unavailable: {e}")
        return self._layered_retrieval

    # ---------- Session Reflection ----------

    def reflect_on_session(
        self,
        messages: list[dict],
        user_id: str,
        session_id: str | None = None,
    ) -> dict | None:
        """Run session reflection — extract episodes, entities, self-notes.

        Call this after a conversation session ends. Stores episodes,
        updates the knowledge graph, and returns the reflection result.
        """
        from mypalclara.core.memory.reflection import (
            build_episodes_from_reflection,
            extract_self_notes,
            reflect_on_session,
        )

        # Create a fresh LLM callable (self.llm may be a factory like make_llm)
        from mypalclara.core import make_llm

        llm = make_llm()

        reflection = reflect_on_session(messages, llm)
        if not reflection:
            return None

        # Store episodes
        if not self.episode_store:
            memory_logger.warning("Episode store not available — episodes will not be stored")
        if self.episode_store:
            episode_dicts = build_episodes_from_reflection(
                reflection, messages, user_id, session_id
            )
            for ep_dict in episode_dicts:
                try:
                    from mypalclara.core.memory.episodes import Episode

                    episode = Episode(
                        id=str(__import__("uuid").uuid4()),
                        **ep_dict,
                    )
                    self.episode_store.store(episode)
                except Exception as e:
                    memory_logger.warning(f"Failed to store episode: {e}")

        # Update entity resolver from conversation
        if self.entity_resolver:
            try:
                self.entity_resolver.register_from_conversation(
                    messages, user_id, llm_callable=self.llm
                )
            except Exception as e:
                memory_logger.warning(f"Entity resolution failed: {e}")

        # Store self-notes as semantic memories
        self_notes = extract_self_notes(reflection)
        if self_notes:
            from mypalclara.core.memory.config import PALACE

            if PALACE is not None:
                for note in self_notes:
                    memory_logger.info(f"Self-note: {note}")
                    try:
                        PALACE.add(
                            messages=[{"role": "assistant", "content": note}],
                            user_id=user_id,
                            agent_id=self.agent_id,
                            metadata={"memory_type": "self_awareness", "category": "self_awareness"},
                            infer=False,
                        )
                    except Exception as e:
                        memory_logger.debug(f"Failed to store self-note: {e}")
            else:
                for note in self_notes:
                    memory_logger.info(f"Self-note: {note}")

        return reflection

    def run_narrative_synthesis(self, user_id: str) -> list[dict]:
        """Synthesize narrative arcs from recent episodes.

        Called periodically (daily/weekly) to connect episodes into
        ongoing stories and arcs.
        """
        from mypalclara.core.memory.reflection import synthesize_narratives

        if not self.episode_store:
            return []

        # Get recent episodes
        recent = self.episode_store.get_recent(user_id, limit=20)
        episode_dicts = [
            ep.__dict__ if hasattr(ep, "__dict__") else ep
            for ep in recent
        ]

        # Get existing arcs
        existing_arcs = [
            arc.__dict__ if hasattr(arc, "__dict__") else arc
            for arc in self.episode_store.get_active_arcs(user_id)
        ]

        # Synthesize
        arcs = synthesize_narratives(episode_dicts, self.llm, existing_arcs)

        # Store arcs
        import uuid

        from mypalclara.core.memory.episodes import NarrativeArc

        for arc_dict in arcs:
            try:
                arc = NarrativeArc(
                    id=str(uuid.uuid4()),
                    title=arc_dict.get("title", ""),
                    summary=arc_dict.get("summary", ""),
                    status=arc_dict.get("status", "active"),
                    user_id=user_id,
                    key_episode_ids=arc_dict.get("key_episodes", []),
                    emotional_trajectory=arc_dict.get("emotional_trajectory", ""),
                )
                self.episode_store.store_arc(arc)
            except Exception as e:
                memory_logger.warning(f"Failed to store arc: {e}")

        memory_logger.info(f"Narrative synthesis: {len(arcs)} arcs for {user_id}")
        return arcs
