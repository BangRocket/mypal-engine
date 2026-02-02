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
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

from config.logging import get_logger

# Module loggers
logger = get_logger("mem0")
thread_logger = get_logger("thread")
memory_logger = get_logger("memory")

# Timezone for message timestamps (defaults to America/New_York)
DEFAULT_TIMEZONE = os.getenv("DEFAULT_TIMEZONE", "America/New_York")

if TYPE_CHECKING:
    from sqlalchemy.orm import Session as OrmSession

    from db.models import Message, Session

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
    """Generate structured memories from user_profile.txt using LLM extraction."""
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
    """Load initial user profile into mem0 once on first run.

    Uses the bootstrap pipeline:
    1. If generated/*.json files exist, load from them
    2. If not, generate from inputs/user_profile.txt first
    3. Apply structured memories to mem0 with graph-friendly grouping
    """
    from config.mem0 import MEM0

    skip_profile = os.getenv("SKIP_PROFILE_LOAD", "true").lower() == "true"
    if skip_profile:
        logger.debug("Profile loading disabled (SKIP_PROFILE_LOAD=true)")
        return

    if MEM0 is None:
        logger.warning("Skipping profile load - mem0 not available")
        return

    if PROFILE_LOADED_FLAG.exists():
        logger.debug("Profile already loaded (flag exists), skipping")
        return

    from src.bootstrap_memory import (
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
        PROFILE_LOADED_FLAG.write_text(
            f"loading started at {datetime.now().isoformat()}"
        )
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

    @classmethod
    def get_instance(cls) -> "MemoryManager":
        """Get the singleton MemoryManager instance.

        Raises:
            RuntimeError: If not initialized
        """
        if cls._instance is None:
            raise RuntimeError(
                "MemoryManager not initialized. Call MemoryManager.initialize() first."
            )
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

        all_msgs = (
            db.query(Message)
            .filter_by(session_id=thread.id)
            .order_by(Message.created_at.asc())
            .all()
        )

        if not all_msgs:
            return ""

        conversation = "\n".join(
            f"{m.role.upper()}: {m.content[:500]}" for m in all_msgs[-30:]
        )

        summary_prompt = [
            {
                "role": "system",
                "content": "Summarize this conversation in 2-3 sentences. "
                "Focus on key topics, decisions, and important context.",
            },
            {"role": "user", "content": conversation},
        ]

        summary = self.llm(summary_prompt)
        thread.session_summary = summary
        db.commit()
        thread_logger.info(f"Updated summary for thread {thread.id}")
        return summary

    # ---------- mem0 integration ----------

    def fetch_mem0_context(
        self,
        user_id: str,
        project_id: str,
        user_message: str,
        participants: list[dict] | None = None,
        is_dm: bool = False,
    ) -> tuple[list[str], list[str], list[dict]]:
        """Fetch relevant memories from mem0.

        Uses entity-scoped memory with user_id + agent_id for proper isolation.

        Memory retrieval:
        1. First fetch "key" memories (is_key=true) - always included
        2. Then fetch relevant memories via semantic search
        3. Fetch graph relations (entity relationships)
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
        from config.mem0 import MEM0

        if MEM0 is None:
            return [], [], []

        # 1. Fetch key memories first (always included)
        key_mems: list[str] = []
        try:
            key_res = MEM0.get_all(
                user_id=user_id,
                agent_id=self.agent_id,
                filters={"is_key": "true"},  # String "true" to match JSON boolean
                limit=MAX_KEY_MEMORIES,
            )
            for r in key_res.get("results", []):
                key_mems.append(f"[KEY] {r['memory']}")
        except Exception as e:
            logger.error(f"Error fetching key memories: {e}")

        # Truncate search query if too long
        search_query = user_message
        if len(search_query) > MAX_SEARCH_QUERY_CHARS:
            search_query = search_query[-MAX_SEARCH_QUERY_CHARS:]
            logger.debug(f"Truncated search query to {MAX_SEARCH_QUERY_CHARS} chars")

        # 2. Entity-scoped search: user_id + agent_id
        # Also collect graph relations from search results
        graph_relations: list[dict] = []

        try:
            user_res = MEM0.search(
                search_query,
                user_id=user_id,
                agent_id=self.agent_id,
            )
            # Collect graph relations if present
            if user_res.get("relations"):
                graph_relations.extend(user_res["relations"])
        except Exception as e:
            logger.error(f"Error searching user memories: {e}", exc_info=True)
            user_res = {"results": []}

        try:
            proj_res = MEM0.search(
                search_query,
                user_id=user_id,
                agent_id=self.agent_id,
                filters={"project_id": project_id},
            )
            # Collect graph relations if present (deduplicate)
            if proj_res.get("relations"):
                existing = {(r.get("source"), r.get("relationship"), r.get("destination")) for r in graph_relations}
                for rel in proj_res["relations"]:
                    key = (rel.get("source"), rel.get("relationship"), rel.get("destination"))
                    if key not in existing:
                        graph_relations.append(rel)
                        existing.add(key)
        except Exception as e:
            logger.error(f"Error searching project memories: {e}", exc_info=True)
            proj_res = {"results": []}

        # Build user memories: key first, then relevant (deduplicated)
        user_mems = list(key_mems)  # Start with key memories
        key_texts = {m.replace("[KEY] ", "") for m in key_mems}  # For dedup

        for r in user_res.get("results", []):
            mem = r["memory"]
            if mem not in key_texts:  # Don't duplicate key memories
                user_mems.append(mem)

        proj_mems = [r["memory"] for r in proj_res.get("results", [])]

        # Also search for memories about each participant
        if participants:
            for p in participants:
                p_id = p.get("id")
                p_name = p.get("name", p_id)
                if not p_id or p_id == user_id:
                    continue

                try:
                    p_search = MEM0.search(
                        f"{p_name} {search_query[:500]}",
                        user_id=user_id,
                        agent_id=self.agent_id,
                    )
                    for r in p_search.get("results", []):
                        mem = r["memory"]
                        if mem not in key_texts and mem not in user_mems:
                            labeled_mem = f"[About {p_name}]: {mem}"
                            if labeled_mem not in user_mems:
                                user_mems.append(labeled_mem)
                except Exception as e:
                    logger.warning(f"Error searching participant {p_id}: {e}")

        # Extract contact-related memories with source info
        for r in user_res.get("results", []):
            metadata = r.get("metadata", {})
            if metadata.get("contact_id"):
                contact_name = metadata.get(
                    "contact_name", metadata.get("contact_id")
                )
                mem_text = f"[About {contact_name}]: {r['memory']}"
                if mem_text not in user_mems:
                    user_mems.append(mem_text)

        # Limit non-key memories to reduce token usage
        # Key memories (at start) are always kept, limit the rest
        num_key = len(key_mems)
        if len(user_mems) > num_key + MAX_MEMORIES_PER_TYPE:
            user_mems = user_mems[:num_key + MAX_MEMORIES_PER_TYPE]
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
            # Send Discord embed with memory summary
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

        self._on_memory_event("memory_retrieved", {
            "user_id": user_id,
            "key_count": key_count,
            "user_count": user_count,
            "proj_count": proj_count,
            "graph_count": graph_count,
            "total": total,
            "samples": samples,
        })

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
        from config.mem0 import MEM0

        if MEM0 is None:
            return

        # Build context with participant names for better extraction
        context_prefix = ""
        if participants:
            names = [p.get("name", p.get("id", "Unknown")) for p in participants]
            context_prefix = f"[Participants: {', '.join(names)}]\n"

        history_slice = [
            {"role": m.role, "content": m.content} for m in recent_msgs[-4:]
        ] + [
            {"role": "user", "content": context_prefix + user_message},
            {"role": "assistant", "content": assistant_reply},
        ]

        # Store with participant metadata for cross-user search
        # Tag source type: "personal" for DMs, "project" for server channels
        source_type = "personal" if is_dm else "project"
        metadata = {
            "project_id": project_id,
            "source_type": source_type,
        }
        if participants:
            metadata["participant_ids"] = [
                p.get("id") for p in participants if p.get("id")
            ]
            metadata["participant_names"] = [
                p.get("name") for p in participants if p.get("name")
            ]

        try:
            result = MEM0.add(
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
                    mem_count = len(result.get("results", []))
                    # Handle graph relations from add result
                    relations = result.get("relations", {})
                    added_entities = relations.get("added_entities", []) if isinstance(relations, dict) else []
                    deleted_entities = relations.get("deleted_entities", []) if isinstance(relations, dict) else []

                    if mem_count or added_entities:
                        logger.info(
                            f"Added {mem_count} memories, "
                            f"{len(added_entities)} graph entities"
                        )
                        if deleted_entities:
                            logger.debug(f"Deleted {len(deleted_entities)} outdated graph entities")

                        # Send Discord embed for memory extraction
                        self._send_memory_added_embed(
                            user_id=user_id,
                            count=mem_count,
                            source_type=source_type,
                            results=result.get("results", []),
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

        self._on_memory_event("memory_extracted", {
            "user_id": user_id,
            "count": count,
            "source_type": source_type,
            "samples": samples,
            "graph_added": graph_added,
        })

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

        from config.mem0 import MEM0

        if MEM0 is None:
            return []

        try:
            # Search for emotional_context memories
            results = MEM0.get_all(
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
                        timestamp = datetime.fromisoformat(
                            timestamp_str.replace("Z", "+00:00")
                        )
                        if timestamp < cutoff:
                            continue
                    except (ValueError, TypeError):
                        timestamp = None
                else:
                    timestamp = None

                emotional_contexts.append({
                    "memory": r.get("memory", ""),
                    "timestamp": timestamp_str,
                    "arc": metadata.get("emotional_arc", "stable"),
                    "energy": metadata.get("energy_level", "neutral"),
                    "channel_name": metadata.get("channel_name", "unknown"),
                    "is_dm": metadata.get("is_dm", False),
                    "ending_sentiment": metadata.get("ending_sentiment", 0.0),
                })

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
    ) -> list[dict[str, str]]:
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
            List of messages ready for LLM
        """
        from config.bot import PERSONALITY

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

        messages: list[dict[str, str]] = [
            {"role": "system", "content": system_base},
        ]

        if context_parts:
            messages.append({"role": "system", "content": "\n\n".join(context_parts)})

        # Add recent messages (only user messages get timestamps to avoid Clara mimicking the format)
        for m in recent_msgs:
            if m.role == "user":
                timestamp = _format_message_timestamp(getattr(m, "created_at", None))
                if timestamp:
                    content = f"[{timestamp}] {m.content}"
                else:
                    content = m.content
            else:
                content = m.content
            messages.append({"role": m.role, "content": content})

        messages.append({"role": "user", "content": user_message})
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
