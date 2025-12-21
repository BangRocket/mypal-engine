#!/bin/bash
# Sync MyPalClara data to remote server
# Usage: ./scripts/sync_to_remote.sh [user@host] [remote_path]

set -e

REMOTE_HOST="${1:-clara}"
REMOTE_PATH="${2:-~/mypalclara}"
LOCAL_PATH="$(cd "$(dirname "$0")/.." && pwd)"

echo "=== MyPalClara Remote Sync ==="
echo "Local:  $LOCAL_PATH"
echo "Remote: $REMOTE_HOST:$REMOTE_PATH"
echo ""

# Check SSH connection
echo "Checking SSH connection..."
ssh -o ConnectTimeout=5 "$REMOTE_HOST" "echo 'Connected to $(hostname)'" || {
    echo "ERROR: Cannot connect to $REMOTE_HOST"
    exit 1
}

# Create remote directory
echo "Creating remote directory..."
ssh "$REMOTE_HOST" "mkdir -p $REMOTE_PATH"

# Sync data files
echo ""
echo "=== Syncing data files ==="

# SQLite database
if [ -f "$LOCAL_PATH/assistant.db" ]; then
    echo "Syncing assistant.db..."
    rsync -avz --progress "$LOCAL_PATH/assistant.db" "$REMOTE_HOST:$REMOTE_PATH/"
fi

# Qdrant vector data
if [ -d "$LOCAL_PATH/qdrant_data" ]; then
    echo "Syncing qdrant_data/..."
    rsync -avz --progress "$LOCAL_PATH/qdrant_data/" "$REMOTE_HOST:$REMOTE_PATH/qdrant_data/"
fi

# Clara local files
if [ -d "$LOCAL_PATH/clara_files" ]; then
    echo "Syncing clara_files/..."
    rsync -avz --progress "$LOCAL_PATH/clara_files/" "$REMOTE_HOST:$REMOTE_PATH/clara_files/"
fi

# Sync code (optional - uncomment if needed)
# echo ""
# echo "=== Syncing code ==="
# rsync -avz --progress \
#     --exclude '.git' \
#     --exclude '__pycache__' \
#     --exclude '.venv' \
#     --exclude 'node_modules' \
#     --exclude '.env' \
#     "$LOCAL_PATH/" "$REMOTE_HOST:$REMOTE_PATH/"

echo ""
echo "=== Sync complete ==="
echo ""
echo "Next steps on $REMOTE_HOST:"
echo "  cd $REMOTE_PATH"
echo "  poetry install"
echo "  # Copy .env.docker.example to .env and configure"
echo "  docker-compose --profile postgres up -d"
echo "  poetry run python scripts/migrate_to_postgres.py --all"
echo "  poetry run python discord_bot.py"
