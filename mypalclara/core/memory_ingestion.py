"""Memory ingestion management for Clara platform.

Handles smart ingestion, validation, and supersession of memories.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from mypalclara.config.logging import get_logger
from mypalclara.core.memory_manager import (
    SMART_INGEST_SKIP_THRESHOLD,
    SMART_INGEST_SUPERSEDE_THRESHOLD,
    SMART_INGEST_UPDATE_THRESHOLD,
)

if TYPE_CHECKING:
    from mypalclara.core.memory_dynamics_manager import MemoryDynamicsManager

memory_logger = get_logger("memory")


class MemoryIngestionManager:
    """Manages memory ingestion: validation, smart ingest, and supersession."""

    def __init__(self, agent_id: str = "clara", dynamics_manager: "MemoryDynamicsManager | None" = None):
        self.agent_id = agent_id
        self._dynamics_manager = dynamics_manager

    def validate_ingested_memories(
        self,
        mem_results: list[dict],
        user_id: str,
    ) -> list[dict]:
        """Validate newly ingested memories against existing ones.

        Uses smart_ingest() as a post-ingestion check to detect duplicates
        and contradictions that ROOK's built-in dedup may have missed.

        Args:
            mem_results: Results from ROOK.add()
            user_id: User who owns the memories

        Returns:
            Filtered list of results (duplicates removed, contradictions superseded)
        """
        from mypalclara.core.memory import ROOK

        if not mem_results or ROOK is None:
            return mem_results

        # Collect all IDs from this batch to exclude from self-matching
        batch_ids = [m.get("id") for m in mem_results if m.get("id")]

        validated = []
        for mem in mem_results:
            memory_text = mem.get("memory", "")
            memory_id = mem.get("id", "")
            event = mem.get("event", "")

            # Only validate newly added memories (not updates ROOK already handled)
            if event != "ADD" or not memory_text:
                validated.append(mem)
                continue

            decision, existing_id = self.smart_ingest(
                content=memory_text,
                user_id=user_id,
                exclude_ids=batch_ids,
            )

            if decision == "skip":
                # Near-duplicate of existing memory — delete the one ROOK just added
                memory_logger.debug(f"Post-ingest: removing duplicate memory {memory_id}")
                try:
                    ROOK.delete(memory_id)
                except Exception as e:
                    memory_logger.warning(f"Failed to delete duplicate: {e}")
                continue  # Don't include in validated list

            elif decision == "supersede" and existing_id:
                memory_logger.info(f"Post-ingest: superseding {existing_id} with {memory_id}")
                try:
                    self._record_supersession(
                        old_memory_id=existing_id,
                        new_memory_id=memory_id,
                        user_id=user_id,
                        reason="post_ingest_contradiction",
                    )
                except Exception as e:
                    memory_logger.warning(f"Failed to supersede: {e}")

            validated.append(mem)

        skipped = len(mem_results) - len(validated)
        if skipped:
            memory_logger.info(f"Post-ingestion validation: {skipped}/{len(mem_results)} " f"duplicates removed")

        return validated

    def smart_ingest(
        self,
        content: str,
        user_id: str,
        metadata: dict | None = None,
        exclude_ids: list[str] | None = None,
    ) -> tuple[str, str | None]:
        """Intelligently decide how to handle new information.

        Uses prediction error gating to determine whether to:
        - SKIP: Information is already known (PE ~ 0)
        - CREATE: Novel information
        - UPDATE: Elaborates existing memory
        - SUPERSEDE: Contradicts existing memory

        Args:
            content: The new information
            user_id: User ID for memory lookup
            metadata: Optional metadata for the memory
            exclude_ids: Memory IDs to exclude from search results

        Returns:
            Tuple of (decision, existing_memory_id)
        """
        from mypalclara.core.memory import ROOK
        from mypalclara.core.memory.dynamics.contradiction import (
            calculate_similarity,
            detect_contradiction,
        )

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

        # Filter out excluded IDs
        if exclude_ids:
            results = [r for r in results if r.get("id") not in exclude_ids]

        if not results:
            return "create", None

        # Find best match
        best_match = results[0]
        best_score = best_match.get("score", 0)
        best_memory_id = best_match.get("id")
        best_memory_text = best_match.get("memory", "")

        # Calculate word-overlap similarity as secondary metric
        text_similarity = calculate_similarity(content, best_memory_text)

        if best_score > SMART_INGEST_SKIP_THRESHOLD or text_similarity > 0.9:
            memory_logger.debug(f"Skipping near-duplicate memory (score={best_score:.2f})")
            return "skip", None

        if best_score > SMART_INGEST_UPDATE_THRESHOLD:
            # Check for contradiction
            contradiction = detect_contradiction(
                content,
                best_memory_text,
                use_llm=False,
            )

            if contradiction.contradicts:
                memory_logger.info(
                    f"Detected contradiction ({contradiction.contradiction_type}): "
                    f"superseding memory {best_memory_id}"
                )
                return "supersede", best_memory_id

            return "update", best_memory_id

        if best_score > SMART_INGEST_SUPERSEDE_THRESHOLD:
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

    def _record_supersession(
        self,
        old_memory_id: str,
        new_memory_id: str,
        user_id: str,
        reason: str = "contradiction",
    ) -> None:
        """Record a supersession relationship and demote the old memory."""
        from mypalclara.db import SessionLocal
        from mypalclara.db.models import MemorySupersession

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

        # Demote old memory via dynamics manager
        if self._dynamics_manager:
            self._dynamics_manager.demote_memory(old_memory_id, user_id, reason="superseded")

        memory_logger.info(f"Recorded supersession: {old_memory_id} -> {new_memory_id}")

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
        from mypalclara.core.memory import ROOK

        if ROOK is None:
            return None

        try:
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

            self._record_supersession(old_memory_id, new_memory_id, user_id, reason)

            return new_memory_id

        except Exception as e:
            memory_logger.error(f"Error superseding memory: {e}")
            return None
