#!/bin/bash
#
# Security Mode Migration Script (Issue #2331)
#
# Detects FLASK_ENV usage and migrates to OPENACE_SECURITY_MODE.
#
# Usage:
#   ./scripts/migrate_security_mode.sh           # Detect only
#   ./scripts/migrate_security_mode.sh --apply   # Auto-apply changes
#
# Exit codes:
#   0: No FLASK_ENV usage found
#   1: FLASK_ENV found, migration needed
#   2: Migration applied successfully (--apply)
#

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Parse arguments
APPLY_MODE=false
if [ "${1:-}" = "--apply" ]; then
    APPLY_MODE=true
fi

echo "=========================================="
echo "  Security Mode Migration Script"
echo "  Issue #2331: FLASK_ENV → OPENACE_SECURITY_MODE"
echo "=========================================="
echo ""

# Find files with FLASK_ENV=production
echo -e "${BLUE}Searching for FLASK_ENV usage...${NC}"
echo ""

FOUND_FILES=()

# Search .env files
while IFS= read -r -d '' file; do
    if grep -q "FLASK_ENV.*production" "$file" 2>/dev/null; then
        FOUND_FILES+=("$file")
    fi
done < <(find . -name ".env*" -type f -print0 2>/dev/null || true)

# Search docker-compose files
for file in docker-compose*.yml; do
    if [ -f "$file" ] && grep -q "FLASK_ENV.*production" "$file" 2>/dev/null; then
        FOUND_FILES+=("$file")
    fi
done

# Search Kubernetes ConfigMaps
while IFS= read -r -d '' file; do
    if grep -q "FLASK_ENV.*production" "$file" 2>/dev/null; then
        FOUND_FILES+=("$file")
    fi
done < <(find k8s -name "*.yaml" -type f -print0 2>/dev/null || true)

# Search systemd unit files
while IFS= read -r -d '' file; do
    if grep -q "FLASK_ENV.*production" "$file" 2>/dev/null; then
        FOUND_FILES+=("$file")
    fi
done < <(find scripts -name "*.service" -type f -print0 2>/dev/null || true)

# Check if any files found
if [ ${#FOUND_FILES[@]} -eq 0 ]; then
    echo -e "${GREEN}✓ No FLASK_ENV usage found.${NC}"
    echo ""
    echo "Your deployment is ready for v2.1.0."
    exit 0
fi

# Display found files
echo -e "${YELLOW}⚠ Found FLASK_ENV usage in the following files:${NC}"
echo ""

for file in "${FOUND_FILES[@]}"; do
    echo "  - $file"
    # Show matching lines
    grep -n "FLASK_ENV" "$file" 2>/dev/null | head -3 | while read -r line; do
        echo "    $line"
    done
done

echo ""

# If not applying, show instructions
if [ "$APPLY_MODE" = false ]; then
    echo -e "${YELLOW}Migration needed.${NC}"
    echo ""
    echo "Recommended changes:"
    echo ""
    echo "  1. Replace FLASK_ENV=production with OPENACE_SECURITY_MODE=production"
    echo "  2. Ensure secrets are explicitly set:"
    echo "     - SECRET_KEY (32+ characters)"
    echo "     - OPENACE_ENCRYPTION_KEY (32+ characters)"
    echo "     - DB_PASSWORD (9+ characters, strong)"
    echo ""
    echo "To auto-apply these changes, run:"
    echo -e "  ${GREEN}./scripts/migrate_security_mode.sh --apply${NC}"
    echo ""
    echo "Migration guide: https://github.com/open-ace/open-ace/issues/2331"
    exit 1
fi

# Apply mode
echo -e "${BLUE}Applying migration...${NC}"
echo ""

CHANGES_APPLIED=0

for file in "${FOUND_FILES[@]}"; do
    echo "Processing: $file"

    # Create backup
    cp "$file" "$file.bak"

    # Replace FLASK_ENV with OPENACE_SECURITY_MODE
    # This handles both standalone lines and environment array entries
    sed -i 's/FLASK_ENV.*production/OPENACE_SECURITY_MODE=production/g' "$file" || true
    sed -i 's/FLASK_ENV=production/OPENACE_SECURITY_MODE=production/g' "$file" || true
    sed -i 's/"FLASK_ENV".*"production"/"OPENACE_SECURITY_MODE": "production"/g' "$file" || true

    CHANGES_APPLIED=$((CHANGES_APPLIED + 1))
    echo "  ✓ Updated"
done

echo ""
if [ $CHANGES_APPLIED -gt 0 ]; then
    echo -e "${GREEN}✓ Migration applied to $CHANGES_APPLIED file(s).${NC}"
    echo ""
    echo "Next steps:"
    echo "  1. Review changes: git diff"
    echo "  2. Test deployment: docker compose config"
    echo "  3. Verify secrets are set in .env"
    echo "  4. Deploy and test: docker compose up -d"
    echo ""
    echo "Migration guide: https://github.com/open-ace/open-ace/issues/2331"
    exit 2
else
    echo -e "${YELLOW}No changes applied.${NC}"
    exit 1
fi