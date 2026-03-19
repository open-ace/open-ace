## Qwen Added Memories
- 测试脚本组织规则：在 tests/issues/{issue_number}/ 目录下创建测试脚本。例如 issue 36 的测试脚本放在 tests/issues/36/ 目录下。
- 截图组织规则：在 screenshots/issues/{issue_number}/ 目录下存放 issue 相关截图。例如 issue 36 的截图放在 screenshots/issues/36/ 目录下。通用截图保留在 screenshots/ 根目录。

## 修复完成后的验证流程（必须执行）

**重要：每次修复完成后，必须使用 todo_write 工具创建以下检查清单并逐一执行：**

```
[ ] 1. 停止旧服务 (kill 旧进程)
[ ] 2. 确认旧服务已结束 (lsof -i :5001)
[ ] 3. 启动新服务 (python3 web.py &)
[ ] 4. 检查服务启动时间 (ps -p PID -o lstart)
[ ] 5. 更新 VERSION 文件为当前 commit 号
[ ] 6. 重启服务加载新版本
[ ] 7. 调用 ui-test 技能测试并报告结果
[ ] 8. 提交 VERSION 更新
```

每完成一步，必须立即标记为 [x]，不得跳过任何步骤。
