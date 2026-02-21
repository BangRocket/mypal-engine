# Identity Service Design

**Date:** 2026-02-21
**Status:** Approved

## Problem

OAuth login in the web UI is broken. Two bugs:

1. `AuthController#find_or_create_user` looks up users by `canonical_user_id = platform_id` (Discord snowflake), but `canonical_user_id` is a UUID. Never matches. Every login creates a new user.
2. Rails calls `POST /api/v1/users/ensure` on the gateway, but that endpoint doesn't exist. Users are never synced to the gateway DB.

Root cause: no single source of truth for user identity. Rails has a `users` table with UUIDs, the gateway has `CanonicalUser`/`PlatformLink` tables, and nothing connects OAuth platform IDs to canonical user IDs.

## Solution

A standalone FastAPI identity service that owns user identity and OAuth flows. Both Rails (web UI) and the Python gateway consume it.

## Scope

The identity service owns **one thing**: who is this user?

**Handles:**
- OAuth flows (authorize URL, code exchange, profile fetch) for Discord, Google, and future providers
- User identity (create/find CanonicalUser, link platform accounts via PlatformLink)
- JWT issuance (sign tokens that Rails and gateway trust)
- User lookup by platform identity (provider + platform_user_id -> canonical_user_id)

**Does NOT handle:**
- Authorization / permissions (stays in each consumer)
- Web session management (Rails handles cookies)
- Anything Clara/AI-specific

**Database:** Owns tables `canonical_users`, `platform_links`, `oauth_tokens` in PostgreSQL. Initially shares the same PG instance as the gateway.

## API

### Browser-facing (JWT auth)

```
POST /oauth/authorize
  Body: { provider: "discord" }
  Returns: { url: "https://discord.com/api/oauth2/authorize?..." }

POST /oauth/callback
  Body: { provider: "discord", code: "abc123" }
  Returns: { token: "eyJ...", user: { id, display_name, avatar_url } }

POST /oauth/refresh
  Body: { token: "eyJ..." }
  Returns: { token: "eyJ..." }

GET /users/me
  Header: Authorization: Bearer <jwt>
  Returns: { id, display_name, avatar_url, links: [...] }
```

### Internal (service secret auth)

```
GET /users/by-platform/:provider/:platform_user_id
  Header: X-Service-Secret: <shared secret>
  Returns: { id, display_name, avatar_url } or 404

POST /users/ensure-link
  Header: X-Service-Secret: <shared secret>
  Body: { provider: "discord", platform_user_id: "123", display_name: "Josh" }
  Returns: { canonical_user_id: "uuid-..." }
  (Idempotent create)
```

### Auth model

- Browser-facing endpoints use JWT in Authorization header
- Internal endpoints use X-Service-Secret header (same pattern as CLARA_GATEWAY_SECRET)

### JWT payload

```json
{
  "sub": "canonical-user-uuid",
  "name": "Display Name",
  "iat": 1708434000,
  "exp": 1708520400
}
```

Signed with IDENTITY_JWT_SECRET (shared with Rails for verification).

## Integration Changes

### Rails (web-ui/backend)

- `AuthController#login` calls identity service `POST /oauth/authorize`
- `AuthController#callback` calls identity service `POST /oauth/callback`, gets JWT + user, sets cookie, finds/creates local User row by canonical_user_id
- `OauthService` class deleted (responsibility moved to identity service)
- `JwtService` changes from signing to verifying only
- `GatewayProxy` unchanged (still forwards X-Canonical-User-Id)

### Gateway (mypalclara)

- `gateway/api/auth.py` unchanged (still trusts X-Canonical-User-Id from Rails)
- Identity models stay in gateway DB as read-only references for now
- `db/user_identity.py` `ensure_platform_link()` calls identity service POST /users/ensure-link

### Discord bot / adapters

- On first message from new user, call identity service POST /users/ensure-link instead of writing directly

### Flow after changes

```
Browser -> Discord OAuth -> redirect to Rails /auth/callback/discord
  Rails -> Identity Service POST /oauth/callback { provider, code }
    Identity Service -> Discord API (exchange code, fetch profile)
    Identity Service -> DB (find/create CanonicalUser + PlatformLink)
    Identity Service -> Returns { jwt, user }
  Rails -> Sets cookie, creates local User row
  Rails -> Redirects to SPA

SPA -> Rails API -> X-Canonical-User-Id -> Gateway (unchanged)
```

## Deployment

- FastAPI app in `identity/` directory at repo root
- Railway service in same project, same PostgreSQL instance
- Dockerfile similar to Dockerfile.gateway

### Environment variables

```
# Identity service
IDENTITY_JWT_SECRET=...
IDENTITY_SERVICE_SECRET=...
DATABASE_URL=postgresql://...

# OAuth providers
DISCORD_OAUTH_CLIENT_ID=...
DISCORD_OAUTH_CLIENT_SECRET=...
DISCORD_OAUTH_REDIRECT_URI=...
GOOGLE_OAUTH_CLIENT_ID=...
GOOGLE_OAUTH_CLIENT_SECRET=...
GOOGLE_OAUTH_REDIRECT_URI=...

# Rails additions
IDENTITY_SERVICE_URL=https://identity.up.railway.app
IDENTITY_JWT_SECRET=...
```

### Health check

`GET /health` -> `{ status: "ok" }`

## Provider Configuration

Extensible provider config pattern:

```python
PROVIDERS = {
    "discord": {
        "authorize_url": "https://discord.com/api/oauth2/authorize",
        "token_url": "https://discord.com/api/oauth2/token",
        "user_url": "https://discord.com/api/users/@me",
        "scope": "identify email",
        "client_id_env": "DISCORD_OAUTH_CLIENT_ID",
        "client_secret_env": "DISCORD_OAUTH_CLIENT_SECRET",
        "redirect_uri_env": "DISCORD_OAUTH_REDIRECT_URI",
    },
    "google": {
        "authorize_url": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_url": "https://oauth2.googleapis.com/token",
        "user_url": "https://www.googleapis.com/oauth2/v2/userinfo",
        "scope": "openid email profile",
        "client_id_env": "GOOGLE_OAUTH_CLIENT_ID",
        "client_secret_env": "GOOGLE_OAUTH_CLIENT_SECRET",
        "redirect_uri_env": "GOOGLE_OAUTH_REDIRECT_URI",
    },
}
```

Adding a new provider = adding an entry to this dict + a `normalize_profile()` function.
