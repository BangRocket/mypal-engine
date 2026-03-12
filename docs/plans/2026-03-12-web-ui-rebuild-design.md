# Web UI Rebuild — Claude.ai Clone Design

**Date:** 2026-03-12
**Status:** Approved

## Summary

Rebuild Clara's web UI as a Claude.ai-style chat interface using assistant-ui. Drop the Rails BFF entirely — React talks directly to the gateway. Auth via Clerk. Introduce a git-style conversation branching model. Feature parity with Discord adapter (streaming, tools, file uploads, model tiers). Shared memories across platforms, branch-scoped memory isolation for forks.

## Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Backend architecture | Drop Rails, React → Gateway directly | Eliminate middle layer, simpler stack |
| Auth | Clerk (managed SaaS) | Zero ops, good React SDK, JWT validation only on gateway |
| Pages | Chat + Settings + Knowledge | Claude.ai-like: chat primary, settings and memory viewer secondary |
| Conversation model | Continuous + git-style branching | Single main conversation, fork/merge at any message |
| Merge strategies | User chooses per merge | Squash (memories only) or full (memories + messages) |
| File handling | Full parity with Discord | Images, text files, PDFs, docx |
| Tool display | Collapsible blocks | Claude.ai style: name + spinner while running, expandable inputs/outputs |

## Architecture

### Services

Two services only:

- **Gateway** (Python/FastAPI) — port 18789 (WebSocket) + 18790 (HTTP API). Auth validation, chat processing, conversation/branch model, memories, settings.
- **Frontend** (React/Vite) — static SPA. Connects directly to gateway.

### Auth Flow

1. Clerk React SDK handles login UI and token management
2. Every request includes Clerk JWT in `Authorization: Bearer` header
3. Gateway validates JWT via Clerk's JWKS endpoint (cached)
4. First valid request auto-creates `CanonicalUser` record linked to Clerk user ID
5. Clerk user metadata (name, email, avatar) synced on login
6. WebSocket auth: JWT as query param (`ws://gateway:18789?token=...`)
7. Existing `X-Canonical-User-Id` header path stays for Discord/other adapters

### Frontend Stack

**Keeping:**
- React 19 + TypeScript + Vite
- `@assistant-ui/react` (Claude example as starting point)
- Tailwind CSS + shadcn/ui components
- Zustand for state management
- React Markdown (GFM, code highlighting)
- `@tanstack/react-query` for settings/knowledge API calls

**Dropping:**
- Rails ActionCable (direct WebSocket to gateway)
- Complex routing (down to 3 routes: `/`, `/settings`, `/knowledge`)
- Games, graph explorer, intentions, admin pages
- TipTap rich text editor (plain textarea + markdown)
- D3/XyFlow graph visualization

## Data Model

### Conversation & Branch Model

```
Conversation
  id: uuid
  user_id: str (canonical user ID)
  created_at: datetime
  updated_at: datetime

Branch
  id: uuid
  conversation_id: uuid → Conversation
  parent_branch_id: uuid → Branch (nullable, null = main trunk)
  fork_message_id: uuid → Message (nullable, the message we forked after)
  name: str (nullable, user can label branches)
  status: enum (active, merged, archived)
  created_at: datetime
  merged_at: datetime (nullable)

Message
  id: uuid
  branch_id: uuid → Branch
  role: enum (user, assistant, system, tool)
  content: text
  attachments: json
  tool_calls: json
  created_at: datetime
```

### How Branching Works

- Every user gets one `Conversation`, created on first message.
- Conversation starts with one `Branch` (main trunk, `parent_branch_id=null`).
- **Fork:** User selects a message → new Branch created with `parent_branch_id` + `fork_message_id`. Context = all ancestor messages up to fork point.
- **Merge (user chooses):**
  - *Squash* — branch-scoped memories promoted to global. Branch messages stay isolated. Branch marked `merged`.
  - *Full merge* — memories promoted to global + messages appended to main trunk. Branch marked `merged`.
- **Archive** — branch hidden, branch-scoped memories discarded or archived.

### Context Building for a Branch

Walk up the parent chain to collect ancestor messages up to the fork point, then append the branch's own messages. Clara gets full context of how the conversation reached this point.

## Memory Isolation

### Branch-Scoped Memories

Forked branches are memory-isolated, like a new Claude.ai chat:

