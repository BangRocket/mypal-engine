"""FSRS-6 memory dynamics management for Clara.

Handles memory promotion, demotion, scoring, and FSRS state tracking.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from clara_core.memory_manager import (
    FSRS_DYNAMICS_WEIGHT,
    FSRS_SEMANTIC_WEIGHT,
    MEMORY_ACCESS_LOG_RETENTION_DAYS,
    PRUNE_CHECK_FREQUENCY,
)
from config.logging import get_logger

if TYPE_CHECKING:
    from sqlalchemy.orm import Session as OrmSession

    from db.models import MemoryDynamics

memory_logger = get_logger("memory")


class MemoryDynamicsManager:
    """Manages FSRS memory dynamics: promotion, demotion, scoring, and ranking."""

    def __init__(self):
        self._last_retrieved_memory_ids: dict[str, list[str]] = {}
        self._promote_count: int = 0

    def get_memory_dynamics(
        self,
        memory_id: str,
        user_id: str,
    ) -> "MemoryDynamics | None":
        """Get FSRS dynamics for a memory."""
        from db import SessionLocal
        from db.models import MemoryDynamics

        db = SessionLocal()
        try:
            return db.query(MemoryDynamics).filter_by(memory_id=memory_id, user_id=user_id).first()
        finally:
            db.close()

    def ensure_memory_dynamics(
        self,
        memory_id: str,
        user_id: str,
        is_key: bool = False,
    ) -> "MemoryDynamics":
        """Ensure FSRS dynamics exist for a memory, creating if needed."""
        from db import SessionLocal
        from db.models import MemoryDynamics

        db = SessionLocal()
        try:
            dynamics = db.query(MemoryDynamics).filter_by(memory_id=memory_id, user_id=user_id).first()

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
        grade: int = 3,
        signal_type: str = "used_in_response",
    ) -> None:
        """Mark memory as successfully recalled, updating FSRS state."""
        from clara_core.memory.dynamics.fsrs import (
            FsrsParams,
            Grade,
            MemoryState,
            retrievability,
            review,
        )
        from db import SessionLocal
        from db.models import MemoryAccessLog, MemoryDynamics

        db = SessionLocal()
        try:
            dynamics = db.query(MemoryDynamics).filter_by(memory_id=memory_id, user_id=user_id).first()

            if not dynamics:
                dynamics = MemoryDynamics(memory_id=memory_id, user_id=user_id)
                db.add(dynamics)
                db.commit()
                db.refresh(dynamics)

            current_state = MemoryState(
                stability=dynamics.stability,
                difficulty=dynamics.difficulty,
                retrieval_strength=dynamics.retrieval_strength,
                storage_strength=dynamics.storage_strength,
                last_review=dynamics.last_accessed_at,
                review_count=dynamics.access_count,
            )

            now = datetime.now(UTC).replace(tzinfo=None)

            if dynamics.last_accessed_at:
                days_elapsed = (now - dynamics.last_accessed_at).total_seconds() / 86400.0
            else:
                days_elapsed = 0.0

            current_r = retrievability(days_elapsed, dynamics.stability)

            grade_enum = Grade(grade) if isinstance(grade, int) else grade
            result = review(current_state, grade_enum, now, FsrsParams())

            dynamics.stability = result.new_state.stability
            dynamics.difficulty = result.new_state.difficulty
            dynamics.retrieval_strength = result.new_state.retrieval_strength
            dynamics.storage_strength = result.new_state.storage_strength
            dynamics.last_accessed_at = now
            dynamics.access_count += 1

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

            self._promote_count += 1
            if self._promote_count % PRUNE_CHECK_FREQUENCY == 0:
                self.prune_old_access_logs(db)

        except Exception as e:
            memory_logger.error(f"Error promoting memory {memory_id}: {e}")
            db.rollback()
        finally:
            db.close()

    def prune_old_access_logs(self, db: "OrmSession", retention_days: int = MEMORY_ACCESS_LOG_RETENTION_DAYS) -> int:
        """Delete MemoryAccessLog records older than retention period."""
        from db.models import MemoryAccessLog

        cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=retention_days)
        try:
            count = db.query(MemoryAccessLog).filter(MemoryAccessLog.accessed_at < cutoff).delete()
            db.commit()
            if count:
                memory_logger.info(f"Pruned {count} access log records older than {retention_days} days")
            return count
        except Exception as e:
            memory_logger.error(f"Error pruning access logs: {e}")
            db.rollback()
            return 0

    def demote_memory(
        self,
        memory_id: str,
        user_id: str,
        reason: str = "user_correction",
    ) -> None:
        """Mark memory as incorrect/outdated, decreasing stability."""
        self.promote_memory(
            memory_id=memory_id,
            user_id=user_id,
            grade=1,  # Grade.AGAIN
            signal_type=reason,
        )

    def calculate_memory_score(
        self,
        memory_id: str,
        user_id: str,
        semantic_score: float,
    ) -> float:
        """Calculate composite score for memory ranking."""
        from clara_core.memory.dynamics.fsrs import calculate_memory_score, retrievability
        from db import SessionLocal
        from db.models import MemoryDynamics

        db = SessionLocal()
        try:
            dynamics = db.query(MemoryDynamics).filter_by(memory_id=memory_id, user_id=user_id).first()

            if not dynamics:
                return semantic_score

            now = datetime.now(UTC).replace(tzinfo=None)
            if dynamics.last_accessed_at:
                days_elapsed = (now - dynamics.last_accessed_at).total_seconds() / 86400.0
            else:
                days_elapsed = 0.0

            current_r = retrievability(days_elapsed, dynamics.stability)

            importance = dynamics.importance_weight if dynamics.importance_weight else 1.0
            fsrs_score = calculate_memory_score(
                current_r,
                dynamics.storage_strength,
                importance,
            )

            return FSRS_SEMANTIC_WEIGHT * semantic_score + FSRS_DYNAMICS_WEIGHT * fsrs_score

        finally:
            db.close()

    def get_last_retrieved_memory_ids(self, user_id: str) -> list[str]:
        """Get memory IDs from the last retrieval for a user."""
        return self._last_retrieved_memory_ids.get(user_id, [])

    def set_last_retrieved_memory_ids(self, user_id: str, ids: list[str]) -> None:
        """Set memory IDs from last retrieval for a user."""
        self._last_retrieved_memory_ids[user_id] = ids

    def rank_results_with_fsrs_batch(
        self,
        results: list[dict],
        user_id: str,
    ) -> list[dict]:
        """Re-rank search results using batched FSRS lookups.

        Performance optimized: single DB query instead of N queries.
        """
        if not results:
            return results

        from clara_core.memory.dynamics.fsrs import calculate_memory_score, retrievability
        from db import SessionLocal
        from db.models import MemoryDynamics

        memory_ids = [r.get("id") for r in results if r.get("id")]

        if not memory_ids:
            return sorted(results, key=lambda x: x.get("score", 0.5), reverse=True)

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

        now = datetime.now(UTC).replace(tzinfo=None)
        scored_results = []

        for r in results:
            memory_id = r.get("id")
            semantic_score = r.get("score", 0.5)

            if memory_id and memory_id in dynamics_map:
                dynamics = dynamics_map[memory_id]

                if dynamics.last_accessed_at:
                    days_elapsed = (now - dynamics.last_accessed_at).total_seconds() / 86400.0
                else:
                    days_elapsed = 0.0

                current_r = retrievability(days_elapsed, dynamics.stability)

                importance = dynamics.importance_weight if dynamics.importance_weight else 1.0
                fsrs_score = calculate_memory_score(
                    current_r,
                    dynamics.storage_strength,
                    importance,
                )

                composite_score = FSRS_SEMANTIC_WEIGHT * semantic_score + FSRS_DYNAMICS_WEIGHT * fsrs_score
            else:
                composite_score = semantic_score

            category = None
            if memory_id and memory_id in dynamics_map:
                category = dynamics_map[memory_id].category

            scored_results.append(
                {
                    **r,
                    "_composite_score": composite_score,
                    "_category": category,
                }
            )

        scored_results.sort(key=lambda x: x.get("_composite_score", 0), reverse=True)

        return scored_results
