# Open-ACE 数据库字段命名约定

本文档定义数据库字段的命名约定，以确保在 PostgreSQL 和 SQLite 数据库中的类型一致性和正确处理。

## 布尔字段

PostgreSQL 中的布尔字段应使用 `BOOLEAN` 类型并设置 `DEFAULT true/false`。SQLite 使用 `INTEGER` 类型亲和性（0/1），但语义上是布尔值。

### 布尔字段命名模式

命名布尔字段时，使用以下模式以确保它们能被正确检测和处理：

| 模式 | 示例 | 说明 |
|------|------|------|
| `is_*` | `is_admin`, `is_active`, `is_published`, `is_public`, `is_featured` | 状态标志 |
| `*_enabled` | `email_enabled`, `push_enabled`, `content_filter_enabled` | 功能开关 |
| `allow_*` | `allow_comments`, `allow_copy` | 权限标志 |
| `must_*` | `must_change_password` | 必须操作标志 |
| `can_*` | `can_edit`, `can_delete`（未来） | 能力标志 |
| `has_*` | `has_permission`, `has_access`（未来） | 所有权标志 |

### 特殊布尔词

某些词即使没有前缀/后缀模式，本身也是布尔含义：

- `read` - 表示已读状态（如 alerts.read）
- `success` - 表示操作成功（如 audit_logs.success）
- `acknowledged` - 表示确认状态
- `verified`、`confirmed`、`approved`、`rejected`、`completed` - 状态指示器

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

## 计数器字段

计数器字段应使用 `INTEGER` 类型，不应被转换为布尔值。

### 计数器字段命名模式

| 模式 | 示例 | 说明 |
|------|------|------|
| `*_count` | `view_count`, `use_count`, `message_count` | 计数器 |
| `*_used` | `tokens_used`, `requests_used` | 使用计数器 |
| `*_made` | `requests_made` | 操作计数器 |
| `*_limit` | `daily_token_limit`, `monthly_token_limit` | 限制值 |
| `*_quota` | `monthly_token_quota` | 配额值 |
| `total_*` | `total_tokens`, `total_requests`, `total_sessions` | 总计 |
| `*_tokens` | `input_tokens`, `output_tokens`, `cache_tokens` | Token 计数 |
| `*_users` | `active_users`, `new_users` | 用户计数 |
| `*_seconds` | `total_duration_seconds` | 时长 |
| `*_requests` | `total_requests` | 请求数 |

### 正确的计数器字段定义

**PostgreSQL 和 SQLite 通用：**
```sql
view_count integer DEFAULT 0,
tokens_used integer DEFAULT 0,
total_tokens integer DEFAULT 0,
message_count integer DEFAULT 0,
```

## 代码指南

### 在 SQL 中使用布尔值

在编写包含布尔值的 SQL 查询时，使用 `app/repositories/database.py` 中的辅助函数：

```python
from app.repositories.database import adapt_boolean_value, adapt_boolean_condition

# 用于 INSERT/UPDATE 值
is_active_val = adapt_boolean_value(True)  # PostgreSQL: True, SQLite: 1

# 用于 WHERE 条件
condition = adapt_boolean_condition("is_active", True)  # PostgreSQL: "(is_active)::int != 0", SQLite: "is_active = 1"
```

### 避免直接使用整数比较

**不要这样写：**
```python
# 错误 - 在 PostgreSQL BOOLEAN 下不工作
cursor.execute("UPDATE users SET must_change_password = 0 WHERE id = ?", (user_id,))
cursor.execute("SELECT * FROM alerts WHERE read = 0")
```

**应该这样写：**
```python
# 正确 - 同时兼容 PostgreSQL 和 SQLite
cursor.execute(
    adapt_sql("UPDATE users SET must_change_password = ? WHERE id = ?"),
    (adapt_boolean_value(False), user_id)
)
cursor.execute(
    adapt_sql(f"SELECT * FROM alerts WHERE {adapt_boolean_condition('read', False)}")
)
```

## 添加新字段

添加新的数据库字段时：

1. **检查命名模式** - 布尔标志使用布尔模式，计数器使用计数器模式
2. **使用正确的类型** - PostgreSQL: `BOOLEAN DEFAULT true/false`，SQLite: `INTEGER DEFAULT 0/1`（加注释）
3. **更新 generate_schema.py** - 如果使用了新模式，将其添加到 `BOOLEAN_FIELD_PATTERNS` 或 `COUNT_FIELD_PATTERNS`
4. **运行验证** - 执行 `python3 scripts/validate_schema.py` 进行验证

## 验证

`scripts/validate_schema.py` 脚本会自动检查模式中的以下内容：

- PostgreSQL 中布尔字段错误地使用了 `integer DEFAULT 0/1`
- 已知模式的正确类型定义

提交前运行：
```bash
python3 scripts/validate_schema.py
```

pre-commit 钩子会在 `schema/schema-postgres.sql` 被修改时自动运行此验证。

## 迁移指南

创建添加布尔字段的迁移时：

```python
# PostgreSQL
op.execute("""
    ALTER TABLE my_table
    ADD COLUMN is_enabled BOOLEAN DEFAULT FALSE
""")

# SQLite 使用 BOOLEAN 类型（实际存储为 INTEGER）
op.add_column(
    "my_table",
    sa.Column("is_enabled", sa.Boolean(), server_default=sa.false())
)
```

## 相关文件

- `scripts/generate_schema.py` - 带布尔检测的模式生成
- `scripts/validate_schema.py` - 布尔一致性的模式验证
- `app/repositories/database.py` - `adapt_boolean_value()` 和 `adapt_boolean_condition()` 辅助函数
- `.pre-commit-config.yaml` - 自动验证钩子
