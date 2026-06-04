---
name: parallel-issue-fix
description: 多 Issue 并行修复 - 使用 Git Worktree 和双智能体协同模式完成问题全流程闭环修复（含循环迭代和标准评审流程）
---

# 多 Issue 并行修复 Skill

使用 Git Worktree 和双智能体协同模式，并行修复多个 GitHub Issues，确保代码质量与流程闭环。

## 使用方法

```
/parallel-issue-fix <issue-number-1> <issue-number-2> ...
```

例如：`/parallel-issue-fix 484 476`

## ⚠️ 前置条件（必须执行）

### 配置 Subagent（P0 优先级）

**评审 Agent 需要启动独立的 subagent session**，必须先配置：

```bash
# 在 Qwen Code CLI 中执行以下命令
/agents create review-correctness "评审正确性"
/agents create review-security "评审安全性"
/agents create review-quality "评审代码质量"
/agents create review-performance "评审性能"
/agents create review-test "评审测试覆盖"
/agents create review-attacker "攻击者视角审计"
/agents create review-oncall "运维视角审计"
/agents create review-maintainer "维护者视角审计"
/agents create review-build-test "构建测试验证"
```

**配置后才能**：
- ✅ 并行启动 9 个评审 Agent（在单个消息中）
- ✅ 实现真正的独立 session 评审
- ✅ 使用标准 review skill 流程（9 步流程）

---

## 核心架构

```
┌─────────────────────────────────────────────────────────────────┐
│                     主仓库 /home/cfhan/open-ace                  │
├─────────────────────────────────────────────────────────────────┤
│  .qwen/tmp/                                                      │
│  ├── fix-473/            # Issue #473 修复工作树（保留用于迭代）   │
│  │   └── (分支: fix/issue-473)                                  │
│  ├── fix-484/            # Issue #484 修复工作树                 │
│  │   └── (分支: fix/issue-484)                                  │
│  ├── review-pr-497/      # PR #497 评审工作树（评审后立即清理）   │
│  └── review-pr-498/      # PR #498 评审工作树                    │
└─────────────────────────────────────────────────────────────────┘

关键改进：
- 修复 Agent 也使用独立工作树（避免多 Issue 并发冲突）
- 评审工作树评审后立即清理（一次性使用）
- 修复工作树保留直到合并完成（支持循环迭代）
```

---

## 🔄 完整工作流程（循环迭代版）

### Phase 1: 修复 Agent（独立工作树 A - 第 N 次修复）

**职责**：
1. 创建独立工作树（避免并发冲突）
2. 定位 Issue 根因
3. 完成代码逻辑修复（首次或根据评审意见优化）
4. 兼容异常场景、边界用例
5. 规避新增 BUG
6. 完成测试用例并验证
7. 提交 commit（或新 commit）
8. 创建/更新 PR
9. 同步评论至 Issue
10. **保留工作树**（用于后续迭代）

**执行步骤**：
```bash
# 1. 创建修复工作树（首次）
git worktree add .qwen/tmp/fix-<ID> -b fix/issue-<ID>

# 2. 在修复工作树中工作
cd .qwen/tmp/fix-<ID>

# 3. 获取 Issue 详情
gh issue view <ID>

# 4. 分析代码，定位根因
# 5. 编写修复代码（首次或根据评审意见优化）
# 6. 编写/更新测试用例
# 7. 运行测试验证
npm run typecheck && npm test && pytest tests/ -v

# 8. 提交修复
git add .
git commit -m "fix(#<ID>): <修复描述>"
# 或迭代优化时：
git commit -m "fix(#<ID>): 根据评审意见优化 - <优化内容>"

# 9. 推送分支
git push origin fix/issue-<ID>

# 10. 创建 PR（首次）或更新 PR（已存在）
gh pr create --title "fix(#<ID>): <标题>" --body "$(cat <<'PR_BODY'
## 相关 Issue
Fixes #<ID>

## 问题原因
<分析问题根本原因>

## 修复方案
<描述修复方案>

## 测试验证
<描述测试方法和结果>

## 修改的文件
| 文件 | 修改内容 |
|------|----------|
| file1 | 描述 |
PR_BODY
)"

# 11. 同步评论到 Issue
gh issue comment <ID> --body "## 修复方案已提交\n\nPR: #<PR号>\n\n### 修复内容\n<详细说明>"

# 12. 保留工作树（不删除，等待评审结果）
# 工作树保留在 .qwen/tmp/fix-<ID>
```

---

### Phase 2: 评审 Agent（独立工作树 B - 第 N 次评审）

