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
#   ./package.sh --generate-schema  # Generate schema.sql from pg_dump
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
PROJECT_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"
DIST_DIR="$PROJECT_DIR/dist"

# Check and install required dependencies
check_and_install_deps() {
    local missing_deps=()
    local has_pip=false

    # Check for pip (pip3 or pip)
    if command -v pip3 &>/dev/null; then
        has_pip=true
    elif command -v pip &>/dev/null; then
        has_pip=true
    fi

    if [ "$has_pip" = false ]; then
        missing_deps+=("pip")
    fi

    # Check for tar
    if ! command -v tar &>/dev/null; then
        missing_deps+=("tar")
    fi

    # If no missing dependencies, return
    if [ ${#missing_deps[@]} -eq 0 ]; then
        return 0
    fi

    echo -e "${YELLOW}Missing required dependencies: ${missing_deps[*]}${NC}"
    echo -e "${YELLOW}Attempting to install missing dependencies...${NC}"

    # Detect OS and install accordingly
    if [[ "$OSTYPE" == "darwin"* ]]; then
        # macOS - use Homebrew
        if ! command -v brew &>/dev/null; then
            echo -e "${RED}Homebrew not found. Please install Homebrew first:${NC}"
            echo "  /bin/bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\""
            exit 1
        fi

        for dep in "${missing_deps[@]}"; do
            case $dep in
                pip)
                    echo -e "${YELLOW}Installing Python (includes pip)...${NC}"
                    brew install python
                    ;;
                tar)
                    echo -e "${YELLOW}Installing gnu-tar...${NC}"
                    brew install gnu-tar
                    # Add gnu-tar to PATH if needed
                    if ! command -v tar &>/dev/null; then
                        export PATH="/opt/homebrew/opt/gnu-tar/libexec/gnubin:/usr/local/opt/gnu-tar/libexec/gnubin:$PATH"
                    fi
                    ;;
            esac
        done
    elif [[ "$OSTYPE" == "linux-gnu"* ]]; then
        # Linux - use apt, yum, or dnf
        if command -v apt-get &>/dev/null; then
            echo -e "${YELLOW}Detected apt-based system${NC}"
            sudo apt-get update -qq
            for dep in "${missing_deps[@]}"; do
                case $dep in
                    pip)
                        echo -e "${YELLOW}Installing python3-pip...${NC}"
                        sudo apt-get install -y python3-pip python3-venv
                        ;;
                    tar)
                        echo -e "${YELLOW}Installing tar...${NC}"
                        sudo apt-get install -y tar
                        ;;
                esac
            done
        elif command -v yum &>/dev/null; then
            echo -e "${YELLOW}Detected yum-based system${NC}"
            for dep in "${missing_deps[@]}"; do
                case $dep in
                    pip)
                        echo -e "${YELLOW}Installing python3-pip...${NC}"
                        sudo yum install -y python3-pip
                        ;;
                    tar)
                        echo -e "${YELLOW}Installing tar...${NC}"
                        sudo yum install -y tar
                        ;;
                esac
            done
        elif command -v dnf &>/dev/null; then
            echo -e "${YELLOW}Detected dnf-based system${NC}"
            for dep in "${missing_deps[@]}"; do
                case $dep in
                    pip)
                        echo -e "${YELLOW}Installing python3-pip...${NC}"
                        sudo dnf install -y python3-pip
                        ;;
                    tar)
                        echo -e "${YELLOW}Installing tar...${NC}"
                        sudo dnf install -y tar
                        ;;
                esac
            done
        else
            echo -e "${RED}Unsupported Linux distribution. Please install manually:${NC}"
            echo "  pip:  pip3 or pip command"
            echo "  tar:  tar command"
            exit 1
        fi
    else
        echo -e "${RED}Unsupported OS: $OSTYPE${NC}"
        echo "Please install manually:"
        echo "  pip:  pip3 or pip command"
        echo "  tar:  tar command"
        exit 1
    fi

    # Verify installation
    local still_missing=()
    if ! command -v pip3 &>/dev/null && ! command -v pip &>/dev/null; then
        still_missing+=("pip")
    fi
    if ! command -v tar &>/dev/null; then
        still_missing+=("tar")
    fi

    if [ ${#still_missing[@]} -gt 0 ]; then
        echo -e "${RED}Failed to install: ${still_missing[*]}${NC}"
        exit 1
    fi

    echo -e "${GREEN}All dependencies installed successfully!${NC}"
}

# Run dependency check
check_and_install_deps

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
GENERATE_SCHEMA=false
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
        --generate-schema|-g)
            GENERATE_SCHEMA=true
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
            echo "  --generate-schema, -g   Generate schema.sql from pg_dump (requires PostgreSQL)"
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

