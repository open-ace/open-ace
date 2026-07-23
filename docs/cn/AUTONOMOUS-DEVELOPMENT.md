# AI 自主开发

本文面向使用、部署和维护 Open ACE AI 自主开发功能的用户与开发者，说明当前实现的功能边界、工作流生命周期、三会话设计、CI 自愈、隔离执行、用量统计和前端可观测性。

> 本文描述的是仓库当前实现。修改自主开发代码时，应同时更新本文、英文版文档和相应回归测试。

## 1. 功能概览

AI 自主开发把一条需求或一个 GitHub Issue 转换为可审计的软件交付流程：

1. 准备独立分支和工作树；
2. 生成方案并独立审查方案；
3. 根据审查意见收敛最终方案；
4. 实现代码并运行定向测试；
5. 创建或更新 Pull Request；
6. 独立审查代码，按意见修复并复审；
7. 生成最终报告；
8. 等待并检查 GitHub CI，必要时自动修复；
9. 处理主分支同步或合并冲突，合并 PR，并清理分支和工作树。

工作流的目标不是让一个 Agent 连续执行一段不可见的脚本，而是把关键决策、AI 会话、代码变更、测试、审查、重试和失败原因记录为可恢复的里程碑。

### 1.1 适用范围

适合：

- 需求边界明确、可以通过代码和测试验证的 GitHub Issue；
- 需要方案审查、代码审查和 CI 闭环的中小型改动；
- 多 Issue 串行批处理；
- 需要在时间线中查看 Token、请求数、会话和代码差异的受控自动化。

不适合：

- 需要生产环境凭据或任意管理员权限的任务；
- 无法通过仓库内测试、CI 或明确验收条件判断完成度的任务；
- 要求 Agent 直接修改受保护 Git 元数据、绕过分支保护或自行合并未通过检查的代码。

## 2. 用户操作

### 2.1 创建工作流

在 AI 自主开发页面中选择项目、CLI 工具、模型和需求来源。需求可以是文本，也可以是 GitHub Issue URL 或编号。

创建时会固化一份 `definition_snapshot`，用于保留当时的需求、工具、模型、分支策略和批次信息。后续配置变化不应偷偷改变已运行工作流的定义。

分支策略包括独立 worktree、新分支和当前分支。批处理会强制使用独立 worktree，并为同一批次锁定共同的 `origin/main` 基线，避免后创建的工作流看到前一个工作流尚未合并的中间状态。

### 2.2 暂停、恢复与停止

- **暂停**：冻结正在运行的 Agent 进程并保留工作流状态；人工暂停不会被调度器自动恢复。
- **恢复**：继续被冻结的进程，或按当前阶段重新进入调度。
- **停止**：终止当前 Agent，并把工作流置为 `cancelled`；同一批次中尚未开始的后续工作流也会取消。
- **失败后重试**：仅适用于 `failed` 或 `planning_timeout`，从持久化的当前阶段恢复。

暂停和停止不是同义操作。新增状态或错误分支时，不得破坏这两个按钮在活动状态下的可用性。

### 2.3 里程碑操作

- **查看定义/方案/审查/报告**：打开持久化内容；
- **查看代码变更**：按里程碑或整个 PR 查看 diff 和增删统计；
- **查看会话**：打开该里程碑所属的稳定会话线；
- **取消轮次**：取消目标里程碑之后的步骤，进入 `wait`，等待用户反馈；
- **从此处分叉**：复制分叉点之前的历史，创建独立工作流和工作树；
- **反馈后继续**：把用户反馈记录为新里程碑，再回到对应流程。

## 3. 领域模型

### 3.1 Workflow

`autonomous_workflows` 是流程级状态，保存：

- 当前阶段、状态、开发轮次和错误；
- 项目、分支、worktree、PR 和批次信息；
- `main_session_id`、`review_session_id`、`test_session_id`；
- Token、输入/输出 Token 和请求数汇总；
- CI 修复次数、失败指纹和诊断状态；
- 暂停、超时、用户反馈和恢复上下文。

