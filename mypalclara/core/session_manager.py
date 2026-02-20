"""Session management for Clara platform.

Handles thread/message persistence and session summaries.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from clara_core.llm.messages import SystemMessage, UserMessage
from config.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.orm import Session as OrmSession

    from db.models import Message, Session

# Re-use constants from memory_manager
from clara_core.memory_manager import CONTEXT_MESSAGE_COUNT, SUMMARY_INTERVAL, THREAD_SUMMARY_MAX_MESSAGES

thread_logger = get_logger("thread")


class SessionManager:
    """Manages session lifecycle: creation, message storage, and summaries."""

    def __init__(self, llm_callable: "Callable[[list[dict]], str] | None" = None):
        self._llm_callable = llm_callable

    @property
    def llm(self) -> "Callable[[list[dict]], str]":
        if self._llm_callable is None:
            raise RuntimeError("SessionManager requires llm_callable for summary generation")
        return self._llm_callable

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
            project_id: Optional project UUID. If None, uses or creates default project.
            title: Optional session title for UI display

        Returns:
            Session object (existing or newly created)
        """
        from db.models import Project, Session

        # Ensure we have a project
        if project_id is None:
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

    def should_update_summary(self, db: "OrmSession", thread_id: str) -> bool:
        """Check if thread summary should be updated."""
        msg_count = self.get_message_count(db, thread_id)
        return msg_count > 0 and msg_count % SUMMARY_INTERVAL == 0

    def update_thread_summary(self, db: "OrmSession", thread: "Session") -> str:
        """Generate/update summary for a thread."""
        from clara_core.memory_manager import _format_message_timestamp
        from db.models import Message

        all_msgs = db.query(Message).filter_by(session_id=thread.id).order_by(Message.created_at.asc()).all()

        if not all_msgs:
            return ""

        # Include timestamps so summaries have temporal context
        lines = []
        for m in all_msgs[-THREAD_SUMMARY_MAX_MESSAGES:]:
            ts = _format_message_timestamp(getattr(m, "created_at", None))
            prefix = f"[{ts}] " if ts else ""
            lines.append(f"{prefix}{m.role.upper()}: {m.content[:500]}")
        conversation = "\n".join(lines)

        summary_prompt = [
            SystemMessage(
                content="Summarize this conversation in 2-3 sentences. "
                "Focus on key topics, decisions, and important context. "
                "Include when things happened (e.g. 'yesterday evening', 'this morning') "
                "based on the timestamps.",
            ),
            UserMessage(content=conversation),
        ]

        summary = self.llm(summary_prompt)
        thread.session_summary = summary
        db.commit()
        thread_logger.info(f"Updated summary for thread {thread.id}")
        return summary
