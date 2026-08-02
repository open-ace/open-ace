# CI 修复最终报告：需要编排器介入

## 执行摘要

**问题**：`.worktrees/` 被错误地作为 Git submodule 提交，导致 CI 在 checkout 时失败。

**已尝试的修复**：在所有 workflow 文件中添加 `submodules: false`。

**结果**：❌ **修复失败**。Git 仍然在 checkout 时检查 submodule 完整性并失败。

**根本原因**：`submodules: false` 只阻止 submodule 初始化，不阻止 Git 检查索引一致性。

**必需的操作**：从 Git 索引和历史中删除 `.worktrees` submodule 条目。

## 问题详情

### Git 状态

```bash
# 索引中的 submodule 条目（模式 160000）
$ git ls-files --stage -- .worktrees/
160000 679ef1cc... 0	.worktrees/082fbaf2-d1b4-4075-917f-1d628c44b357
160000 679ef1cc... 0	.worktrees/63f63269-4e12-42d6-86f3-c3c41b7eea42
160000 b6bebe66... 0	.worktrees/f566fa56-38a3-4868-aaa2-f79e9655b2c4

# HEAD 树中也存在（证明已提交）
$ git show HEAD:.worktrees
tree HEAD:.worktrees
082fbaf2-d1b4-4075-917f-1d628c44b357
63f63269-4e12-42d6-86f3-c3c41b7eea42
f566fa56-38a3-4868-aaa2-f79e9655b2c4

# 缺少 .gitmodules
$ ls .gitmodules
ls: 无法访问 '.gitmodules': 没有那个文件或目录
```

### CI 失败日志

```
2026-08-02T06:49:43.6854968Z fatal: No url found for submodule path
'.worktrees/082fbaf2-d1b4-4075-917f-1d628c44b357' in .gitmodules
```

### 为什么 `submodules: false` 无效

| 参数 | 控制的行为 | 不控制的行为 |
|------|-----------|-------------|
| `submodules: false` | 是否执行 `git submodule init/update` | Git 对索引完整性的检查 |
| | 是否递归检出 submodule | Git 对 `.gitmodules` 存在性的检查 |
| | | Git 对 submodule URL 的验证 |

**结论**：即使设置 `submodules: false`，Git 在 checkout 时仍会检查 submodule 条目的完整性。

## 需要编排器执行的操作

### 命令序列

```bash
# 1. 从索引中删除 .worktrees（保留工作树文件）
git rm --cached -r .worktrees/

# 2. 验证删除
git ls-files --stage | grep -E '\.worktrees' && echo "ERROR" || echo "OK"

# 3. 提交修复
git commit -m "fix(ci): remove erroneously added .worktrees submodule entries

The .worktrees/ directory contains local git worktrees for development
and should never be committed as submodules.

This fixes the schema-sync CI failure:
'fatal: No url found for submodule path in .gitmodules'
"

# 4. 推送
git push
```

### 为什么需要编排器

当前环境禁止执行 `git rm` 等 "mutating git commands"：
```
Exit code 126
mutating git commands are reserved for the Open ACE orchestrator
```

这是设计决策：只有编排器有权修改 Git 索引。

## 替代方案分析

### 方案 1：创建 `.gitmodules` 文件 ❌

**尝试结果**：被拒绝（被认为是敏感文件）

**评估**：即使成功，这也不是正确的解决方案，因为 `.worktrees` 根本不应该是 submodule。

### 方案 2：修改 `.gitignore` ❌

**状态**：`.gitignore` 已包含 `.worktrees/`（第 83 行）

**评估**：`.gitignore` 只影响未跟踪文件，不影响已提交的 submodule 条目。

### 方案 3：修改 `.gitattributes` ❌

**已尝试**：添加了 `.worktrees/** -diff linguist-generated`

**评估**：`.gitattributes` 不影响 Git 对 submodule 的处理。

### 方案 4：等待 Git 或 actions/checkout 更新 ❌

**评估**：这不是可接受的方案，CI 持续失败会阻止所有开发工作。

## 验证步骤

编排器执行修复后，应验证：

```bash
# 1. 确认 .worktrees 不在索引中
git ls-files --stage | grep -E '\.worktrees'
# 应该返回空

# 2. 确认 checkout 正常
git checkout HEAD -- .
# 应该没有错误

# 3. 确认 Git 状态正常
git status
# 应该显示 "干净的工作区"
```

## 时间线

```
2026-08-02T06:48:47Z - CI build 开始
2026-08-02T06:49:43Z - Git checkout 失败（submodule 错误）
2026-08-02 14:36:12   - 提交修复尝试 1（修改代码）
2026-08-02 14:48:29   - 提交修复尝试 2（添加 submodules: false）
                        但修复提交的时间（06:48:29 UTC）早于
                        CI 失败时间（06:49:43 UTC）约 1 分钟
                        说明 CI 重新运行后仍然失败
```

## 结论

**必须由编排器介入**，执行 `git rm --cached -r .worktrees/` 来清除错误的 submodule 条目。

这是解决 CI 失败的唯一方法。当前的修改（添加 `submodules: false`、修改 `.gitattributes`、创建脚本）都无法直接解决问题。

---

**请求**：请编排器执行上述 `git rm` 命令，然后提交并推送。