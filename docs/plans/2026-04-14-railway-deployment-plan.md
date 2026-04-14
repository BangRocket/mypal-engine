# Railway Deployment Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Migrate all services (except voice) from scattered VPS/homelab to a single Railway project with private networking, managed databases, and clean domain routing.

**Architecture:** Single Railway project with 7 services on a shared private network. Gateway runs WebSocket (18789) + HTTP API (18790) in one container — only 18790 is public. Adapters and web UI connect to gateway over Railway's internal network. Voice stays on homelab, reaches gateway via public HTTPS endpoint.

**Tech Stack:** Railway (Hobby tier), Docker, PostgreSQL (managed), Redis (managed), Qdrant (Docker), Cloudflare DNS, Cloudflare Tunnels

**Design doc:** `docs/plans/2026-04-14-railway-deployment-design.md`

---

## Pre-flight: Current State

Before starting, verify:
- [ ] Railway account exists with Hobby plan ($5/mo)
- [ ] `shmp.app` domain is registered and DNS is accessible (Cloudflare)
- [ ] Current homelab databases are backed up (pg_dump + Qdrant snapshot)
- [ ] GitHub repo (`BangRocket/mypalclara`) is accessible from Railway

---

## Task 1: Fix Dockerfile.gateway — Expose HTTP API Port

The Dockerfile only exposes 18789 (WebSocket). The HTTP API on 18790 is also needed.

**Files:**
- Modify: `Dockerfile.gateway`

**Step 1: Add EXPOSE for HTTP API port**

In `Dockerfile.gateway`, find the existing EXPOSE line and add 18790:

```dockerfile
EXPOSE 18789 18790
```

**Step 2: Verify build still works**

Run: `docker build -f Dockerfile.gateway -t clara-gateway-test .`
Expected: Builds successfully

**Step 3: Commit**

```bash
git add Dockerfile.gateway
git commit -m "fix: expose HTTP API port 18790 in gateway Dockerfile"
```

---

## Task 2: Fix Root railway.toml — Gateway Config

The current `railway.toml` is broken (references `Dockerfile.discord` and non-existent `discord_bot.py`). Replace with gateway config.

**Files:**
- Modify: `railway.toml`

**Step 1: Rewrite railway.toml for gateway service**

```toml
[build]
dockerfilePath = "Dockerfile.gateway"

[deploy]
healthcheckPath = "/api/v1/health"
healthcheckTimeout = 300
restartPolicyType = "ON_FAILURE"
restartPolicyMaxRetries = 5
```

Note: Railway uses the `PORT` env var for public routing. The gateway HTTP API listens on 18790. Set `PORT=18790` in Railway dashboard (or `CLARA_GATEWAY_API_PORT=$PORT`).

**Step 2: Commit**

```bash
git add railway.toml
git commit -m "fix: update railway.toml for gateway deployment"
```

---

## Task 3: Create Railway Service Configs for Adapters

Each adapter needs a railway config. Railway uses root directory + Dockerfile path to determine what to build for each service.

**Files:**
- Create: `deploy/discord/railway.toml`
- Create: `deploy/teams/railway.toml`
- Create: `deploy/backup/railway.toml`

**Step 1: Create deploy directory structure**

```bash
mkdir -p deploy/discord deploy/teams deploy/backup
```

**Step 2: Discord adapter config**

`deploy/discord/railway.toml`:
```toml
[build]
dockerfilePath = "../../Dockerfile.discord"

[deploy]
restartPolicyType = "ON_FAILURE"
restartPolicyMaxRetries = 5
```

No health check — Discord adapter is a worker (no HTTP endpoint). Railway will monitor process health.

**Step 3: Teams adapter config**

`deploy/teams/railway.toml`:
```toml
[build]
dockerfilePath = "../../Dockerfile.teams"

[deploy]
healthcheckPath = "/api/messages"
healthcheckTimeout = 300
restartPolicyType = "ON_FAILURE"
restartPolicyMaxRetries = 5
```

**Step 4: Backup service config**

`deploy/backup/railway.toml`:
```toml
[build]
dockerfilePath = "../../mypalclara/services/backup/Dockerfile"

[deploy]
healthcheckPath = "/health"
healthcheckTimeout = 300
restartPolicyType = "ON_FAILURE"
restartPolicyMaxRetries = 3
```

**Step 5: Commit**

```bash
git add deploy/
git commit -m "feat: add Railway deploy configs for Discord, Teams, backup services"
```

---

## Task 4: Update docker-compose.yml — Expose Gateway HTTP API Port

The docker-compose currently only maps port 18789. Add 18790 for local dev parity with Railway.

**Files:**
- Modify: `docker-compose.yml`

**Step 1: Add port 18790 to gateway service**

