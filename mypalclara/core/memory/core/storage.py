"""History storage backends for Clara Memory System.

Provides both SQLite (local/development) and PostgreSQL (production) backends
for tracking memory history events (ADD, UPDATE, DELETE).
"""

import logging
import os
import sqlite3
import threading
import uuid
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

logger = logging.getLogger("clara.memory.storage")


class HistoryManager(ABC):
    """Abstract base class for memory history storage."""

    @abstractmethod
    def add_history(
        self,
        memory_id: str,
        old_memory: Optional[str],
        new_memory: Optional[str],
        event: str,
        *,
        created_at: Optional[str] = None,
        updated_at: Optional[str] = None,
        is_deleted: int = 0,
        actor_id: Optional[str] = None,
        role: Optional[str] = None,
    ) -> None:
        """Add a history record."""
        pass

    @abstractmethod
    def get_history(self, memory_id: str) -> List[Dict[str, Any]]:
        """Get history for a memory."""
        pass

    @abstractmethod
    def reset(self) -> None:
        """Reset all history."""
        pass

    @abstractmethod
    def close(self) -> None:
        """Close connections."""
        pass


class PostgresHistoryManager(HistoryManager):
    """PostgreSQL-based storage manager for memory history.

    Uses the main DATABASE_URL connection via SQLAlchemy.
    """

    def __init__(self):
        """Initialize PostgreSQL history manager."""
        # Import here to avoid circular imports and allow lazy loading
        from mypalclara.db import SessionLocal

        self._session_factory = SessionLocal
        logger.info("Memory history: PostgreSQL (DATABASE_URL)")

    def add_history(
        self,
        memory_id: str,
        old_memory: Optional[str],
        new_memory: Optional[str],
        event: str,
        *,
        created_at: Optional[str] = None,
        updated_at: Optional[str] = None,
        is_deleted: int = 0,
        actor_id: Optional[str] = None,
        role: Optional[str] = None,
    ) -> None:
        """Add a history record to PostgreSQL."""
        from datetime import datetime

        from mypalclara.db.models import MemoryHistory, utcnow

        session = self._session_factory()
        try:
            # Parse datetime strings if provided
            created_dt = None
            if created_at:
                try:
                    created_dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                except (ValueError, AttributeError):
                    created_dt = utcnow()
            else:
                created_dt = utcnow()

            updated_dt = None
            if updated_at:
                try:
                    updated_dt = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
                except (ValueError, AttributeError):
                    pass

            history = MemoryHistory(
                id=str(uuid.uuid4()),
                memory_id=memory_id,
                old_memory=old_memory,
                new_memory=new_memory,
                event=event,
                created_at=created_dt,
                updated_at=updated_dt,
                is_deleted=bool(is_deleted),
                actor_id=actor_id,
                role=role,
            )
            session.add(history)
            session.commit()
        except Exception as e:
            session.rollback()
            logger.error(f"Failed to add history record: {e}")
            raise
        finally:
            session.close()

    def get_history(self, memory_id: str) -> List[Dict[str, Any]]:
        """Get history for a memory from PostgreSQL."""
        from mypalclara.db.models import MemoryHistory

        session = self._session_factory()
        try:
            records = (
                session.query(MemoryHistory)
                .filter(MemoryHistory.memory_id == memory_id)
                .order_by(MemoryHistory.created_at.asc(), MemoryHistory.updated_at.asc())
                .all()
            )

            return [
                {
                    "id": r.id,
                    "memory_id": r.memory_id,
                    "old_memory": r.old_memory,
                    "new_memory": r.new_memory,
                    "event": r.event,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                    "updated_at": r.updated_at.isoformat() if r.updated_at else None,
                    "is_deleted": r.is_deleted,
                    "actor_id": r.actor_id,
                    "role": r.role,
                }
                for r in records
            ]
        finally:
            session.close()

    def reset(self) -> None:
        """Delete all history records."""
        from mypalclara.db.models import MemoryHistory

        session = self._session_factory()
        try:
            session.query(MemoryHistory).delete()
            session.commit()
            logger.info("Memory history reset (PostgreSQL)")
        except Exception as e:
            session.rollback()
            logger.error(f"Failed to reset history: {e}")
            raise
        finally:
            session.close()

    def close(self) -> None:
        """No-op for PostgreSQL (uses connection pool)."""
        pass


