# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

MyPalClara is a personal AI assistant with session management and persistent memory (via Rook, Clara's memory system). The assistant's name is Clara. It uses a Discord bot interface with SQLite/PostgreSQL storage.

## Versioning

Uses CalVer format: `YYYY.WW.N` (Year.Week.Build)

- `2026.04.1` = First build of week 4, 2026
- `2026.04.2` = Second build of week 4, 2026
- `2026.05.1` = First build of week 5, 2026

**Auto-bump:** Version is automatically bumped via git hook, but only for significant commits using conventional commit prefixes.

### Commit Types That Bump Version
| Prefix | Description | Bumps? |
|--------|-------------|--------|
| `feat:` | New feature | Yes |
| `fix:` | Bug fix | Yes |
| `perf:` | Performance improvement | Yes |
| `breaking:` | Breaking change | Yes |
| `chore:` | Maintenance, dependencies | No |
| `docs:` | Documentation only | No |
| `style:` | Formatting, no code change | No |
| `refactor:` | Code restructuring | No |
| `test:` | Adding/fixing tests | No |
| `ci:` | CI/CD changes | No |
| `build:` | Build system changes | No |

### Override Tags
- `[bump]` - Force version bump for any commit type
- `[skip-version]` - Skip version bump for any commit type

```bash
# Install git hooks (run once after cloning)
git config core.hooksPath .githooks

# Manual version management
python scripts/bump_version.py          # Bump version
python scripts/bump_version.py --dry    # Preview without changing
python scripts/bump_version.py --show   # Show current version

# Examples
git commit -m "feat: add user authentication"      # Bumps version
git commit -m "fix: resolve login bug"             # Bumps version
git commit -m "chore: update dependencies"         # No bump
git commit -m "docs: update README"                # No bump
git commit -m "refactor: simplify code [bump]"     # Force bump
git commit -m "feat: add feature [skip-version]"   # Skip bump
```

Version is stored in `VERSION` file and synced to `pyproject.toml`. Bot displays version on startup.

## Development Commands

### Backend (Python)
```bash
poetry install                    # Install dependencies
poetry run python discord_bot.py  # Run Discord bot
poetry run ruff check .           # Lint
poetry run ruff format .          # Format

# Daemon mode (Unix only)
poetry run python discord_bot.py --daemon                    # Run in background
poetry run python discord_bot.py --daemon --logfile bot.log  # With log file
poetry run python discord_bot.py --status                    # Check if running
poetry run python discord_bot.py --stop                      # Stop daemon

# Restart with confirmation and optional delay
poetry run python scripts/restart_bot.py              # Interactive restart
poetry run python scripts/restart_bot.py -y           # Skip confirmation
poetry run python scripts/restart_bot.py -y -d 30     # 30 second delay before restart
poetry run python scripts/restart_bot.py --no-start   # Stop only, don't restart
poetry run python scripts/restart_bot.py --logfile /var/log/clara.log  # With log file
```

### Docker
```bash
docker-compose --profile discord up                    # Run Discord bot only
docker-compose --profile discord --profile postgres up # Discord bot + databases
```

### Memory Management
```bash
# Clear all memory data
poetry run python clear_dbs.py             # With confirmation prompt
poetry run python clear_dbs.py --yes       # Skip confirmation
poetry run python clear_dbs.py --user <id> # Clear specific user
```

### Database Migrations
```bash
# Run pending migrations (default)
poetry run python scripts/migrate.py

# Show migration status
poetry run python scripts/migrate.py status

# Create new migration (autogenerate from model changes)
poetry run python scripts/migrate.py create "add user preferences table"

# Rollback last migration
poetry run python scripts/migrate.py rollback

# Rollback multiple migrations
poetry run python scripts/migrate.py rollback 3

# Show current heads
poetry run python scripts/migrate.py heads

# Show migration history
poetry run python scripts/migrate.py history

# Reset to base (DANGEROUS - drops all tables)
poetry run python scripts/migrate.py reset
```

**Auto-migration on startup:** The bot automatically runs pending migrations when `init_db()` is called. If migrations fail, it falls back to `create_all()`.

## Architecture

### Core Structure
- `discord_bot.py` - Discord bot with multi-user support, reply chains, and streaming responses
- `discord_monitor.py` - Web dashboard for monitoring Discord bot status and activity
- `memory_manager.py` - Core orchestrator: session handling, Rook integration, prompt building with Clara's persona
- `clara_core/llm/` - Unified LLM provider architecture (modular, supports OpenRouter, NanoGPT, OpenAI, Anthropic)
- `clara_core/memory/` - Rook memory system (Qdrant/pgvector for vectors, OpenAI embeddings)
- `models.py` - SQLAlchemy models: Project, Session, Message, ChannelSummary
- `db.py` - Database setup (SQLite for dev, PostgreSQL for production)
- `clara_core/email/` - Email monitoring and auto-response system

### Sandbox System
- `sandbox/docker.py` - Docker sandbox for code execution
- `sandbox/incus.py` - Incus container/VM sandbox
- `sandbox/manager.py` - Unified sandbox manager (auto-selects Docker or Incus)

### Storage
- `storage/local_files.py` - Local file storage system for persistent user files

### MCP Plugin System
- `clara_core/mcp/client.py` - MCPClient wrapper for connecting to MCP servers (stdio & HTTP transports)
- `clara_core/mcp/manager.py` - MCPServerManager singleton for managing all server connections
- `clara_core/mcp/installer.py` - Installation from Smithery, npm, GitHub, Docker, or local paths
- `clara_core/mcp/models.py` - MCPServer SQLAlchemy model for configuration storage
- `tools/mcp_management.py` - User-facing management tools (mcp_install, mcp_list, etc.)

### Gateway System
WebSocket-based gateway for platform adapters (in development):
- `gateway/server.py` - WebSocket server accepting adapter connections
- `gateway/processor.py` - Message processing and context building
- `gateway/llm_orchestrator.py` - Streaming LLM responses with tool detection
- `gateway/tool_executor.py` - Tool execution wrapper
- `gateway/events.py` - Event system for gateway lifecycle and message events
- `gateway/hooks.py` - Hook registration and execution (shell commands or Python callables)
- `gateway/scheduler.py` - Task scheduler (one-shot, interval, cron)

**Run the gateway:**
```bash
poetry run python -m gateway --host 127.0.0.1 --port 18789
```

### Memory System (Rook)
- **User memories**: Persistent facts/preferences per user (stored in Rook, searched via `_fetch_rook_context`)
- **Project memories**: Topic-specific context per project (filtered by project_id in Rook)
- **Graph memories**: Optional relationship tracking via Neo4j or Kuzu (disabled by default, enable with `ENABLE_GRAPH_MEMORY=true`)
- **Session context**: Recent 20 messages + snapshot of last 10 messages from previous session
- **Session summary**: LLM-generated summary stored when session times out
- Sessions auto-timeout after 30 minutes of inactivity (`SESSION_IDLE_MINUTES`)

## Environment Variables

### Required
- `OPENAI_API_KEY` - Always required for Rook embeddings (text-embedding-3-small)
- `LLM_PROVIDER` - Chat LLM provider: "openrouter" (default), "nanogpt", "openai", or "anthropic"

### Chat LLM Providers (based on LLM_PROVIDER)

**OpenRouter** (`LLM_PROVIDER=openrouter`):
- `OPENROUTER_API_KEY` - API key
- `OPENROUTER_MODEL` - Chat model (default: anthropic/claude-sonnet-4)
- `OPENROUTER_SITE` / `OPENROUTER_TITLE` - Optional headers

**NanoGPT** (`LLM_PROVIDER=nanogpt`):
- `NANOGPT_API_KEY` - API key
- `NANOGPT_MODEL` - Chat model (default: moonshotai/Kimi-K2-Instruct-0905)

**Custom OpenAI** (`LLM_PROVIDER=openai`):
- `CUSTOM_OPENAI_API_KEY` - API key for LLM (separate from embeddings)
- `CUSTOM_OPENAI_BASE_URL` - Base URL (default: https://api.openai.com/v1)
- `CUSTOM_OPENAI_MODEL` - Chat model (default: gpt-4o)

**Anthropic** (`LLM_PROVIDER=anthropic`):
- `ANTHROPIC_API_KEY` - Anthropic API key
- `ANTHROPIC_BASE_URL` - Custom base URL for proxies like clewdr (optional)
- `ANTHROPIC_MODEL` - Chat model (default: claude-sonnet-4-5)

Uses native Anthropic SDK with native tool calling. Recommended for Claude proxies like clewdr.

### Model Tiers (Discord Bot)
The Discord bot supports dynamic model selection via message prefixes:
- `!high` or `!opus` → High tier (Opus-class, most capable)
- `!mid` or `!sonnet` → Mid tier (Sonnet-class, balanced) - default
- `!low`, `!haiku`, or `!fast` → Low tier (Haiku-class, fast/cheap)

Optional tier-specific model overrides:
- `OPENROUTER_MODEL_HIGH`, `OPENROUTER_MODEL_MID`, `OPENROUTER_MODEL_LOW`
- `NANOGPT_MODEL_HIGH`, `NANOGPT_MODEL_MID`, `NANOGPT_MODEL_LOW`
- `CUSTOM_OPENAI_MODEL_HIGH`, `CUSTOM_OPENAI_MODEL_MID`, `CUSTOM_OPENAI_MODEL_LOW`
- `ANTHROPIC_MODEL_HIGH`, `ANTHROPIC_MODEL_MID`, `ANTHROPIC_MODEL_LOW`
- `MODEL_TIER` - Default tier when not specified. If not set, uses the base model (e.g., `ANTHROPIC_MODEL`). Set to "high", "mid", or "low" to enable tier-based defaults.
- `AUTO_TIER_SELECTION` - Enable automatic tier selection based on message complexity (default: false)

**Auto Tier Selection:**
When `AUTO_TIER_SELECTION=true`, Clara uses the fast/low model to classify message complexity and automatically selects the appropriate tier:
- LOW = Simple greetings, quick facts, basic questions, casual chat
- MID = Moderate tasks, explanations, summaries, most coding questions
- HIGH = Complex reasoning, long-form writing, difficult coding, multi-step analysis

The classifier considers recent conversation history (up to 4 messages) when making its decision. This prevents short replies like "yes" or "ok" from dropping to a lower tier when they're part of a complex ongoing discussion.

Example usage in Discord: `!high What is quantum entanglement?`

### Cloudflare Access (for endpoints behind cloudflared)
For custom OpenAI endpoints behind Cloudflare Access (like cloudflared tunnels):
- `CF_ACCESS_CLIENT_ID` - Cloudflare Access Service Token client ID
- `CF_ACCESS_CLIENT_SECRET` - Cloudflare Access Service Token client secret

### Rook Provider (independent from chat LLM)
Environment variables use `ROOK_*` prefix with `MEM0_*` fallback for backward compatibility.

- `ROOK_PROVIDER` - Provider for memory extraction: "openrouter" (default), "nanogpt", "openai", or "anthropic"
- `ROOK_MODEL` - Model for memory extraction (default: openai/gpt-4o-mini)
- `ROOK_API_KEY` - Optional: override the provider's default API key
- `ROOK_BASE_URL` - Optional: override the provider's default base URL

Note: For `ROOK_PROVIDER=anthropic`, uses native Anthropic SDK with `anthropic_base_url` support for proxies.

### Optional
- `USER_ID` - Single-user identifier (default: "demo-user")
- `DEFAULT_PROJECT` - Default project name (default: "Default Project")
- `SKIP_PROFILE_LOAD` - Skip initial Rook profile loading (default: true)
- `ENABLE_GRAPH_MEMORY` - Enable graph memory for relationship tracking (default: false)
- `GRAPH_STORE_PROVIDER` - Graph store provider: "neo4j" (default) or "kuzu" (embedded)
- `NEO4J_URL`, `NEO4J_USERNAME`, `NEO4J_PASSWORD` - Neo4j connection (when using neo4j provider)

### PostgreSQL (Production)
For production, use managed PostgreSQL instead of SQLite/Qdrant:
- `DATABASE_URL` - PostgreSQL connection for SQLAlchemy (default: uses SQLite)
- `ROOK_DATABASE_URL` - PostgreSQL+pgvector connection for Rook vectors (default: uses Qdrant)

Example (Railway):
```bash
DATABASE_URL=postgresql://user:pass@host:5432/clara_main
ROOK_DATABASE_URL=postgresql://user:pass@host:5432/clara_vectors
```

To migrate existing data:
```bash
poetry run python scripts/migrate_to_postgres.py --all
```

### Discord Bot
- `DISCORD_BOT_TOKEN` - Discord bot token (required for Discord integration)
- `DISCORD_CLIENT_ID` - Client ID for invite link generation
- `DISCORD_ALLOWED_SERVERS` - Comma-separated server IDs to whitelist all channels (optional, supersedes channel list)
- `DISCORD_ALLOWED_CHANNELS` - Comma-separated channel IDs to restrict bot (optional)
- `DISCORD_ALLOWED_ROLES` - Comma-separated role IDs for access control (optional)
- `DISCORD_MAX_MESSAGES` - Max messages in conversation chain (default: 25)
- `DISCORD_MAX_TOOL_RESULT_CHARS` - Max chars per tool result before truncation (default: 50000). Large results are truncated with a message suggesting pagination.
- `DISCORD_STOP_PHRASES` - Comma-separated phrases that interrupt running tasks (default: "clara stop,stop clara,nevermind,never mind")
- `DISCORD_SUMMARY_AGE_MINUTES` - Messages older than this are summarized (default: 30)
- `DISCORD_CHANNEL_HISTORY_LIMIT` - Max messages to fetch from channel (default: 50)
- `DISCORD_MONITOR_PORT` - Monitor dashboard port (default: 8001)
- `DISCORD_MONITOR_ENABLED` - Enable monitor dashboard (default: true)
- `DISCORD_LOG_CHANNEL_ID` - Channel ID to mirror console logs to (optional)

**Console Log Mirroring:**
When `DISCORD_LOG_CHANNEL_ID` is set, all console log output is mirrored to the specified Discord channel. Each log line becomes a separate message. Special events are highlighted:
- 🟢 Bot started
- 🔴 Bot shutting down
- 🟡 Bot disconnected
- 🔄 Bot reconnected/resumed

**Stop Phrases:**
Users can interrupt Clara mid-task by sending a stop phrase (e.g., "@Clara stop" or "@Clara nevermind"). This immediately cancels the current task and clears any queued requests for that channel. Useful when Clara is taking too long or working on the wrong thing.

**Message Queuing (Active Mode Batching):**
When Clara is busy processing a message, incoming messages are queued. The queuing behavior differs based on message type:

- **DMs and Mentions:** Each message is processed individually with queue position notifications. Users see "-# ⏳ Your request is queued (position N)" and "-# ▶️ Starting your queued request (waited Xs)..." when it's their turn.

- **Active Mode (Channel):** Messages in active mode channels (where Clara responds to all messages without needing mentions) are batched together. When chat gets very active:
  1. Incoming messages get an ⏳ reaction instead of a reply notification (less noise)
  2. When Clara finishes her current task, she collects all consecutive queued active-mode messages
  3. She responds to them together in a single combined response, prefixed with "-# 📨 Catching up on N messages"
  4. The combined context shows each message with its author: `[Username]: message content`

This allows Clara to keep up with fast-moving conversations without flooding the channel with individual responses or queue notifications.

**Image/Vision Support:**
Clara can see and analyze images sent in Discord messages. Images are automatically resized to optimal dimensions and batched to avoid payload size limits.

Configuration:
- `DISCORD_MAX_IMAGE_DIMENSION` - Maximum pixels on longest edge (default: 1568, Claude's recommendation)
- `DISCORD_MAX_IMAGE_SIZE` - Maximum image file size after resize in bytes (default: 4194304 = 4MB)
- `DISCORD_MAX_IMAGES_PER_REQUEST` - Maximum images per LLM request (default: 1). When exceeded, images are processed in sequential batches with context preserved between calls.

Supported formats: `.png`, `.jpg`, `.jpeg`, `.gif`, `.webp`

How it works:
1. Images are automatically detected from Discord attachments
2. Images are resized to fit within `MAX_IMAGE_DIMENSION` (preserving aspect ratio)
3. Large images are converted to JPEG for efficient compression
4. **Multi-image batching**: If more than `MAX_IMAGES_PER_REQUEST` images are present:
   - Images are processed in batches with separate LLM calls
   - Each batch includes context from previous batches
   - Responses are combined into a single Discord message
5. The resized image is base64-encoded and included in the LLM message
6. The image format is converted appropriately for each LLM provider:
   - OpenRouter/OpenAI: Uses `image_url` format with data URLs
   - Anthropic (native): Converts to Anthropic's `image` source format with base64 data
7. Original images are also saved to local storage for later reference

Note: Vision capabilities depend on the model being used. Images are resized to ~1.15 megapixels as recommended by Claude's vision documentation. The batching system prevents 413 payload errors when multiple images are posted in a single message.

### Sandbox Code Execution

Clara supports code execution via Docker or Incus containers/VMs.

**Mode Selection:**
- `SANDBOX_MODE` - Backend selection (default: auto)
  - `docker` or `local`: Use local Docker containers
  - `incus`: Use Incus containers (lighter, faster)
  - `incus-vm`: Use Incus VMs (stronger isolation for untrusted code)
  - `auto`: Use Incus if available, fall back to Docker

**Docker** (`SANDBOX_MODE=docker`):
- `DOCKER_SANDBOX_IMAGE` - Docker image for sandbox (default: python:3.12-slim)
- `DOCKER_SANDBOX_TIMEOUT` - Container idle timeout in seconds (default: 900)
- `DOCKER_SANDBOX_MEMORY` - Memory limit per container (default: 512m)
- `DOCKER_SANDBOX_CPU` - CPU limit per container (default: 1.0)

**Incus** (`SANDBOX_MODE=incus` or `incus-vm`):
Incus provides system containers and VMs via the [Linux Containers project](https://linuxcontainers.org/incus/).
- `INCUS_SANDBOX_IMAGE` - Base image (default: images:debian/12/cloud)
- `INCUS_SANDBOX_TYPE` - "container" or "vm" (default: container)
- `INCUS_SANDBOX_TIMEOUT` - Instance idle timeout in seconds (default: 900)
- `INCUS_SANDBOX_MEMORY` - Memory limit (default: 512MiB)
- `INCUS_SANDBOX_CPU` - CPU limit (default: 1)
- `INCUS_REMOTE` - Incus remote to use (default: local)

**Incus vs Docker:**
| Feature | Docker | Incus Container | Incus VM |
|---------|--------|-----------------|----------|
| Startup time | ~1s | ~2s | ~10s |
| Isolation | Namespace | Namespace + user ns | Full hardware |
| Memory overhead | Low | Low | ~100-300MB |
| Untrusted code | Moderate | Moderate | High |

Use `incus-vm` when running untrusted code that requires stronger isolation boundaries.

**Web Search:**
- `TAVILY_API_KEY` - Tavily API key for web search (Docker sandbox only)

### Gateway (WebSocket Server)

The gateway provides a central message processing hub for platform adapters. Run separately from the Discord bot.

**Environment Variables:**
- `CLARA_GATEWAY_HOST` - Bind address (default: 127.0.0.1)
- `CLARA_GATEWAY_PORT` - Port to listen on (default: 18789)
- `CLARA_GATEWAY_SECRET` - Shared secret for authentication (optional)
- `CLARA_HOOKS_DIR` - Directory containing hooks.yaml (default: ./hooks)
- `CLARA_SCHEDULER_DIR` - Directory containing scheduler.yaml (default: .)

**Hooks System:**
Hooks are automations triggered by gateway events. Configure in `hooks/hooks.yaml`:

```yaml
hooks:
  - name: log-startup
    event: gateway:startup
    command: echo "Gateway started at ${CLARA_TIMESTAMP}"

  - name: notify-errors
    event: tool:error
    command: curl -X POST https://webhook.example.com/notify -d "${CLARA_EVENT_DATA}"
    timeout: 10
```

Event types: `gateway:startup`, `gateway:shutdown`, `adapter:connected`, `adapter:disconnected`, `session:start`, `session:end`, `session:timeout`, `message:received`, `message:sent`, `message:cancelled`, `tool:start`, `tool:end`, `tool:error`, `scheduler:task_run`, `scheduler:task_error`

Environment variables available in hook commands:
- `CLARA_EVENT_TYPE`, `CLARA_TIMESTAMP` - Event metadata
- `CLARA_NODE_ID`, `CLARA_PLATFORM` - Adapter info
- `CLARA_USER_ID`, `CLARA_CHANNEL_ID`, `CLARA_REQUEST_ID` - Context
- `CLARA_EVENT_DATA` - Full event data as JSON

**Scheduler System:**
Schedule tasks with one-shot, interval, or cron expressions. Configure in `scheduler.yaml`:

```yaml
tasks:
  - name: cleanup-sessions
    type: interval
    interval: 3600  # Every hour
    command: poetry run python -m scripts.cleanup_sessions

  - name: daily-backup
    type: cron
    cron: "0 3 * * *"  # 3 AM daily
    command: ./scripts/backup.sh
    timeout: 1800
```

Task types: `one_shot` (run once), `interval` (every N seconds), `cron` (cron expression)

**Python Decorators:**
Register hooks and tasks programmatically:

```python
from gateway import hook, scheduled, EventType, TaskType, Event

@hook(EventType.SESSION_START)
async def on_session_start(event: Event):
    print(f"Session started: {event.user_id}")

@scheduled(type=TaskType.INTERVAL, interval=3600)
async def hourly_cleanup():
    # Cleanup logic
    pass
```

### Tool Calling LLM
By default, tool calling uses the **same endpoint and model as your main chat LLM**. This means if you're using a custom endpoint (like clewdr), tool calls go through it too.

**Tool tier minimum**: Tool calls never use the "low" tier (e.g., Haiku). When a message triggers the "low" tier, tool calls automatically use the base model instead (e.g., `CUSTOM_OPENAI_MODEL`). This ensures tools always have sufficient capability for complex operations.

Example configuration:
```bash
CUSTOM_OPENAI_MODEL="claude-sonnet-4-5"       # Base model (used for tools when tier=low)
CUSTOM_OPENAI_MODEL_HIGH="claude-opus-4-5"   # High tier
CUSTOM_OPENAI_MODEL_MID="claude-sonnet-4-5"  # Mid tier
CUSTOM_OPENAI_MODEL_LOW="claude-haiku-4-5"   # Low tier (chat only, not for tools)
```

Optional overrides:
- `TOOL_API_KEY` - Override API key for tool calls
- `TOOL_BASE_URL` - Override base URL for tool calls

**Tool Status Descriptions:**
When Clara executes tools, she displays status messages in Discord. These descriptions are generated using an LLM to provide rich context.

- `TOOL_DESC_TIER` - Model tier for generating descriptions: "high" (default, Opus-class), "mid" (Sonnet-class), or "low" (Haiku-class)
- `TOOL_DESC_MAX_WORDS` - Maximum words in descriptions (default: 20)

Example output with high tier:
```
-# 🐍 Running Python code... (step 1)
-# ↳ *Analyzing the uploaded CSV file to compute summary statistics and identify missing values*
```

**For Claude proxies (like clewdr)**: Use `LLM_PROVIDER=anthropic` with `ANTHROPIC_BASE_URL` for native Anthropic SDK support. This uses native Claude tool calling without format conversion.

**Tool Communication Mode (OpenClaw-style):**
- `TOOL_CALL_MODE` - How tools are communicated to the LLM:
  - `xml` (default): OpenClaw-style system prompt injection
  - `native`: Uses API-native tool calling (OpenAI/Anthropic format)

When using `TOOL_CALL_MODE=xml`:
- Tools are serialized to XML and injected into the system prompt
- Works with any LLM provider (doesn't require native tool support)
- Function calls are parsed from the LLM response text using XML tags
- Format: `<function_calls><invoke name="tool_name"><parameter name="arg">value</parameter></invoke></function_calls>`

**Unified Tool Calling (Recommended for New Code):**
The `make_llm_with_tools_unified()` function provides a standardized interface for all providers:

```python
from clara_core import make_llm_with_tools_unified, ToolResponse

# Create unified tool-calling LLM
llm = make_llm_with_tools_unified(tools, tier="mid")

# Call returns standardized ToolResponse
response: ToolResponse = llm(messages)

if response.has_tool_calls:
    for call in response.tool_calls:
        print(f"Tool: {call.name}, Args: {call.arguments}")
else:
    print(response.content)

# Convert to OpenAI dict format if needed
openai_dict = response.to_openai_dict()
```

Benefits:
- Works with all providers (OpenRouter, NanoGPT, OpenAI, Anthropic)
- Returns standardized `ToolResponse` object (not provider-specific types)
- No provider-specific branching in calling code
- Handles format conversion internally

To enable Docker sandbox + web search:
```bash
# Install Docker and start the daemon
docker --version  # Verify Docker is installed

# Set web search API key (optional)
export TAVILY_API_KEY="your-tavily-key"
```

### Local File Storage (Discord Bot)
Clara can save files locally that persist across sessions:
- `CLARA_FILES_DIR` - Directory for local file storage (default: ./clara_files)
- `CLARA_MAX_FILE_SIZE` - Max file size in bytes (default: 50MB)

Files are organized per-user. Discord attachments are automatically saved locally.

**Local File Tools** (always available, even without Docker):
- `save_to_local` - Save content to local storage
- `list_local_files` - List saved files
- `read_local_file` - Read a saved file
- `delete_local_file` - Delete a saved file
- `download_from_sandbox` - Copy Docker sandbox file to local storage
- `upload_to_sandbox` - Upload local file to Docker sandbox
- `send_local_file` - Send a saved file to Discord chat

### GitHub Integration (Discord Bot)
Clara can interact with GitHub repositories, issues, PRs, and workflows:
- `GITHUB_TOKEN` - GitHub Personal Access Token (required for GitHub tools)

**GitHub Tools** (requires GITHUB_TOKEN):
- `github_get_me` - Get authenticated user's profile
- `github_search_repositories` - Search for repositories
- `github_get_repository` - Get repository details
- `github_list_issues` / `github_get_issue` / `github_create_issue` - Manage issues
- `github_list_pull_requests` / `github_get_pull_request` / `github_create_pull_request` - Manage PRs
- `github_list_commits` / `github_get_commit` - View commit history
- `github_get_file_contents` / `github_create_or_update_file` - Read/write files
- `github_list_workflows` / `github_list_workflow_runs` / `github_run_workflow` - Manage Actions
- `github_list_gists` / `github_create_gist` - Manage Gists
- And many more (search users, branches, releases, tags, notifications, stars)

### Google Workspace Integration (Discord Bot)
Clara can interact with Google Sheets, Drive, Docs, and Calendar using per-user OAuth 2.0:
- `GOOGLE_CLIENT_ID` - OAuth 2.0 client ID from Google Cloud Console
- `GOOGLE_CLIENT_SECRET` - OAuth 2.0 client secret
- `GOOGLE_REDIRECT_URI` - Callback URL (e.g., https://your-api.up.railway.app/oauth/google/callback)
- `CLARA_API_URL` - API service URL for OAuth redirects (e.g., https://your-api.up.railway.app)

**Connection Tools** (users must connect before using other tools):
- `google_connect` - Generate OAuth URL to connect Google account
- `google_status` - Check if Google account is connected
- `google_disconnect` - Disconnect Google account

**Google Sheets Tools:**
- `google_sheets_create` - Create a new spreadsheet
- `google_sheets_read` - Read data from a range (A1 notation)
- `google_sheets_write` - Write data to a range
- `google_sheets_append` - Append rows to a sheet
- `google_sheets_list` - List user's spreadsheets

**Google Drive Tools:**
- `google_drive_list` - List files with optional query
- `google_drive_upload` - Upload text content as a file
- `google_drive_download` - Download file content
- `google_drive_create_folder` - Create a folder
- `google_drive_share` - Share a file with someone

**Google Docs Tools:**
- `google_docs_create` - Create a new document
- `google_docs_read` - Read document content
- `google_docs_write` - Append text to a document

**Google Calendar Tools:**
- `google_calendar_list_events` - List upcoming events (with filters)
- `google_calendar_get_event` - Get specific event details
- `google_calendar_create_event` - Create a new event
- `google_calendar_update_event` - Modify an existing event
- `google_calendar_delete_event` - Delete an event
- `google_calendar_list_calendars` - List available calendars

**Setup:**
1. Create OAuth 2.0 credentials in Google Cloud Console
2. Enable Google Sheets, Drive, Docs, Calendar, and Gmail APIs
3. Deploy the API service (see below) and add its URL to "Authorized redirect URIs": `https://your-api.up.railway.app/oauth/google/callback`
4. Set the environment variables on both the Discord bot and API service

### Email Monitoring Service (Discord Bot)
Clara can monitor user email accounts and send Discord alerts for important messages.

**Environment Variables:**
- `EMAIL_MONITORING_ENABLED` - Enable the email monitoring service (default: false)
- `EMAIL_ENCRYPTION_KEY` - Fernet key for encrypting IMAP passwords (required for IMAP accounts)
- `EMAIL_DEFAULT_POLL_INTERVAL` - Default polling interval in minutes (default: 5)

Generate encryption key: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`

**Account Management Tools:**
- `email_connect_gmail` - Connect Gmail (uses existing Google OAuth)
- `email_connect_imap` - Connect IMAP account (iCloud, Outlook, etc.)
- `email_list_accounts` - List connected accounts
- `email_disconnect` - Remove an account

**Configuration Tools:**
- `email_set_alert_channel` - Set Discord channel for alerts
- `email_set_quiet_hours` - Configure quiet hours (no alerts)
- `email_toggle_ping` - Enable/disable @mentions

**Rules Tools:**
- `email_apply_preset` - Apply built-in preset (job_hunting, urgent, security, financial, shipping)
- `email_list_presets` - List available presets
- `email_add_rule` - Create custom rule
- `email_list_rules` - List configured rules
- `email_remove_rule` - Remove a rule

**Status Tools:**
- `email_status` - Check monitoring status
- `email_recent_alerts` - View recent alerts

**Built-in Presets:**
- `job_hunting` - Recruiter emails, ATS platforms (Greenhouse, Lever, Workday), job keywords
- `urgent` - Emails with urgent/ASAP keywords
- `security` - Password resets, 2FA codes, security alerts
- `financial` - Banking, payment notifications
- `shipping` - Package tracking, delivery updates

**Database Tables:**
- `email_accounts` - User email connections (Gmail OAuth or encrypted IMAP)
- `email_rules` - Per-user importance rules
- `email_alerts` - Alert history for deduplication

### API Service (OAuth & Endpoints)

Standalone FastAPI service for OAuth callbacks and API endpoints. Runs separately from the Discord bot.

**Location:** `api_service/`

**Environment Variables:**
- `DATABASE_URL` - PostgreSQL connection string (same as Discord bot)
- `GOOGLE_CLIENT_ID` - Google OAuth client ID
- `GOOGLE_CLIENT_SECRET` - Google OAuth client secret
- `GOOGLE_REDIRECT_URI` - OAuth callback URL (e.g., https://your-api.up.railway.app/oauth/google/callback)

**Endpoints:**
- `GET /health` - Health check
- `GET /oauth/google/authorize/{user_id}` - Get OAuth authorization URL (JSON)
- `GET /oauth/google/start/{user_id}` - Redirect to Google OAuth (for Discord buttons)
- `GET /oauth/google/callback` - OAuth callback handler
- `GET /oauth/google/status/{user_id}` - Check connection status
- `POST /oauth/google/disconnect/{user_id}` - Disconnect account

**Railway Deployment:**
1. Create new service from `api_service/` directory
2. Set root directory to `api_service`
3. Enable public networking and note the domain
4. Set environment variables
5. Update `GOOGLE_REDIRECT_URI` on both services to use the API service URL

### Release Dashboard

Standalone FastAPI service for tracking releases across stage/main and triggering deployment workflows.

**Location:** `release_dashboard/`

**Environment Variables:**
- `DATABASE_URL` - PostgreSQL connection string (shared with main app)
- `GITHUB_CLIENT_ID` - GitHub OAuth App client ID
- `GITHUB_CLIENT_SECRET` - GitHub OAuth App client secret
- `GITHUB_REDIRECT_URI` - OAuth callback URL (e.g., https://release.up.railway.app/oauth/callback)
- `GITHUB_REPO_OWNER` - Repository owner (e.g., "BangRocket")
- `GITHUB_REPO_NAME` - Repository name (e.g., "mypalclara")
- `SESSION_SECRET` - Cookie signing secret (auto-generated if not set)
- `WORKFLOW_DEPLOY` - Workflow filename (default: "promote-to-main.yml")

**Features:**
- GitHub OAuth with collaborator check (only repo collaborators can access)
- Two-column view showing stage (development) and main (production)
- Commit diffs showing what's pending deployment from stage to main
- One-click deploy button to trigger the deployment workflow
- Creates release tags on successful deployment (format: `v{YYYY.MM.DD}-{sha}`)
- Deployment timeline with status, who triggered, and release tags

**Endpoints:**
- `GET /` - Main dashboard (requires auth)
- `GET /health` - Health check
- `GET /login` - Redirect to GitHub OAuth
- `GET /oauth/callback` - OAuth callback handler
- `GET /logout` - Clear session
- `POST /api/deploy` - Trigger stage to main deployment

**Railway Deployment:**
1. Create new service from `release_dashboard/` directory
2. Set root directory to `release_dashboard`
3. Enable public networking and note the domain
4. Create a GitHub OAuth App at https://github.com/settings/developers
5. Set environment variables including `GITHUB_REDIRECT_URI` to the Railway domain + `/oauth/callback`

### Claude Code Integration (Discord Bot)
Clara can delegate complex coding tasks to Claude Code, an autonomous AI coding agent.

**Authentication (one of these):**
- Claude Max/Pro subscription: Login via `claude login` in terminal (no API key needed)
- `ANTHROPIC_API_KEY` - Anthropic API key for API-based authentication

**Optional Configuration:**
- `CLAUDE_CODE_WORKDIR` - Default working directory for coding tasks
- `CLAUDE_CODE_MAX_TURNS` - Maximum agent steps per task (default: 10)

**Claude Code Tools:**
- `claude_code` - Execute coding tasks autonomously (read/write files, run commands, etc.)
- `claude_code_status` - Check availability and authentication method
- `claude_code_set_workdir` - Set the working directory for coding tasks
- `claude_code_get_workdir` - Get the current working directory

**Capabilities:**
- Read and write files within the working directory
- Execute shell commands (bash, python, npm, git, etc.)
- Search code with glob and grep patterns
- Multi-step, agentic workflows with automatic file editing

**Example Usage in Discord:**
```
@Clara Check Claude Code status
@Clara Use Claude Code to add error handling to src/api/users.py
@Clara claude_code: Write unit tests for the utils module in /path/to/project
```

### MCP Plugin System (Discord Bot)
Clara can install and use tools from external MCP (Model Context Protocol) servers, similar to Claude Code's `/plugins` command.

**How It Works:**
- MCP servers are installed from Smithery registry, npm, GitHub, Docker, or local paths
- Server configurations are stored as JSON in `MCP_SERVERS_DIR` (default: `.mcp_servers/`)
- Tools from all connected servers are automatically registered with Clara
- Tools use namespaced names: `{server_name}__{tool_name}` (e.g., `everything__echo`)

**Environment Variables:**
- `MCP_SERVERS_DIR` - Directory for cloned repos and built servers (default: `.mcp_servers`)
- `SMITHERY_API_TOKEN` or `SMITHERY_API_KEY` - Smithery API key for authenticated registry access. Get your key from [Smithery API Keys](https://smithery.ai/docs/use/connect). Required for installing servers from Smithery registry.

**Docker Configuration:**
- MCP servers directory is mounted as a bind mount for external access
- Set `MCP_SERVERS_PATH` to customize the host path (default: `./mcp_servers`)
- Inside container, files are at `/app/mcp_servers`

**Installation Sources:**
- **Smithery local**: `smithery:e2b` - Runs server locally via @smithery/cli (stdio transport)
- **Smithery hosted**: `smithery-hosted:@smithery/notion` - Connects to Smithery's hosted infrastructure (HTTP transport with OAuth)
- **npm packages**: `@modelcontextprotocol/server-everything`
- **GitHub repos**: `github.com/user/mcp-server`
- **Docker images**: `ghcr.io/user/mcp-server:latest`
- **Local paths**: `/path/to/mcp-server`

**Hosted Smithery Servers (OAuth):**
Hosted Smithery servers run on Smithery's infrastructure and connect via HTTP transport. They often require OAuth authentication:
1. Install: `mcp_install(source="smithery-hosted:@smithery/notion")`
2. Server status shows "pending_auth"
3. Start OAuth: `mcp_oauth_start(server_name="notion")` - returns authorization URL
4. User visits URL, authorizes access on Smithery
5. Complete: `mcp_oauth_complete(server_name="notion", code="<code>")` - exchanges code for tokens
6. Server connects and tools become available

OAuth tokens are stored in `.mcp_servers/.oauth/` and auto-refresh when expired.

**Management Tools:**
- `smithery_search` - Search Smithery registry for available MCP servers
- `mcp_install` - Install an MCP server from various sources
- `mcp_uninstall` - Remove an installed server
- `mcp_list` - List all installed servers and their tools
- `mcp_enable` / `mcp_disable` - Toggle servers without uninstalling
- `mcp_restart` - Restart a running server
- `mcp_status` - Get detailed status of servers

**OAuth Tools (for hosted Smithery):**
- `mcp_oauth_start` - Start OAuth flow, returns authorization URL
- `mcp_oauth_complete` - Complete OAuth with authorization code
- `mcp_oauth_status` - Check OAuth status of a server
- `mcp_oauth_set_token` - Manually set access token (admin only)

**Permissions (Discord):**
Admin operations require one of:
- Administrator permission
- Manage Channels permission
- Clara-Admin role

**Example Usage in Discord:**
```
@Clara search smithery for file system servers
@Clara install the MCP server smithery:e2b
@Clara install the hosted MCP server smithery-hosted:@smithery/notion
@Clara start oauth for notion
@Clara complete oauth for notion with code ABC123
@Clara list MCP servers
@Clara use everything__echo to echo "Hello World"
```

**Discord Slash Commands:**
- `/mcp search <query>` - Search Smithery registry
- `/mcp install <source>` - Install a server (admin only)
- `/mcp list` - List installed servers
- `/mcp status <server>` - Get server status
- `/mcp tools [server]` - List tools
- `/mcp enable/disable <server>` - Toggle servers
- `/mcp uninstall <server>` - Remove a server (admin only)

**Dependencies:**
- `mcp` - Official MCP Python SDK
- `gitpython` - For cloning GitHub repos

## Key Patterns

- Discord bot uses global `MemoryManager` instance initialized at startup with LLM callable
- **Unified LLM architecture** in `clara_core/llm/`:
  - `LLMConfig` dataclass for configuration (supports tiers, env loading)
  - `LLMProvider` abstract interface with `LangChainProvider`, `DirectAnthropicProvider`, `DirectOpenAIProvider`
  - `ProviderRegistry` for provider caching and factory pattern
  - `ToolCall`/`ToolResponse` dataclasses for standardized tool handling
  - Backward compatibility via `make_llm()`, `make_llm_streaming()`, etc.
- `LLM_PROVIDER=anthropic` uses native Anthropic SDK with native tool calling (recommended for clewdr)
- Sandbox system auto-selects between Docker and Incus based on availability
- Rook (Clara's memory system) is in `clara_core/memory/`

## Production Deployment

### With PostgreSQL (recommended)

Set `DATABASE_URL` and `ROOK_DATABASE_URL` to use PostgreSQL instead of SQLite/Qdrant:

```bash
# .env
DATABASE_URL=postgresql://user:pass@localhost:5432/clara_main
ROOK_DATABASE_URL=postgresql://user:pass@localhost:5432/clara_vectors
```

Enable pgvector on the vectors database:
```sql
CREATE EXTENSION IF NOT EXISTS vector;
```

### Docker Compose

```bash
# Run Discord bot + postgres databases
docker-compose --profile discord --profile postgres up
```

### Migrate Existing Data

```bash
poetry run python scripts/migrate_to_postgres.py --all
```

### Database Backup Service

Automated backup service for Clara and Rook PostgreSQL databases to S3-compatible storage (Wasabi).

**Location:** `backup_service/`

**Environment Variables:**
- `DATABASE_URL` - Clara PostgreSQL connection string
- `ROOK_DATABASE_URL` - Rook PostgreSQL connection string (MEM0_DATABASE_URL also accepted)
- `S3_BUCKET` - S3 bucket name (default: clara-backups)
- `S3_ENDPOINT_URL` - S3 endpoint (default: https://s3.wasabisys.com)
- `S3_ACCESS_KEY` - S3 access key
- `S3_SECRET_KEY` - S3 secret key
- `S3_REGION` - S3 region (default: us-east-1)
- `BACKUP_RETENTION_DAYS` - Days to keep backups (default: 7)
- `RESPAWN_PROTECTION_HOURS` - Min hours between backups (default: 23)
- `FORCE_BACKUP` - Set to "true" to bypass respawn protection
- `DB_RETRY_ATTEMPTS` - Max DB connection retries (default: 5)
- `DB_RETRY_DELAY` - Initial retry delay in seconds (default: 2)

**Railway Deployment:**
Deploy as a separate Railway service with cron schedule:
```bash
# In Railway, create new service from backup_service/ directory
# Cron runs daily at 3:00 AM UTC
```

**Manual Usage:**
```bash
cd backup_service
python backup.py              # Run backup
python backup.py --list       # List available backups
python backup.py --restore    # Show restore instructions
```

**Restore from Backup:**
```bash
# 1. Download backup from Wasabi/S3
aws s3 cp s3://clara-backups/backups/clara/clara_YYYYMMDD_HHMMSS.sql.gz . --endpoint-url=https://s3.wasabisys.com

# 2. Decompress
gunzip clara_YYYYMMDD_HHMMSS.sql.gz

# 3. Restore
psql $DATABASE_URL < clara_YYYYMMDD_HHMMSS.sql
```
