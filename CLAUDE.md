# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

MyPalClara is a personal AI assistant with session management and persistent memory (via mem0). The assistant's name is Clara. It uses a Discord bot interface with SQLite/PostgreSQL storage.

## Development Commands

### Backend (Python)
```bash
poetry install                    # Install dependencies
poetry run python discord_bot.py  # Run Discord bot
poetry run ruff check .           # Lint
poetry run ruff format .          # Format
```

### Docker
```bash
docker-compose --profile discord up                    # Run Discord bot only
docker-compose --profile discord --profile postgres up # Discord bot + databases
```

### Memory Management
```bash
# Bootstrap profile data from inputs/user_profile.txt
poetry run python -m src.bootstrap_memory          # Dry run (generates JSON)
poetry run python -m src.bootstrap_memory --apply  # Apply to mem0

# Clear all memory data
poetry run python clear_dbs.py             # With confirmation prompt
poetry run python clear_dbs.py --yes       # Skip confirmation
poetry run python clear_dbs.py --user <id> # Clear specific user
```

## Architecture

### Core Structure
- `discord_bot.py` - Discord bot with multi-user support, reply chains, and streaming responses
- `discord_monitor.py` - Web dashboard for monitoring Discord bot status and activity
- `memory_manager.py` - Core orchestrator: session handling, mem0 integration, prompt building with Clara's persona
- `llm_backends.py` - LLM provider abstraction (OpenRouter, NanoGPT, custom OpenAI, native Anthropic) - both streaming and non-streaming
- `mem0_config.py` - mem0 memory system configuration (Qdrant/pgvector for vectors, OpenAI embeddings)
- `models.py` - SQLAlchemy models: Project, Session, Message, ChannelSummary
- `db.py` - Database setup (SQLite for dev, PostgreSQL for production)
- `email_monitor.py` - Email monitoring and auto-response system

### Sandbox System
- `sandbox/docker.py` - Local Docker sandbox for code execution
- `sandbox/remote_client.py` - Remote VPS sandbox client
- `sandbox/manager.py` - Unified sandbox manager (auto-selects local or remote)

### Storage
- `storage/local_files.py` - Local file storage system for persistent user files

### MCP Plugin System
- `clara_core/mcp/client.py` - MCPClient wrapper for connecting to MCP servers (stdio & HTTP transports)
- `clara_core/mcp/manager.py` - MCPServerManager singleton for managing all server connections
- `clara_core/mcp/installer.py` - Installation from npm, GitHub, Docker, or local paths
- `clara_core/mcp/registry_adapter.py` - Bridge between MCP tools and Clara's ToolRegistry
- `clara_core/mcp/models.py` - MCPServer SQLAlchemy model for configuration storage
- `tools/mcp_management.py` - User-facing management tools (mcp_install, mcp_list, etc.)

### Memory System
- **User memories**: Persistent facts/preferences per user (stored in mem0, searched via `_fetch_mem0_context`)
- **Project memories**: Topic-specific context per project (filtered by project_id in mem0)
- **Graph memories**: Optional relationship tracking via Neo4j or Kuzu (disabled by default, enable with `ENABLE_GRAPH_MEMORY=true`)
- **Session context**: Recent 20 messages + snapshot of last 10 messages from previous session
- **Session summary**: LLM-generated summary stored when session times out
- Sessions auto-timeout after 30 minutes of inactivity (`SESSION_IDLE_MINUTES`)

## Environment Variables

### Required
- `OPENAI_API_KEY` - Always required for mem0 embeddings (text-embedding-3-small)
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

### Mem0 Provider (independent from chat LLM)
- `MEM0_PROVIDER` - Provider for memory extraction: "openrouter" (default), "nanogpt", "openai", or "anthropic"
- `MEM0_MODEL` - Model for memory extraction (default: openai/gpt-4o-mini)
- `MEM0_API_KEY` - Optional: override the provider's default API key
- `MEM0_BASE_URL` - Optional: override the provider's default base URL

Note: For `MEM0_PROVIDER=anthropic`, uses native Anthropic SDK with `anthropic_base_url` support for proxies.

### Optional
- `USER_ID` - Single-user identifier (default: "demo-user")
- `DEFAULT_PROJECT` - Default project name (default: "Default Project")
- `SKIP_PROFILE_LOAD` - Skip initial mem0 profile loading (default: true)
- `ENABLE_GRAPH_MEMORY` - Enable graph memory for relationship tracking (default: false)
- `GRAPH_STORE_PROVIDER` - Graph store provider: "neo4j" (default) or "kuzu" (embedded)
- `NEO4J_URL`, `NEO4J_USERNAME`, `NEO4J_PASSWORD` - Neo4j connection (when using neo4j provider)

