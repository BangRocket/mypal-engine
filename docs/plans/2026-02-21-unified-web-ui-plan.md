# Unified Web-UI Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Merge games/ (Rails+React) and web-ui/ (Vite+React) into a single unified service at web-ui/backend/ (Rails API) + web-ui/frontend/ (React SPA), with Python web API routes moved into the gateway.

**Architecture:** Rails API-only app serves as BFF — handles game logic directly (own PostgreSQL) and proxies all other requests (sessions, memories, graph, intentions, users, admin) to the Python gateway's new HTTP API. React SPA (Vite) is isolated source code that builds to static assets. The Python `mypalclara/web/` module is deleted; its API routes move into `mypalclara/gateway/api/`.

**Tech Stack:** Ruby on Rails 8.1 (API-only), React 19, TypeScript, Vite, React Router 7, Zustand, TailwindCSS, shadcn/ui, ActionCable, @assistant-ui/react

**Design Doc:** `docs/plans/2026-02-21-unified-web-ui-design.md`

---

## Task 1: Move Python Web API Routes into Gateway

Move the HTTP API endpoints from `mypalclara/web/api/` into `mypalclara/gateway/api/` so the gateway serves both WebSocket and HTTP. The gateway currently uses the `websockets` library on port 18789. We'll add a FastAPI HTTP server on a separate port (default 18790) running in the same async event loop.

**Files:**
- Create: `mypalclara/gateway/api/__init__.py`
- Create: `mypalclara/gateway/api/app.py` — FastAPI app factory for gateway HTTP API
- Create: `mypalclara/gateway/api/auth.py` — simple auth (trust X-Canonical-User-Id header)
- Copy+adapt: `mypalclara/web/api/sessions.py` → `mypalclara/gateway/api/sessions.py`
- Copy+adapt: `mypalclara/web/api/memories.py` → `mypalclara/gateway/api/memories.py`
- Copy+adapt: `mypalclara/web/api/graph.py` → `mypalclara/gateway/api/graph.py`
- Copy+adapt: `mypalclara/web/api/intentions.py` → `mypalclara/gateway/api/intentions.py`
- Copy+adapt: `mypalclara/web/api/users.py` → `mypalclara/gateway/api/users.py`
- Copy+adapt: `mypalclara/web/api/admin.py` → `mypalclara/gateway/api/admin.py`
- Copy+adapt: `mypalclara/web/api/game.py` → `mypalclara/gateway/api/game.py`
- Modify: `mypalclara/gateway/__main__.py` — start HTTP server alongside WebSocket
- Modify: `mypalclara/gateway/server.py` — expose references for HTTP routes

### Step 1: Create gateway API package and auth

Create `mypalclara/gateway/api/__init__.py` (empty).

Create `mypalclara/gateway/api/auth.py`:
```python
"""Gateway HTTP API authentication.

Rails sends X-Canonical-User-Id header. Gateway trusts it (internal network).
Optional X-Gateway-Secret for verification.
"""
from __future__ import annotations

import os
from fastapi import Header, HTTPException, Depends, status
from sqlalchemy.orm import Session as DBSession
from mypalclara.db.connection import SessionLocal
from mypalclara.db.models import CanonicalUser


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_current_user(
    x_canonical_user_id: str = Header(...),
    x_gateway_secret: str | None = Header(None),
    db: DBSession = Depends(get_db),
) -> CanonicalUser:
    """Extract user from X-Canonical-User-Id header.

    Optionally verify X-Gateway-Secret matches CLARA_GATEWAY_SECRET env var.
    """
    expected_secret = os.getenv("CLARA_GATEWAY_SECRET")
    if expected_secret and x_gateway_secret != expected_secret:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid gateway secret")

    user = db.query(CanonicalUser).filter(CanonicalUser.id == x_canonical_user_id).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return user


def get_approved_user(user: CanonicalUser = Depends(get_current_user)) -> CanonicalUser:
    if getattr(user, "status", "active") != "active":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"Account is {user.status}")
    return user


def get_admin_user(user: CanonicalUser = Depends(get_approved_user)) -> CanonicalUser:
    if not getattr(user, "is_admin", False):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return user
```

### Step 2: Copy and adapt API route files

For each file in `mypalclara/web/api/` (sessions, memories, graph, intentions, users, admin, game), copy to `mypalclara/gateway/api/` and change imports:

- `from mypalclara.web.auth.dependencies import get_approved_user, get_admin_user, get_db` → `from mypalclara.gateway.api.auth import get_approved_user, get_admin_user, get_db`
- Remove any references to `mypalclara.web.config` (use `os.getenv()` directly if needed)
- Keep all other imports (SQLAlchemy models, Rook, etc.) unchanged

### Step 3: Create gateway API app factory