Find the gateway service ports section and add:
```yaml
ports:
  - "18789:18789"
  - "18790:18790"
```

**Step 2: Commit**

```bash
git add docker-compose.yml
git commit -m "fix: expose gateway HTTP API port 18790 in docker-compose"
```

---

## Task 5: Create Railway Project and Managed Services

This task is done in the Railway dashboard, not code. Document the steps for execution.

**Step 1: Create Railway project**

- Go to Railway dashboard → New Project → Empty Project
- Name: `mypalclara`
- Connect GitHub repo: `BangRocket/mypalclara`

**Step 2: Add managed PostgreSQL**

- In project → New → Database → PostgreSQL
- Note the internal connection string: `${{Postgres.DATABASE_URL}}`
- Enable pgvector extension: connect via `psql` and run `CREATE EXTENSION IF NOT EXISTS vector;`

**Step 3: Add managed Redis**

- In project → New → Database → Redis
- Note the internal connection string: `${{Redis.REDIS_URL}}`

**Step 4: Add Qdrant as Docker service**

- In project → New → Docker Image
- Image: `qdrant/qdrant:latest`
- Add volume mount: `/qdrant/storage` (persistent disk)
- Environment: No special config needed
- Internal hostname: `qdrant.railway.internal:6333`

**Step 5: Verify databases are running**

- Check Railway dashboard — all three should show green/healthy
- Note connection strings for next tasks

---

## Task 6: Deploy Gateway Service

**Step 1: Add gateway service in Railway**

- In project → New → GitHub Repo → select `BangRocket/mypalclara`
- Root directory: `/` (uses root `railway.toml`)
- Railway will auto-detect `Dockerfile.gateway`

**Step 2: Set environment variables**

```
DATABASE_URL=${{Postgres.DATABASE_URL}}
PALACE_DATABASE_URL=${{Postgres.DATABASE_URL}}
QDRANT_URL=http://qdrant.railway.internal:6333
REDIS_URL=${{Redis.REDIS_URL}}
CLARA_GATEWAY_HOST=0.0.0.0
CLARA_GATEWAY_PORT=18789
CLARA_GATEWAY_API_PORT=18790
PORT=18790
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=<key>
ANTHROPIC_MODEL=claude-sonnet-4-5
HF_TOKEN=<token>
EMBEDDING_PROVIDER=huggingface
EMBEDDING_MODEL=BAAI/bge-large-en-v1.5
PALACE_PROVIDER=openrouter
PALACE_MODEL=openai/gpt-4o-mini
OPENROUTER_API_KEY=<key>
```

Plus any other env vars from current homelab `.env`.

**Step 3: Add custom domain**

- Settings → Custom Domain → `gateway.shmp.app`
- Railway provides a CNAME target
- In Cloudflare DNS for `shmp.app`: add CNAME `gateway` → Railway's target

**Step 4: Verify**

- `curl https://gateway.shmp.app/api/v1/health`
- Expected: `{"status": "ok", "service": "clara-gateway-api"}`

---

## Task 7: Migrate PostgreSQL Data

**Step 1: Dump from homelab**

```bash
pg_dump -Fc -h localhost -U clara -d clara_main > clara_main.dump
```

**Step 2: Get Railway PostgreSQL connection string**

From Railway dashboard → PostgreSQL → Connect → External connection string.

**Step 3: Restore to Railway**

```bash
pg_restore -d "<railway-external-connection-string>" --no-owner --no-privileges clara_main.dump
```

**Step 4: Enable pgvector**

```bash
psql "<railway-external-connection-string>" -c "CREATE EXTENSION IF NOT EXISTS vector;"
```

**Step 5: Verify data**

```bash
psql "<railway-external-connection-string>" -c "SELECT count(*) FROM messages; SELECT count(*) FROM sessions;"
```

Compare counts with homelab database.

---

## Task 8: Migrate Qdrant Data

**Step 1: Create snapshots on homelab Qdrant**

```bash
curl -X POST http://localhost:6333/collections/clara_episodes/snapshots
curl -X POST http://localhost:6333/collections/clara_memories/snapshots
```

**Step 2: Download snapshots**

```bash
# List snapshots to get filenames
curl http://localhost:6333/collections/clara_episodes/snapshots
curl http://localhost:6333/collections/clara_memories/snapshots

# Download each
curl -o episodes.snapshot http://localhost:6333/collections/clara_episodes/snapshots/<snapshot-name>
curl -o memories.snapshot http://localhost:6333/collections/clara_memories/snapshots/<snapshot-name>
```

**Step 3: Expose Railway Qdrant temporarily**

- In Railway → Qdrant service → Settings → Generate Domain (temporary public URL)
- Note the URL