- Fork starts with: existing global Rook memories + message context up to fork point
- New memories extracted during the branch are **branch-scoped** — not globally visible
- Discord doesn't see them. Main trunk doesn't see them. Other branches don't see them.
- They're "uncommitted changes" in the git analogy.

### Implementation

- Rook memories get a `branch_id` column (nullable). `NULL` = global. Non-null = branch-scoped.
- Memory queries filter: `WHERE branch_id IS NULL OR branch_id = :current_branch`
- Merge promotes: `UPDATE memories SET branch_id = NULL WHERE branch_id = :merged_branch`
- Main trunk memories are immediately global (same behavior as Discord).

### Cross-Platform Sharing

- Memories keyed by `canonical_user_id` — same user across all platforms
- Memory extracted on Discord → visible on web's knowledge page and in chat context
- Memory extracted on web main trunk → visible on Discord
- Branch-scoped memories → invisible until merged

## API Changes

### New Endpoints

```
# Conversation
GET    /api/v1/conversation              → get user's conversation (auto-create if none)

# Branches
GET    /api/v1/branches                  → list branches (filter: active/merged/archived)
POST   /api/v1/branches/fork             → fork from message_id, returns new branch
PATCH  /api/v1/branches/:id              → rename, archive
POST   /api/v1/branches/:id/merge        → merge into parent (body: {strategy: "squash"|"full"})
DELETE /api/v1/branches/:id              → delete branch + messages

# Messages (scoped to branch)
GET    /api/v1/branches/:id/messages     → paginated messages for branch (includes ancestor context)
```

### WebSocket Protocol Changes

- `MessageRequest` gains `branch_id` field (nullable)
- Processor uses `branch_id` to determine context
- No `branch_id` → falls back to existing session behavior (backward compatible)

### Auth Middleware

- New `ClerkJWTMiddleware` on FastAPI
- Validates JWT signature against Clerk JWKS (cached with TTL)
- Extracts `sub` claim → resolves to `CanonicalUser`
- Existing `X-Canonical-User-Id` header path stays for non-web adapters

### Unchanged Endpoints

- `/api/v1/memories` — no changes
- `/api/v1/users` — no changes
- `/api/v1/sessions` — stays for Discord, web doesn't use it

## Frontend Layout

### Routes

- `/` — Chat (default)
- `/settings` — Preferences (model defaults, tier, Clerk profile)
- `/knowledge` — Memory viewer/manager

### Chat Layout

```
┌──────────┬─────────────────────────────┐
│ Branch   │                             │
│ sidebar  │     Chat thread             │
│          │                             │
│ • main   │  [messages scroll here]     │
│   ├ fix  │                             │
│   └ idea │                             │
│          │                             │
│ [+fork]  │  ┌─────────────────────┐    │
│          │  │ Composer + attach    │    │
│          │  └─────────────────────┘    │
└──────────┴─────────────────────────────┘
```

### Sidebar

- Branch tree (main trunk + children, indented)
- Active branch highlighted
- Status indicators (active, merged, archived)
- Right-click/menu: rename, archive, merge, delete
- Collapsible

### Composer

- Text input with file drop zone
- Attachment preview (image thumbnails, file chips)
- Tier selector (dropdown or keyboard shortcut)
- Send button / Enter to submit

### Messages

- Markdown rendering (GFM, syntax highlighting)
- Tool calls as collapsible blocks (name + spinner → expandable inputs/outputs)
- Fork button on hover per message
- Streaming chunk-by-chunk rendering

### assistant-ui Integration

- Start from Claude example as base
- Custom `ExternalStoreRuntime` backed by Zustand
- Store manages: WebSocket connection, messages for active branch, branch tree state

### WebSocket (Direct to Gateway)

- Connect on app load with Clerk JWT as query param
- Send: `{type: "message", content, branch_id, attachments, tier}`
- Receive: `response_start → chunk → tool_start → tool_result → response_end`
- Reconnect with exponential backoff

## What Gets Removed

### Frontend — Removing

- All game pages/components
- Graph explorer + D3/XyFlow deps
- Intentions page
- Admin panel
- ActionCable / Rails WebSocket bridge
- OAuth callback flow (replaced by Clerk)
- TipTap rich text editor

### Rails Backend — Removing Entirely

- `web-ui/backend/` directory — deleted
- All controllers, channels, models, services

### Not Touching

- Discord adapter
- Gateway WebSocket server (backward compatible)
- Rook memory system (additive change only)
- All other adapters