### 3.2 Milestone

`workflow_milestones` 是时间线中的审计单元。一个里程碑只描述一次明确事件，例如方案生成、方案审查、实现、测试、PR 审查、CI 诊断或冲突修复。

里程碑保存自己的增量用量 `phase_*`，以及会话、内容、提交、diff 统计和错误。它不是工作流累计用量的副本。

### 3.3 Agent session

`agent_sessions` 保存 Open ACE 的稳定会话身份和 CLI/模型提供商的实际会话 ID。恢复会话时可能替换底层 provider transcript，但不应替换工作流的稳定会话线身份。

## 4. 生命周期和状态机

正常阶段顺序为：

```text
preparation → planning → development → pr_review → report → merge
```

`wait` 是用户反馈等待阶段，不属于线性 `PHASE_ORDER`；它可以从取消轮次或反馈流程进入，再返回合适的业务阶段。

主要状态：

| 状态 | 含义 |
|------|------|
| `queued` | 批处理中等待前序工作流 |
| `pending` | 可由调度器启动 |
| `preparing` | 准备仓库、分支和 worktree |
| `planning` | 方案生成、审查和定稿 |
| `developing` | 实现与测试 |
| `pr_review` | PR 审查、修复和复审 |
| `reporting` | 生成最终报告 |
| `waiting` | 等待用户反馈 |
| `merging` | CI 检查、修复、同步和合并 |
| `paused` | 人工暂停、应用配额暂停或上游硬配额暂停 |
| `planning_timeout` | 方案阶段超时，等待延时或重试 |
| `completed` | PR 已合并且收尾完成 |
| `failed` | 自动恢复边界已耗尽，需要人工处理 |
| `cancelled` | 用户停止或批次级取消 |

持久化状态是恢复依据。进程重启后不得仅依赖内存中的 Agent、锁或 SSE 连接来判断下一步。

## 5. 严格的三会话设计

每个工作流只维护三条稳定会话线：

| 会话线 | 持久化字段 | 覆盖里程碑 |
|--------|------------|------------|
| `main` | `main_session_id` | 方案生成、方案收敛、开发、PR 修复、最终总结和 CI 修复 |
| `review` | `review_session_id` | 方案审查和 PR 代码审查 |
| `test` | `test_session_id` | 各开发轮次的测试与验证 |

三条线在多个里程碑间通过 resume 复用，目的是：

- 让实现 Agent 保留需求、方案和已做改动的连续上下文；
- 让审查 Agent 独立于实现者，避免同一上下文自我确认；
- 让测试 Agent 独立设计验证矩阵，而不是只接受实现者声明；
- 让 UI、用量统计和问题排查有稳定身份。

### 5.1 上下文溢出

底层模型会话达到上下文上限时，系统会：

1. 识别 provider 的 input/context overflow 错误；
2. 清除该稳定会话线与旧 provider transcript 的绑定；
3. 使用自包含、精简的提示重新调用；
4. 把新 provider 会话重新绑定到同一个 Open ACE 会话行；
5. 保留失败尝试已经产生的用量。

因此上下文恢复不会创建“第四条会话线”。任何修改都必须保持 `main / review / test` 三字段是工作流的唯一稳定拓扑。

## 6. 调度、并发和批处理

`AutonomousScheduler` 周期性扫描活动工作流，最多并行推进 3 个工作流。这是模块级硬上限（`MAX_CONCURRENT_WORKFLOWS = 3`，见 `app/services/autonomous_scheduler.py`），并非运维可调项，修改需改动代码。

调度同时执行三层互斥：

- **数据库锁**：防止多实例同时推进同一工作流；
- **工作空间锁**：防止两个工作流修改同一实际 checkout；
- **分支锁**：防止同一分支被两个 worktree 同时使用。

`waiting` 会占用用户的活动工作流额度，但不会作为批次中“正在执行”的 Agent。批处理同一时间只推进一个工作流；前序 `paused` 或 `cancelled` 会阻塞队列，前序完成、失败或进入可推进的等待状态后才评估下一项。