Create `mypalclara/gateway/api/app.py`:
```python
"""FastAPI app for gateway HTTP API."""
from __future__ import annotations

from contextlib import asynccontextmanager
from fastapi import FastAPI
from mypalclara.gateway.api.sessions import router as sessions_router
from mypalclara.gateway.api.memories import router as memories_router
from mypalclara.gateway.api.graph import router as graph_router
from mypalclara.gateway.api.intentions import router as intentions_router
from mypalclara.gateway.api.users import router as users_router
from mypalclara.gateway.api.admin import router as admin_router
from mypalclara.gateway.api.game import router as game_router


def create_gateway_api() -> FastAPI:
    app = FastAPI(title="Clara Gateway API", version="1.0.0")
    app.include_router(sessions_router, prefix="/api/v1/sessions", tags=["sessions"])
    app.include_router(memories_router, prefix="/api/v1/memories", tags=["memories"])
    app.include_router(graph_router, prefix="/api/v1/graph", tags=["graph"])
    app.include_router(intentions_router, prefix="/api/v1/intentions", tags=["intentions"])
    app.include_router(users_router, prefix="/api/v1/users", tags=["users"])
    app.include_router(admin_router, prefix="/api/v1/admin", tags=["admin"])
    app.include_router(game_router, prefix="/api/v1/game", tags=["game"])
    return app
```

### Step 4: Add HTTP server startup to gateway

Modify `mypalclara/gateway/__main__.py` `_async_run_gateway()` function. After `await server.start()` (line 481), add:

```python
    # Start HTTP API server alongside WebSocket
    import uvicorn
    from mypalclara.gateway.api.app import create_gateway_api

    api_app = create_gateway_api()
    api_port = int(os.getenv("CLARA_GATEWAY_API_PORT", "18790"))
    api_config = uvicorn.Config(api_app, host=args.host, port=api_port, log_level="info")
    api_server = uvicorn.Server(api_config)
    api_task = asyncio.create_task(api_server.serve())
    logger.info(f"Gateway HTTP API started on http://{args.host}:{api_port}")
```

And in the shutdown section (before `await server.stop()`), add:
```python
    api_server.should_exit = True
    await api_task
```

### Step 5: Test gateway API startup

Run: `poetry run python -m mypalclara.gateway start -f --no-adapters`

Expected: Both WebSocket (18789) and HTTP API (18790) start. Verify with:
```bash
curl http://127.0.0.1:18790/docs  # Should show FastAPI Swagger docs
```

### Step 6: Commit

```bash
git add mypalclara/gateway/api/
git add mypalclara/gateway/__main__.py
git commit -m "feat: add HTTP API to gateway (moved from web module)"
```

---

## Task 2: Create Rails API App (Backend)

Create the Rails API-only app at `web-ui/backend/` by moving the existing `games/` app and converting it to API-only mode (remove Inertia.js, views, asset pipeline).

**Files:**
- Move: `games/` → `web-ui/backend/`
- Modify: `web-ui/backend/config/application.rb` — switch to API-only
- Modify: `web-ui/backend/Gemfile` — remove Inertia, add http client gems
- Modify: `web-ui/backend/config/routes.rb` — restructure under `/api/v1/`
- Delete: `web-ui/backend/app/views/` (Inertia layouts)
- Delete: `web-ui/backend/app/javascript/` (moves to frontend/)
- Modify: All controllers — return JSON instead of Inertia renders

### Step 1: Move games/ to web-ui/backend/

```bash
mkdir -p web-ui
mv games web-ui/backend
```

### Step 2: Convert to API-only Rails

Modify `web-ui/backend/config/application.rb`:
- Change `class Application < Rails::Application` to include `config.api_only = true`
- Remove `require "sprockets/railtie"` if present
- Remove Inertia-related configuration

Modify `web-ui/backend/Gemfile`:
- Remove: `inertia_rails`, `jsbundling-rails`, `turbo-rails`, `stimulus-rails`, `tailwindcss-rails`, `propshaft`
- Add: `httpparty` or `faraday` (for gateway proxy), `rack-cors`
- Keep: `rails`, `pg`, `puma`, `jwt`, `solid_cache`, `solid_queue`, `solid_cable`

### Step 3: Remove frontend files from backend

```bash
rm -rf web-ui/backend/app/javascript
rm -rf web-ui/backend/app/views
rm -rf web-ui/backend/app/assets
rm -f web-ui/backend/package.json web-ui/backend/tsconfig.json
```

### Step 4: Update controllers to JSON-only

**GamesController** — replace Inertia renders with JSON:

Replace `render inertia: ...` calls with `render json:` calls. The `game_props` helper already builds the right hash — just return it as JSON.

```ruby
# show action: replace Inertia render with JSON
def show
  render json: game_props(@game)
end
```

**LobbyController** — return JSON:
```ruby
def index
  render json: { recent_games: ..., stats: ... }
end
```

**HistoryController** — return JSON for both index and show.

**AuthController** — return JSON tokens instead of session redirects.

### Step 5: Restructure routes under /api/v1

