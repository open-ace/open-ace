#!/usr/bin/env bash
#
# Verify code-server installation in Docker image
# Issue #2238: Ensure code-server is installed for local workspace VS Code button
#
# Usage:
#   docker run --rm <image> /app/scripts/verify-code-server.sh
#

set -euo pipefail

echo "=== Verifying code-server installation ==="
echo ""

# Step 1: Check if code-server executable exists
echo "1. Checking which code-server..."
if ! CS_PATH=$(which code-server 2>/dev/null); then
    echo "   ERROR: code-server not found in PATH"
    echo "   PATH: $PATH"
    exit 1
fi
echo "   Found at: $CS_PATH"
echo ""

# Step 2: Check if it's executable
echo "2. Checking executable permission..."
if ! test -x "$CS_PATH"; then
    echo "   ERROR: $CS_PATH is not executable"
    ls -la "$CS_PATH"
    exit 1
fi
echo "   Executable: OK"
echo ""

# Step 3: Check version
echo "3. Checking version..."
if ! VERSION=$(code-server --version 2>/dev/null | head -1); then
    echo "   ERROR: Failed to get code-server version"
    exit 1
fi
echo "   Version: $VERSION"
echo ""

# Step 4: Validate version format (X.Y.Z)
echo "4. Validating version format..."
if ! echo "$VERSION" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+$'; then
    echo "   WARNING: Version format does not match X.Y.Z pattern"
    echo "   Version: $VERSION"
    # Don't fail on format mismatch, just warn
else
    echo "   Format: OK"
fi
echo ""

# Step 5: Check expected path (Debian installation)
echo "5. Checking expected installation path..."
EXPECTED_PATH="/usr/bin/code-server"
if [ "$CS_PATH" != "$EXPECTED_PATH" ]; then
    echo "   WARNING: code-server is not at expected path"
    echo "   Expected: $EXPECTED_PATH"
    echo "   Actual: $CS_PATH"
    # Don't fail on path mismatch, just warn
else
    echo "   Path: OK ($EXPECTED_PATH)"
fi
echo ""

echo "=== code-server verification PASSED ==="
exit 0