服务停止时，调度器先通知正在运行的 orchestrator 收尾；启动时会清理上次遗留的 Agent 进程并把不确定状态置为可检查状态，避免重复推进。

## 7. Git、PR 和变更边界

### 7.1 Worktree 优先

独立 worktree 是默认的隔离策略。工作流保存 `preferred_worktree_path`，即使冲突处理临时移除了原 worktree，后续 CI 修复也必须先恢复同一 PR 分支的 worktree，再消耗修复次数。

### 7.2 Agent 不直接管理受保护 Git 元数据

Agent 负责修改工作树文件。创建分支、提交、推送、PR、同步主分支、冲突提交和合并由受控的 `GitHubOps` 完成。提示词和命令过滤都不能作为唯一安全边界，操作系统权限才是最终边界。

### 7.3 有效变更范围

验证不能只比较 Agent 启动前后的本地 `HEAD`。本地 worktree 可能已经包含：

- 上一次推送失败留下的未推送提交；
- 冲突解析产生的临时提交；
- 被中断轮次留下的文件；
- 主分支同步提交。

当前实现以远端 PR head 和合并前有效 PR diff 为基线：

- 保留远端 PR 已有的合法变更；
- 只接受本轮范围内的新修改；
- 拒绝超出需求或文件数上限的扩散；
- 主分支同步本身不算一次 AI 修复；
- Agent 没有产生新改动时，不能把“命令运行过”当作修复成功；
- 若本地已有待推送合法提交，仍需验证并推送，不能误报“无代码变更”。

## 8. 开发、测试与独立审查

开发阶段由 `main` 会话实现最终方案。随后 `test` 会话根据方案和实际 diff 设计定向验证矩阵，并运行仓库可用的检查。

PR 审查由 `review` 会话执行。审查结论是结构化信号，而不是仅凭自然语言长度判断。存在实质问题时，`main` 修复后再次由 `review` 复审；最终摘要应反映实际落地情况和仍然存在的风险。

仓库声明的运行时优先于 Open ACE 服务自身的运行时。例如服务进程使用 Python 3.9，不代表 Agent 可以把声明 Python 3.11 的目标仓库降级为 3.9 语法。

## 9. 合并阶段和 CI 自动修复

### 9.1 检查顺序

合并阶段按以下顺序处理：

1. 获取 PR 和检查状态；
2. 如果 PR 分支落后于 `main`，先同步主分支并等待新一轮 CI；
3. 对失败检查收集完整、可操作的日志；
4. 构造本地复现要求和仓库运行时契约；
5. 由 `main` 会话修复；
6. 运行对应命令和隔离的 pre-commit 收敛；
7. 验证有效变更范围，提交并推送；
8. 等待下一轮 CI；
9. 检查失败指纹是否真正变化；
10. 检查通过后合并并清理。

主分支同步、worktree 恢复和等待 CI 日志都不消耗 AI 修复次数。只有具备可操作日志、真正启动 Agent 的修复才计数。

### 9.2 诊断与重试边界

- CI 日志暂不可用时最多轮询 6 次，不让 Agent 盲猜；
- 自动 CI 修复最多 3 次；
- pre-commit 最多收敛 3 轮；
- 同一失败指纹在代码已变化后仍完全不变，会提前停止；
- 没有日志时的降级指纹不能触发“失败未变化”误判；
- cancelled check 不作为需要修复的代码失败；
- runner 失败、无输出、上下文溢出和“无新代码”必须分别记录，不能都折叠成同一个提示。

CI 修复提示要求 Agent 先查看 `.github/workflows/`、`package.json`、`Makefile`、`tox.ini`、`pytest.ini` 和 `scripts/`，用 CI 实际命令复现，而不是只跑它认为相关的少量测试。

### 9.3 合并冲突

