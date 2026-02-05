"""Memory management for Clara platform.

Provides the MemoryManager singleton that handles:
- Thread/message persistence
- mem0 integration for semantic memory
- Session summaries
- Prompt building with context
"""

from __future__ import annotations

import os
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

# Import personality at module level to ensure it loads early
from clara_core.llm.messages import AssistantMessage, Message, SystemMessage, UserMessage
from config.bot import BOT_NAME, PERSONALITY
from config.logging import get_logger

# Module loggers
logger = get_logger("mem0")
thread_logger = get_logger("thread")
memory_logger = get_logger("memory")

# Timezone for message timestamps (defaults to America/New_York)
DEFAULT_TIMEZONE = os.getenv("DEFAULT_TIMEZONE", "America/New_York")

if TYPE_CHECKING:
    from sqlalchemy.orm import Session as OrmSession

    from db.models import MemoryDynamics, Message, Session

# Configuration constants
CONTEXT_MESSAGE_COUNT = 15  # Reduced from 20 to save tokens
SUMMARY_INTERVAL = 10
MAX_SEARCH_QUERY_CHARS = 6000
MAX_KEY_MEMORIES = 15  # Key memories always included in every request
MAX_MEMORIES_PER_TYPE = 35  # Limit per type (50 total - 15 reserved for key memories)
MAX_GRAPH_RELATIONS = 20  # Limit graph relations to avoid prompt bloat

# Paths for initial profile loading
BASE_DIR = Path(__file__).parent.parent
USER_PROFILE_PATH = BASE_DIR / "inputs" / "user_profile.txt"
GENERATED_DIR = BASE_DIR / "generated"
PROFILE_LOADED_FLAG = BASE_DIR / ".profile_loaded"


def _has_generated_memories() -> bool:
    """Check if generated memory JSON files exist."""
    if not GENERATED_DIR.exists():
        return False
    memory_files = ["profile_bio.json", "interaction_style.json", "project_seed.json"]
    return any((GENERATED_DIR / f).exists() for f in memory_files)


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


def _generate_memories_from_profile() -> dict | None:
    """Generate structured memories from user_profile.txt using LLM extraction.

    DEPRECATED: This function is disabled by default (SKIP_PROFILE_LOAD=true).
    The bootstrap_memory system is legacy code from early development.
    """
    logger.warning("_generate_memories_from_profile is deprecated and will be removed")
    if not USER_PROFILE_PATH.exists():
        logger.warning("No user_profile.txt found, cannot generate memories")
        return None

    from scripts.bootstrap_memory import (
        consolidate_memories,
        extract_memories_with_llm,
        validate_memories,
        write_json_files,
    )

    logger.info("Generating memories from user_profile.txt...")
    try:
        profile_text = USER_PROFILE_PATH.read_text()
        raw_memories = extract_memories_with_llm(profile_text)
        memories = validate_memories(raw_memories)
        memories = consolidate_memories(memories)
        write_json_files(memories, GENERATED_DIR)
        return memories
    except Exception as e:
        logger.error(f"Error generating memories: {e}", exc_info=True)
        return None


def load_initial_profile(user_id: str) -> None:
    """Load initial user profile into Rook once on first run.

    DEPRECATED: This function is disabled by default (SKIP_PROFILE_LOAD=true).
    The bootstrap_memory system is legacy code from early development.

    Uses the bootstrap pipeline:
    1. If generated/*.json files exist, load from them
    2. Apply structured memories to Rook with graph-friendly grouping
    """
    from clara_core.memory import ROOK

    skip_profile = os.getenv("SKIP_PROFILE_LOAD", "true").lower() == "true"
    if skip_profile:
        logger.debug("Profile loading disabled (SKIP_PROFILE_LOAD=true)")
        return

    logger.warning("load_initial_profile is deprecated and will be removed")

    if ROOK is None:
        logger.warning("Skipping profile load - mem0 not available")
        return

    if PROFILE_LOADED_FLAG.exists():
        logger.debug("Profile already loaded (flag exists), skipping")
        return

    from scripts.bootstrap_memory import (
        apply_to_mem0,
        load_existing_memories,
    )

    if _has_generated_memories():
        logger.info("Loading from existing generated/*.json files...")
        memories = load_existing_memories(GENERATED_DIR)
    else:
        logger.info("No generated files found, extracting from profile...")
        memories = _generate_memories_from_profile()
        if not memories:
            logger.warning("Could not generate memories, skipping profile load")
            return

    logger.debug("Creating flag file to prevent duplicate loads...")
    try:
        PROFILE_LOADED_FLAG.write_text(f"loading started at {datetime.now().isoformat()}")
    except Exception as e:
        logger.error(f"Could not create flag file: {e}")

    try:
        apply_to_mem0(memories, user_id)
        PROFILE_LOADED_FLAG.write_text(f"completed at {datetime.now().isoformat()}")
        logger.info("Profile loaded successfully")
    except Exception as e:
        logger.error(f"Error applying memories to mem0: {e}", exc_info=True)