class SQLiteManager(HistoryManager):
    """SQLite-based storage manager for memory history."""

    def __init__(self, db_path: str = ":memory:"):
        """Initialize SQLite manager.

        Args:
            db_path: Path to SQLite database file, or ":memory:" for in-memory database
        """
        self.db_path = db_path
        self.connection = sqlite3.connect(self.db_path, check_same_thread=False)
        self._lock = threading.Lock()
        self._migrate_history_table()
        self._create_history_table()
        logger.info(f"Memory history: SQLite ({db_path})")

    def _migrate_history_table(self) -> None:
        """Migrate history table if schema has changed."""
        with self._lock:
            try:
                self.connection.execute("BEGIN")
                cur = self.connection.cursor()

                cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='history'")
                if cur.fetchone() is None:
                    self.connection.execute("COMMIT")
                    return  # nothing to migrate

                cur.execute("PRAGMA table_info(history)")
                old_cols = {row[1] for row in cur.fetchall()}

                expected_cols = {
                    "id",
                    "memory_id",
                    "old_memory",
                    "new_memory",
                    "event",
                    "created_at",
                    "updated_at",
                    "is_deleted",
                    "actor_id",
                    "role",
                }

                if old_cols == expected_cols:
                    self.connection.execute("COMMIT")
                    return

                logger.info("Migrating history table to new schema.")

                # Clean up any existing history_old table from previous failed migration
                cur.execute("DROP TABLE IF EXISTS history_old")

                # Rename the current history table
                cur.execute("ALTER TABLE history RENAME TO history_old")

                # Create the new history table with updated schema
                cur.execute(
                    """
                    CREATE TABLE history (
                        id           TEXT PRIMARY KEY,
                        memory_id    TEXT,
                        old_memory   TEXT,
                        new_memory   TEXT,
                        event        TEXT,
                        created_at   DATETIME,
                        updated_at   DATETIME,
                        is_deleted   INTEGER,
                        actor_id     TEXT,
                        role         TEXT
                    )
                """
                )

                # Copy data from old table to new table
                intersecting = list(expected_cols & old_cols)
                if intersecting:
                    cols_csv = ", ".join(intersecting)
                    cur.execute(f"INSERT INTO history ({cols_csv}) SELECT {cols_csv} FROM history_old")

                # Drop the old table
                cur.execute("DROP TABLE history_old")

                # Commit the transaction
                self.connection.execute("COMMIT")
                logger.info("History table migration completed successfully.")

            except Exception as e:
                self.connection.execute("ROLLBACK")
                logger.error(f"History table migration failed: {e}")
                raise

    def _create_history_table(self) -> None:
        """Create the history table if it doesn't exist."""
        with self._lock:
            try:
                self.connection.execute("BEGIN")
                self.connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS history (
                        id           TEXT PRIMARY KEY,
                        memory_id    TEXT,
                        old_memory   TEXT,
                        new_memory   TEXT,
                        event        TEXT,
                        created_at   DATETIME,
                        updated_at   DATETIME,
                        is_deleted   INTEGER,
                        actor_id     TEXT,
                        role         TEXT
                    )
                """
                )
                self.connection.execute("COMMIT")
            except Exception as e:
                self.connection.execute("ROLLBACK")
                logger.error(f"Failed to create history table: {e}")
                raise

    def add_history(
        self,
        memory_id: str,
        old_memory: Optional[str],
        new_memory: Optional[str],
        event: str,
        *,
        created_at: Optional[str] = None,
        updated_at: Optional[str] = None,
        is_deleted: int = 0,
        actor_id: Optional[str] = None,
        role: Optional[str] = None,
    ) -> None:
        """Add a history record.

        Args:
            memory_id: ID of the memory
            old_memory: Previous memory content
            new_memory: New memory content
            event: Event type (ADD, UPDATE, DELETE)
            created_at: Creation timestamp
            updated_at: Update timestamp
            is_deleted: Whether the memory was deleted
            actor_id: ID of the actor
            role: Role of the message
        """
        with self._lock:
            try:
                self.connection.execute("BEGIN")
                self.connection.execute(
                    """
                    INSERT INTO history (
                        id, memory_id, old_memory, new_memory, event,
                        created_at, updated_at, is_deleted, actor_id, role
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        str(uuid.uuid4()),
                        memory_id,
                        old_memory,
                        new_memory,
                        event,
                        created_at,
                        updated_at,
                        is_deleted,
                        actor_id,
                        role,
                    ),
                )
                self.connection.execute("COMMIT")
            except Exception as e:
                self.connection.execute("ROLLBACK")
                logger.error(f"Failed to add history record: {e}")
                raise

    def get_history(self, memory_id: str) -> List[Dict[str, Any]]:
        """Get history for a memory.

        Args:
            memory_id: ID of the memory

        Returns:
            List of history records
        """
        with self._lock:
            cur = self.connection.execute(
                """
                SELECT id, memory_id, old_memory, new_memory, event,
                       created_at, updated_at, is_deleted, actor_id, role
                FROM history
                WHERE memory_id = ?
                ORDER BY created_at ASC, DATETIME(updated_at) ASC
            """,
                (memory_id,),
            )
            rows = cur.fetchall()

        return [
            {
                "id": r[0],
                "memory_id": r[1],
                "old_memory": r[2],
                "new_memory": r[3],
                "event": r[4],
                "created_at": r[5],
                "updated_at": r[6],
                "is_deleted": bool(r[7]),
                "actor_id": r[8],
                "role": r[9],
            }
            for r in rows
        ]

    def reset(self) -> None:
        """Drop and recreate the history table."""
        with self._lock:
            try:
                self.connection.execute("BEGIN")
                self.connection.execute("DROP TABLE IF EXISTS history")
                self.connection.execute("COMMIT")
                self._create_history_table()
            except Exception as e:
                self.connection.execute("ROLLBACK")
                logger.error(f"Failed to reset history table: {e}")
                raise

    def close(self) -> None:
        """Close the database connection."""
        conn = getattr(self, "connection", None)
        if conn:
            conn.close()
            self.connection = None

    def __del__(self):
        """Cleanup on deletion."""
        self.close()


def get_history_manager(sqlite_path: Optional[str] = None) -> HistoryManager:
    """Factory function to get the appropriate history manager.

    Uses PostgreSQL if DATABASE_URL is set, otherwise falls back to SQLite.

    Args:
        sqlite_path: Path for SQLite database (used if DATABASE_URL not set)

    Returns:
        HistoryManager instance (PostgreSQL or SQLite)
    """
    database_url = os.getenv("DATABASE_URL")

    if database_url:
        try:
            return PostgresHistoryManager()
        except Exception as e:
            logger.warning(f"PostgreSQL history unavailable: {e}, falling back to SQLite")

    # Fall back to SQLite
    if sqlite_path:
        return SQLiteManager(sqlite_path)

    # Default SQLite path
    home_dir = os.path.expanduser("~")
    clara_memory_dir = os.environ.get("CLARA_MEMORY_DIR") or os.path.join(home_dir, ".clara_memory")
    os.makedirs(clara_memory_dir, exist_ok=True)
    return SQLiteManager(os.path.join(clara_memory_dir, "history.db"))
