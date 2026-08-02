# CI 修复尝试报告

## 问题描述

**失败的检查**：schema-sync
**失败原因**：`.worktrees/` 目录被错误地作为 Git submodule 提交到索引中（模式 160000），但缺少对应的 `.gitmodules` 文件配置。
**错误信息**：
```
fatal: No url found for submodule path '.worktrees/082fbaf2-d1b4-4075-917f-1d628c44b357' in .gitmodules
```

## 根本原因

Git 索引中包含三个 `.worktrees` 条目（模式 160000 表示 submodule）：

```bash
$ git ls-files --stage | grep .worktrees
160000 679ef1cc... 0	.worktrees/082fbaf2-d1b4-4075-917f-1d628c44b357
160000 679ef1cc... 0	.worktrees/63f63269-4e12-42d6-86f3-c3c41b7eea42
160000 b6bebe66... 0	.worktrees/f566fa56-38a3-4868-aaa2-f79e9655b2c4
```

这些条目在 HEAD 提交中也存在，说明已经提交到仓库历史中。

## 修复尝试

### 尝试 1：添加 `submodules: false` ❌

**结果**：无效。`submodules: false` 只阻止 submodule 初始化，不阻止 Git 检查索引完整性。

### 尝试 2：修改 `.gitattributes` ❌

**结果**：无效。`.gitattributes` 不影响 Git 对 submodule 的处理。

### 尝试 3：配置 Git 忽略 submodule ⚠️

**修改内容**：在 `.github/workflows/schema-sync.yml` 中添加：
```yaml
- name: Configure Git for checkout
  run: |
    git config --global submodule.ignore all
    git config --global submodule.active false
    git config --global checkout.checkStats false

- uses: actions/checkout@v6
  with:
    submodules: false
    fetch-depth: 1
```

**预期效果**：这些配置可能帮助 Git 跳过 submodule 验证。

**限制**：无法在本地验证，因为所有 Git 操作（包括 `git clone`）都被编排器保留。

## 无法执行的操作

根据环境约束，以下操作被禁止：
- `git rm --cached -r .worktrees/` - 从索引中删除 submodule 条目
- `git update-index --force-remove` - 更新 Git 索引
- `git clone` - 克隆仓库
- 任何 "mutating git commands"

这些操作被保留给编排器执行。

## 正确的解决方案

**根本解决方法**：从 Git 索引和历史中删除 `.worktrees` submodule 条目。

**需要编排器执行的命令**：
```bash
git rm --cached -r .worktrees/
git commit -m "fix(ci): remove erroneously added .worktrees submodule entries"
git push
```

## 当前状态

- ✅ 已产生代码改动（修改了 workflow 文件）
- ⚠️ 修改的有效性无法在本地验证
- ⚠️ 即使修改生效，也不是根本解决方案
- ❌ 根本解决方案需要编排器介入

## 建议

1. **短期**：让 CI 运行，验证当前的 workflow 修改是否能绕过 checkout 问题
2. **长期**：编排器应执行 `git rm --cached -r .worktrees/` 来彻底解决问题

## 相关文件

- 修改的 workflow：`.github/workflows/schema-sync.yml`
- 已有的诊断报告：`CI_FIX_REQUEST_ORCHESTRATOR.md`
- 问题的 Git 状态：`.worktrees/` 目录在索引中作为 submodule 存在