Modify `web-ui/backend/config/routes.rb`:
```ruby
Rails.application.routes.draw do
  # Auth endpoints (not namespaced — frontend calls these directly)
  scope :auth do
    get "config", to: "auth#config"
    post "dev-login", to: "auth#dev_login"
    get "login/:provider", to: "auth#login"
    get "callback/:provider", to: "auth#callback"
    post "logout", to: "auth#logout"
    get "me", to: "auth#me"
    post "link/:provider", to: "auth#link"
    delete "link/:provider", to: "auth#unlink"
  end

  namespace :api do
    namespace :v1 do
      # Game endpoints (direct DB)
      resources :games, only: [:create, :show] do
        member do
          post :move
          post :ai_move
        end
      end
      get "lobby", to: "lobby#index"
      resources :history, only: [:index, :show]

      # Proxy endpoints (forwarded to Python gateway)
      # Sessions
      resources :sessions, only: [:index, :show, :update, :destroy] do
        member do
          patch :archive
          patch :unarchive
        end
      end

      # Memories
      resources :memories, only: [:index, :show, :create, :update, :destroy] do
        member do
          get :history
          get :dynamics
          put :tags
        end
        collection do
          get :stats
          post :search
          get "tags/all", to: "memories#all_tags"
          get :export
          post :import
        end
      end

      # Graph
      get "graph/entities", to: "graph#entities"
      get "graph/entities/:name", to: "graph#entity"
      get "graph/search", to: "graph#search"
      get "graph/subgraph", to: "graph#subgraph"

      # Intentions
      resources :intentions, only: [:index, :create, :update, :destroy]

      # Users
      get "users/me", to: "users#me"
      put "users/me", to: "users#update_me"
      get "users/me/links", to: "users#links"

      # Admin
      get "admin/users", to: "admin#users"
      post "admin/users/:id/approve", to: "admin#approve"
      post "admin/users/:id/suspend", to: "admin#suspend"
      get "admin/users/pending/count", to: "admin#pending_count"
    end
  end

  # Health check
  get "up" => "rails/health#show", as: :rails_health_check
end
```

### Step 6: Move controllers into Api::V1 namespace

Move existing controllers into `app/controllers/api/v1/`:
```bash
mkdir -p web-ui/backend/app/controllers/api/v1
mv web-ui/backend/app/controllers/games_controller.rb web-ui/backend/app/controllers/api/v1/
mv web-ui/backend/app/controllers/lobby_controller.rb web-ui/backend/app/controllers/api/v1/
mv web-ui/backend/app/controllers/history_controller.rb web-ui/backend/app/controllers/api/v1/
```

Wrap each controller class in `module Api; module V1; ... end; end` namespace.

Keep `AuthController` at top level (not namespaced).

### Step 7: Run and verify

```bash
cd web-ui/backend && bundle install && rails db:migrate && rails s
curl http://localhost:3000/api/v1/lobby  # Should return JSON
```

### Step 8: Commit

```bash
git add web-ui/backend/
git rm -r games/
git commit -m "refactor: move games/ to web-ui/backend/, convert to API-only Rails"
```

---

## Task 3: Add Gateway Proxy Service to Rails

Create the `GatewayProxy` service in Rails that forwards requests to the Python gateway HTTP API.

**Files:**
- Create: `web-ui/backend/app/services/gateway_proxy.rb`
- Create: `web-ui/backend/app/controllers/api/v1/sessions_controller.rb`
- Create: `web-ui/backend/app/controllers/api/v1/memories_controller.rb`
- Create: `web-ui/backend/app/controllers/api/v1/graph_controller.rb`
- Create: `web-ui/backend/app/controllers/api/v1/intentions_controller.rb`
- Create: `web-ui/backend/app/controllers/api/v1/users_controller.rb`
- Create: `web-ui/backend/app/controllers/api/v1/admin_controller.rb`

### Step 1: Create GatewayProxy service

```ruby
# app/services/gateway_proxy.rb
class GatewayProxy
  BASE_URL = ENV.fetch("CLARA_GATEWAY_API_URL", "http://127.0.0.1:18790")
  SECRET = ENV["CLARA_GATEWAY_SECRET"]

  def self.forward(method:, path:, user_id:, params: {}, body: nil)
    uri = URI("#{BASE_URL}#{path}")
    uri.query = URI.encode_www_form(params) if params.any?

    http = Net::HTTP.new(uri.host, uri.port)
    http.open_timeout = 5
    http.read_timeout = 30

    request = case method
              when :get    then Net::HTTP::Get.new(uri)
              when :post   then Net::HTTP::Post.new(uri)
              when :put    then Net::HTTP::Put.new(uri)
              when :patch  then Net::HTTP::Patch.new(uri)
              when :delete then Net::HTTP::Delete.new(uri)
              end

    request["Content-Type"] = "application/json"
    request["X-Canonical-User-Id"] = user_id
    request["X-Gateway-Secret"] = SECRET if SECRET
    request.body = body.to_json if body

    response = http.request(request)
    { status: response.code.to_i, body: JSON.parse(response.body), headers: response.to_hash }
  rescue JSON::ParserError
    { status: response.code.to_i, body: response.body, headers: response.to_hash }
  rescue StandardError => e
    { status: 502, body: { error: "Gateway unavailable: #{e.message}" }, headers: {} }
  end
end
```

### Step 2: Create proxy controllers

Each proxy controller follows the same pattern — extract user from JWT, forward to gateway, return response:

