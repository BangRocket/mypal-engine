"""Memory writing operations for Clara platform.

Handles adding conversation exchanges to Rook memory, classifying memories,
creating FSRS dynamics records, and notifying about extraction events.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, ClassVar

from mypalclara.config.logging import get_logger
from mypalclara.core.llm.messages import AssistantMessage, UserMessage
from mypalclara.core.memory_manager import MEMORY_CONTEXT_SLICE

# Module loggers (matching memory_manager.py conventions)
logger = get_logger("rook")
memory_logger = get_logger("memory")

if TYPE_CHECKING:
    from mypalclara.core.memory_dynamics_manager import MemoryDynamicsManager
    from mypalclara.core.memory_ingestion import MemoryIngestionManager
    from mypalclara.db.models import Message


class MemoryWriter:
    """Extracts and writes memories from conversation exchanges.

    Handles the write path of memory management: sending conversation slices
    to Rook for extraction, classifying results, creating FSRS dynamics
    records, and emitting notification events.
    """

    # Category keywords for memory classification
    CATEGORY_KEYWORDS: ClassVar[dict[str, list[str]]] = {
        "preferences": ["prefer", "like", "favorite", "love", "hate", "dislike", "enjoy", "want"],
        "personal": ["my name", "i am", "i'm from", "my family", "my wife", "my husband", "birthday"],
        "professional": ["work", "job", "career", "company", "project", "team", "meeting"],
        "goals": ["want to", "plan to", "going to", "hope to", "goal", "dream", "aspire"],
        "emotional": ["feel", "feeling", "mood", "happy", "sad", "anxious", "excited", "stressed"],
        "temporal": ["yesterday", "today", "tomorrow", "last week", "next week", "recently"],
    }

    def __init__(
        self,
        agent_id: str,
        on_memory_event: Callable[[str, dict[str, Any]], None] | None,
        ingestion_manager: MemoryIngestionManager,
        dynamics_manager: MemoryDynamicsManager,
        on_memories_changed: Callable[[str], None] | None = None,
    ) -> None:
        """Initialize MemoryWriter.

        Args:
            agent_id: Agent identifier for Rook storage.
            on_memory_event: Optional callback for memory extraction notifications.
                Called with (event_type, event_data).
            ingestion_manager: Reference to MemoryIngestionManager for
                post-ingestion validation.
            dynamics_manager: Reference to MemoryDynamicsManager (reserved for
                future use in dynamics record creation).
            on_memories_changed: Optional callback invoked with user_id when
                memories are added/changed, so upstream code (e.g. MemoryRetriever)
                can invalidate caches.
        """
        self.agent_id = agent_id
        self._on_memory_event = on_memory_event
        self._ingestion_manager = ingestion_manager
        self._dynamics_manager = dynamics_manager
        self._on_memories_changed = on_memories_changed

    def add_to_mem0(
        self,
        user_id: str,
        project_id: str,
        recent_msgs: list[Message],
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
        from mypalclara.core.memory import ROOK

        if ROOK is None:
            return

        # Build context with participant names for better extraction
        context_prefix = ""
        if participants:
            names = [p.get("name", p.get("id", "Unknown")) for p in participants]
            context_prefix = f"[Participants: {', '.join(names)}]\n"

        history_slice = [
            UserMessage(content=m.content) if m.role == "user" else AssistantMessage(content=m.content)
            for m in recent_msgs[-MEMORY_CONTEXT_SLICE:]
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

                    # Post-ingestion validation via smart_ingest
                    mem_results = self._ingestion_manager.validate_ingested_memories(mem_results, user_id)

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
                        if self._on_memories_changed:
                            self._on_memories_changed(user_id)

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

    def _classify_memory(self, memory_text: str) -> str | None:
        """Classify a memory into a category using keyword matching.

        Falls back to None if no clear category matches.

        Args:
            memory_text: The memory content to classify

        Returns:
            Category string or None
        """
        text_lower = memory_text.lower()
        scores: dict[str, int] = {}
        for category, keywords in self.CATEGORY_KEYWORDS.items():
            score = sum(1 for kw in keywords if kw in text_lower)
            if score > 0:
                scores[category] = score

        if not scores:
            return None

        return max(scores, key=scores.get)

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

        from mypalclara.db import SessionLocal
        from mypalclara.db.models import MemoryDynamics

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
                    # Classify the memory content
                    memory_text = r.get("memory", "")
                    category = self._classify_memory(memory_text) if memory_text else None

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
                        category=category,
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
