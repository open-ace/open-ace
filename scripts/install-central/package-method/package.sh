#!/bin/bash
#
# Open ACE - AI Computing Explorer - Package Script
#
# This script creates a package with version and date in the filename.
#
# Usage:
#   ./package.sh                    # Use version from VERSION file
#   ./package.sh --version 1.2.0    # Specify version
#   ./package.sh --force-download   # Force re-download dependencies
#   ./package.sh --help             # Show help
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

# Force re-download dependencies
FORCE_DOWNLOAD=false

# Get version from git commit hash and date (same format as web UI)
# Format: commit_hash (MM-DD HH:MM:SS)
get_git_version() {
    local commit_hash commit_date
    commit_hash=$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")
    commit_date=$(git log -1 --format=%cd --date=format:%m-%d_%H-%M-%S 2>/dev/null || echo "unknown")
    echo "${commit_hash}-${commit_date}"
}

# Get version for VERSION file (same format as web.py displays)
# Format: commit_hash (MM-DD HH:MM:SS)
get_git_version_for_file() {
    local commit_hash commit_date
    commit_hash=$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")
    commit_date=$(git log -1 --format=%cd --date=format:%m-%d\ %H:%M:%S 2>/dev/null || echo "unknown")
    echo "${commit_hash} (${commit_date})"
}

# Default version from git
VERSION=$(get_git_version)

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --version|-v)
            VERSION="$2"
            shift 2
            ;;
        --force-download|-f)
            FORCE_DOWNLOAD=true
            shift
            ;;
        --help|-h)
            echo "Open ACE - Package Script"
            echo ""
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --version, -v VERSION   Specify version (default: from git commit)"
            echo "  --force-download, -f    Force re-download dependencies"
            echo "  --help, -h              Show this help message"
            echo ""
            echo "Output:"
            echo "  Creates: dist/open-ace-{VERSION}.tar.gz"
            echo ""
            exit 0
            ;;
        *)
            echo -e "${RED}Unknown option: $1${NC}"
            exit 1
            ;;
    esac
done

# Package name (version already includes date from git)
PACKAGE_NAME="open-ace-${VERSION}"
ARCHIVE_NAME="${PACKAGE_NAME}.tar.gz"

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  Open ACE - Package Builder${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
echo "Version: $VERSION"
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
    "install.conf.sample"
    "alembic.ini"
    "config"
    "contrib"
    "cron"
    "scripts"
    "static"
    "app"
    "migrations"
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
    "package.sh"
    "manage.py"
)

# Files to exclude from static directory
STATIC_EXCLUDES=(
    "node_modules"
    "*.log"
)

echo -e "${YELLOW}Creating package...${NC}"

# Create temporary directory
TEMP_DIR=$(mktemp -d)
PACKAGE_DIR="$TEMP_DIR/$PACKAGE_NAME"
mkdir -p "$PACKAGE_DIR"

# Download Python dependencies to vendor directory
VENDOR_DIR="$PACKAGE_DIR/vendor"
mkdir -p "$VENDOR_DIR"

# Check if we should skip download
SKIP_DOWNLOAD=false
CACHED_VENDOR_DIR="$DIST_DIR/.vendor_cache"
REQUIREMENTS_HASH=""
HASH_FILE="$CACHED_VENDOR_DIR/.requirements_hash"

# Calculate requirements.txt hash
if [ -f "$PROJECT_DIR/requirements.txt" ]; then
    REQUIREMENTS_HASH=$(shasum -a 256 "$PROJECT_DIR/requirements.txt" | cut -d' ' -f1)
fi