```ruby
# app/controllers/api/v1/sessions_controller.rb
module Api
  module V1
    class SessionsController < ApplicationController
      before_action :authenticate_user!

      def index
        result = GatewayProxy.forward(
          method: :get, path: "/api/v1/sessions",
          user_id: current_user.canonical_user_id,
          params: request.query_parameters
        )
        render json: result[:body], status: result[:status]
      end

      def show
        result = GatewayProxy.forward(
          method: :get, path: "/api/v1/sessions/#{params[:id]}",
          user_id: current_user.canonical_user_id
        )
        render json: result[:body], status: result[:status]
      end

      # ... update, destroy, archive, unarchive follow same pattern
    end
  end
end
```

Repeat for memories, graph, intentions, users, admin controllers. Each method:
1. Calls `GatewayProxy.forward()` with appropriate method/path/params/body
2. Returns the response status and body as-is

### Step 3: Add JWT authentication to ApplicationController

The current games app uses Rails session-based auth. Adapt to JWT:

```ruby
# app/controllers/application_controller.rb
class ApplicationController < ActionController::API
  private

  def authenticate_user!
    token = extract_token
    return render json: { error: "Not authenticated" }, status: :unauthorized unless token

    payload = decode_jwt(token)
    return render json: { error: "Invalid token" }, status: :unauthorized unless payload

    @current_user = User.find_or_create_by(canonical_user_id: payload["sub"]) do |u|
      u.display_name = payload["name"] || "User"
    end
  end

  def current_user
    @current_user
  end

  def extract_token
    auth_header = request.headers["Authorization"]
    return auth_header.split(" ").last if auth_header&.start_with?("Bearer ")
    cookies[:access_token]
  end

  def decode_jwt(token)
    secret = ENV.fetch("WEB_SECRET_KEY", "change-me-in-production")
    JWT.decode(token, secret, true, algorithm: "HS256").first
  rescue JWT::DecodeError
    nil
  end
end
```

### Step 4: Test proxy endpoints

Start gateway with HTTP API, then start Rails:
```bash
# Terminal 1
poetry run python -m mypalclara.gateway start -f --no-adapters
# Terminal 2
cd web-ui/backend && rails s -p 3000
# Terminal 3 — test proxy
curl -H "Authorization: Bearer <test-token>" http://localhost:3000/api/v1/sessions
```

### Step 5: Commit

```bash
git add web-ui/backend/app/services/gateway_proxy.rb
git add web-ui/backend/app/controllers/api/v1/
git add web-ui/backend/app/controllers/application_controller.rb
git commit -m "feat: add gateway proxy service and proxy controllers"
```

---

## Task 4: Add Chat WebSocket Proxy (ActionCable)

Add a `ChatChannel` in Rails that proxies WebSocket chat to the Python gateway.

**Files:**
- Create: `web-ui/backend/app/channels/chat_channel.rb`
- Modify: `web-ui/backend/config/cable.yml` (ensure async or solid_cable adapter)
- Create: `web-ui/backend/app/services/gateway_ws_client.rb`

### Step 1: Create gateway WebSocket client

```ruby
# app/services/gateway_ws_client.rb
require "websocket-client-simple"

class GatewayWsClient
  GATEWAY_URL = ENV.fetch("CLARA_GATEWAY_URL", "ws://127.0.0.1:18789/ws")

  # This service manages a persistent WebSocket connection to the Python gateway
  # and routes responses back to ActionCable subscribers by request_id.

  def initialize
    @callbacks = {}
    @ws = nil
    @mutex = Mutex.new
  end

  def connect
    @ws = WebSocket::Client::Simple.connect(GATEWAY_URL)
    setup_handlers
  end

  def send_message(request_id:, content:, user_id:, display_name:, tier: nil)
    # Register message and send to gateway
    # Format matches gateway protocol (MessageRequest)
    msg = {
      type: "message",
      request_id: request_id,
      user: { id: user_id, name: display_name },
      channel: { id: "web-chat", type: "dm", name: "Web Chat" },
      content: content,
      platform: "web",
      capabilities: ["streaming", "tool_display"],
    }
    msg[:tier_override] = tier if tier
    @ws.send(msg.to_json)
  end

  def register_callback(request_id, &block)
    @mutex.synchronize { @callbacks[request_id] = block }
  end

  def unregister_callback(request_id)
    @mutex.synchronize { @callbacks.delete(request_id) }
  end

  private

  def setup_handlers
    @ws.on :message do |msg|
      data = JSON.parse(msg.data)
      rid = data["request_id"]
      @mutex.synchronize do
        @callbacks[rid]&.call(data)
      end
    end
  end
end
```

### Step 2: Create ChatChannel

```ruby
# app/channels/chat_channel.rb
class ChatChannel < ApplicationCable::Channel
  def subscribed
    stream_for current_user
  end

  def receive(data)
    # Client sends: { "content": "hello", "tier": "mid" }
    request_id = SecureRandom.uuid

    GatewayWsClient.instance.register_callback(request_id) do |event|
      ChatChannel.broadcast_to(current_user, event)
    end

    GatewayWsClient.instance.send_message(
      request_id: request_id,
      content: data["content"],
      user_id: "web-#{current_user.canonical_user_id}",
      display_name: current_user.display_name,
      tier: data["tier"]
    )
  end

  def unsubscribed
    # Cleanup
  end
end
```

### Step 3: Test chat flow

