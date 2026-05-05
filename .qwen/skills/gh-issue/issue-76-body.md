## 问题描述

在测试 install.sh (issue #75) 过程中发现多个数据库 migration 执行失败，根本原因是 **init_db.py 创建的数据库结构与 alembic migration 不一致**。

### 发现的具体问题

1. **migration 021**: PostgreSQL boolean 类型比较语法错误
   - `is_active = 1` 在 PostgreSQL 中报错（boolean 不能与 integer 比较）
   - `acknowledged = TRUE` 在 integer 字段上报错

2. **migration 023**: `CREATE INDEX CONCURRENTLY` 不能在事务块中运行
   - alembic 默认在事务中执行 migration
   - CONCURRENTLY 语法与事务块冲突

3. **migration 024**: `linux_account` 字段不存在
   - migration 引用 `linux_account`，但数据库只有 `system_account`
   - init_db.py 已将字段改名为 `system_account`（不走 alembic）

## 问题原因

### 根本原因：init_db.py 和 alembic 版本不一致

`init_db.py` 在创建数据库时：
- 直接创建 `system_account` 字段（第 1359-1365 行）
- 将 `linux_account` 重命名为 `system_account`
- 不更新 alembic_version 表

导致：
- alembic 版本停留在初始版本
- 后续 migration 执行时，引用的字段名与实际数据库不匹配
- migration 024 执行 SQL 时找不到 `linux_account` 字段

### 数据库版本状态

服务器 192.168.64.2 上测试时发现：
- alembic_version 卡在 `018_add_daily_stats`
- 实际数据库已有 `system_account` 字段（init_db.py 创建）
- 缺少 `user_tool_accounts` 表（migration 024 未执行）

## 修复方案

| 文件 | 修改内容 |
|------|----------|
| migrations/versions/20260330_021_postgresql_optimization.py | 修复 boolean 比较语法 |
| migrations/versions/20260403_023_add_user_request_trend_index.py | 移除 CONCURRENTLY |
| migrations/versions/20260403_024_add_user_id_and_tool_accounts.py | 动态检测字段名 |

### 待修复：调整 init_db.py 逻辑

**建议方案**：init_db.py 应只执行 `alembic upgrade head`，不直接创建/修改表结构

好处：
- 所有 schema 变化由 alembic 管理
- 版本记录一致
- 避免 migration 与实际结构不匹配

## 提交记录

- 04a6607: fix: correct acknowledged field type in migration 021
- 4233a94: fix: use IS TRUE/IS FALSE for PostgreSQL boolean comparisons
- 23c3837: fix: remove CONCURRENTLY from migration 023
- 2325130: fix: use COALESCE for system_account/linux_account in migration 024
- 5048bf9: fix: dynamically detect account column name in migration 024

## 测试状态

- ✅ migration 021 boolean 类型问题已修复
- ✅ migration 023 CONCURRENTLY 问题已修复
- ⏳ migration 024 字段名问题修复中
- ❌ init_db.py 与 alembic 不一致问题待修复

## 关联 Issue

测试 issue #75 时发现此问题。
