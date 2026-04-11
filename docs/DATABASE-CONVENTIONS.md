# Open-ACE Database Field Naming Conventions

This document defines the naming conventions for database fields to ensure consistency and proper type handling across PostgreSQL and SQLite databases.

## Boolean Fields

Boolean fields in PostgreSQL should use `BOOLEAN` type with `DEFAULT true/false`. SQLite uses `INTEGER` type affinity (0/1) but the semantics are boolean.

### Boolean Field Naming Patterns

When naming boolean fields, use the following patterns to ensure they are correctly detected and handled:

| Pattern | Examples | Description |
|---------|----------|-------------|
| `is_*` | `is_admin`, `is_active`, `is_published`, `is_public`, `is_featured` | Status flags |
| `*_enabled` | `email_enabled`, `push_enabled`, `content_filter_enabled` | Feature toggles |
| `allow_*` | `allow_comments`, `allow_copy` | Permission flags |
| `must_*` | `must_change_password` | Required action flags |
| `can_*` | `can_edit`, `can_delete` (future) | Capability flags |
| `has_*` | `has_permission`, `has_access` (future) | Ownership flags |

### Special Boolean Words

Some words are inherently boolean even without prefix/suffix patterns:

- `read` - Indicates read status (e.g., alerts.read)
- `success` - Indicates operation success (e.g., audit_logs.success)
- `acknowledged` - Indicates acknowledgment status
- `verified`, `confirmed`, `approved`, `rejected`, `completed` - Status indicators

### Correct Boolean Field Definitions

**PostgreSQL:**
```sql
is_admin boolean DEFAULT false,
is_active boolean DEFAULT true,
must_change_password boolean DEFAULT false,
read boolean DEFAULT false,
```

**SQLite:**
```sql
is_admin integer DEFAULT 0,  -- boolean: admin status
is_active integer DEFAULT 1,  -- boolean: active status
must_change_password integer DEFAULT 0,  -- boolean: password change required
read integer DEFAULT 0,  -- boolean: read status (0=unread, 1=read)
```

## Counter Fields

Counter fields should use `INTEGER` type and should NOT be converted to boolean.

### Counter Field Naming Patterns

| Pattern | Examples | Description |
|---------|----------|-------------|
| `*_count` | `view_count`, `use_count`, `message_count` | Counters |
| `*_used` | `tokens_used`, `requests_used` | Usage counters |
| `*_made` | `requests_made` | Action counters |
| `*_limit` | `daily_token_limit`, `monthly_token_limit` | Limits |
| `*_quota` | `monthly_token_quota` | Quota values |
| `total_*` | `total_tokens`, `total_requests`, `total_sessions` | Totals |
| `*_tokens` | `input_tokens`, `output_tokens`, `cache_tokens` | Token counts |
| `*_users` | `active_users`, `new_users` | User counts |
| `*_seconds` | `total_duration_seconds` | Duration |
| `*_requests` | `total_requests` | Request counts |

### Correct Counter Field Definitions

**Both PostgreSQL and SQLite:**
```sql
view_count integer DEFAULT 0,
tokens_used integer DEFAULT 0,
total_tokens integer DEFAULT 0,
message_count integer DEFAULT 0,
```

## Code Guidelines

### Using Boolean Values in SQL

When writing SQL queries with boolean values, use the helper functions from `app/repositories/database.py`:

```python
from app.repositories.database import adapt_boolean_value, adapt_boolean_condition

# For INSERT/UPDATE values
is_active_val = adapt_boolean_value(True)  # PostgreSQL: True, SQLite: 1

# For WHERE conditions
condition = adapt_boolean_condition("is_active", True)  # PostgreSQL: "is_active IS TRUE", SQLite: "is_active = 1"
```

### Avoid Direct Integer Comparisons

**Don't use:**
```python
# Bad - won't work with PostgreSQL BOOLEAN
cursor.execute("UPDATE users SET must_change_password = 0 WHERE id = ?", (user_id,))
cursor.execute("SELECT * FROM alerts WHERE read = 0")
```

**Use instead:**
```python
# Good - works with both PostgreSQL and SQLite
cursor.execute(
    adapt_sql("UPDATE users SET must_change_password = ? WHERE id = ?"),
    (adapt_boolean_value(False), user_id)
)
cursor.execute(
    adapt_sql(f"SELECT * FROM alerts WHERE {adapt_boolean_condition('read', False)}")
)
```

## Adding New Fields

When adding new database fields:

1. **Check the naming pattern** - Use boolean patterns for flags, counter patterns for counts
2. **Use correct type** - PostgreSQL: `BOOLEAN DEFAULT true/false`, SQLite: `INTEGER DEFAULT 0/1` (with comment)
3. **Update generate_schema.py** - If using a new pattern, add it to `BOOLEAN_FIELD_PATTERNS` or `COUNT_FIELD_PATTERNS`
4. **Run validation** - Execute `python3 scripts/validate_schema.py` to verify

## Validation

The `scripts/validate_schema.py` script automatically checks the schema for:

- Boolean fields incorrectly using `integer DEFAULT 0/1` in PostgreSQL
- Proper type definitions for known patterns

Run before committing:
```bash
python3 scripts/validate_schema.py
```

The pre-commit hook also runs this validation automatically when `schema/schema-postgres.sql` is modified.

## Migration Guidelines

When creating migrations that add boolean fields:

```python
# PostgreSQL
op.execute("""
    ALTER TABLE my_table
    ADD COLUMN is_enabled BOOLEAN DEFAULT FALSE
""")

# SQLite uses BOOLEAN type (stored as INTEGER)
op.add_column(
    "my_table",
    sa.Column("is_enabled", sa.Boolean(), server_default=sa.false())
)
```

## Related Files

- `scripts/generate_schema.py` - Schema generation with boolean detection
- `scripts/validate_schema.py` - Schema validation for boolean consistency
- `app/repositories/database.py` - `adapt_boolean_value()` and `adapt_boolean_condition()` helpers
- `.pre-commit-config.yaml` - Automatic validation hook