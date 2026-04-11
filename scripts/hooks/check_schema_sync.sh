#!/bin/bash
# Check if schema.sql needs to be regenerated when migrations change
#
# This script is called by pre-commit when migration files are modified.
# It checks if schema.sql is older than the latest migration and warns the user.

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

# Check if migrations directory exists
if [ ! -d "$MIGRATIONS_DIR" ]; then
    echo -e "${YELLOW}Warning: migrations directory not found${NC}"
    exit 0
fi

# Find the latest migration file (by modification time)
LATEST_MIGRATION=$(find "$MIGRATIONS_DIR" -name "*.py" -type f -printf '%T@ %p\n' | sort -rn | head -1 | cut -d' ' -f2-)

if [ -z "$LATEST_MIGRATION" ]; then
    echo -e "${YELLOW}Warning: no migration files found${NC}"
    exit 0
fi

# Check if schema files exist
SCHEMA_PG="$SCHEMA_DIR/schema-postgres.sql"
SCHEMA_SQLITE="$SCHEMA_DIR/schema-sqlite.sql"

if [ ! -f "$SCHEMA_PG" ] || [ ! -f "$SCHEMA_SQLITE" ]; then
    echo -e "${RED}Error: schema files not found${NC}"
    echo -e "${YELLOW}Please run: python3 scripts/generate_schema.py${NC}"
    exit 1
fi

# Compare modification times
MIGRATION_TIME=$(stat -c %Y "$LATEST_MIGRATION" 2>/dev/null || stat -f %m "$LATEST_MIGRATION")
SCHEMA_TIME=$(stat -c %Y "$SCHEMA_PG" 2>/dev/null || stat -f %m "$SCHEMA_PG")

if [ "$MIGRATION_TIME" -gt "$SCHEMA_TIME" ]; then
    echo -e "${YELLOW}========================================${NC}"
    echo -e "${YELLOW}Warning: Migration files are newer than schema.sql${NC}"
    echo -e "${YELLOW}========================================${NC}"
    echo ""
    echo "Latest migration: $LATEST_MIGRATION"
    echo "Schema file: $SCHEMA_PG"
    echo ""
    echo -e "${YELLOW}You may need to regenerate the schema:${NC}"
    echo "  python3 scripts/generate_schema.py"
    echo ""
    echo -e "${YELLOW}Or run package.sh which will auto-generate the schema${NC}"
    echo ""
    # Don't fail the commit, just warn
    exit 0
fi

echo -e "${GREEN}Schema files are up to date with migrations${NC}"
exit 0