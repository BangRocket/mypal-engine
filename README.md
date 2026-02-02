# MyPalClara

A personal AI assistant with persistent memory and tool capabilities. The assistant's name is Clara.

## Features

### Core Capabilities
- **Multi-Platform Support** - Discord, Microsoft Teams, and CLI adapters
- **Persistent Memory** - User and project memories via [mem0](https://github.com/mem0ai/mem0) with graph relationship tracking
- **MCP Plugin System** - Install and use tools from external MCP servers (similar to Claude Code's `/plugins`)
- **Code Execution** - Sandboxed Python/Bash via Docker, Incus containers/VMs, or remote VPS
- **Web Search** - Real-time web search via Tavily

### Integrations
- **GitHub** - Repository, issue, PR, and workflow management
- **Azure DevOps** - Repos, pipelines, work items
- **Google Workspace** - Sheets, Drive, Docs, and Calendar via OAuth
- **Email Monitoring** - Watch for important emails and send Discord alerts
- **Claude Code** - Delegate complex coding tasks to Claude Code agent

### LLM Support
- **Multiple Backends** - OpenRouter, NanoGPT, Anthropic, or custom OpenAI-compatible endpoints
- **Model Tiers** - Dynamic model selection via message prefixes (`!high`, `!mid`, `!low`)
- **Auto-Tier Selection** - Automatic complexity-based model selection

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         Gateway Server                           │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────────┐  │
│  │   Router    │  │  Processor  │  │    LLM Orchestrator     │  │
│  │  (queuing)  │──│  (context)  │──│  (streaming, tools)     │  │
│  └─────────────┘  └─────────────┘  └─────────────────────────┘  │
│         │                                        │               │
│         ▼                                        ▼               │
│  ┌─────────────┐                        ┌─────────────────────┐  │
│  │   Session   │                        │   Tool Executor     │  │
│  │   Manager   │                        │   (MCP, built-in)   │  │
│  └─────────────┘                        └─────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
         │                                         │
         │ WebSocket                               │
         ▼                                         ▼
┌─────────────────┐  ┌─────────────────┐  ┌─────────────────────┐
│ Discord Adapter │  │  Teams Adapter  │  │   MCP Servers       │
│  (py-cord)      │  │  (Bot Framework)│  │  (stdio/HTTP)       │
└─────────────────┘  └─────────────────┘  └─────────────────────┘
┌─────────────────┐
│   CLI Adapter   │
│  (Terminal)     │
└─────────────────┘
```

## Quick Start

### Prerequisites

- Python 3.11+
- [Poetry](https://python-poetry.org/)
- Docker (optional, for code execution sandbox)
- Discord bot token

### Installation

```bash
# Clone and install
git clone https://github.com/BangRocket/mypalclara.git
cd mypalclara
poetry install

# Configure environment
cp .env.example .env
# Edit .env with your API keys
```

### Running

```bash
# Run Discord bot directly
poetry run python discord_bot.py

# Or run via gateway (recommended for multi-platform)
poetry run python -m gateway start

# With Docker
docker-compose --profile discord up
```

## Configuration

### Required Environment Variables

| Variable | Description |
|----------|-------------|
| `DISCORD_BOT_TOKEN` | Discord bot token |
| `OPENAI_API_KEY` | Required for mem0 embeddings |
| `LLM_PROVIDER` | `openrouter`, `nanogpt`, `anthropic`, or `openai` |

### LLM Providers

**OpenRouter** (default):
```bash
LLM_PROVIDER=openrouter
OPENROUTER_API_KEY=your-key
OPENROUTER_MODEL=anthropic/claude-sonnet-4
```

**Anthropic** (native SDK, recommended for Claude proxies like clewdr):
```bash
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=your-key
ANTHROPIC_MODEL=claude-sonnet-4-5
ANTHROPIC_BASE_URL=https://custom-proxy.example.com  # Optional
```

**NanoGPT**:
```bash
LLM_PROVIDER=nanogpt
NANOGPT_API_KEY=your-key
NANOGPT_MODEL=moonshotai/Kimi-K2-Instruct-0905
```

**Custom OpenAI-compatible**:
```bash
LLM_PROVIDER=openai
CUSTOM_OPENAI_API_KEY=your-key
CUSTOM_OPENAI_BASE_URL=https://api.openai.com/v1
CUSTOM_OPENAI_MODEL=gpt-4o
```

### Model Tiers

Clara supports dynamic model selection via message prefixes:
- `!high` or `!opus` - High tier (most capable)
- `!mid` or `!sonnet` - Mid tier (balanced) - default
- `!low`, `!haiku`, or `!fast` - Low tier (fast/cheap)

Configure tier-specific models:
```bash
ANTHROPIC_MODEL_HIGH=claude-opus-4-5
ANTHROPIC_MODEL_MID=claude-sonnet-4-5
ANTHROPIC_MODEL_LOW=claude-haiku-4-5
```

Enable auto-tier selection:
```bash
AUTO_TIER_SELECTION=true
```

### Optional Features

| Variable | Description |
|----------|-------------|
| `TAVILY_API_KEY` | Enable web search |
| `GITHUB_TOKEN` | Enable GitHub integration |
| `AZURE_DEVOPS_ORG` / `AZURE_DEVOPS_PAT` | Enable Azure DevOps integration |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | Enable Google Workspace integration |
| `ANTHROPIC_API_KEY` | Enable Claude Code agent |
| `ENABLE_GRAPH_MEMORY=true` | Enable relationship tracking (Neo4j/Kuzu) |
| `SMITHERY_API_TOKEN` | Enable Smithery MCP server registry |

## MCP Plugin System

Clara can extend its capabilities by installing [MCP (Model Context Protocol)](https://modelcontextprotocol.io/) servers.

### Installing MCP Servers

**From Smithery registry:**
```
@Clara install the MCP server smithery:exa
```

**Smithery hosted (with OAuth):**
```
@Clara install smithery-hosted:@smithery/notion
```

**npm packages:**
```
@Clara install the MCP server @modelcontextprotocol/server-everything
```

**GitHub repos:**
```
@Clara install MCP server from github.com/modelcontextprotocol/servers
```

### Multi-User Support

MCP servers support per-user isolation:
- Users can only access their own installed servers
- Global servers (installed by admins) are available to all users
- OAuth tokens are stored per-user for hosted servers
- Usage metrics are tracked per-user

### Management Commands

| Command | Description |
|---------|-------------|
| `mcp_list` | List all installed servers and their tools |
| `mcp_status` | Get detailed status of servers |
| `mcp_install` | Install a new MCP server |
| `mcp_uninstall` | Remove an installed server |
| `mcp_enable` / `mcp_disable` | Toggle servers without uninstalling |
| `mcp_restart` | Restart a running server |
| `mcp_oauth_start` | Start OAuth for hosted servers |
| `mcp_oauth_complete` | Complete OAuth with authorization code |

### Permissions

Admin operations (install, uninstall, enable, disable, restart) require one of:
- Discord Administrator permission
- Manage Channels permission
- Clara-Admin role

## Gateway System

The gateway provides a central message processing hub for platform adapters.

### Running the Gateway

```bash
# Foreground
poetry run python -m gateway --host 127.0.0.1 --port 18789

# Daemon mode
poetry run python -m gateway start
poetry run python -m gateway status
poetry run python -m gateway stop
```

### Hooks

Hooks are automations triggered by gateway events. Configure in `hooks/hooks.yaml`:

```yaml
hooks:
  - name: log-startup
    event: gateway:startup
    command: echo "Gateway started at ${CLARA_TIMESTAMP}"

  - name: notify-errors
    event: tool:error
    command: curl -X POST https://webhook.example.com/notify -d "${CLARA_EVENT_DATA}"
```

### Scheduler

Schedule tasks with cron or interval expressions in `scheduler.yaml`:

```yaml
tasks:
  - name: cleanup-sessions
    type: interval
    interval: 3600
    command: poetry run python -m scripts.cleanup_sessions

  - name: daily-backup
    type: cron
    cron: "0 3 * * *"
    command: ./scripts/backup.sh
```

## Memory System

Clara uses mem0 for persistent memory with vector search (pgvector/Qdrant) and optional graph storage (Neo4j/Kuzu).

### Memory Types
- **User Memories** - Personal facts, preferences, and context
- **Project Memories** - Topic-specific knowledge
- **Key Memories** - Important facts always included in context
- **Emotional Context** - Recent conversation emotional patterns

### Bootstrap Profile Data

```bash
# Generate memory JSON (dry run)
poetry run python -m src.bootstrap_memory

# Apply to mem0
poetry run python -m src.bootstrap_memory --apply
```

### Clear Memory

```bash
poetry run python clear_dbs.py              # With prompt
poetry run python clear_dbs.py --yes        # Skip prompt
poetry run python clear_dbs.py --user <id>  # Specific user
```

## Discord Features

### Channel Modes

- **Active Mode** - Clara responds to all messages
- **Mention Mode** - Clara only responds when mentioned (default)
- **Off Mode** - Clara ignores the channel

Configure with `/clara mode active|mention|off`

### Stop Phrases

Interrupt Clara mid-task with stop phrases:
- "@Clara stop"
- "@Clara nevermind"

### Image Support

Clara can analyze images sent in messages. Configure:
```bash
DISCORD_MAX_IMAGE_DIMENSION=1568
DISCORD_MAX_IMAGES_PER_REQUEST=1
```

## Microsoft Teams Integration

Clara supports Microsoft Teams via the Bot Framework SDK.

### Setup

1. Create an Azure Bot in [Azure Portal](https://portal.azure.com)
2. Enable the Microsoft Teams channel
3. Configure environment variables:

```bash
TEAMS_APP_ID=your-azure-bot-app-id
TEAMS_APP_PASSWORD=your-azure-bot-password
TEAMS_TENANT_ID=your-tenant-id  # Optional
```

### Running

```bash
# Standalone
poetry run python -m adapters.teams

# Via gateway
poetry run python -m gateway start --adapter teams
```

### Features

- Conversation history via Microsoft Graph API
- File uploads to OneDrive with shareable links
- Adaptive Cards for rich responses
- Model tier selection (`!high`, `!mid`, `!low`)

### Azure Permissions

For full functionality, add these Graph API permissions:
- `Chat.Read.All` - Conversation history
- `ChannelMessage.Read.All` - Channel messages
- `Files.ReadWrite.All` - File uploads

See [[Teams-Adapter|wiki/Teams-Adapter]] for detailed setup guide.

## Google Workspace Integration

Clara can interact with Google Sheets, Drive, Docs, and Calendar using per-user OAuth.

### Setup

1. Create OAuth 2.0 credentials in [Google Cloud Console](https://console.cloud.google.com/)
2. Enable Google Sheets, Drive, Docs, Calendar, and Gmail APIs
3. Deploy the API service and configure redirect URI
4. Set environment variables:

```bash
GOOGLE_CLIENT_ID=your-client-id
GOOGLE_CLIENT_SECRET=your-client-secret
GOOGLE_REDIRECT_URI=https://your-api.up.railway.app/oauth/google/callback
CLARA_API_URL=https://your-api.up.railway.app
```

### Connecting

Users connect their Google account via Discord:
```
@Clara connect my Google account
```

## Production Deployment

### Railway

The repo includes `railway.toml` for one-click deployment:

1. Connect your GitHub repo to Railway
2. Set environment variables in Railway dashboard
3. Deploy

### Docker Compose with PostgreSQL

```bash
# Run with PostgreSQL databases
docker-compose --profile discord --profile postgres up
```

Set these for PostgreSQL:
```bash
DATABASE_URL=postgresql://user:pass@host:5432/clara_main
MEM0_DATABASE_URL=postgresql://user:pass@host:5432/clara_vectors
```

### Database Migrations

```bash
poetry run python scripts/migrate.py          # Run pending
poetry run python scripts/migrate.py status   # Check status
poetry run python scripts/migrate.py create "description"  # New migration
```

### Database Backups

The `backup_service/` directory contains an automated backup service for S3-compatible storage:

```bash
cd backup_service
docker-compose up -d
```

## Development

```bash
poetry run ruff check .    # Lint
poetry run ruff format .   # Format
poetry run pytest          # Test
```

### Versioning

Uses CalVer format: `YYYY.WW.N` (Year.Week.Build)

```bash
poetry run python scripts/bump_version.py --show  # Show current
```

See [CLAUDE.md](CLAUDE.md) for detailed development documentation.

## Documentation

- [Wiki](wiki/) - Full documentation
  - [Quick Start](wiki/Quick-Start.md)
  - [Installation](wiki/Installation.md)
  - [Configuration](wiki/Configuration.md)
  - [Discord Features](wiki/Discord-Features.md)
  - [Teams Adapter](wiki/Teams-Adapter.md)
  - [MCP Plugin System](wiki/MCP-Plugin-System.md)
  - [Memory System](wiki/Memory-System.md)
  - [Gateway](wiki/Gateway.md)
  - [Deployment](wiki/Deployment.md)
- [CLAUDE.md](CLAUDE.md) - Development guide and API reference

## License

[PolyForm Noncommercial 1.0.0](https://polyformproject.org/licenses/noncommercial/1.0.0/) - Free for non-commercial use. Commercial use requires a separate license.
