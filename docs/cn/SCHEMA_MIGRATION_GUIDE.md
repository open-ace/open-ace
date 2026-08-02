# Schema 迁移指南

> Issue: #2190

本文档说明 Open ACE 的 schema 迁移策略、最佳实践和故障排查方法。

## Schema 权威模型

Open ACE 采用 **Alembic 作为唯一 schema 变更通道** 的权威模型：

```
┌─────────────────────────────────────────────────────────────┐
│              Schema 变更权威模型                             │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  生产环境：alembic upgrade head 是唯一 schema 变更通道      │
│                                                             │
│  开发环境：SQLite 允许 bootstrap，但必须与生产路径分离      │
│                                                             │
│  禁止：create_app() 对 PostgreSQL 执行 DDL                  │
│        运行时自动补列/ALTER TABLE                           │
│        业务代码发现缺列后修 schema                          │
└─────────────────────────────────────────────────────────────┘
```

## 迁移前的准备工作

### 1. 检查当前数据库版本

```bash
alembic current
```

输出示例：
```
20260801_001_add_platform_tenant_admin_roles (head)
```

### 2. 查看待执行的 migrations

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

## 执行迁移

### 标准升级流程

```bash
# 1. 检查当前版本
alembic current

# 2. 执行升级
alembic upgrade head

# 3. 验证升级成功
alembic current  # 应显示最新 revision

# 4. 验证 schema 完整性
python3 scripts/verify_schema_integrity.py
```

### 降级（回滚）

```bash
# 回退一个版本
alembic downgrade -1

# 回退到指定版本
alembic downgrade <revision_id>

# 回退所有 migrations（保留数据库结构）
alembic downgrade base
```

**注意**：降级操作会删除列，可能导致数据丢失。生产环境降级前必须备份。

## 迁移策略

### Expand/Contract 模式

对于滚动升级场景，采用 expand/contract migration 模式：

#### 阶段 1: Expand（扩展）

添加可空列或有默认值的列：

```python
# migration: add_column_expand.py
def upgrade():
    op.add_column(
        "users",
        sa.Column("new_field", sa.Text(), nullable=True),  # 可空
    )
```

**特点**：
- 新应用可以使用新列
- 旧应用忽略新列，不受影响
- 支持新旧版本共存

#### 阶段 2: 稳定运行

所有 Pod 升级到新版本，新列正常使用。

#### 阶段 3: Contract（收缩，可选）

确认无回退需求后，添加约束或删除旧列：

```python
# migration: add_column_constraint.py
def upgrade():
    # 添加 NOT NULL 约束
    op.alter_column(
        "users",
        "new_field",
        nullable=False,
        server_default="default_value",
    )
```

### 兼容性窗口

应用版本与 schema 版本的兼容关系：

| 应用版本 | 最小 Schema 版本 | 最大 Schema 版本 | 兼容窗口 |
|---------|-----------------|-----------------|---------|
| v2.1    | baseline_2026_06_23 | HEAD          | 10 revisions |
| v2.0    | baseline_2026_06_23 | 20260717_004  | 5 revisions |

**判断逻辑**：
- 应用启动时检查 schema 版本是否在兼容窗口内
- 版本过低：启动失败，提示升级
- 版本过高：启动失败，提示应用需要升级

## 禁止的操作

### ❌ 生产环境禁止运行时 DDL

```python
# 错误示例（已禁止）
@app.before_request
def ensure_columns():
    # 不要在运行时执行 ALTER TABLE
    cursor.execute("ALTER TABLE users ADD COLUMN ...")
```

### ❌ 业务代码直接修改 schema

```python
# 错误示例
def create_session():
    try:
        cursor.execute("INSERT INTO sessions ...")
    except ColumnMissingError:
        # 不要尝试修复 schema
        cursor.execute("ALTER TABLE sessions ADD COLUMN ...")
        cursor.execute("INSERT INTO sessions ...")
```

