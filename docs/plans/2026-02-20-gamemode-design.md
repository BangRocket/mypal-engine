# Game Mode Design — Clara's Game Room

**Date:** 2026-02-20
**Domain:** games.mypalclara.com
**Stack:** Rails 8 + Inertia.js + React 19 + PostgreSQL

## Overview

A retro pixel-art games site where users play tabletop games against Clara (and her alternate personalities Flo and Clarissa). Clara is the LLM-powered opponent — she reasons about the game, picks moves from legal options, and provides in-character banter. Full game history with move-by-move replay.

**Starting games:** Blackjack (multiplayer), Checkers (1v1)

## System Architecture

```
┌─────────────────────────────────────────────────────┐
│                  games.mypalclara.com                │
│                                                     │
│  Rails 8 + Inertia.js + React 19                    │
│  ┌──────────┐  ┌──────────┐  ┌───────────────────┐ │
│  │ Auth     │  │ Game     │  │ React Pages       │ │
│  │ (JWT     │  │ Engine   │  │ (Inertia)         │ │
│  │ handshake│  │ Models   │  │ - Lobby           │ │
│  │ from     │  │ - BJ     │  │ - Blackjack board │ │
│  │ Clara)   │  │ - Checkers│  │ - Checkers board  │ │
│  │          │  │ - State  │  │ - History/Stats   │ │
│  └──────────┘  └──────────┘  └───────────────────┘ │
│        │              │                             │
│        │              ▼                             │
│        │     Clara Game API                         │
│        │     (HTTP to mypalclara.com)                │
└────────┼────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────┐
│   mypalclara.com        │
│   FastAPI               │
│                         │
│   /api/v1/game/move     │
│   - Receives game state │
│   - Loads personality   │
│   - Builds Clara prompt │
│   - Returns move+banter │
│                         │
│   /auth/game-redirect   │
│   - Issues signed JWT   │
│   - Redirects to games  │
└─────────────────────────┘
```

**Infrastructure:**
- Hosted on existing Hostinger VPS (KVM 2: 2 CPUs, 8GB RAM, 100GB disk, Debian 13)
- PostgreSQL: new `clara_games_production` database on existing Postgres instance
- Nginx reverse proxy: `games.mypalclara.com` → Puma (Rails)
- Cloudflare DNS: A record for `games.mypalclara.com` → 167.88.44.192

## Data Models

```ruby
User
  - canonical_user_id  (string, matches Clara's canonical user system)
  - display_name       (string)
  - avatar_url         (string, nullable)
  - created_at / updated_at

Game
  - game_type          (enum: blackjack, checkers)
  - state              (enum: waiting, in_progress, resolved)
  - current_turn       (string — player identifier)
  - game_data          (jsonb — full board/deck state)
  - move_count         (integer)
  - created_by         (FK → User)
  - started_at / finished_at / created_at / updated_at

GamePlayer
  - game_id            (FK → Game)
  - user_id            (FK → User, nullable for AI players)
  - ai_personality     (string, nullable — "clara", "flo", "clarissa")
  - seat_position      (integer, 0-3)
  - player_state       (enum: active, stood, busted, disconnected)
  - hand_data          (jsonb — this player's cards/game-specific state)
  - result             (enum: won, lost, draw, nullable)

Move
  - game_id            (FK → Game)
  - game_player_id     (FK → GamePlayer)
  - move_number        (integer)
  - action             (jsonb — game-specific move data)
  - game_data_snapshot  (jsonb — state after this move)
  - clara_commentary   (text, nullable)
  - created_at
```

- `game_data` as jsonb keeps it flexible per game type
- `GamePlayer` join table supports multiplayer and AI personalities at the same table
- `Move` records enable full replay with Clara's commentary preserved

## Game Logic

### Blackjack (Multiplayer)

- Standard rules: hit, stand, double down, split
- Clara is always the dealer
- 1-4 players per table: any mix of real users and AI personalities (Flo, Clarissa)
- Rails validates every player action against current hand state
- Turn order: left to right by seat position, then dealer
- AI players take turns via parallel API calls (near-instant)
- Real players who disconnect auto-stand after 60s timeout
- Deck shuffled and stored in `game_data` at game start

### Checkers (1v1)

- Standard 8x8 American checkers
- Mandatory jumps, kings, multi-jumps
- Rails validates legal moves and enforces capture rules
- Player vs one AI personality (Clara, Flo, or Clarissa)
- Clara won't always play optimally — that's a feature

### Move Validation

Rails computes legal moves and sends them to the Clara API. Clara picks from valid options — she cannot make illegal moves. This gives correct gameplay with personality on top.

## Clara Game API

**Endpoint:** `POST /api/v1/game/move` (on FastAPI / mypalclara.com)

**Request:**
```json
{
  "game_type": "blackjack",
  "game_state": { "...current game_data..." },
  "legal_moves": ["hit", "stand"],
  "personality": "flo",
  "user_id": "canonical-user-id",
  "move_history": ["...last ~5 moves..."]
}
```

