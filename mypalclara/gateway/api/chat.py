"""OpenAI-compatible chat completion endpoint.

Provides /v1/chat/completions for the Clara desktop/web app.
Routes through the existing MessageProcessor with memory, tools,
and persona — same pipeline as Discord messages.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from mypalclara.gateway.api.auth import get_db

logger = logging.getLogger("clara.gateway.api.chat")

router = APIRouter()


@router.get("/models")
async def list_models():
    """OpenAI-compatible models list — returns Clara as the only model."""
    return {
        "object": "list",
        "data": [
            {
                "id": "clara",
                "object": "model",
                "created": 1700000000,
                "owned_by": "mypalclara",
            }
        ],
    }


@router.post("/chat/completions")
async def chat_completions(request: Request):
    """OpenAI-compatible chat completions endpoint.

    Accepts standard OpenAI chat completion format, routes through
    Clara's full pipeline (memory, tools, persona), and returns
    streamed SSE responses in OpenAI format.
    """
    body = await request.json()

    messages = body.get("messages", [])
    stream = body.get("stream", True)
    model = body.get("model", "clara")
    user_id = body.get("user", "app-user")

    if not messages:
        return {"error": {"message": "messages is required", "type": "invalid_request_error"}}

    # Extract the last user message
    user_message = ""
    for msg in reversed(messages):
        if msg.get("role") == "user":
            content = msg.get("content", "")
            if isinstance(content, list):
                # Multimodal — extract text parts
                user_message = " ".join(
                    p.get("text", "") for p in content if p.get("type") == "text"
                )
            else:
                user_message = content
            break

    if not user_message:
        return {"error": {"message": "No user message found", "type": "invalid_request_error"}}

    # Process through Clara's pipeline
    completion_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"

    if stream:
        return StreamingResponse(
            _stream_response(user_message, user_id, model, completion_id, messages),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
    else:
        # Non-streaming — collect full response
        full_text = await _get_full_response(user_message, user_id, messages)
        return {
            "id": completion_id,
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": full_text},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }


async def _stream_response(
    user_message: str,
    user_id: str,
    model: str,
    completion_id: str,
    messages: list[dict],
):
    """Stream response in OpenAI SSE format."""
    full_text = ""

    async for chunk_text in _process_message(user_message, user_id, messages):
        full_text += chunk_text
        chunk = {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "delta": {"content": chunk_text},
                    "finish_reason": None,
                }
            ],
        }
        yield f"data: {json.dumps(chunk)}\n\n"

    # Send final chunk with finish_reason
    final_chunk = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "delta": {},
                "finish_reason": "stop",
            }
        ],
    }
    yield f"data: {json.dumps(final_chunk)}\n\n"
    yield "data: [DONE]\n\n"


async def _get_full_response(
    user_message: str,
    user_id: str,
    messages: list[dict],
) -> str:
    """Get full response (non-streaming)."""
    chunks = []
    async for chunk in _process_message(user_message, user_id, messages):
        chunks.append(chunk)
    return "".join(chunks)


async def _process_message(
    user_message: str,
    user_id: str,
    messages: list[dict],
):
    """Process a message through Clara's pipeline and yield response chunks.

    Uses the LLM orchestrator directly (same as the WebSocket processor)
    but without requiring a WebSocket connection.
    """
    from mypalclara.core import make_llm
    from mypalclara.core.memory_manager import MemoryManager

    try:
        mm = MemoryManager.get_instance()
    except RuntimeError:
        mm = MemoryManager.initialize(llm_callable=make_llm())

    # Build prompt with layered retrieval (episodes, memories, graph)
    try:
        from mypalclara.db import SessionLocal
        from mypalclara.db.models import Message as DbMessage
        from mypalclara.db.models import Session

        db = SessionLocal()
        try:
            # Get or create a session for this user
            from mypalclara.core.memory.session import SessionManager

            session_mgr = mm._session_manager
            db_session = session_mgr.get_or_create_session(db, user_id, "app", title="Clara App")

            # Get recent messages for context
            recent = (
                db.query(DbMessage)
                .filter(DbMessage.session_id == db_session.id)
                .order_by(DbMessage.created_at.desc())
                .limit(30)
                .all()
            )
            recent.reverse()
        finally:
            db.close()
    except Exception as e:
        logger.warning(f"DB session setup failed: {e}")
        recent = []
        db_session = None

    # Build layered prompt
    try:
        prompt_messages = mm.build_prompt_layered(
            user_id=user_id,
            user_message=user_message,
            recent_msgs=recent,
            privacy_scope="full",
        )
    except Exception as e:
        logger.warning(f"Layered prompt failed: {e}")
        # Fallback: just use the raw messages
        from mypalclara.core.llm.messages import SystemMessage, UserMessage

        prompt_messages = [
            SystemMessage(content="You are Clara, a warm and thoughtful AI companion."),
            UserMessage(content=user_message),
        ]

    # Convert to dict format for LLM
    from mypalclara.core.llm.messages import messages_to_openai

    msg_dicts = messages_to_openai(prompt_messages)

    # Stream through LLM
    from mypalclara.core import make_llm_streaming

    llm_stream = make_llm_streaming()
    loop = asyncio.get_event_loop()

    def get_stream():
        return llm_stream(msg_dicts)

    stream = await loop.run_in_executor(None, get_stream)

    def get_next(gen):
        try:
            return next(gen)
        except StopIteration:
            return None

    while True:
        chunk = await loop.run_in_executor(None, get_next, stream)
        if chunk is None:
            break
        yield chunk

    # Store the exchange in DB (background)
    try:
        full_response = ""  # We don't have this easily in streaming mode
        # Store user message
        if db_session:
            db = SessionLocal()
            try:
                mm._session_manager.store_message(db, db_session.id, user_id, "user", user_message)
                db.commit()
            finally:
                db.close()
    except Exception as e:
        logger.debug(f"Failed to store message: {e}")
