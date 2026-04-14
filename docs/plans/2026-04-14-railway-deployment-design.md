# Railway Deployment Design — Service Decomposition

**Date:** 2026-04-14
**Status:** Approved
**Goal:** Move from scattered VPS deployments to a clean Railway + homelab topology. One dashboard for all services except GPU voice.

---

## Target Topology

| Machine | Services | Network |
|---------|----------|---------|
| **Railway** (private network) | Gateway, Discord adapter, Teams adapter, Web UI, Email service, Backup service, Qdrant, Managed PostgreSQL, Managed Redis | Internal private network + public endpoints |
| **Homelab** (`204.210.251.190`) | Voice server only (Pipecat, GPU for Whisper/Kokoro) | CF tunnel `9f635d8a` → `gateway.shmp.app` |
| **Hostinger VPS** (`167.88.44.192`) | Mail server, Matrix, Anytype | Independent, stays as-is |
| **Hostinger shared hosting** (`145.223.106.236`) | Landing page (`mypalclara.com`) | Static, stays as-is |

---

## Domain Plan

### `mypalclara.com` — Public / User-Facing

| Domain | Points to | Purpose | Action |
|--------|-----------|---------|--------|
| `mypalclara.com` | `145.223.106.236` (shared hosting) | Landing page | **Keep** |
| `www.mypalclara.com` | CNAME → `mypalclara.com` | Redirect | **Keep** |
| `app.mypalclara.com` | Railway web service | Web UI (chat, accounts, memories) | **Move** from Hostinger VPS |
| `voice.mypalclara.com` | CF tunnel `9f635d8a` → homelab | Voice chat (Pipecat WebRTC) | **Keep** |
| `mail.mypalclara.com` | `167.88.44.192` (Hostinger VPS) | Mail server | **Keep** |

MX, SPF, DKIM, DMARC, SRV records — **all stay unchanged** (mail infrastructure).

### `shmp.app` — Infrastructure / Internal

| Domain | Points to | Purpose | Action |
|--------|-----------|---------|--------|
| `gateway.shmp.app` | Railway web service | Gateway HTTP API (:18790) | **New** |
| `ws.shmp.app` | Railway web service | Gateway WebSocket (:18789) | **New** |
| `teams.shmp.app` | Railway web service | Teams adapter webhook endpoint | **New** |
| `matrix.shmp.app` | `167.88.44.192` (Hostinger VPS) | Matrix server | **New** |
| `anytype.shmp.app` | `167.88.44.192` (Hostinger VPS) | Anytype | **New** |

### Dropped Domains

| Domain | Reason | Action |
|--------|--------|--------|
| `chat.mypalclara.com` | Redundant — `app.mypalclara.com` covers it | **Delete** CNAME |
| `accounts.mypalclara.com` | Redundant — route under `app.mypalclara.com` | **Delete** CNAME |
| `api.mypalclara.com` | Replaced by `gateway.shmp.app` | **Delete** CNAME |
| `endpoint.mypalclara.com` | Replaced by `gateway.shmp.app` | **Delete** CNAME |
| `anytype.mypalclara.com` | Moved to `anytype.shmp.app` | **Delete** A record |
| `_railway-verify.games.mypalclara.com` | Stale — will re-verify if needed | **Delete** TXT |
| `_railway-verify.mypalclara.com` | Stale — will re-verify if needed | **Delete** TXT |

### Railway Internal (no DNS needed)

Services on Railway's private network use internal hostnames:
- `postgres.railway.internal` — Managed PostgreSQL
- `redis.railway.internal` — Managed Redis
- `qdrant.railway.internal` — Docker service, accessible at `qdrant.railway.internal:6333`
- `gateway.railway.internal` — Gateway, accessible to adapters and web UI

---

## Railway Services

All services in a single Railway project, connected via private network.

