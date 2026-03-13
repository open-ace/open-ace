---
name: list-open-issues
description: 列出当前项目中所有未解决的 GitHub issues
---

# GitHub Issues List Skill

列出当前项目中所有未解决的 GitHub issues。

## 使用方法

直接调用此 skill，无需额外参数。

## 工作流程

1. 使用 `gh issue list --state open` 命令获取所有未关闭的 issues
2. 解析并格式化输出结果
3. 显示每个 issue 的 ID、标题、标签和更新时间

## 输出格式

| ID | 标题 | 类型 |
|----|------|------|
| #1 | Issue 标题 | bug/enhancement |

## 注意事项

- 确保已安装 `gh` CLI 工具
- 确保已登录 GitHub (`gh auth status`)
- 默认只列出 open 状态的 issues