### PostgreSQL (Production)
For production, use managed PostgreSQL instead of SQLite/Qdrant:
- `DATABASE_URL` - PostgreSQL connection for SQLAlchemy (default: uses SQLite)
- `MEM0_DATABASE_URL` - PostgreSQL+pgvector connection for mem0 vectors (default: uses Qdrant)

Example (Railway):
```bash
DATABASE_URL=postgresql://user:pass@host:5432/clara_main
MEM0_DATABASE_URL=postgresql://user:pass@host:5432/clara_vectors
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

### Sandbox Code Execution

Clara supports code execution via local Docker or a remote self-hosted sandbox service.

**Mode Selection:**
- `SANDBOX_MODE` - Backend selection: "local", "remote", or "auto" (default: auto)
  - `local`: Use local Docker containers only
  - `remote`: Use remote sandbox API only
  - `auto`: Use remote if configured, fall back to local Docker

**Local Docker** (`SANDBOX_MODE=local` or fallback):
- `DOCKER_SANDBOX_IMAGE` - Docker image for sandbox (default: python:3.12-slim)
- `DOCKER_SANDBOX_TIMEOUT` - Container idle timeout in seconds (default: 900)
- `DOCKER_SANDBOX_MEMORY` - Memory limit per container (default: 512m)
- `DOCKER_SANDBOX_CPU` - CPU limit per container (default: 1.0)

**Remote Sandbox** (`SANDBOX_MODE=remote` or auto with config):
- `SANDBOX_API_URL` - Remote sandbox service URL (e.g., https://sandbox.example.com)
- `SANDBOX_API_KEY` - API key for authentication
- `SANDBOX_TIMEOUT` - Request timeout in seconds (default: 60)

The self-hosted sandbox service is in `sandbox_service/`. Deploy to a VPS with Docker:
```bash
cd sandbox_service
docker-compose build sandbox-image  # Build sandbox container image
docker-compose up -d                # Start API service
```

**Web Search:**
- `TAVILY_API_KEY` - Tavily API key for web search (optional but recommended)

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

**For Claude proxies (like clewdr)**: Use `LLM_PROVIDER=anthropic` with `ANTHROPIC_BASE_URL` for native Anthropic SDK support. This uses native Claude tool calling without format conversion.

### Deprecated
- `TOOL_FORMAT` - No longer needed. Use `LLM_PROVIDER=anthropic` for native Claude tool calling.
- `TOOL_MODEL` - No longer used. Tool calls use tier-based model selection, with "low" tier bumped to base model.

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

### Azure DevOps Integration (Discord Bot)
Clara can interact with Azure DevOps projects, repos, work items, and pipelines:
- `AZURE_DEVOPS_ORG` - Azure DevOps organization name or URL (required)
- `AZURE_DEVOPS_PAT` - Azure DevOps Personal Access Token (required)

**Azure DevOps Tools** (requires AZURE_DEVOPS_ORG and AZURE_DEVOPS_PAT):
- `ado_list_projects` / `ado_list_project_teams` - List projects and teams
- `ado_list_repos` / `ado_get_repo` / `ado_list_branches` - Manage repositories
- `ado_list_pull_requests` / `ado_create_pull_request` / `ado_list_pr_threads` - Manage PRs
- `ado_get_work_item` / `ado_create_work_item` / `ado_search_work_items` / `ado_my_work_items` - Manage work items
- `ado_list_pipelines` / `ado_list_builds` / `ado_run_pipeline` - Manage pipelines
- `ado_list_wikis` / `ado_get_wiki_page` / `ado_create_or_update_wiki_page` - Manage wikis
- `ado_search_code` - Search code across repos
- `ado_list_iterations` / `ado_list_team_iterations` - View sprints/iterations

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
- MCP servers are installed from npm, GitHub, Docker, or local paths
- Server configurations are stored in SQLite (`mcp_servers` table)
- Tools from all connected servers are automatically registered with Clara
- Tools use namespaced names: `{server_name}__{tool_name}` (e.g., `everything__echo`)

**Installation Sources:**
- **npm packages**: `@modelcontextprotocol/server-everything`
- **GitHub repos**: `github.com/user/mcp-server`
- **Docker images**: `ghcr.io/user/mcp-server:latest`
- **Local paths**: `/path/to/mcp-server`

**Management Tools:**
- `mcp_install` - Install an MCP server from various sources
- `mcp_uninstall` - Remove an installed server
- `mcp_list` - List all installed servers and their tools
- `mcp_enable` / `mcp_disable` - Toggle servers without uninstalling
- `mcp_restart` - Restart a running server
- `mcp_status` - Get detailed status of servers

**Permissions (Discord):**
Admin operations require one of:
- Administrator permission
- Manage Channels permission
- Clara-Admin role

**Example Usage in Discord:**
```
@Clara install the MCP server @modelcontextprotocol/server-everything
@Clara list MCP servers
@Clara use everything__echo to echo "Hello World"
```

**Dependencies:**
- `mcp` - Official MCP Python SDK
- `gitpython` - For cloning GitHub repos

### Organic Response System (ORS) - Proactive Conversations
Clara can initiate conversations without user prompting when there's genuine reason to reach out.

**Philosophy:** Reach out when there's genuine reason - not on a schedule. Feel like a thoughtful friend who texts at the right moment, not because a timer went off.

**Environment Variables:**
- `ORS_ENABLED` or `PROACTIVE_ENABLED` - Enable ORS (default: false)
- `ORS_BASE_INTERVAL_MINUTES` - Base check interval, adapts dynamically (default: 15)
- `ORS_MIN_SPEAK_GAP_HOURS` - Minimum hours between proactive messages (default: 2)
- `ORS_ACTIVE_DAYS` - Only check users active in last N days (default: 7)
- `ORS_NOTE_DECAY_DAYS` - Days before note relevance decays to 0 (default: 7)
- `ORS_IDLE_TIMEOUT_MINUTES` - Minutes before extracting conversation summary (default: 30)

**State Machine:**
```
WAIT  ◄──► THINK ◄──► SPEAK
  ▲                      │
  └──────────────────────┘