# Files and directories to include (VERSION is created dynamically during packaging)
INCLUDE_ITEMS=(
    "cli.py"
    "web.py"
    "README.md"
    "CHANGELOG.md"
    "requirements.txt"
    "alembic.ini"
    "docker-compose.yml"
    "Dockerfile"
    "docker-entrypoint.sh"
    "config"
    "contrib"
    "cron"
    "scripts"
    "static"
    "app"
    "migrations"
    "docs"
    "schema"
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

# ============================================
# Generate database schema
# ============================================
if [ "$GENERATE_SCHEMA" = true ]; then
    echo -e "${YELLOW}Generating database schema...${NC}"
    if [ -f "$PROJECT_DIR/scripts/generate_schema.py" ]; then
        cd "$PROJECT_DIR"
        python3 scripts/generate_schema.py
        if [ -f "$PROJECT_DIR/schema/schema-postgres.sql" ] && [ -f "$PROJECT_DIR/schema/schema-sqlite.sql" ]; then
            echo -e "${GREEN}Database schema generated successfully${NC}"
        else
            echo -e "${YELLOW}Warning: Schema generation may have failed. Check schema directory.${NC}"
        fi
    else
        echo -e "${YELLOW}Warning: generate_schema.py not found, skipping schema generation${NC}"
    fi
else
    echo -e "${YELLOW}Skipping schema generation (use --generate-schema to enable)${NC}"
fi

# ============================================
# Build frontend (React app)
# ============================================
echo -e "${YELLOW}Building frontend...${NC}"
FRONTEND_DIR="$PROJECT_DIR/frontend"
if [ -d "$FRONTEND_DIR" ]; then
    # Check if npm is available
    if command -v npm &> /dev/null; then
        # Check Node.js version (Vite 6.x requires Node.js 18+)
        NODE_VERSION=$(node --version 2>/dev/null | sed 's/v//')
        if [ -n "$NODE_VERSION" ]; then
            NODE_MAJOR=$(echo "$NODE_VERSION" | cut -d. -f1)
            if [ "$NODE_MAJOR" -lt 18 ]; then
                echo -e "${RED}Error: Node.js version $NODE_VERSION is too old${NC}"
                echo -e "${RED}Vite 6.x requires Node.js 18 or higher${NC}"
                echo -e "${YELLOW}Please upgrade Node.js:${NC}"
                echo ""
                echo -e "${YELLOW}For Rocky Linux/CentOS/RHEL (if yum install fails due to conflicts):${NC}"
                echo -e "${YELLOW}  sudo yum remove -y nodejs npm${NC}"
                echo -e "${YELLOW}  curl -fsSL https://rpm.nodesource.com/setup_20.x | sudo bash -${NC}"
                echo -e "${YELLOW}  sudo yum install -y nodejs${NC}"
                echo ""
                echo -e "${YELLOW}Or use nvm (recommended for multiple Node.js versions):${NC}"
                echo -e "${YELLOW}  curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.7/install.sh | bash${NC}"
                echo -e "${YELLOW}  source ~/.bashrc${NC}"
                echo -e "${YELLOW}  nvm install 20${NC}"
                echo -e "${YELLOW}  nvm use 20${NC}"
                exit 1
            fi
            echo -e "${GREEN}Node.js version: v$NODE_VERSION (OK, requires 18+)${NC}"
        fi

        cd "$FRONTEND_DIR"

        # Install dependencies if node_modules doesn't exist or package.json changed
        if [ ! -d "node_modules" ] || [ "$(find package.json -newer node_modules 2>/dev/null | head -1)" ]; then
            echo -e "${BLUE}Installing frontend dependencies...${NC}"
            npm install --silent 2>/dev/null || npm install
        fi

        # Build frontend
        echo -e "${BLUE}Building frontend with Vite...${NC}"
        npm run build 2>/dev/null || npm run build

        if [ -d "$PROJECT_DIR/static/js/dist" ]; then
            echo -e "${GREEN}Frontend built successfully${NC}"
            echo -e "${BLUE}Output: static/js/dist/${NC}"
        else
            echo -e "${YELLOW}Warning: Frontend build output not found at static/js/dist${NC}"
        fi

        cd "$PROJECT_DIR"
    else
        echo -e "${YELLOW}Warning: npm not found, skipping frontend build${NC}"
        echo -e "${YELLOW}The package will not include built frontend. Install npm and run 'npm run build' manually.${NC}"
        echo -e "${YELLOW}Or install frontend on the target server after installation.${NC}"
    fi
else
    echo -e "${YELLOW}Warning: Frontend directory not found at $FRONTEND_DIR${NC}"
fi

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
    REQUIREMENTS_HASH=$(sha256sum "$PROJECT_DIR/requirements.txt" 2>/dev/null | cut -d' ' -f1 || shasum -a 256 "$PROJECT_DIR/requirements.txt" | cut -d' ' -f1)
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

    # Create a temporary requirements file excluding psycopg2-binary
    # psycopg2-binary causes segfault on some Linux systems (see Issue #38)
    # Users should install system package python3-psycopg2 instead
    TEMP_REQ=$(mktemp)
    grep -v "psycopg2-binary" "$PROJECT_DIR/requirements.txt" > "$TEMP_REQ" || true

    # Download dependencies with prefer-binary for reliable installation
    pip3 download -r "$TEMP_REQ" -d "$VENDOR_DIR" --prefer-binary || \
        pip download -r "$TEMP_REQ" -d "$VENDOR_DIR" --prefer-binary || \
        echo -e "${YELLOW}Warning: Failed to download some dependencies. Install will require network.${NC}"

    rm -f "$TEMP_REQ"

    # Count downloaded packages
    pkg_count=$(ls -1 "$VENDOR_DIR"/*.whl 2>/dev/null | wc -l | tr -d ' ')
    echo "  ✓ Downloaded $pkg_count packages to vendor/"
    echo -e "${YELLOW}Note: psycopg2-binary excluded. Install will use system package python3-psycopg2.${NC}"

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
CHECKSUM=$(sha256sum "$DIST_DIR/$ARCHIVE_NAME" 2>/dev/null | cut -d' ' -f1 || shasum -a 256 "$DIST_DIR/$ARCHIVE_NAME" | cut -d' ' -f1)

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
echo "  ./scripts/install-central/package-method/install.sh --config <config-file>"
echo ""