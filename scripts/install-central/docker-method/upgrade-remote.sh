#!/bin/bash
#
# Open ACE - Remote Upgrade Script
#
# Usage: ./scripts/upgrade-remote.sh [user@]host [deploy_dir]
#
# Examples:
#   ./scripts/upgrade-remote.sh open-ace@192.168.31.159
#   ./scripts/upgrade-remote.sh open-ace@192.168.31.159 /home/open-ace/open-ace
#

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Default values
IMAGE_NAME="open-ace:latest"
IMAGE_FILE="open-ace-images.tar.gz"
LOCAL_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DEFAULT_REMOTE="open-ace@192.168.31.159"
DEFAULT_DEPLOY_DIR="/home/open-ace/open-ace"

# Parse arguments
REMOTE_HOST="${1:-$DEFAULT_REMOTE}"
DEPLOY_DIR="${2:-$DEFAULT_DEPLOY_DIR}"

# Show usage if -h or --help
if [ "$1" = "-h" ] || [ "$1" = "--help" ]; then
    echo "Usage: $0 [user@]host [deploy_dir]"
    echo ""
    echo "Defaults:"
    echo "  Host: $DEFAULT_REMOTE"
    echo "  Dir:  $DEFAULT_DEPLOY_DIR"
    echo ""
    echo "Examples:"
    echo "  $0                              # Use defaults"
    echo "  $0 open-ace@192.168.31.159      # Custom host"
    echo "  $0 open-ace@192.168.31.159 /opt/open-ace  # Custom host and dir"
    exit 0
fi

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}Open ACE Remote Upgrade${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
echo -e "${BLUE}Remote Host:${NC} $REMOTE_HOST"
echo -e "${BLUE}Deploy Dir:${NC} $DEPLOY_DIR"
echo ""

# Step 1: Build Docker image locally
echo -e "${YELLOW}[Step 1/5] Building Docker image locally...${NC}"
cd "$LOCAL_DIR"
docker build -t "$IMAGE_NAME" .
echo -e "${GREEN}✓ Docker image built: $IMAGE_NAME${NC}"
echo ""

# Step 2: Export Docker image
echo -e "${YELLOW}[Step 2/5] Exporting Docker image...${NC}"
docker save "$IMAGE_NAME" | gzip > "$IMAGE_FILE"
echo -e "${GREEN}✓ Image exported: $IMAGE_FILE ($(du -h "$IMAGE_FILE" | cut -f1))${NC}"
echo ""

# Step 3: Copy image to remote server
echo -e "${YELLOW}[Step 3/5] Copying image to remote server...${NC}"
scp "$IMAGE_FILE" "${REMOTE_HOST}:${DEPLOY_DIR}/"
echo -e "${GREEN}✓ Image copied to ${REMOTE_HOST}:${DEPLOY_DIR}/${NC}"
echo ""

# Step 4: Load image and restart services on remote server
echo -e "${YELLOW}[Step 4/5] Loading image and restarting services...${NC}"
ssh "$REMOTE_HOST" bash << EOF
set -e
DEPLOY_DIR="$DEPLOY_DIR"
IMAGE_FILE="$IMAGE_FILE"

echo "Loading Docker image..."
cd "\$DEPLOY_DIR"
docker load < "\$IMAGE_FILE"

echo "Restarting services..."
docker compose down
docker compose up -d

echo "Cleaning up image file..."
rm -f "\$IMAGE_FILE"

echo "Services restarted successfully!"
EOF
echo -e "${GREEN}✓ Remote services restarted${NC}"
echo ""

# Step 5: Cleanup local image file
echo -e "${YELLOW}[Step 5/5] Cleaning up local image file...${NC}"
rm -f "$LOCAL_DIR/$IMAGE_FILE"
echo -e "${GREEN}✓ Cleanup complete${NC}"
echo ""

# Done
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}✓ Upgrade Complete!${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
echo -e "Access the application at: ${BLUE}http://${REMOTE_HOST#*@}:5000/${NC}"
echo ""