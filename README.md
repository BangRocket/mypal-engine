# MyPalClara

A personal AI assistant with persistent memory and tool capabilities. The assistant's name is Clara.

## Features

### Core Capabilities
- **Multi-Platform Support** - Discord, Teams, Slack, Telegram, Matrix, Signal, WhatsApp, and CLI adapters via gateway architecture
- **Persistent Memory (Palace)** - Episodic memory, semantic extraction, knowledge graph, and layered retrieval with HuggingFace embeddings
- **Clara Voice** - Browser-based voice chat via Pipecat + WebRTC with local STT/TTS
- **Web UI** - React SPA with Rails backend for browser-based chat
- **MCP Plugin System** - Install and use tools from external MCP servers
- **Code Execution** - Sandboxed Python/Bash via Docker containers
- **Web Search** - Real-time web search via Tavily/Brave

### Integrations
- **GitHub** - Repository, issue, PR, and workflow management
- **Azure DevOps** - Repos, pipelines, work items
- **Google Workspace** - Sheets, Drive, Docs, and Calendar via OAuth
- **Email Monitoring** - Watch for important emails and send Discord alerts

### LLM Support
- **Multiple Backends** - OpenRouter, NanoGPT, Anthropic, OpenAI, Amazon Bedrock, Azure OpenAI
- **Model Tiers** - Dynamic model selection via message prefixes (`!high`, `!mid`, `!low`)
- **Auto-Tier Selection** - Automatic complexity-based model selection
- **Unified Tool Calling** - Standardized tool interface across all providers (LangChain, native, or XML mode)

## Architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│                         Gateway Server                               │
│  ┌─────────────┐  ┌─────────────┐  ┌──────────────────────────────┐ │
│  │   Router    │  │  Processor  │  │     LLM Orchestrator         │ │
│  │  (queuing)  │──│  (context)  │──│  (streaming, tools, tiers)   │ │
│  └─────────────┘  └─────────────┘  └──────────────────────────────┘ │
│         │                                         │                  │
│         ▼                                         ▼                  │
│  ┌─────────────┐  ┌─────────────┐        ┌──────────────────────┐  │
│  │   Session   │  │   Palace    │        │   Tool Executor      │  │
│  │   Manager   │  │   Memory    │        │   (MCP, built-in)    │  │
│  └─────────────┘  └─────────────┘        └──────────────────────┘  │
│         │                                         │                  │
│  WebSocket :18789                          HTTP API :18790           │
└──────────────────────────────────────────────────────────────────────┘
         │                                         │
         ▼                                         ▼
┌─────────────────┐  ┌─────────────────┐  ┌──────────────────────┐
│ Discord Adapter │  │  Teams Adapter  │  │   MCP Servers        │
│  (py-cord)      │  │  (Bot Framework)│  │   (stdio/HTTP)       │
└─────────────────┘  └─────────────────┘  └──────────────────────┘
┌─────────────────┐  ┌─────────────────┐  ┌──────────────────────┐
│   CLI Adapter   │  │  Voice Server   │  │   Web UI             │
│   (Terminal)    │  │  (Pipecat/RTC)  │  │   (Rails + React)    │
└─────────────────┘  └─────────────────┘  └──────────────────────┘
```

## Quick Start

### Prerequisites

- Python 3.11+
- [Poetry](https://python-poetry.org/)
- Docker (optional, for code execution sandbox)

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
# Run Discord adapter directly
poetry run python -m mypalclara.adapters.discord

# Or run via gateway (recommended for multi-platform)
poetry run python -m mypalclara.gateway start

# With Docker
docker-compose --profile discord up
```

## Configuration

### Required Environment Variables

| Variable | Description |
|----------|-------------|
| `LLM_PROVIDER` | `openrouter`, `nanogpt`, `anthropic`, `openai`, `bedrock`, or `azure` |
| `HF_TOKEN` | Required for HuggingFace embeddings (default provider) |

Plus at least one platform token (e.g. `DISCORD_BOT_TOKEN`).

### Embedding Provider

HuggingFace is the default. OpenAI embeddings are optional.

```bash
EMBEDDING_PROVIDER=huggingface          # Default; or "openai"
HF_TOKEN=your-token                     # Required for HuggingFace
EMBEDDING_MODEL=BAAI/bge-large-en-v1.5  # Default model (1024 dims)
```

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

