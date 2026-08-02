# Schema-Sync CI 失败修复报告

## 问题诊断

### 根本原因

`.worktrees/` 目录被**错误地作为 Git submodule 提交**到仓库中，导致 CI 在 checkout 时失败。

### 详细分析

1. **错误来源**：
   - 提交 `4b9dfeeb` ("auto: development changes (round 1)") 将 `.worktrees/` 作为 submodule 添加
   - 该提交已进入 `origin/main` 分支

2. **当前状态**：
   ```bash
   # HEAD 树中存在 submodule 条目
   $ git show HEAD:.worktrees
   tree HEAD:.worktrees

   082fbaf2-d1b4-4075-917f-1d628c44b357
   63f63269-4e12-42d6-86f3-c3c41b7eea42
   f566fa56-38a3-4868-aaa2-f79e9655b2c4

   # 索引中的 submodule 条目（模式 160000）
   $ git ls-files --stage -- .worktrees/
   160000 679ef1cc... 0	.worktrees/082fbaf2-d1b4-4075-917f-1d628c44b357
   160000 679ef1cc... 0	.worktrees/63f63269-4e12-42d6-86f3-c3c41b7eea42
   160000 b6bebe66... 0	.worktrees/f566fa56-38a3-4868-aaa2-f79e9655b2c4

   # 缺少 .gitmodules 文件
   $ ls .gitmodules
   ls: 无法访问 '.gitmodules': 没有那个文件或目录
   ```

3. **CI 失败原因**：
   - `actions/checkout` 执行 `git checkout` 时
   - Git 检测到索引中的 submodule 条目（模式 160000）
   - 尝试查找 `.gitmodules` 文件来获取 submodule URL
   - 因 `.gitmodules` 不存在而失败：
     ```
     fatal: No url found for submodule path '.worktrees/082fbaf2-d1b4-4075-917f-1d628c44b357' in .gitmodules
     ```

4. **已应用的修复**：
   - ✅ 在所有 workflow 文件中添加 `submodules: false`
   - ❌ **但这不足以解决问题**：Git 在 checkout 时仍会检查 submodule 的一致性

## 必需的修复操作

### 操作 1：从 Git 索引和历史中删除 `.worktrees` submodule 条目

**需要执行**：
```bash
# 从索引中删除
git rm --cached -r .worktrees/

# 提交修改
git commit -m "fix: remove erroneously added .worktrees submodule entries

The .worktrees/ directory contains local git worktrees for development
and should never be committed to the repository as submodules.

This fixes the schema-sync CI failure:
'fatal: No url found for submodule path in .gitmodules'
"
```

### 操作 2：验证 `.gitignore`

**当前状态**：`.gitignore` 已包含 `.worktrees/`（第 83 行）

**验证**：
```bash
grep -n "\.worktrees" .gitignore
# 输出: 83:.worktrees/
```

✅ 正确配置，无需修改

## 为什么 `submodules: false` 不足以解决问题

`actions/checkout@v6` 的 `submodules: false` 参数只影响：
- 是否执行 `git submodule init`
- 是否执行 `git submodule update`

**但不影响**：
- Git 在 `git checkout` 时对索引的检查
- Git 对 submodule 条目完整性的验证

当 Git 发现：
1. 索引中有 submodule 条目（160000 模式）
2. 但没有 `.gitmodules` 文件

Git 会认为仓库状态不一致，并报错。

## 时间线分析

```
2026-08-02T06:48:47Z - CI build 开始
2026-08-02T06:49:43Z - Git checkout 失败：
                       "fatal: No url found for submodule path..."
2026-08-02 14:48:29   - 提交修复：添加 submodules: false
                        (修复提交时间：14:48:29 +0800 = 06:48:29 UTC)
```

**问题**：CI 失败时间（06:49:43 UTC）在修复提交时间（06:48:29 UTC）之后约 1 分钟，说明修复提交被推送后 CI 重新运行，但仍然失败。

这证明：**仅添加 `submodules: false` 无法解决问题**。

## 后续步骤

### 必须由编排器执行

由于当前环境禁止执行 `git rm` 等 mutating git commands，必须由编排器执行：

```bash
# 1. 删除 submodule 条目
git rm --cached -r .worktrees/

# 2. 验证删除
git ls-files --stage | grep -E '\.worktrees' || echo "Removed successfully"

# 3. 提交并推送
git commit -m "fix: remove .worktrees submodule entries"
git push
```

### 验证步骤

修复后，CI 应该能够成功 checkout：
```bash
# 本地验证
git checkout HEAD -- .
# 应该没有错误
```

## 关于 Issue #2185 的说明

本次修复是 CI 基础设施修复，不直接影响 Issue #2185（统一安全模式）的功能需求。
但修复 CI 是继续开发和验证 Issue #2185 的前提条件。

## 总结

- ✅ **已识别根本原因**：`.worktrees` 被错误地作为 submodule 提交
- ✅ **已诊断失败机制**：Git checkout 时检查 submodule 完整性失败
- ❌ **当前修复不足**：`submodules: false` 无法解决 Git 内部检查
- ⏳ **需要编排器介入**：执行 `git rm --cached -r .worktrees/`

---

**请求编排器**：请执行上述 `git rm` 和提交操作，以清除错误的 submodule 条目。