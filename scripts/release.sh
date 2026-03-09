#!/bin/bash
#
# AI Token Analyzer - Release Script
# 
# This script creates a release package with version and date in the filename.
#
# Usage:
#   ./release.sh                    # Use version from VERSION file
#   ./release.sh --version 1.2.0    # Specify version
#   ./release.sh --help             # Show help
#

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
DIST_DIR="$PROJECT_DIR/dist"

# Default version from VERSION file
VERSION_FILE="$PROJECT_DIR/VERSION"
if [ -f "$VERSION_FILE" ]; then
    DEFAULT_VERSION=$(cat "$VERSION_FILE" | tr -d '[:space:]')
else
    DEFAULT_VERSION="1.0.0"
fi

VERSION="$DEFAULT_VERSION"

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --version|-v)
            VERSION="$2"
            shift 2
            ;;
        --help|-h)
            echo "AI Token Analyzer - Release Script"
            echo ""
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --version, -v VERSION   Specify version (default: from VERSION file)"
            echo "  --help, -h              Show this help message"
            echo ""
            echo "Output:"
            echo "  Creates: dist/ai-token-analyzer-v{VERSION}-{DATE}.tar.gz"
            echo ""
            exit 0
            ;;
        *)
            echo -e "${RED}Unknown option: $1${NC}"
            exit 1
            ;;
    esac
done

# Get current date
DATE=$(date +%Y%m%d)

# Package name
PACKAGE_NAME="ai-token-analyzer-v${VERSION}-${DATE}"
ARCHIVE_NAME="${PACKAGE_NAME}.tar.gz"

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  AI Token Analyzer - Release Builder${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
echo "Version: $VERSION"
echo "Date: $DATE"
echo "Project: $PROJECT_DIR"
echo ""

# Create dist directory
mkdir -p "$DIST_DIR"

# Files and directories to include
INCLUDE_ITEMS=(
    "cli.py"
    "web.py"
    "README.md"
    "requirements.txt"
    "VERSION"
    "config"
    "contrib"
    "cron"
    "scripts"
    "static"
    "templates"
    "web"
)

# Documentation files (optional)
DOC_ITEMS=(
    "FEISHU_GROUP_CONFIG.md"
    "FEISHU_USER_CONFIG.md"
    "REMOTE_DEPLOY.md"
)

# Files to exclude from scripts directory
SCRIPT_EXCLUDES=(
    "__pycache__"
    "*.pyc"
    "*.pyo"
    ".DS_Store"
    "release.sh"
    "manage.py"
    "clean_message_content.py"
    "migrate_messages.py"
    "restore_queued_messages.py"
)

# Files to exclude from static directory
STATIC_EXCLUDES=(
    "node_modules"
    "*.log"
)

echo -e "${YELLOW}Creating release package...${NC}"

# Create temporary directory
TEMP_DIR=$(mktemp -d)
PACKAGE_DIR="$TEMP_DIR/$PACKAGE_NAME"
mkdir -p "$PACKAGE_DIR"

# Copy included items
for item in "${INCLUDE_ITEMS[@]}"; do
    src="$PROJECT_DIR/$item"
    if [ -e "$src" ]; then
        if [ -d "$src" ]; then
            cp -r "$src" "$PACKAGE_DIR/"
        else
            cp "$src" "$PACKAGE_DIR/"
        fi
        echo "  ✓ Included: $item"
    else
        echo -e "  ${YELLOW}⚠ Not found: $item${NC}"
    fi
done

# Copy documentation files
for item in "${DOC_ITEMS[@]}"; do
    src="$PROJECT_DIR/$item"
    if [ -e "$src" ]; then
        cp "$src" "$PACKAGE_DIR/"
        echo "  ✓ Included: $item"
    fi
done

# Clean up unnecessary files in scripts directory
echo ""
echo -e "${YELLOW}Cleaning up scripts directory...${NC}"
cd "$PACKAGE_DIR/scripts"
for pattern in "${SCRIPT_EXCLUDES[@]}"; do
    find . -name "$pattern" -type f -delete 2>/dev/null || true
    find . -name "$pattern" -type d -exec rm -rf {} + 2>/dev/null || true
done
cd "$PROJECT_DIR"

# Clean up unnecessary files in static directory (node_modules, etc.)
echo -e "${YELLOW}Cleaning up static directory...${NC}"
cd "$PACKAGE_DIR/static"
for pattern in "${STATIC_EXCLUDES[@]}"; do
    find . -name "$pattern" -type d -exec rm -rf {} + 2>/dev/null || true
    find . -name "$pattern" -type f -delete 2>/dev/null || true
done
cd "$PROJECT_DIR"

# Create logs directory (empty)
mkdir -p "$PACKAGE_DIR/logs"
echo "  ✓ Created: logs/"

# Create the archive
echo ""
echo -e "${YELLOW}Creating archive...${NC}"
cd "$TEMP_DIR"
tar -czf "$DIST_DIR/$ARCHIVE_NAME" "$PACKAGE_NAME"
cd "$PROJECT_DIR"

# Clean up temp directory
rm -rf "$TEMP_DIR"

# Calculate checksum
CHECKSUM=$(shasum -a 256 "$DIST_DIR/$ARCHIVE_NAME" | cut -d' ' -f1)

echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  Release Package Created Successfully!${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
echo "Package: $DIST_DIR/$ARCHIVE_NAME"
echo "Size:    $(du -h "$DIST_DIR/$ARCHIVE_NAME" | cut -f1)"
echo "SHA256:  $CHECKSUM"
echo ""
echo "To install, run:"
echo "  tar -xzf $ARCHIVE_NAME"
echo "  cd $PACKAGE_NAME"
echo "  ./scripts/install.sh"
echo ""