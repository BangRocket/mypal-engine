#!/bin/bash
# Migrate Clara sandbox containers to a remote server
# Usage: ./scripts/migrate_sandboxes.sh [remote_host]

set -e

REMOTE_HOST="${1:-clara}"
EXPORT_DIR="/tmp/clara-sandboxes"

echo "=== Clara Sandbox Migration ==="

# Find all clara sandbox containers
CONTAINERS=$(docker ps -a --filter "name=clara-sandbox" --format "{{.Names}}")

if [ -z "$CONTAINERS" ]; then
    echo "No clara-sandbox containers found."
    exit 0
fi

echo "Found containers:"
echo "$CONTAINERS"
echo ""

# Create export directory
mkdir -p "$EXPORT_DIR"

# Commit and save each container
for CONTAINER in $CONTAINERS; do
    echo "=== Processing: $CONTAINER ==="

    # Commit container to image
    IMAGE_NAME="clara-sandbox-export:${CONTAINER#clara-sandbox-}"
    echo "Committing to image: $IMAGE_NAME"
    docker commit "$CONTAINER" "$IMAGE_NAME"

    # Save image to tar
    TAR_FILE="$EXPORT_DIR/${CONTAINER}.tar"
    echo "Saving to: $TAR_FILE"
    docker save "$IMAGE_NAME" -o "$TAR_FILE"

    echo "Done: $(du -h "$TAR_FILE" | cut -f1)"
    echo ""
done

echo "=== All containers exported to $EXPORT_DIR ==="
ls -lh "$EXPORT_DIR"

echo ""
echo "=== Transferring to $REMOTE_HOST ==="
scp "$EXPORT_DIR"/*.tar "$REMOTE_HOST:/tmp/"

echo ""
echo "=== Loading on remote server ==="
for CONTAINER in $CONTAINERS; do
    TAR_FILE="/tmp/${CONTAINER}.tar"
    IMAGE_NAME="clara-sandbox-export:${CONTAINER#clara-sandbox-}"

    echo "Loading $CONTAINER..."
    ssh "$REMOTE_HOST" "docker load -i $TAR_FILE"

    # Recreate container from image
    echo "Creating container $CONTAINER..."
    ssh "$REMOTE_HOST" "docker run -d --name $CONTAINER $IMAGE_NAME tail -f /dev/null"
done

echo ""
echo "=== Migration complete ==="
echo "Containers on $REMOTE_HOST:"
ssh "$REMOTE_HOST" "docker ps -a --filter 'name=clara-sandbox' --format 'table {{.Names}}\t{{.Status}}'"

# Cleanup
echo ""
read -p "Clean up local export files? [y/N] " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    rm -rf "$EXPORT_DIR"
    echo "Cleaned up $EXPORT_DIR"
fi
