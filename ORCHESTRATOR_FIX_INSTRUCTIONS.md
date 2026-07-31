# CI Fix Instructions for Orchestrator

## Problem Summary

PR #2176 is failing the schema-sync CI check due to two issues:

### Issue 1: Git Submodules Error

**Problem**: `.worktrees/` directories are tracked as git submodules (mode 160000) but there's no `.gitmodules` file, causing git operations to fail with:
```
fatal: No url found for submodule path '.worktrees/082fbaf2-d1b4-4075-917f-1d628c44b357' in .gitmodules
```

**Root Cause**: Previous commits added `.worktrees/` directories as submodules but didn't create `.gitmodules`.

**Fix**: Run the following commands:
```bash
git rm --cached -r .worktrees/
git commit -m "fix(ci): remove worktree submodules from git index"
git push
```

### Issue 2: Schema Drift

**Problem**: Committed schema files don't match what alembic migrations produce.

**Specific Issues**:
1. Multiple indexes in committed schema but not in migrations
2. Column drift in several tables (e.g., `dingtalk_webhook_secret` missing from `notification_preferences`, extra columns in `registration_tokens`)

**Root Cause**: Schema files were manually edited or generated from a database with runtime-created tables, not from pure migrations.

**Fix**: Requires PostgreSQL to properly rebuild. Run:
```bash
# Start PostgreSQL (Docker or local)
docker run -d --name postgres-schema -e POSTGRES_PASSWORD=ace -e POSTGRES_USER=ace -e POSTGRES_DB=ace_schema -p 5432:5432 postgres:15

# Wait for PostgreSQL to start
sleep 5

# Rebuild schema snapshots
python scripts/rebuild_schema_snapshots.py --postgres-url "postgresql://ace:ace@localhost:5432/ace_schema"

# Stop PostgreSQL
docker stop postgres-schema
docker rm postgres-schema

# Commit changes
git add schema/
git commit -m "fix(ci): rebuild schema snapshots from clean migrations"
git push
```

## Current State

- `.worktrees/` submodules still in git index (blocking git operations)
- Schema drift still exists (requires PostgreSQL to fix properly)

## Recommended Action

The orchestrator should:
1. First fix the `.worktrees/` issue (quick fix)
2. Then properly rebuild the schema files using PostgreSQL
3. Run `SKIP=bandit,no-commit-to-branch pre-commit run --all-files` to verify
4. Push changes

## Files Modified

- `schema/schema-postgres.sql` - Needs rebuild from clean migrations
- `schema/schema-sqlite.sql` - Needs rebuild from clean migrations

## Verification

After fixes, run:
```bash
python scripts/check_schema_sync.py
SKIP=bandit,no-commit-to-branch pre-commit run --all-files
```