class MemoryManager:
    """Central orchestrator for Clara's memory system.

    Handles:
    - Thread and message persistence
    - mem0 semantic memory integration (with entity-scoped memory)
    - Session summaries
    - Prompt building with full context

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
        self.llm = llm_callable
        self.agent_id = agent_id
        self._on_memory_event = on_memory_event
        # FSRS integration: track memory IDs from last retrieval for promotion
        self._last_retrieved_memory_ids: dict[str, list[str]] = {}

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
        """Normalize a user ID to unified format: {platform}-{id}.

        Args:
            user_id: The raw user ID
            platform: Platform name (api, discord, slack, etc.)

        Returns:
            Normalized user ID like "discord-123456" or "api-demo-user"
        """
        # Already normalized
        known_platforms = ["api", "discord", "slack", "telegram"]
        for p in known_platforms:
            if user_id.startswith(f"{p}-"):
                return user_id

        return f"{platform}-{user_id}"

    def parse_user_id(self, unified_user_id: str) -> tuple[str, str]:
        """Parse a unified user ID into (platform, original_id).

        Args:
            unified_user_id: A normalized user ID

        Returns:
            Tuple of (platform, original_id)
        """
        if "-" in unified_user_id:
            parts = unified_user_id.split("-", 1)
            return parts[0], parts[1]
        return "unknown", unified_user_id

    # ---------- Session management ----------

    def get_or_create_session(
        self,
        db: "OrmSession",
        user_id: str,
        context_id: str = "default",
        project_id: str | None = None,
        title: str | None = None,
    ) -> "Session":
        """Get or create a session with platform-agnostic context.

        Finds an active session matching user_id + context_id + project_id,
        or creates a new one if none exists.

        Args:
            db: Database session
            user_id: Unified user ID (e.g., "discord-123", "cli-demo")
            context_id: Context identifier for session isolation
                - Discord: "channel-{channel_id}" or "dm-{user_id}"
                - CLI: "cli" or "cli-{terminal_session}"
                - Default: "default" for backward compatibility
            project_id: Optional project UUID. If None, uses or creates default project.
            title: Optional session title for UI display

        Returns:
            Session object (existing or newly created)
        """
        from db.models import Project, Session

        # Ensure we have a project
        if project_id is None:
            # Get or create default project for this user
            project = db.query(Project).filter_by(owner_id=user_id).first()
            if not project:
                import os

                project_name = os.getenv("DEFAULT_PROJECT", "Default Project")
                project = Project(owner_id=user_id, name=project_name)
                db.add(project)
                db.commit()
                db.refresh(project)
            project_id = project.id

        # Find existing active session for this user + context + project
        session = (
            db.query(Session)
            .filter(
                Session.user_id == user_id,
                Session.context_id == context_id,
                Session.project_id == project_id,
                Session.archived != "true",
            )
            .order_by(Session.last_activity_at.desc())
            .first()
        )

        if session:
            return session

        # Create new session
        session = Session(
            user_id=user_id,
            context_id=context_id,
            project_id=project_id,
            title=title,
        )
        db.add(session)
        db.commit()
        db.refresh(session)
        thread_logger.info(f"Created session {session.id} for {user_id}/{context_id}")
        return session

    # ---------- Thread/Message helpers ----------

    def get_thread(self, db: "OrmSession", thread_id: str) -> "Session | None":
        """Get a thread by ID."""
        from db.models import Session

        return db.query(Session).filter_by(id=thread_id).first()

    def get_recent_messages(self, db: "OrmSession", thread_id: str) -> list["Message"]:
        """Get recent messages from a thread."""
        from db.models import Message

        msgs = (
            db.query(Message)
            .filter_by(session_id=thread_id)
            .order_by(Message.created_at.desc())
            .limit(CONTEXT_MESSAGE_COUNT)
            .all()
        )
        return list(reversed(msgs))

    def get_message_count(self, db: "OrmSession", thread_id: str) -> int:
        """Get total message count for a thread."""
        from db.models import Message

        return db.query(Message).filter_by(session_id=thread_id).count()

    def store_message(
        self,
        db: "OrmSession",
        thread_id: str,
        user_id: str,
        role: str,
        content: str,
    ) -> "Message":
        """Store a message in a thread."""
        from db.models import Message

        msg = Message(
            session_id=thread_id,
            user_id=user_id,
            role=role,
            content=content,
        )
        db.add(msg)
        db.commit()
        db.refresh(msg)
        return msg

    # ---------- Summary management ----------

    def should_update_summary(self, db: "OrmSession", thread_id: str) -> bool:
        """Check if thread summary should be updated."""
        msg_count = self.get_message_count(db, thread_id)
        return msg_count > 0 and msg_count % SUMMARY_INTERVAL == 0

    def update_thread_summary(self, db: "OrmSession", thread: "Session") -> str:
        """Generate/update summary for a thread."""
        from db.models import Message

        all_msgs = db.query(Message).filter_by(session_id=thread.id).order_by(Message.created_at.asc()).all()

        if not all_msgs:
            return ""

        conversation = "\n".join(f"{m.role.upper()}: {m.content[:500]}" for m in all_msgs[-30:])

        summary_prompt = [
            SystemMessage(
                content="Summarize this conversation in 2-3 sentences. "
                "Focus on key topics, decisions, and important context.",
            ),
            UserMessage(content=conversation),
        ]

        summary = self.llm(summary_prompt)
        thread.session_summary = summary
        db.commit()
        thread_logger.info(f"Updated summary for thread {thread.id}")
        return summary

    # ---------- mem0 integration ----------

    def _get_cache(self):
        """Get Redis cache instance (lazy-loaded)."""
        try:
            from clara_core.memory.cache.redis_cache import RedisCache

            return RedisCache.get_instance()
        except Exception:
            return None

    def _invalidate_memory_cache(self, user_id: str) -> None:
        """Invalidate memory cache for a user after memories change.

        Invalidates search results cache but NOT embeddings (those rarely change).

        Args:
            user_id: User whose cache to invalidate
        """
        cache = self._get_cache()
        if cache and cache.available:
            count = cache.invalidate_search_cache(user_id)
            if count:
                memory_logger.debug(f"Invalidated {count} cached searches for {user_id}")

    def fetch_mem0_context(
        self,
        user_id: str,
        project_id: str,
        user_message: str,
        participants: list[dict] | None = None,
        is_dm: bool = False,
    ) -> tuple[list[str], list[str], list[dict]]:
        """Fetch relevant memories from mem0 using parallel fetches.

        Uses entity-scoped memory with user_id + agent_id for proper isolation.
        Performance optimized with:
        - Parallel fetches via ThreadPoolExecutor
        - Redis caching for key memories and search results
        - Batched FSRS lookups

        Memory retrieval:
        1. Parallel fetch: key memories, user search, project search
        2. Fetch graph relations (entity relationships)
        3. Re-rank with batched FSRS
        4. Combine with key memories first, then relevant (deduplicated)

        Args:
            user_id: The user making the request
            project_id: Project context
            user_message: The message to search for relevant memories
            participants: List of {"id": str, "name": str} for conversation members
            is_dm: Whether this is a DM conversation (changes retrieval priority)

        Returns:
            Tuple of (user_memories, project_memories, graph_relations)
            graph_relations is a list of dicts with keys: source, relationship, destination
        """
        from clara_core.memory import ROOK

        if ROOK is None:
            return [], [], []

        # Truncate search query if too long
        search_query = user_message
        if len(search_query) > MAX_SEARCH_QUERY_CHARS:
            search_query = search_query[-MAX_SEARCH_QUERY_CHARS:]
            logger.debug(f"Truncated search query to {MAX_SEARCH_QUERY_CHARS} chars")

        # Get cache for potential cache hits
        cache = self._get_cache()

        # Define fetch functions for parallel execution
        def fetch_key_memories():
            """Fetch key memories (with caching)."""
            # Try cache first
            if cache and cache.available:
                cached = cache.get_key_memories(user_id, self.agent_id)
                if cached is not None:
                    logger.debug("Key memories cache hit")
                    return {"results": cached, "_cached": True}

            result = ROOK.get_all(
                user_id=user_id,
                agent_id=self.agent_id,
                filters={"is_key": "true"},
                limit=MAX_KEY_MEMORIES,
            )

            # Cache the results
            if cache and cache.available and result.get("results"):
                cache.set_key_memories(user_id, self.agent_id, result["results"])

            return result

        def fetch_user_memories():
            """Fetch user memories via semantic search (with caching)."""
            # Try cache first
            if cache and cache.available:
                cached = cache.get_search_results(user_id, search_query, "user")
                if cached is not None:
                    logger.debug("User search cache hit")
                    return {"results": cached, "_cached": True}

            result = ROOK.search(
                search_query,
                user_id=user_id,
                agent_id=self.agent_id,
            )

            # Cache the results
            if cache and cache.available and result.get("results"):
                cache.set_search_results(user_id, search_query, result["results"], "user")

            return result

        def fetch_project_memories():
            """Fetch project memories via semantic search (with caching)."""
            filters = {"project_id": project_id}

            # Try cache first
            if cache and cache.available:
                cached = cache.get_search_results(user_id, search_query, "project", filters)
                if cached is not None:
                    logger.debug("Project search cache hit")
                    return {"results": cached, "_cached": True}

            result = ROOK.search(
                search_query,
                user_id=user_id,
                agent_id=self.agent_id,
                filters=filters,
            )

            # Cache the results
            if cache and cache.available and result.get("results"):
                cache.set_search_results(user_id, search_query, result["results"], "project", filters)

            return result

        # Execute parallel fetches
        key_res = {"results": []}
        user_res = {"results": []}
        proj_res = {"results": []}

        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = {
                executor.submit(fetch_key_memories): "key",
                executor.submit(fetch_user_memories): "user",
                executor.submit(fetch_project_memories): "project",
            }

            for future in as_completed(futures):
                fetch_type = futures[future]
                try:
                    result = future.result()
                    if fetch_type == "key":
                        key_res = result
                    elif fetch_type == "user":
                        user_res = result
                    elif fetch_type == "project":
                        proj_res = result
                except Exception as e:
                    logger.error(f"Error in {fetch_type} memory fetch: {e}", exc_info=True)

        # Build key memories list
        key_mems: list[str] = []
        for r in key_res.get("results", []):
            key_mems.append(f"[KEY] {r['memory']}")

        # Collect graph relations from search results
        graph_relations: list[dict] = []
        if user_res.get("relations"):
            graph_relations.extend(user_res["relations"])
        if proj_res.get("relations"):
            existing = {(r.get("source"), r.get("relationship"), r.get("destination")) for r in graph_relations}
            for rel in proj_res["relations"]:
                key = (rel.get("source"), rel.get("relationship"), rel.get("destination"))
                if key not in existing:
                    graph_relations.append(rel)
                    existing.add(key)

        # Combine all results for batched FSRS ranking
        all_user_results = user_res.get("results", [])
        all_proj_results = proj_res.get("results", [])

        # Re-rank results using batched FSRS retrievability weighting
        user_results = self._rank_results_with_fsrs_batch(all_user_results, user_id)
        proj_results = self._rank_results_with_fsrs_batch(all_proj_results, user_id)

        # Track memory IDs for later promotion
        retrieved_ids: list[str] = []
        for r in user_results:
            if r.get("id"):
                retrieved_ids.append(r["id"])
        for r in proj_results:
            if r.get("id") and r["id"] not in retrieved_ids:
                retrieved_ids.append(r["id"])
        self._last_retrieved_memory_ids[user_id] = retrieved_ids

        # Build user memories: key first, then relevant (deduplicated)
        user_mems = list(key_mems)
        key_texts = {m.replace("[KEY] ", "") for m in key_mems}

        for r in user_results:
            mem = r["memory"]
            if mem not in key_texts:
                user_mems.append(mem)

        proj_mems = [r["memory"] for r in proj_results]

        # Fetch participant memories in parallel if we have participants
        if participants:
            participant_futures = {}
            with ThreadPoolExecutor(max_workers=min(len(participants), 3)) as executor:
                for p in participants:
                    p_id = p.get("id")
                    p_name = p.get("name", p_id)
                    if not p_id or p_id == user_id:
                        continue

                    def fetch_participant(name=p_name, query=search_query):
                        return ROOK.search(
                            f"{name} {query[:500]}",
                            user_id=user_id,
                            agent_id=self.agent_id,
                        )

                    participant_futures[executor.submit(fetch_participant)] = p_name

                for future in as_completed(participant_futures):
                    p_name = participant_futures[future]
                    try:
                        p_search = future.result()
                        p_results = self._rank_results_with_fsrs_batch(p_search.get("results", []), user_id)
                        for r in p_results:
                            mem = r["memory"]
                            if mem not in key_texts and mem not in user_mems:
                                labeled_mem = f"[About {p_name}]: {mem}"
                                if labeled_mem not in user_mems:
                                    user_mems.append(labeled_mem)
                                    if r.get("id") and r["id"] not in retrieved_ids:
                                        retrieved_ids.append(r["id"])
                    except Exception as e:
                        logger.warning(f"Error searching participant {p_name}: {e}")

        # Update tracked IDs after participant search
        self._last_retrieved_memory_ids[user_id] = retrieved_ids

        # Extract contact-related memories with source info
        for r in user_results:
            metadata = r.get("metadata", {})
            if metadata.get("contact_id"):
                contact_name = metadata.get("contact_name", metadata.get("contact_id"))
                mem_text = f"[About {contact_name}]: {r['memory']}"
                if mem_text not in user_mems:
                    user_mems.append(mem_text)

        # Limit non-key memories to reduce token usage
        num_key = len(key_mems)
        if len(user_mems) > num_key + MAX_MEMORIES_PER_TYPE:
            user_mems = user_mems[: num_key + MAX_MEMORIES_PER_TYPE]
        if len(proj_mems) > MAX_MEMORIES_PER_TYPE:
            proj_mems = proj_mems[:MAX_MEMORIES_PER_TYPE]
        if len(graph_relations) > MAX_GRAPH_RELATIONS:
            graph_relations = graph_relations[:MAX_GRAPH_RELATIONS]

        if user_mems or proj_mems or graph_relations:
            logger.info(
                f"Found {len(key_mems)} key, "
                f"{len(user_mems) - len(key_mems)} user, "
                f"{len(proj_mems)} project memories, "
                f"{len(graph_relations)} graph relations"
            )
            self._send_memory_embed(
                user_id=user_id,
                key_count=len(key_mems),
                user_count=len(user_mems) - len(key_mems),
                proj_count=len(proj_mems),
                graph_count=len(graph_relations),
                sample_memories=user_mems[:3] if user_mems else [],
            )
        return user_mems, proj_mems, graph_relations

    def _send_memory_embed(
        self,
        user_id: str,
        key_count: int,
        user_count: int,
        proj_count: int,
        sample_memories: list[str],
        graph_count: int = 0,
    ) -> None:
        """Notify about memory retrieval (if callback registered)."""
        if not self._on_memory_event:
            return

        total = key_count + user_count + proj_count + graph_count
        if total == 0:
            return

        # Format samples for notification
        samples = []
        for mem in sample_memories[:3]:
            clean_mem = mem.replace("[KEY] ", "")
            if len(clean_mem) > 60:
                clean_mem = clean_mem[:57] + "..."
            samples.append(clean_mem)

        self._on_memory_event(
            "memory_retrieved",
            {
                "user_id": user_id,
                "key_count": key_count,
                "user_count": user_count,
                "proj_count": proj_count,
                "graph_count": graph_count,
                "total": total,
                "samples": samples,
            },
        )

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
        """Send conversation slice to mem0 for memory extraction.

        Args:
            user_id: The user ID for memory storage
            project_id: Project context
            recent_msgs: Recent message history
            user_message: Current user message
            assistant_reply: Clara's response
            participants: List of {"id": str, "name": str} for people mentioned
            is_dm: Whether this is a DM conversation (stores as "personal" vs "project")
        """
        from clara_core.memory import ROOK

        if ROOK is None:
            return

        # Build context with participant names for better extraction
        context_prefix = ""
        if participants:
            names = [p.get("name", p.get("id", "Unknown")) for p in participants]
            context_prefix = f"[Participants: {', '.join(names)}]\n"

        history_slice = [
            UserMessage(content=m.content) if m.role == "user" else AssistantMessage(content=m.content)
            for m in recent_msgs[-4:]
        ] + [
            UserMessage(content=context_prefix + user_message),
            AssistantMessage(content=assistant_reply),
        ]

        # Store with participant metadata for cross-user search
        # Tag source type: "personal" for DMs, "project" for server channels
        source_type = "personal" if is_dm else "project"
        metadata = {
            "project_id": project_id,
            "source_type": source_type,
        }
        if participants:
            metadata["participant_ids"] = [p.get("id") for p in participants if p.get("id")]
            metadata["participant_names"] = [p.get("name") for p in participants if p.get("name")]

        try:
            result = ROOK.add(
                history_slice,
                user_id=user_id,
                agent_id=self.agent_id,
                metadata=metadata,
            )
            # Check for errors in result
            if isinstance(result, dict):
                if result.get("error"):
                    logger.error(f"Error adding memories: {result.get('error')}")
                else:
                    mem_results = result.get("results", [])
                    mem_count = len(mem_results)
                    # Handle graph relations from add result
                    relations = result.get("relations", {})
                    added_entities = relations.get("added_entities", []) if isinstance(relations, dict) else []
                    deleted_entities = relations.get("deleted_entities", []) if isinstance(relations, dict) else []

                    if mem_count or added_entities:
                        logger.info(f"Added {mem_count} memories, " f"{len(added_entities)} graph entities")
                        if deleted_entities:
                            logger.debug(f"Deleted {len(deleted_entities)} outdated graph entities")

                        # Invalidate search cache (not embeddings) since memories changed
                        self._invalidate_memory_cache(user_id)

                        # Create MemoryDynamics records for FSRS tracking
                        self._create_memory_dynamics_records(
                            user_id=user_id,
                            memory_results=mem_results,
                        )

                        # Send Discord embed for memory extraction
                        self._send_memory_added_embed(
                            user_id=user_id,
                            count=mem_count,
                            source_type=source_type,
                            results=mem_results,
                            graph_added=len(added_entities),
                        )
                    else:
                        logger.debug(f"Add result: {result}")
            else:
                logger.debug(f"Added memories: {result}")
        except Exception as e:
            logger.error(f"Error adding memories: {e}", exc_info=True)

    def add_to_memory(
        self,
        user_id: str,
        user_message: str,
        assistant_reply: str,
        is_dm: bool = False,
    ) -> None:
        """Simplified method to add a conversation exchange to memory.

        This is a convenience wrapper around add_to_mem0 for use by the
        gateway processor where we don't have access to recent messages
        or project context.

        Args:
            user_id: The user ID for memory storage
            user_message: The user's message
            assistant_reply: Clara's response
            is_dm: Whether this is a DM conversation
        """
        self.add_to_mem0(
            user_id=user_id,
            project_id="default",
            recent_msgs=[],
            user_message=user_message,
            assistant_reply=assistant_reply,
            participants=None,
            is_dm=is_dm,
        )

    def _send_memory_added_embed(
        self,
        user_id: str,
        count: int,
        source_type: str,
        results: list[dict],
        graph_added: int = 0,
    ) -> None:
        """Notify about memory extraction (if callback registered)."""
        if not self._on_memory_event or (count == 0 and graph_added == 0):
            return

        # Format samples for notification
        samples = []
        for r in results[:3]:
            mem = r.get("memory", "")
            if len(mem) > 60:
                mem = mem[:57] + "..."
            samples.append(mem)

        self._on_memory_event(
            "memory_extracted",
            {
                "user_id": user_id,
                "count": count,
                "source_type": source_type,
                "samples": samples,
                "graph_added": graph_added,
            },
        )

    def _create_memory_dynamics_records(
        self,
        user_id: str,
        memory_results: list[dict],
    ) -> None:
        """Create MemoryDynamics records for new memories.

        Initializes FSRS tracking data for newly added memories.

        Args:
            user_id: User who owns the memories
            memory_results: Results from ROOK.add() containing memory IDs
        """
        from datetime import UTC, datetime

        from db import SessionLocal
        from db.models import MemoryDynamics

        if not memory_results:
            return

        db = SessionLocal()
        try:
            now = datetime.now(UTC).replace(tzinfo=None)
            created_count = 0

            for r in memory_results:
                memory_id = r.get("id")
                if not memory_id:
                    continue

                # Check if dynamics record already exists
                existing = (
                    db.query(MemoryDynamics)
                    .filter_by(
                        memory_id=memory_id,
                        user_id=user_id,
                    )
                    .first()
                )

                if not existing:
                    # Create new dynamics record with default FSRS values
                    dynamics = MemoryDynamics(
                        memory_id=memory_id,
                        user_id=user_id,
                        stability=1.0,  # Default initial stability
                        difficulty=5.0,  # Middle difficulty
                        retrieval_strength=1.0,
                        storage_strength=0.5,
                        is_key=False,
                        importance_weight=1.0,
                        last_accessed_at=now,
                        access_count=1,  # Created = first access
                        created_at=now,
                        updated_at=now,
                    )
                    db.add(dynamics)
                    created_count += 1

            if created_count > 0:
                db.commit()
                memory_logger.debug(f"Created {created_count} MemoryDynamics records for user {user_id}")

        except Exception as e:
            db.rollback()
            memory_logger.warning(f"Error creating MemoryDynamics records: {e}")
        finally:
            db.close()

    # ---------- emotional context ----------

    def fetch_emotional_context(
        self,
        user_id: str,
        limit: int = 3,
        max_age_days: int = 7,
    ) -> list[dict]:
        """
        Fetch recent emotional context memories for session warmth.

        Retrieves emotional summaries from recent conversations to help
        calibrate tone at session start.

        Args:
            user_id: The user to fetch emotional context for
            limit: Maximum number of emotional context memories to return
            max_age_days: Only return context from the last N days

        Returns:
            List of emotional context dicts with keys:
            - memory: The formatted emotional summary text
            - timestamp: When the conversation ended
            - arc: Emotional arc (stable, improving, declining, volatile)
            - energy: Energy level (stressed, focused, casual, etc.)
            - channel_name: Where the conversation happened
            - is_dm: Whether it was a DM
        """
        from datetime import timedelta

        from clara_core.memory import ROOK

        if ROOK is None:
            return []

        try:
            # Search for emotional_context memories
            results = ROOK.get_all(
                user_id=user_id,
                agent_id=self.agent_id,
                limit=limit * 2,  # Fetch extra to filter by age
            )

            emotional_contexts = []
            cutoff = datetime.now(UTC) - timedelta(days=max_age_days)

            for r in results.get("results", []):
                metadata = r.get("metadata", {})

                # Only include emotional_context type memories
                if metadata.get("memory_type") != "emotional_context":
                    continue

                # Parse and filter by timestamp
                timestamp_str = metadata.get("timestamp")
                if timestamp_str:
                    try:
                        timestamp = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
                        if timestamp < cutoff:
                            continue
                    except (ValueError, TypeError):
                        timestamp = None
                else:
                    timestamp = None

                emotional_contexts.append(
                    {
                        "memory": r.get("memory", ""),
                        "timestamp": timestamp_str,
                        "arc": metadata.get("emotional_arc", "stable"),
                        "energy": metadata.get("energy_level", "neutral"),
                        "channel_name": metadata.get("channel_name", "unknown"),
                        "is_dm": metadata.get("is_dm", False),
                        "ending_sentiment": metadata.get("ending_sentiment", 0.0),
                    }
                )

            # Sort by timestamp descending (most recent first)
            emotional_contexts.sort(
                key=lambda x: x.get("timestamp") or "",
                reverse=True,
            )

            return emotional_contexts[:limit]

        except Exception as e:
            logger.error(f"Error fetching emotional context: {e}", exc_info=True)
            return []

    # ---------- prompt building ----------

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
    ) -> list[Message]:
        """Build the full prompt for the LLM.

        Args:
            user_mems: List of user memories
            proj_mems: List of project memories
            thread_summary: Optional thread summary
            recent_msgs: Recent messages in the conversation
            user_message: Current user message
            emotional_context: Optional emotional context from recent sessions
            recurring_topics: Optional recurring topic patterns from fetch_topic_recurrence
            graph_relations: Optional list of entity relationships from graph memory

        Returns:
            List of typed Messages ready for LLM
        """
        system_base = PERSONALITY

        # Build context sections
        context_parts = []

        if user_mems:
            user_block = "\n".join(f"- {m}" for m in user_mems)
            context_parts.append(f"USER MEMORIES:\n{user_block}")

        if proj_mems:
            proj_block = "\n".join(f"- {m}" for m in proj_mems)
            context_parts.append(f"PROJECT MEMORIES:\n{proj_block}")

        # Add graph relations (entity relationships)
        if graph_relations:
            graph_block = self._format_graph_relations(graph_relations)
            if graph_block:
                context_parts.append(f"KNOWN RELATIONSHIPS:\n{graph_block}")

        # Add emotional context from recent sessions (for tone calibration)
        if emotional_context:
            emotional_block = self._format_emotional_context(emotional_context)
            if emotional_block:
                context_parts.append(f"RECENT EMOTIONAL CONTEXT:\n{emotional_block}")

        # Add recurring topic patterns (for awareness of what keeps coming up)
        if recurring_topics:
            topic_block = self._format_topic_recurrence(recurring_topics)
            if topic_block:
                context_parts.append(f"RECURRING TOPICS:\n{topic_block}")

        if thread_summary:
            context_parts.append(f"THREAD SUMMARY:\n{thread_summary}")

        messages: list[Message] = [
            SystemMessage(content=system_base),
        ]

        if context_parts:
            messages.append(SystemMessage(content="\n\n".join(context_parts)))

        # Add recent messages (only user messages get timestamps to avoid Clara mimicking the format)
        for m in recent_msgs:
            if m.role == "user":
                timestamp = _format_message_timestamp(getattr(m, "created_at", None))
                if timestamp:
                    content = f"[{timestamp}] {m.content}"
                else:
                    content = m.content
                messages.append(UserMessage(content=content))
            else:
                messages.append(AssistantMessage(content=m.content))

        messages.append(UserMessage(content=user_message))

        # Log prompt composition summary
        components = []
        components.append(f"personality={len(system_base)} chars")
        if user_mems:
            components.append(f"user_mems={len(user_mems)}")
        if proj_mems:
            components.append(f"proj_mems={len(proj_mems)}")
        if graph_relations:
            components.append(f"graph={len(graph_relations)}")
        if emotional_context:
            components.append(f"emotional={len(emotional_context)}")
        if recurring_topics:
            components.append(f"topics={len(recurring_topics)}")
        if thread_summary:
            components.append("summary")
        if recent_msgs:
            components.append(f"history={len(recent_msgs)}")
        logger.info(f"[prompt] Built with: {', '.join(components)}")

        return messages

    def _format_emotional_context(self, emotional_context: list[dict]) -> str:
        """
        Format emotional context for inclusion in the system prompt.

        Only includes non-neutral contexts to avoid noise. Includes channel
        hints so Clara understands the source (work channel vs personal DM).

        Args:
            emotional_context: List of emotional context dicts from fetch_emotional_context

        Returns:
            Formatted string for the prompt, or empty string if nothing meaningful
        """
        if not emotional_context:
            return ""

        lines = []
        for ctx in emotional_context:
            memory = ctx.get("memory", "")
            arc = ctx.get("arc", "stable")
            energy = ctx.get("energy", "neutral")
            channel_name = ctx.get("channel_name", "")
            is_dm = ctx.get("is_dm", False)
            timestamp_str = ctx.get("timestamp", "")

            # Skip stable/neutral contexts - not worth mentioning
            if arc == "stable" and energy in ("neutral", "casual"):
                continue

            # Format channel hint
            if is_dm:
                channel_hint = "DM"
            elif channel_name:
                channel_hint = channel_name if channel_name.startswith("#") else f"#{channel_name}"
            else:
                channel_hint = "unknown"

            # Format time hint
            time_hint = self._format_relative_time(timestamp_str)

            # Build the line with channel and time hints
            if time_hint:
                lines.append(f"- [{channel_hint}, {time_hint}] {memory}")
            else:
                lines.append(f"- [{channel_hint}] {memory}")

        return "\n".join(lines) if lines else ""

    def _format_relative_time(self, timestamp_str: str | None) -> str:
        """Format a timestamp as relative time (e.g., '2h ago', 'yesterday')."""
        if not timestamp_str:
            return ""

        try:
            timestamp = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
            now = datetime.now(UTC)
            delta = now - timestamp

            if delta.days > 1:
                return f"{delta.days} days ago"
            elif delta.days == 1:
                return "yesterday"
            elif delta.seconds >= 3600:
                hours = delta.seconds // 3600
                return f"{hours}h ago"
            elif delta.seconds >= 60:
                mins = delta.seconds // 60
                return f"{mins}m ago"
            else:
                return "just now"
        except (ValueError, TypeError):
            return ""

    def fetch_topic_recurrence(
        self,
        user_id: str,
        lookback_days: int = 14,
        min_mentions: int = 2,
    ) -> list[dict]:
        """
        Fetch recurring topic patterns for a user.

        Wrapper around topic_recurrence.fetch_topic_recurrence that uses
        the MemoryManager's agent_id.

        Args:
            user_id: The user to fetch topic recurrence for
            lookback_days: How many days to look back
            min_mentions: Minimum mentions to consider a topic recurring

        Returns:
            List of recurring topic patterns with keys:
            - topic: The topic name
            - topic_type: "entity" or "theme"
            - mention_count: Number of times mentioned
            - first_mentioned: Relative time string
            - last_mentioned: Relative time string
            - sentiment_trend: "stable", "improving", or "declining"
            - avg_emotional_weight: "light", "moderate", or "heavy"
            - pattern_note: Natural language description
            - channels: List of channel names where mentioned
        """
        from clara_core.topic_recurrence import fetch_topic_recurrence

        return fetch_topic_recurrence(
            user_id=user_id,
            lookback_days=lookback_days,
            min_mentions=min_mentions,
            agent_id=self.agent_id,
        )

    def _format_topic_recurrence(self, recurring_topics: list[dict]) -> str:
        """
        Format recurring topics for inclusion in the system prompt.

        Only includes significant patterns - topics that have been mentioned
        multiple times with non-trivial emotional weight or sentiment changes.

        Args:
            recurring_topics: List of topic patterns from fetch_topic_recurrence

        Returns:
            Formatted string for the prompt, or empty string if nothing meaningful
        """
        if not recurring_topics:
            return ""

        lines = []
        for topic in recurring_topics[:3]:  # Max 3 topics
            topic_name = topic.get("topic", "")
            pattern_note = topic.get("pattern_note", "")
            mention_count = topic.get("mention_count", 0)
            sentiment_trend = topic.get("sentiment_trend", "stable")
            avg_weight = topic.get("avg_emotional_weight", "light")

            # Skip topics that aren't significant enough
            if mention_count < 2:
                continue
            if sentiment_trend == "stable" and avg_weight == "light":
                continue

            # Build the line
            if pattern_note:
                lines.append(f"- {topic_name}: {pattern_note}")
            else:
                lines.append(f"- {topic_name}: mentioned {mention_count} times")

        return "\n".join(lines) if lines else ""

    def _format_graph_relations(self, graph_relations: list[dict]) -> str:
        """
        Format graph relations for inclusion in the system prompt.

        Converts entity relationships from the graph store into a readable format.

        Args:
            graph_relations: List of relation dicts with keys: source, relationship, destination
                (or source, relationship, target for some providers)

        Returns:
            Formatted string for the prompt, or empty string if nothing meaningful
        """
        if not graph_relations:
            return ""

        lines = []
        seen = set()  # Deduplicate

        for rel in graph_relations:
            source = rel.get("source", "")
            relationship = rel.get("relationship", "")
            # Handle both "destination" and "target" keys
            destination = rel.get("destination") or rel.get("target", "")

            if not source or not relationship or not destination:
                continue

            # Clean up relationship name (convert snake_case to readable)
            readable_rel = relationship.replace("_", " ").lower()

            # Create dedup key
            key = (source.lower(), readable_rel, destination.lower())
            if key in seen:
                continue
            seen.add(key)

            # Format: "josh → works at → anthropic"
            lines.append(f"- {source} → {readable_rel} → {destination}")

        return "\n".join(lines) if lines else ""

    # ---------- FSRS-6 Memory Dynamics ----------

    def get_memory_dynamics(
        self,
        memory_id: str,
        user_id: str,
    ) -> "MemoryDynamics | None":
        """Get FSRS dynamics for a memory.

        Args:
            memory_id: The mem0 memory ID
            user_id: User who owns the memory

        Returns:
            MemoryDynamics object or None if not found
        """
        from db import SessionLocal
        from db.models import MemoryDynamics

        db = SessionLocal()
        try:
            return (
                db.query(MemoryDynamics)
                .filter_by(
                    memory_id=memory_id,
                    user_id=user_id,
                )
                .first()
            )
        finally:
            db.close()

    def ensure_memory_dynamics(
        self,
        memory_id: str,
        user_id: str,
        is_key: bool = False,
    ) -> "MemoryDynamics":
        """Ensure FSRS dynamics exist for a memory, creating if needed.

        Args:
            memory_id: The mem0 memory ID
            user_id: User who owns the memory
            is_key: Whether this is a key memory

        Returns:
            MemoryDynamics object (existing or newly created)
        """
        from db import SessionLocal
        from db.models import MemoryDynamics

        db = SessionLocal()
        try:
            dynamics = (
                db.query(MemoryDynamics)
                .filter_by(
                    memory_id=memory_id,
                    user_id=user_id,
                )
                .first()
            )

            if not dynamics:
                dynamics = MemoryDynamics(
                    memory_id=memory_id,
                    user_id=user_id,
                    is_key=is_key,
                )
                db.add(dynamics)
                db.commit()
                db.refresh(dynamics)

            return dynamics
        finally:
            db.close()

    def promote_memory(
        self,
        memory_id: str,
        user_id: str,
        grade: int = 3,  # Grade.GOOD
        signal_type: str = "used_in_response",
    ) -> None:
        """Mark memory as successfully recalled, updating FSRS state.

        This should be called when a memory is used in a response
        to strengthen it according to the spaced repetition algorithm.

        Args:
            memory_id: The mem0 memory ID
            user_id: User who owns the memory
            grade: FSRS grade (1=Again, 2=Hard, 3=Good, 4=Easy)
            signal_type: What triggered this promotion
        """
        from datetime import UTC, datetime

        from clara_core.fsrs import (
            FsrsParams,
            Grade,
            MemoryState,
            review,
        )
        from db import SessionLocal
        from db.models import MemoryAccessLog, MemoryDynamics

        db = SessionLocal()
        try:
            # Get or create dynamics
            dynamics = (
                db.query(MemoryDynamics)
                .filter_by(
                    memory_id=memory_id,
                    user_id=user_id,
                )
                .first()
            )

            if not dynamics:
                dynamics = MemoryDynamics(
                    memory_id=memory_id,
                    user_id=user_id,
                )
                db.add(dynamics)
                db.commit()
                db.refresh(dynamics)

            # Build current state
            current_state = MemoryState(
                stability=dynamics.stability,
                difficulty=dynamics.difficulty,
                retrieval_strength=dynamics.retrieval_strength,
                storage_strength=dynamics.storage_strength,
                last_review=dynamics.last_accessed_at,
                review_count=dynamics.access_count,
            )

            # Calculate current retrievability before review
            now = datetime.now(UTC).replace(tzinfo=None)
            from clara_core.fsrs import retrievability

            if dynamics.last_accessed_at:
                days_elapsed = (now - dynamics.last_accessed_at).total_seconds() / 86400.0
            else:
                days_elapsed = 0.0

            current_r = retrievability(days_elapsed, dynamics.stability)

            # Apply review
            grade_enum = Grade(grade) if isinstance(grade, int) else grade
            result = review(current_state, grade_enum, now, FsrsParams())

            # Update dynamics
            dynamics.stability = result.new_state.stability
            dynamics.difficulty = result.new_state.difficulty
            dynamics.retrieval_strength = result.new_state.retrieval_strength
            dynamics.storage_strength = result.new_state.storage_strength
            dynamics.last_accessed_at = now
            dynamics.access_count += 1

            # Log the access
            access_log = MemoryAccessLog(
                memory_id=memory_id,
                user_id=user_id,
                grade=grade,
                signal_type=signal_type,
                retrievability_at_access=current_r,
            )
            db.add(access_log)
            db.commit()

            memory_logger.debug(
                f"Promoted memory {memory_id}: S={dynamics.stability:.2f}, "
                f"D={dynamics.difficulty:.2f}, R={current_r:.2f}"
            )

        except Exception as e:
            memory_logger.error(f"Error promoting memory {memory_id}: {e}")
            db.rollback()
        finally:
            db.close()

    def demote_memory(
        self,
        memory_id: str,
        user_id: str,
        reason: str = "user_correction",
    ) -> None:
        """Mark memory as incorrect/outdated, decreasing stability.

        This should be called when a memory is found to be wrong
        or when the user corrects information.

        Args:
            memory_id: The mem0 memory ID
            user_id: User who owns the memory
            reason: Why the memory is being demoted
        """
        from clara_core.memory import ROOK

        # Promote with grade=AGAIN (1) to decrease stability
        self.promote_memory(
            memory_id=memory_id,
            user_id=user_id,
            grade=1,  # Grade.AGAIN
            signal_type=reason,
        )

        # Also send negative feedback to mem0
        if ROOK is not None:
            try:
                ROOK.feedback(memory_id, feedback="NEGATIVE")
            except Exception as e:
                memory_logger.debug(f"Could not send mem0 feedback: {e}")

    def calculate_memory_score(
        self,
        memory_id: str,
        user_id: str,
        semantic_score: float,
    ) -> float:
        """Calculate composite score for memory ranking.

        Combines semantic similarity with FSRS retrievability for
        more intelligent memory retrieval.

        Args:
            memory_id: The mem0 memory ID
            user_id: User who owns the memory
            semantic_score: Semantic similarity score from mem0

        Returns:
            Composite score for ranking
        """
        from datetime import UTC, datetime

        from clara_core.fsrs import calculate_memory_score, retrievability
        from db import SessionLocal
        from db.models import MemoryDynamics

        db = SessionLocal()
        try:
            dynamics = (
                db.query(MemoryDynamics)
                .filter_by(
                    memory_id=memory_id,
                    user_id=user_id,
                )
                .first()
            )

            if not dynamics:
                # No FSRS data, just use semantic score
                return semantic_score

            # Calculate current retrievability
            now = datetime.now(UTC).replace(tzinfo=None)
            if dynamics.last_accessed_at:
                days_elapsed = (now - dynamics.last_accessed_at).total_seconds() / 86400.0
            else:
                days_elapsed = 0.0

            current_r = retrievability(days_elapsed, dynamics.stability)

            # Combine semantic and FSRS scores
            # Semantic similarity is primary (0.6), FSRS modulates (0.4)
            importance = dynamics.importance_weight if dynamics.importance_weight else 1.0
            fsrs_score = calculate_memory_score(
                current_r,
                dynamics.storage_strength,
                importance,
            )

            return 0.6 * semantic_score + 0.4 * fsrs_score

        finally:
            db.close()

    def get_last_retrieved_memory_ids(self, user_id: str) -> list[str]:
        """Get memory IDs from the last retrieval for a user.

        Use this to promote memories that were used in a response.

        Args:
            user_id: User ID to get memory IDs for

        Returns:
            List of memory IDs from the last fetch_mem0_context call
        """
        return self._last_retrieved_memory_ids.get(user_id, [])

    def _rank_results_with_fsrs(
        self,
        results: list[dict],
        user_id: str,
    ) -> list[dict]:
        """Re-rank search results using FSRS retrievability.

        Calculates composite score (semantic * FSRS) and sorts by it.

        Args:
            results: List of search results from mem0
            user_id: User ID for FSRS lookup

        Returns:
            Results re-ranked by composite score
        """
        if not results:
            return results

        # Calculate composite scores for each result
        scored_results = []
        for r in results:
            memory_id = r.get("id")
            semantic_score = r.get("score", 0.5)  # Default to 0.5 if no score

            if memory_id:
                composite_score = self.calculate_memory_score(memory_id, user_id, semantic_score)
            else:
                composite_score = semantic_score

            scored_results.append(
                {
                    **r,
                    "_composite_score": composite_score,
                }
            )

        # Sort by composite score (descending)
        scored_results.sort(key=lambda x: x.get("_composite_score", 0), reverse=True)

        return scored_results

    def _rank_results_with_fsrs_batch(
        self,
        results: list[dict],
        user_id: str,
    ) -> list[dict]:
        """Re-rank search results using batched FSRS lookups.

        Performance optimized: single DB query instead of N queries.
        Calculates composite score (semantic * FSRS) and sorts by it.

        Args:
            results: List of search results from mem0
            user_id: User ID for FSRS lookup

        Returns:
            Results re-ranked by composite score
        """
        if not results:
            return results

        from datetime import UTC, datetime

        from clara_core.fsrs import calculate_memory_score, retrievability
        from db import SessionLocal
        from db.models import MemoryDynamics

        # Collect all memory IDs that have them
        memory_ids = [r.get("id") for r in results if r.get("id")]

        if not memory_ids:
            # No memory IDs, just return results sorted by semantic score
            return sorted(results, key=lambda x: x.get("score", 0.5), reverse=True)

        # Single batch query for all memory dynamics
        db = SessionLocal()
        try:
            dynamics_records = (
                db.query(MemoryDynamics)
                .filter(
                    MemoryDynamics.memory_id.in_(memory_ids),
                    MemoryDynamics.user_id == user_id,
                )
                .all()
            )
            dynamics_map = {d.memory_id: d for d in dynamics_records}
        finally:
            db.close()

        # Calculate composite scores using the batched data
        now = datetime.now(UTC).replace(tzinfo=None)
        scored_results = []

        for r in results:
            memory_id = r.get("id")
            semantic_score = r.get("score", 0.5)

            if memory_id and memory_id in dynamics_map:
                dynamics = dynamics_map[memory_id]

                # Calculate current retrievability
                if dynamics.last_accessed_at:
                    days_elapsed = (now - dynamics.last_accessed_at).total_seconds() / 86400.0
                else:
                    days_elapsed = 0.0

                current_r = retrievability(days_elapsed, dynamics.stability)

                # Combine semantic and FSRS scores
                importance = dynamics.importance_weight if dynamics.importance_weight else 1.0
                fsrs_score = calculate_memory_score(
                    current_r,
                    dynamics.storage_strength,
                    importance,
                )

                composite_score = 0.6 * semantic_score + 0.4 * fsrs_score
            else:
                # No FSRS data, just use semantic score
                composite_score = semantic_score

            scored_results.append(
                {
                    **r,
                    "_composite_score": composite_score,
                }
            )

        # Sort by composite score (descending)
        scored_results.sort(key=lambda x: x.get("_composite_score", 0), reverse=True)

        return scored_results

    # ---------- Prediction Error Gating ----------

    def smart_ingest(
        self,
        content: str,
        user_id: str,
        metadata: dict | None = None,
    ) -> tuple[str, str | None]:
        """Intelligently decide how to handle new information.

        Uses prediction error gating to determine whether to:
        - SKIP: Information is already known (PE ≈ 0)
        - CREATE: Novel information
        - UPDATE: Elaborates existing memory
        - SUPERSEDE: Contradicts existing memory

        Args:
            content: The new information
            user_id: User ID for memory lookup
            metadata: Optional metadata for the memory

        Returns:
            Tuple of (decision, existing_memory_id)
            - decision: "skip", "create", "update", or "supersede"
            - existing_memory_id: ID of existing memory for update/supersede
        """
        from clara_core.contradiction import (
            calculate_similarity,
            detect_contradiction,
        )
        from clara_core.memory import ROOK

        if ROOK is None:
            return "create", None

        # Search for similar existing memories
        try:
            existing = ROOK.search(
                content,
                user_id=user_id,
                agent_id=self.agent_id,
                limit=5,
            )
        except Exception as e:
            memory_logger.warning(f"Error searching for similar memories: {e}")
            return "create", None

        results = existing.get("results", [])
        if not results:
            return "create", None

        # Find best match
        best_match = results[0]
        best_score = best_match.get("score", 0)
        best_memory_id = best_match.get("id")
        best_memory_text = best_match.get("memory", "")

        # Calculate word-overlap similarity as secondary metric
        text_similarity = calculate_similarity(content, best_memory_text)

        # Decision thresholds
        SKIP_THRESHOLD = 0.95  # Nearly identical
        UPDATE_THRESHOLD = 0.75  # Similar enough to be related
        SUPERSEDE_THRESHOLD = 0.6  # Similar topic but may contradict

        if best_score > SKIP_THRESHOLD or text_similarity > 0.9:
            memory_logger.debug(f"Skipping near-duplicate memory (score={best_score:.2f})")
            return "skip", None

        if best_score > UPDATE_THRESHOLD:
            # Check for contradiction
            contradiction = detect_contradiction(
                content,
                best_memory_text,
                use_llm=False,  # Use fast layers only
            )

            if contradiction.contradicts:
                memory_logger.info(
                    f"Detected contradiction ({contradiction.contradiction_type}): "
                    f"superseding memory {best_memory_id}"
                )
                return "supersede", best_memory_id

            # No contradiction, this updates existing
            return "update", best_memory_id

        if best_score > SUPERSEDE_THRESHOLD:
            # Lower similarity, still check for contradiction
            contradiction = detect_contradiction(
                content,
                best_memory_text,
                use_llm=False,
            )

            if contradiction.contradicts and contradiction.confidence > 0.7:
                memory_logger.info(
                    f"Detected contradiction ({contradiction.contradiction_type}): "
                    f"superseding memory {best_memory_id}"
                )
                return "supersede", best_memory_id

        # Novel information
        return "create", None

    def supersede_memory(
        self,
        old_memory_id: str,
        new_content: str,
        user_id: str,
        reason: str = "contradiction",
        metadata: dict | None = None,
    ) -> str | None:
        """Replace an old memory with new information.

        Creates a new memory and records the supersession relationship.

        Args:
            old_memory_id: ID of memory being superseded
            new_content: New memory content
            user_id: User ID
            reason: Why the supersession occurred
            metadata: Optional metadata for new memory

        Returns:
            New memory ID, or None on failure
        """
        from clara_core.memory import ROOK
        from db import SessionLocal
        from db.models import MemorySupersession

        if ROOK is None:
            return None

        try:
            # Add new memory
            result = ROOK.add(
                new_content,
                user_id=user_id,
                agent_id=self.agent_id,
                metadata=metadata or {},
            )

            new_memory_id = None
            if isinstance(result, dict) and result.get("results"):
                new_memory_id = result["results"][0].get("id")

            if not new_memory_id:
                memory_logger.warning("Failed to create new memory for supersession")
                return None

            # Record supersession
            db = SessionLocal()
            try:
                supersession = MemorySupersession(
                    old_memory_id=old_memory_id,
                    new_memory_id=new_memory_id,
                    user_id=user_id,
                    reason=reason,
                )
                db.add(supersession)
                db.commit()
            finally:
                db.close()

            # Demote old memory
            self.demote_memory(old_memory_id, user_id, reason="superseded")

            memory_logger.info(f"Superseded memory {old_memory_id} with {new_memory_id}")
            return new_memory_id

        except Exception as e:
            memory_logger.error(f"Error superseding memory: {e}")
            return None

    # ---------- Intentions ----------

    def set_intention(
        self,
        user_id: str,
        content: str,
        trigger_conditions: dict,
        expires_at: datetime | None = None,
        source_memory_id: str | None = None,
    ) -> str:
        """Create a new intention/reminder for future surfacing.

        Args:
            user_id: User this intention is for
            content: What to remind about
            trigger_conditions: When to fire (see intentions.py)
            expires_at: Optional expiration time
            source_memory_id: Optional link to source memory

        Returns:
            The created intention ID
        """
        from clara_core.intentions import create_intention

        return create_intention(
            user_id=user_id,
            content=content,
            trigger_conditions=trigger_conditions,
            agent_id=self.agent_id,
            expires_at=expires_at,
            source_memory_id=source_memory_id,
        )

    def check_intentions(
        self,
        user_id: str,
        message: str,
        context: dict | None = None,
    ) -> list[dict]:
        """Check if any intentions should fire for the given context.

        Args:
            user_id: User to check intentions for
            message: Current user message
            context: Additional context

        Returns:
            List of fired intention dicts
        """
        from clara_core.intentions import CheckStrategy, check_intentions

        return check_intentions(
            user_id=user_id,
            message=message,
            context=context,
            strategy=CheckStrategy.TIERED,
            agent_id=self.agent_id,
        )

    def format_intentions_for_prompt(
        self,
        fired_intentions: list[dict],
    ) -> str:
        """Format fired intentions for the system prompt.

        Args:
            fired_intentions: List of fired intention dicts

        Returns:
            Formatted string for the prompt
        """
        from clara_core.intentions import format_intentions_for_prompt

        return format_intentions_for_prompt(fired_intentions)
