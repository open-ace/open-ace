# Schema Sync Fix - 2026-08-11

## Issue
PR #2436 failed in merge stage with schema-sync CI failure.

## Root Cause
The migration `20260808_001_add_schema_metadata.py` created the `schema_metadata` table with inconsistent default values between PostgreSQL and SQLite:

- **PostgreSQL**: `DEFAULT NOW()` specified explicitly
- **SQLite**: No default value specified in `sa.Column()`

This caused schema drift because:
1. When Alembic runs on SQLite, it produces a table without a default
2. The committed schema-sqlite.sql had `DEFAULT CURRENT_TIMESTAMP`
3. The schema sync check detected this mismatch

## Fix Applied
Updated `migrations/versions/20260808_001_add_schema_metadata.py` to specify `server_default=sa.text("CURRENT_TIMESTAMP")` for SQLite, matching PostgreSQL's behavior.

### Changes
```python
# Before (line 69-73):
op.create_table(
    "schema_metadata",
    sa.Column("initialized_at", sa.DateTime(), nullable=False),
    sa.Column("schema_version", sa.String(64), nullable=True),
)

# After:
op.create_table(
    "schema_metadata",
    sa.Column(
        "initialized_at",
        sa.DateTime(),
        nullable=False,
        server_default=sa.text("CURRENT_TIMESTAMP"),
    ),
    sa.Column("schema_version", sa.String(64), nullable=True),
)
```

## Verification Results
All checks pass:

✅ **Schema Sync Check**
- `[sqlite] committed schema-postgres.sql -> schema-sqlite.sql`: Match
- `[sqlite] alembic head -> committed schema-sqlite.sql`: Match

✅ **Schema Validation**: Passed

✅ **Migration Heads**: Single head (`20260810_001_enforce_admin_role_migration`)

✅ **Black**: 1 file formatted correctly

✅ **Ruff**: All checks passed

## Modified Files
- `migrations/versions/20260808_001_add_schema_metadata.py`: Added `server_default` for SQLite

## Impact
This fix ensures that:
1. Alembic migrations produce consistent schema across PostgreSQL and SQLite
2. Schema snapshots match what migrations generate
3. The schema-sync CI check passes

The fix is minimal and targeted, affecting only the migration file to ensure cross-database consistency.