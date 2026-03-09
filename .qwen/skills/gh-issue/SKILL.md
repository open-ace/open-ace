---
name: gh-issue
description: 将当前处理的问题记录到 GitHub issue，包括问题描述、原因分析、修复方案和截图证明。
---

# Steps

### 1. 收集问题信息

从当前对话中提取以下信息：
- 问题描述：用户最初报告的问题是什么
- 问题原因：分析问题的根本原因
- 修复方案：采取了哪些修复措施
- 修改的文件：列出了哪些文件被修改

### 2. 创建 GitHub Issue

使用 `gh issue create` 命令创建 issue，格式如下：

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

### 3. 上传截图证明

**重要：截图必须先提交到 git 并推送到 GitHub，否则链接无效！**

步骤：

1. **添加截图到 git**
```bash
git add screenshots/
```

2. **提交并推送**
```bash
git commit -m "Add screenshots for issue #<issue-number>"
git push
```

3. **使用 GitHub raw 链接格式**
```bash
gh issue comment <issue-number> --body "
## 修复后截图

![截图描述](https://raw.githubusercontent.com/<user>/<repo>/main/screenshots/screenshot.png)
"
```

**正确的链接格式：**
```
https://raw.githubusercontent.com/<user>/<repo>/main/screenshots/<filename>.png
```

**错误的链接格式（不要使用）：**
```
./screenshots/screenshot.png
```

## 注意事项

1. 确保当前目录是 git 仓库
2. 确保已安装 `gh` CLI 工具并已登录
3. 截图应存放在项目的 `screenshots/` 目录下
4. **截图必须先 git push 到 GitHub，再在 issue 中引用**
5. Issue 标题应简洁明了，概括问题核心

## 示例输出

```
Issue 创建成功！

链接: https://github.com/user/repo/issues/123

内容:
- 问题描述
- 问题原因分析
- 修复方案
- 修改的文件列表
- 修复后截图（已推送到 GitHub）
```
