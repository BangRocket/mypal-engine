"""Session and node management for the Clara Gateway.

Handles:
- Node registration and lifecycle
- Session state preservation for reconnection
- User/channel session routing
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

from config.logging import get_logger

if TYPE_CHECKING:
    from websockets.server import WebSocketServerProtocol

logger = get_logger("gateway.session")


@dataclass
class NodeConnection:
    """Represents a connected adapter node."""

    node_id: str
    session_id: str
    platform: str
    websocket: WebSocketServerProtocol
    capabilities: list[str] = field(default_factory=list)
    connected_at: datetime = field(default_factory=datetime.now)
    last_ping: datetime = field(default_factory=datetime.now)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def supports_streaming(self) -> bool:
        """Check if this node supports streaming responses."""
        return "streaming" in self.capabilities

    @property
    def supports_attachments(self) -> bool:
        """Check if this node supports file attachments."""
        return "attachments" in self.capabilities


@dataclass
class UserSession:
    """Tracks a user's active session state."""

    user_id: str
    channel_id: str
    node_id: str  # Which adapter node is handling this user
    thread_id: str | None = None  # Clara session thread ID
    project_id: str | None = None
    active_request_id: str | None = None  # Currently processing request
    last_activity: datetime = field(default_factory=datetime.now)
    context: dict[str, Any] = field(default_factory=dict)

    def touch(self) -> None:
        """Update last activity timestamp."""
        self.last_activity = datetime.now()


class NodeRegistry:
    """Registry of connected adapter nodes.

    Thread-safe registration and lookup of adapter connections.
    Supports reconnection via session_id preservation.
    """

    def __init__(self) -> None:
        self._nodes: dict[str, NodeConnection] = {}  # node_id -> NodeConnection
        self._sessions: dict[str, str] = {}  # session_id -> node_id
        self._websockets: dict[WebSocketServerProtocol, str] = {}  # ws -> node_id
        self._lock = asyncio.Lock()

    async def register(
        self,
        websocket: WebSocketServerProtocol,
        node_id: str,
        platform: str,
        capabilities: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        session_id: str | None = None,
    ) -> tuple[str, bool]:
        """Register a new adapter node or reconnect an existing one.

        Args:
            websocket: WebSocket connection
            node_id: Unique node identifier from the adapter
            platform: Platform name (discord, cli, slack)
            capabilities: List of supported features
            metadata: Additional adapter info
            session_id: Previous session ID for reconnection

        Returns:
            Tuple of (session_id, is_reconnection)
        """
        async with self._lock:
            is_reconnection = False

            # Check for reconnection
            if session_id and session_id in self._sessions:
                old_node_id = self._sessions[session_id]
                if old_node_id in self._nodes:
                    old_node = self._nodes[old_node_id]
                    # Clean up old connection
                    if old_node.websocket in self._websockets:
                        del self._websockets[old_node.websocket]
                    del self._nodes[old_node_id]
                    logger.info(f"Node {node_id} reconnecting with session {session_id}")
                    is_reconnection = True

            # Generate new session ID if not reconnecting
            if not session_id or not is_reconnection:
                session_id = f"gw-{uuid.uuid4().hex[:12]}"

            # Create node connection
            node = NodeConnection(
                node_id=node_id,
                session_id=session_id,
                platform=platform,
                websocket=websocket,
                capabilities=capabilities or [],
                metadata=metadata or {},
            )

            # Register in all maps
            self._nodes[node_id] = node
            self._sessions[session_id] = node_id
            self._websockets[websocket] = node_id

            logger.info(
                f"Registered node {node_id} ({platform}) with session {session_id}, "
                f"capabilities={capabilities or []}"
            )

            return session_id, is_reconnection

    async def unregister(self, websocket: WebSocketServerProtocol) -> str | None:
        """Unregister a node by its WebSocket connection.

        Args:
            websocket: The WebSocket that disconnected

        Returns:
            The node_id that was unregistered, or None
        """
        async with self._lock:
            node_id = self._websockets.pop(websocket, None)
            if node_id and node_id in self._nodes:
                node = self._nodes.pop(node_id)
                # Keep session_id -> node_id mapping for reconnection
                logger.info(f"Unregistered node {node_id} (session {node.session_id} preserved)")
                return node_id
            return None

    async def get_node(self, node_id: str) -> NodeConnection | None:
        """Get a node by ID."""
        async with self._lock:
            return self._nodes.get(node_id)

    async def get_node_by_websocket(self, websocket: WebSocketServerProtocol) -> NodeConnection | None:
        """Get a node by its WebSocket connection."""
        async with self._lock:
            node_id = self._websockets.get(websocket)
            if node_id:
                return self._nodes.get(node_id)
            return None

    async def get_nodes_by_platform(self, platform: str) -> list[NodeConnection]:
        """Get all nodes for a specific platform."""
        async with self._lock:
            return [n for n in self._nodes.values() if n.platform == platform]

    async def update_ping(self, websocket: WebSocketServerProtocol) -> None:
        """Update the last ping time for a node."""
        async with self._lock:
            node_id = self._websockets.get(websocket)
            if node_id and node_id in self._nodes:
                self._nodes[node_id].last_ping = datetime.now()

    async def get_all_nodes(self) -> list[NodeConnection]:
        """Get all connected nodes."""
        async with self._lock:
            return list(self._nodes.values())

    async def get_stats(self) -> dict[str, Any]:
        """Get registry statistics."""
        async with self._lock:
            by_platform: dict[str, int] = {}
            for node in self._nodes.values():
                by_platform[node.platform] = by_platform.get(node.platform, 0) + 1

            return {
                "total_nodes": len(self._nodes),
                "preserved_sessions": len(self._sessions),
                "by_platform": by_platform,
            }


