"""Tests for the target message classifier in the gateway processor."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import mypalclara.gateway.processor as processor_module
from mypalclara.gateway.processor import MessageProcessor
from mypalclara.gateway.protocol import ChannelInfo, MessageRequest, UserInfo


def make_request(
    content: str = "Hello",
    channel_type: str = "server",
    channel_id: str = "channel-1",
    user_name: str = "TestUser",
    metadata: dict | None = None,
    reply_chain: list | None = None,
) -> MessageRequest:
    """Create a test message request."""
    return MessageRequest(
        id="req-test",
        user=UserInfo(
            id="user-1",
            platform_id="user-1",
            name=user_name,
            display_name=user_name,
        ),
        channel=ChannelInfo(
            id=channel_id,
            type=channel_type,
        ),
        content=content,
        metadata=metadata or {},
        reply_chain=reply_chain or [],
    )


@pytest.fixture
def processor():
    """Create a processor instance (not initialized — we only test _classify_target)."""
    return MessageProcessor()


class TestTargetClassifierRules:
    """Layer 1: Deterministic rule tests — no LLM call should be made."""

    @pytest.mark.asyncio
    async def test_dm_always_classified_as_clara(self, processor):
        """DMs are always for Clara, regardless of content."""
        request = make_request(content="hey bob", channel_type="dm")

        with patch("mypalclara.config.bot.BOT_NAME", "Clara"):
            result = await processor._classify_target(request)

        assert result == "CLARA"

    @pytest.mark.asyncio
    async def test_mention_always_classified_as_clara(self, processor):
        """Explicit @mention routes to Clara."""
        request = make_request(
            content="what do you think?",
            metadata={"is_mention": True},
        )

        with patch("mypalclara.config.bot.BOT_NAME", "Clara"):
            result = await processor._classify_target(request)

        assert result == "CLARA"

    @pytest.mark.asyncio
    async def test_name_in_message_classified_as_clara(self, processor):
        """Bot name appearing in message routes to Clara."""
        request = make_request(content="hey Clara, what time is it?")

        with patch("mypalclara.config.bot.BOT_NAME", "Clara"):
            result = await processor._classify_target(request)

        assert result == "CLARA"

    @pytest.mark.asyncio
    async def test_name_case_insensitive(self, processor):
        """Bot name matching is case-insensitive."""
        request = make_request(content="CLARA help me")

        with patch("mypalclara.config.bot.BOT_NAME", "Clara"):
            result = await processor._classify_target(request)

        assert result == "CLARA"

    @pytest.mark.asyncio
    async def test_reply_to_clara_classified_as_clara(self, processor):
        """Reply to Clara's message routes to Clara."""
        request = make_request(
            content="yes, that's right",
            reply_chain=[
                {"role": "user", "content": "what is 2+2?"},
                {"role": "assistant", "content": "4"},
            ],
        )

        with patch("mypalclara.config.bot.BOT_NAME", "Clara"):
            result = await processor._classify_target(request)

        assert result == "CLARA"

    @pytest.mark.asyncio
    async def test_reply_to_other_user_falls_through(self, processor):
        """Reply to another user's message falls through to LLM layer."""
        request = make_request(
            content="I agree with you",
            reply_chain=[
                {"role": "user", "content": "I think we should go hiking"},
            ],
        )

        # Mock the LLM layer to avoid actual calls
        with (
            patch("mypalclara.config.bot.BOT_NAME", "Clara"),
            patch.object(processor, "_get_channel_context", return_value=[]),
            patch("mypalclara.core.make_llm") as mock_make_llm,
            patch("mypalclara.core.ModelTier"),
        ):
            mock_llm = MagicMock(return_value="OTHER")
            mock_make_llm.return_value = mock_llm

            result = await processor._classify_target(request)

        assert result == "OTHER"


