# Schema Migration Guide

> Issue: #2190

This document explains Open ACE's schema migration strategy, best practices,
and troubleshooting.

## The Schema Authority Model

Open ACE adopts the authority model of **Alembic as the only channel for schema
changes**:

```
┌─────────────────────────────────────────────────────────────┐
│              Schema change authority model                   │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  Production: alembic upgrade head is the ONLY schema        │
│              change channel                                 │
│                                                             │
│  Development: SQLite bootstrap is allowed, but must stay    │
│              separated from the production path             │
│                                                             │
│  Forbidden: create_app() running DDL against PostgreSQL     │
│             runtime auto-adding columns / ALTER TABLE       │
│             business code repairing schema on missing cols  │
└─────────────────────────────────────────────────────────────┘
```

## Preparation Before Migrating

### 1. Check the current database version

```bash
alembic current
```

Example output:
```
20260801_001_add_platform_tenant_admin_roles (head)
```

### 2. Review pending migrations

```bash
alembic history --verbose
```

### 3. Back up the database

```bash
# PostgreSQL
pg_dump -h localhost -U open-ace ace > backup_$(date +%Y%m%d).sql

# SQLite
cp open-ace.db open-ace.db.backup
```

## Running Migrations

### Standard upgrade flow

```bash
# 1. Check the current version
alembic current

# 2. Run the upgrade
alembic upgrade head

# 3. Verify the upgrade succeeded
alembic current  # should print the latest revision

# 4. Verify schema integrity
python3 scripts/verify_schema_integrity.py
```

### Downgrade (rollback)

```bash
# Roll back one revision
alembic downgrade -1

# Roll back to a specific revision
alembic downgrade <revision_id>

# Roll back all migrations (database structure kept)
alembic downgrade base
```

**Note**: downgrades drop columns and may lose data. Always back up before
downgrading in production.

## Migration Strategies

### Expand/Contract pattern

For rolling-upgrade scenarios, use the expand/contract migration pattern:

#### Phase 1: Expand

Add nullable columns or columns with defaults:

```python
# migration: add_column_expand.py
def upgrade():
    op.add_column(
        "users",
        sa.Column("new_field", sa.Text(), nullable=True),  # nullable
    )
```

**Properties**:
- New applications can use the new column
- Old applications ignore it and are unaffected
- New and old versions can coexist

#### Phase 2: Steady state

All pods upgrade to the new version and the new column is used normally.

#### Phase 3: Contract (optional)

Once rollback is no longer required, add constraints or drop old columns:

```python
# migration: add_column_constraint.py
def upgrade():
    # Add a NOT NULL constraint
    op.alter_column(
        "users",
        "new_field",
        nullable=False,
        server_default="default_value",
    )
```

### Compatibility window

How application releases map to schema versions:

| App version | Minimum upgrade starting point | Required at runtime |
|-------------|--------------------------------|---------------------|
| v2.0.x      | `baseline_2026_06_23`          | Alembic head shipped with the release |
| v1.2.x      | `baseline_2026_06_23`          | Alembic head shipped with the release |

Databases on a pre-baseline revision (e.g. a historical hash from before
v1.2.0) cannot be upgraded in place; there is no supported migration path from
them.

**Decision logic**:
- Install / upgrade: `scripts/check_min_revision.py` runs before
  `alembic upgrade head` and accepts any revision in the baseline lineage, so
  the upgrade can apply the missing migrations. A pre-baseline revision fails
  the install with an error.
- Web and scheduler startup: `check_schema_compatibility()`
  (`app/repositories/schema_guard.py`) requires the fully migrated head, so
  services never run on a partially migrated schema.

## Forbidden Operations

### ❌ No runtime DDL in production

```python
# Wrong (forbidden)
@app.before_request
def ensure_columns():
    # Do NOT run ALTER TABLE at runtime
    cursor.execute("ALTER TABLE users ADD COLUMN ...")
```

### ❌ Business code must not modify the schema

```python
# Wrong
def create_session():
    try:
        cursor.execute("INSERT INTO sessions ...")
    except ColumnMissingError:
        # Do not try to repair the schema
        cursor.execute("ALTER TABLE sessions ADD COLUMN ...")
        cursor.execute("INSERT INTO sessions ...")
```

### ✅ The correct approach