Verify ActionCable connection and message round-trip with a simple WebSocket client or browser console test.

### Step 4: Commit

```bash
git add web-ui/backend/app/channels/chat_channel.rb
git add web-ui/backend/app/services/gateway_ws_client.rb
git commit -m "feat: add ChatChannel for gateway WebSocket proxy"
```

---

## Task 5: Update Rails Auth (Unified JWT + OAuth)

Unify authentication: Rails handles OAuth (Discord, Google) and issues JWTs. Port the auth logic from `mypalclara/web/auth/` to Rails.

**Files:**
- Modify: `web-ui/backend/app/controllers/auth_controller.rb` — full OAuth + JWT
- Create: `web-ui/backend/app/services/jwt_service.rb`
- Create: `web-ui/backend/app/services/oauth_service.rb`

### Step 1: Create JWT service

```ruby
# app/services/jwt_service.rb
class JwtService
  SECRET = ENV.fetch("WEB_SECRET_KEY", "change-me-in-production")
  EXPIRE_MINUTES = ENV.fetch("WEB_JWT_EXPIRE_MINUTES", "1440").to_i

  def self.encode(canonical_user_id, extra_claims = {})
    payload = {
      sub: canonical_user_id,
      exp: EXPIRE_MINUTES.minutes.from_now.to_i,
      iat: Time.now.to_i,
    }.merge(extra_claims)
    JWT.encode(payload, SECRET, "HS256")
  end

  def self.decode(token)
    JWT.decode(token, SECRET, true, algorithm: "HS256").first
  rescue JWT::DecodeError
    nil
  end
end
```

### Step 2: Create OAuth service

Port the Discord/Google OAuth flows from `mypalclara/web/auth/oauth.py`:

```ruby
# app/services/oauth_service.rb
class OauthService
  PROVIDERS = {
    "discord" => {
      authorize_url: "https://discord.com/api/oauth2/authorize",
      token_url: "https://discord.com/api/oauth2/token",
      user_url: "https://discord.com/api/users/@me",
      scope: "identify email",
      client_id_env: "DISCORD_OAUTH_CLIENT_ID",
      client_secret_env: "DISCORD_OAUTH_CLIENT_SECRET",
      redirect_uri_env: "DISCORD_OAUTH_REDIRECT_URI",
    },
    "google" => {
      authorize_url: "https://accounts.google.com/o/oauth2/v2/auth",
      token_url: "https://oauth2.googleapis.com/token",
      user_url: "https://www.googleapis.com/oauth2/v2/userinfo",
      scope: "openid email profile",
      client_id_env: "GOOGLE_OAUTH_CLIENT_ID",
      client_secret_env: "GOOGLE_OAUTH_CLIENT_SECRET",
      redirect_uri_env: "GOOGLE_OAUTH_REDIRECT_URI",
    }
  }.freeze

  def self.authorize_url(provider)
    cfg = PROVIDERS.fetch(provider)
    params = {
      client_id: ENV[cfg[:client_id_env]],
      redirect_uri: ENV[cfg[:redirect_uri_env]],
      response_type: "code",
      scope: cfg[:scope],
    }
    "#{cfg[:authorize_url]}?#{URI.encode_www_form(params)}"
  end

  def self.exchange_code(provider, code)
    cfg = PROVIDERS.fetch(provider)
    response = Net::HTTP.post_form(
      URI(cfg[:token_url]),
      client_id: ENV[cfg[:client_id_env]],
      client_secret: ENV[cfg[:client_secret_env]],
      grant_type: "authorization_code",
      code: code,
      redirect_uri: ENV[cfg[:redirect_uri_env]],
    )
    JSON.parse(response.body)
  end

  def self.fetch_user(provider, access_token)
    cfg = PROVIDERS.fetch(provider)
    uri = URI(cfg[:user_url])
    req = Net::HTTP::Get.new(uri)
    req["Authorization"] = "Bearer #{access_token}"
    response = Net::HTTP.start(uri.hostname, uri.port, use_ssl: true) { |http| http.request(req) }
    JSON.parse(response.body)
  end
end
```

### Step 3: Rewrite AuthController

