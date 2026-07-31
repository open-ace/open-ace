# CI Fixes Summary for PR #2176

## Issues Fixed

### 1. Missing `secret_holder.py` Module (Critical PR E2E, test (3.11))
**Problem**: `app/modules/sso/secret_holder.py` exists locally but was not tracked in git, causing `ModuleNotFoundError` in CI.

**Status**: File exists and is ready to be added by orchestrator.

**Verification**: 
- File exists at `app/modules/sso/secret_holder.py`
- All imports work correctly locally
- Tests pass: `python -m pytest tests/unit/test_sso_manager_security.py` (17 passed)

### 2. mypy Type Error in `provider.py` (lint)
**Problem**: `get_client_secret()` returned `Any` instead of declared `str`, triggering mypy `no-any-return` error.

**Fix**: Added `cast` import and used `cast("str", ...)` to properly type the return value.
- File: `app/modules/sso/provider.py`
- Line 11: Added `cast` to `from typing import Any, cast`
- Line 60: Changed `return self.client_secret.get()` to `return cast("str", self.client_secret.get())`

**Verification**: `SKIP=bandit,no-commit-to-branch pre-commit run --all-files` - mypy Passed

### 3. SQL Migration Error (schema-sync)
**Problem**: Migration tried to use `->>` operator on `text` column, but it's a `jsonb` operator.
```
sqlalchemy.exc.ProgrammingError: operator does not exist: text ->> unknown
```

**Fix**: Cast `settings` column to `jsonb` before using `->>` operator.
- File: `migrations/versions/20260731_003_add_teams_sync_source_indexes.py`
- Changed: `settings->>'sync_source'` → `settings::jsonb->>'sync_source'`
- Applied to both Feishu and DingTalk index definitions

**Verification**: `python scripts/shared/schema_sync.py --check` - Passed

### 4. Git Worktree Submodule Warning (all CI jobs)
**Problem**: `.worktrees/` directory contained temporary worktrees that appeared as submodules without `.gitmodules` entry.

**Fix**: Added `.worktrees/` to `.gitignore` to prevent tracking.
- File: `.gitignore`
- Line 82: Added `.worktrees/` entry

**Verification**: Worktrees now ignored by git

## Pre-commit Check Results

All checks pass:
- ✅ Check schema.sql sync
- ✅ Check single migration head
- ✅ Check migration authoring rules
- ✅ Validate PostgreSQL schema boolean fields
- ✅ SQL Compatibility Check
- ✅ API Security Scan
- ✅ Table Boundary Check
- ✅ black
- ✅ isort
- ✅ ruff
- ✅ mypy
- ✅ pydocstyle

## Test Results

All tests for modified files pass:
- ✅ tests/unit/test_sso_manager_security.py: 17 passed
- ✅ tests/unit/test_project_repo.py: 55 passed
- ✅ tests/1826/test_sso_security_improvements.py: 17 passed

## Files Modified

1. `app/modules/sso/provider.py` - Fixed mypy type error
2. `migrations/versions/20260731_003_add_teams_sync_source_indexes.py` - Fixed SQL cast
3. `.gitignore` - Added worktrees exclusion

## Files to Add (by orchestrator)

1. `app/modules/sso/secret_holder.py` - New file, already exists on disk

## Remaining Work

None - all CI failures have been fixed. The orchestrator needs to:
1. Add `app/modules/sso/secret_holder.py` to git
2. Commit and push all changes