## 修复完成 ✅

### 实施方案

采用 **schema.sql + alembic stamp head** 方案：
- 安装时直接执行 schema.sql 创建完整数据库结构
- 使用 `alembic stamp head` 标记版本为最新（跳过 migration 重放）
- init_database() 简化为只做验证检查

### 修改的文件

| 文件 | 修改内容 |
|------|----------|
| schema/schema-postgres.sql | 新建，PostgreSQL 最终 schema（从 pg_dump 导出并清理） |
| schema/schema-sqlite.sql | 新建，SQLite 兼容 schema |
| scripts/generate_schema.py | 新建，从 pg_dump 导出并生成清理后的 schema |
| scripts/init_db.py | 简化，只创建 admin 用户，移除 init_database() 调用 |
| scripts/shared/db.py | init_database() 简化为：PostgreSQL 只验证，SQLite 保留创建逻辑 |
| scripts/install-central/package-method/install.sh | 执行 schema.sql + alembic stamp head |
| web.py | 移除 init_database() 调用 |

### 提交记录

- 54ae893: feat: use schema.sql for database initialization instead of alembic migrations
- 5048bf9: fix: dynamically detect account column name in migration 024
- 23c3837: fix: remove CONCURRENTLY from migration 023
- 04a6607: fix: correct acknowledged field type in migration 021

### 修复后效果

**安装流程变化**：
1. 执行 `schema/schema-postgres.sql`（PostgreSQL）或 `schema/schema-sqlite.sql`（SQLite）
2. 执行 `alembic stamp head` 标记版本
3. 执行 `init_db.py` 创建 admin 用户

**好处**：
- 所有 schema 变化集中在 schema.sql
- 安装时无需重放所有 migration（避免执行失败）
- alembic 版本记录一致
- PostgreSQL 使用固定 schema，SQLite 保留灵活创建（用于开发/测试）

### 待测试

需要在 192.168.64.2 服务器上重新安装验证：
- [ ] 执行 uninstall.sh
- [ ] 运行 package.sh 打包
- [ ] 运行 install.sh
- [ ] 验证数据库 schema 正确创建
- [ ] 验证服务正常启动