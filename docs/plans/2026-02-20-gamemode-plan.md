# Game Mode Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a retro pixel-art games site at games.mypalclara.com where users play blackjack and checkers against Clara (and her alternate personalities Flo/Clarissa).

**Architecture:** Separate Rails 8 app with Inertia.js + React 19 frontend. Communicates with existing FastAPI backend via a new game API endpoint for Clara's moves, and a JWT redirect handshake for shared authentication.

**Tech Stack:** Rails 8.1, Ruby 3.4, Inertia.js, React 19, TypeScript, PostgreSQL, Action Cable, PixiJS/Canvas, Tailwind CSS

**Design doc:** `docs/plans/2026-02-20-gamemode-design.md`
**Sprite guidelines:** `docs/plans/2026-02-20-gamemode-sprite-guidelines.md`

---

## Phase 1: FastAPI — Game API Endpoint & Auth Redirect

These changes go in the existing MyPalClara codebase. They provide the two backend services the Rails app depends on: Clara's game move API and the auth redirect flow.

### Task 1: Game move API endpoint — test

**Files:**
- Create: `tests/web/api/test_game.py`

**Step 1: Write the failing test**

```python
"""Tests for the game move API endpoint."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    """Create a test client with the game router mounted."""
    from mypalclara.web.app import create_app
    app = create_app()
    return TestClient(app)


@pytest.fixture
def game_api_key(monkeypatch):
    """Set the game API key for auth."""
    monkeypatch.setenv("GAME_API_KEY", "test-secret-key")


class TestGameMoveEndpoint:
    def test_returns_move_and_commentary(self, client, game_api_key):
        """Clara should return a valid move from legal_moves plus commentary."""
        payload = {
            "game_type": "blackjack",
            "game_state": {"player_hand": ["A♠", "7♥"], "dealer_hand": ["K♦", "?"]},
            "legal_moves": ["hit", "stand"],
            "personality": "clara",
            "user_id": "test-user-123",
            "move_history": [],
        }
        with patch("mypalclara.web.api.game.get_clara_move") as mock_get_move:
            mock_get_move.return_value = {
                "move": {"type": "stand"},
                "commentary": "Playing it safe, huh?",
                "mood": "smug",
            }
            resp = client.post(
                "/api/v1/game/move",
                json=payload,
                headers={"X-Game-API-Key": "test-secret-key"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["move"]["type"] in ["hit", "stand"]
        assert "commentary" in data
        assert data["mood"] in ["idle", "happy", "nervous", "smug", "surprised", "defeated"]

    def test_rejects_missing_api_key(self, client, game_api_key):
        """Requests without API key should be rejected."""
        payload = {
            "game_type": "blackjack",
            "game_state": {},
            "legal_moves": ["hit", "stand"],
            "personality": "clara",
            "user_id": "test-user-123",
            "move_history": [],
        }
        resp = client.post("/api/v1/game/move", json=payload)
        assert resp.status_code == 401

    def test_rejects_invalid_api_key(self, client, game_api_key):
        """Requests with wrong API key should be rejected."""
        payload = {
            "game_type": "blackjack",
            "game_state": {},
            "legal_moves": ["hit", "stand"],
            "personality": "clara",
            "user_id": "test-user-123",
            "move_history": [],
        }
        resp = client.post(
            "/api/v1/game/move",
            json=payload,
            headers={"X-Game-API-Key": "wrong-key"},
        )
        assert resp.status_code == 401

    def test_validates_personality(self, client, game_api_key):
        """Only known personalities should be accepted."""
        payload = {
            "game_type": "blackjack",
            "game_state": {},
            "legal_moves": ["hit", "stand"],
            "personality": "nonexistent",
            "user_id": "test-user-123",
            "move_history": [],
        }
        resp = client.post(
            "/api/v1/game/move",
            json=payload,
            headers={"X-Game-API-Key": "test-secret-key"},
        )
        assert resp.status_code == 422
```

**Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/web/api/test_game.py -v`
Expected: FAIL — module `mypalclara.web.api.game` not found

**Step 3: Commit**

```bash
git add tests/web/api/test_game.py
git commit -m "test: add game move API endpoint tests"
```

### Task 2: Game move API endpoint — implementation

**Files:**
- Create: `mypalclara/web/api/game.py`
- Modify: `mypalclara/web/api/router.py`

**Step 1: Create the game API router**

```python
"""Game move API for Clara's Game Room."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, field_validator

logger = logging.getLogger(__name__)

router = APIRouter()

VALID_PERSONALITIES = {"clara", "flo", "clarissa"}
VALID_MOODS = {"idle", "happy", "nervous", "smug", "surprised", "defeated"}
PERSONALITIES_DIR = Path(__file__).parent.parent.parent.parent / "personalities"


class GameMoveRequest(BaseModel):
    game_type: str
    game_state: dict[str, Any]
    legal_moves: list[str]
    personality: str
    user_id: str
    move_history: list[dict[str, Any]] = []

    @field_validator("personality")
    @classmethod
    def validate_personality(cls, v: str) -> str:
        if v not in VALID_PERSONALITIES:
            msg = f"Unknown personality: {v}. Must be one of: {VALID_PERSONALITIES}"
            raise ValueError(msg)
        return v


class GameMoveResponse(BaseModel):
    move: dict[str, Any]
    commentary: str
    mood: str


def _verify_api_key(x_game_api_key: str | None = Header(None)) -> None:
    """Verify the game API key from the request header."""
    expected = os.getenv("GAME_API_KEY")
    if not expected or x_game_api_key != expected:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


def _load_personality_text(personality: str) -> str:
    """Load personality file content."""
    path = PERSONALITIES_DIR / f"{personality}.md"
    if not path.exists():
        logger.warning("Personality file not found: %s, using clara", path)
        path = PERSONALITIES_DIR / "clara.md"
    return path.read_text(encoding="utf-8").strip()


async def get_clara_move(
    request: GameMoveRequest,
    personality_text: str,
) -> dict[str, Any]:
    """Call the LLM to get Clara's game move and commentary.

    This function builds a game-specific prompt and calls the configured
    LLM provider to get a move selection and banter.
    """
    from clara_core.llm import make_llm

    llm = make_llm(tier="mid")

    # Fetch user memories for context
    user_memories = []
    try:
        from clara_core.memory import ROOK

        results = ROOK.search(
            f"playing {request.game_type}",
            user_id=request.user_id,
            agent_id="mypalclara",
            limit=5,
        )
        user_memories = [r.get("memory", "") for r in (results or []) if r.get("memory")]
    except Exception:
        logger.debug("Could not fetch user memories for game", exc_info=True)

    memory_context = ""
    if user_memories:
        memory_context = "\n\nWhat you know about this player:\n" + "\n".join(
            f"- {m}" for m in user_memories
        )

    history_text = ""
    if request.move_history:
        recent = request.move_history[-5:]
        history_text = "\n\nRecent moves:\n" + "\n".join(
            f"- {m}" for m in recent
        )

    prompt = f"""{personality_text}
{memory_context}

You are playing {request.game_type} against a player.
Current game state: {json.dumps(request.game_state, indent=2)}
{history_text}

Your legal moves: {json.dumps(request.legal_moves)}

Pick ONE move from the legal moves list. Provide commentary in character — trash talk, encouragement, nervousness, whatever fits. Also indicate your mood.

