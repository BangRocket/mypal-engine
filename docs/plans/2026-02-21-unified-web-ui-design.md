# Unified Web-UI Design

**Date**: 2026-02-21
**Status**: Approved

## Goal

Merge the `games/` Rails app and `web-ui/` React SPA into a single unified service called `web-ui`, built with Ruby on Rails (backend API) and React (frontend SPA). Frontend and backend are isolated: backend is a deployable service, frontend is source code that produces static assets.

## Decisions

| Decision | Choice |
|----------|--------|
| Backend framework | Ruby on Rails (API-only) |
| Frontend framework | React + Vite + TypeScript (SPA) |
| Backend role | BFF — proxies non-game requests to Python gateway |
| Frontend architecture | Vite SPA with React Router (not Inertia.js) |
| Directory layout | `web-ui/backend/` + `web-ui/frontend/` (monorepo) |
| Feature scope | Full parity from day one |
| Database | Own PostgreSQL for game tables; everything else via API proxy |
| Python web backend | Delete `mypalclara/web/`; move its API routes into the gateway |
| Python game adapter | Stays in `mypalclara/adapters/game/` |

## Architecture

### System Diagram

```
Browser (React SPA)
  ├── HTTP ──→ Rails API (:3000)
  │              ├── Game endpoints → own PostgreSQL (game tables)
  │              ├── Auth endpoints → JWT + own DB (users table)
  │              └── Proxy → Python gateway HTTP API (:18789)
  │                           ├── /api/v1/sessions/*
  │                           ├── /api/v1/memories/*
  │                           ├── /api/v1/graph/*
  │                           ├── /api/v1/intentions/*
  │                           ├── /api/v1/users/*
  │                           └── /api/v1/admin/*
  │
  ├── WebSocket (chat) ──→ Rails ActionCable ──→ proxy → Python gateway WS
  │
  └── WebSocket (games) ──→ Rails ActionCable (direct, game state broadcasts)
```

### Directory Structure

