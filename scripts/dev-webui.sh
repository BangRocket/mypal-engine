#!/usr/bin/env bash
#
# Start the local web UI dev environment.
#
# Ensures PostgreSQL, Rails backend, and Vite frontend are all running,
# creates a dev test user in both databases, and opens the browser.
#
# Usage:
#   ./scripts/dev-webui.sh          # start everything
#   ./scripts/dev-webui.sh stop     # stop Rails + Vite
#   ./scripts/dev-webui.sh status   # check what's running

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BACKEND="$ROOT/web-ui/backend"
FRONTEND="$ROOT/web-ui/frontend"

# Homebrew Ruby (keg-only, not on PATH by default)
RUBY_BIN="/usr/local/opt/ruby/bin"
GEM_BIN="/usr/local/lib/ruby/gems/4.0.0/bin"
export PATH="$RUBY_BIN:$GEM_BIN:$PATH"

RAILS_PID_FILE="$BACKEND/tmp/pids/server.pid"
VITE_PID_FILE="$FRONTEND/.vite.pid"

DEV_USER_UUID="00000000-0000-0000-0000-000000000dev"
DEV_USER_NAME="Dev User"

# Colors
RED='\033[31m'
GREEN='\033[32m'
YELLOW='\033[33m'
BLUE='\033[94m'
RESET='\033[0m'

info()  { echo -e "${BLUE}[dev]${RESET} $*"; }
ok()    { echo -e "${GREEN}[dev]${RESET} $*"; }
warn()  { echo -e "${YELLOW}[dev]${RESET} $*"; }
err()   { echo -e "${RED}[dev]${RESET} $*"; }

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

is_pg_running() {
    pg_isready -q 2>/dev/null
}

is_rails_running() {
    if [[ -f "$RAILS_PID_FILE" ]] && kill -0 "$(cat "$RAILS_PID_FILE")" 2>/dev/null; then
        return 0
    fi
    # Also check by port
    lsof -iTCP:3000 -sTCP:LISTEN -t >/dev/null 2>&1
}

is_vite_running() {
    if [[ -f "$VITE_PID_FILE" ]] && kill -0 "$(cat "$VITE_PID_FILE")" 2>/dev/null; then
        return 0
    fi
    lsof -iTCP:5173 -sTCP:LISTEN -t >/dev/null 2>&1
}

stop_rails() {
    if [[ -f "$RAILS_PID_FILE" ]]; then
        local pid
        pid=$(cat "$RAILS_PID_FILE")
        if kill -0 "$pid" 2>/dev/null; then
            kill "$pid" 2>/dev/null
            sleep 1
            info "Stopped Rails (pid $pid)"
        fi
    fi
    # Always clean PID file
    rm -f "$RAILS_PID_FILE"
    # Kill anything else on :3000
    local pids
    pids=$(lsof -iTCP:3000 -sTCP:LISTEN -t 2>/dev/null || true)
    if [[ -n "$pids" ]]; then
        echo "$pids" | xargs kill 2>/dev/null || true
        sleep 1
    fi
}

stop_vite() {
    if [[ -f "$VITE_PID_FILE" ]]; then
        local pid
        pid=$(cat "$VITE_PID_FILE")
        if kill -0 "$pid" 2>/dev/null; then
            kill "$pid" 2>/dev/null
            info "Stopped Vite (pid $pid)"
        fi
        rm -f "$VITE_PID_FILE"
    fi
    local pids
    pids=$(lsof -iTCP:5173 -sTCP:LISTEN -t 2>/dev/null || true)
    if [[ -n "$pids" ]]; then
        echo "$pids" | xargs kill 2>/dev/null || true
    fi
}

# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

cmd_stop() {
    info "Stopping services..."
    stop_rails
    stop_vite
    ok "All stopped."
}

cmd_status() {
    echo ""
    if is_pg_running; then
        ok "PostgreSQL:  running"
    else
        err "PostgreSQL:  not running"
    fi

    if is_rails_running; then
        ok "Rails:       running on :3000"
    else
        err "Rails:       not running"
    fi

    if is_vite_running; then
        ok "Vite:        running on :5173"
    else
        err "Vite:        not running"
    fi
    echo ""
}

ensure_env_file() {
    if [[ ! -f "$BACKEND/.env" ]]; then
        err "Missing $BACKEND/.env"
        err "Create it with at minimum:"
        echo ""
        echo "  IDENTITY_SERVICE_URL=https://identity-service-production-3b83.up.railway.app"
        echo "  IDENTITY_SERVICE_SECRET=<from Railway>"
        echo "  IDENTITY_JWT_SECRET=<from Railway>"
        echo "  CLARA_GATEWAY_API_URL=https://backend.mypalclara.com"
        echo "  CLARA_GATEWAY_SECRET=<from VPS>"
        echo "  WEB_DEV_MODE=true"
        echo ""
        exit 1
    fi
}

ensure_postgres() {
    if is_pg_running; then
        ok "PostgreSQL already running"
    else
        info "Starting PostgreSQL..."
        brew services start postgresql@18 >/dev/null 2>&1
        sleep 2
        if is_pg_running; then
            ok "PostgreSQL started"
        else
            err "Failed to start PostgreSQL"
            exit 1
        fi
    fi
}