冲突解析在临时隔离 worktree 中进行，并绑定当前 PR 分支。解析结果仍需经过范围校验，不能把主分支或其他工作树中的无关变化带入 PR。成功推送后再恢复常规合并流程。

## 10. 错误分类和恢复策略

错误分类必须基于 runner 的结构化错误和零 Token 错误信封，不能扫描正常方案正文中的关键词，否则文档里提到 “rate limit” 也会误触发重试。

| 类型 | 示例 | 行为 |
|------|------|------|
| 瞬时网络/Git 错误 | TLS、连接重置、DNS、临时 push 失败 | 保持当前阶段，短周期重试；达到上限后失败 |
| 瞬时 API 错误 | 429、5xx、overloaded | 指数退避重试，最长约 30 分钟 |
| 百炼分配限速 | `usage allocated quota exceeded` | 按瞬时限速重试，**不得**转人工暂停 |
| 上游硬配额耗尽 | `platform quota exceeded` 等明确硬配额错误 | `paused`，标记为可人工恢复；恢复额度后人工继续 |
| Open ACE 应用配额 | 用户 Token/请求/费用配额超限或配额检查失败 | fail-closed 暂停；额度恢复后调度器自动恢复 |
| 上下文溢出 | maximum context/input length | 在同一稳定会话线上换新 provider transcript 重试 |
| CI 证据不足 | Actions 日志尚未生成或无权限 | 等待诊断；达到上限后失败并提示检查权限 |
| 仓库完整性异常 | `.git` 内容、inode、所有者或 ACL 被篡改 | fail-closed，退出码 68，要求人工检查 |

人工暂停、应用配额暂停和上游硬配额暂停虽然都使用 `paused`，但通过错误原因区分。只有应用配额暂停允许自动恢复；人工暂停和上游硬配额暂停必须由用户决定何时继续。

## 11. 跨用户隔离与安全边界

### 11.1 专用 Agent 账户

当前实现使用专用、无登录凭据的低权限账户 `openace-agent`（可通过 `OPENACE_AUTONOMOUS_AGENT_ACCOUNT` 或 `autonomous.agent_system_account` 配置），而不是以项目所有者或 Open ACE 服务账户运行代码 Agent。

该账户必须：

- 非 root；
- 不属于 `root`、`wheel`、`sudo` 或 `admin` 等管理组；
- 与项目所有者和服务账户不同；
- 只能通过受限的 `openace-run-as --isolated` sudoers 规则启动。

`/usr/local/libexec/openace-agent-bin` 中的受控命令守卫会约束 Agent 对 `git`、`gh`、Python 和 pytest 等关键命令的调用，避免把受限 sudo 入口变成绕过编排层的任意 Git/运行时操作入口。

### 11.2 文件权限

启动器：

- 从空环境开始，仅注入必要的 HOME、用户、语言、临时目录、Git safe.directory 和显式代理变量；
- 只给工作树文件授予 Agent 写 ACL；
- 给普通 clone 或 linked worktree 的 Git 元数据只读/遍历 ACL；
- 保留项目所有者对新文件的访问；
- 串行化同一隔离账户的启动；
- 无论正常结束、信号退出或下次恢复，都撤销临时 ACL 并杀死遗留 Agent 进程。

### 11.3 Git 完整性注册表

运行前，root 启动器把 `.git` 入口的类型、设备/inode、mode、owner/group、内容摘要和精确 ACL 快照原子写入 `/run`。运行后：

1. 先检查结构、内容、所有权以及 owner/other 权限未变化；
2. 只允许启动器自身可能引起的 POSIX ACL `mask::` 表示变化；
3. 验证所有基础和命名 ACL 项完全一致；
4. 恢复原 ACL 后再次做原始签名和 ACL 精确比较。

任何内容、inode、类型、owner、other 权限或非 mask ACL 改动都会 fail-closed。旧版两行注册表只在确实存在 ACL mask 的情况下允许一次兼容恢复，成功后立即升级到精确格式。

