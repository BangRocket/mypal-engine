# Render Deployment Design — Service Decomposition

**Date:** 2026-04-14
**Status:** Approved
**Goal:** Move from scattered VPS deployments to a clean Render + homelab topology. One dashboard for all services except GPU voice.

---

## Target Topology

| Machine | Services | Network |
|---------|----------|---------|
| **Render** (private network) | Gateway, Discord adapter, Teams adapter, Web UI, Email service, Backup service, Qdrant, Managed PostgreSQL, Managed Redis | Internal private network + public endpoints |
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
| `app.mypalclara.com` | Render web service | Web UI (chat, accounts, memories) | **Move** from Hostinger VPS |
| `voice.mypalclara.com` | CF tunnel `9f635d8a` → homelab | Voice chat (Pipecat WebRTC) | **Keep** |
| `mail.mypalclara.com` | `167.88.44.192` (Hostinger VPS) | Mail server | **Keep** |

MX, SPF, DKIM, DMARC, SRV records — **all stay unchanged** (mail infrastructure).

### `shmp.app` — Infrastructure / Internal

| Domain | Points to | Purpose | Action |
|--------|-----------|---------|--------|
| `gateway.shmp.app` | Render web service | Gateway HTTP API (:18790) | **New** |
| `ws.shmp.app` | Render web service | Gateway WebSocket (:18789) | **New** |
| `teams.shmp.app` | Render web service | Teams adapter webhook endpoint | **New** |
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
| `_railway-verify.games.mypalclara.com` | Stale — no longer using Railway | **Delete** TXT |
| `_railway-verify.mypalclara.com` | Stale — no longer using Railway | **Delete** TXT |

### Render Internal (no DNS needed)

Services on Render's private network use internal hostnames:
- `postgres` — Managed PostgreSQL (Render provides connection string)
- `redis` — Managed Redis (Render provides connection string)
- `qdrant` — Docker service, accessible at `qdrant:6333` on private network

---

## Render Services

| Service | Render Type | Exposed? | Docker | Connects to |
|---------|-------------|----------|--------|-------------|
| **Gateway** | Web Service | `gateway.shmp.app` (HTTP API), `ws.shmp.app` (WebSocket) | `Dockerfile.gateway` | PostgreSQL, Qdrant, Redis, LLM APIs |
| **Discord Adapter** | Background Worker | No (outbound only to Discord API) | `Dockerfile.discord` | Gateway WS (private network) |
| **Teams Adapter** | Web Service | `teams.shmp.app` (Azure webhook) | `Dockerfile.teams` | Gateway WS (private network) |
| **Web UI** | Web Service | `app.mypalclara.com` | `web-ui/Dockerfile` | Gateway API (private network), PostgreSQL |
| **Email Service** | Background Worker | No | Shared image | Gateway (private network), IMAP providers |
| **Backup Service** | Cron Job | No | Backup Dockerfile | PostgreSQL, S3 (Wasabi) |
| **Qdrant** | Private Service | No (private network only) | `qdrant/qdrant` image | Persistent disk |