| Service | Railway Type | Exposed? | Docker | Connects to |
|---------|-------------|----------|--------|-------------|
| **Gateway** | Web Service | `gateway.shmp.app` (HTTP API), `ws.shmp.app` (WebSocket) | `Dockerfile.gateway` | PostgreSQL, Qdrant, Redis, LLM APIs |
| **Discord Adapter** | Worker | No (outbound only to Discord API) | `Dockerfile.discord` | Gateway WS (private network) |
| **Teams Adapter** | Web Service | `teams.shmp.app` (Azure webhook) | `Dockerfile.teams` | Gateway WS (private network) |
| **Web UI** | Web Service | `app.mypalclara.com` | `web-ui/Dockerfile` | Gateway API (private network), PostgreSQL |
| **Email Service** | Worker | No | Shared image | Gateway (private network), IMAP providers |
| **Backup Service** | Cron Job | No | Backup Dockerfile | PostgreSQL, S3 (Wasabi) |
| **Qdrant** | Docker Service | No (private network only) | `qdrant/qdrant` image | Volume for persistence |
| **PostgreSQL** | Railway Plugin | No (private network only) | Managed | — |
| **Redis** | Railway Plugin | No (private network only) | Managed | — |

### Service Communication (all on Railway private network)

```
Discord Adapter ──WebSocket──► Gateway ──► PostgreSQL (managed)
Teams Adapter  ──WebSocket──►    │     ──► Qdrant (private service)
Web UI (Rails) ──HTTP─────────►  │     ──► Redis (managed)
                                 │     ──► LLM APIs (external)
                                 │
Voice (homelab) ──HTTP──────────►│  (via gateway.shmp.app, public)
```

### Environment Variables (key changes per service)

**Gateway:**
```bash
DATABASE_URL=${{Postgres.DATABASE_URL}}
PALACE_DATABASE_URL=${{Postgres.DATABASE_URL}}
QDRANT_URL=http://qdrant.railway.internal:6333
REDIS_URL=${{Redis.REDIS_URL}}
CLARA_GATEWAY_HOST=0.0.0.0
CLARA_GATEWAY_PORT=18789
CLARA_GATEWAY_API_PORT=18790
```

**Discord Adapter:**
```bash
DISCORD_BOT_TOKEN=<token>
CLARA_GATEWAY_URL=ws://gateway.railway.internal:18789
USE_GATEWAY=true
```

**Teams Adapter:**
```bash
TEAMS_APP_ID=<app-id>
TEAMS_APP_PASSWORD=<password>
CLARA_GATEWAY_URL=ws://gateway.railway.internal:18789
TEAMS_PORT=3978
```

**Web UI (Rails):**
```bash
CLARA_GATEWAY_API_URL=http://gateway.railway.internal:18790
DATABASE_URL=${{Postgres.DATABASE_URL}}
RAILS_ENV=production
```

**Voice (homelab — NOT on Railway):**
```bash
CLARA_GATEWAY_API_URL=https://gateway.shmp.app
VOICE_TTS_SPEAKER=af_heart
VOICE_STT_MODEL=small
```

**Backup:**
```bash
DATABASE_URL=${{Postgres.DATABASE_URL}}
S3_BUCKET=clara-backups
S3_ENDPOINT_URL=https://s3.wasabisys.com
S3_ACCESS_KEY=<key>
S3_SECRET_KEY=<secret>
BACKUP_CRON_SCHEDULE=0 3 * * *
```

---

## Migration Order

Do these in sequence. Each step should be verified working before moving to the next.

### Phase 1: Railway Project + Databases
1. Create Railway project (`mypalclara`)
2. Add PostgreSQL plugin
3. Add Redis plugin
4. Deploy Qdrant as Docker service with persistent volume
5. Migrate PostgreSQL data from homelab → Railway (pg_dump/pg_restore)
6. Migrate Qdrant data from homelab → Railway Qdrant (snapshot/restore)
7. Verify all data is intact

### Phase 2: Gateway on Railway
1. Deploy gateway from `Dockerfile.gateway`
2. Point gateway at Railway-internal DB/Qdrant/Redis
3. Add custom domain `gateway.shmp.app` + `ws.shmp.app`
4. Update homelab voice server to use `https://gateway.shmp.app`
5. Verify voice still works through new gateway URL
6. Verify gateway HTTP API works

