# Schema Migration Guide — Schema 迁移指南

[English](#english) | [中文](#中文)

---

## English

> Issue: #2190

This document explains Open ACE's schema migration strategy, authoring rules,
best practices, and troubleshooting.

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

See also [../guide/DATABASE_BACKUP.md](../guide/DATABASE_BACKUP.md) for the
full backup/restore procedures (Kubernetes CronJob and Docker Compose).

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

## Migration Authoring Rules (MIG001–MIG003)

Three repository-level migration constraints are enforced automatically by
`scripts/lint/check_migration_rules.py`. They are easy to miss and hard to infer
from local unit tests alone (Issue #1704), because the failure only surfaces
when migrations load from a synthetic pre-merged tree (CI) or run against
PostgreSQL (no PG service in the default CI job). All three rules are enforced
by a pre-commit hook and by the Migration Graph CI workflow
([`.github/workflows/migration-graph.yml`](../../.github/workflows/migration-graph.yml)),
which assembles the pre-merged tree (base branch + PR) and checks it before
merge.

### MIG001 — Migrations must not import `app.*` runtime modules

The migration-graph CI job and `ScriptDirectory.get_heads()` load each migration
module from a synthetic pre-merged tree that does **not** contain the `app/`
package. A migration that does `from app.xxx import ...` therefore fails to
import there, breaking the single-head check with an opaque `ImportError` —
even though every local test passes.

**Rule:** migration files under `migrations/versions/` must not import `app` or
any `app.*` submodule. Operate via `alembic.op`, `sqlalchemy`, schema
introspection queries (`information_schema` / `sqlite_master`), and the sibling
`migrations.baseline` helper only. The only exception is an import guarded by
`if TYPE_CHECKING:`, which is never executed at import time and so cannot break
module loading.

### MIG002 — PostgreSQL `CONCURRENTLY` operations must use the approved pattern

`CREATE INDEX CONCURRENTLY` cannot run inside a transaction block. Issuing it
the wrong way raises `ACTIVE SQL TRANSACTION` (or silently misbehaves) during
`alembic upgrade` on PostgreSQL. There is exactly **one** approved pattern:

```python
def _is_postgresql() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    if _is_postgresql():
        with op.get_context().autocommit_block():          # <- required wrapper
            op.create_index(
                INDEX_NAME, TABLE, COLUMNS,
                postgresql_concurrently=True,              # <- required kwarg
            )
    else:
        op.create_index(INDEX_NAME, TABLE, COLUMNS)        # SQLite: plain index
```

The `downgrade()` mirrors this with `op.drop_index(..., postgresql_concurrently=True)`
inside its own `autocommit_block()`. The check rejects two mistakes:

- **Raw concurrent DDL** via `op.execute(...)` / `conn.execute(...)` /
  `sa.text(...)` with a string literal containing `CONCURRENTLY`. Raw SQL bypasses
  Alembic's autocommit handling. The check matches any statement led by a
  `CONCURRENTLY`-bearing DDL verb (`CREATE`/`DROP`/`REINDEX`/`REFRESH`, covering
  `CREATE/DROP INDEX`, `REINDEX`, and `REFRESH MATERIALIZED VIEW`); use
  `op.create_index`/`op.drop_index` (wrapped as above) instead. `REFRESH
  MATERIALIZED VIEW CONCURRENTLY` has no Alembic helper — if you genuinely need
  it, run it outside Alembic (e.g. a post-deploy script), not in a migration.
- **`postgresql_concurrently=True` outside an `autocommit_block()`**. The kwarg
  is what issues `... CONCURRENTLY`; it is only valid outside a transaction, so
  the call must be lexically nested inside the `with op.get_context().autocommit_block():`
  statement (inline the `op.create_index` call under the `with`, do not delegate
  to a sibling helper).

### MIG003 — A released revision id must never disappear

Alembic keys history off the `revision` string, not the filename: every deployed
database stores it verbatim in `alembic_version.version_num`. Delete or rewrite
an id that is already reachable from the base branch and every database stamped
with it dies on the next `upgrade head` with `Can't locate revision identified
by '<id>'` — and stays stuck for every later migration too.

This is not hypothetical: `20260731_003_add_proxy_token_terminated_fields` was
renumbered to `20260731_004_...` so a newer migration could take the 003 slot,
wedging every database upgraded in that window. The recovery was
`migrations/versions/20260731_003_bridge_renamed_proxy_token_revision.py` — a
no-DDL node that exists only to keep the old id alive.

**Rule:** renaming the *file* is fine; only the id matters. If an id genuinely
has to stop carrying DDL, keep the node and empty its `upgrade()` instead of
deleting it. The check compares post-merge ids against the base branch (default
`origin/main`), which is precisely what a deployed database will experience.

### Running the checks

```bash
# Check the committed migrations/versions/ tree
python3 scripts/lint/check_migration_rules.py

# Check an alternate tree (e.g. a synthetic pre-merged tree)
python3 scripts/lint/check_migration_rules.py /path/to/migrations/versions
```

The pre-commit hook `check-migration-rules` runs this on every commit that
touches `migrations/versions/*.py`; the `Migration Graph` CI workflow
([`.github/workflows/migration-graph.yml`](../../.github/workflows/migration-graph.yml))
runs it against the pre-merged tree and additionally asserts a single Alembic
head (two PRs forking off the same parent migration create no git conflict but
fork the chain — only the pre-merge single-head check catches it). Both exit
non-zero with a `file:line: MIGxx ...` message on violation.

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
| `Can't locate revision` | broken migration chain | check `down_revision` values; if a released id was deleted, restore it (MIG003) |
| `relation "xxx" already exists` | migration re-executed | use idempotency checks |
| `column "xxx" of relation "xxx" does not exist` | column not added | check migration ordering |
| `ACTIVE SQL TRANSACTION` on a `CONCURRENTLY` index | raw DDL or missing `autocommit_block()` | use the MIG002 template |

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
- [ ] No `app.*` imports (MIG001)
- [ ] `CONCURRENTLY` indexes use the `autocommit_block()` template (MIG002)
- [ ] No previously released revision id was deleted or rewritten (MIG003)
- [ ] Whether the expand/contract pattern applies is stated
- [ ] Impact on rolling upgrades is noted
- [ ] The upgrade path from baseline is tested

## Related documentation

- [DATABASE_SCHEMA.md](DATABASE_SCHEMA.md) — database domain map (103 tables)
- [DATABASE_CONVENTIONS.md](DATABASE_CONVENTIONS.md) — field naming and type conventions
- [../guide/DATABASE_BACKUP.md](../guide/DATABASE_BACKUP.md) — backup before migrating
- [`scripts/lint/check_migration_rules.py`](../../scripts/lint/check_migration_rules.py) — MIG001–MIG003 enforcement
- [`.github/workflows/migration-graph.yml`](../../.github/workflows/migration-graph.yml) — Migration Graph CI workflow

## Getting help

If you hit a schema migration problem:
1. Collect the error logs
2. Capture the output of `alembic current`
3. Capture the output of `python3 scripts/audit_production_schema.py --json`
4. Open an issue with the above attached

---

## 中文

> Issue: #2190

本文档说明 Open ACE 的模式迁移策略、迁移编写铁律、最佳实践与排障方法。

## 模式权威模型

Open ACE 采用 **Alembic 作为模式变更唯一通道** 的权威模型：

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

## 迁移前准备

### 1. 查看当前数据库版本

```bash
alembic current
```

示例输出：
```
20260801_001_add_platform_tenant_admin_roles (head)
```

### 2. 查看待执行的迁移

```bash
alembic history --verbose
```

### 3. 备份数据库

```bash
# PostgreSQL
pg_dump -h localhost -U open-ace ace > backup_$(date +%Y%m%d).sql

# SQLite
cp open-ace.db open-ace.db.backup
```

完整备份/恢复流程（Kubernetes CronJob 与 Docker Compose）见
[../guide/DATABASE_BACKUP.md](../guide/DATABASE_BACKUP.md)。

## 执行迁移

### 标准升级流程

```bash
# 1. 查看当前版本
alembic current

# 2. 执行升级
alembic upgrade head

# 3. 确认升级成功
alembic current  # 应输出最新 revision

# 4. 校验模式完整性
python3 scripts/verify_schema_integrity.py
```

### 降级（回滚）

```bash
# 回滚一个 revision
alembic downgrade -1

# 回滚到指定 revision
alembic downgrade <revision_id>

# 回滚全部迁移（保留数据库结构）
alembic downgrade base
```

**注意**：降级会删列，可能丢数据。生产环境降级前务必备份。

## 迁移编写铁律（MIG001–MIG003）

三条仓库级迁移约束由 `scripts/lint/check_migration_rules.py` 自动执行。它们
很容易被漏掉，且难以从本地单测推断（Issue #1704）——失败只在迁移从合入前的
合成树加载（CI）或跑在 PostgreSQL 上（默认 CI 作业没有 PG 服务）时才暴露。
三条规则都由 pre-commit 钩子和 Migration Graph CI 工作流
（[`.github/workflows/migration-graph.yml`](../../.github/workflows/migration-graph.yml)）
强制执行：该工作流组装合入前树（基线分支 + PR）并在合并前检查。

### MIG001 — 迁移不得导入 `app.*` 运行时模块

migration-graph CI 作业和 `ScriptDirectory.get_heads()` 从一个**不含** `app/`
包的合成树加载每个迁移模块。因此 `from app.xxx import ...` 的迁移在那里会
导入失败，用一条晦涩的 `ImportError` 打断单头检查——即使所有本地测试都通过。

**规则：** `migrations/versions/` 下的迁移文件不得导入 `app` 或任何 `app.*`
子模块。只允许通过 `alembic.op`、`sqlalchemy`、模式内省查询
（`information_schema` / `sqlite_master`）以及同目录的 `migrations.baseline`
助手操作。唯一例外是用 `if TYPE_CHECKING:` 保护的导入——它在导入时不执行，
不会破坏模块加载。

### MIG002 — PostgreSQL `CONCURRENTLY` 操作必须使用批准模板

`CREATE INDEX CONCURRENTLY` 不能在事务块内运行。写错的话，在 PostgreSQL 上
执行 `alembic upgrade` 会抛 `ACTIVE SQL TRANSACTION`（或静默异常）。批准的
模板**有且只有一个**：

```python
def _is_postgresql() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    if _is_postgresql():
        with op.get_context().autocommit_block():          # <- required wrapper
            op.create_index(
                INDEX_NAME, TABLE, COLUMNS,
                postgresql_concurrently=True,              # <- required kwarg
            )
    else:
        op.create_index(INDEX_NAME, TABLE, COLUMNS)        # SQLite: plain index
```

`downgrade()` 与之镜像：在自己的 `autocommit_block()` 内执行
`op.drop_index(..., postgresql_concurrently=True)`。检查器拒绝两类错误：

- **裸并发 DDL**：通过 `op.execute(...)` / `conn.execute(...)` / `sa.text(...)`
  发出字符串字面量含 `CONCURRENTLY` 的语句。裸 SQL 绕过了 Alembic 的
  autocommit 处理。检查器匹配任何由含 `CONCURRENTLY` 的 DDL 动词
  （`CREATE`/`DROP`/`REINDEX`/`REFRESH`，覆盖 `CREATE/DROP INDEX`、`REINDEX`
  和 `REFRESH MATERIALIZED VIEW`）引导的语句；请改用
  `op.create_index`/`op.drop_index`（按上述方式包裹）。`REFRESH MATERIALIZED
  VIEW CONCURRENTLY` 没有 Alembic 助手——确实需要的话，在 Alembic 之外跑
  （例如部署后脚本），不要写进迁移。
- **`postgresql_concurrently=True` 出现在 `autocommit_block()` 之外**。该
  kwarg 正是发出 `... CONCURRENTLY` 的开关；它只在事务外合法，因此调用必须在
  `with op.get_context().autocommit_block():` 语句的词法嵌套内（把
  `op.create_index` 调用内联写在 `with` 之下，不要委托给兄弟函数）。

### MIG003 — 已发布的 revision id 不得消失

Alembic 以 `revision` 字符串而非文件名组织历史：每个已部署数据库都把它原样
存在 `alembic_version.version_num` 里。删掉或改写一个已可从基线分支到达的 id，
所有打过该 id 的数据库在下一次 `upgrade head` 时就会死于
`Can't locate revision identified by '<id>'` ——而且之后的每个迁移也会被卡住。

这不是假设：`20260731_003_add_proxy_token_terminated_fields` 曾被重编号为
`20260731_004_...`，好让一个更新的迁移占用 003 槽位，结果把该窗口内升级过的
每个数据库都卡死了。修复方式是
`migrations/versions/20260731_003_bridge_renamed_proxy_token_revision.py` ——
一个不带 DDL、只为保住旧 id 的节点。

**规则：** 重命名*文件*没问题；要紧的只有 id。如果某个 id 确实不再需要承载
DDL，保留节点并把它的 `upgrade()` 清空，而不是删除。检查器将合入后的 id 与
基线分支（默认 `origin/main`）对比——这正是已部署数据库将经历的事情。

### 运行检查

```bash
# Check the committed migrations/versions/ tree
python3 scripts/lint/check_migration_rules.py

# Check an alternate tree (e.g. a synthetic pre-merged tree)
python3 scripts/lint/check_migration_rules.py /path/to/migrations/versions
```

pre-commit 钩子 `check-migration-rules` 在每次触碰 `migrations/versions/*.py`
的提交上运行；`Migration Graph` CI 工作流
（[`.github/workflows/migration-graph.yml`](../../.github/workflows/migration-graph.yml)）
在合入前树上运行，并额外断言 Alembic 单头（两个 PR 从同一父迁移分叉不会产生
git 文本冲突，但会分叉迁移链——只有合入前的单头检查能抓住）。违反时二者都以
`file:line: MIGxx ...` 消息非零退出。

## 迁移策略

### Expand/Contract 模式

滚动升级场景使用扩展/收缩迁移模式：

#### 阶段 1：Expand

添加可空列或带默认值的列：

```python
# migration: add_column_expand.py
def upgrade():
    op.add_column(
        "users",
        sa.Column("new_field", sa.Text(), nullable=True),  # nullable
    )
```

**特性**：
- 新应用可以使用新列
- 旧应用忽略它，不受影响
- 新旧版本可以共存

#### 阶段 2：稳定态

所有 Pod 升级到新版本，新列正常使用。

#### 阶段 3：Contract（可选）

不再需要回滚后，添加约束或删除旧列：

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

### 兼容窗口

应用版本与 schema 版本的兼容关系：

| 应用版本 | 最低升级起点 | 运行时要求 |
|---------|-------------|-----------|
| v2.0.x  | `baseline_2026_06_23` | 该版本随附的 Alembic head |
| v1.2.x  | `baseline_2026_06_23` | 该版本随附的 Alembic head |

仍停留在基线之前 revision（如 v1.2.0 之前的历史 hash）的数据库不支持原地升级，没有受支持的迁移路径。

**判定逻辑**：
- 安装 / 升级：`scripts/check_min_revision.py` 在 `alembic upgrade head` 之前运行，接受基线谱系内的任意 revision，以便升级补齐缺失的迁移；基线之前的 revision 直接报错并中止安装。
- Web 与调度器启动：`check_schema_compatibility()`（`app/repositories/schema_guard.py`）要求已迁移到 head，确保服务不会在迁移未完成的 schema 上运行。

## 禁止操作

### ❌ 生产环境禁止运行时 DDL

```python
# Wrong (forbidden)
@app.before_request
def ensure_columns():
    # Do NOT run ALTER TABLE at runtime
    cursor.execute("ALTER TABLE users ADD COLUMN ...")
```

### ❌ 业务代码不得修改模式

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

### ✅ 正确做法

发现缺列时：
1. 停止应用
2. 执行迁移
3. 重启应用

## 排障

### 迁移执行失败

**症状**：`alembic upgrade head` 报错

**诊断步骤**：

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

**常见错误与修复**：

| 错误 | 原因 | 修复 |
|------|------|------|
| `Can't locate revision` | 迁移链断裂 | 检查 `down_revision`；若删除了已发布的 id，恢复它（MIG003） |
| `relation "xxx" already exists` | 迁移重复执行 | 使用幂等检查 |
| `column "xxx" of relation "xxx" does not exist` | 列未添加 | 检查迁移顺序 |
| `CONCURRENTLY` 索引报 `ACTIVE SQL TRANSACTION` | 裸 DDL 或缺少 `autocommit_block()` | 使用 MIG002 模板 |

### 模式版本不匹配

**症状**：应用启动失败，提示模式版本过旧

**修复**：

```bash
# 1. Check the current version
alembic current

# 2. Run the upgrade
alembic upgrade head

# 3. For a fresh database, run full initialization
alembic upgrade head
python3 scripts/init_db.py  # create default users
```

### 跨 Pod 并发迁移冲突

**症状**：多个 Pod 同时启动，迁移遇到锁等待或死锁

**修复**：

#### 选项 1：Kubernetes init 容器

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

#### 选项 2：专用迁移 Job

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

## /readyz 端点

应用暴露 `/readyz` 用于就绪检查：

```bash
curl http://localhost:19888/readyz
```

**示例响应**：

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

**状态码**：
- `200 OK`：全部检查通过，服务就绪
- `503 Service Unavailable`：有检查失败，需要修复

**注意**：`/readyz` 只报告状态——它从不修复模式问题。

## 生产审计

应用模式变更前，审计所有生产实例：

```bash
python3 scripts/audit_production_schema.py --output audit_report.txt
```

**报告覆盖**：
- 当前模式版本
- 缺失的列
- 迁移优先级

## 最佳实践

### 1. 编写迁移文件

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

### 2. 测试迁移

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

### 3. 代码评审清单

PR 包含迁移时，检查：
- [ ] 同时提供 upgrade 与 downgrade
- [ ] 有条件检查保证幂等
- [ ] 没有 `app.*` 导入（MIG001）
- [ ] `CONCURRENTLY` 索引使用 `autocommit_block()` 模板（MIG002）
- [ ] 没有删除或改写已发布的 revision id（MIG003）
- [ ] 说明是否适用 expand/contract 模式
- [ ] 注明对滚动升级的影响
- [ ] 测试了从 baseline 出发的升级路径

## 相关文档

- [DATABASE_SCHEMA.md](DATABASE_SCHEMA.md) — 数据库领域地图（103 张表）
- [DATABASE_CONVENTIONS.md](DATABASE_CONVENTIONS.md) — 字段命名与类型约定
- [../guide/DATABASE_BACKUP.md](../guide/DATABASE_BACKUP.md) — 迁移前先备份
- [`scripts/lint/check_migration_rules.py`](../../scripts/lint/check_migration_rules.py) — MIG001–MIG003 检查器
- [`.github/workflows/migration-graph.yml`](../../.github/workflows/migration-graph.yml) — Migration Graph CI 工作流

## 获取帮助

遇到模式迁移问题时：
1. 收集错误日志
2. 抓取 `alembic current` 的输出
3. 抓取 `python3 scripts/audit_production_schema.py --json` 的输出
4. 携带以上内容提 issue