不要为“恢复运行”直接删除 `/run/openace-agent-*` 注册表。出现 `OPENACE_REPO_INTEGRITY_VIOLATION` 时，应先核对 worktree 注册、远端 PR head、`.git` 指针/目录和 ACL，再决定是否归档旧注册表并重建 worktree。

## 12. 用量统计和 AI Activity

### 12.1 用量

工作流总用量从每个里程碑自己的 `phase_total_tokens`、`phase_input_tokens`、`phase_output_tokens` 和 `phase_request_count` 重算。不能把跨里程碑复用会话的累计总量逐次相加，否则会重复计费。

runner 对 provider 的累计计数维护基线，只保存本次增量。上下文恢复和 API 重试产生的真实用量也必须合并到当前里程碑。

### 12.2 AI Activity

AI Activity 通过 SSE 实时传递 tool use、assistant 文本、usage、重试和系统事件。它是运行可观测性，不是持久化审计日志：

- `thinking_tokens` 是高频累计估算，不是离散活动，也不是权威用量，因此后端不向 UI 输出；
- 空 assistant 文本和单独的 `-` 不显示；
- SSE 连接立即返回 connected，并每 30 秒发送可见 keepalive；
- 活动阶段在相邻里程碑的调度间隙仍保留一个稳定面板宿主，避免闪现和消失；
- 首个 Token 较慢时显示友好的等待/心跳，不显示虚构的 `--:--:--` 时间；
- 长时间无活动才进入 stale 提示，不能把正常的大模型首包等待过早标为故障；
- 每个里程碑最多显示最近活动，完整会话从“查看会话”进入。

AI Activity 只挂在真正运行 Agent 的 planning、development、pr_review，以及明确的 merge 修复/冲突里程碑上。排队、准备、报告和用户等待阶段不能伪装成 AI 正在运行。

## 13. 时间线用户体验约束

时间线是运行控制面，也是主要的故障诊断入口。修改前端时应保持：

- 里程碑按数据库创建时间和自增 ID 稳定升序，前置系统事件必须出现在由它触发的 AI 修复之前；
- 活动工作流始终可见暂停和停止按钮；
- Header 在窄屏下压缩和换行，不覆盖统计卡片或最终方案/审查/代码变更按钮；
- 里程碑操作按钮可以换行；
- 方案定稿及其他 AI 里程碑显示自己的 Token、请求数和会话；没有新调用时明确标记“无新增 AI 用量”；
- 系统里程碑没有 AI 会话时不展示误导性的 0 Token；
- 全屏内容和 diff modal 内部可滚动，标题栏不因全屏按钮产生额外空行；
- 自动展开跟随最新活动里程碑，但用户手动折叠或查看旧里程碑后不抢夺焦点；
- 自动滚动仅在用户仍位于底部附近时启用。

## 14. 部署要求

标准安装脚本会配置：

- `/usr/local/bin/openace-run-as`；
- `/usr/local/libexec/openace-agent-bin`；
- `openace-agent` 系统账户；
- 仅允许隔离形式的 sudoers 规则；
- `setfacl`、`getfacl`、`flock`、`pkill`、Git、GitHub CLI 和对应 Agent CLI。

关键可调项：

| 配置 | 默认值 | 说明 |
|------|--------|------|
| `AUTONOMOUS_TASK_TIMEOUT` | `3600` 秒 | 单次 Agent 任务超时 |
| `AUTONOMOUS_MAX_CHANGED_FILES` | `60` | 自动变更文件数上限 |
| `OPENACE_AUTONOMOUS_AGENT_ACCOUNT` | `openace-agent` | 隔离 Agent 账户 |
| `OPENACE_RUN_AS` | `/usr/local/bin/openace-run-as` | 隔离启动器 |
| `OPENACE_AGENT_GUARD_BIN` | `/usr/local/libexec/openace-agent-bin` | 隔离环境的受控命令目录 |

本文档其他位置引用的内部限值同样是模块级常量（位于 `app/services/autonomous_scheduler.py` 和 `app/modules/workspace/autonomous/orchestrator.py`），并非运维可调项：

