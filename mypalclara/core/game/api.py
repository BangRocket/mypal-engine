"""Game API router — thin FastAPI layer over game engine."""

from __future__ import annotations

from fastapi import APIRouter, Header

from mypalclara.core.game.engine import (
    GameMoveRequest,
    GameMoveResponse,
    _load_personality_text,
    _verify_api_key,
    get_clara_move,
)

router = APIRouter()


@router.post("/move", response_model=GameMoveResponse)
async def game_move(
    request: GameMoveRequest,
    x_game_api_key: str | None = Header(None),
) -> GameMoveResponse:
    """Get Clara's next move and commentary for a game."""
    _verify_api_key(x_game_api_key)
    personality_text = _load_personality_text(request.personality)
    result = await get_clara_move(request, personality_text)
    return GameMoveResponse(**result)
