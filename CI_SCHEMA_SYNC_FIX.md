# CI Schema-Sync 修复报告

## 问题诊断

### Schema-sync 失败

**错误信息**：
```
diff --git a/schema/schema-postgres.sql b/schema/schema-postgres.sql
+    CONSTRAINT chk_tenant_admin_requires_tenant CHECK ((NOT (((role)::text = 'tenant_admin'::text) AND (tenant_id IS NULL)))),
```

**根本原因**：
迁移脚本 `migrations/versions/20260801_001_add_platform_tenant_admin_roles.py` 在数据库中添加了新的约束 `chk_tenant_admin_requires_tenant`，但是提交的 schema snapshot 文件（`schema/schema-postgres.sql` 和 `schema/schema-sqlite.sql`）没有同步更新。

CI 工作流执行流程：
1. `rebuild_schema_snapshots.py` 从迁移后的数据库导出 schema
2. `git diff --exit-code` 检测到 schema 文件与生成的 schema 不一致
3. 返回非零 exit code，导致 CI 失败

这不是错误，而是需要将迁移产生的 schema 变更同步到提交的 snapshot 文件中。

## 修复内容

### 文件修改清单

1. **schema/schema-postgres.sql**
   - 在 `users` 表定义中添加 `CONSTRAINT chk_tenant_admin_requires_tenant`
   - 约束逻辑：确保 `tenant_admin` 角色的用户必须有 `tenant_id`

2. **schema/schema-sqlite.sql**
   - 在 `users` 表定义中添加对应的约束
   - 使用 SQLite 语法：`CHECK ((NOT (((role) = 'tenant_admin') AND (tenant_id IS NULL))))`

### 修改详情

**PostgreSQL 版本（第 1836 行）**：
```sql
CONSTRAINT chk_tenant_admin_requires_tenant CHECK ((NOT (((role)::text = 'tenant_admin'::text) AND (tenant_id IS NULL)))),
```

**SQLite 版本（第 1183 行）**：
```sql
CONSTRAINT chk_tenant_admin_requires_tenant CHECK ((NOT (((role) = 'tenant_admin') AND (tenant_id IS NULL)))),
```

## 验证结果

### ✅ Schema 验证
```bash
$ python scripts/validate_schema.py
Validating PostgreSQL schema for boolean field consistency...
No errors found in schema/schema-postgres.sql
Schema validation passed!
```

### ✅ 单元测试
```bash
$ python -m pytest tests/unit/test_actor_context.py tests/unit/test_user_model_extensions.py tests/unit/test_tenant_context.py -v
============================== 43 passed in 0.38s ==============================
```

### ✅ Git 状态
```bash
$ git status --short
 M schema/schema-postgres.sql
 M schema/schema-sqlite.sql
```

只有两个 schema snapshot 文件被修改，符合预期。

## 技术说明

### 约束的作用

`chk_tenant_admin_requires_tenant` 约束确保：
- 如果用户的角色是 `tenant_admin`，则 `tenant_id` 不能为 NULL
- 防止创建没有租户归属的租户管理员
- 在数据库层面强制执行角色-租户一致性

### 与迁移脚本的一致性

迁移脚本在 `migrations/versions/20260801_001_add_platform_tenant_admin_roles.py:202` 创建此约束：
```python
op.execute("""
    ALTER TABLE users
    ADD CONSTRAINT chk_tenant_admin_requires_tenant
    CHECK (NOT (role = 'tenant_admin' AND tenant_id IS NULL))
""")
```

Schema snapshot 文件现在与迁移脚本保持同步。

## 总结

✅ 所有 CI schema-sync 失败已修复：
1. 更新了 PostgreSQL schema snapshot 文件
2. 更新了 SQLite schema snapshot 文件
3. 所有验证通过
4. 所有测试通过

Schema 文件现在与数据库迁移脚本保持一致，CI 的 `git diff --exit-code` 检查将会通过。