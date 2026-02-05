"""Tests for gateway session management."""

import asyncio
from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest

from mypalclara.gateway.session import NodeConnection, NodeRegistry, SessionManager, UserSession


@pytest.fixture
def node_registry():
    """Create a fresh node registry."""
    return NodeRegistry()


@pytest.fixture
def session_manager(node_registry):
    """Create a fresh session manager."""
    return SessionManager(node_registry)


@pytest.fixture
def mock_websocket():
    """Create a mock WebSocket."""
    ws = MagicMock()

    async def mock_send(*args, **kwargs):
        return None

    ws.send = MagicMock(side_effect=mock_send)
    return ws


class TestNodeRegistry:
    """Tests for NodeRegistry."""

    @pytest.mark.asyncio
    async def test_register_node(self, node_registry, mock_websocket):
        """Should register a new node and return session ID."""
        session_id, is_reconnect = await node_registry.register(
            websocket=mock_websocket,
            node_id="node-1",
            platform="discord",
            capabilities=["streaming", "attachments"],
        )

        assert session_id.startswith("gw-")
        assert is_reconnect is False

        node = await node_registry.get_node("node-1")
        assert node is not None
        assert node.platform == "discord"
        assert "streaming" in node.capabilities

    @pytest.mark.asyncio
    async def test_reconnect_while_connected(self, node_registry, mock_websocket):
        """Should recognize reconnection when old node is still connected."""
        mock_websocket2 = MagicMock()

        session_id1, _ = await node_registry.register(
            websocket=mock_websocket,
            node_id="node-1",
            platform="discord",
        )

        # Reconnect with same session ID (old node still connected)
        session_id2, is_reconnect = await node_registry.register(
            websocket=mock_websocket2,
            node_id="node-1-new",
            platform="discord",
            session_id=session_id1,
        )

        assert session_id2 == session_id1
        assert is_reconnect is True

    @pytest.mark.asyncio
    async def test_unregister_then_register_new_session(self, node_registry, mock_websocket):
        """After unregister, new registration gets new session."""
        session_id, _ = await node_registry.register(
            websocket=mock_websocket,
            node_id="node-1",
            platform="discord",
        )

        node_id = await node_registry.unregister(mock_websocket)

        assert node_id == "node-1"
        # Node should be gone
        node = await node_registry.get_node("node-1")
        assert node is None

        # After unregister, reconnecting with old session creates new session
        # (reconnection requires old node to still be connected)
        mock_websocket2 = MagicMock()
        session_id2, is_reconnect = await node_registry.register(
            websocket=mock_websocket2,
            node_id="node-1-new",
            platform="discord",
            session_id=session_id,
        )
        # After unregister, the old node is gone, so it's not a reconnection
        assert is_reconnect is False
        assert session_id2 != session_id  # New session

    @pytest.mark.asyncio
    async def test_get_nodes_by_platform(self, node_registry):
        """Should filter nodes by platform."""
        ws1 = MagicMock()
        ws2 = MagicMock()
        ws3 = MagicMock()

        await node_registry.register(ws1, "discord-1", "discord")
        await node_registry.register(ws2, "discord-2", "discord")
        await node_registry.register(ws3, "teams-1", "teams")

        discord_nodes = await node_registry.get_nodes_by_platform("discord")
        teams_nodes = await node_registry.get_nodes_by_platform("teams")

        assert len(discord_nodes) == 2
        assert len(teams_nodes) == 1

    @pytest.mark.asyncio
    async def test_stats(self, node_registry):
        """Should return correct statistics."""
        ws1 = MagicMock()
        ws2 = MagicMock()

        await node_registry.register(ws1, "discord-1", "discord")
        await node_registry.register(ws2, "teams-1", "teams")

        stats = await node_registry.get_stats()

        assert stats["total_nodes"] == 2
        assert stats["by_platform"]["discord"] == 1
        assert stats["by_platform"]["teams"] == 1


