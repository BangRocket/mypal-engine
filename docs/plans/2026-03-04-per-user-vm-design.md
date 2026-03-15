# Per-User Persistent VMs with Privacy-Scoped Memory

**Date:** 2026-03-04
**Status:** Approved

## Overview

Give each MyPalClara user a persistent Incus VM with full filesystem access, personal workspace files, and privacy-controlled memory. Clara operates as a shared service but provides single-user-like depth per person, with explicit boundaries between what's private and what's visible in group channels.

## VM Lifecycle

Each user gets a persistent Incus VM named `clara-user-{user_id}`.

| State | Trigger | Action |
|-------|---------|--------|
| **Provision** | First time user needs VM access | `incus launch` with Clara profile, cloud-init |
| **Running** | User is active | Full filesystem/process access via `exec` |
| **Suspend** | Idle timeout (default 30 min) | `incus pause` — state frozen to disk |
| **Resume** | Next user interaction needing VM | `incus start` — resumes in seconds |
| **Destroy** | Admin action only | Never auto-destroyed |

Existing ephemeral sandboxes (code execution) continue to work alongside persistent VMs. They are separate instance classes.

## Per-User Workspace

Per-user workspace files live inside the VM at `/home/clara/workspace/`:

| File | Scope | Location |
|------|-------|----------|
| SOUL.md | Global (owner-controlled) | Shared `mypalclara/workspace/` |
| IDENTITY.md | Global (owner-controlled) | Shared `mypalclara/workspace/` |
| USER.md | Per-user | User's VM `/home/clara/workspace/` |
| MEMORY.md | Per-user | User's VM `/home/clara/workspace/` |
| HEARTBEAT.md | Per-user | User's VM `/home/clara/workspace/` |
| TOOLS.md | Per-user | User's VM `/home/clara/workspace/` |
| AGENTS.md | Per-user | User's VM `/home/clara/workspace/` |
| Custom *.md | Per-user | User's VM `/home/clara/workspace/` |

Prompt builder loads global files from shared dir, per-user files from VM (cached with TTL).

## Privacy Model

Every piece of user-specific data gets a `visibility` field: `private` (default) or `public`.

### What gets tagged

- **Rook memories** — `visibility` metadata field. Defaults to `private`. Filtered at query time (same pattern as `user_id`).
- **Files in VM** — Two directories: `/home/clara/private/` and `/home/clara/public/`. Clara references public files in group channels, private files only in DMs.
- **Workspace files** — Always private (inside user's VM).

### Enforcement

| Context | Memory scope | File access | Workspace |
|---------|-------------|-------------|-----------|
| DM / VM | All (public + private) | All files | Full per-user workspace |
| Group channel | Public only | Public dir only | No per-user workspace |

### Tagging mechanics

- **User-initiated:** "Clara, make my timezone public" → tool updates visibility
- **Clara-suggested:** "You mentioned Project X — want me to share that with the team?" → user confirms → visibility updated
- **Clara never auto-publishes** without user confirmation
- **Default:** Everything is private until explicitly marked public

### Tools

- `memory_set_visibility(memory_id, visibility)` — update a memory's visibility
- `memory_list_public(user_id)` — list a user's public memories ("what can the team see about me?")

## Gateway Integration

### Context assembly

`MessageProcessor` determines `privacy_scope` from channel type:
- DM or VM → `privacy_scope = "full"`
- Group channel → `privacy_scope = "public_only"`

`PromptBuilder.build_prompt()` receives `privacy_scope` parameter and filters:
- `full` → load all memories, full VM workspace
- `public_only` → load only `visibility=public` memories, no VM workspace content

### Workspace tool scoping

`workspace_read/write/create` resolve path based on user context:
- When called in user context → operates on user's VM workspace (`/home/clara/workspace/`)
- `ToolContext` already carries `user_id` → used to resolve correct VM

### No adapter changes

Adapters already send channel type metadata. Privacy scope is determined entirely in the gateway.

## New & Modified Components

### New

| Component | Purpose |
|-----------|---------|
| `mypalclara/core/vm_manager.py` | Persistent VM lifecycle (provision, suspend, resume, status) |
| `memory_set_visibility` tool | Update memory visibility metadata |
| `memory_list_public` tool | List a user's public memories |
| `UserVM` DB model | Track VM state per user (instance, status, timestamps) |

### Modified

| Component | Change |
|-----------|--------|
| `mypalclara/core/memory/` | Add `visibility` field to metadata, filter during search |
| `mypalclara/gateway/processor.py` | Determine `privacy_scope` from channel type |
| `mypalclara/core/prompt_builder.py` | Accept `privacy_scope`, load per-user workspace from VM |
| `mypalclara/core/core_tools/workspace_tool.py` | Resolve workspace path from user's VM |
| `mypalclara/sandbox/incus.py` | Add suspend/resume, separate persistent VM from ephemeral sandbox |
| `mypalclara/workspace/SOUL.md` | Add privacy instructions for group channels |

### Unchanged

- Adapters (already send channel type)
- Rook vector store backends (visibility = another metadata filter)
- Session model (already scoped by user_id + context_id)
- Existing ephemeral sandbox flow