### Service Communication (all on Render private network)

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
DATABASE_URL=<render-managed-postgres-url>
PALACE_DATABASE_URL=<render-managed-postgres-url>  # pgvector in same DB, or separate
QDRANT_URL=http://qdrant:6333                      # Render private network
REDIS_URL=<render-managed-redis-url>
CLARA_GATEWAY_HOST=0.0.0.0
CLARA_GATEWAY_PORT=18789
CLARA_GATEWAY_API_PORT=18790
```

**Discord Adapter:**
```bash
DISCORD_BOT_TOKEN=<token>
CLARA_GATEWAY_URL=ws://gateway:18789    # Render private network hostname
USE_GATEWAY=true
```

**Teams Adapter:**
```bash
TEAMS_APP_ID=<app-id>
TEAMS_APP_PASSWORD=<password>
CLARA_GATEWAY_URL=ws://gateway:18789    # Render private network
TEAMS_PORT=3978
```

**Web UI (Rails):**
```bash
CLARA_GATEWAY_API_URL=http://gateway:18790   # Render private network
DATABASE_URL=<render-managed-postgres-url>
RAILS_ENV=production
```

**Voice (homelab — NOT on Render):**
```bash
CLARA_GATEWAY_API_URL=https://gateway.shmp.app   # Public endpoint
VOICE_TTS_SPEAKER=af_heart
VOICE_STT_MODEL=small
```

**Backup:**
```bash
DATABASE_URL=<render-managed-postgres-url>
S3_BUCKET=clara-backups
S3_ENDPOINT_URL=https://s3.wasabisys.com
S3_ACCESS_KEY=<key>
S3_SECRET_KEY=<secret>
BACKUP_CRON_SCHEDULE=0 3 * * *
```

---

## Migration Order

Do these in sequence. Each step should be verified working before moving to the next.

### Phase 1: Databases on Render
1. Create Render managed PostgreSQL
2. Create Render managed Redis
3. Deploy Qdrant as private Docker service with persistent disk
4. Migrate PostgreSQL data from homelab → Render (pg_dump/pg_restore)
5. Migrate Qdrant data from homelab → Render Qdrant
6. Verify all data is intact

### Phase 2: Gateway on Render
1. Deploy gateway as Render web service
2. Point gateway at Render-internal DB/Qdrant/Redis
3. Set up `gateway.shmp.app` and `ws.shmp.app` DNS
4. Update homelab voice server to use `gateway.shmp.app`
5. Verify voice still works through new gateway URL
6. Verify gateway HTTP API works

### Phase 3: Adapters on Render
1. Deploy Discord adapter as background worker
2. Verify Discord bot is responsive
3. Deploy Teams adapter as web service
4. Set up `teams.shmp.app` DNS
5. Update Azure Bot messaging endpoint to `https://teams.shmp.app/api/messages`
6. Verify Teams bot works

### Phase 4: Web UI on Render
1. Deploy Web UI as web service (unified Docker image)
2. Set up `app.mypalclara.com` DNS (CNAME to Render)
3. Verify web UI works (chat, memories, accounts)
4. Delete old CF tunnel routes for accounts/api/app/chat

### Phase 5: Support Services on Render
1. Deploy backup service as cron job
2. Deploy email service as background worker
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
   - Railway TXT verification records
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

CF tunnel `7918f71e` can be decommissioned once web UI is on Render.

---

## Cost Estimate (Render)

| Service | Render Tier | Est. Cost |
|---------|-------------|-----------|
| Gateway | Starter ($7/mo) or Standard ($25/mo) | $7-25/mo |
| Discord Adapter | Starter ($7/mo) | $7/mo |
| Teams Adapter | Starter ($7/mo) | $7/mo |
| Web UI | Starter ($7/mo) | $7/mo |
| Email Service | Starter ($7/mo) | $7/mo |
| Backup Service | Cron (free tier or $1/mo) | $0-1/mo |
| Qdrant | Private Service ($7/mo + disk) | $7-15/mo |
| PostgreSQL | Starter ($7/mo) or Standard ($20/mo) | $7-20/mo |
| Redis | Starter ($7/mo) | $7/mo |
| **Total** | | **~$56-96/mo** |

Compare to current: Hostinger VPS (~$5-15/mo) + homelab electricity + maintenance time.
The trade-off is cost for operational simplicity.

---

## Rollback Plan

If Render doesn't work out:
1. Databases: pg_dump from Render, restore to any PostgreSQL
2. Qdrant: snapshot and restore
3. Services: Docker images work anywhere
4. DNS: Point records back to previous IPs/tunnels
5. CF tunnels: Re-enable old tunnel configs

Nothing in this migration is one-way. All data is exportable, all services are standard Docker.

---

## Open Questions

1. **Gateway port split:** Render web services expose one port. Gateway currently uses two (18789 WS, 18790 HTTP). May need to either combine into one port or run as two Render services.
2. **Qdrant persistent disk size:** How large is the current Qdrant data? Determines disk allocation on Render.
3. **FalkorDB:** Currently optional (`ENABLE_GRAPH_MEMORY`). If enabled, needs its own service on Render. Not included in initial plan — add later if needed.
4. **Proactive messaging engine:** Currently integrated into gateway. Stays there — no separate service needed.