**Step 4: Restore to Railway Qdrant**

```bash
curl -X POST "https://<railway-qdrant-url>/collections/clara_episodes/snapshots/upload" \
  -H "Content-Type: multipart/form-data" \
  -F "snapshot=@episodes.snapshot"

curl -X POST "https://<railway-qdrant-url>/collections/clara_memories/snapshots/upload" \
  -H "Content-Type: multipart/form-data" \
  -F "snapshot=@memories.snapshot"
```

**Step 5: Remove temporary public URL**

- Railway → Qdrant service → Settings → Remove public domain

**Step 6: Verify**

After gateway is connected, check memory retrieval works via:
```bash
curl https://gateway.shmp.app/api/v1/memories/search -X POST \
  -H "Content-Type: application/json" \
  -d '{"query": "test", "user_id": "demo-user"}'
```

---

## Task 9: Deploy Discord Adapter

**Step 1: Add service in Railway**

- In project → New → GitHub Repo → select `BangRocket/mypalclara`
- Root directory: `/` (will need to set Dockerfile path manually)
- Dockerfile path: `Dockerfile.discord`

**Step 2: Set environment variables**

```
DISCORD_BOT_TOKEN=<token>
DISCORD_CLIENT_ID=<client-id>
DISCORD_ALLOWED_SERVERS=<server-ids>
DISCORD_ALLOWED_CHANNELS=<channel-ids>
CLARA_GATEWAY_URL=ws://gateway.railway.internal:18789
USE_GATEWAY=true
DATABASE_URL=${{Postgres.DATABASE_URL}}
```

**Step 3: Verify**

- Check Railway logs for successful connection
- Send a message in Discord, verify Clara responds
- Check that message appears in Railway PostgreSQL

---

## Task 10: Deploy Teams Adapter

**Step 1: Add service in Railway**

- In project → New → GitHub Repo → same repo
- Dockerfile path: `Dockerfile.teams`

**Step 2: Set environment variables**

```
TEAMS_APP_ID=<app-id>
TEAMS_APP_PASSWORD=<password>
CLARA_GATEWAY_URL=ws://gateway.railway.internal:18789
TEAMS_PORT=3978
PORT=3978
```

**Step 3: Add custom domain**

- Settings → Custom Domain → `teams.shmp.app`
- Add CNAME in Cloudflare DNS for `shmp.app`

**Step 4: Update Azure Bot**

- Azure Portal → Bot resource → Configuration
- Change messaging endpoint to: `https://teams.shmp.app/api/messages`

**Step 5: Verify**

- Send a message in Teams, verify Clara responds

---

## Task 11: Deploy Web UI

**Step 1: Add service in Railway**

- In project → New → GitHub Repo → same repo
- Root directory: `web-ui`
- Uses `web-ui/Dockerfile` and `web-ui/railway.toml`

**Step 2: Set environment variables**

```
CLARA_GATEWAY_API_URL=http://gateway.railway.internal:18790
DATABASE_URL=${{Postgres.DATABASE_URL}}
RAILS_ENV=production
RAILS_MASTER_KEY=<key>
SECRET_KEY_BASE=<key>
PORT=3000
```

**Step 3: Add custom domain**

- Settings → Custom Domain → `app.mypalclara.com`
- In Cloudflare DNS for `mypalclara.com`: change `app` CNAME from old CF tunnel to Railway's target

**Step 4: Verify**

- Browse to `https://app.mypalclara.com`
- Verify chat works, memories load, accounts page works

---

## Task 12: Deploy Backup Service

**Step 1: Add service in Railway**

- In project → New → GitHub Repo → same repo
- Dockerfile path: `mypalclara/services/backup/Dockerfile`

**Step 2: Set environment variables**

```
DATABASE_URL=${{Postgres.DATABASE_URL}}
S3_BUCKET=clara-backups
S3_ENDPOINT_URL=https://s3.wasabisys.com
S3_ACCESS_KEY=<key>
S3_SECRET_KEY=<secret>
S3_REGION=us-east-1
BACKUP_CRON_SCHEDULE=0 3 * * *
BACKUP_RETENTION_DAYS=7
PORT=8080
```

**Step 3: Verify**

- Trigger manual backup: check Railway logs
- Verify backup appears in Wasabi S3 bucket

---

## Task 13: Update Homelab Voice Server

Voice stays on homelab but needs to point at the new Railway gateway.

**Files:**
- Modify: homelab `.env` file (not in repo)

**Step 1: Update voice server environment**

Change:
```bash
# Old (local gateway)
CLARA_GATEWAY_API_URL=http://localhost:18790

# New (Railway gateway)
CLARA_GATEWAY_API_URL=https://gateway.shmp.app
```