**职责**：
1. 创建独立评审工作树（每次评审都新建）
2. 执行标准 review skill 流程（9 步流程）
3. 多维度评审（9 个并行 Agent）
4. 提交评审意见到 PR
5. 同步评审结果到 Issue
6. **清理评审工作树**（一次性使用）

**标准评审流程（严格遵循 review skill）**：

```bash
# Step 1: 创建评审工作树并获取 PR
git fetch origin pull/<PR号>/head:qwen-review/pr-<PR号>
git worktree add .qwen/tmp/review-pr-<PR号> qwen-review/pr-<PR号>
cd .qwen/tmp/review-pr-<PR号>

# Step 2: 加载项目评审规则（从 base branch）
qwen review load-rules main --out .qwen/tmp/qwen-review-pr-<PR号>-rules.md

# Step 3: 确定性分析
npm install
npm run typecheck  # TypeScript 类型检查
npx eslint --format=json <changed-files>  # ESLint 检查
pytest tests/ -v  # 运行测试

# Step 4: 并行启动 9 个评审 Agent（在单个消息中）
# ⚠️ 需要先配置 subagent（见前置条件）
# Agent 1: Correctness（正确性）
# Agent 2: Security（安全性）
# Agent 3: Code Quality（代码质量）
# Agent 4: Performance（性能）
# Agent 5: Test Coverage（测试覆盖）
# Agent 6a: Attacker Mindset（攻击者视角）
# Agent 6b: 3AM Oncall（运维视角）
# Agent 6c: Maintainer（维护者视角）
# Agent 7: Build & Test（构建测试）

# Step 5: Deduplicate, verify, and aggregate findings
# 合并重复发现，验证每个发现，聚合相同模式

# Step 6: Iterative reverse audit（反向审计）
# 启动反向审计 Agent，查找遗漏问题（最多 3 轮）

# Step 7: Present findings（呈现评审结果）
# 按严重程度分类：Critical, Suggestion, Nice to have

# Step 8: Autofix（可选）
# 如果有 Critical/Suggestion 问题，询问是否自动修复

# Step 9: 使用 Create Review API 提交评审
# ⚠️ Self-PR 检测：如果是自己的 PR，自动降级为 COMMENT
write_file(".qwen/tmp/qwen-review-pr-<PR号>-review.json", JSON格式)
gh api repos/{owner}/{repo}/pulls/{PR号}/reviews \
  --input .qwen/tmp/qwen-review-pr-<PR号>-review.json

# Step 10: 保存评审报告
mkdir -p .qwen/reviews
# 保存到 .qwen/reviews/<timestamp>-pr-<PR号>.md

# Step 11: 清理评审工作树
git worktree remove .qwen/tmp/review-pr-<PR号>
git branch -D qwen-review/pr-<PR号>
```

**评审维度详解**：

| Agent | 维度 | 职责 |
|-------|------|------|
| Agent 1 | Correctness | 逻辑错误、边界情况、类型安全、并发问题 |
| Agent 2 | Security | 注入、XSS、认证授权、敏感数据暴露、硬编码密钥 |
| Agent 3 | Code Quality | 代码风格、命名、重复、注释、死代码 |
| Agent 4 | Performance | 性能瓶颈、内存泄漏、算法效率、Bundle 影响 |
| Agent 5 | Test Coverage | 测试覆盖、分支测试、断言有效性、集成边界 |
| Agent 6a | Attacker Mindset | 恶意输入、攻击场景、最尴尬的 bug |
| Agent 6b | 3AM Oncall | 故障诊断、日志清晰度、静默失败 |
| Agent 6c | Maintainer | 维护性、隐式假设、未来踩坑点 |
| Agent 7 | Build & Test | 构建验证、测试执行、编译错误 |

**Self-PR 处理**：
```bash
# 检测是否为自己的 PR
gh pr view <PR号> --json author --jq '.author.login'

# 如果是自己的 PR：
# - 禁止 APPROVE 和 REQUEST_CHANGES
# - 只能提交 COMMENT
# - 评审内容仍然有效，只是 event 类型降级
```

---

### Phase 3: 循环迭代判断

