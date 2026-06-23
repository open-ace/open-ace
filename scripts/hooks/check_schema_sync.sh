#!/bin/bash
# Warn when committed schema snapshots drift from generated structures.
#
# This script is called by pre-commit when migration files are modified.
# It performs structural validation in warn-only mode so contributors see the
# drift immediately without being hard-blocked mid-fix.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
PYTHON_BIN="$PROJECT_ROOT/.venv/bin/python"
if [ ! -x "$PYTHON_BIN" ]; then
    PYTHON_BIN="python3"
fi

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
    echo -e "${YELLOW}Please run: $PYTHON_BIN scripts/rebuild_schema_snapshots.py --postgres-url <temp-db-url>${NC}"
    exit 1
fi

echo -e "${YELLOW}Running structural schema sync checks (warn-only)...${NC}"
set +e
CHECK_OUTPUT="$("$PYTHON_BIN" "$PROJECT_ROOT/scripts/check_schema_sync.py" --warn-only --json 2>&1)"
CHECK_STATUS=$?
set -e
echo "$CHECK_OUTPUT"

if [ $CHECK_STATUS -ne 0 ]; then
    echo -e "${YELLOW}Warning: schema sync checker could not run in the current Python environment.${NC}"
    echo -e "${YELLOW}Use a project environment with Alembic installed, then run:${NC}"
    echo "  cd \"$PROJECT_ROOT\""
    echo "  $PYTHON_BIN scripts/check_schema_sync.py --warn-only"
    echo -e "${YELLOW}CI will run the authoritative schema-sync check separately.${NC}"
    echo -e "${GREEN}Schema sync warning check complete${NC}"
    exit 0
fi

HAS_FAILURE="$(printf '%s' "$CHECK_OUTPUT" | "$PYTHON_BIN" -c 'import json,sys; print("yes" if json.load(sys.stdin).get("has_failure") else "no")')"
if [ "$HAS_FAILURE" = "yes" ]; then
    echo -e "${YELLOW}Warning: schema sync checker reported drift${NC}"
    echo -e "${YELLOW}If you changed Alembic migrations, refresh and validate the committed schema snapshots with:${NC}"
    echo "  cd \"$PROJECT_ROOT\""
    echo "  $PYTHON_BIN scripts/rebuild_schema_snapshots.py --postgres-url postgresql://USER:PASSWORD@HOST:5432/DB"
    echo "  $PYTHON_BIN scripts/check_schema_sync.py --postgres-url postgresql://USER:PASSWORD@HOST:5432/DB"
    echo "  $PYTHON_BIN scripts/validate_schema.py"
    echo -e "${YELLOW}CI will fail until updated schema snapshots are committed.${NC}"
fi

echo -e "${GREEN}Schema sync warning check complete${NC}"
exit 0