```
web-ui/
├── backend/                        # Rails API-only app (deployable service)
│   ├── app/
│   │   ├── controllers/
│   │   │   ├── auth_controller.rb          # OAuth + JWT
│   │   │   └── api/v1/
│   │   │       ├── games_controller.rb     # Game CRUD, moves, AI (direct DB)
│   │   │       ├── lobby_controller.rb     # Game stats (direct DB)
│   │   │       ├── history_controller.rb   # Game replay (direct DB)
│   │   │       ├── sessions_controller.rb  # Proxy → gateway
│   │   │       ├── memories_controller.rb  # Proxy → gateway
│   │   │       ├── graph_controller.rb     # Proxy → gateway
│   │   │       ├── intentions_controller.rb # Proxy → gateway
│   │   │       ├── users_controller.rb     # Proxy → gateway
│   │   │       └── admin_controller.rb     # Proxy → gateway
│   │   ├── models/
│   │   │   ├── user.rb                     # canonical_user_id, display_name
│   │   │   ├── game.rb                     # game_type, state, game_data (JSONB)
│   │   │   ├── game_player.rb             # seat, AI personality, result
│   │   │   └── move.rb                     # action (JSONB), commentary
│   │   ├── channels/
│   │   │   ├── game_channel.rb            # Game state broadcasts (direct)
│   │   │   └── chat_channel.rb            # Proxies to Python gateway WS
│   │   └── services/
│   │       ├── games/
│   │       │   ├── blackjack_engine.rb    # Game rules
│   │       │   └── checkers_engine.rb     # Game rules
│   │       ├── clara_api.rb               # Calls Python game adapter
│   │       └── gateway_proxy.rb           # HTTP proxy to Python gateway
│   ├── config/
│   │   ├── routes.rb
│   │   ├── cable.yml
│   │   └── environments/
│   ├── db/
│   │   ├── migrate/                       # Game tables only
│   │   └── schema.rb
│   ├── Gemfile
│   ├── Dockerfile
│   └── railway.toml
│
├── frontend/                       # React SPA (source code, not a service)
│   ├── src/
│   │   ├── main.tsx                       # Entry point
│   │   ├── App.tsx                        # React Router setup
│   │   ├── api/
│   │   │   └── client.ts                  # Typed API client (all endpoints)
│   │   ├── auth/
│   │   │   ├── AuthProvider.tsx
│   │   │   └── OAuthCallback.tsx
│   │   ├── pages/
│   │   │   ├── Chat.tsx                   # From web-ui
│   │   │   ├── KnowledgeBase.tsx          # From web-ui
│   │   │   ├── GraphExplorer.tsx          # From web-ui
│   │   │   ├── Intentions.tsx             # From web-ui
│   │   │   ├── Settings.tsx               # From web-ui
│   │   │   ├── AdminUsers.tsx             # From web-ui
│   │   │   ├── Login.tsx                  # From web-ui
│   │   │   ├── PendingApproval.tsx        # From web-ui
│   │   │   ├── Suspended.tsx              # From web-ui
│   │   │   ├── Lobby.tsx                  # From games (port to React Router)
│   │   │   ├── Blackjack.tsx              # From games (port to React Router)
│   │   │   ├── Checkers.tsx               # From games (port to React Router)
│   │   │   ├── GameHistory.tsx            # From games (port)
│   │   │   └── Replay.tsx                 # From games (port)
│   │   ├── components/
│   │   │   ├── layout/
│   │   │   │   ├── AppLayout.tsx          # Main layout with unified sidebar
│   │   │   │   └── UnifiedSidebar.tsx     # Nav: Chat, Knowledge, Graph, Games, etc.
│   │   │   ├── chat/                      # From web-ui
│   │   │   ├── knowledge/                 # From web-ui
│   │   │   ├── graph/                     # From web-ui
│   │   │   ├── settings/                  # From web-ui
│   │   │   ├── assistant-ui/              # From web-ui
│   │   │   ├── games/                     # From games app
│   │   │   │   ├── Card.tsx
│   │   │   │   ├── PlayerHand.tsx
│   │   │   │   ├── DealerArea.tsx
│   │   │   │   ├── CheckerBoard.tsx
│   │   │   │   ├── CheckerPiece.tsx
│   │   │   │   ├── ClaraSprite.tsx
│   │   │   │   ├── SpeechBubble.tsx
│   │   │   │   └── GameCard.tsx
│   │   │   └── ui/                        # shadcn/ui components
│   │   ├── stores/                        # Zustand stores
│   │   ├── hooks/
│   │   └── lib/
│   ├── package.json
│   ├── vite.config.ts
│   ├── tsconfig.json
│   └── index.html
│
└── docker-compose.yml              # Dev environment (Rails + frontend dev server)
```

## Rails Backend Details

### API Endpoints

#### Direct (own database)

```
POST   /auth/dev-login                  # Dev mode auto-login
GET    /auth/login/:provider            # OAuth redirect
GET    /auth/callback/:provider         # OAuth callback → JWT
POST   /auth/logout                     # Clear cookie
GET    /auth/me                         # Current user
GET    /auth/config                     # Auth providers config
POST   /auth/link/:provider             # Link additional account
DELETE /auth/link/:provider             # Unlink account

POST   /api/v1/games                    # Create game
GET    /api/v1/games/:id                # Game state
POST   /api/v1/games/:id/move           # Human move
POST   /api/v1/games/:id/ai_move        # AI move (calls Python adapter)
GET    /api/v1/lobby                    # Game stats
GET    /api/v1/history                  # Game history
GET    /api/v1/history/:id              # Replay data
```

#### Proxy (forwarded to Python gateway)

