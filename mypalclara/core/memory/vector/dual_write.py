"""Dual-write vector store for zero-downtime migration.

Supports four modes for blue-green migration:
1. primary_only: All operations go to primary (default, pre-migration)
2. dual_write: Writes go to both, reads from primary
3. dual_read: Writes go to both, reads from both (validation)
4. secondary_only: All operations go to secondary (post-migration)

Usage:
    export VECTOR_STORE_MODE=dual_write
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mypalclara.core.memory.vector.base import VectorStoreBase

logger = logging.getLogger("clara.memory.vector.dual_write")


class DualWriteMode(str, Enum):
    """Migration modes for dual-write."""

    PRIMARY_ONLY = "primary_only"
    DUAL_WRITE = "dual_write"
    DUAL_READ = "dual_read"
    SECONDARY_ONLY = "secondary_only"


class DualWriteVectorStore:
    """Dual-write vector store for zero-downtime migration.

    Writes to both stores, reads from primary (or secondary in secondary_only mode).
    Provides comparison logging in dual_read mode for validation.
    """

    def __init__(
        self,
        primary: "VectorStoreBase",
        secondary: "VectorStoreBase",
        mode: DualWriteMode | str = DualWriteMode.PRIMARY_ONLY,
    ):
        """Initialize dual-write vector store.

        Args:
            primary: Primary vector store (current production)
            secondary: Secondary vector store (migration target)
            mode: Migration mode (see DualWriteMode)
        """
        self.primary = primary
        self.secondary = secondary
        self._mode = DualWriteMode(mode) if isinstance(mode, str) else mode
        self._comparison_mismatches = 0

        logger.info(f"DualWriteVectorStore initialized in {self._mode.value} mode")

    @property
    def mode(self) -> DualWriteMode:
        """Get current migration mode."""
        return self._mode

    @mode.setter
    def mode(self, value: DualWriteMode | str) -> None:
        """Set migration mode."""
        old_mode = self._mode
        self._mode = DualWriteMode(value) if isinstance(value, str) else value
        if old_mode != self._mode:
            logger.info(f"DualWriteVectorStore mode changed: {old_mode.value} -> {self._mode.value}")

    def _should_write_to_primary(self) -> bool:
        """Check if we should write to primary store."""
        return self._mode in (
            DualWriteMode.PRIMARY_ONLY,
            DualWriteMode.DUAL_WRITE,
            DualWriteMode.DUAL_READ,
        )

    def _should_write_to_secondary(self) -> bool:
        """Check if we should write to secondary store."""
        return self._mode in (
            DualWriteMode.DUAL_WRITE,
            DualWriteMode.DUAL_READ,
            DualWriteMode.SECONDARY_ONLY,
        )

    def _should_read_from_secondary(self) -> bool:
        """Check if we should read from secondary store."""
        return self._mode == DualWriteMode.SECONDARY_ONLY

    def _should_compare_reads(self) -> bool:
        """Check if we should compare reads from both stores."""
        return self._mode == DualWriteMode.DUAL_READ

    # ---------- Write Operations ----------

    def insert(self, vectors: list, payloads: list = None, ids: list = None):
        """Insert vectors into store(s)."""
        result = None

        if self._should_write_to_primary():
            result = self.primary.insert(vectors, payloads, ids)

        if self._should_write_to_secondary():
            try:
                self.secondary.insert(vectors, payloads, ids)
            except Exception as e:
                logger.error(f"Secondary insert failed: {e}")
                # Don't fail the operation - primary succeeded

        return result

    def update(self, vector_id, vector: list = None, payload: dict = None):
        """Update a vector in store(s)."""
        result = None

        if self._should_write_to_primary():
            result = self.primary.update(vector_id, vector, payload)

        if self._should_write_to_secondary():
            try:
                self.secondary.update(vector_id, vector, payload)
            except Exception as e:
                logger.error(f"Secondary update failed: {e}")

        return result

    def delete(self, vector_id):
        """Delete a vector from store(s)."""
        result = None

        if self._should_write_to_primary():
            result = self.primary.delete(vector_id)

        if self._should_write_to_secondary():
            try:
                self.secondary.delete(vector_id)
            except Exception as e:
                logger.error(f"Secondary delete failed: {e}")

        return result

    # ---------- Read Operations ----------

    def search(self, query: str, vectors: list, limit: int = 5, filters: dict = None) -> list:
        """Search for similar vectors."""
        if self._should_read_from_secondary():
            return self.secondary.search(query, vectors, limit, filters)

        primary_results = self.primary.search(query, vectors, limit, filters)

        if self._should_compare_reads():
            try:
                secondary_results = self.secondary.search(query, vectors, limit, filters)
                self._compare_results(primary_results, secondary_results, "search")
            except Exception as e:
                logger.error(f"Secondary search failed: {e}")

        return primary_results

    def get(self, vector_id) -> dict | None:
        """Retrieve a vector by ID."""
        if self._should_read_from_secondary():
            return self.secondary.get(vector_id)

        primary_result = self.primary.get(vector_id)

        if self._should_compare_reads():
            try:
                secondary_result = self.secondary.get(vector_id)
                if primary_result != secondary_result:
                    self._comparison_mismatches += 1
                    logger.warning(
                        f"Get mismatch for {vector_id}: "
                        f"primary={primary_result is not None}, "
                        f"secondary={secondary_result is not None}"
                    )
            except Exception as e:
                logger.error(f"Secondary get failed: {e}")

        return primary_result

    def list(self, filters: dict = None, limit: int = 100) -> list:
        """List vectors with optional filters."""
        if self._should_read_from_secondary():
            return self.secondary.list(filters, limit)

        primary_results = self.primary.list(filters, limit)

        if self._should_compare_reads():
            try:
                secondary_results = self.secondary.list(filters, limit)
                self._compare_results(primary_results, secondary_results, "list")
            except Exception as e:
                logger.error(f"Secondary list failed: {e}")

        return primary_results

    # ---------- Collection Operations ----------

    def create_col(self, vector_size: int, on_disk: bool = False, distance=None):
        """Create collection in store(s)."""
        if self._should_write_to_primary():
            self.primary.create_col(vector_size, on_disk, distance)

        if self._should_write_to_secondary():
            try:
                self.secondary.create_col(vector_size, on_disk, distance)
            except Exception as e:
                logger.error(f"Secondary create_col failed: {e}")

    def delete_col(self):
        """Delete collection from store(s)."""
        if self._should_write_to_primary():
            self.primary.delete_col()

        if self._should_write_to_secondary():
            try:
                self.secondary.delete_col()
            except Exception as e:
                logger.error(f"Secondary delete_col failed: {e}")

    def col_info(self) -> dict:
        """Get collection info from active store."""
        if self._should_read_from_secondary():
            return self.secondary.col_info()
        return self.primary.col_info()

    def list_cols(self) -> list:
        """List collections from active store."""
        if self._should_read_from_secondary():
            return self.secondary.list_cols()
        return self.primary.list_cols()

    def reset(self):
        """Reset store(s)."""
        if self._should_write_to_primary():
            self.primary.reset()

        if self._should_write_to_secondary():
            try:
                self.secondary.reset()
            except Exception as e:
                logger.error(f"Secondary reset failed: {e}")

    # ---------- Comparison Helpers ----------

    def _compare_results(
        self,
        primary: list,
        secondary: list,
        operation: str,
    ) -> None:
        """Compare results from primary and secondary stores.

        Logs mismatches for validation during dual_read mode.
        """
        primary_ids = set()
        secondary_ids = set()

        # Extract IDs from results
        for r in primary[:10]:  # Compare top 10
            if hasattr(r, "id"):
                primary_ids.add(r.id)
            elif isinstance(r, dict) and "id" in r:
                primary_ids.add(r["id"])

        for r in secondary[:10]:
            if hasattr(r, "id"):
                secondary_ids.add(r.id)
            elif isinstance(r, dict) and "id" in r:
                secondary_ids.add(r["id"])

        # Check overlap
        overlap = len(primary_ids & secondary_ids)
        total = max(len(primary_ids), len(secondary_ids))

        if total > 0 and overlap / total < 0.8:
            self._comparison_mismatches += 1
            logger.warning(
                f"{operation} result mismatch: "
                f"overlap={overlap}/{total} ({overlap/total*100:.1f}%), "
                f"total mismatches={self._comparison_mismatches}"
            )

    def get_stats(self) -> dict:
        """Get dual-write statistics."""
        return {
            "mode": self._mode.value,
            "comparison_mismatches": self._comparison_mismatches,
        }