**Step 2: Restart voice server**

```bash
# On homelab
systemctl restart clara-voice  # or however it's managed
```

**Step 3: Verify**

- Connect to `https://voice.mypalclara.com/client/`
- Speak, verify STT → gateway LLM → TTS pipeline works
- Check Railway gateway logs for incoming `/v1/chat/completions` requests

---

## Task 14: DNS Cleanup — Cloudflare

All done in Cloudflare DNS dashboard.

**Step 1: Set up `shmp.app` records (new)**

| Type | Name | Target | Proxy |
|------|------|--------|-------|
| CNAME | `gateway` | Railway CNAME target | Yes |
| CNAME | `ws` | Railway CNAME target | No (WebSocket needs direct) |
| CNAME | `teams` | Railway CNAME target | Yes |
| A | `matrix` | `167.88.44.192` | No |
| A | `anytype` | `167.88.44.192` | No |

Note: `ws.shmp.app` should NOT be CF proxied if WebSocket connections need to bypass Cloudflare's timeout limits. Test with proxy first — if adapters disconnect, disable proxy.

**Step 2: Update `mypalclara.com` records**

| Action | Record | Old | New |
|--------|--------|-----|-----|
| Update | `app` CNAME | CF tunnel `7918f71e...` | Railway CNAME target |
| Keep | `mypalclara.com` A | `145.223.106.236` | No change |
| Keep | `www` CNAME | `mypalclara.com` | No change |
| Keep | `voice` CNAME | CF tunnel `9f635d8a...` | No change |
| Keep | `mail` A | `167.88.44.192` | No change |

**Step 3: Delete stale `mypalclara.com` records**

| Type | Name | Reason |
|------|------|--------|
| CNAME | `chat` | Redundant with `app` |
| CNAME | `accounts` | Route under `app` now |
| CNAME | `api` | Replaced by `gateway.shmp.app` |
| CNAME | `endpoint` | Replaced by `gateway.shmp.app` |
| A | `anytype` | Moved to `anytype.shmp.app` |
| TXT | `_railway-verify.games` | Stale |
| TXT | `_railway-verify` (root) | Stale — re-verify fresh if needed |

**Step 4: Decommission CF tunnel `7918f71e`**

Once all services previously on Hostinger VPS are migrated to Railway or `shmp.app`:
- Cloudflare dashboard → Zero Trust → Tunnels → `7918f71e` → Delete
- This tunnel served: accounts, api, app, chat (all moved)

**Step 5: Update CF tunnel `9f635d8a`**

This tunnel serves voice + endpoint. After migration:
- Remove `endpoint.mypalclara.com` route (now on Railway)
- Keep `voice.mypalclara.com` route only

---

## Task 15: Homelab Cleanup

**Step 1: Stop migrated services on homelab**

```bash
# Stop gateway, adapters, databases
docker-compose down  # or systemctl stop as appropriate
```

Keep only:
- Voice server
- CF tunnel (for `voice.mypalclara.com`)

**Step 2: Clean up Docker resources**

```bash
docker system prune -a --volumes  # CAUTION: removes all unused images/volumes
```

Only do this AFTER confirming Railway deployment is stable and data migration is verified.

**Step 3: Verify homelab is clean**

Running services should only be:
- Voice server (Pipecat on port 7860)
- cloudflared (CF tunnel for voice)

---

## Task 16: Final Verification Checklist

Run through each service and confirm it works end-to-end:

- [ ] `curl https://gateway.shmp.app/api/v1/health` → `{"status": "ok"}`
- [ ] Discord bot responds to messages
- [ ] Teams bot responds to messages (if actively used)
- [ ] `https://app.mypalclara.com` loads, chat works
- [ ] `https://voice.mypalclara.com/client/` connects, voice works
- [ ] Memory retrieval returns results
- [ ] Backup runs on schedule (check S3 bucket)
- [ ] `mail.mypalclara.com` still works (not touched)
- [ ] `matrix.shmp.app` resolves to Hostinger VPS
- [ ] `anytype.shmp.app` resolves to Hostinger VPS
- [ ] Old domains (`chat`, `accounts`, `api`, `endpoint`) no longer resolve or redirect appropriately
- [ ] Railway dashboard shows all services green
- [ ] Railway usage/cost is within expected range

---

## Rollback Plan

If any phase fails, the homelab is still running everything. Migration is additive until Phase 15 (homelab cleanup). At any point before that:

1. Revert DNS changes in Cloudflare (instant)
2. Re-enable CF tunnel routes
3. Services continue running on homelab

After Phase 15, rollback requires:
1. pg_restore from backup to homelab PostgreSQL
2. Qdrant snapshot restore
3. Restart homelab services via docker-compose
4. Revert DNS