Respond with ONLY valid JSON (no markdown fences):
{{"move": {{"type": "<your chosen move>"}}, "commentary": "<your in-character reaction>", "mood": "<one of: idle, happy, nervous, smug, surprised, defeated>"}}"""

    try:
        messages = [{"role": "user", "content": prompt}]
        response = await llm.ainvoke(messages)
        content = response.content if hasattr(response, "content") else str(response)

        # Parse JSON from response
        result = json.loads(content)

        # Validate move is legal
        move_type = result.get("move", {}).get("type", "")
        if move_type not in request.legal_moves:
            logger.warning("Clara returned illegal move: %s, picking random", move_type)
            import random
            result["move"] = {"type": random.choice(request.legal_moves)}
            result["commentary"] = "Hmm, let me think... okay, I'll do this."

        # Validate mood
        if result.get("mood") not in VALID_MOODS:
            result["mood"] = "idle"

        return result

    except Exception:
        logger.exception("Failed to get LLM game move")
        import random
        return {
            "move": {"type": random.choice(request.legal_moves)},
            "commentary": "Give me a second... okay, here goes.",
            "mood": "nervous",
        }


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
```

**Step 2: Register the router**

Add to `mypalclara/web/api/router.py`:

```python
from mypalclara.web.api.game import router as game_router

api_router.include_router(game_router, prefix="/game", tags=["game"])
```

**Step 3: Run tests**

Run: `poetry run pytest tests/web/api/test_game.py -v`
Expected: All 4 tests PASS

**Step 4: Commit**

```bash
git add mypalclara/web/api/game.py mypalclara/web/api/router.py
git commit -m "feat: add game move API endpoint for Clara's Game Room"
```

### Task 3: Auth game redirect endpoint — test

**Files:**
- Create: `tests/web/auth/test_game_redirect.py`

**Step 1: Write the failing test**

```python
"""Tests for the game auth redirect endpoint."""

import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from mypalclara.web.app import create_app
    app = create_app()
    return TestClient(app, follow_redirects=False)


class TestGameRedirect:
    def test_redirects_authenticated_user(self, client):
        """Authenticated user should get redirected with a JWT."""
        # Mock an authenticated user
        from db.models import CanonicalUser

        mock_user = CanonicalUser(
            id="test-user-123",
            display_name="Joshua",
            avatar_url="https://example.com/avatar.png",
            status="active",
        )
        with patch("mypalclara.web.auth.dependencies.get_current_user", return_value=mock_user):
            resp = client.get(
                "/auth/game-redirect?redirect_uri=https://games.mypalclara.com/auth/callback",
                cookies={"access_token": "fake-valid-token"},
            )
        assert resp.status_code == 302
        location = resp.headers["location"]
        assert "games.mypalclara.com/auth/callback" in location
        assert "token=" in location

    def test_rejects_invalid_redirect_uri(self, client):
        """Should reject redirect URIs not on games.mypalclara.com."""
        from db.models import CanonicalUser

        mock_user = CanonicalUser(
            id="test-user-123",
            display_name="Joshua",
            status="active",
        )
        with patch("mypalclara.web.auth.dependencies.get_current_user", return_value=mock_user):
            resp = client.get(
                "/auth/game-redirect?redirect_uri=https://evil.com/steal",
                cookies={"access_token": "fake-valid-token"},
            )
        assert resp.status_code == 400
```

**Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/web/auth/test_game_redirect.py -v`
Expected: FAIL — endpoint not found (404)

**Step 3: Commit**

```bash
git add tests/web/auth/test_game_redirect.py
git commit -m "test: add game auth redirect endpoint tests"
```

### Task 4: Auth game redirect endpoint — implementation

**Files:**
- Modify: `mypalclara/web/auth/oauth.py`
- Modify: `mypalclara/web/auth/session.py`

**Step 1: Add game token creation to session.py**

Add this function to `mypalclara/web/auth/session.py`:

```python
def create_game_redirect_token(
    canonical_user_id: str,
    display_name: str,
    avatar_url: str | None,
    audience: str = "games.mypalclara.com",
) -> str:
    """Create a short-lived JWT for game site auth redirect.

    This token is only valid for 5 minutes and scoped to the games site.
    """
    config = get_web_config()
    now = datetime.now(timezone.utc)
    payload = {
        "sub": canonical_user_id,
        "name": display_name,
        "avatar": avatar_url,
        "aud": audience,
        "iat": now,
        "exp": now + timedelta(minutes=5),
    }
    return jwt.encode(payload, config.secret_key, algorithm=config.jwt_algorithm)
```

**Step 2: Add the redirect endpoint to oauth.py**

Add to `mypalclara/web/auth/oauth.py`:

```python
from urllib.parse import urlparse, urlencode, urljoin
from mypalclara.web.auth.session import create_game_redirect_token

ALLOWED_GAME_REDIRECT_HOSTS = {"games.mypalclara.com"}


@router.get("/game-redirect")
async def game_redirect(
    redirect_uri: str,
    user: CanonicalUser = Depends(get_approved_user),
):
    """Issue a short-lived JWT and redirect to the games site."""
    parsed = urlparse(redirect_uri)
    if parsed.hostname not in ALLOWED_GAME_REDIRECT_HOSTS:
        raise HTTPException(status_code=400, detail="Invalid redirect URI")

    token = create_game_redirect_token(
        canonical_user_id=user.id,
        display_name=user.display_name,
        avatar_url=user.avatar_url,
    )

    separator = "&" if "?" in redirect_uri else "?"
    redirect_url = f"{redirect_uri}{separator}token={token}"
    return RedirectResponse(url=redirect_url, status_code=302)
```

**Step 3: Run tests**

Run: `poetry run pytest tests/web/auth/test_game_redirect.py -v`
Expected: All tests PASS

**Step 4: Run all tests to check for regressions**

Run: `poetry run pytest tests/ -v --timeout=30`
Expected: No new failures

**Step 5: Lint**

Run: `poetry run ruff check . && poetry run ruff format .`

**Step 6: Commit**

```bash
git add mypalclara/web/auth/session.py mypalclara/web/auth/oauth.py
git commit -m "feat: add game auth redirect endpoint with short-lived JWT"
```

---

## Phase 2: Rails App Scaffold

This creates the new Rails application in a separate directory. The Rails app lives in its own repo/directory, not inside the MyPalClara Python codebase.

### Task 5: Create Rails app with Inertia + React

**Files:**
- Create: `~/Code/clara-games/` (new Rails app)

**Step 1: Install Ruby and Rails (if needed)**

```bash
# On the dev machine — install ruby 3.4.x via rbenv
rbenv install 3.4.8
rbenv local 3.4.8

# Install rails
gem install rails -v "~> 8.1"
```

**Step 2: Create the Rails app**

```bash
cd ~/Code
rails new clara-games \
  --database=postgresql \
  --skip-test \
  --skip-jbuilder \
  --skip-action-mailbox \
  --skip-action-text \
  --skip-active-storage
```

- `--skip-test` because we'll use RSpec
- Other skips remove features we don't need

**Step 3: Add Inertia.js and React**

Add to `Gemfile`:
```ruby
gem "inertia_rails", "~> 3.0"
```

Run:
```bash
cd ~/Code/clara-games
bundle install
```

Set up JS dependencies:
```bash
# If using jsbundling-rails (included by default in Rails 8):
bin/rails javascript:install:esbuild

# Add React + Inertia
npm install react react-dom @inertiajs/react @types/react @types/react-dom typescript
```

**Step 4: Configure Inertia**

Create `app/frontend/entrypoints/inertia.tsx`:
```tsx
import { createInertiaApp } from '@inertiajs/react'
import { createRoot } from 'react-dom/client'

createInertiaApp({
  resolve: (name: string) => {
    const pages = import.meta.glob('../pages/**/*.tsx', { eager: true })
    return pages[`../pages/${name}.tsx`]
  },
  setup({ el, App, props }) {
    createRoot(el).render(<App {...props} />)
  },
})
```

Create `app/frontend/pages/Lobby.tsx`:
```tsx
export default function Lobby() {
  return <h1>Clara's Game Room</h1>
}
```

**Step 5: Set up the root route**

Create `app/controllers/lobby_controller.rb`:
```ruby
class LobbyController < ApplicationController
  def index
    render inertia: "Lobby", props: {
      message: "Welcome to Clara's Game Room"
    }
  end
end
```

Add to `config/routes.rb`:
```ruby
Rails.application.routes.draw do
  root "lobby#index"
end
```

**Step 6: Create the layout**

Update `app/views/layouts/application.html.erb` to include the Inertia head tag and div.

**Step 7: Verify it runs**

```bash
bin/dev
```

Visit `http://localhost:3000` — should see "Clara's Game Room"

**Step 8: Initialize git and commit**

```bash
cd ~/Code/clara-games
git init
git add .
git commit -m "feat: scaffold Rails 8 app with Inertia.js + React 19"
```

### Task 6: Add RSpec and Tailwind

**Files:**
- Modify: `Gemfile`
- Create: `spec/rails_helper.rb`

**Step 1: Add testing and styling gems**

Add to `Gemfile`:
```ruby
group :development, :test do
  gem "rspec-rails", "~> 7.0"
  gem "factory_bot_rails"
end

gem "tailwindcss-rails"
```

**Step 2: Install and configure**

```bash
bundle install
bin/rails generate rspec:install
bin/rails tailwindcss:install
```

**Step 3: Verify RSpec runs**

```bash
bin/rspec
```

Expected: 0 examples, 0 failures

**Step 4: Commit**

```bash
git add .
git commit -m "chore: add RSpec, FactoryBot, and Tailwind CSS"
```

### Task 7: Add Action Cable for real-time

**Step 1: Verify Action Cable is included**

Action Cable ships with Rails 8 by default. Check `config/cable.yml` exists.

**Step 2: Configure for production**

Update `config/cable.yml`:
```yaml
development:
  adapter: async

production:
  adapter: redis
  url: <%= ENV.fetch("REDIS_URL", "redis://localhost:6379/1") %>
```

**Step 3: Commit**

```bash
git add config/cable.yml
git commit -m "chore: configure Action Cable for development and production"
```

---

## Phase 3: Database Models & Auth

### Task 8: User model — test

**Files:**
- Create: `spec/models/user_spec.rb`
- Create: `spec/factories/users.rb`

**Step 1: Write the failing test**

```ruby
# spec/models/user_spec.rb
require "rails_helper"

RSpec.describe User, type: :model do
  describe "validations" do
    it "requires canonical_user_id" do
      user = User.new(display_name: "Test")
      expect(user).not_to be_valid
      expect(user.errors[:canonical_user_id]).to include("can't be blank")
    end

    it "requires display_name" do
      user = User.new(canonical_user_id: "abc-123")
      expect(user).not_to be_valid
      expect(user.errors[:display_name]).to include("can't be blank")
    end

    it "enforces unique canonical_user_id" do
      create(:user, canonical_user_id: "abc-123")
      duplicate = build(:user, canonical_user_id: "abc-123")
      expect(duplicate).not_to be_valid
    end

    it "creates a valid user with required fields" do
      user = build(:user)
      expect(user).to be_valid
    end
  end
end
```

```ruby
# spec/factories/users.rb
FactoryBot.define do
  factory :user do
    sequence(:canonical_user_id) { |n| "user-#{n}" }
    display_name { "Test User" }
    avatar_url { nil }
  end
end
```

**Step 2: Run test to verify it fails**

Run: `bin/rspec spec/models/user_spec.rb`
Expected: FAIL — User model not found

**Step 3: Commit**

```bash
git add spec/models/user_spec.rb spec/factories/users.rb
git commit -m "test: add User model specs"
```

### Task 9: User model — implementation

**Step 1: Generate the model**

```bash
bin/rails generate model User \
  canonical_user_id:string:uniq \
  display_name:string \
  avatar_url:string
```

**Step 2: Add validations to model**

```ruby
# app/models/user.rb
class User < ApplicationRecord
  validates :canonical_user_id, presence: true, uniqueness: true
  validates :display_name, presence: true

  has_many :games, foreign_key: :created_by_id
  has_many :game_players
end
```

**Step 3: Run migration and tests**

```bash
bin/rails db:create db:migrate
bin/rspec spec/models/user_spec.rb
```

Expected: All PASS

**Step 4: Commit**

```bash
git add app/models/user.rb db/migrate/ spec/
git commit -m "feat: add User model with canonical_user_id"
```

### Task 10: Game model — test

**Files:**
- Create: `spec/models/game_spec.rb`
- Create: `spec/factories/games.rb`

**Step 1: Write the failing test**

```ruby
# spec/models/game_spec.rb
require "rails_helper"

RSpec.describe Game, type: :model do
  describe "validations" do
    it "requires game_type" do
      game = build(:game, game_type: nil)
      expect(game).not_to be_valid
    end

    it "requires state" do
      game = build(:game, state: nil)
      expect(game).not_to be_valid
    end

    it "only allows valid game_types" do
      expect(build(:game, game_type: "blackjack")).to be_valid
      expect(build(:game, game_type: "checkers")).to be_valid
      expect(build(:game, game_type: "poker")).not_to be_valid
    end

    it "only allows valid states" do
      expect(build(:game, state: "waiting")).to be_valid
      expect(build(:game, state: "in_progress")).to be_valid
      expect(build(:game, state: "resolved")).to be_valid
      expect(build(:game, state: "invalid")).not_to be_valid
    end
  end

  describe "associations" do
    it "belongs to a creator" do
      game = create(:game)
      expect(game.creator).to be_a(User)
    end

    it "has many game_players" do
      game = create(:game)
      expect(game.game_players).to eq([])
    end

    it "has many moves" do
      game = create(:game)
      expect(game.moves).to eq([])
    end
  end
end
```

```ruby
# spec/factories/games.rb
FactoryBot.define do
  factory :game do
    association :creator, factory: :user
    game_type { "blackjack" }
    state { "waiting" }
    game_data { {} }
    move_count { 0 }
  end
end
```

**Step 2: Run test to verify it fails**

Run: `bin/rspec spec/models/game_spec.rb`
Expected: FAIL — Game model not found

**Step 3: Commit**

```bash
git add spec/models/game_spec.rb spec/factories/games.rb
git commit -m "test: add Game model specs"
```

### Task 11: Game model — implementation

**Step 1: Generate the model**

```bash
bin/rails generate model Game \
  game_type:string \
  state:string \
  current_turn:string \
  game_data:jsonb \
  move_count:integer \
  created_by_id:references \
  started_at:datetime \
  finished_at:datetime
```

**Step 2: Fix migration** — change `created_by_id` foreign key to reference `users`:

In the migration file, ensure:
```ruby
t.references :created_by, null: false, foreign_key: { to_table: :users }
```

And add defaults:
```ruby
t.jsonb :game_data, default: {}, null: false
t.integer :move_count, default: 0, null: false
t.string :state, default: "waiting", null: false
```

**Step 3: Add validations**

```ruby
# app/models/game.rb
class Game < ApplicationRecord
  GAME_TYPES = %w[blackjack checkers].freeze
  STATES = %w[waiting in_progress resolved].freeze

  belongs_to :creator, class_name: "User", foreign_key: :created_by_id
  has_many :game_players, dependent: :destroy
  has_many :moves, dependent: :destroy

  validates :game_type, presence: true, inclusion: { in: GAME_TYPES }
  validates :state, presence: true, inclusion: { in: STATES }
end
```

**Step 4: Run migration and tests**

```bash
bin/rails db:migrate
bin/rspec spec/models/game_spec.rb
```

Expected: All PASS

**Step 5: Commit**

```bash
git add app/models/game.rb db/migrate/
git commit -m "feat: add Game model with types and state machine"
```

### Task 12: GamePlayer model — test

**Files:**
- Create: `spec/models/game_player_spec.rb`
- Create: `spec/factories/game_players.rb`

**Step 1: Write the failing test**

```ruby
# spec/models/game_player_spec.rb
require "rails_helper"

RSpec.describe GamePlayer, type: :model do
  describe "validations" do
    it "requires seat_position" do
      gp = build(:game_player, seat_position: nil)
      expect(gp).not_to be_valid
    end

    it "requires player_state" do
      gp = build(:game_player, player_state: nil)
      expect(gp).not_to be_valid
    end

    it "requires either user or ai_personality" do
      gp = build(:game_player, user: nil, ai_personality: nil)
      expect(gp).not_to be_valid
    end

    it "accepts a human player" do
      gp = build(:game_player, ai_personality: nil)
      expect(gp).to be_valid
    end

    it "accepts an AI player" do
      gp = build(:ai_game_player)
      expect(gp).to be_valid
    end

    it "validates ai_personality values" do
      expect(build(:ai_game_player, ai_personality: "flo")).to be_valid
      expect(build(:ai_game_player, ai_personality: "clarissa")).to be_valid
      expect(build(:ai_game_player, ai_personality: "unknown")).not_to be_valid
    end
  end
end
```

```ruby
# spec/factories/game_players.rb
FactoryBot.define do
  factory :game_player do
    association :game
    association :user
    ai_personality { nil }
    seat_position { 0 }
    player_state { "active" }
    hand_data { {} }
  end

  factory :ai_game_player, parent: :game_player do
    user { nil }
    ai_personality { "clara" }
  end
end
```

**Step 2: Run test, verify failure, commit**

```bash
bin/rspec spec/models/game_player_spec.rb
git add spec/models/game_player_spec.rb spec/factories/game_players.rb
git commit -m "test: add GamePlayer model specs"
```

### Task 13: GamePlayer model — implementation

**Step 1: Generate**

```bash
bin/rails generate model GamePlayer \
  game:references \
  user:references \
  ai_personality:string \
  seat_position:integer \
  player_state:string \
  hand_data:jsonb \
  result:string
```

**Step 2: Fix migration** — make `user_id` nullable (AI players have no user):

```ruby
t.references :user, null: true, foreign_key: true
t.jsonb :hand_data, default: {}, null: false
t.string :player_state, default: "active", null: false
```

**Step 3: Add validations**

```ruby
# app/models/game_player.rb
class GamePlayer < ApplicationRecord
  VALID_AI_PERSONALITIES = %w[clara flo clarissa].freeze
  PLAYER_STATES = %w[active stood busted disconnected].freeze
  RESULTS = %w[won lost draw].freeze

  belongs_to :game
  belongs_to :user, optional: true
  has_many :moves, dependent: :destroy

  validates :seat_position, presence: true
  validates :player_state, presence: true, inclusion: { in: PLAYER_STATES }
  validates :ai_personality, inclusion: { in: VALID_AI_PERSONALITIES }, allow_nil: true
  validates :result, inclusion: { in: RESULTS }, allow_nil: true
  validate :must_have_user_or_ai

  private

  def must_have_user_or_ai
    if user_id.blank? && ai_personality.blank?
      errors.add(:base, "Must have either a user or an AI personality")
    end
  end
end
```

**Step 4: Run tests**

```bash
bin/rails db:migrate
bin/rspec spec/models/game_player_spec.rb
```

Expected: All PASS

**Step 5: Commit**

```bash
git add app/models/game_player.rb db/migrate/
git commit -m "feat: add GamePlayer model for multiplayer support"
```

### Task 14: Move model — test and implementation

**Files:**
- Create: `spec/models/move_spec.rb`, `spec/factories/moves.rb`
- Create: `app/models/move.rb` + migration

**Step 1: Write test**

```ruby
# spec/models/move_spec.rb
require "rails_helper"

RSpec.describe Move, type: :model do
  it "requires move_number" do
    move = build(:move, move_number: nil)
    expect(move).not_to be_valid
  end

  it "requires action" do
    move = build(:move, action: nil)
    expect(move).not_to be_valid
  end

  it "belongs to a game and game_player" do
    move = create(:move)
    expect(move.game).to be_a(Game)
    expect(move.game_player).to be_a(GamePlayer)
  end
end
```

```ruby
# spec/factories/moves.rb
FactoryBot.define do
  factory :move do
    association :game
    association :game_player
    sequence(:move_number) { |n| n }
    action { { type: "hit" } }
    game_data_snapshot { {} }
    clara_commentary { nil }
  end
end
```

**Step 2: Generate and implement**

```bash
bin/rails generate model Move \
  game:references \
  game_player:references \
  move_number:integer \
  action:jsonb \
  game_data_snapshot:jsonb \
  clara_commentary:text
```

```ruby
# app/models/move.rb
class Move < ApplicationRecord
  belongs_to :game
  belongs_to :game_player

  validates :move_number, presence: true
  validates :action, presence: true
end
```

**Step 3: Run tests**

```bash
bin/rails db:migrate
bin/rspec spec/models/
```

Expected: All model specs PASS

**Step 4: Commit**

```bash
git add app/models/move.rb db/migrate/ spec/
git commit -m "feat: add Move model for game history"
```

### Task 15: JWT authentication — test

**Files:**
- Create: `spec/controllers/auth_controller_spec.rb`

**Step 1: Write the failing test**

```ruby
# spec/controllers/auth_controller_spec.rb
require "rails_helper"

RSpec.describe AuthController, type: :controller do
  describe "GET #callback" do
    let(:secret_key) { "test-secret-key" }
    let(:valid_token) do
      payload = {
        "sub" => "canonical-user-123",
        "name" => "Joshua",
        "avatar" => "https://example.com/avatar.png",
        "aud" => "games.mypalclara.com",
        "iat" => Time.now.to_i,
        "exp" => 5.minutes.from_now.to_i,
      }
      JWT.encode(payload, secret_key, "HS256")
    end

    before do
      ENV["CLARA_JWT_SECRET"] = secret_key
    end

    it "creates a user and sets session from valid token" do
      get :callback, params: { token: valid_token }
      expect(response).to redirect_to(root_path)
      expect(User.find_by(canonical_user_id: "canonical-user-123")).to be_present
      expect(session[:user_id]).to be_present
    end

    it "finds existing user on repeat login" do
      user = create(:user, canonical_user_id: "canonical-user-123")
      get :callback, params: { token: valid_token }
      expect(User.where(canonical_user_id: "canonical-user-123").count).to eq(1)
      expect(session[:user_id]).to eq(user.id)
    end

    it "rejects expired tokens" do
      expired_payload = {
        "sub" => "user-123",
        "name" => "Test",
        "aud" => "games.mypalclara.com",
        "iat" => 10.minutes.ago.to_i,
        "exp" => 5.minutes.ago.to_i,
      }
      token = JWT.encode(expired_payload, secret_key, "HS256")
      get :callback, params: { token: token }
      expect(response).to redirect_to(root_path)
      expect(flash[:alert]).to be_present
    end

    it "rejects tokens with wrong audience" do
      wrong_aud_payload = {
        "sub" => "user-123",
        "name" => "Test",
        "aud" => "evil.com",
        "iat" => Time.now.to_i,
        "exp" => 5.minutes.from_now.to_i,
      }
      token = JWT.encode(wrong_aud_payload, secret_key, "HS256")
      get :callback, params: { token: token }
      expect(response).to redirect_to(root_path)
      expect(flash[:alert]).to be_present
    end
  end
end
```

**Step 2: Run, verify failure, commit**

```bash
bin/rspec spec/controllers/auth_controller_spec.rb
git add spec/controllers/auth_controller_spec.rb
git commit -m "test: add AuthController JWT callback specs"
```

### Task 16: JWT authentication — implementation

**Files:**
- Create: `app/controllers/auth_controller.rb`
- Modify: `config/routes.rb`
- Modify: `Gemfile` (add `jwt` gem)

**Step 1: Add jwt gem**

Add to `Gemfile`:
```ruby
gem "jwt", "~> 2.9"
```

```bash
bundle install
```

**Step 2: Create AuthController**

```ruby
# app/controllers/auth_controller.rb
class AuthController < ApplicationController
  skip_before_action :authenticate_user!, only: [:callback, :login]

  def login
    redirect_uri = "#{request.base_url}/auth/callback"
    clara_url = ENV.fetch("CLARA_AUTH_URL", "https://mypalclara.com")
    redirect_to "#{clara_url}/auth/game-redirect?redirect_uri=#{CGI.escape(redirect_uri)}",
      allow_other_host: true
  end

  def callback
    token = params[:token]
    payload = decode_game_token(token)

    if payload.nil?
      redirect_to root_path, alert: "Authentication failed. Please try again."
      return
    end

    user = User.find_or_create_by!(canonical_user_id: payload["sub"]) do |u|
      u.display_name = payload["name"]
      u.avatar_url = payload["avatar"]
    end

    # Update display name and avatar on each login
    user.update(
      display_name: payload["name"],
      avatar_url: payload["avatar"]
    )

    session[:user_id] = user.id
    redirect_to root_path
  end

  def logout
    reset_session
    redirect_to root_path
  end

  private

  def decode_game_token(token)
    secret = ENV.fetch("CLARA_JWT_SECRET")
    JWT.decode(
      token,
      secret,
      true,
      algorithm: "HS256",
      aud: "games.mypalclara.com",
      verify_aud: true,
    ).first
  rescue JWT::DecodeError, JWT::ExpiredSignature, JWT::InvalidAudError, KeyError
    nil
  end
end
```

**Step 3: Create ApplicationController with auth**

```ruby
# app/controllers/application_controller.rb
class ApplicationController < ActionController::Base
  before_action :authenticate_user!

  private

  def authenticate_user!
    unless current_user
      redirect_to auth_login_path
    end
  end

  def current_user
    @current_user ||= User.find_by(id: session[:user_id]) if session[:user_id]
  end

  helper_method :current_user
end
```

**Step 4: Add routes**

```ruby
# config/routes.rb
Rails.application.routes.draw do
  get "auth/login", to: "auth#login"
  get "auth/callback", to: "auth#callback"
  delete "auth/logout", to: "auth#logout"

  root "lobby#index"
end
```

**Step 5: Run tests**

```bash
bin/rspec spec/controllers/auth_controller_spec.rb
```

Expected: All PASS

**Step 6: Commit**

```bash
git add Gemfile Gemfile.lock app/controllers/ config/routes.rb
git commit -m "feat: add JWT auth with Clara redirect handshake"
```

---

## Phase 4: Clara API Client

### Task 17: Clara API client service — test

**Files:**
- Create: `spec/services/clara_api_spec.rb`

**Step 1: Write the failing test**

```ruby
# spec/services/clara_api_spec.rb
require "rails_helper"

RSpec.describe ClaraApi, type: :service do
  let(:api) { ClaraApi.new }

  before do
    ENV["CLARA_API_URL"] = "https://mypalclara.com"
    ENV["GAME_API_KEY"] = "test-key"
  end

  describe "#get_move" do
    it "returns move, commentary, and mood" do
      stub_request(:post, "https://mypalclara.com/api/v1/game/move")
        .with(
          headers: { "X-Game-API-Key" => "test-key" },
        )
        .to_return(
          status: 200,
          body: {
            move: { type: "stand" },
            commentary: "Nice hand!",
            mood: "happy",
          }.to_json,
          headers: { "Content-Type" => "application/json" },
        )

      result = api.get_move(
        game_type: "blackjack",
        game_state: { player_hand: ["A♠", "7♥"] },
        legal_moves: ["hit", "stand"],
        personality: "clara",
        user_id: "user-123",
      )

      expect(result[:move][:type]).to eq("stand")
      expect(result[:commentary]).to eq("Nice hand!")
      expect(result[:mood]).to eq("happy")
    end

    it "returns fallback on API failure" do
      stub_request(:post, "https://mypalclara.com/api/v1/game/move")
        .to_return(status: 500)

      result = api.get_move(
        game_type: "blackjack",
        game_state: {},
        legal_moves: ["hit", "stand"],
        personality: "clara",
        user_id: "user-123",
      )

      expect(["hit", "stand"]).to include(result[:move][:type])
      expect(result[:commentary]).to be_present
      expect(result[:mood]).to eq("nervous")
    end
  end
end
```

**Step 2: Add webmock gem**

Add to `Gemfile` test group:
```ruby
gem "webmock"
```

```bash
bundle install
```

**Step 3: Run, verify failure, commit**

```bash
bin/rspec spec/services/clara_api_spec.rb
git add spec/services/clara_api_spec.rb Gemfile Gemfile.lock
git commit -m "test: add ClaraApi service specs"
```

### Task 18: Clara API client service — implementation

**Files:**
- Create: `app/services/clara_api.rb`

```ruby
# app/services/clara_api.rb
class ClaraApi
  TIMEOUT = 10 # seconds

  def initialize
    @base_url = ENV.fetch("CLARA_API_URL", "https://mypalclara.com")
    @api_key = ENV.fetch("GAME_API_KEY")
  end

  def get_move(game_type:, game_state:, legal_moves:, personality:, user_id:, move_history: [])
    uri = URI("#{@base_url}/api/v1/game/move")

    body = {
      game_type: game_type,
      game_state: game_state,
      legal_moves: legal_moves,
      personality: personality,
      user_id: user_id,
      move_history: move_history,
    }

    response = Net::HTTP.post(
      uri,
      body.to_json,
      "Content-Type" => "application/json",
      "X-Game-API-Key" => @api_key,
    )

    if response.is_a?(Net::HTTPSuccess)
      JSON.parse(response.body, symbolize_names: true)
    else
      fallback_move(legal_moves)
    end
  rescue Net::OpenTimeout, Net::ReadTimeout, StandardError => e
    Rails.logger.error("ClaraApi error: #{e.message}")
    fallback_move(legal_moves)
  end

  private

  def fallback_move(legal_moves)
    {
      move: { type: legal_moves.sample },
      commentary: "Give me a second... okay, here goes.",
      mood: "nervous",
    }
  end
end
```

**Step 1: Run tests**

```bash
bin/rspec spec/services/clara_api_spec.rb
```

Expected: All PASS

**Step 2: Commit**

```bash
git add app/services/clara_api.rb
git commit -m "feat: add ClaraApi service for game move requests"
```

---

## Phase 5: Blackjack Game Engine

### Task 19: Blackjack engine — test

**Files:**
- Create: `spec/services/games/blackjack_engine_spec.rb`

**Step 1: Write the failing test**

```ruby
# spec/services/games/blackjack_engine_spec.rb
require "rails_helper"

RSpec.describe Games::BlackjackEngine do
  let(:engine) { Games::BlackjackEngine.new }

  describe "#new_game" do
    it "creates a shuffled deck and empty hands" do
      state = engine.new_game
      expect(state[:deck].length).to eq(52)
      expect(state[:dealer_hand]).to eq([])
      expect(state[:phase]).to eq("dealing")
    end
  end

  describe "#deal" do
    it "deals two cards to each player and one to dealer" do
      state = engine.new_game
      player_ids = ["player-1", "player-2"]
      state = engine.deal(state, player_ids)

      expect(state[:hands]["player-1"].length).to eq(2)
      expect(state[:hands]["player-2"].length).to eq(2)
      expect(state[:dealer_hand].length).to eq(2)
      expect(state[:phase]).to eq("player_turns")
      # 52 - (2 per player * 2 players) - 2 dealer = 46
      expect(state[:deck].length).to eq(46)
    end
  end

  describe "#hand_value" do
    it "counts number cards at face value" do
      expect(engine.hand_value(["7♠", "3♥"])).to eq(10)
    end

    it "counts face cards as 10" do
      expect(engine.hand_value(["K♠", "Q♥"])).to eq(20)
    end

    it "counts ace as 11 when safe" do
      expect(engine.hand_value(["A♠", "7♥"])).to eq(18)
    end

    it "counts ace as 1 when 11 would bust" do
      expect(engine.hand_value(["A♠", "7♥", "8♦"])).to eq(16)
    end

    it "detects blackjack" do
      expect(engine.hand_value(["A♠", "K♥"])).to eq(21)
    end
  end

  describe "#legal_moves" do
    it "returns hit and stand for normal hand" do
      state = { hands: { "p1" => ["7♠", "3♥"] }, phase: "player_turns" }
      moves = engine.legal_moves(state, "p1")
      expect(moves).to include("hit", "stand")
    end

    it "returns empty for busted hand" do
      state = { hands: { "p1" => ["K♠", "Q♥", "5♦"] }, phase: "player_turns" }
      moves = engine.legal_moves(state, "p1")
      expect(moves).to eq([])
    end

    it "includes double_down on first two cards" do
      state = { hands: { "p1" => ["7♠", "3♥"] }, phase: "player_turns" }
      moves = engine.legal_moves(state, "p1")
      expect(moves).to include("double_down")
    end

    it "excludes double_down after hit" do
      state = { hands: { "p1" => ["7♠", "3♥", "2♦"] }, phase: "player_turns" }
      moves = engine.legal_moves(state, "p1")
      expect(moves).not_to include("double_down")
    end
  end

  describe "#apply_move" do
    it "adds a card on hit" do
      state = engine.new_game
      state = engine.deal(state, ["p1"])
      hand_before = state[:hands]["p1"].length
      state = engine.apply_move(state, "p1", "hit")
      expect(state[:hands]["p1"].length).to eq(hand_before + 1)
    end

    it "marks player as stood on stand" do
      state = engine.new_game
      state = engine.deal(state, ["p1"])
      state = engine.apply_move(state, "p1", "stand")
      expect(state[:stood]).to include("p1")
    end
  end

  describe "#resolve" do
    it "determines winners correctly" do
      state = {
        hands: { "p1" => ["K♠", "9♥"] }, # 19
        dealer_hand: ["K♦", "7♣"],        # 17
        deck: [],
        phase: "resolving",
        stood: ["p1"],
      }
      results = engine.resolve(state)
      expect(results["p1"]).to eq("won")
    end

    it "dealer wins on tie" do
      state = {
        hands: { "p1" => ["K♠", "8♥"] }, # 18
        dealer_hand: ["K♦", "8♣"],        # 18
        deck: [],
        phase: "resolving",
        stood: ["p1"],
      }
      results = engine.resolve(state)
      expect(results["p1"]).to eq("draw")
    end

    it "busted players lose" do
      state = {
        hands: { "p1" => ["K♠", "Q♥", "5♦"] }, # 25 bust
        dealer_hand: ["K♦", "7♣"],
        deck: [],
        phase: "resolving",
        stood: [],
      }
      results = engine.resolve(state)
      expect(results["p1"]).to eq("lost")
    end
  end
end
```

**Step 2: Run, verify failure, commit**

```bash
bin/rspec spec/services/games/blackjack_engine_spec.rb
git add spec/services/games/blackjack_engine_spec.rb
git commit -m "test: add BlackjackEngine specs"
```

### Task 20: Blackjack engine — implementation

**Files:**
- Create: `app/services/games/blackjack_engine.rb`

**Step 1: Implement**

```ruby
# app/services/games/blackjack_engine.rb
module Games
  class BlackjackEngine
    SUITS = %w[♠ ♥ ♦ ♣].freeze
    RANKS = %w[A 2 3 4 5 6 7 8 9 10 J Q K].freeze

    def new_game
      deck = SUITS.product(RANKS).map { |s, r| "#{r}#{s}" }.shuffle
      {
        deck: deck,
        dealer_hand: [],
        hands: {},
        stood: [],
        phase: "dealing",
      }
    end

    def deal(state, player_ids)
      state = state.deep_dup
      player_ids.each { |pid| state[:hands][pid] = [] }

      # Deal two cards to each player, then two to dealer
      2.times do
        player_ids.each { |pid| state[:hands][pid] << state[:deck].shift }
      end
      2.times { state[:dealer_hand] << state[:deck].shift }

      state[:phase] = "player_turns"
      state
    end

    def hand_value(hand)
      values = hand.map { |card| card_value(card) }
      total = values.sum
      aces = hand.count { |c| c.start_with?("A") }

      while total > 21 && aces > 0
        total -= 10
        aces -= 1
      end

      total
    end

    def legal_moves(state, player_id)
      hand = state[:hands][player_id]
      return [] if hand.nil?

      value = hand_value(hand)
      return [] if value > 21

      moves = %w[hit stand]
      moves << "double_down" if hand.length == 2
      moves
    end

    def apply_move(state, player_id, move)
      state = state.deep_dup

      case move
      when "hit"
        state[:hands][player_id] << state[:deck].shift
      when "stand"
        state[:stood] ||= []
        state[:stood] << player_id unless state[:stood].include?(player_id)
      when "double_down"
        state[:hands][player_id] << state[:deck].shift
        state[:stood] ||= []
        state[:stood] << player_id unless state[:stood].include?(player_id)
      end

      state
    end

    def dealer_play(state)
      state = state.deep_dup
      while hand_value(state[:dealer_hand]) < 17
        state[:dealer_hand] << state[:deck].shift
      end
      state[:phase] = "resolving"
      state
    end

    def resolve(state)
      dealer_value = hand_value(state[:dealer_hand])
      dealer_bust = dealer_value > 21

      results = {}
      state[:hands].each do |player_id, hand|
        player_value = hand_value(hand)

        results[player_id] = if player_value > 21
          "lost"
        elsif dealer_bust
          "won"
        elsif player_value > dealer_value
          "won"
        elsif player_value == dealer_value
          "draw"
        else
          "lost"
        end
      end

      results
    end

    private

    def card_value(card)
      rank = card[0..-2] # everything except last char (suit)
      # Handle 10 which is two chars
      rank = card.match(/\A(\d+|[AJQK])/)[1]

      case rank
      when "A" then 11
      when "K", "Q", "J" then 10
      else rank.to_i
      end
    end
  end
end
```

**Step 2: Run tests**

```bash
bin/rspec spec/services/games/blackjack_engine_spec.rb
```

Expected: All PASS

**Step 3: Commit**

```bash
git add app/services/games/blackjack_engine.rb
git commit -m "feat: add BlackjackEngine with dealing, hitting, standing, and resolution"
```

### Task 21: Checkers engine — test

**Files:**
- Create: `spec/services/games/checkers_engine_spec.rb`

**Step 1: Write the failing test**

```ruby
# spec/services/games/checkers_engine_spec.rb
require "rails_helper"

RSpec.describe Games::CheckersEngine do
  let(:engine) { Games::CheckersEngine.new }

  describe "#new_game" do
    it "creates a standard 8x8 board with 12 pieces each" do
      state = engine.new_game
      board = state[:board]

      red_count = board.flatten.count { |c| c == "r" }
      black_count = board.flatten.count { |c| c == "b" }

      expect(red_count).to eq(12)
      expect(black_count).to eq(12)
    end
  end

  describe "#legal_moves" do
    it "returns valid moves for a piece" do
      state = engine.new_game
      moves = engine.legal_moves(state, "red")
      expect(moves).not_to be_empty
      moves.each do |move|
        expect(move).to have_key(:from)
        expect(move).to have_key(:to)
      end
    end

    it "forces jumps when available" do
      # Set up a board where red must jump
      state = engine.new_game
      state[:board] = Array.new(8) { Array.new(8, nil) }
      state[:board][4][3] = "r"
      state[:board][3][4] = "b"

      moves = engine.legal_moves(state, "red")
      expect(moves.length).to eq(1)
      expect(moves[0][:to]).to eq([2, 5])
    end
  end

  describe "#apply_move" do
    it "moves a piece to a new position" do
      state = engine.new_game
      moves = engine.legal_moves(state, "red")
      move = moves.first

      new_state = engine.apply_move(state, move)
      expect(new_state[:board][move[:from][0]][move[:from][1]]).to be_nil
      expect(new_state[:board][move[:to][0]][move[:to][1]]).to eq("r")
    end

    it "removes captured pieces on jump" do
      state = engine.new_game
      state[:board] = Array.new(8) { Array.new(8, nil) }
      state[:board][4][3] = "r"
      state[:board][3][4] = "b"

      move = { from: [4, 3], to: [2, 5], captures: [[3, 4]] }
      new_state = engine.apply_move(state, move)

      expect(new_state[:board][3][4]).to be_nil
      expect(new_state[:board][2][5]).to eq("r")
    end

    it "kings a piece reaching the opposite end" do
      state = engine.new_game
      state[:board] = Array.new(8) { Array.new(8, nil) }
      state[:board][1][2] = "r"

      move = { from: [1, 2], to: [0, 3] }
      new_state = engine.apply_move(state, move)

      expect(new_state[:board][0][3]).to eq("R")
    end
  end

  describe "#winner" do
    it "returns nil for ongoing game" do
      state = engine.new_game
      expect(engine.winner(state)).to be_nil
    end

    it "returns winner when opponent has no pieces" do
      state = { board: Array.new(8) { Array.new(8, nil) }, captured: {} }
      state[:board][4][3] = "r"
      expect(engine.winner(state)).to eq("red")
    end
  end
end
```

**Step 2: Run, verify failure, commit**

```bash
bin/rspec spec/services/games/checkers_engine_spec.rb
git add spec/services/games/checkers_engine_spec.rb
git commit -m "test: add CheckersEngine specs"
```

### Task 22: Checkers engine — implementation

**Files:**
- Create: `app/services/games/checkers_engine.rb`

Implement standard American checkers: 8x8 board, mandatory jumps, multi-jumps, kinging.

- Board is an 8x8 array: `nil` for empty, `"r"/"b"` for regular, `"R"/"B"` for kings
- Red moves toward row 0 (up), black moves toward row 7 (down)
- Must jump if jump is available
- King when reaching opposite back row

**Step 1: Implement**

Full implementation of `Games::CheckersEngine` with `new_game`, `legal_moves`, `apply_move`, and `winner` methods following the test expectations above.

**Step 2: Run tests**

```bash
bin/rspec spec/services/games/checkers_engine_spec.rb
```

Expected: All PASS

**Step 3: Commit**

```bash
git add app/services/games/checkers_engine.rb
git commit -m "feat: add CheckersEngine with standard American checkers rules"
```

---

## Phase 6: Game Controllers

### Task 23: GamesController — test and implementation

**Files:**
- Create: `spec/controllers/games_controller_spec.rb`
- Create: `app/controllers/games_controller.rb`
- Modify: `config/routes.rb`

This controller handles:
- `POST /games` — create a new game (pick type, pick AI opponents)
- `GET /games/:id` — show game (Inertia page)
- `POST /games/:id/move` — player makes a move
- `POST /games/:id/ai_move` — trigger AI player's turn (called after player move)

**Step 1: Write tests for game creation and move flow**

Test that:
- Creating a blackjack game sets up the correct state, deals cards
- Creating a checkers game sets up the board
- Making a move updates game state and records the move
- AI move triggers ClaraApi call and records the response
- Invalid moves are rejected

**Step 2: Implement controller using Inertia**

```ruby
class GamesController < ApplicationController
  def create
    game = Game.create!(
      game_type: params[:game_type],
      state: "waiting",
      created_by: current_user,
      game_data: {},
    )

    # Add human player
    game.game_players.create!(
      user: current_user,
      seat_position: 0,
      player_state: "active",
    )

    # Add AI players
    (params[:ai_players] || []).each_with_index do |personality, i|
      game.game_players.create!(
        ai_personality: personality,
        seat_position: i + 1,
        player_state: "active",
      )
    end

    # Initialize game state
    engine = game_engine(game.game_type)
    state = engine.new_game
    player_ids = game.game_players.order(:seat_position).map { |gp| gp_identifier(gp) }
    state = engine.deal(state, player_ids) if game.game_type == "blackjack"
    game.update!(game_data: state, state: "in_progress", started_at: Time.current)

    redirect_to game_path(game)
  end

  def show
    game = Game.find(params[:id])
    render inertia: "games/#{game.game_type.capitalize}", props: {
      game: game_props(game),
    }
  end

  def move
    game = Game.find(params[:id])
    engine = game_engine(game.game_type)
    player = game.game_players.find_by!(user: current_user)

    # Validate move
    state = game.game_data.deep_symbolize_keys
    legal = engine.legal_moves(state, gp_identifier(player))
    # ... validate, apply, record, check for game end
  end

  private

  def game_engine(type)
    case type
    when "blackjack" then Games::BlackjackEngine.new
    when "checkers" then Games::CheckersEngine.new
    end
  end

  def gp_identifier(game_player)
    game_player.ai_personality || "player-#{game_player.user_id}"
  end
end
```

**Step 3: Add routes**

```ruby
resources :games, only: [:create, :show] do
  member do
    post :move
  end
end
```

**Step 4: Run tests, commit**

```bash
bin/rspec spec/controllers/games_controller_spec.rb
git add app/controllers/games_controller.rb config/routes.rb spec/
git commit -m "feat: add GamesController with create, show, and move actions"
```

### Task 24: Action Cable for multiplayer blackjack

**Files:**
- Create: `app/channels/game_channel.rb`
- Create: `spec/channels/game_channel_spec.rb`

**Step 1: Create GameChannel**

```ruby
# app/channels/game_channel.rb
class GameChannel < ApplicationCable::Channel
  def subscribed
    game = Game.find(params[:game_id])
    stream_for game
  end

  def unsubscribed
    # Handle disconnect timeout
  end
end
```

**Step 2: Broadcast game state updates from GamesController**

After each move, broadcast to all players:
```ruby
GameChannel.broadcast_to(game, { type: "game_update", game: game_props(game) })
```

**Step 3: Test, commit**

```bash
bin/rspec spec/channels/
git add app/channels/ spec/channels/
git commit -m "feat: add GameChannel for real-time multiplayer updates"
```

---

## Phase 7: React Frontend

### Task 25: Lobby page

**Files:**
- Create: `app/frontend/pages/Lobby.tsx`
- Create: `app/frontend/components/GameCard.tsx`
- Create: `app/frontend/components/ClaraSprite.tsx`
- Modify: `app/controllers/lobby_controller.rb`

**Step 1: Update LobbyController to pass props**

```ruby
class LobbyController < ApplicationController
  def index
    games = current_user.games.order(created_at: :desc).limit(10)
    stats = {
      blackjack: game_stats("blackjack"),
      checkers: game_stats("checkers"),
    }
    render inertia: "Lobby", props: {
      user: { name: current_user.display_name, avatar: current_user.avatar_url },
      recent_games: games.map { |g| game_summary(g) },
      stats: stats,
    }
  end
end
```

**Step 2: Build Lobby.tsx**

React component showing:
- Game selection cards (Blackjack, Checkers) with stats
- Recent games list
- Clara sprite with greeting
- "New Game" flow: pick game type → pick AI opponents → create

**Step 3: Build ClaraSprite.tsx**

React component that:
- Loads sprite manifest JSON
- Renders current animation frame on Canvas
- Accepts `mood` and `talking` props
- Handles frame animation loop via `requestAnimationFrame`

**Step 4: Verify, commit**

```bash
bin/dev  # Check lobby renders in browser
git add app/frontend/ app/controllers/lobby_controller.rb
git commit -m "feat: add Lobby page with game selection and Clara sprite"
```

### Task 26: Blackjack game page

**Files:**
- Create: `app/frontend/pages/games/Blackjack.tsx`
- Create: `app/frontend/components/Card.tsx`
- Create: `app/frontend/components/PlayerHand.tsx`
- Create: `app/frontend/components/DealerArea.tsx`
- Create: `app/frontend/components/SpeechBubble.tsx`

Implement the blackjack game view:
- Dealer area at top with Clara sprite
- Player seats showing hands and totals
- AI player commentary in speech bubbles
- Action buttons (Hit/Stand/Double Down)
- Action Cable subscription for multiplayer updates
- Inertia form submission for moves

**Step 1: Build components, connect to Inertia props and Action Cable**

**Step 2: Verify in browser, commit**

```bash
git add app/frontend/
git commit -m "feat: add Blackjack game page with multiplayer UI"
```

### Task 27: Checkers game page

**Files:**
- Create: `app/frontend/pages/games/Checkers.tsx`
- Create: `app/frontend/components/CheckerBoard.tsx`
- Create: `app/frontend/components/CheckerPiece.tsx`

Implement the checkers game view:
- 8x8 board rendered on Canvas (pixel art style)
- Click-to-select, click-to-move
- Legal move highlighting
- Clara sprite with commentary
- Captured pieces display

**Step 1: Build components**

**Step 2: Verify, commit**

```bash
git add app/frontend/
git commit -m "feat: add Checkers game page with interactive board"
```

### Task 28: History and replay pages

**Files:**
- Create: `app/frontend/pages/History.tsx`
- Create: `app/frontend/pages/Replay.tsx`
- Create: `app/controllers/history_controller.rb`
- Modify: `config/routes.rb`

**Step 1: HistoryController**

```ruby
class HistoryController < ApplicationController
  def index
    games = current_user.game_players
      .includes(game: :game_players)
      .order("games.created_at DESC")
      .page(params[:page])

    render inertia: "History", props: { games: games.map { |gp| history_entry(gp) } }
  end

  def show
    game = Game.find(params[:id])
    moves = game.moves.includes(:game_player).order(:move_number)

    render inertia: "Replay", props: {
      game: game_props(game),
      moves: moves.map { |m| move_props(m) },
    }
  end
end
```

**Step 2: Build History.tsx** — filterable game list with stats

**Step 3: Build Replay.tsx** — step through moves with Clara's commentary

**Step 4: Add routes, verify, commit**

```ruby
resources :history, only: [:index, :show]
```

```bash
git add app/frontend/ app/controllers/history_controller.rb config/routes.rb
git commit -m "feat: add History and Replay pages"
```

---

## Phase 8: Deployment

### Task 29: VPS setup — Ruby, Rails, Nginx, PostgreSQL

**Step 1: SSH into VPS and install Ruby**

```bash
ssh root@167.88.44.192

# Install rbenv + ruby-build
apt-get update && apt-get install -y git curl libssl-dev libreadline-dev zlib1g-dev
git clone https://github.com/rbenv/rbenv.git ~/.rbenv
git clone https://github.com/rbenv/ruby-build.git ~/.rbenv/plugins/ruby-build
echo 'eval "$(~/.rbenv/bin/rbenv init -)"' >> ~/.bashrc
source ~/.bashrc

rbenv install 3.4.8
rbenv global 3.4.8
gem install bundler
```

**Step 2: Create PostgreSQL database**

```bash
sudo -u postgres createdb clara_games_production
sudo -u postgres psql -c "GRANT ALL ON DATABASE clara_games_production TO clara_user;"
```

**Step 3: Clone and set up the Rails app**

```bash
mkdir -p /var/www
cd /var/www
git clone <repo-url> clara-games
cd clara-games
bundle install --deployment --without development test
RAILS_ENV=production bin/rails db:migrate
RAILS_ENV=production bin/rails assets:precompile
```

**Step 4: Configure Puma**

Create `config/puma/production.rb`:
```ruby
workers 2
threads_count = ENV.fetch("RAILS_MAX_THREADS", 5)
threads threads_count, threads_count
bind "unix:///var/www/clara-games/tmp/sockets/puma.sock"
environment "production"
pidfile "/var/www/clara-games/tmp/pids/puma.pid"
```

**Step 5: Create systemd service**

Create `/etc/systemd/system/clara-games.service`:
```ini
[Unit]
Description=Clara Games (Puma)
After=network.target postgresql.service

[Service]
User=www-data
WorkingDirectory=/var/www/clara-games
Environment=RAILS_ENV=production
Environment=CLARA_JWT_SECRET=<shared-secret>
Environment=GAME_API_KEY=<api-key>
Environment=CLARA_API_URL=https://mypalclara.com
Environment=DATABASE_URL=postgresql://clara_user:pass@localhost/clara_games_production
ExecStart=/home/deploy/.rbenv/shims/bundle exec puma -C config/puma/production.rb
Restart=always

[Install]
WantedBy=multi-user.target
```

```bash
systemctl enable clara-games
systemctl start clara-games
```

**Step 6: Configure Nginx**

Add `/etc/nginx/sites-available/games.mypalclara.com`:
```nginx
upstream clara_games {
    server unix:///var/www/clara-games/tmp/sockets/puma.sock;
}

server {
    listen 80;
    server_name games.mypalclara.com;

    root /var/www/clara-games/public;

    location / {
        proxy_pass http://clara_games;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location /cable {
        proxy_pass http://clara_games;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "Upgrade";
        proxy_set_header Host $host;
    }

    location ~* \.(js|css|png|jpg|jpeg|gif|ico|svg|woff2?)$ {
        expires max;
        add_header Cache-Control public;
    }
}
```

```bash
ln -s /etc/nginx/sites-available/games.mypalclara.com /etc/nginx/sites-enabled/
nginx -t && systemctl reload nginx
```

**Step 7: Commit deployment configs**

```bash
git add config/puma/production.rb
git commit -m "chore: add production Puma config"
```

### Task 30: Cloudflare DNS

**Step 1: Add A record**

Using Cloudflare dashboard or API:
- Type: A
- Name: games
- Content: 167.88.44.192
- Proxy: Yes (orange cloud)
- TTL: Auto

**Step 2: SSL**

Cloudflare handles SSL termination with proxied records. Nginx listens on port 80, Cloudflare proxies HTTPS → HTTP.

**Step 3: Verify**

```bash
curl -I https://games.mypalclara.com
```

Expected: 200 OK (or redirect to login)

### Task 31: Environment variables

Set on the VPS in the systemd service or a `.env` file:

**Rails app (clara-games):**
```bash
RAILS_ENV=production
SECRET_KEY_BASE=<generate with bin/rails secret>
DATABASE_URL=postgresql://clara_user:pass@localhost/clara_games_production
CLARA_JWT_SECRET=<shared with FastAPI>
CLARA_API_URL=https://mypalclara.com
GAME_API_KEY=<shared with FastAPI>
REDIS_URL=redis://localhost:6379/1
```

**FastAPI app (mypalclara) — add these:**
```bash
GAME_API_KEY=<same key as Rails>
# WEB_SECRET_KEY is already used for JWT — same key goes to CLARA_JWT_SECRET on Rails side
```

---

## Phase 9: Integration Testing

### Task 32: End-to-end smoke test

**Step 1: Verify auth flow**
- Visit games.mypalclara.com → redirected to mypalclara.com login
- Log in via Discord → redirected back to games.mypalclara.com with session
- User record created in games DB

**Step 2: Verify blackjack**
- Create new game with Flo as AI opponent
- Play a hand — hit, stand
- Flo makes moves with commentary and mood
- Game resolves, result saved

**Step 3: Verify checkers**
- Create new game vs Clara
- Make moves, verify legal move enforcement
- Clara responds with moves and banter

**Step 4: Verify history**
- Check completed games appear in history
- Replay a game step-by-step

---

## Summary

| Phase | Tasks | What it builds |
|-------|-------|---------------|
| 1 | 1-4 | FastAPI game API + auth redirect |
| 2 | 5-7 | Rails scaffold with Inertia + React |
| 3 | 8-16 | Database models + JWT auth |
| 4 | 17-18 | Clara API client service |
| 5 | 19-22 | Blackjack + Checkers game engines |
| 6 | 23-24 | Game controllers + Action Cable |
| 7 | 25-28 | React pages (Lobby, games, history) |
| 8 | 29-31 | VPS deployment + Cloudflare |
| 9 | 32 | Integration smoke test |
