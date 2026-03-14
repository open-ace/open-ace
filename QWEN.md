## Qwen Added Memories
- 修复完成后的验证流程：1) 重启服务 2) 确认旧服务已结束 3) 启动新服务 4) 检查服务启动时间 5) 页面上的 Version 应该是解决本问题的 commit 号 6) 调用 ui-test 技能测试并报告结果
- 测试脚本组织规则：在 tests/issues/{issue_number}/ 目录下创建测试脚本。例如 issue 36 的测试脚本放在 tests/issues/36/ 目录下。
- 截图组织规则：在 screenshots/issues/{issue_number}/ 目录下存放 issue 相关截图。例如 issue 36 的截图放在 screenshots/issues/36/ 目录下。通用截图保留在 screenshots/ 根目录。