class SessionManager:
    """Manages user sessions across adapter nodes.

    Handles:
    - User session creation and lookup
    - Session state for reconnection
    - Channel/user routing to nodes
    """

    def __init__(self, node_registry: NodeRegistry) -> None:
        self._node_registry = node_registry
        self._sessions: dict[str, UserSession] = {}  # "user_id:channel_id" -> UserSession
        self._lock = asyncio.Lock()

    def _session_key(self, user_id: str, channel_id: str) -> str:
        """Generate session key from user and channel."""
        return f"{user_id}:{channel_id}"

    async def get_or_create_session(
        self,
        user_id: str,
        channel_id: str,
        node_id: str,
    ) -> UserSession:
        """Get or create a session for a user in a channel.

        Args:
            user_id: The user's ID
            channel_id: The channel ID
            node_id: The adapter node handling this session

        Returns:
            The user's session
        """
        key = self._session_key(user_id, channel_id)

        async with self._lock:
            if key in self._sessions:
                session = self._sessions[key]
                session.node_id = node_id  # Update node in case of reconnection
                session.touch()
                return session

            session = UserSession(
                user_id=user_id,
                channel_id=channel_id,
                node_id=node_id,
            )
            self._sessions[key] = session
            logger.debug(f"Created session for user {user_id} in channel {channel_id}")
            return session

    async def get_session(self, user_id: str, channel_id: str) -> UserSession | None:
        """Get an existing session."""
        key = self._session_key(user_id, channel_id)
        async with self._lock:
            return self._sessions.get(key)

    async def update_session(
        self,
        user_id: str,
        channel_id: str,
        **kwargs: Any,
    ) -> UserSession | None:
        """Update session attributes.

        Args:
            user_id: The user's ID
            channel_id: The channel ID
            **kwargs: Attributes to update (thread_id, project_id, context, etc.)

        Returns:
            Updated session or None if not found
        """
        key = self._session_key(user_id, channel_id)
        async with self._lock:
            session = self._sessions.get(key)
            if session:
                for attr, value in kwargs.items():
                    if hasattr(session, attr):
                        setattr(session, attr, value)
                session.touch()
            return session

    async def set_active_request(
        self,
        user_id: str,
        channel_id: str,
        request_id: str | None,
    ) -> None:
        """Set or clear the active request for a session."""
        key = self._session_key(user_id, channel_id)
        async with self._lock:
            if key in self._sessions:
                self._sessions[key].active_request_id = request_id
                self._sessions[key].touch()

    async def get_sessions_for_node(self, node_id: str) -> list[UserSession]:
        """Get all sessions being handled by a node."""
        async with self._lock:
            return [s for s in self._sessions.values() if s.node_id == node_id]

    async def cleanup_stale_sessions(
        self,
        max_age_minutes: int = 60,
        finalize_emotional: bool = True,
    ) -> int:
        """Remove sessions that have been inactive too long.

        Optionally finalizes emotional context for each stale session before removal.

        Args:
            max_age_minutes: Maximum inactivity in minutes
            finalize_emotional: Whether to finalize emotional context

        Returns:
            Number of sessions removed
        """
        from datetime import timedelta

        cutoff = datetime.now() - timedelta(minutes=max_age_minutes)
        removed = 0

        async with self._lock:
            stale_keys = [key for key, session in self._sessions.items() if session.last_activity < cutoff]

            for key in stale_keys:
                session = self._sessions[key]

                # Finalize emotional context before removing
                if finalize_emotional:
                    await self._finalize_session_emotional_context(session)

                del self._sessions[key]
                removed += 1

        if removed:
            logger.info(f"Cleaned up {removed} stale session(s)")
        return removed

    async def _finalize_session_emotional_context(self, session: UserSession) -> None:
        """Finalize emotional context for a session before removal.

        Args:
            session: The session being finalized
        """
        try:
            from clara_core.emotional_context import (
                finalize_conversation_emotional_context,
                get_conversation_sentiments,
                has_pending_emotional_context,
            )

            if not has_pending_emotional_context(session.user_id, session.channel_id):
                return

            # Get channel info from context if available
            channel_name = session.context.get("channel_name", f"channel-{session.channel_id}")
            is_dm = session.context.get("is_dm", False)

            # Use placeholder energy and summary if not available
            energy = session.context.get("energy", "neutral")
            summary = session.context.get("last_topic", "general conversation")

            finalize_conversation_emotional_context(
                user_id=session.user_id,
                channel_id=session.channel_id,
                channel_name=channel_name,
                is_dm=is_dm,
                energy=energy,
                summary=summary,
            )

            # Extract topics from conversation
            await self._extract_session_topics(session, channel_name, is_dm)

            logger.debug(f"Finalized emotional context for user {session.user_id} " f"in channel {session.channel_id}")

        except ImportError:
            logger.debug("Emotional context module not available")
        except Exception as e:
            logger.warning(f"Failed to finalize emotional context: {e}")

    async def _extract_session_topics(
        self,
        session: UserSession,
        channel_name: str,
        is_dm: bool,
    ) -> None:
        """Extract and store topics from the session's conversation.

        Args:
            session: The session being finalized
            channel_name: Human-readable channel name
            is_dm: Whether this is a DM
        """
        try:
            from clara_core.emotional_context import get_conversation_sentiments
            from clara_core.topic_recurrence import extract_and_store_topics

            # Fetch recent messages from database for this session
            conversation_text = await self._fetch_session_conversation(session)
            if not conversation_text or len(conversation_text) < 50:
                return

            # Get average sentiment
            sentiments = get_conversation_sentiments(session.user_id, session.channel_id)
            avg_sentiment = sum(sentiments) / len(sentiments) if sentiments else 0.0

            # Create async LLM callable
            async def llm_call(messages):
                from clara_core import ModelTier, make_llm

                llm = make_llm(tier=ModelTier.LOW)
                return llm(messages)

            await extract_and_store_topics(
                user_id=session.user_id,
                channel_id=session.channel_id,
                channel_name=channel_name,
                is_dm=is_dm,
                conversation_text=conversation_text,
                conversation_sentiment=avg_sentiment,
                llm_call=llm_call,
            )

            logger.debug(f"Extracted topics for session {session.user_id}:{session.channel_id}")

        except ImportError:
            logger.debug("Topic extraction module not available")
        except Exception as e:
            logger.debug(f"Failed to extract topics: {e}")

    async def _fetch_session_conversation(self, session: UserSession) -> str:
        """Fetch recent conversation text for a session from the database.

        Args:
            session: The session to fetch conversation for

        Returns:
            Formatted conversation text
        """
        try:
            from db import SessionLocal
            from db.models import Message
            from db.models import Session as DBSession

            db = SessionLocal()
            try:
                # Find the database session if we have a thread_id
                if not session.thread_id:
                    return ""

                # Get recent messages from this session
                messages = (
                    db.query(Message)
                    .filter(Message.session_id == session.thread_id)
                    .order_by(Message.created_at.desc())
                    .limit(20)
                    .all()
                )

                if not messages:
                    return ""

                # Format as conversation text
                lines = []
                for msg in reversed(messages):  # Chronological order
                    role = "Clara" if msg.role == "assistant" else "User"
                    content = msg.content[:500] if msg.content else ""
                    lines.append(f"{role}: {content}")

                return "\n".join(lines)

            finally:
                db.close()

        except Exception as e:
            logger.debug(f"Failed to fetch conversation: {e}")
            return ""

    async def get_stats(self) -> dict[str, Any]:
        """Get session statistics."""
        async with self._lock:
            active_requests = sum(1 for s in self._sessions.values() if s.active_request_id)
            return {
                "total_sessions": len(self._sessions),
                "active_requests": active_requests,
            }