class TestSessionManager:
    """Tests for SessionManager."""

    @pytest.mark.asyncio
    async def test_create_session(self, session_manager):
        """Should create a new session."""
        session = await session_manager.get_or_create_session(
            user_id="user-1",
            channel_id="channel-1",
            node_id="node-1",
        )

        assert session.user_id == "user-1"
        assert session.channel_id == "channel-1"
        assert session.node_id == "node-1"

    @pytest.mark.asyncio
    async def test_get_existing_session(self, session_manager):
        """Should return existing session."""
        session1 = await session_manager.get_or_create_session(
            user_id="user-1",
            channel_id="channel-1",
            node_id="node-1",
        )
        session1.thread_id = "thread-123"

        session2 = await session_manager.get_or_create_session(
            user_id="user-1",
            channel_id="channel-1",
            node_id="node-2",
        )

        # Should be same session with updated node
        assert session2.thread_id == "thread-123"
        assert session2.node_id == "node-2"

    @pytest.mark.asyncio
    async def test_different_users_different_sessions(self, session_manager):
        """Different users should have different sessions."""
        session1 = await session_manager.get_or_create_session(
            user_id="user-1",
            channel_id="channel-1",
            node_id="node-1",
        )
        session2 = await session_manager.get_or_create_session(
            user_id="user-2",
            channel_id="channel-1",
            node_id="node-1",
        )

        assert session1 is not session2

    @pytest.mark.asyncio
    async def test_update_session(self, session_manager):
        """Should update session attributes."""
        await session_manager.get_or_create_session(
            user_id="user-1",
            channel_id="channel-1",
            node_id="node-1",
        )

        updated = await session_manager.update_session(
            user_id="user-1",
            channel_id="channel-1",
            thread_id="thread-456",
            project_id="project-789",
        )

        assert updated is not None
        assert updated.thread_id == "thread-456"
        assert updated.project_id == "project-789"

    @pytest.mark.asyncio
    async def test_set_active_request(self, session_manager):
        """Should track active request."""
        await session_manager.get_or_create_session(
            user_id="user-1",
            channel_id="channel-1",
            node_id="node-1",
        )

        await session_manager.set_active_request(
            user_id="user-1",
            channel_id="channel-1",
            request_id="req-123",
        )

        session = await session_manager.get_session("user-1", "channel-1")
        assert session.active_request_id == "req-123"

    @pytest.mark.asyncio
    async def test_cleanup_stale_sessions(self, session_manager):
        """Should clean up sessions that have been inactive."""
        session = await session_manager.get_or_create_session(
            user_id="user-1",
            channel_id="channel-1",
            node_id="node-1",
        )

        # Make session appear old
        session.last_activity = datetime.now() - timedelta(hours=2)

        removed = await session_manager.cleanup_stale_sessions(
            max_age_minutes=60,
            finalize_emotional=False,  # Skip emotional finalization for test
        )

        assert removed == 1

        # Session should be gone
        result = await session_manager.get_session("user-1", "channel-1")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_sessions_for_node(self, session_manager):
        """Should return all sessions for a node."""
        await session_manager.get_or_create_session("user-1", "channel-1", "node-1")
        await session_manager.get_or_create_session("user-2", "channel-2", "node-1")
        await session_manager.get_or_create_session("user-3", "channel-3", "node-2")

        node1_sessions = await session_manager.get_sessions_for_node("node-1")
        node2_sessions = await session_manager.get_sessions_for_node("node-2")

        assert len(node1_sessions) == 2
        assert len(node2_sessions) == 1

    @pytest.mark.asyncio
    async def test_stats(self, session_manager):
        """Should return correct statistics."""
        await session_manager.get_or_create_session("user-1", "channel-1", "node-1")
        await session_manager.get_or_create_session("user-2", "channel-2", "node-1")

        await session_manager.set_active_request("user-1", "channel-1", "req-1")

        stats = await session_manager.get_stats()

        assert stats["total_sessions"] == 2
        assert stats["active_requests"] == 1


class TestNodeConnection:
    """Tests for NodeConnection dataclass."""

    def test_supports_streaming(self):
        """Should detect streaming capability."""
        node = NodeConnection(
            node_id="node-1",
            session_id="session-1",
            platform="discord",
            websocket=MagicMock(),
            capabilities=["streaming", "attachments"],
        )

        assert node.supports_streaming is True
        assert node.supports_attachments is True

    def test_no_capabilities(self):
        """Should handle missing capabilities."""
        node = NodeConnection(
            node_id="node-1",
            session_id="session-1",
            platform="cli",
            websocket=MagicMock(),
            capabilities=[],
        )

        assert node.supports_streaming is False
        assert node.supports_attachments is False


class TestUserSession:
    """Tests for UserSession dataclass."""

    def test_touch_updates_activity(self):
        """Touch should update last_activity."""
        session = UserSession(
            user_id="user-1",
            channel_id="channel-1",
            node_id="node-1",
        )

        old_time = session.last_activity
        session.touch()

        assert session.last_activity >= old_time