| 常量 | 默认值 | 用途 |
|------|--------|------|
| `MAX_CONCURRENT_WORKFLOWS` | `3` | 调度器同时推进的工作流数（另见 §6） |
| `MAX_CI_REPAIR_ATTEMPTS` | `3` | 合并阶段自动 CI 修复次数（§9.2） |
| `MAX_CI_DIAGNOSTICS_ATTEMPTS` | `6` | 失败日志暂不可用时的调度轮询上限（§9.2） |
| `MAX_PRE_COMMIT_CONVERGENCE_PASSES` | `3` | 隔离 `pre-commit` 收敛轮数（§9.2） |
| `API_RETRY_TOTAL_TIMEOUT` | `1800` 秒 | 瞬时 API 错误的最大退避总时长，约 30 分钟（§10） |
| `PLANNING_TIMEOUT` | `1800` 秒 | 规划阶段超时 |

升级旧安装时，应运行安装脚本的校验/升级流程，确保旧的宽权限 `openace-run-as` sudoers 文件已禁用。

## 15. API 概览

所有接口要求认证，并校验工作流所有者或管理员权限。

| 接口 | 用途 |
|------|------|
| `POST /api/autonomous/workflows` | 创建单个或批量工作流 |
| `GET /api/autonomous/workflows` | 查询工作流列表 |
| `GET /api/autonomous/workflows/:id` | 查询工作流详情 |
| `POST /api/autonomous/workflows/:id/pause` | 暂停 |
| `POST /api/autonomous/workflows/:id/resume` | 恢复 |
| `POST /api/autonomous/workflows/:id/stop` | 停止 |
| `POST /api/autonomous/workflows/:id/retry` | 失败/超时后重试 |
| `GET /api/autonomous/workflows/:id/timeline` | 查询里程碑 |
| `POST /api/autonomous/workflows/:id/milestones/:mid/cancel` | 取消轮次并等待反馈 |
| `POST /api/autonomous/workflows/:id/milestones/:mid/fork` | 从里程碑分叉 |
| `GET /api/autonomous/workflows/:id/events/stream` | SSE 活动流 |
| `GET /api/autonomous/workflows/:id/pr-diff` | PR diff |
| `GET /api/autonomous/workflows/:id/pr-stats` | PR 变更统计 |

完整字段和返回结构以 [API 文档](API.md) 与 `app/routes/autonomous.py` 为准。

## 16. 代码导航

| 文件 | 职责 |
|------|------|
| `app/routes/autonomous.py` | API、权限、暂停/恢复/停止、SSE、里程碑操作 |
| `app/services/autonomous_scheduler.py` | 调度、配额门、并发、批次和分布式锁 |
| `app/modules/workspace/autonomous/orchestrator.py` | 状态机、提示词、三会话、CI 修复、冲突和合并 |
| `app/modules/workspace/autonomous/agent_runner.py` | CLI 适配、会话恢复、活动与用量采集 |
| `app/modules/workspace/autonomous/github_ops.py` | 受控 Git/GitHub 操作 |
| `app/repositories/autonomous_repo.py` | Workflow、Milestone、锁和用量持久化 |
| `scripts/openace-run-as.sh` | 跨用户低权限启动、ACL 和 Git 完整性 |
| `app/modules/workspace/autonomous/agent_bin/` | 隔离环境中的 Git、GitHub 和运行时命令守卫 |
| `frontend/src/components/work/WorkflowTimeline.tsx` | 时间线、活动面板、控制和 modal |
| `frontend/src/components/work/WorkflowTimeline.utils.ts` | 活动宿主、过滤和 diff 工具 |

## 17. 修改设计时的回归矩阵

不要只验证本次报错的单个函数。至少覆盖完整生命周期：

### 17.1 会话和用量