class TestTargetClassifierLLM:
    """Layer 2: LLM fallback tests."""

    @pytest.mark.asyncio
    async def test_llm_returns_clara(self, processor):
        """LLM returning CLARA routes to Clara."""
        request = make_request(content="can someone help me with this?")

        with (
            patch("mypalclara.config.bot.BOT_NAME", "Clara"),
            patch.object(processor, "_get_channel_context", return_value=[]),
            patch("mypalclara.core.make_llm") as mock_make_llm,
            patch("mypalclara.core.ModelTier"),
        ):
            mock_llm = MagicMock(return_value="CLARA")
            mock_make_llm.return_value = mock_llm

            result = await processor._classify_target(request)

        assert result == "CLARA"

    @pytest.mark.asyncio
    async def test_llm_returns_other(self, processor):
        """LLM returning OTHER skips processing."""
        request = make_request(content="@bob what did you think of the movie?")

        with (
            patch("mypalclara.config.bot.BOT_NAME", "Clara"),
            patch.object(processor, "_get_channel_context", return_value=[]),
            patch("mypalclara.core.make_llm") as mock_make_llm,
            patch("mypalclara.core.ModelTier"),
        ):
            mock_llm = MagicMock(return_value="OTHER")
            mock_make_llm.return_value = mock_llm

            result = await processor._classify_target(request)

        assert result == "OTHER"

    @pytest.mark.asyncio
    async def test_unrecognized_response_without_active_clara_returns_other(self, processor):
        """Unrecognized LLM response defaults to OTHER when Clara isn't active."""
        request = make_request(content="that's interesting")

        with (
            patch("mypalclara.config.bot.BOT_NAME", "Clara"),
            patch.object(processor, "_get_channel_context", return_value=[]),
            patch("mypalclara.core.make_llm") as mock_make_llm,
            patch("mypalclara.core.ModelTier"),
        ):
            mock_llm = MagicMock(return_value="MAYBE")
            mock_make_llm.return_value = mock_llm

            result = await processor._classify_target(request)

        assert result == "OTHER"

    @pytest.mark.asyncio
    async def test_unrecognized_response_with_active_clara_returns_clara(self, processor):
        """Unrecognized LLM response defaults to CLARA when she's an active participant."""
        request = make_request(content="that's interesting")

        # Mock a channel context where Clara recently spoke
        mock_msg = MagicMock()
        mock_msg.role = "assistant"
        mock_msg.user_id = None
        mock_msg.content = "I think so too!"

        with (
            patch("mypalclara.config.bot.BOT_NAME", "Clara"),
            patch.object(processor, "_get_channel_context", return_value=[mock_msg]),
            patch("mypalclara.core.make_llm") as mock_make_llm,
            patch("mypalclara.core.ModelTier"),
        ):
            mock_llm = MagicMock(return_value="MAYBE")
            mock_make_llm.return_value = mock_llm

            result = await processor._classify_target(request)

        assert result == "CLARA"

    @pytest.mark.asyncio
    async def test_llm_failure_defaults_to_clara(self, processor):
        """If LLM call fails, default to CLARA (don't silently drop messages)."""
        request = make_request(content="something something")

        with (
            patch("mypalclara.config.bot.BOT_NAME", "Clara"),
            patch.object(processor, "_get_channel_context", return_value=[]),
            patch("mypalclara.core.make_llm", side_effect=RuntimeError("API down")),
            patch("mypalclara.core.ModelTier"),
        ):
            result = await processor._classify_target(request)

        assert result == "CLARA"


