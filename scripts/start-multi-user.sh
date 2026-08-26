#!/bin/bash
# Open ACE - Multi-User Mode One-Click Startup Script
# Issue #2242: Simplifies multi-user mode deployment
#
# Usage:
#   ./scripts/start-multi-user.sh [docker compose arguments]
#
# Examples:
#   ./scripts/start-multi-user.sh                    # Start with defaults
#   ./scripts/start-multi-user.sh --build            # Rebuild and start
#   ./scripts/start-multi-user.sh up -d --scale open-ace=2  # Scale service
#
# This script:
#   1. Detects Docker Compose version (v1/v2)
#   2. Validates overlay file existence and compatibility
#   3. Starts containers with multi-user configuration
#   4. Outputs access URL and status

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Configuration files
BASE_COMPOSE="$PROJECT_ROOT/docker-compose.yml"
OVERLAY_COMPOSE="$PROJECT_ROOT/docker-compose.multi-user.yml"

echo -e "${BLUE}=========================================="
echo -e "  Open ACE - Multi-User Mode Startup"
echo -e "==========================================${NC}"
echo ""

# ============================================================================
# Step 1: Detect Docker Compose Command
# ============================================================================
detect_compose_cmd() {
    if command -v docker &> /dev/null && docker compose version &> /dev/null 2>&1; then
        echo "docker compose"
        return 0
    elif command -v docker-compose &> /dev/null; then
        echo "docker-compose"
        return 0
    else
        echo ""
        return 1
    fi
}

echo -e "${BLUE}[1/5] Detecting Docker Compose...${NC}"
COMPOSE_CMD=$(detect_compose_cmd)

if [ -z "$COMPOSE_CMD" ]; then
    echo -e "${RED}ERROR: Docker Compose not found!${NC}"
    echo ""
    echo "Please install Docker Compose:"
    echo "  - Docker Compose v2 (recommended): https://docs.docker.com/compose/install/"
    echo "  - Docker Compose v1: pip install docker-compose"
    exit 1
fi

COMPOSE_VERSION=$($COMPOSE_CMD version 2>&1 | head -n1)
echo -e "${GREEN}✓ Docker Compose detected: $COMPOSE_VERSION${NC}"
echo "  Command: $COMPOSE_CMD"
echo ""

# ============================================================================
# Step 2: Validate Configuration Files
# ============================================================================
echo -e "${BLUE}[2/5] Validating configuration files...${NC}"

# Check base compose file
if [ ! -f "$BASE_COMPOSE" ]; then
    echo -e "${RED}ERROR: Base compose file not found: $BASE_COMPOSE${NC}"
    exit 1
fi
echo -e "${GREEN}✓ Found: docker-compose.yml${NC}"

# Check overlay compose file
if [ ! -f "$OVERLAY_COMPOSE" ]; then
    echo -e "${RED}ERROR: Multi-user overlay file not found: $OVERLAY_COMPOSE${NC}"
    echo ""
    echo "The multi-user configuration file is missing. Please ensure you have:"
    echo "  1. Cloned the repository correctly, OR"
    echo "  2. Downloaded the docker-compose.multi-user.yml file from:"
    echo "     https://github.com/open-ace/open-ace/blob/main/docker-compose.multi-user.yml"
    exit 1
fi
echo -e "${GREEN}✓ Found: docker-compose.multi-user.yml${NC}"

# ============================================================================
# Step 3: Validate Overlay File Compatibility
# ============================================================================
echo -e "${BLUE}[3/5] Checking overlay compatibility...${NC}"

