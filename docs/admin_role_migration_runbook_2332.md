# Issue #2332: Legacy Admin Role Migration Runbook

## Overview

This document describes the migration process for enforcing strict admin role semantics
as part of Issue #2332. The migration ensures that legacy `admin` accounts are properly
classified and that the system no longer accepts legacy `admin` role for platform admin
operations when strict mode is enabled.

## Migration Semantics

### Classification Rules

1. **Tenant Admins**: Users with `role='admin'` AND `tenant_id IS NOT NULL`
   - Migrated to `role='tenant_admin'`
   - Must reference an active, non-deleted tenant
   - Orphan tenant references cause migration failure

2. **Platform Admins**: Users with `role='admin'` AND `tenant_id IS NULL`
   - Only migrated if proven to be initial platform admin
   - Proof methods (precedence):
     1. `OPENACE_INITIAL_PLATFORM_ADMINS` environment variable (comma-separated usernames)
     2. Config file `config/migration_initial_admins.yaml`
     3. Heuristic: `user.id = 1` (first created account)

3. **Ambiguous Accounts**: Legacy admin accounts that don't match any proof criteria
   - **Migration fails** with clear error message
   - Requires manual intervention before proceeding

## Pre-Migration Checklist

- [ ] Review migration dry-run output
- [ ] Identify accounts requiring manual intervention
- [ ] Set `OPENACE_INITIAL_PLATFORM_ADMINS` environment variable if needed
- [ ] Create database backup
- [ ] Communicate maintenance window to affected users
- [ ] Verify monitoring dashboards are ready
- [ ] Stage rollback procedures

## Migration Steps

### 1. Dry Run (Required)

```bash
# Run preflight validation without making changes
python -c "
import importlib
migration_module = importlib.import_module(
    'migrations.versions.20260810_001_enforce_admin_role_migration'
)

# Dry-run output will show:
# - Accounts to be classified
# - Accounts requiring manual intervention
# - Session invalidation count
"
```

### 2. Set Environment Variable (Optional)

If you have known initial platform admins:

```bash
export OPENACE_INITIAL_PLATFORM_ADMINS="admin,sysadmin"
```

### 3. Run Migration

```bash
# For Alembic-managed environments
alembic upgrade +1

# Or for direct execution
python -m migrations.versions.20260810_001_enforce_admin_role_migration
```

### 4. Verify Migration Success

```sql
-- Verify no legacy admin accounts remain
SELECT COUNT(*) FROM users WHERE role = 'admin';
-- Expected: 0

-- Verify tenant admins have valid tenant
SELECT u.id, u.username, u.tenant_id
FROM users u
WHERE u.role = 'tenant_admin'
  AND u.tenant_id IS NOT NULL
  AND NOT EXISTS (
      SELECT 1 FROM tenants t
      WHERE t.id = u.tenant_id
        AND t.is_active = true
        AND t.deleted_at IS NULL
  );
-- Expected: 0 rows
```

### 5. Enable Strict Mode

```bash
# Set environment variable
export OPENACE_PLATFORM_ADMIN_STRICT_MODE=true

# Restart application processes
# All processes must be restarted to pick up new value
systemctl restart openace
# or
docker-compose restart
```

### 6. Verify Strict Mode Active

Check application logs for:
```
Platform admin strict mode: ENABLED
```

## Rollback Procedure

If migration fails or issues are discovered post-deployment:

```bash
# Rollback migration
alembic downgrade -1

# Or manual rollback
python -c "
import importlib
import sqlalchemy as sa
from app.repositories.database import get_connection

migration_module = importlib.import_module(
    'migrations.versions.20260810_001_enforce_admin_role_migration'
)

conn = get_connection()
migration_module.downgrade()
"
```

## Verification Post-Rollback

```sql
-- Verify roles restored
SELECT role, COUNT(*) FROM users
WHERE role IN ('admin', 'platform_admin', 'tenant_admin')
GROUP BY role;

-- Verify constraints removed (PostgreSQL)
SELECT conname FROM pg_constraint
WHERE conname LIKE 'chk_2332_%';

-- Should return no rows
```

## Mixed-Version Deployment

During rolling deployment with strict mode enabled:

1. **Before Migration**: All processes run with legacy-tolerant code
2. **During Migration**: Database updated, processes still use legacy-tolerant code
3. **After Restart**: New processes use strict mode

**Important**: All processes must complete restart before strict mode takes effect.

## Monitoring and Alerting

### Key Metrics

- **403 Error Rate**: Increase may indicate strict mode rejecting legitimate users
- **Migration Execution Time**: Should complete within 60 seconds
- **Active Sessions by Role**: Verify session distribution matches expectations

### Alerting Thresholds

- 403 error rate > 50% baseline → Alert and investigate
- Migration execution time > 60s → Alert (may indicate lock contention)
- Constraint violation in logs → Alert (attempt to create legacy admin)

## Common Issues

### Issue: Ambiguous Account Migration Failure

**Error**: "User X has no tenant_id and is not proven initial platform admin"

**Resolution**:
1. Add username to `OPENACE_INITIAL_PLATFORM_ADMINS`
2. OR manually update role before migration:
   ```sql
   UPDATE users SET role = 'platform_admin' WHERE username = 'X';
   ```

### Issue: Orphan Tenant Reference

**Error**: "User X has tenant_id Y which doesn't exist or is inactive"

**Resolution**:
1. Verify tenant status:
   ```sql
   SELECT * FROM tenants WHERE id = Y;
   ```
2. If tenant was deleted, set user tenant_id to NULL and reclassify manually
3. If tenant is inactive, decide appropriate action (reactivate or reclassify user)

### Issue: Sessions Invalidated Unexpectedly

**Error**: Users logged out after migration

**Resolution**: This is expected behavior. Users with legacy admin role must re-authenticate.
Communicate this in advance to affected users.

## Support

For issues or questions:
- GitHub Issue: #2332
- Related Issues: #2179, #2286, #2327

## References

- Implementation Plan: See `docs/issues/2332-implementation-plan.md`
- API Documentation: See `docs/api_permission_matrix.md`
- Audit Tool: `scripts/audit_admin_role_usage.py`