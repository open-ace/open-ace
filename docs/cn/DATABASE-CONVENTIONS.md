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

## 迁移编写规则

仓库级的两条迁移约束由 `scripts/lint/check_migration_rules.py` 自动强制执行。
它们很容易被忽略，且仅靠本地单元测试难以发现（Issue #1704），因为故障只在
迁移从合成的预合并树（CI）加载、或在 PostgreSQL 上运行（默认 CI 任务没有 PG
服务）时才会暴露。这两条规则同时由 pre-commit 钩子和 `Migration Graph` CI
工作流强制执行。

### MIG001 —— 迁移禁止导入 `app.*` 运行期模块

migration-graph CI 任务和 `ScriptDirectory.get_heads()` 会从不包含 `app/` 包的
合成预合并树加载每个迁移模块。因此，执行 `from app.xxx import ...` 的迁移在
那里会导入失败，并以晦涩的 `ImportError` 破坏单头检查——而此时所有本地测试
仍然通过。

**规则：** `migrations/versions/` 下的迁移文件不得导入 `app` 或任何 `app.*`
子模块。只能通过 `alembic.op`、`sqlalchemy`、模式内省查询
（`information_schema` / `sqlite_master`）以及兄弟模块 `migrations.baseline`
来操作。唯一的例外是被 `if TYPE_CHECKING:` 守卫的导入——它在导入期不会执行，
因此不会破坏模块加载。

### MIG002 —— PostgreSQL `CONCURRENTLY` 操作必须使用受批准的模式

`CREATE INDEX CONCURRENTLY` 不能在事务块内运行。在 PostgreSQL 上执行
`alembic upgrade` 时，错误用法会抛出 `ACTIVE SQL TRANSACTION`（或静默行为异常）。
受批准的模式只有**唯一一种**：

```python
def _is_postgresql() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    if _is_postgresql():
        with op.get_context().autocommit_block():          # <- 必需的包裹
            op.create_index(
                INDEX_NAME, TABLE, COLUMNS,
                postgresql_concurrently=True,              # <- 必需的参数
            )
    else:
        op.create_index(INDEX_NAME, TABLE, COLUMNS)        # SQLite：普通索引
```

`downgrade()` 与之对称：在各自的 `autocommit_block()` 内调用
`op.drop_index(..., postgresql_concurrently=True)`。检查器会拒绝两类错误：

- **通过原始 SQL 发出并发 DDL**：`op.execute(...)` / `conn.execute(...)` /
  `sa.text(...)` 的字符串字面量包含 `CONCURRENTLY`。原始 SQL 绕过了 Alembic 的
  autocommit 处理。检查器匹配由带 `CONCURRENTLY` 的 DDL 动词
  （`CREATE`/`DROP`/`REINDEX`/`REFRESH`，覆盖 `CREATE/DROP INDEX`、`REINDEX` 与
  `REFRESH MATERIALIZED VIEW`）引导的语句；应改用（按上文包裹的）
  `op.create_index`/`op.drop_index`。`REFRESH MATERIALIZED VIEW CONCURRENTLY`
  没有 Alembic helper——若确需使用，请在 Alembic 之外（如部署后脚本）运行，不要写进迁移。
- **`postgresql_concurrently=True` 不在 `autocommit_block()` 内**。该参数正是
  发出 `... CONCURRENTLY` 的开关；它仅在事务外有效，因此调用必须在词法上嵌套在
  `with op.get_context().autocommit_block():` 语句内（将 `op.create_index` 调用
  直接内联到 `with` 下，不要委托给同级辅助函数）。

### 运行检查

```bash
# 检查已提交的 migrations/versions/ 树
python3 scripts/lint/check_migration_rules.py

# 检查其他目录（例如合成的预合并树）
python3 scripts/lint/check_migration_rules.py /path/to/migrations/versions
```

pre-commit 钩子 `check-migration-rules` 会在每次改动 `migrations/versions/*.py`
的提交时运行此检查；`Migration Graph` CI 工作流则针对预合并树运行。两者在违规时
均以非零退出码结束，并打印 `file:line: MIGxx ...` 消息。

## 相关文件

- `scripts/generate_schema.py` - 带布尔检测的模式生成
- `scripts/validate_schema.py` - 布尔一致性的模式验证
- `scripts/lint/check_migration_rules.py` - 迁移编写规则（MIG001/MIG002）
- `app/repositories/database.py` - `adapt_boolean_value()` 和 `adapt_boolean_condition()` 辅助函数
- `.pre-commit-config.yaml` - 自动验证钩子