```ruby
# app/controllers/auth_controller.rb
class AuthController < ApplicationController
  skip_before_action :authenticate_user!, raise: false

  def config
    render json: {
      dev_mode: ENV["WEB_DEV_MODE"] == "true",
      providers: OauthService::PROVIDERS.keys.select { |p|
        ENV[OauthService::PROVIDERS[p][:client_id_env]].present?
      }
    }
  end

  def dev_login
    return render json: { error: "Dev mode disabled" }, status: :forbidden unless ENV["WEB_DEV_MODE"] == "true"
    user = User.find_or_create_by(canonical_user_id: "00000000-0000-0000-0000-000000000dev") do |u|
      u.display_name = ENV.fetch("WEB_DEV_USER_NAME", "Dev User")
    end
    token = JwtService.encode(user.canonical_user_id, name: user.display_name)
    set_cookie(token)
    render json: { token: token, user: user_json(user) }
  end

  def login
    url = OauthService.authorize_url(params[:provider])
    render json: { url: url }
  end

  def callback
    token_data = OauthService.exchange_code(params[:provider], params[:code])
    profile = OauthService.fetch_user(params[:provider], token_data["access_token"])

    # Find or create user + platform link via gateway proxy
    user = find_or_create_user(params[:provider], profile)

    jwt = JwtService.encode(user.canonical_user_id, name: user.display_name)
    set_cookie(jwt)
    render json: { token: jwt, user: user_json(user) }
  end

  def logout
    cookies.delete(:access_token)
    render json: { ok: true }
  end

  def me
    authenticate_user!
    render json: user_json(current_user)
  end

  private

  def set_cookie(token)
    cookies[:access_token] = {
      value: token,
      httponly: true,
      secure: Rails.env.production?,
      same_site: :lax,
      expires: ENV.fetch("WEB_JWT_EXPIRE_MINUTES", "1440").to_i.minutes.from_now
    }
  end

  def user_json(user)
    { id: user.canonical_user_id, display_name: user.display_name, avatar_url: user.avatar_url }
  end

  def find_or_create_user(provider, profile)
    # Extract platform user ID and create/update local User record
    platform_id = case provider
                  when "discord" then profile["id"]
                  when "google" then profile["id"]
                  end
    display_name = profile["global_name"] || profile["username"] || profile["name"] || "User"

    user = User.find_by(canonical_user_id: platform_id) || User.create!(
      canonical_user_id: SecureRandom.uuid,
      display_name: display_name,
      avatar_url: profile["avatar"] ? "https://cdn.discordapp.com/avatars/#{platform_id}/#{profile["avatar"]}.png" : nil
    )

    # Also create user in main DB via gateway proxy
    GatewayProxy.forward(
      method: :post, path: "/api/v1/users/ensure",
      user_id: user.canonical_user_id,
      body: { provider: provider, platform_user_id: platform_id, display_name: display_name }
    )

    user
  end
end
```

### Step 4: Commit

```bash
git add web-ui/backend/app/services/jwt_service.rb
git add web-ui/backend/app/services/oauth_service.rb
git add web-ui/backend/app/controllers/auth_controller.rb
git commit -m "feat: unified JWT + OAuth auth in Rails"
```

---

## Task 6: Create React SPA (Frontend)

Create the unified React SPA at `web-ui/frontend/` by merging the existing `web-ui/` Vite app with the games React components.

**Files:**
- Move: `web-ui/src/` → `web-ui/frontend/src/` (existing web-ui React code)
- Move: `web-ui/package.json` etc. → `web-ui/frontend/`
- Copy: games React pages from `web-ui/backend/app/javascript/pages/` → `web-ui/frontend/src/pages/`
- Copy: games React components from `web-ui/backend/app/javascript/components/` → `web-ui/frontend/src/components/games/`
- Modify: `web-ui/frontend/src/App.tsx` — add game routes
- Modify: `web-ui/frontend/src/api/client.ts` — add game API methods
- Modify: `web-ui/frontend/src/components/layout/UnifiedSidebar.tsx` — add Games nav

### Step 1: Restructure directories

```bash
# Move existing web-ui frontend files into web-ui/frontend/
mkdir -p web-ui/frontend
# Move all web-ui files except the backend/ directory
mv web-ui/src web-ui/frontend/src
mv web-ui/package.json web-ui/frontend/
mv web-ui/vite.config.ts web-ui/frontend/
mv web-ui/tsconfig.json web-ui/frontend/
mv web-ui/index.html web-ui/frontend/
mv web-ui/tsconfig.app.json web-ui/frontend/ 2>/dev/null || true
mv web-ui/tsconfig.node.json web-ui/frontend/ 2>/dev/null || true
mv web-ui/postcss.config.* web-ui/frontend/ 2>/dev/null || true
mv web-ui/tailwind.config.* web-ui/frontend/ 2>/dev/null || true
mv web-ui/components.json web-ui/frontend/ 2>/dev/null || true
mv web-ui/.gitignore web-ui/frontend/ 2>/dev/null || true
mv web-ui/pnpm-lock.yaml web-ui/frontend/ 2>/dev/null || true
mv web-ui/package-lock.json web-ui/frontend/ 2>/dev/null || true
```

### Step 2: Copy game React code into frontend

```bash
# Copy game pages (will be adapted in next step)
cp web-ui/backend/app/javascript/pages/Lobby.tsx web-ui/frontend/src/pages/
cp web-ui/backend/app/javascript/pages/Blackjack.tsx web-ui/frontend/src/pages/
cp web-ui/backend/app/javascript/pages/Checkers.tsx web-ui/frontend/src/pages/
cp web-ui/backend/app/javascript/pages/History.tsx web-ui/frontend/src/pages/GameHistory.tsx
cp web-ui/backend/app/javascript/pages/Replay.tsx web-ui/frontend/src/pages/

# Copy game components
mkdir -p web-ui/frontend/src/components/games
cp web-ui/backend/app/javascript/components/*.tsx web-ui/frontend/src/components/games/
```

### Step 3: Port game pages from Inertia to React Router

