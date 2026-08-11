# CI Fix Summary - 2026-08-11

## Issue
PR #2436 failed in merge stage with multiple CI failures:
- **PR Gate**: FAILURE (LINT=failure, TEST=failure, BUILD=skipped)
- **test (3.11)**: FAILURE with CHECK constraint violation
- **lint**: FAILURE with black formatting issues

## Root Causes

### 1. Test Failure - CHECK Constraint Violation
Migration `20260810_001_enforce_admin_role_migration` added CHECK constraints to the `users.role` column that only allow: 'platform_admin', 'tenant_admin', 'manager', 'user', 'readonly'. Several tests were still using the deprecated 'admin' role, causing SQLite integrity errors.

### 2. Black Formatting Issues
Several files had formatting that didn't match black's style requirements.

## Fixes Applied

### 1. tests/integration/test_governance_audit_tenant_scope.py
- Changed `role='admin'` to `role='tenant_admin'` in `_insert_admin()` function (line 26)
- Changed `role='admin'` to `role='tenant_admin'` in `_login_as()` function (line 39)

### 2. tests/integration/test_user_repo_sqlite.py
- Changed `role='admin'` to `role='tenant_admin'` in `test_update_user_multiple_fields_with_tenant_id()` (line 92)
- Changed assertion `role=='admin'` to `role=='tenant_admin'` (line 102)
- Changed `role='admin'` to `role='manager'` in `test_update_user_role()` (line 178)
- Changed assertion `role=='admin'` to `role=='manager'` (line 182)

### 3. Formatting Fixes
Applied black formatting to:
- `tests/unit/test_check_min_revision.py`
- `migrations/versions/20260810_001_enforce_admin_role_migration.py`

## Verification Results

### Test Results
```bash
python -m pytest tests/integration/test_governance_audit_tenant_scope.py \
                 tests/integration/test_user_repo_sqlite.py \
                 tests/unit/test_check_min_revision.py -v
```
**Result**: ✅ All 23 tests passed

### Lint Checks
```bash
python -m black --check <modified files>
python -m ruff check <modified files>
```
**Result**: ✅ All checks passed

### Schema Validation
```bash
python scripts/validate_schema.py
```
**Result**: ✅ Schema validation passed

## Modified Files Summary
- `migrations/versions/20260810_001_enforce_admin_role_migration.py` - Black formatting
- `tests/integration/test_governance_audit_tenant_scope.py` - Fixed role='admin' to role='tenant_admin'
- `tests/integration/test_user_repo_sqlite.py` - Fixed role='admin' to valid roles
- `tests/unit/test_check_min_revision.py` - Black formatting

Total: 4 files changed, 50 insertions(+), 126 deletions(-)

## Notes

1. **Git-related test failures**: The `test_repo_drift_integration.py::test_external_pull_is_allowed` test fails with exit code 126 due to git command execution restrictions in this environment. This is expected and was documented in previous CI fix attempts.

2. **Schema-sync**: The schema files already contain the correct changes matching the migrations:
   - Added `schema_metadata` table
   - Updated CHECK constraints to use issue-prefixed names and remove 'admin' from allowed roles

3. **No new code introduced**: All changes are fixes to existing code to comply with the new CHECK constraint introduced by migration 20260810_001_enforce_admin_role_migration.

## CI Status After Fixes
- ✅ Test (3.11): Should pass - CHECK constraint violations fixed
- ✅ Lint: Should pass - Black formatting applied
- ✅ Schema-sync: Should pass - Schema files match expected state
- ✅ PR Gate: Should pass once above checks pass