### Phase 3: Adapters on Railway
1. Deploy Discord adapter as worker from `Dockerfile.discord`
2. Verify Discord bot is responsive
3. Deploy Teams adapter as web service from `Dockerfile.teams`
4. Add custom domain `teams.shmp.app`
5. Update Azure Bot messaging endpoint to `https://teams.shmp.app/api/messages`
6. Verify Teams bot works

### Phase 4: Web UI on Railway
1. Deploy Web UI from `web-ui/Dockerfile`
2. Add custom domain `app.mypalclara.com`
3. Verify web UI works (chat, memories, accounts)
4. Delete old CF tunnel routes for accounts/api/app/chat

### Phase 5: Support Services on Railway
1. Deploy backup service as cron job
2. Deploy email service as worker
3. Verify scheduled backups run
4. Verify email monitoring works

### Phase 6: DNS Cleanup
1. Set up `shmp.app` DNS records (matrix, anytype)
2. Delete dropped `mypalclara.com` records:
   - `chat.mypalclara.com` CNAME
   - `accounts.mypalclara.com` CNAME
   - `api.mypalclara.com` CNAME
   - `endpoint.mypalclara.com` CNAME
   - `anytype.mypalclara.com` A record
   - Stale Railway TXT verification records
3. Update `voice.mypalclara.com` CF tunnel if needed (should still point to homelab)
4. Decommission old CF tunnel `7918f71e` (Hostinger VPS tunnel) once all services are migrated

### Phase 7: Homelab Cleanup
1. Stop gateway, adapters, databases on homelab
2. Voice server is the only remaining service
3. Clean up old Docker containers/volumes
4. Update CF tunnel `9f635d8a` config — only route `voice.mypalclara.com`

---

## Hostinger VPS (post-migration)

Remains running:
- **Mail server** — `mail.mypalclara.com` (A record stays)
- **Matrix** — `matrix.shmp.app` (new DNS)
- **Anytype** — `anytype.shmp.app` (new DNS)

CF tunnel `7918f71e` can be decommissioned once web UI is on Railway.

---

## Cost Estimate (Railway Hobby — $5/mo subscription)

Usage-based pricing. Idle services cost almost nothing.

| Service | Est. CPU/RAM | Est. Cost |
|---------|-------------|-----------|
| Gateway | 0.5 vCPU / 1GB avg | $10-20/mo |
| Discord Adapter | 0.1 vCPU / 256MB avg | $3-5/mo |
| Teams Adapter | 0.1 vCPU / 128MB avg | $2-3/mo |
| Web UI (Rails) | 0.2 vCPU / 256MB avg | $3-5/mo |
| Email Service | 0.05 vCPU / 128MB avg | $1-2/mo |
| Backup Service | Burst 5 min/day | <$1/mo |
| Qdrant (Docker) | 0.2 vCPU / 512MB + disk | $5-10/mo |
| PostgreSQL (managed) | Usage-based | $5-10/mo |
| Redis (managed) | Usage-based | $3-5/mo |
| Subscription | Hobby plan | $5/mo |
| Included credits | | -$5/mo |
| **Total** | | **~$32-61/mo** |

---

## Rollback Plan

If Railway doesn't work out:
1. Databases: pg_dump from Railway, restore to any PostgreSQL
2. Qdrant: snapshot and restore
3. Services: Docker images work anywhere (Render, Fly.io, self-hosted)
4. DNS: Point records back to previous IPs/tunnels
5. CF tunnels: Re-enable old tunnel configs

Nothing in this migration is one-way. All data is exportable, all services are standard Docker.

---

## Open Questions

1. **Gateway port split:** Railway web services expose one port by default. Gateway currently uses two (18789 WS, 18790 HTTP). Options: combine into one port, or deploy as two Railway services sharing the same codebase.
2. **Qdrant persistent volume size:** How large is the current Qdrant data? Determines volume allocation on Railway.
3. **FalkorDB:** Currently optional (`ENABLE_GRAPH_MEMORY`). If enabled, needs its own Docker service on Railway. Not included in initial plan — add later if needed.
4. **Proactive messaging engine:** Currently integrated into gateway. Stays there — no separate service needed.
5. **Railway sleep policy:** Hobby plan services may sleep after inactivity. Gateway and Discord adapter need to stay awake. Verify Railway's always-on behavior for workers.