# Extract version from both files (for warning purposes)
BASE_VERSION=$(grep -E "^version:" "$BASE_COMPOSE" | head -n1 | sed 's/version: *//' | tr -d '"'"'"')
OVERLAY_VERSION=$(grep -E "^version:" "$OVERLAY_COMPOSE" | head -n1 | sed 's/version: *//' | tr -d '"'"'"')

if [ -n "$BASE_VERSION" ] && [ -n "$OVERLAY_VERSION" ] && [ "$BASE_VERSION" != "$OVERLAY_VERSION" ]; then
    echo -e "${YELLOW}⚠ WARNING: Compose file versions differ${NC}"
    echo "  docker-compose.yml: version $BASE_VERSION"
    echo "  docker-compose.multi-user.yml: version $OVERLAY_VERSION"
    echo ""
    echo "This may cause unexpected behavior. Consider updating the overlay file."
    echo "Continuing in 5 seconds... (Ctrl+C to abort)"
    sleep 5
else
    echo -e "${GREEN}✓ Compose file versions compatible${NC}"
fi

# ============================================================================
# Step 4: Environment Bootstrap / Check
# ============================================================================
echo -e "${BLUE}[4/5] Checking environment configuration...${NC}"

# Only commands that start services may create/update .env. Read-only and
# lifecycle commands such as config/logs/down never mutate configuration.
START_REQUEST=false
BOOTSTRAP_PROJECT_NAME=""
if [ $# -eq 0 ] || [ "${1:-}" = "--build" ]; then
    START_REQUEST=true
fi
args=("$@")
for ((i=0; i<${#args[@]}; i++)); do
    case "${args[$i]}" in
        up) START_REQUEST=true; break ;;
        -p|--project-name)
            if (( i + 1 < ${#args[@]} )); then
                BOOTSTRAP_PROJECT_NAME="${args[$((i + 1))]}"
                i=$((i + 1))
            fi
            ;;
        --project-name=*) BOOTSTRAP_PROJECT_NAME="${args[$i]#*=}" ;;
    esac
done
if [ "$START_REQUEST" = true ]; then
    if [ -n "$BOOTSTRAP_PROJECT_NAME" ]; then
        COMPOSE_PROJECT_NAME="$BOOTSTRAP_PROJECT_NAME" "$PROJECT_ROOT/scripts/bootstrap-compose-env.sh"
    else
        "$PROJECT_ROOT/scripts/bootstrap-compose-env.sh"
    fi
fi

# Multi-user mode is configured in the overlay file
# Check if user is trying to override
if [ -n "$WORKSPACE_MULTI_USER_MODE" ] && [ "$WORKSPACE_MULTI_USER_MODE" != "true" ]; then
    echo -e "${YELLOW}⚠ WARNING: WORKSPACE_MULTI_USER_MODE is set to '$WORKSPACE_MULTI_USER_MODE'${NC}"
    echo "  The overlay file sets WORKSPACE_MULTI_USER_MODE=true"
    echo "  Your environment variable will be overridden by the overlay configuration."
fi

echo ""
echo -e "${BLUE}Multi-user mode configuration:${NC}"
echo "  • Container user: root (user: \"0\")"
echo "  • WORKSPACE_MULTI_USER_MODE: true (from overlay)"
echo "  • OPENACE_ALLOW_ROOT_MULTI_USER: 1 (from overlay)"
echo "  • OPENACE_CONFIG_DIR: /home/open-ace/.open-ace (from overlay)"
echo ""

# ============================================================================
# Step 5: Start Services
# ============================================================================
echo -e "${BLUE}[5/5] Starting multi-user mode services...${NC}"
echo ""

# Change to project root for compose
cd "$PROJECT_ROOT"

# Build the command
COMPOSE_ARGS=("-f" "docker-compose.yml" "-f" "docker-compose.multi-user.yml")

# Add any user-provided arguments
if [ $# -eq 0 ]; then
    COMPOSE_ARGS+=("up" "-d")
    if $COMPOSE_CMD up --help 2>&1 | grep -q -- '--wait'; then
        COMPOSE_ARGS+=("--wait")
    fi
elif [ "$1" = "--build" ]; then
    COMPOSE_ARGS+=("up" "-d" "--build")
    shift
    COMPOSE_ARGS+=("$@")
    if $COMPOSE_CMD up --help 2>&1 | grep -q -- '--wait'; then
        COMPOSE_ARGS+=("--wait")
    fi
else
    COMPOSE_ARGS+=("$@")
fi

# Execute
echo "Running: $COMPOSE_CMD ${COMPOSE_ARGS[*]}"
echo ""

if $COMPOSE_CMD "${COMPOSE_ARGS[@]}"; then
    echo ""
    echo -e "${GREEN}=========================================="
    echo -e "  ✓ Multi-User Compose Command Completed"
    echo -e "==========================================${NC}"
    echo ""
    if [[ " ${COMPOSE_ARGS[*]} " != *" --wait "* ]]; then
        echo -e "${YELLOW}Health was not awaited by this Compose version; verify with compose ps.${NC}"
        echo ""
    fi
    echo -e "${BLUE}Access Open ACE:${NC}"
    echo "  URL: http://localhost:${PORT:-19888}"
    echo ""
    echo -e "${BLUE}Default credentials:${NC}"
    echo "  Username: admin"
    echo "  Password: admin123"
    echo -e "${YELLOW}  ⚠ Please change the default password after first login!${NC}"
    echo ""
    echo -e "${BLUE}Useful commands:${NC}"
    echo "  View logs:    $COMPOSE_CMD -f docker-compose.yml -f docker-compose.multi-user.yml logs -f"
    echo "  Stop:         $COMPOSE_CMD -f docker-compose.yml -f docker-compose.multi-user.yml down"
    echo "  Restart:      $COMPOSE_CMD -f docker-compose.yml -f docker-compose.multi-user.yml restart"
    echo ""
    echo -e "${BLUE}Documentation:${NC}"
    echo "  Multi-user deployment: docs/cn/DEPLOYMENT.md#多用户工作区部署"
    echo "  Troubleshooting:       docs/cn/DEPLOYMENT.md#故障排查"
    echo ""
else
    echo ""
    echo -e "${RED}=========================================="
    echo -e "  ✗ Failed to start services"
    echo -e "==========================================${NC}"
    echo ""
    echo "Please check the error messages above and try:"
    echo "  1. Ensure Docker is running: docker info"
    echo "  2. Check for port conflicts: netstat -tlnp | grep 19888"
    echo "  3. View detailed logs: $COMPOSE_CMD -f docker-compose.yml -f docker-compose.multi-user.yml logs"
    echo "  4. Read troubleshooting guide: docs/cn/DEPLOYMENT.md#故障排查"
    exit 1
fi