For each game page, replace:
- `usePage<Props>()` → `useQuery()` or `useLoaderData()` for data fetching
- `router.visit()` → `useNavigate()` + `navigate()`
- `router.post()` → `fetch()` with API client
- `createConsumer()` → ActionCable client imported from `@rails/actioncable`
- Inertia CSRF tokens → Bearer token from auth context

Example transformation for Lobby.tsx:
```typescript
// Before (Inertia):
import { usePage, router } from "@inertiajs/react"
const { user, recent_games, stats } = usePage<Props>().props
router.post("/games", { game_type, ai_players })

// After (React Router + API client):
import { useQuery, useMutation } from "@tanstack/react-query"
import { useNavigate } from "react-router-dom"
import { api } from "@/api/client"

const { data } = useQuery({ queryKey: ["lobby"], queryFn: () => api.games.lobby() })
const createGame = useMutation({
  mutationFn: (body) => api.games.create(body),
  onSuccess: (game) => navigate(`/games/${game.id}`)
})
```

### Step 4: Add game API methods to client.ts

Add to `web-ui/frontend/src/api/client.ts`:
```typescript
games: {
  lobby: () => request<LobbyData>("/api/v1/lobby"),
  create: (body: { game_type: string; ai_players: string[] }) =>
    request<GameData>("/api/v1/games", { method: "POST", body: JSON.stringify(body) }),
  show: (id: number) => request<GameData>(`/api/v1/games/${id}`),
  move: (id: number, body: { move_type: string }) =>
    request<MoveResponse>(`/api/v1/games/${id}/move`, { method: "POST", body: JSON.stringify(body) }),
  aiMove: (id: number, body: { game_player_id: number }) =>
    request<MoveResponse>(`/api/v1/games/${id}/ai_move`, { method: "POST", body: JSON.stringify(body) }),
  history: () => request<HistoryData[]>("/api/v1/history"),
  replay: (id: number) => request<ReplayData>(`/api/v1/history/${id}`),
},
```

### Step 5: Add game routes to App.tsx

```typescript
// In App.tsx, add inside the protected routes:
<Route path="/games" element={<Lobby />} />
<Route path="/games/:id" element={<GameWrapper />} />
<Route path="/games/history" element={<GameHistory />} />
<Route path="/games/history/:id" element={<Replay />} />
```

Where `GameWrapper` checks `game.game_type` and renders `Blackjack` or `Checkers`.

### Step 6: Update UnifiedSidebar

Add Games section to the sidebar navigation:
```typescript
// In UnifiedSidebar.tsx, add nav item:
{ icon: Gamepad2, label: "Games", path: "/games" }
```

### Step 7: Update Vite config

Update `web-ui/frontend/vite.config.ts` proxy targets to point to Rails (port 3000):
```typescript
server: {
  proxy: {
    "/api": "http://localhost:3000",
    "/auth": "http://localhost:3000",
    "/cable": { target: "ws://localhost:3000", ws: true },
  }
}
```

### Step 8: Add ActionCable dependency

```bash
cd web-ui/frontend && npm install @rails/actioncable
```

### Step 9: Install and verify

```bash
cd web-ui/frontend && npm install && npm run dev
```

### Step 10: Commit

```bash
git add web-ui/frontend/
git commit -m "feat: unified React SPA with games + web-ui merged"
```

---

## Task 7: Add SPA Serving to Rails (Production)

Configure Rails to serve the built React SPA assets in production.

**Files:**
- Modify: `web-ui/backend/config/routes.rb` — add SPA fallback
- Create: `web-ui/backend/app/controllers/spa_controller.rb`
- Modify: `web-ui/backend/config/environments/production.rb` — enable static files

### Step 1: Create SPA controller

```ruby
# app/controllers/spa_controller.rb
class SpaController < ActionController::Base
  def index
    render file: Rails.public_path.join("index.html"), layout: false
  end
end
```

### Step 2: Add SPA fallback route

At the bottom of routes.rb:
```ruby
# SPA fallback — must be last
get "*path", to: "spa#index", constraints: ->(req) {
  !req.path.start_with?("/api/", "/auth/", "/cable", "/up")
}
root to: "spa#index"
```

### Step 3: Commit

```bash
git add web-ui/backend/app/controllers/spa_controller.rb
git add web-ui/backend/config/routes.rb
git commit -m "feat: serve React SPA from Rails in production"
```

---

## Task 8: Create Unified Dockerfile

Create a multi-stage Dockerfile that builds the React frontend and runs the Rails backend.

**Files:**
- Create: `web-ui/backend/Dockerfile` (replace existing)

### Step 1: Write Dockerfile

