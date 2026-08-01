# CI Merge 阶段修复报告

## 问题诊断

### 1. schema-sync 失败
**错误信息**：
```
sqlalchemy.exc.DataError: (psycopg2.errors.InvalidTextRepresentation) invalid input syntax for type integer: ""
LINE 1: ... COUNT(*) FROM users WHERE role = 'admin' AND tenant_id = ''
```

**根本原因**：
- 迁移脚本中使用 `tenant_id = ''` 与整数类型列比较
- PostgreSQL 的 `tenant_id` 是整数类型，不能与空字符串比较

**修复方案**：
- 检测 `tenant_id` 列的数据类型
- 根据数据类型动态构建 SQL 条件：
  - 整数类型：只检查 `tenant_id IS NULL`
  - 字符串类型：检查 `tenant_id IS NULL OR tenant_id = ''`

### 2. lint 失败 (API Security Scanner)
**错误信息**：
```
Found 13 violation(s).
  app/routes/tenant.py:40: SEC001 Route GET  has no authentication
  app/routes/tenant.py:66: SEC001 Route GET /<int:tenant_id> has no authentication
  ...
```

**根本原因**：
- API Security Scanner 只识别预定义的装饰器名称
- 新添加的装饰器不在识别列表中：
  - `platform_admin_required`
  - `tenant_admin_required`
  - `same_tenant_or_platform_admin`

**修复方案**：
- 在 `scripts/lint/api_security_scanner.py` 的 `AUTH_DECORATORS` 集合中添加新装饰器名称

## 修复内容

### 文件修改清单

1. **migrations/versions/20260801_001_add_platform_tenant_admin_roles.py**
   - 添加数据类型检测逻辑
   - 根据类型动态构建 SQL 条件
   - 避免整数类型与空字符串比较

2. **scripts/lint/api_security_scanner.py**
   - 在 `AUTH_DECORATORS` 中添加新装饰器名称
   - Issue #2179: New role-based authentication decorators

## 验证结果

### 1. API Security Scanner
```bash
$ python scripts/lint/api_security_scanner.py
No new violations. (0 baseline suppression(s) active)
```
✅ 通过

### 2. Lint 检查
```bash
$ ruff check app/routes/tenant.py app/auth/decorators.py migrations/versions/20260801_001_add_platform_tenant_admin_roles.py scripts/lint/api_security_scanner.py --line-length=100
All checks passed!
```
✅ 通过

### 3. 单元测试
```bash
$ python -m pytest tests/unit/test_actor_context.py tests/unit/test_user_model_extensions.py tests/unit/test_tenant_context.py -v
============================== 43 passed in 0.38s ==============================
```
✅ 通过

### 4. 迁移逻辑验证
测试了两种数据类型的迁移逻辑：
- 整数类型（PostgreSQL 默认）：`tenant_id IS NULL`
- 字符串类型（遗留）：`tenant_id IS NULL OR tenant_id = ''`

✅ 通过

### 5. Actor 验证测试
```bash
$ python -m pytest tests/unit/test_tenant_service_actor_validation.py -v
============================== 10 passed in 0.51s ==============================
```
✅ 通过

## 预先存在的问题

以下测试失败是预先存在的 schema 语法问题，**不是本次修改引入的**：
- `tests/issues/1781/test_tenant_boundaries.py` - SQLite schema 语法错误 (`near ">>": syntax error`)

## 总结

所有 CI merge 阶段失败已修复：
1. ✅ schema-sync 失败已修复（SQL 类型错误）
2. ✅ lint 失败已修复（API Security Scanner 识别新装饰器）
3. ✅ 所有相关测试通过
4. ✅ 代码符合 lint 规范

代码现在可以安全合并。
