#!/bin/bash
# Warn when committed schema snapshots drift from generated structures.
#
# This script is called by pre-commit when migration files are modified.
# It performs structural validation in warn-only mode so contributors see the
# drift immediately without being hard-blocked mid-fix.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

MIGRATIONS_DIR="$PROJECT_ROOT/migrations/versions"
SCHEMA_DIR="$PROJECT_ROOT/schema"

# Colors
RED='\033[0;31m'
YELLOW='\033[1;33m'
GREEN='\033[0;32m'
NC='\033[0m' # No Color

SCHEMA_PG="$SCHEMA_DIR/schema-postgres.sql"
SCHEMA_SQLITE="$SCHEMA_DIR/schema-sqlite.sql"

if [ ! -f "$SCHEMA_PG" ] || [ ! -f "$SCHEMA_SQLITE" ]; then
    echo -e "${RED}Error: schema files not found${NC}"
    echo -e "${YELLOW}Please run: python3 scripts/rebuild_schema_snapshots.py --postgres-url <temp-db-url>${NC}"
    exit 1
fi

echo -e "${YELLOW}Running structural schema sync checks (warn-only)...${NC}"
if ! python3 "$PROJECT_ROOT/scripts/check_schema_sync.py" --warn-only; then
    echo -e "${YELLOW}Warning: schema sync checker reported drift${NC}"
fi

echo -e "${GREEN}Schema sync warning check complete${NC}"
exit 0
