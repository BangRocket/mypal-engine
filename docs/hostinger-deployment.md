# Deploying the Web UI on Hostinger

Split deployment: Hostinger serves the static React SPA, the homelab runs everything else (FastAPI, gateway, databases). All API and WebSocket traffic routes from the browser through a cloudflared tunnel to the homelab.

## Architecture

```
Browser (mypalclara.com)
  │
  ├─ Static files ──→ Hostinger (LiteSpeed shared hosting)
  │                    ~/domains/mypalclara.com/public_html/
  │
  ├─ REST API ──────→ api.mypalclara.com (cloudflared tunnel)
  │  /api/v1/*             ↓
  │                    Homelab: FastAPI (:8000)
  ├─ WebSocket ─────→      ├─ /ws/chat
  │  wss://api.../ws/chat  ├─ /api/v1/* (memories, sessions, admin)
  │                        └─ /auth/*   (OAuth callbacks)
  └─ OAuth ─────────→           │
     /auth/login/*         Gateway (:18789) ← local adapters
                           PostgreSQL, Qdrant
```

## Hostinger Environment

- **Type**: Shared hosting (CageFS, LiteSpeed)
- **SSH**: `ssh -i ~/.ssh/id_ed25519_clara -p 65002 u890312866@145.223.106.236`
- **Webroot**: `~/domains/mypalclara.com/public_html/`
- **Capabilities**: Static file serving only (no Node, no Docker, Python 3.6 only)
- **Rewrite engine**: LiteSpeed (Apache-compatible `.htaccess`)

## Prerequisites

- Domain `mypalclara.com` pointed at Hostinger (already configured)
- Subdomain `api.mypalclara.com` — either:
  - CNAME to cloudflared tunnel hostname, or
  - Pointed at homelab public IP with cloudflared handling TLS
- cloudflared installed on homelab
- FastAPI web server running on homelab (`mypalclara.web`)

## Step 1: Homelab — cloudflared Tunnel

### Create the tunnel

```bash
cloudflared tunnel create clara-api
cloudflared tunnel route dns clara-api api.mypalclara.com
```

### Configure the tunnel

`~/.cloudflared/config.yml`:

```yaml
tunnel: <tunnel-id>
credentials-file: /path/to/.cloudflared/<tunnel-id>.json

ingress:
  - hostname: api.mypalclara.com
    service: http://localhost:8000
    originRequest:
      # WebSocket support
      noTLSVerify: false
  - service: http_status:404
```

cloudflared handles TLS termination automatically — the FastAPI server receives plain HTTP.

### Run the tunnel

```bash
# Foreground (testing)
cloudflared tunnel run clara-api

# As a service (production)
sudo cloudflared service install
sudo systemctl enable cloudflared
sudo systemctl start cloudflared
```

## Step 2: Homelab — FastAPI Configuration

Add/update these environment variables for the web server:

```bash
# Server
WEB_HOST=0.0.0.0
WEB_PORT=8000
WEB_SECRET_KEY=<generate-a-strong-random-value>

# CORS — allow the Hostinger-served frontend
WEB_CORS_ORIGINS=https://mypalclara.com,https://www.mypalclara.com

# Frontend URL — where to redirect after OAuth
WEB_FRONTEND_URL=https://mypalclara.com

# Cookie domain — dot prefix allows subdomains to share cookies
# Only needed if cookies must cross between mypalclara.com and api.mypalclara.com
WEB_COOKIE_DOMAIN=.mypalclara.com

# Gateway stays local
CLARA_GATEWAY_URL=ws://127.0.0.1:18789/ws

# OAuth redirect URIs — callbacks go to the API server, not the SPA
DISCORD_OAUTH_REDIRECT_URI=https://api.mypalclara.com/auth/callback/discord
GOOGLE_OAUTH_REDIRECT_URI=https://api.mypalclara.com/auth/callback/google
```

Update the OAuth app settings in Discord Developer Portal / Google Cloud Console to add the new redirect URIs.

## Step 3: Build the Frontend

On your dev machine:

```bash
cd web-ui

# Install deps (first time only)
pnpm install

# Build with the API URL baked in
VITE_API_URL=https://api.mypalclara.com pnpm build
```

This produces `web-ui/dist/` containing:
- `index.html` — SPA entry point
- `assets/` — hashed JS/CSS bundles