ensure_db() {
    # Check if the dev database exists
    if /usr/local/opt/postgresql@18/bin/psql -U clara_games -d clara_games_development -c "SELECT 1" >/dev/null 2>&1; then
        ok "Database clara_games_development exists"
    else
        info "Creating database..."
        # Create user if needed
        /usr/local/opt/postgresql@18/bin/psql -U "$(whoami)" -d postgres \
            -c "CREATE USER clara_games WITH PASSWORD 'claragames2026' CREATEDB;" 2>/dev/null || true
        cd "$BACKEND" && rails db:prepare 2>&1
        ok "Database created and migrated"
    fi

    # Run pending migrations
    cd "$BACKEND"
    local pending
    pending=$(rails db:migrate:status 2>&1 | grep "^\s*down" || true)
    if [[ -n "$pending" ]]; then
        info "Running pending migrations..."
        rails db:migrate 2>&1
        ok "Migrations complete"
    fi
}

ensure_dev_user_rails() {
    # Create dev user in Rails DB if not exists
    cd "$BACKEND"
    rails runner "
      User.find_or_create_by!(canonical_user_id: '$DEV_USER_UUID') do |u|
        u.display_name = '$DEV_USER_NAME'
      end
      puts 'Dev user ready: $DEV_USER_UUID'
    " 2>&1 | grep -v "^$" || true
}

ensure_dev_user_gateway() {
    # Create dev user in the gateway's SQLite DB so gateway API calls work.
    # The gateway uses SQLAlchemy with canonical_users table.
    local db_file="$ROOT/assistant.db"

    if [[ ! -f "$db_file" ]]; then
        warn "Gateway DB ($db_file) not found — skipping gateway dev user."
        warn "Run the gateway once to create it, then re-run this script."
        return
    fi

    # Insert dev user if not exists
    sqlite3 "$db_file" <<SQL
INSERT OR IGNORE INTO canonical_users (id, display_name, status, is_admin)
VALUES ('$DEV_USER_UUID', '$DEV_USER_NAME', 'active', 0);
SQL
    ok "Dev user in gateway DB: $DEV_USER_UUID"
}

ensure_deps() {
    # Backend
    cd "$BACKEND"
    if ! bundle check >/dev/null 2>&1; then
        info "Installing Ruby gems..."
        bundle install 2>&1 | tail -1
    fi

    # Frontend
    cd "$FRONTEND"
    if [[ ! -d "node_modules" ]]; then
        info "Installing frontend deps..."
        pnpm install 2>&1 | tail -3
    fi
}

start_rails() {
    if is_rails_running; then
        ok "Rails already running on :3000"
        return
    fi

    info "Starting Rails on :3000..."
    cd "$BACKEND"
    mkdir -p tmp/pids
    # Remove stale PID file so Puma doesn't refuse to start
    rm -f "$RAILS_PID_FILE"
    nohup rails server -p 3000 >> "$BACKEND/log/development.log" 2>&1 &

    for i in {1..15}; do
        if curl -s -o /dev/null http://localhost:3000/auth/config 2>/dev/null; then
            ok "Rails started on :3000 (pid $(cat "$RAILS_PID_FILE" 2>/dev/null || echo '?'))"
            return
        fi
        sleep 1
    done
    err "Rails failed to start — check $BACKEND/log/development.log"
    exit 1
}

start_vite() {
    if is_vite_running; then
        ok "Vite already running on :5173"
        return
    fi

    info "Starting Vite on :5173..."
    cd "$FRONTEND"
    nohup pnpm run dev > "$FRONTEND/vite.log" 2>&1 &
    local pid=$!
    echo "$pid" > "$VITE_PID_FILE"

    for i in {1..10}; do
        if lsof -iTCP:5173 -sTCP:LISTEN -t >/dev/null 2>&1; then
            ok "Vite started on :5173 (pid $pid)"
            return
        fi
        sleep 1
    done
    err "Vite failed to start — check $FRONTEND/vite.log"
    exit 1
}

cmd_start() {
    echo ""
    info "=== MyPalClara Web UI — Local Dev ==="
    echo ""

    ensure_env_file
    ensure_postgres
    ensure_deps
    ensure_db
    ensure_dev_user_rails
    ensure_dev_user_gateway

    echo ""
    start_rails
    start_vite

    echo ""
    ok "=== Ready ==="
    echo ""
    echo "  Frontend:  http://localhost:5173"
    echo "  Backend:   http://localhost:3000"
    echo "  Dev user:  $DEV_USER_NAME ($DEV_USER_UUID)"
    echo ""
    echo "  Stop:      $0 stop"
    echo "  Status:    $0 status"
    echo ""

    # Open browser
    if command -v open >/dev/null 2>&1; then
        open "http://localhost:5173"
    fi
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

case "${1:-start}" in
    start)  cmd_start ;;
    stop)   cmd_stop ;;
    status) cmd_status ;;
    *)
        echo "Usage: $0 [start|stop|status]"
        exit 1
        ;;
esac
