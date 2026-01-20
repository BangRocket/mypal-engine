# MyPalClara

A personal AI assistant with persistent memory and tool capabilities, powered by Discord. The assistant's name is Clara.

## Features

- **Discord Interface** - Full-featured Discord bot with streaming responses and reply chains
- **Persistent Memory** - User and project memories via [mem0](https://github.com/mem0ai/mem0)
- **MCP Plugin System** - Install and use tools from external MCP servers (similar to Claude Code's `/plugins`)
- **Code Execution** - Sandboxed Python/Bash via local Docker or remote VPS
- **Web Search** - Real-time web search via Tavily
- **File Management** - Local file storage with S3 sync support
- **GitHub/Azure DevOps** - Repository, issue, PR, and pipeline management
- **Google Workspace** - Sheets, Drive, Docs, and Calendar via OAuth
- **Email Monitoring** - Watch for important emails and send Discord alerts
- **Claude Code Integration** - Delegate complex coding tasks to Claude Code agent
- **Multiple LLM Backends** - OpenRouter, NanoGPT, Anthropic, or custom OpenAI-compatible endpoints
- **Model Tiers** - Dynamic model selection via message prefixes (`!high`, `!mid`, `!low`)

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
# Run Discord bot locally
poetry run python discord_bot.py

# Or with Docker
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

### Optional Features

| Variable | Description |
|----------|-------------|
| `TAVILY_API_KEY` | Enable web search |
| `GITHUB_TOKEN` | Enable GitHub integration |
| `AZURE_DEVOPS_ORG` / `AZURE_DEVOPS_PAT` | Enable Azure DevOps integration |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | Enable Google Workspace integration |
| `ANTHROPIC_API_KEY` | Enable Claude Code agent |
| `ENABLE_GRAPH_MEMORY=true` | Enable relationship tracking (Neo4j/Kuzu) |

## MCP Plugin System

Clara can extend its capabilities by installing [MCP (Model Context Protocol)](https://modelcontextprotocol.io/) servers. This is similar to Claude Code's `/plugins` command.

### Installing MCP Servers

MCP servers can be installed from multiple sources:

**npm packages:**
```
@Clara install the MCP server @modelcontextprotocol/server-everything
```

**GitHub repos:**
```
@Clara install MCP server from github.com/modelcontextprotocol/servers
```

**Docker images:**
```
@Clara install MCP server from ghcr.io/example/mcp-server:latest
```

### Using MCP Tools

Once installed, MCP tools are automatically available. Tools use namespaced names:
- Format: `{server_name}__{tool_name}`
- Example: `everything__echo`, `filesystem__read_file`

### Management Commands

| Command | Description |
|---------|-------------|
| `mcp_list` | List all installed servers and their tools |
| `mcp_status` | Get detailed status of servers |
| `mcp_install` | Install a new MCP server |
| `mcp_uninstall` | Remove an installed server |
| `mcp_enable` / `mcp_disable` | Toggle servers without uninstalling |
| `mcp_restart` | Restart a running server |

### Permissions

Admin operations (install, uninstall, enable, disable, restart) require one of:
- Discord Administrator permission
- Manage Channels permission
- Clara-Admin role

## Memory System

Clara uses mem0 for persistent memory with vector search (pgvector/Qdrant) and optional graph storage (Neo4j/Kuzu).

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

See [CLAUDE.md](CLAUDE.md) for detailed development documentation.

## License

[PolyForm Noncommercial 1.0.0](https://polyformproject.org/licenses/noncommercial/1.0.0/) - Free for non-commercial use. Commercial use requires a separate license.