## Step 4: Deploy to Hostinger

### Upload the built files

```bash
# Clean the webroot (preserve .htaccess if it exists)
ssh -i ~/.ssh/id_ed25519_clara -p 65002 u890312866@145.223.106.236 \
  "rm -rf ~/domains/mypalclara.com/public_html/assets ~/domains/mypalclara.com/public_html/index.html"

# Upload dist contents
scp -i ~/.ssh/id_ed25519_clara -P 65002 -r web-ui/dist/* \
  u890312866@145.223.106.236:~/domains/mypalclara.com/public_html/
```

### Create the `.htaccess` for SPA routing

The React app uses client-side routing (`/chat`, `/knowledge`, `/settings`, etc.). All paths must fall back to `index.html`:

```bash
ssh -i ~/.ssh/id_ed25519_clara -p 65002 u890312866@145.223.106.236 \
  'cat > ~/domains/mypalclara.com/public_html/.htaccess << '\''EOF'\''
RewriteEngine On
RewriteBase /

# If the request is for an existing file or directory, serve it directly
RewriteCond %{REQUEST_FILENAME} !-f
RewriteCond %{REQUEST_FILENAME} !-d

# Otherwise fall back to index.html (SPA routing)
RewriteRule . /index.html [L]

# Cache static assets aggressively (hashed filenames handle busting)
<IfModule mod_expires.c>
    ExpiresActive On
    ExpiresByType text/css "access plus 1 year"
    ExpiresByType application/javascript "access plus 1 year"
    ExpiresByType image/svg+xml "access plus 1 year"
    ExpiresByType image/png "access plus 1 year"
    ExpiresByType image/jpeg "access plus 1 year"
    ExpiresByType font/woff2 "access plus 1 year"
</IfModule>

# Security headers
<IfModule mod_headers.c>
    Header set X-Content-Type-Options "nosniff"
    Header set X-Frame-Options "DENY"
    Header set Referrer-Policy "strict-origin-when-cross-origin"
</IfModule>
EOF'
```

## Step 5: Verify

1. **Static SPA**: Visit `https://mypalclara.com` — should load the React app
2. **API health**: Visit `https://api.mypalclara.com/api/v1/health` or similar — should respond
3. **SPA routing**: Visit `https://mypalclara.com/chat` — should load the app (not 404)
4. **OAuth flow**: Click login — should redirect to Discord/Google and back
5. **WebSocket**: Open chat — messages should stream in real time

## Redeployment

After frontend changes, rebuild and re-upload:

```bash
cd web-ui
VITE_API_URL=https://api.mypalclara.com pnpm build
scp -i ~/.ssh/id_ed25519_clara -P 65002 -r web-ui/dist/* \
  u890312866@145.223.106.236:~/domains/mypalclara.com/public_html/
```

Consider scripting this as `scripts/deploy-hostinger.sh`.

## Troubleshooting

### SPA routes return 404
The `.htaccess` rewrite rules aren't being applied. Check:
- LiteSpeed has `mod_rewrite` enabled (Hostinger enables it by default)
- `.htaccess` is in the `public_html/` directory
- File permissions: `chmod 644 .htaccess`

### CORS errors in browser console
The FastAPI server isn't allowing the Hostinger origin. Check:
- `WEB_CORS_ORIGINS` includes `https://mypalclara.com`
- No trailing slashes in the origin URLs

### WebSocket won't connect
- Verify cloudflared tunnel is running: `cloudflared tunnel info clara-api`
- Check that `VITE_API_URL` was set at **build time** (it's baked into the JS bundle)
- Cloudflared supports WebSocket upgrade by default — no special config needed

### OAuth callback fails
- Redirect URIs must exactly match between env vars and the OAuth provider dashboard
- Callbacks go to `api.mypalclara.com`, not `mypalclara.com`
- After OAuth completes, the backend redirects to `WEB_FRONTEND_URL` — make sure that's set to `https://mypalclara.com`

### Cookies not sent cross-origin
- `WEB_COOKIE_DOMAIN` must be `.mypalclara.com` (dot prefix)
- Browser requires `SameSite=None; Secure` for cross-origin cookies
- Verify the FastAPI CORS middleware has `allow_credentials=True` (it does by default)
