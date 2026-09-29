# Database Field Naming Conventions — 数据库字段命名约定

[English](#english) | [中文](#中文)

---

## English

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

## Using Boolean Values in Code

When writing SQL queries with boolean values, use the helper functions from `app/repositories/database.py`:

```python
from app.repositories.database import adapt_boolean_value, adapt_boolean_condition

# For INSERT/UPDATE values
is_active_val = adapt_boolean_value(True)  # PostgreSQL: True, SQLite: 1

# For WHERE conditions
condition = adapt_boolean_condition("is_active", True)  # PostgreSQL: "(is_active)::int != 0", SQLite: "is_active = 1"
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

The `adapt_sql()` function also handles placeholder conversion (`?` → `%s`) automatically.

## Schema Validation Workflow

When adding new database fields:

1. **Check the naming pattern** - Use boolean patterns for flags, counter patterns for counts
2. **Use correct type** - PostgreSQL: `BOOLEAN DEFAULT true/false`, SQLite: `INTEGER DEFAULT 0/1` (with comment)
3. **Update validate_schema.py** - If using a new pattern, add it to `BOOLEAN_FIELD_PATTERNS` or `COUNT_FIELD_PATTERNS`
4. **Run validation** - Execute `python3 scripts/validate_schema.py` to verify

The `scripts/validate_schema.py` script automatically checks the schema for:

- Boolean fields incorrectly using `integer DEFAULT 0/1` in PostgreSQL
- Proper type definitions for known patterns

Run before committing:
```bash
python3 scripts/validate_schema.py
```

The pre-commit hook also runs this validation automatically when `schema/schema-postgres.sql` is modified.

## Migrations

Migration authoring rules (MIG001–MIG003) and the full migration workflow live in [SCHEMA_MIGRATION_GUIDE.md](SCHEMA_MIGRATION_GUIDE.md).

## Related Files

- `scripts/generate_schema.py` - Schema generation with boolean detection
- `scripts/validate_schema.py` - Schema validation for boolean consistency
- `app/repositories/database.py` - `adapt_boolean_value()` and `adapt_boolean_condition()` helpers
- `.pre-commit-config.yaml` - Automatic validation hooks

---

## 中文

本文档定义数据库字段的命名约定，以确保在 PostgreSQL 和 SQLite 数据库中的类型一致性和正确处理。

## 布尔字段

PostgreSQL 中的布尔字段应使用 `BOOLEAN` 类型并设置 `DEFAULT true/false`。SQLite 使用 `INTEGER` 类型亲和性（0/1），但语义上是布尔值。

### 布尔字段命名模式

命名布尔字段时，使用以下模式以确保它们能被正确检测和处理：

| 模式 | 示例 | 说明 |
|------|------|------|
| `is_*` | `is_admin`、`is_active`、`is_published`、`is_public`、`is_featured` | 状态标志 |
| `*_enabled` | `email_enabled`、`push_enabled`、`content_filter_enabled` | 功能开关 |
| `allow_*` | `allow_comments`、`allow_copy` | 权限标志 |
| `must_*` | `must_change_password` | 必须动作标志 |
| `can_*` | `can_edit`、`can_delete`（未来） | 能力标志 |
| `has_*` | `has_permission`、`has_access`（未来） | 拥有标志 |

### 天然布尔词

有些词即使没有前缀/后缀模式，语义上也是布尔值：

- `read` - 已读状态（如 alerts.read）
- `success` - 操作是否成功（如 audit_logs.success）
- `acknowledged` - 确认状态
- `verified`、`confirmed`、`approved`、`rejected`、`completed` - 状态指示

### 正确的布尔字段定义

**PostgreSQL：**
```sql
is_admin boolean DEFAULT false,
is_active boolean DEFAULT true,
must_change_password boolean DEFAULT false,
read boolean DEFAULT false,
```

**SQLite：**
```sql
is_admin integer DEFAULT 0,  -- boolean: admin status
is_active integer DEFAULT 1,  -- boolean: active status
must_change_password integer DEFAULT 0,  -- boolean: password change required
read integer DEFAULT 0,  -- boolean: read status (0=unread, 1=read)
```

## 计数字段

计数字段应使用 `INTEGER` 类型，且不得被当作布尔值处理。

### 计数字段命名模式

| 模式 | 示例 | 说明 |
|------|------|------|
| `*_count` | `view_count`、`use_count`、`message_count` | 计数器 |
| `*_used` | `tokens_used`、`requests_used` | 用量计数 |
| `*_made` | `requests_made` | 动作计数 |
| `*_limit` | `daily_token_limit`、`monthly_token_limit` | 限额 |
| `*_quota` | `monthly_token_quota` | 配额值 |
| `total_*` | `total_tokens`、`total_requests`、`total_sessions` | 合计 |
| `*_tokens` | `input_tokens`、`output_tokens`、`cache_tokens` | 令牌数 |
| `*_users` | `active_users`、`new_users` | 用户数 |
| `*_seconds` | `total_duration_seconds` | 时长 |
| `*_requests` | `total_requests` | 请求数 |

### 正确的计数字段定义

**PostgreSQL 与 SQLite 通用：**
```sql
view_count integer DEFAULT 0,
tokens_used integer DEFAULT 0,
total_tokens integer DEFAULT 0,
message_count integer DEFAULT 0,
```

## 代码中布尔值的用法

在 SQL 查询中写布尔值时，使用 `app/repositories/database.py` 中的助手函数：

```python
from app.repositories.database import adapt_boolean_value, adapt_boolean_condition

# For INSERT/UPDATE values
is_active_val = adapt_boolean_value(True)  # PostgreSQL: True, SQLite: 1

# For WHERE conditions
condition = adapt_boolean_condition("is_active", True)  # PostgreSQL: "(is_active)::int != 0", SQLite: "is_active = 1"
```

### 避免直接的整数比较

**不要这样写：**
```python
# Bad - won't work with PostgreSQL BOOLEAN
cursor.execute("UPDATE users SET must_change_password = 0 WHERE id = ?", (user_id,))
cursor.execute("SELECT * FROM alerts WHERE read = 0")
```

**应当这样写：**
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

`adapt_sql()` 同时自动处理占位符转换（`?` → `%s`）。

## 模式校验流程

新增数据库字段时：

1. **核对命名模式** - 标志用布尔模式，计数用计数模式
2. **使用正确类型** - PostgreSQL：`BOOLEAN DEFAULT true/false`；SQLite：`INTEGER DEFAULT 0/1`（加注释）
3. **更新 validate_schema.py** - 使用新模式时，加入 `BOOLEAN_FIELD_PATTERNS` 或 `COUNT_FIELD_PATTERNS`
4. **运行校验** - 执行 `python3 scripts/validate_schema.py` 验证

`scripts/validate_schema.py` 会自动检查：

- PostgreSQL 中误用 `integer DEFAULT 0/1` 的布尔字段
- 已知模式的类型定义是否正确

提交前运行：
```bash
python3 scripts/validate_schema.py
```

修改 `schema/schema-postgres.sql` 时，pre-commit 钩子也会自动运行该校验。

## 迁移

迁移编写铁律（MIG001–MIG003）与完整迁移流程见 [SCHEMA_MIGRATION_GUIDE.md](SCHEMA_MIGRATION_GUIDE.md)。

## 相关文件

- `scripts/generate_schema.py` - 带布尔检测的模式生成
- `scripts/validate_schema.py` - 布尔一致性模式校验
- `app/repositories/database.py` - `adapt_boolean_value()` 与 `adapt_boolean_condition()` 助手
- `.pre-commit-config.yaml` - 自动校验钩子