class TestTargetClassifierIntegration:
    """Integration tests for how the classifier hooks into process()."""

    @pytest.mark.asyncio
    async def test_other_target_skips_processing(self, processor):
        """When classifier returns OTHER, process() sends empty ResponseEnd and returns."""
        request = make_request(content="hey bob, what's up?")
        mock_ws = AsyncMock()

        with (
            patch.object(processor, "_classify_target", return_value="OTHER"),
            patch.object(processor_module, "TARGET_CLASSIFIER_ENABLED", True),
        ):
            await processor.process(request, mock_ws, MagicMock())

        # Should have sent exactly one message: ResponseEnd with empty text
        assert mock_ws.send.call_count == 1
        import json

        sent = json.loads(mock_ws.send.call_args[0][0])
        assert sent["type"] == "response_end"
        assert sent["full_text"] == ""

    @pytest.mark.asyncio
    async def test_dm_skips_classifier_entirely(self, processor):
        """DMs bypass the classifier — process() should not call _classify_target."""
        request = make_request(content="hello", channel_type="dm")
        mock_ws = AsyncMock()

        with (
            patch.object(processor, "_classify_target") as mock_classify,
            patch.object(processor_module, "TARGET_CLASSIFIER_ENABLED", True),
            patch.object(processor, "_initialized", True),
            patch.object(processor, "_build_context", new_callable=AsyncMock) as mock_ctx,
            patch.object(processor, "_llm_orchestrator") as mock_orch,
            patch.object(processor, "_tool_executor") as mock_tools,
            patch.object(processor, "_store_messages_db", new_callable=AsyncMock),
            patch.object(processor, "_prepare_file_data", new_callable=AsyncMock, return_value=[]),
            patch.object(processor, "_background_memory_ops", new_callable=AsyncMock),
        ):
            mock_ctx.return_value = {
                "messages": [],
                "user_id": "user-1",
                "channel_id": "channel-1",
                "is_dm": True,
                "user_mems": [],
                "proj_mems": [],
                "participants": [],
                "db_session_id": "sess-1",
                "user_content": "hello",
                "fired_intentions": [],
            }

            async def fake_generate(**kwargs):
                yield {"type": "complete", "text": "hi there", "tool_count": 0, "files": []}

            mock_orch.generate_with_tools = fake_generate
            mock_tools.get_all_tools.return_value = []

            # Mock server.node_registry
            mock_server = MagicMock()
            mock_node = MagicMock()
            mock_node.capabilities = []
            mock_server.node_registry.get_node_by_websocket = AsyncMock(return_value=mock_node)

            await processor.process(request, mock_ws, mock_server)

        # _classify_target should NOT have been called for a DM
        mock_classify.assert_not_called()

    @pytest.mark.asyncio
    async def test_classifier_disabled_processes_all(self, processor):
        """When TARGET_CLASSIFIER is disabled, all messages are processed."""
        request = make_request(content="hey bob, what's up?")
        mock_ws = AsyncMock()

        with (
            patch.object(processor, "_classify_target") as mock_classify,
            patch.object(processor_module, "TARGET_CLASSIFIER_ENABLED", False),
            patch.object(processor, "_initialized", True),
            patch.object(processor, "_build_context", new_callable=AsyncMock) as mock_ctx,
            patch.object(processor, "_llm_orchestrator") as mock_orch,
            patch.object(processor, "_tool_executor") as mock_tools,
            patch.object(processor, "_store_messages_db", new_callable=AsyncMock),
            patch.object(processor, "_prepare_file_data", new_callable=AsyncMock, return_value=[]),
            patch.object(processor, "_background_memory_ops", new_callable=AsyncMock),
        ):
            mock_ctx.return_value = {
                "messages": [],
                "user_id": "user-1",
                "channel_id": "channel-1",
                "is_dm": False,
                "user_mems": [],
                "proj_mems": [],
                "participants": [],
                "db_session_id": "sess-1",
                "user_content": "hey bob",
                "fired_intentions": [],
            }

            async def fake_generate(**kwargs):
                yield {"type": "complete", "text": "response", "tool_count": 0, "files": []}

            mock_orch.generate_with_tools = fake_generate
            mock_tools.get_all_tools.return_value = []

            mock_server = MagicMock()
            mock_node = MagicMock()
            mock_node.capabilities = []
            mock_server.node_registry.get_node_by_websocket = AsyncMock(return_value=mock_node)

            await processor.process(request, mock_ws, mock_server)

        # Classifier should NOT have been called when disabled
        mock_classify.assert_not_called()

    @pytest.mark.asyncio
    async def test_mention_skips_classifier(self, processor):
        """Messages with is_mention=True bypass the classifier."""
        request = make_request(
            content="what do you think?",
            metadata={"is_mention": True},
        )
        mock_ws = AsyncMock()

        with (
            patch.object(processor, "_classify_target") as mock_classify,
            patch.object(processor_module, "TARGET_CLASSIFIER_ENABLED", True),
            patch.object(processor, "_initialized", True),
            patch.object(processor, "_build_context", new_callable=AsyncMock) as mock_ctx,
            patch.object(processor, "_llm_orchestrator") as mock_orch,
            patch.object(processor, "_tool_executor") as mock_tools,
            patch.object(processor, "_store_messages_db", new_callable=AsyncMock),
            patch.object(processor, "_prepare_file_data", new_callable=AsyncMock, return_value=[]),
            patch.object(processor, "_background_memory_ops", new_callable=AsyncMock),
        ):
            mock_ctx.return_value = {
                "messages": [],
                "user_id": "user-1",
                "channel_id": "channel-1",
                "is_dm": False,
                "user_mems": [],
                "proj_mems": [],
                "participants": [],
                "db_session_id": "sess-1",
                "user_content": "what do you think?",
                "fired_intentions": [],
            }

            async def fake_generate(**kwargs):
                yield {"type": "complete", "text": "I think...", "tool_count": 0, "files": []}

            mock_orch.generate_with_tools = fake_generate
            mock_tools.get_all_tools.return_value = []

            mock_server = MagicMock()
            mock_node = MagicMock()
            mock_node.capabilities = []
            mock_server.node_registry.get_node_by_websocket = AsyncMock(return_value=mock_node)

            await processor.process(request, mock_ws, mock_server)

        # Classifier should NOT have been called for a mention
        mock_classify.assert_not_called()