```
# Sessions
GET    /api/v1/sessions                 → gateway
GET    /api/v1/sessions/:id             → gateway
PUT    /api/v1/sessions/:id             → gateway
PATCH  /api/v1/sessions/:id/archive     → gateway
PATCH  /api/v1/sessions/:id/unarchive   → gateway
DELETE /api/v1/sessions/:id             → gateway

# Memories
GET    /api/v1/memories                 → gateway
GET    /api/v1/memories/stats           → gateway
GET    /api/v1/memories/:id             → gateway
POST   /api/v1/memories                 → gateway
PUT    /api/v1/memories/:id             → gateway
DELETE /api/v1/memories/:id             → gateway
GET    /api/v1/memories/:id/history     → gateway
GET    /api/v1/memories/:id/dynamics    → gateway
POST   /api/v1/memories/search          → gateway
PUT    /api/v1/memories/:id/tags        → gateway
GET    /api/v1/memories/tags/all        → gateway
GET    /api/v1/memories/export          → gateway
POST   /api/v1/memories/import          → gateway

# Graph
GET    /api/v1/graph/entities           → gateway
GET    /api/v1/graph/entities/:name     → gateway
GET    /api/v1/graph/search             → gateway
GET    /api/v1/graph/subgraph           → gateway

# Intentions
GET    /api/v1/intentions               → gateway
POST   /api/v1/intentions               → gateway
PUT    /api/v1/intentions/:id           → gateway
DELETE /api/v1/intentions/:id           → gateway

# Users
GET    /api/v1/users/me                 → gateway
PUT    /api/v1/users/me                 → gateway
GET    /api/v1/users/me/links           → gateway

# Admin
GET    /api/v1/admin/users              → gateway
POST   /api/v1/admin/users/:id/approve  → gateway
POST   /api/v1/admin/users/:id/suspend  → gateway
GET    /api/v1/admin/users/pending/count → gateway
```

#### WebSocket

```
ActionCable /cable
  ├── GameChannel    # Direct: game state broadcasts
  └── ChatChannel    # Proxy: relays to/from Python gateway WebSocket
```

### GatewayProxy Service

```ruby
class GatewayProxy
  # Forwards HTTP requests to Python gateway, passing:
  #   - X-Canonical-User-Id header (from JWT)
  #   - Original query params and body
  #   - Returns gateway response as-is to client

  def initialize
    @base_url = ENV.fetch("CLARA_GATEWAY_API_URL", "http://127.0.0.1:18789")
  end

  def forward(method:, path:, params:, body:, user_id:)
    # HTTP request to gateway with proper headers
  end
end
```

### Database (Game Tables Only)

```
users
  - id (PK)
  - canonical_user_id (unique)
  - display_name
  - avatar_url
  - timestamps

games
  - id (PK)
  - game_type (string)
  - state (string: waiting/in_progress/resolved)
  - game_data (JSONB)
  - move_count (integer)
  - current_turn (string)
  - created_by_id (FK → users)
  - started_at, finished_at
  - timestamps

game_players
  - id (PK)
  - game_id (FK)
  - user_id (FK, nullable)
  - ai_personality (string, nullable)
  - seat_position (integer)
  - player_state (string)
  - hand_data (JSONB)
  - result (string, nullable)
  - timestamps

moves
  - id (PK)
  - game_id (FK)
  - game_player_id (FK)
  - move_number (integer)
  - action (JSONB)
  - game_data_snapshot (JSONB)
  - clara_commentary (text)
  - timestamps
```

## Python Gateway Changes

Move HTTP API routes from `mypalclara/web/api/` into the gateway process. The gateway already runs an async server; add HTTP endpoints alongside the existing WebSocket.

### New Gateway HTTP Endpoints

All existing `mypalclara/web/api/` routes become gateway routes:
- `/api/v1/sessions/*`
- `/api/v1/memories/*`
- `/api/v1/graph/*`
- `/api/v1/intentions/*`
- `/api/v1/users/*`
- `/api/v1/admin/*`