### ✅ 正确做法

如果发现缺少列，应该：
1. 停止应用
2. 执行 migration
3. 重新启动应用

## 故障排查

### Migration 执行失败

**症状**：`alembic upgrade head` 报错

**诊断步骤**：

```bash
# 1. 检查数据库连接
pg_isready -h <host> -p <port>

# 2. 检查当前版本
alembic current

# 3. 检查 migration 历史
alembic history

# 4. 查看详细错误信息
alembic upgrade head --sql
```

**常见错误及解决方法**：

| 错误信息 | 原因 | 解决方法 |
|---------|------|---------|
| `Can't locate revision` | migration 链断裂 | 检查 down_revision 是否正确 |
| `relation "xxx" already exists` | migration 重复执行 | 使用幂等性检查 |
| `column "xxx" of relation "xxx" does not exist` | 列未添加 | 检查 migration 执行顺序 |

### Schema 版本不匹配

**症状**：应用启动失败，提示 schema 版本过低

**解决方法**：

```bash
# 1. 检查当前版本
alembic current

# 2. 执行升级
alembic upgrade head

# 3. 如果是新数据库，执行完整初始化
alembic upgrade head
python3 scripts/init_db.py  # 创建默认用户
```

### 多 Pod 并发迁移冲突

**症状**：多个 Pod 同时启动，执行 migration 时出现锁等待或死锁

**解决方法**：

#### 方案 1: Kubernetes Init Container

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

#### 方案 2: 独立迁移任务

```yaml
# 在部署应用前，单独运行 migration job
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

应用提供 `/readyz` endpoint 用于检查服务就绪状态：

```bash
curl http://localhost:19888/readyz
```

**响应示例**：

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
- `200 OK`: 所有检查通过，服务就绪
- `503 Service Unavailable`: 检查失败，需要修复

**注意**：`/readyz` 不会自动修复 schema 问题，只会报告状态。

## 生产环境普查

在执行 schema 变更前，建议先普查所有生产实例：

```bash
python3 scripts/audit_production_schema.py --output audit_report.txt
```

**报告内容**：
- 当前 schema 版本
- 缺失的列
- 需要迁移的优先级

## 最佳实践

### 1. Migration 文件编写

```python
def upgrade():
    # ✅ 使用条件检查，确保幂等性
    inspector = sa.inspect(op.get_bind())
    existing_columns = {col["name"] for col in inspector.get_columns("users")}

    if "new_column" not in existing_columns:
        op.add_column("users", sa.Column("new_column", sa.Text()))

def downgrade():
    # ✅ 检查列是否存在再删除
    inspector = sa.inspect(op.get_bind())
    existing_columns = {col["name"] for col in inspector.get_columns("users")}

    if "new_column" in existing_columns:
        op.drop_column("users", "new_column")
```

### 2. 测试 Migration

```bash
# 1. 在测试环境验证 upgrade
alembic upgrade head

# 2. 验证 downgrade
alembic downgrade -1
alembic upgrade head

# 3. 验证幂等性（重复执行）
alembic upgrade head
alembic upgrade head  # 不应报错
```

### 3. 代码审查要点

PR 包含 migration 时，检查：
- [ ] 提供 upgrade 和 downgrade 路径
- [ ] 使用条件检查确保幂等性
- [ ] 说明是否为 expand/contract 模式
- [ ] 标注对滚动升级的影响
- [ ] 测试从 baseline 升级路径

## 相关文档

- [部署指南](DEPLOYMENT.md)
- [数据库 Schema 说明](DATABASE-SCHEMA.md)
- [数据库约定](DATABASE-CONVENTIONS.md)

## 联系支持

如遇到 schema 迁移问题，请：
1. 收集错误日志
2. 执行 `alembic current` 输出
3. 执行 `python3 scripts/audit_production_schema.py --json` 输出
4. 提交 Issue 并附上以上信息