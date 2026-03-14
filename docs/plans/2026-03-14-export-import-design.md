# Clara Data Export/Import Tool — Design

## Purpose

A CLI tool to extract Clara records from any current or legacy database backend and produce portable archive files that can be imported into a differently-configured deployment. Supports disaster recovery, provider migration, user data portability, and dev/staging seeding.

## Use Cases

1. **Migration between providers** — export from one set of services (e.g., self-hosted Qdrant + PostgreSQL), import to another (e.g., pgvector on Railway). The export format is backend-agnostic.
2. **Dev/staging seeding** — export production data, import into a local dev environment running SQLite + local Qdrant.
3. **User data portability** — export a single user's data for archival or transfer.
4. **Disaster recovery** — full snapshot of all data for backup/restore.

## Architecture

A single script at `scripts/clara_export_import.py` with two subcommands:

```
poetry run python scripts/clara_export_import.py export [options]
poetry run python scripts/clara_export_import.py import <archive> [options]
```

Connects directly to databases using the same env vars the gateway uses. No running gateway required.

## Export Format

JSONL directory bundled as `.tar.gz`:

```
clara-export-2026-03-14T120000Z.tar.gz
├── manifest.json
├── relational/
│   ├── projects.jsonl
│   ├── sessions.jsonl
│   ├── messages.jsonl
│   ├── canonical_users.jsonl
│   ├── platform_links.jsonl
│   ├── conversations.jsonl
│   ├── branches.jsonl
│   ├── branch_messages.jsonl
│   ├── memory_dynamics.jsonl
│   ├── memory_history.jsonl
│   ├── memory_supersessions.jsonl
│   ├── intentions.jsonl
│   ├── personality_traits.jsonl
│   ├── personality_trait_history.jsonl
│   ├── channel_configs.jsonl
│   ├── guild_configs.jsonl
│   ├── proactive_messages.jsonl
│   ├── user_interaction_patterns.jsonl
│   ├── proactive_notes.jsonl
│   └── proactive_assessments.jsonl
├── vectors/
│   └── memories.jsonl
└── graph/
    ├── nodes.jsonl
    └── edges.jsonl
```

### manifest.json

```json
{
  "version": "1",
  "created_at": "2026-03-14T12:00:00Z",
  "source": {
    "relational": "postgresql",
    "vector": "qdrant",
    "graph": "falkordb"
  },
  "filters": { "user_id": null, "since": null },
  "embedding_model": "text-embedding-3-small",
  "embedding_dimensions": 1536,
  "record_counts": { "sessions": 142, "messages": 3891, "memories": 512 }
}
```

### Data Serialization

- Datetimes as ISO 8601 strings
- JSON/text columns stored as-is (strings)
- Vectors as raw float arrays
- One record per JSONL line

### What Is Exported

**Relational (from SQLAlchemy):**
- Projects, Sessions, Messages
- CanonicalUser, PlatformLink
- Conversations, Branches, BranchMessages
- MemoryDynamics, MemoryHistory, MemorySupersession
- Intentions
- PersonalityTrait, PersonalityTraitHistory
- ChannelConfig, GuildConfig
- ProactiveMessage, UserInteractionPattern, ProactiveNote, ProactiveAssessment

**Vector store (Qdrant or pgvector):**
- All memory vectors with text, metadata, and float[] embeddings

**Graph (FalkorDB):**
- All nodes with properties
- All edges with properties

### What Is NOT Exported

- Redis cache (rebuilt automatically)
- WebSession (ephemeral login sessions)
- OAuthToken, GoogleOAuthToken (secrets, must re-auth)
- LogEntry (operational logs)
- ChannelSummary (re-generated)
- MCP models (installation-specific)
- ToolAuditLog (large, operational)
- EmailAccount/EmailRule/EmailAlert (contains credentials)

## Import Behavior

**Conflict resolution:** Upsert by primary key. Re-running an import is idempotent.

**Ordering:** Tables imported in FK dependency order (projects → sessions → messages, etc.).

**Vectors:** Importer checks manifest's `embedding_model` against current config. If they match, inserts exported vectors directly. If they differ or vectors are missing, re-embeds from text. `--re-embed` flag forces re-embedding.

**Graph:** Nodes and edges inserted via MERGE (upsert), so re-imports don't duplicate.

**Selective import:** `--tables memories,sessions` imports only specific files from the archive. Warns about skipped FK dependencies.

**Dry run:** `--dry-run` parses the archive, validates manifest, reports record counts, writes nothing.

**Progress:** Logs every 500 records per table. Final summary with counts.

## CLI Interface

```bash
# Export everything
poetry run python scripts/clara_export_import.py export -o ./backups/

# Export one user
poetry run python scripts/clara_export_import.py export --user discord-12345 -o ./backups/

# Export since a date
poetry run python scripts/clara_export_import.py export --since 2026-01-01 -o ./backups/

# Import everything
poetry run python scripts/clara_export_import.py import ./backups/clara-export-2026-03-14.tar.gz

# Import specific tables
poetry run python scripts/clara_export_import.py import archive.tar.gz --tables memories,sessions

# Import with forced re-embedding
poetry run python scripts/clara_export_import.py import archive.tar.gz --re-embed

# Dry run
poetry run python scripts/clara_export_import.py import archive.tar.gz --dry-run
```

## Dependencies

No new dependencies. Uses:
- `argparse`, `tarfile`, `json`, `os`, `datetime` (stdlib)
- Existing SQLAlchemy engine/models from `mypalclara.db`
- Existing Qdrant/pgvector clients from `mypalclara.core.memory.vector`
- Existing FalkorDB client from `mypalclara.core.memory.graph`
- Existing OpenAI embeddings from `mypalclara.core.memory.embeddings` (for re-embed)

## Error Handling

- Export: if a backend is unavailable (e.g., no FalkorDB configured), skip that section with a warning and continue. The manifest records which backends were available.
- Import: if target backend is unavailable for a section that has data, warn and skip. If `--strict` flag is set, fail instead.
- Import: FK violations on individual records are logged and skipped (with count in summary).