# Check if we can use cached vendor directory
if [ "$FORCE_DOWNLOAD" = false ] && [ -f "$PROJECT_DIR/requirements.txt" ]; then
    if [ -d "$CACHED_VENDOR_DIR" ] && [ "$(ls -A $CACHED_VENDOR_DIR/*.whl 2>/dev/null)" ]; then
        cached_pkg_count=$(ls -1 "$CACHED_VENDOR_DIR"/*.whl 2>/dev/null | wc -l | tr -d ' ')
        
        # Check if hash matches
        if [ -f "$HASH_FILE" ]; then
            OLD_HASH=$(cat "$HASH_FILE")
            if [ "$REQUIREMENTS_HASH" = "$OLD_HASH" ] && [ "$cached_pkg_count" -gt 0 ]; then
                echo -e "${GREEN}Using cached vendor directory ($cached_pkg_count packages, requirements unchanged)${NC}"
                cp -r "$CACHED_VENDOR_DIR"/* "$VENDOR_DIR/"
                SKIP_DOWNLOAD=true
            else
                echo -e "${YELLOW}Requirements changed or cache incomplete, re-downloading...${NC}"
            fi
        else
            # No hash file, but has packages - use them but warn
            if [ "$cached_pkg_count" -gt 10 ]; then
                echo -e "${GREEN}Using cached vendor directory ($cached_pkg_count packages)${NC}"
                cp -r "$CACHED_VENDOR_DIR"/* "$VENDOR_DIR/"
                SKIP_DOWNLOAD=true
            fi
        fi
    fi
fi

if [ "$SKIP_DOWNLOAD" = false ] && [ -f "$PROJECT_DIR/requirements.txt" ]; then
    echo -e "${YELLOW}Downloading Python dependencies...${NC}"
    
    # Download pure Python packages (platform independent)
    pip3 download -r "$PROJECT_DIR/requirements.txt" -d "$VENDOR_DIR" \
        --platform any --only-binary=:all: 2>/dev/null || true

    # Download for Linux aarch64
    pip3 download -r "$PROJECT_DIR/requirements.txt" -d "$VENDOR_DIR" \
        --platform manylinux2014_aarch64 --platform manylinux_2_17_aarch64 \
        --only-binary=:all: 2>/dev/null || true

    # Download for Linux x86_64
    pip3 download -r "$PROJECT_DIR/requirements.txt" -d "$VENDOR_DIR" \
        --platform manylinux2014_x86_64 --platform manylinux_2_17_x86_64 \
        --only-binary=:all: 2>/dev/null || true

    # Download for macOS arm64 (Apple Silicon)
    pip3 download -r "$PROJECT_DIR/requirements.txt" -d "$VENDOR_DIR" \
        --platform macosx_11_0_arm64 \
        --only-binary=:all: 2>/dev/null || true

    # Download for macOS x86_64 (Intel)
    pip3 download -r "$PROJECT_DIR/requirements.txt" -d "$VENDOR_DIR" \
        --platform macosx_10_9_x86_64 \
        --only-binary=:all: 2>/dev/null || true

    # Fallback: download any missing packages
    pip3 download -r "$PROJECT_DIR/requirements.txt" -d "$VENDOR_DIR" --prefer-binary 2>/dev/null || \
        pip download -r "$PROJECT_DIR/requirements.txt" -d "$VENDOR_DIR" --prefer-binary 2>/dev/null || \
        echo -e "${YELLOW}Warning: Failed to download some dependencies. Install will require network.${NC}"

    # Count downloaded packages
    pkg_count=$(ls -1 "$VENDOR_DIR"/*.whl 2>/dev/null | wc -l | tr -d ' ')
    echo "  ✓ Downloaded $pkg_count packages to vendor/ (multi-platform)"
    
    # Cache the vendor directory for future use
    echo -e "${YELLOW}Caching vendor directory for future packages...${NC}"
    mkdir -p "$CACHED_VENDOR_DIR"
    cp -r "$VENDOR_DIR"/* "$CACHED_VENDOR_DIR/"
    
    # Save requirements hash
    echo "$REQUIREMENTS_HASH" > "$HASH_FILE"
elif [ ! -f "$PROJECT_DIR/requirements.txt" ]; then
    echo -e "${YELLOW}Warning: requirements.txt not found${NC}"
fi

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

# Write VERSION file with git commit info (for deployed environments without .git)
VERSION_FILE_CONTENT=$(get_git_version_for_file)
echo "$VERSION_FILE_CONTENT" > "$PACKAGE_DIR/VERSION"
echo "  ✓ Updated: VERSION ($VERSION_FILE_CONTENT)"

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

# Remove macOS extended attributes and AppleDouble files
echo -e "${YELLOW}Removing macOS metadata files...${NC}"
find "$PACKAGE_DIR" -name "._*" -type f -delete 2>/dev/null || true
# Remove extended attributes recursively (macOS only)
if command -v xattr &>/dev/null; then
    xattr -cr "$PACKAGE_DIR" 2>/dev/null || true
fi

# Create the archive
echo ""
echo -e "${YELLOW}Creating archive...${NC}"
cd "$TEMP_DIR"
# Use COPYFILE_DISABLE=1 and --no-xattrs to prevent macOS extended attributes
COPYFILE_DISABLE=1 tar --no-xattrs -czf "$DIST_DIR/$ARCHIVE_NAME" "$PACKAGE_NAME" 2>/dev/null || \
    COPYFILE_DISABLE=1 tar -czf "$DIST_DIR/$ARCHIVE_NAME" "$PACKAGE_NAME"
cd "$PROJECT_DIR"

# Clean up temp directory
rm -rf "$TEMP_DIR"

# Calculate checksum
CHECKSUM=$(shasum -a 256 "$DIST_DIR/$ARCHIVE_NAME" | cut -d' ' -f1)

echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  Package Created Successfully!${NC}"
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