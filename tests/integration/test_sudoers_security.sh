#!/bin/bash
# sudoers security test for Issue #1855 + #2181
# Tests that sudoers generation follows security requirements:
# 1. gh pr merge --admin is not in default whitelist
# 2. cat/chown/useradd/rm wildcards are removed
# 3. OPENACE_CLI is removed
# 4. env_keep does not contain sensitive variables
# 5. --admin is only present when OPENACE_ALLOW_ADMIN_MERGE=1

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

echo "========================================"
echo "sudoers Security Tests (Issue #1855 + #2181)"
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

if grep -q "openace-rm" "$ENTRYPOINT"; then
    pass "Entrypoint references openace-rm wrapper (Issue #2181)"
else
    fail "Entrypoint missing openace-rm wrapper reference"
fi

# Test 2: Verify OPENACE_UTILS does not contain removed commands
echo ""
echo "Test 2: Verifying OPENACE_UTILS excludes dangerous wildcards..."

# Check that the new OPENACE_UTILS definition excludes cat/chown/useradd/rm wildcards
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

# Issue #2181: rm wildcard should be removed
if grep -E "Cmnd_Alias OPENACE_UTILS.*=/usr/bin/rm \*" "$ENTRYPOINT" 2>/dev/null; then
    fail "OPENACE_UTILS still contains rm wildcard (Issue #2181)"
else
    pass "OPENACE_UTILS does not contain rm wildcard (Issue #2181)"
fi

# Test 3: Verify OPENACE_CLI is removed (Issue #2181)
echo ""
echo "Test 3: Verifying OPENACE_CLI is removed (Issue #2181)..."

if grep -q "Cmnd_Alias OPENACE_CLI" "$ENTRYPOINT" 2>/dev/null; then
    fail "OPENACE_CLI alias should be removed (Issue #2181)"
else
    pass "OPENACE_CLI alias is removed (Issue #2181)"
fi

if grep -q "NOPASSWD: OPENACE_CLI" "$ENTRYPOINT" 2>/dev/null; then
    fail "OPENACE_CLI rule should be removed (Issue #2181)"
else
    pass "OPENACE_CLI rule is removed (Issue #2181)"
fi

# Test 4: Verify env_keep does not contain sensitive variables (Issue #2181)
echo ""
echo "Test 4: Verifying env_keep excludes sensitive variables (Issue #2181)..."

# Check that env_keep does not contain API keys
if grep -E "env_keep.*OPENAI_API_KEY" "$ENTRYPOINT" 2>/dev/null | grep -v "^[[:space:]]*#" | grep -q "."; then
    fail "env_keep should not contain OPENAI_API_KEY (Issue #2181)"
else
    pass "env_keep does not contain OPENAI_API_KEY (Issue #2181)"
fi

if grep -E "env_keep.*ANTHROPIC_API_KEY" "$ENTRYPOINT" 2>/dev/null | grep -v "^[[:space:]]*#" | grep -q "."; then
    fail "env_keep should not contain ANTHROPIC_API_KEY (Issue #2181)"
else
    pass "env_keep does not contain ANTHROPIC_API_KEY (Issue #2181)"
fi

if grep -E "env_keep.*GH_TOKEN" "$ENTRYPOINT" 2>/dev/null | grep -v "^[[:space:]]*#" | grep -q "."; then
    fail "env_keep should not contain GH_TOKEN (Issue #2181)"
else
    pass "env_keep does not contain GH_TOKEN (Issue #2181)"
fi

# Test 5: Verify GH_SAFE admin handling
echo ""
echo "Test 5: Verifying GH_SAFE admin merge opt-in mechanism..."

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

# Test 6: Verify wrapper scripts exist
echo ""
echo "Test 6: Verifying wrapper scripts exist..."

WRAPPER_SCRIPTS=(
    "scripts/openace-chown.sh"
    "scripts/openace-useradd.sh"
    "scripts/openace-cat.sh"
    "scripts/openace-mkdir.sh"
    "scripts/openace-rm.sh"
    "scripts/openace-restore-sudoers.sh"
)

for wrapper in "${WRAPPER_SCRIPTS[@]}"; do
    if [ -f "${PROJECT_ROOT}/${wrapper}" ]; then
        pass "Wrapper exists: ${wrapper}"
    else
        fail "Wrapper missing: ${wrapper}"
    fi
done

# Test 7: Verify wrapper scripts have proper security checks
echo ""
echo "Test 7: Verifying wrapper security constraints..."

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

# Check openace-rm has proper security checks (Issue #2181)
if grep -q "MIN_UID=1000" "${PROJECT_ROOT}/scripts/openace-rm.sh"; then
    pass "openace-rm has MIN_UID check"
else
    fail "openace-rm missing MIN_UID check"
fi

if grep -q "RESERVED_USERNAMES" "${PROJECT_ROOT}/scripts/openace-rm.sh"; then
    pass "openace-rm has reserved username check"
else
    fail "openace-rm missing reserved username check"
fi

if grep -q "DANGEROUS_OPTIONS" "${PROJECT_ROOT}/scripts/openace-rm.sh"; then
    pass "openace-rm has dangerous options check"
else
    fail "openace-rm missing dangerous options check"
fi

if grep -q "flock" "${PROJECT_ROOT}/scripts/openace-rm.sh"; then
    pass "openace-rm has TOCTOU protection (flock)"
else
    fail "openace-rm missing TOCTOU protection"
fi

# Check all wrappers have audit logging
for wrapper in openace-chown openace-useradd openace-cat openace-mkdir openace-rm; do
    if grep -q "log_audit" "${PROJECT_ROOT}/scripts/${wrapper}.sh"; then
        pass "${wrapper} has audit logging"
    else
        fail "${wrapper} missing audit logging"
    fi
done

# Test 8: Verify Dockerfile copies wrappers
echo ""
echo "Test 8: Verifying Dockerfile installs wrappers..."

DOCKERFILE="${PROJECT_ROOT}/Dockerfile"
for wrapper in openace-chown openace-useradd openace-cat openace-mkdir openace-rm openace-restore-sudoers; do
    if grep -q "COPY scripts/${wrapper}.sh" "$DOCKERFILE"; then
        pass "Dockerfile copies ${wrapper}.sh"
    else
        fail "Dockerfile missing ${wrapper}.sh"
    fi
done

# Test 9: Verify package-method install.sh is consistent (Issue #2181)
echo ""
echo "Test 9: Verifying package-method install.sh consistency (Issue #2181)..."

INSTALL_SH="${PROJECT_ROOT}/scripts/install-central/package-method/install.sh"

# Check that install.sh does not define OPENACE_CLI Cmnd_Alias (excluding detection comments)
if grep -E "^[^#]*Cmnd_Alias OPENACE_CLI" "$INSTALL_SH" 2>/dev/null | grep -v "grep" | grep -v "print_warning" | grep -q "."; then
    fail "package-method install.sh should not define OPENACE_CLI (Issue #2181)"
else
    pass "package-method install.sh does not define OPENACE_CLI (Issue #2181)"
fi

# Check that env_keep in defaults_section does not contain sensitive variables
if grep -E "Defaults env_keep.*OPENAI_API_KEY" "$INSTALL_SH" 2>/dev/null | grep -v "^[[:space:]]*#" | grep -v "grep" | grep -v "print_warning" | grep -q "."; then
    fail "package-method install.sh env_keep should not contain OPENAI_API_KEY (Issue #2181)"
else
    pass "package-method install.sh env_keep is clean (Issue #2181)"
fi

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