When a missing column is discovered:
1. Stop the application
2. Run the migration
3. Restart the application

## Troubleshooting

### Migration execution failure

**Symptom**: `alembic upgrade head` errors out

**Diagnosis steps**:

```bash
# 1. Check the database connection
pg_isready -h <host> -p <port>

# 2. Check the current version
alembic current

# 3. Check the migration history
alembic history

# 4. Inspect the detailed error
alembic upgrade head --sql
```

**Common errors and fixes**:

| Error | Cause | Fix |
|-------|-------|-----|
| `Can't locate revision` | broken migration chain | check `down_revision` values |
| `relation "xxx" already exists` | migration re-executed | use idempotency checks |
| `column "xxx" of relation "xxx" does not exist` | column not added | check migration ordering |

### Schema version mismatch

**Symptom**: application startup fails reporting the schema version is too old

**Fix**:

```bash
# 1. Check the current version
alembic current

# 2. Run the upgrade
alembic upgrade head

# 3. For a fresh database, run full initialization
alembic upgrade head
python3 scripts/init_db.py  # create default users
```

### Concurrent migration conflicts across pods

**Symptom**: multiple pods start at once and migrations hit lock waits or
deadlocks

**Fix**:

#### Option 1: Kubernetes init container

```yaml
initContainers:
- name: run-migrations
  image: open-ace:latest
  command: ["alembic", "upgrade", "head"]
  env:
  - name: DATABASE_URL
    valueFrom:
      secretKeyRef:
        name: db-credentials
        key: url
```

#### Option 2: a dedicated migration job

```yaml
# Run the migration job before deploying the application
apiVersion: batch/v1
kind: Job
metadata:
  name: schema-migration
spec:
  template:
    spec:
      containers:
      - name: migrator
        image: open-ace:latest
        command: ["alembic", "upgrade", "head"]
```

## /readyz Endpoint

The application exposes `/readyz` for readiness checks:

```bash
curl http://localhost:19888/readyz
```

**Example response**:

```json
{
  "status": "ready",
  "checks": {
    "database": {"status": "ok"},
    "schema_version": {
      "status": "ok",
      "compatible": true,
      "current": "20260801_001",
      "required": "baseline_2026_06_23"
    },
    "background_services": {"status": "ok"}
  }
}
```

**Status codes**:
- `200 OK`: all checks passed, service is ready
- `503 Service Unavailable`: a check failed and needs fixing

**Note**: `/readyz` reports status only — it never repairs schema problems.

## Production audit

Before applying schema changes, audit all production instances:

```bash
python3 scripts/audit_production_schema.py --output audit_report.txt
```

**The report covers**:
- Current schema version
- Missing columns
- Migration priorities

## Best Practices

### 1. Writing migration files

```python
def upgrade():
    # ✅ Use conditional checks for idempotency
    inspector = sa.inspect(op.get_bind())
    existing_columns = {col["name"] for col in inspector.get_columns("users")}

    if "new_column" not in existing_columns:
        op.add_column("users", sa.Column("new_column", sa.Text()))

def downgrade():
    # ✅ Check the column exists before dropping
    inspector = sa.inspect(op.get_bind())
    existing_columns = {col["name"] for col in inspector.get_columns("users")}

    if "new_column" in existing_columns:
        op.drop_column("users", "new_column")
```

### 2. Testing migrations

```bash
# 1. Verify upgrade in a test environment
alembic upgrade head

# 2. Verify downgrade
alembic downgrade -1
alembic upgrade head

# 3. Verify idempotency (repeat execution)
alembic upgrade head
alembic upgrade head  # must not error
```

### 3. Code-review checklist

When a PR includes a migration, check:
- [ ] Both upgrade and downgrade paths are provided
- [ ] Conditional checks make it idempotent
- [ ] Whether the expand/contract pattern applies is stated
- [ ] Impact on rolling upgrades is noted
- [ ] The upgrade path from baseline is tested

## Related documentation

- [Deployment guide](DEPLOYMENT.md)
- [Database schema](DATABASE_SCHEMA.md)
- [Database conventions](DATABASE_CONVENTIONS.md)

## Getting help

If you hit a schema migration problem:
1. Collect the error logs
2. Capture the output of `alembic current`
3. Capture the output of `python3 scripts/audit_production_schema.py --json`
4. Open an issue with the above attached