```

- **WAIT** - No action needed. Stay quiet, but keep gathering context.
- **THINK** - Something's brewing. Process and file an observation/note for later.
- **SPEAK** - There's a clear reason to reach out now with purpose.

**How it works:**
1. Continuous loop with adaptive timing (not fixed polling)
2. Gathers rich context: temporal, calendar, conversation, notes, patterns
3. Situation assessment: What's going on with the user?
4. Action decision: WAIT, THINK, or SPEAK based on assessment
5. Adaptive timing: Next check in 5 min to 8 hours based on context
6. Note system: Observations accumulate, connect, and decay over time

**Context Sources:**
- Temporal: Time of day, day of week, active hours, time since last interaction
- Calendar: Upcoming events (if Google Calendar connected)
- Conversation: Last interaction summary, energy level, open threads
- Notes: Accumulated observations with relevance scoring
- Patterns: Response rates, preferred times, explicit boundaries

**Database Tables:**
- `proactive_messages` - History of proactive messages sent
- `proactive_assessments` - Situation assessments for continuity
- `proactive_notes` - Internal observations with relevance decay
- `user_interaction_patterns` - Learned patterns per user (enhanced)

## Key Patterns

- Discord bot uses global `MemoryManager` instance initialized at startup with LLM callable
- LLM backends support OpenAI-compatible API (via OpenAI SDK) and native Anthropic SDK
- `LLM_PROVIDER=anthropic` uses native Anthropic SDK with native tool calling (recommended for clewdr)
- Sandbox system auto-selects between local Docker and remote VPS based on configuration
- mem0 is vendored locally (in `vendor/mem0/`) with fix for `anthropic_base_url` support

## Production Deployment

### With PostgreSQL (recommended)

Set `DATABASE_URL` and `MEM0_DATABASE_URL` to use PostgreSQL instead of SQLite/Qdrant:

```bash
# .env
DATABASE_URL=postgresql://user:pass@localhost:5432/clara_main
MEM0_DATABASE_URL=postgresql://user:pass@localhost:5432/clara_vectors
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

Automated backup service for Clara and Mem0 PostgreSQL databases to S3-compatible storage (Wasabi).

**Location:** `backup_service/`

**Environment Variables:**
- `DATABASE_URL` - Clara PostgreSQL connection string
- `MEM0_DATABASE_URL` - Mem0 PostgreSQL connection string
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