```dockerfile
# Stage 1: Build React frontend
FROM node:22-slim AS frontend
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json* frontend/pnpm-lock.yaml* ./
RUN npm install --frozen-lockfile 2>/dev/null || npx pnpm install --frozen-lockfile 2>/dev/null || npm install
COPY frontend/ .
RUN npm run build

# Stage 2: Ruby base
FROM ruby:3.4.8-slim AS base
RUN apt-get update -qq && apt-get install --no-install-recommends -y \
    libpq5 curl && \
    rm -rf /var/lib/apt/lists /var/cache/apt/archives

WORKDIR /app
ENV RAILS_ENV=production \
    BUNDLE_WITHOUT="development:test"

# Stage 3: Build Ruby dependencies
FROM base AS build
RUN apt-get update -qq && apt-get install --no-install-recommends -y \
    build-essential libpq-dev pkg-config && \
    rm -rf /var/lib/apt/lists /var/cache/apt/archives

COPY backend/Gemfile backend/Gemfile.lock ./
RUN bundle install && \
    rm -rf ~/.bundle/ "${BUNDLE_PATH}"/ruby/*/cache

COPY backend/ .
RUN bundle exec bootsnap precompile app/ lib/

# Stage 4: Final
FROM base
COPY --from=build /usr/local/bundle /usr/local/bundle
COPY --from=build /app /app
COPY --from=frontend /app/frontend/dist /app/public/

# Non-root user
RUN groupadd --system --gid 1000 rails && \
    useradd rails --uid 1000 --gid 1000 --create-home --shell /bin/bash && \
    chown -R rails:rails /app
USER rails:rails

EXPOSE 3000
HEALTHCHECK CMD curl -f http://localhost:3000/up || exit 1
CMD ["bundle", "exec", "puma", "-C", "config/puma.rb"]
```

### Step 2: Update railway.toml

```toml
[build]
dockerfilePath = "Dockerfile"

[deploy]
healthcheckPath = "/up"
restartPolicyType = "ON_FAILURE"
restartPolicyMaxRetries = 10
```

Note: The Dockerfile is at `web-ui/Dockerfile` (context is `web-ui/`) so it can access both `backend/` and `frontend/`.

### Step 3: Commit

```bash
git add web-ui/Dockerfile web-ui/backend/railway.toml
git commit -m "feat: unified Dockerfile for Rails + React SPA"
```

---

## Task 9: Delete Old Code

Remove the old Python web module, old web-ui directory, and old Dockerfile.

**Files:**
- Delete: `mypalclara/web/` (entire module)
- Delete: `Dockerfile.web`
- Delete: `web-ui/backend/app/javascript/` (if any remains — game React code already copied to frontend)
- Modify: `mypalclara/__init__.py` — remove web references if any
- Modify: `pyproject.toml` — remove web-related script entries if any

### Step 1: Remove Python web module

```bash
rm -rf mypalclara/web/
rm -f Dockerfile.web
```

### Step 2: Clean up imports and references

Search for any remaining imports of `mypalclara.web` and remove/update them:

```bash
grep -r "mypalclara.web" --include="*.py" -l
```

Fix any found references.

### Step 3: Remove old game JavaScript from backend

```bash
rm -rf web-ui/backend/app/javascript/
```

### Step 4: Commit

```bash
git add -u  # Stage all deletions
git commit -m "chore: delete old Python web module and Dockerfile.web"
```

---

## Task 10: Update Documentation

Update CLAUDE.md, README, and memory files to reflect the new architecture.

**Files:**
- Modify: `CLAUDE.md` — update architecture section, directory structure, commands
- Modify: `/Users/heidornj/.claude/projects/-Users-heidornj-Code-mypalclara/memory/MEMORY.md` — update package structure

### Step 1: Update CLAUDE.md

Update the following sections:
- **Directory Structure**: Add `web-ui/backend/` and `web-ui/frontend/`, remove `mypalclara/web/` and `games/`
- **Quick Reference**: Add Rails commands (`cd web-ui/backend && rails s`, etc.)
- **Architecture**: Note Rails BFF + gateway HTTP API
- **Environment Variables**: Add `CLARA_GATEWAY_API_PORT`, `CLARA_GATEWAY_API_URL`
- **Production Deployment**: Update Docker/Railway instructions

### Step 2: Update memory

Update MEMORY.md package structure section.

### Step 3: Commit

```bash
git add CLAUDE.md
git commit -m "docs: update CLAUDE.md for unified web-ui architecture"
```

---

## Task Summary

| # | Task | Scope | Estimated Steps |
|---|------|-------|-----------------|
| 1 | Move Python web API routes into gateway | Python backend | 6 |
| 2 | Create Rails API app (move games/) | Rails backend | 8 |
| 3 | Add gateway proxy service to Rails | Rails backend | 5 |
| 4 | Add chat WebSocket proxy (ActionCable) | Rails backend | 4 |
| 5 | Update Rails auth (unified JWT + OAuth) | Rails backend | 4 |
| 6 | Create React SPA (merge frontends) | React frontend | 10 |
| 7 | Add SPA serving to Rails (production) | Rails backend | 3 |
| 8 | Create unified Dockerfile | DevOps | 3 |
| 9 | Delete old code | Cleanup | 4 |
| 10 | Update documentation | Docs | 3 |

**Dependencies:**
- Task 1 must complete before Tasks 3 and 4 (gateway needs HTTP API)
- Task 2 must complete before Tasks 3, 4, 5, 7 (Rails app must exist)
- Tasks 3, 4, 5 can be done in parallel after 1 and 2
- Task 6 can be done in parallel with Tasks 3-5 (frontend is independent)
- Task 7 depends on Task 2
- Task 8 depends on Tasks 2 and 6
- Task 9 depends on Tasks 1-8 (delete only after everything works)
- Task 10 depends on Task 9
