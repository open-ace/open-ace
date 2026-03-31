## Qwen Added Memories
- 测试脚本组织规则：在 tests/issues/{issue_number}/ 目录下创建测试脚本。例如 issue 36 的测试脚本放在 tests/issues/36/ 目录下。
- 截图组织规则：在 screenshots/issues/{issue_number}/ 目录下存放 issue 相关截图。例如 issue 36 的截图放在 screenshots/issues/36/ 目录下。通用截图保留在 screenshots/ 根目录。
- **多 issue 规则**：每个 issue 必须单独创建目录，禁止使用范围命名（如 73-78）。例如 issue 73、74、75 应分别创建 tests/issues/73/、tests/issues/74/、tests/issues/75/ 目录。
- 修复前端代码后必须执行：1) cd frontend && npm run build 构建前端 2) 重启后端服务 3) 然后才能告诉用户修复完成。前端使用 Vite 构建，输出到 ../static/js/dist/ 目录。
- 处理数据库相关问题时，必须先检查环境变量 DATABASE_URL 或 ~/.open-ace/config.json 配置文件确定数据库类型（PostgreSQL 或 SQLite），不能假设数据库类型。
- 修改数据库结构（包括添加索引、修改表结构等）必须使用 Alembic migration，不能直接执行 SQL 命令。

## 修复完成后的验证流程（必须执行）

**重要：每次修复完成后，必须使用 todo_write 工具创建以下检查清单并逐一执行：**

```
[ ] 1. 停止旧服务 (kill 旧进程)
[ ] 2. 确认旧服务已结束 (lsof -i :5000)
[ ] 3. 启动新服务 (python3 web.py &)
[ ] 4. 检查服务启动时间 (ps -p PID -o lstart)
[ ] 5. 更新 VERSION 文件为当前 commit 号
[ ] 6. 重启服务加载新版本
[ ] 7. 调用 ui-test 技能测试并报告结果
[ ] 8. 提交 VERSION 更新
```

每完成一步，必须立即标记为 [x]，不得跳过任何步骤。