- 三条稳定会话线跨多个里程碑 resume；
- 上下文溢出后仍是同一稳定行；
- API 重试和上下文恢复用量不丢失、不重复；
- 方案定稿、测试、审查、CI 修复的会话和用量展示；
- `thinking_tokens` 和空 activity 不进入 UI。

### 17.2 CI 修复

- PR 分支落后主分支；
- 远端 head 与本地 head 不同；
- 本地存在未推送合法提交；
- worktree 被冲突解析临时移除；
- runner 非零退出、退出 0 但错误信封、无输出；
- 日志暂不可用、取消的 check、失败指纹变化/不变；
- Agent 无改动、真实改动、越界改动；
- pre-commit 修改文件、多轮收敛和缓存目录权限；
- 上下文溢出后最小上下文重试。

### 17.3 隔离执行

- 普通 clone 和 linked worktree；
- 无扩展 ACL、已有命名 ACL、继承 ACL；
- 连续多次正常运行；
- TERM 和 SIGKILL 后恢复；
- worktree 删除/重建；
- `.git` 内容、inode、mode、owner 和非 mask ACL 篡改；
- 旧两行注册表升级。

### 17.4 配额和恢复

- Open ACE 应用配额超限及恢复；
- 配额检查异常时 fail-closed；
- 人工暂停不自动恢复；
- 上游硬配额人工恢复；
- 百炼 `allocated quota exceeded` 继续自动重试；
- 正常正文提到 quota/rate limit 不触发错误分类。

### 17.5 前端

- 宽屏和窄屏 Header；
- 活动面板首事件前、里程碑切换间隙和长等待；
- 里程碑顺序相同时间戳时稳定；
- 操作按钮换行；
- 内容/diff modal 全屏滚动；
- 用户手动折叠、旧里程碑查看和自动滚动互不争抢。

建议优先运行：

```bash
pytest -q tests/issues/716 tests/unit/test_autonomous_ci_guardrails.py
pytest -q tests/unit/test_autonomous_timeline_session_identity.py
pytest -q tests/unit/test_upstream_quota_pause.py
pytest -q tests/issues/1395
cd frontend && npm test -- --run WorkflowTimeline
```

再根据改动范围运行 `tests/autonomous/`、相关 issue 回归和项目完整 CI。

## 18. 故障排查

| 现象 | 首要检查 |
|------|----------|
| 工作流长时间不推进 | 状态、`error_message`、调度器日志、DB 锁和同分支/worktree 冲突 |
| AI Activity 没有出现 | 当前是否为 Agent 阶段、是否有活动宿主里程碑、SSE/keepalive、稳定会话 ID |
| Activity 有一行横杠 | assistant 空文本过滤是否被绕过 |
| Token 异常放大 | 是否把累计 session 用量重复加到多个里程碑 |
| 百炼限速后停止 | 是否把 `allocated quota exceeded` 错归为硬配额 |
| CI 修复立即耗尽 | 是否在拿到日志、恢复 worktree、同步 main 之前增加 attempt |
| 修复后提示无改动 | 对比基线是否错误使用本地 HEAD，而不是远端 PR head |
| `.git` 完整性失败 | 核对注册表、ACL、worktree 指针、inode 和中断前后日志，不要直接删注册表 |
| 暂停后自动恢复 | 检查错误原因是否错误使用应用配额前缀 |
| modal 全屏无法滚动 | 检查 modal body、内容容器的 `min-height: 0` 和内部 `overflow: auto` |

## 19. 已知边界

- 实时 AI Activity 依赖当前服务进程内的 SSE，不作为跨重启完整日志；持久化审计以里程碑和会话为准。
- 调度器的进程内工作空间/分支集合用于快速互斥，多实例正确性依赖数据库锁和 Git 自身约束。
- 自动 CI 修复有明确次数上限，不会无限尝试。
- 上游 provider 的错误文案可能变化；新增适配时必须用零 Token 错误信封和结构化 runner 结果约束匹配范围。
- 隔离执行依赖 Linux ACL 和受控 sudoers；不满足这些条件时不应降级为以项目所有者身份执行。