**Amazon Bedrock**:
```bash
LLM_PROVIDER=bedrock
AWS_REGION=us-east-1
AWS_ACCESS_KEY_ID=your-key
AWS_SECRET_ACCESS_KEY=your-secret
BEDROCK_MODEL=anthropic.claude-3-5-sonnet-20241022-v2:0
```

**Azure OpenAI**:
```bash
LLM_PROVIDER=azure
AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com
AZURE_OPENAI_API_KEY=your-key
AZURE_DEPLOYMENT_NAME=your-deployment
AZURE_API_VERSION=2024-02-15-preview
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
| `ENABLE_GRAPH_MEMORY=true` | Enable relationship tracking (FalkorDB) |
| `SMITHERY_API_KEY` | Enable Smithery MCP server registry |

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

The gateway provides a central message processing hub for platform adapters. It serves both WebSocket (port 18789) and HTTP API (port 18790).

### Running the Gateway

```bash
# Foreground (development)
poetry run python -m mypalclara.gateway --host 127.0.0.1 --port 18789

# Daemon mode with all enabled adapters
poetry run python -m mypalclara.gateway start
poetry run python -m mypalclara.gateway status
poetry run python -m mypalclara.gateway stop

# Start with specific adapter only
poetry run python -m mypalclara.gateway start --adapter discord

# Manage individual adapters
poetry run python -m mypalclara.gateway adapter discord status
poetry run python -m mypalclara.gateway adapter discord restart
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

## Memory System (Palace)

Clara uses the Palace memory system for persistent memory with episodic storage, semantic extraction, layered retrieval, and optional graph relationships.

### Memory Architecture
- **Episodes** - Verbatim conversation chunks stored in Qdrant with topics, emotional tone, significance
- **Semantic Memories** - Extracted facts and preferences in Qdrant
- **Knowledge Graph** - Typed entities with temporal relationships (optional, FalkorDB)
- **Layered Retrieval** - L0 identity -> L1 user profile -> L2 relevant context (episodes + memories + graph)
- **Reflection** - Session-end extraction of episodes, entities, and self-awareness notes

### Configuration

```bash
# Palace LLM (for memory extraction, independent from chat LLM)
PALACE_PROVIDER=openrouter
PALACE_MODEL=openai/gpt-4o-mini

# Vector store (default: Qdrant, or PostgreSQL+pgvector)
PALACE_DATABASE_URL=postgresql://user:pass@host:5432/clara_vectors

# Graph memory (optional)
ENABLE_GRAPH_MEMORY=true
FALKORDB_HOST=localhost
FALKORDB_PORT=6379
```

### Bootstrap Profile Data

```bash
# Generate memory JSON (dry run)
poetry run python scripts/bootstrap_memory.py

# Apply to Palace
poetry run python scripts/bootstrap_memory.py --apply
```

### Clear Memory

```bash
poetry run python scripts/clear_dbs.py              # With prompt
poetry run python scripts/clear_dbs.py --yes        # Skip prompt
poetry run python scripts/clear_dbs.py --user <id>  # Specific user
```

## Web UI

Clara includes a browser-based chat interface.

```bash
# Rails API backend
cd web-ui/backend && rails s -p 3000

# React frontend (Vite dev server)
cd web-ui/frontend && npm run dev  # port 5173

# Docker (unified image)
cd web-ui && docker build -t clara-web .
```

Rails handles game logic directly and proxies API requests to the gateway HTTP API (port 18790).

## Clara Voice

Browser-based voice chat using Pipecat with WebRTC. Uses local STT/TTS by default (no API costs):

- **Silero VAD** - Voice activity detection
- **faster-whisper** - Local speech-to-text
- **Kokoro** - Natural local text-to-speech
- Routes through Clara gateway for full Palace memory context

```bash
# Requires separate installation
pip install pipecat-ai[silero,whisper,kokoro,smallwebrtc] pipecat-ai-small-webrtc-prebuilt

# Run voice server
python -m mypalclara.services.voice.server --host 0.0.0.0 --port 7860
```

