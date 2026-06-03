"""Game backend for Clara's Game Room.

An HTTP-served engine module (not a platform adapter): the gateway mounts
`api.router` at /api/v1/game and the web-UI calls it. Lives in the engine.

Module structure:
- engine.py: Core game logic, models, and LLM integration
- api.py: FastAPI router with /move endpoint
"""

from mypalclara.core.game.api import router
from mypalclara.core.game.engine import (
    GameMoveRequest,
    GameMoveResponse,
    get_clara_move,
)

__all__ = [
    "GameMoveRequest",
    "GameMoveResponse",
    "get_clara_move",
    "router",
]