```
┌─────────────────────────────────────────────────────────────────┐
│                                                                 │
│  ┌───────────────────┐                                         │
│  │ Phase 1: 修复 Agent │（工作树 A）                            │
│  │ 实施修复 + 提交 PR  │                                         │
│  └───────────────────┘                                         │
│           │                                                      │
│           ▼                                                      │
│  ┌───────────────────┐                                         │
│  │ Phase 2: 评审 Agent │（工作树 B）                            │
│  │ 确定性分析 + 9维度 │                                         │
│  │ 评审 + 提交意见     │                                         │
│  │ 清理工作树 B        │                                         │
│  └───────────────────┘                                         │
│           │                                                      │
│           ▼                                                      │
│      ┌──────────────┐                                           │
│      │  评审结论？   │                                           │
│      └──────────────┘                                           │
│           │                                                      │
│    ┌──────┴──────┐                                              │
│    │             │                                              │
│  Approve      Critical/Suggestion                              │
│    │             │                                              │
│    │             ▼                                              │
│    │      ┌──────────────┐                                      │
│    │      │ 迭代次数 < 3？│                                      │
│    │      └──────────────┘                                      │
│    │         │        │                                         │
│    │        Yes       No                                        │
│    │         │        │                                         │
│    │         ▼        ▼                                         │
│    │    回到 Phase 1  人工介入                                   │
│    │    （修复 Agent  在工作树 A                                │
│    │     根据评审意见  继续修复）                                │
│    │         │                                                  │
│    │    ┌────┴────┐                                             │
│    │    │ 循环迭代 │                                             │
│    │    │ 直到     │                                             │
│    │    │ Approve │                                             │
│    │    └─────────┘                                             │
│    │                                                            │
│    ▼                                                            │
│  ┌───────────────────┐                                         │
│  │ Phase 4: 安全合并  │                                         │
│  │ 确认 CI 通过       │                                         │
│  │ 合并 PR            │                                         │
│  │ 清理工作树 A        │                                         │
│  │ 关闭 Issue         │                                         │
│  └───────────────────┘                                         │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

**终止条件（退出循环）**：
1. ✅ **评审 Agent 给出 Approve**（无 Critical 问题）
2. ✅ **所有核心 CI 检查通过**
3. ✅ **修复 Agent 确认问题彻底解决**

**继续循环的情况**：
1. ⚠️ 评审 Agent 发现 **Critical 问题** → 修复 Agent 必须修复
2. ⚠️ 评审 Agent 发现 **Suggestion 问题** → 修复 Agent 可选择修复
3. ⚠️ **CI 检查失败** → 修复 Agent 必须修复

**迭代上限**：
- 最多 **3 轮迭代**
- 超过 3 轮后，**人工介入**
- 每轮迭代需更新 PR 和 Issue 评论

---

### Phase 4: 安全合并

评审通过后，执行合并流程：

```bash
# 1. 确保 CI 全部通过
gh pr checks <PR号>

# 2. 合并 PR (squash merge)
gh pr merge <PR号> --squash --delete-branch

# 3. 关闭 Issue (如果未自动关闭)
gh issue close <ID> --comment "$(cat <<'EOF'
## ✅ Issue #<ID> 已成功修复并合并

### 合并信息
- PR: #<PR号> (已合并)
- 合并方式: Squash merge
- CI 状态: 全部通过

### 修复总结
<修复内容>

### 评审结果
- 评审方式: 双智能体协同模式
- 评审结论: Approve
- 迭代次数: <N>

---
问题已彻底解决，关闭此 Issue。
EOF
)"

# 4. 回到主工作目录
cd /home/cfhan/open-ace
git checkout main
git pull origin main

# 5. 清理修复工作树
git worktree remove .qwen/tmp/fix-<ID>
```

---

## 📊 并行执行策略（多 Issue 同时修复）

```
┌─────────────────────────────────────────────────────────────────┐
│                        主控进程                                  │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  Issue #484                     Issue #476                      │
│  ┌────────────────┐            ┌────────────────┐               │
│  │ 工作树 A       │            │ 工作树 A       │               │
│  │ .qwen/tmp/     │            │ .qwen/tmp/     │               │
│  │ fix-484        │            │ fix-476        │               │
│  └────────────────┘            └────────────────┘               │
│                                                                 │
│  ┌────────────────┐            ┌────────────────┐               │
│  │ 修复 Agent     │            │ 修复 Agent     │               │
│  │ (Session 1)    │            │ (Session 2)    │               │
│  └────────────────┘            └────────────────┘               │
│         │                              │                        │
│         ▼                              ▼                        │
│  ┌────────────────┐            ┌────────────────┐               │
│  │ 工作树 B       │            │ 工作树 B       │               │
│  │ .qwen/tmp/     │            │ .qwen/tmp/     │               │
│  │ review-pr-XXX  │            │ review-pr-YYY  │               │
│  └────────────────┘            └────────────────┘               │
│                                                                 │
│  ┌────────────────┐            ┌────────────────┐               │
│  │ 评审 Agent     │            │ 评审 Agent     │               │
│  │ (Session 3)    │            │ (Session 4)    │               │
│  │ 9个子Agent并行 │            │ 9个子Agent并行 │               │
│  └────────────────┘            └────────────────┘               │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