Configuration:
```bash
CLARA_GATEWAY_API_URL=http://localhost:18790  # Gateway for LLM
VOICE_TTS_SPEAKER=af_heart                    # Kokoro voice
VOICE_STT_MODEL=small                         # Whisper model size
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

Clara supports Microsoft Teams via the Bot Framework SDK. Setup requires several Azure configuration steps.

### Step 1: Create an Azure Bot Resource

1. Go to [Azure Portal](https://portal.azure.com)
2. Click **Create a resource** -> search for **Azure Bot**
3. Click **Create** and configure:
   - **Bot handle**: A unique name like `MyPalClara`
   - **Subscription**: Your Azure subscription
   - **Resource group**: Create new or use existing
   - **Pricing tier**: F0 (Free) for development
   - **Type of App**: **Multi Tenant** -- This is critical!
   - **Creation type**: **Create new Microsoft App ID**
4. Click **Review + create** -> **Create**

### Step 2: Get Your App Credentials

1. Go to your new Azure Bot resource
2. Click **Configuration** in the left sidebar
3. Copy the **Microsoft App ID** -> this is your `TEAMS_APP_ID`
4. Click **Manage Password** (next to the App ID) to open the App Registration
5. Go to **Certificates & secrets** -> **+ New client secret**
   - Description: "Clara Bot"
   - Expiration: Choose up to 24 months
6. Click **Add** and **immediately copy the Value** (not the Secret ID!)
   - This is your `TEAMS_APP_PASSWORD`
   - You won't be able to see it again

### Step 3: Verify Multi-Tenant Configuration

This is the most common source of errors. The Bot Framework authenticates against its own tenant, not yours.

1. In Azure Portal, go to **Microsoft Entra ID** -> **App registrations**
2. Find your app (search by App ID)
3. Click **Authentication**
4. Under **Supported account types**, ensure it's set to:

   **"Accounts in any organizational directory (Any Microsoft Entra ID tenant - Multitenant)"**

5. If it says "Single tenant", change it and click **Save**

### Step 4: Enable the Teams Channel

1. Go back to your Azure Bot resource
2. Click **Channels** in the left sidebar
3. Click the **Microsoft Teams** icon
4. Accept the terms of service
5. Click **Apply**

### Step 5: Configure the Messaging Endpoint

Your bot needs a public HTTPS URL for Teams to send messages to.

**For local development (ngrok):**
```bash
ngrok http 3978
```
Use the URL: `https://your-subdomain.ngrok.io/api/messages`

**For production:**
Use your deployed URL: `https://your-app.railway.app/api/messages`

Then in Azure Portal:
1. Go to your Azure Bot -> **Configuration**
2. Set **Messaging endpoint** to your URL + `/api/messages`
3. Click **Apply**

### Step 6: Configure Environment Variables

Add to your `.env` file:

```bash
TEAMS_APP_ID=your-microsoft-app-id
TEAMS_APP_PASSWORD="your-client-secret-value"  # Quote if it contains special chars like ~
TEAMS_TENANT_ID=your-tenant-id  # Optional - only if restricting to one org
```

**Important**: If your client secret contains special characters (like `~`), wrap it in quotes.

### Step 7: Create and Install the Teams App Package

The Azure Portal's "Open in Teams" button often fails with permission errors. Create an app manifest instead:

1. Create a folder `teams-app/` with these files:

**`teams-app/manifest.json`**:
```json
{
  "$schema": "https://developer.microsoft.com/en-us/json-schemas/teams/v1.16/MicrosoftTeams.schema.json",
  "manifestVersion": "1.16",
  "version": "1.0.0",
  "id": "YOUR_APP_ID_HERE",
  "packageName": "com.mypalclara.bot",
  "developer": {
    "name": "Your Name",
    "websiteUrl": "https://github.com/BangRocket/mypalclara",
    "privacyUrl": "https://github.com/BangRocket/mypalclara",
    "termsOfUseUrl": "https://github.com/BangRocket/mypalclara"
  },
  "name": {
    "short": "Clara",
    "full": "MyPalClara AI Assistant"
  },
  "description": {
    "short": "Personal AI assistant",
    "full": "Clara is a personal AI assistant with persistent memory and tool capabilities."
  },
  "icons": {
    "outline": "outline.png",
    "color": "color.png"
  },
  "accentColor": "#5558AF",
  "bots": [
    {
      "botId": "YOUR_APP_ID_HERE",
      "scopes": ["personal", "team", "groupChat"],
      "supportsFiles": false,
      "isNotificationOnly": false
    }
  ],
  "permissions": ["identity", "messageTeamMembers"],
  "validDomains": []
}
```