**Response:**
```json
{
  "move": { "type": "stand" },
  "commentary": "You're sitting on 19? Bold. I like it.",
  "mood": "nervous"
}
```

**Prompt construction:**
1. Load personality file (`personalities/{personality}.md`)
2. Fetch user memories from Rook (via existing `MemoryRetriever`)
3. Build game context (type, state, move history)
4. Instruct LLM to pick a move from legal options and provide commentary
5. Parse structured JSON response

**`mood` values:** `idle`, `happy`, `nervous`, `smug`, `surprised`, `defeated` — maps to sprite animation states.

**Error handling:**
- Invalid move from LLM → fall back to random legal move + canned commentary
- API timeout (>10s) → "Thinking..." UI, retry once
- Auth: Rails passes shared API key in header

## Authentication

JWT redirect handshake — no shared session store required.

```
1. User visits games.mypalclara.com
2. No session → redirect to mypalclara.com/auth/game-redirect?redirect=games.mypalclara.com
3. User logs in via Discord/Google OAuth (existing flow)
4. FastAPI issues signed JWT (canonical_user_id, display_name, avatar_url)
5. Redirects to games.mypalclara.com/auth/callback?token=<jwt>
6. Rails validates JWT signature, finds-or-creates local User, sets session
7. Subsequent requests use Rails session cookie
```

**JWT payload:**
```json
{
  "sub": "canonical-user-id-123",
  "name": "Joshua",
  "avatar": "https://cdn.discordapp.com/...",
  "iat": 1708000000,
  "exp": 1708000300,
  "aud": "games.mypalclara.com"
}
```

- Short-lived (5 min) — only used during redirect handshake
- `aud` claim scoped to games site
- Signing key shared via environment variable

## Pages & Routing

| Route | Page | Component |
|-------|------|-----------|
| `/` | Lobby — game selection, stats, Clara greeting | `Lobby.tsx` |
| `/blackjack/:id` | Blackjack table | `games/Blackjack.tsx` |
| `/checkers/:id` | Checkers board | `games/Checkers.tsx` |
| `/history` | Game history + win/loss stats | `History.tsx` |
| `/history/:id` | Move-by-move replay | `Replay.tsx` |
| `/auth/callback` | JWT handshake (no UI, redirects) | — |

## UI Layout

### Lobby
- Game selection cards with win/loss stats per game type
- Recent games list with outcomes
- Clara sprite with greeting/commentary on stats

### Game View (Blackjack)
- Dealer (Clara) at top with sprite + speech bubble
- Player seats in a row — each showing hand, total, and commentary for AI players
- Action buttons at bottom (contextual to game state)
- Real-time updates via Action Cable (Rails) for multiplayer

### Game View (Checkers)
- Board on left, Clara sprite + speech bubble on right
- Click-to-select, click-to-move interaction
- Captured pieces display
- Legal move highlighting

### History
- Filterable game list with outcomes
- Aggregate stats per game type
- Click to open replay — step through moves with preserved commentary

## Art Assets

Three characters need full sprite sheets: **Clara, Flo, Clarissa**.

All assets follow the format defined in `docs/plans/2026-02-20-gamemode-sprite-guidelines.md`.

**Character sprites:**
```
sprites/clara/manifest.json + animation PNGs
sprites/flo/manifest.json + animation PNGs
sprites/clarissa/manifest.json + animation PNGs
```

**Game assets:**
```
sprites/games/blackjack/cards.png + cards.json + table.png
sprites/games/checkers/board.png + pieces.png + pieces.json
```

**Sprite format:**
- Horizontal strips, frames left to right, no padding
- JSON manifest defines frame dimensions, frame count, FPS, loop behavior
- Resolution-agnostic — set `frame_width`/`frame_height` in manifest
- Transparent PNG backgrounds
- Nearest-neighbor scaling to preserve pixel art crispness

**Animation states per character:** idle, talk, happy, nervous, smug, surprised, defeated

## Real-Time (Multiplayer)

- Action Cable (Rails WebSocket layer) for multiplayer blackjack
- When a player acts, all players at the table receive the updated game state
- AI personality turns fire in parallel, results broadcast to all players
- "Waiting for {player}..." indicator for pending human turns
- 60s disconnect timeout → auto-stand

## Tech Stack Summary

| Layer | Technology |
|-------|-----------|
| Backend | Rails 8, Ruby 3.3+ |
| Frontend bridge | Inertia.js |
| Frontend | React 19, TypeScript |
| Styling | Tailwind CSS |
| Pixel art rendering | Canvas/PixiJS via React components |
| Real-time | Action Cable |
| Database | PostgreSQL (jsonb for game state) |
| Auth | JWT handshake from mypalclara.com |
| AI opponent | Clara Game API (FastAPI endpoint) |
| Hosting | Hostinger VPS (Debian 13), Nginx, Puma |
| DNS/CDN | Cloudflare |