**并行规则**：
1. ✅ **每个 Issue 使用独立的修复工作树**（避免并发冲突）
2. ✅ **每个评审使用独立的评审工作树**（一次性使用）
3. ✅ **修复 Agent 和评审 Agent 完全独立**（不同 session）
4. ✅ **评审 Agent 启动 9 个并行 subagent**（单个消息中）
5. ✅ **循环迭代独立执行**（每个 Issue 有自己的迭代循环）

---

## 📋 输出报告

修复完成后，生成汇总报告：

```
╔══════════════════════════════════════════════════════════════════╗
║                   多 Issue 并行修复报告                           ║
╠══════════════════════════════════════════════════════════════════╣
║ Issue #484: Request 和 Token 使用量统计问题                       ║
║   状态: ✅ 已合并                                                ║
║   PR: #498                                                      ║
║   迭代次数: 2                                                   ║
║   评审维度: 9 (并行执行)                                         ║
║   CI 状态: 全部通过                                              ║
║   耗时: XX分钟                                                   ║
╠══════════════════════════════════════════════════════════════════╣
║ Issue #476: 项目管理页面 token 统计显示问题                       ║
║   状态: ✅ 已合并                                                ║
║   PR: #499                                                      ║
║   迭代次数: 1                                                   ║
║   评审维度: 9 (并行执行)                                         ║
║   CI 状态: 全部通过                                              ║
║   耗时: XX分钟                                                   ║
╚══════════════════════════════════════════════════════════════════╝
```

---

## ⚠️ 注意事项

### 工作树管理（关键）
1. **修复工作树**：每个 Issue 必须有独立的修复工作树，**保留直到合并完成**
2. **评审工作树**：每次评审新建独立工作树，**评审后立即清理**
3. **工作树命名**：使用 `.qwen/tmp/fix-<ID>` 和 `.qwen/tmp/review-pr-<PR号>`
4. **并发冲突**：修复 Agent 也使用独立工作树，避免多 Issue 同时修复时冲突

### 分支和提交规范
1. **分支命名**：统一使用 `fix/issue-<ID>` 格式
2. **提交信息**：使用 `fix(#<ID>): <描述>` 格式
3. **PR 标题**：使用 `fix(#<ID>): <标题>` 格式
4. **迭代提交**：使用 `fix(#<ID>): 根据评审意见优化 - <内容>` 格式

### 评审流程规范
1. **配置 subagent**：前置条件，必须先配置 9 个评审 subagent
2. **标准流程**：严格遵循 review skill 的 9 步流程
3. **Self-PR 检测**：评审提交时必须检测是否为自己的 PR，自动降级为 COMMENT
4. **并行启动**：9 个评审 Agent 在单个消息中并行启动

### 合合并迭代规则
1. **CI 检查**：合并前必须确保核心 CI 全部通过
2. **迭代上限**：最多 3 轮迭代，超过后人工介入
3. **终止条件**：Approve + CI 通过 + 问题彻底解决
4. **清理工作**：完成后清理所有工作树和分支

---

## 🔄 故障恢复

如果修复过程中断，可通过以下命令恢复：

```bash
# 查看所有 worktree
git worktree list

# 进入修复工作树继续
cd .qwen/tmp/fix-<ID>

# 查看当前状态
git status

# 查看评审工作树（如果未清理）
cd .qwen/tmp/review-pr-<PR号>
```

---

## 📚 参考：Issue #473 实际执行案例

**完整流程记录**：
- Memory 文档：`/home/cfhan/.qwen/projects/-home-cfhan-open-ace/memory/dual-agent-fix-process.md`
- Issue：https://github.com/open-ace/open-ace/issues/473
- PR：https://github.com/open-ace/open-ace/pull/497

**执行结果**：
- 修复：114 行代码，解决核心问题
- 评审：独立工作树 + 多维度分析，Approve
- CI：全部通过（12 pass, 2 skip）
- 合并：Squash merge，Issue 已关闭

**改进建议（已整合到本文档）**：
- ✅ 修复 Agent 独立工作树
- ✅ 循环迭代机制
- ✅ 标准 review skill 流程
- ✅ 配置 subagent 说明
- ✅ 并发修复支持

---
*此 Skill 封装了完整的双智能体协同流程，基于 Issue #473 实际执行经验和改进建议。*
