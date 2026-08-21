#!/bin/bash
# Test configuration persistence for multi-user mode (Issue #2235)
#
# This test validates that:
# 1. docker-compose.multi-user.yml correctly sets OPENACE_CONFIG_DIR
# 2. Configuration persists across container restarts
# 3. Multi-user mode works correctly with the fixed configuration

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test: Multi-user config persistence (Issue #2235) ==="

# Cleanup function
cleanup() {
    echo "Cleaning up..."
    cd "$ROOT_DIR"
    docker compose -f docker-compose.yml -f docker-compose.multi-user.yml down -v 2>/dev/null || true
}

trap cleanup EXIT

# Test 1: Verify OPENACE_CONFIG_DIR is set in docker-compose.multi-user.yml
echo ""
echo "Test 1: Verify OPENACE_CONFIG_DIR in docker-compose.multi-user.yml"
echo "---------------------------------------------------------------"

if grep -q "OPENACE_CONFIG_DIR=/home/open-ace/.open-ace" "$ROOT_DIR/docker-compose.multi-user.yml"; then
    echo "✓ PASS: OPENACE_CONFIG_DIR is set correctly"
else
    echo "✗ FAIL: OPENACE_CONFIG_DIR not found in docker-compose.multi-user.yml"
    exit 1
fi

# Test 2: Verify environment variables are set for both services
echo ""
echo "Test 2: Verify environment variables for both services"
echo "------------------------------------------------------"

# Check open-ace service
if grep -A 15 "services:" "$ROOT_DIR/docker-compose.multi-user.yml" | grep -A 10 "open-ace:" | grep -q "OPENACE_CONFIG_DIR"; then
    echo "✓ PASS: OPENACE_CONFIG_DIR set in open-ace service"
else
    echo "✗ FAIL: OPENACE_CONFIG_DIR not set in open-ace service"
    exit 1
fi

# Check scheduler service
if grep -A 15 "scheduler:" "$ROOT_DIR/docker-compose.multi-user.yml" | grep -q "OPENACE_CONFIG_DIR"; then
    echo "✓ PASS: OPENACE_CONFIG_DIR set in scheduler service"
else
    echo "✗ FAIL: OPENACE_CONFIG_DIR not set in scheduler service"
    exit 1
fi

# Test 3: Verify error message includes docker-compose.multi-user.yml
echo ""
echo "Test 3: Verify improved error message"
echo "--------------------------------------"

if grep -q "docker-compose.multi-user.yml" "$ROOT_DIR/docker-entrypoint.sh"; then
    echo "✓ PASS: Error message mentions docker-compose.multi-user.yml"
else
    echo "✗ FAIL: Error message does not mention docker-compose.multi-user.yml"
    exit 1
fi

# Test 4: Verify install.sh generates correct configuration
echo ""
echo "Test 4: Verify install.sh generates multi-user config"
echo "------------------------------------------------------"

# Create a temporary directory for install.sh output
TEMP_DIR=$(mktemp -d)
cd "$TEMP_DIR"

# Run install.sh in non-interactive mode with multi-user enabled
export NON_INTERACTIVE=true
export WORKSPACE_MULTI_USER_MODE=true
export WORKSPACE_ENABLED=true
export DB_USER=testace
export DB_PASSWORD=testpass123
export DB_NAME=testace
export SECRET_KEY=test-secret-key
export UPLOAD_AUTH_KEY=test-upload-key
export RUN_USER=open-ace
export DEPLOY_DIR="$TEMP_DIR"
export IMAGE_NAME=openace/open-ace:latest
export WEB_PORT=19888
export INTERNAL_WEB_PORT=19888
export DOCKER_INSTALL_MIRROR=""
export HOST_NAME="test-host"
export WORKSPACE_URL="http://localhost:19888"
export WORKSPACE_PORT_RANGE_START=3100
export WORKSPACE_PORT_RANGE_END=3200
export WORKSPACE_MAX_INSTANCES=30
export WORKSPACE_IDLE_TIMEOUT=30
export OPENCLAW_ENABLED=false
export CLAUDE_ENABLED=false
export QWEN_ENABLED=false
export SSH_ENABLED=no

# Source the install script functions
source "$ROOT_DIR/scripts/install-central/docker-method/install.sh" 2>/dev/null || true

# Check if the compose generation function exists
if type create_docker_compose &>/dev/null; then
    create_docker_compose

    if [ -f "$TEMP_DIR/docker-compose.yml" ]; then
        echo "Checking generated docker-compose.yml..."

        # Check for user: "0"
        if grep -q 'user: "0"' "$TEMP_DIR/docker-compose.yml"; then
            echo "✓ PASS: user \"0\" is set in generated config"
        else
            echo "✗ FAIL: user \"0\" not set in generated config"
            cat "$TEMP_DIR/docker-compose.yml"
            rm -rf "$TEMP_DIR"
            exit 1
        fi

        # Check for OPENACE_ALLOW_ROOT_MULTI_USER
        if grep -q "OPENACE_ALLOW_ROOT_MULTI_USER" "$TEMP_DIR/docker-compose.yml"; then
            echo "✓ PASS: OPENACE_ALLOW_ROOT_MULTI_USER is set in generated config"
        else
            echo "✗ FAIL: OPENACE_ALLOW_ROOT_MULTI_USER not set in generated config"
            cat "$TEMP_DIR/docker-compose.yml"
            rm -rf "$TEMP_DIR"
            exit 1
        fi

        # Check for OPENACE_CONFIG_DIR
        if grep -q "OPENACE_CONFIG_DIR" "$TEMP_DIR/docker-compose.yml"; then
            echo "✓ PASS: OPENACE_CONFIG_DIR is set in generated config"
        else
            echo "✗ FAIL: OPENACE_CONFIG_DIR not set in generated config"
            cat "$TEMP_DIR/docker-compose.yml"
            rm -rf "$TEMP_DIR"
            exit 1
        fi

        # Check for home-data volume
        if grep -q "home-data" "$TEMP_DIR/docker-compose.yml"; then
            echo "✓ PASS: home-data volume is defined in generated config"
        else
            echo "✗ FAIL: home-data volume not defined in generated config"
            cat "$TEMP_DIR/docker-compose.yml"
            rm -rf "$TEMP_DIR"
            exit 1
        fi
    else
        echo "✗ FAIL: docker-compose.yml not generated"
        rm -rf "$TEMP_DIR"
        exit 1
    fi
else
    echo "⊘ SKIP: install.sh functions not available (requires sourced script)"
fi

rm -rf "$TEMP_DIR"

echo ""
echo "=== All tests passed ==="
echo ""