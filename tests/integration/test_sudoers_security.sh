#!/bin/bash
# sudoers security test for Issue #1855
# Tests that sudoers generation follows security requirements:
# 1. gh pr merge --admin is not in default whitelist
# 2. cat/chown/useradd wildcards are removed
# 3. --admin is only present when OPENACE_ALLOW_ADMIN_MERGE=1

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
ENTRYPOINT="${PROJECT_ROOT}/docker-entrypoint.sh"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m' # No Color

PASSED=0
FAILED=0

pass() {
    echo "${GREEN}✅ PASS: $1${NC}"
    PASSED=$((PASSED + 1))
}

fail() {
    echo "${RED}❌ FAIL: $1${NC}"
    FAILED=$((FAILED + 1))
}

# Generate sudoers content with given environment
generate_sudoers() {
    local env_vars="$1"
    local temp_file=$(mktemp)

    # Source the entrypoint functions in a subshell
    (
        cd "$PROJECT_ROOT"
        # Set up minimal environment for testing
        export WORKSPACE_MULTI_USER_MODE=true
        export DATABASE_URL="postgresql://test:test@localhost:5432/test"
        export OPENACE_CONFIG_DIR="/tmp/test-config"
        $env_vars

        # Extract the sudoers generation part
        # This is a simplified test that checks the generated content patterns
        cat > "$temp_file" << 'SUDOERS_TEST'
# Test sudoers content placeholder
SUDOERS_TEST

    ) 2>/dev/null || true

    cat "$temp_file"
    rm -f "$temp_file"
}

echo "========================================"
echo "sudoers Security Tests (Issue #1855)"
echo "========================================"
echo ""

# Test 1: Check entrypoint contains security wrapper logic
echo "Test 1: Verifying entrypoint contains security wrapper logic..."
if grep -q "OPENACE_ALLOW_ADMIN_MERGE" "$ENTRYPOINT"; then
    pass "Entrypoint contains OPENACE_ALLOW_ADMIN_MERGE check"
else
    fail "Entrypoint missing OPENACE_ALLOW_ADMIN_MERGE check"
fi

if grep -q "openace-chown" "$ENTRYPOINT"; then
    pass "Entrypoint references openace-chown wrapper"
else
    fail "Entrypoint missing openace-chown wrapper reference"
fi

if grep -q "openace-useradd" "$ENTRYPOINT"; then
    pass "Entrypoint references openace-useradd wrapper"
else
    fail "Entrypoint missing openace-useradd wrapper reference"
fi

# Test 2: Verify OPENACE_UTILS does not contain removed commands
echo ""
echo "Test 2: Verifying OPENACE_UTILS excludes dangerous wildcards..."

# Check that the new OPENACE_UTILS definition excludes cat/chown/useradd wildcards
if grep -E "Cmnd_Alias OPENACE_UTILS.*=/usr/bin/cat \*" "$ENTRYPOINT" 2>/dev/null; then
    fail "OPENACE_UTILS still contains cat wildcard"
else
    pass "OPENACE_UTILS does not contain cat wildcard"
fi

if grep -E "Cmnd_Alias OPENACE_UTILS.*=/usr/bin/chown \*" "$ENTRYPOINT" 2>/dev/null; then
    fail "OPENACE_UTILS still contains chown wildcard"
else
    pass "OPENACE_UTILS does not contain chown wildcard"
fi

if grep -E "Cmnd_Alias OPENACE_UTILS.*=/usr/bin/useradd \*" "$ENTRYPOINT" 2>/dev/null; then
    fail "OPENACE_UTILS still contains useradd wildcard"
else
    pass "OPENACE_UTILS does not contain useradd wildcard"
fi

# Test 3: Verify GH_SAFE admin handling
echo ""
echo "Test 3: Verifying GH_SAFE admin merge opt-in mechanism..."

# Check that GH_ADMIN_RULE is conditional
if grep -q 'GH_ADMIN_RULE=' "$ENTRYPOINT"; then
    pass "GH_ADMIN_RULE variable exists for conditional --admin"
else
    fail "GH_ADMIN_RULE variable missing"
fi

# Check that pr merge * --admin is not directly in GH_SAFE
if grep -E 'GH_SAFE.*pr merge.*--admin' "$ENTRYPOINT" 2>/dev/null | grep -v 'GHADMIN_RULE' | grep -v '#'; then
    fail "GH_SAFE contains --admin without opt-in check"
else
    pass "GH_SAFE does not contain unconditional --admin"
fi

# Test 4: Verify wrapper scripts exist
echo ""
echo "Test 4: Verifying wrapper scripts exist..."

WRAPPER_SCRIPTS=(
    "scripts/openace-chown.sh"
    "scripts/openace-useradd.sh"
    "scripts/openace-cat.sh"
    "scripts/openace-mkdir.sh"
    "scripts/openace-restore-sudoers.sh"
)

for wrapper in "${WRAPPER_SCRIPTS[@]}"; do
    if [ -f "${PROJECT_ROOT}/${wrapper}" ]; then
        pass "Wrapper exists: ${wrapper}"
    else
        fail "Wrapper missing: ${wrapper}"
    fi
done

# Test 5: Verify wrapper scripts have proper security checks
echo ""
echo "Test 5: Verifying wrapper security constraints..."

# Check openace-chown has UID validation
if grep -q "MIN_UID=1000" "${PROJECT_ROOT}/scripts/openace-chown.sh"; then
    pass "openace-chown has MIN_UID check"
else
    fail "openace-chown missing MIN_UID check"
fi

# Check openace-useradd has reserved username check
if grep -q "RESERVED_USERNAMES" "${PROJECT_ROOT}/scripts/openace-useradd.sh"; then
    pass "openace-useradd has reserved username check"
else
    fail "openace-useradd missing reserved username check"
fi

# Check openace-cat has sensitive file blacklist
if grep -q "SENSITIVE_PATTERNS" "${PROJECT_ROOT}/scripts/openace-cat.sh"; then
    pass "openace-cat has sensitive file blacklist"
else
    fail "openace-cat missing sensitive file blacklist"
fi

# Check all wrappers have audit logging
for wrapper in openace-chown openace-useradd openace-cat openace-mkdir; do
    if grep -q "log_audit" "${PROJECT_ROOT}/scripts/${wrapper}.sh"; then
        pass "${wrapper} has audit logging"
    else
        fail "${wrapper} missing audit logging"
    fi
done

# Test 6: Verify Dockerfile copies wrappers
echo ""
echo "Test 6: Verifying Dockerfile installs wrappers..."

DOCKERFILE="${PROJECT_ROOT}/Dockerfile"
for wrapper in openace-chown openace-useradd openace-cat openace-mkdir openace-restore-sudoers; do
    if grep -q "COPY scripts/${wrapper}.sh" "$DOCKERFILE"; then
        pass "Dockerfile copies ${wrapper}.sh"
    else
        fail "Dockerfile missing ${wrapper}.sh"
    fi
done

# Summary
echo ""
echo "========================================"
echo "Test Summary"
echo "========================================"
echo "Passed: ${PASSED}"
echo "Failed: ${FAILED}"
echo ""

if [ $FAILED -gt 0 ]; then
    echo "${RED}Some tests failed!${NC}"
    exit 1
else
    echo "${GREEN}All tests passed!${NC}"
    exit 0
fi