---
name: gh-issue
description: 将当前处理的问题记录到 GitHub issue，包括问题描述、原因分析、修复方案和提交记录。
---

# Steps

### 1. 收集问题信息

从当前对话中提取以下信息：
- 问题描述：用户最初报告的问题是什么
- 问题原因：分析问题的根本原因
- 修复方案：采取了哪些修复措施
- 修改的文件：列出了哪些文件被修改
- 提交记录：相关的 git commit hash 和消息

### 2. 创建或更新 GitHub Issue

**如果 issue 已存在且已关闭**，添加修复记录评论：

```bash
gh issue comment <issue-number> --body "
## 修复完成 ✅

### 问题原因

<分析问题原因>

### 修复方案

<描述修复方案>

### 修改的文件

| 文件 | 修改内容 |
|------|----------|
| file1 | 描述 |
| file2 | 描述 |

### 提交记录

- commit_hash1: commit message 1
- commit_hash2: commit message 2

### 修复后效果

<描述修复后的效果>
"
```

**如果 issue 不存在**，创建新 issue：

```bash
gh issue create --title "<问题标题>" --body "
## 问题描述

<详细描述问题>

## 问题原因

<分析问题原因>

## 修复方案

<描述修复方案>

## 修改的文件

| 文件 | 修改内容 |
|------|----------|
| file1 | 描述 |
| file2 | 描述 |

## 修复后效果

<描述修复后的效果>
" --label "bug"
```

## 注意事项

1. 确保当前目录是 git 仓库
2. 确保已安装 `gh` CLI 工具并已登录
3. Issue 标题应简洁明了，概括问题核心
4. 提交记录使用简短的 commit hash（7位）
5. 如果 screenshots 目录被 .gitignore 忽略，不需要包含截图

## 示例输出

```
已添加修复记录到 Issue #36

链接: https://github.com/user/repo/issues/36#issuecomment-xxx

内容:
- 问题原因分析
- 修复方案
- 修改的文件列表
- 提交记录
- 修复后效果
```