Auth: Gateway trusts `X-Canonical-User-Id` header from Rails (internal network). Optional `X-Gateway-Secret` for verification.

### Files to Move

```
mypalclara/web/api/sessions.py    → mypalclara/gateway/api/sessions.py
mypalclara/web/api/memories.py    → mypalclara/gateway/api/memories.py
mypalclara/web/api/graph.py       → mypalclara/gateway/api/graph.py
mypalclara/web/api/intentions.py  → mypalclara/gateway/api/intentions.py
mypalclara/web/api/users.py       → mypalclara/gateway/api/users.py
mypalclara/web/api/admin.py       → mypalclara/gateway/api/admin.py
mypalclara/web/api/game.py        → mypalclara/gateway/api/game.py
```

Auth dependencies (`mypalclara/web/auth/`) are NOT moved — Rails handles auth. Gateway endpoints accept `X-Canonical-User-Id` directly.

## Frontend Details

### Porting Games from Inertia.js to React Router

Current games pages receive data as Inertia props from the server. In the new SPA:
- Pages fetch data from Rails API using the typed API client
- `useEffect` + `useState` (or React Query) replaces Inertia page props
- ActionCable JS client connects directly for real-time game updates
- React Router handles navigation instead of Inertia visits

### Unified Navigation

Sidebar sections:
1. **Chat** — conversation list + new chat
2. **Knowledge** — memory search/browse
3. **Graph** — entity explorer
4. **Games** — lobby, active games
5. **Intentions** — trigger management
6. **Settings** — profile, account linking
7. **Admin** — user management (admin only)

### Shared Dependencies

From web-ui: React 19, TypeScript, Vite, React Router, Zustand, TailwindCSS, shadcn/ui, @assistant-ui/react, @xyflow/react, D3-force, React Query.

From games: @rails/actioncable (for game WebSockets).

## Auth Flow

1. Browser → Rails `/auth/login/discord` → Discord OAuth → callback
2. Rails creates/updates `User` record, signs JWT
3. JWT stored as httpOnly cookie
4. All API requests carry cookie
5. For proxied requests, Rails extracts `canonical_user_id` from JWT and forwards as `X-Canonical-User-Id` header to gateway
6. Gateway trusts the header (internal network)

## Deployment

### Production (Railway)

```
Service 1: web-ui/backend (Rails + built SPA)
  - Dockerfile: multi-stage (Node builds frontend, Ruby runs Rails)
  - Rails serves SPA from public/ directory
  - Own PostgreSQL for game tables
  - Connects to Python gateway over internal network

Service 2: mypalclara gateway (Python)
  - WebSocket + HTTP API
  - Main PostgreSQL + Qdrant/pgvector
  - No frontend serving
```

### Development

```
Terminal 1: cd web-ui/backend && rails s          # Rails on :3000
Terminal 2: cd web-ui/frontend && npm run dev      # Vite on :5173
Terminal 3: python -m mypalclara.gateway start     # Gateway on :18789
```

Vite dev server proxies `/api` and `/cable` to Rails in development.

## Files to Delete

```
games/                          # Entire directory (absorbed into web-ui/backend/)
mypalclara/web/                 # Entire module (APIs moved to gateway)
web-ui/ (current)               # Replaced by web-ui/frontend/
Dockerfile.web                  # Replaced by web-ui/backend/Dockerfile
```

## Migration Plan

1. Add HTTP API routes to Python gateway (move from `mypalclara/web/api/`)
2. Create Rails API app in `web-ui/backend/` (start from `games/` codebase)
3. Add GatewayProxy service and proxy controllers
4. Create React SPA in `web-ui/frontend/` (merge existing frontends)
5. Port game pages from Inertia.js to React Router
6. Unified auth (single JWT, single OAuth flow)
7. Unified Dockerfile (multi-stage build)
8. Delete old directories (`games/`, `mypalclara/web/`, old `web-ui/`)
9. Update CLAUDE.md and deployment configs
