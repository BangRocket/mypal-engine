# Accounts System — account.mypalclara.com

## Goal

Standalone identity service with invite-based registration, OAuth platform linking, and API key generation. Runs on Hostinger VPS alongside Matrix/email. Enables cross-platform user identity for Clara (Discord, Jan app, Matrix, Teams).

## Architecture

```
account.mypalclara.com (Hostinger VPS)
├── nginx (static frontend + reverse proxy)
├── identity service (FastAPI, SQLite)
│   ├── /register — invite code + display name
│   ├── /oauth/* — Discord, Google, Matrix linking
│   ├── /users/me — account dashboard data
│   ├── /api-keys — generate/revoke keys
│   └── /admin/invites — create invite codes
└── SQLite database (users, links, keys, invites)

Clara Gateway (Clara server)
├── /v1/chat/completions — authenticates via API key
└── Resolves API key → canonical user → cross-platform identity
```

## Backend (extend identity/)

- Invite codes: table, admin generation, redemption
- API keys: table, generation, hashing, revocation
- Registration endpoint: invite code + name → JWT
- SQLite on VPS (no Postgres dependency)

## Frontend (static SPA)

- No build step — HTML/CSS/JS served by nginx
- Pages: register, login, dashboard, link platforms, API keys

## Registration flow

1. User gets invite code
2. account.mypalclara.com/register → enter code + name
3. Account created → redirect to dashboard
4. Link Discord via OAuth
5. Generate API key for Jan
6. Configure Jan with key
7. Messages authenticated → shared memory with Discord