Replace both `YOUR_APP_ID_HERE` with your `TEAMS_APP_ID`.

2. Add icon files (any PNGs will work for testing):
   - `color.png` -- 192x192 pixels
   - `outline.png` -- 32x32 pixels

3. Create the zip package:
```bash
cd teams-app
zip -r ../clara-teams-app.zip *
```

4. Sideload in Teams:
   - Open Teams -> **Apps** -> **Manage your apps**
   - Click **Upload an app** -> **Upload a custom app**
   - Select `clara-teams-app.zip`
   - Click **Add**

### Step 8: Run the Teams Adapter

```bash
# Standalone
poetry run python -m mypalclara.adapters.teams

# Via gateway
poetry run python -m mypalclara.gateway start --adapter teams
```

### Troubleshooting

#### "You do not have permission to use this app here"
The app needs to be installed first. Create and sideload the app manifest (Step 7).

#### "Upload a custom app" is grayed out
Your Teams admin has disabled sideloading. Ask them to enable it in Teams Admin Center -> Teams apps -> Setup policies -> Upload custom apps.

#### AADSTS700016: Application not found in directory 'Bot Framework'
Your App Registration is set to Single Tenant. Change it to Multi-Tenant (Step 3).

#### "Unauthorized" when bot tries to reply
1. Verify you copied the client secret **Value**, not the Secret ID
2. Check for special characters in the secret -- wrap in quotes in `.env`
3. Verify the secret hasn't expired
4. Test credentials manually:
```bash
curl -X POST https://login.microsoftonline.com/botframework.com/oauth2/v2.0/token \
  -d "grant_type=client_credentials" \
  -d "client_id=YOUR_APP_ID" \
  -d "client_secret=YOUR_SECRET" \
  -d "scope=https://api.botframework.com/.default"
```
If this returns a token, credentials are correct.

#### Bot receives messages but doesn't respond
Check the adapter logs for errors. Common causes:
- Messaging endpoint URL is wrong in Azure Bot Configuration
- Firewall blocking outbound connections to Bot Framework
- LLM provider credentials not configured

### Features

- Conversation history via Microsoft Graph API
- File uploads to OneDrive with shareable links
- Adaptive Cards for rich responses
- Model tier selection (`!high`, `!mid`, `!low`)

### Permissions (RSC Recommended)

Use Resource-Specific Consent for scoped access (no tenant-wide admin consent):

```json
"authorization": {
  "permissions": {
    "resourceSpecific": [
      {"name": "ChatMessage.Read.Chat", "type": "Application"},
      {"name": "ChannelMessage.Read.Group", "type": "Application"}
    ]
  }
}
```

See [Teams-Adapter](wiki/Teams-Adapter.md) for full permission options.

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
PALACE_DATABASE_URL=postgresql://user:pass@host:5432/clara_vectors
```

### Database Migrations

```bash
poetry run python scripts/migrate.py          # Run pending
poetry run python scripts/migrate.py status   # Check status
poetry run python scripts/migrate.py create "description"  # New migration
```

### Database Backups

The `mypalclara/services/backup/` directory contains an automated backup service for S3-compatible storage:

```bash
docker-compose --profile backup up -d
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
  - [Architecture](wiki/Architecture.md)
  - [Discord Features](wiki/Discord-Features.md)
  - [Teams Adapter](wiki/Teams-Adapter.md)
  - [CLI Adapter](wiki/CLI-Adapter.md)
  - [MCP Plugin System](wiki/MCP-Plugin-System.md)
  - [Memory System](wiki/Memory-System.md)
  - [Gateway](wiki/Gateway.md)
  - [Proactive Messaging](wiki/Proactive-Messaging.md)
  - [Email Monitoring](wiki/Email-Monitoring.md)
  - [Sandbox System](wiki/Sandbox-System.md)
  - [Backup Service](wiki/Backup-Service.md)
  - [Tool Development](wiki/Tool-Development.md)
  - [Deployment](wiki/Deployment.md)
  - [Troubleshooting](wiki/Troubleshooting.md)
- [CLAUDE.md](CLAUDE.md) - Development guide and API reference

## License

[PolyForm Noncommercial 1.0.0](https://polyformproject.org/licenses/noncommercial/1.0.0/) - Free for non-commercial use. Commercial use requires a separate license.
