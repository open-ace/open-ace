# Plan: PR review verdict 不得被 artifact 评分门/解析器判空

- 日期：2026-08-17（v3，两轮独立审查：v1 抓 1 BLOCKING（解析器漏改），v2 复审
  zero-blocking 附 2 必改 + 1 建议，均已采纳）
- 背景：monitor-autonomous-workflows class-2，workflow `02dae370`（issue #2550 / PR #2578）
  于 2026-08-16 13:15 UTC failed，error="PR review agent returned no result"
- 状态：审查通过，进入实现

## 1. 根因（主证据验证 + 本地复现 + 两轮独立审查复核）

agent 严格按 review prompt 契约（`phases/pr_review.py` ~L510：机器可读单行
`REVIEW_RESULT: {"verdict":...,"blocking_findings":[...]}` 必须是 TL;DR 前最后一个
非摘要行）输出最小合规 verdict，但链路上有**两道闸门**都会把这种最小形态判死：

**闸门 1（提取层）**：`pr_review.py` `host.artifact_text` →
`pick_best_artifact_text`（artifact_text.py L256）只接受 score > −1 的候选；最小
合规回复（无 markdown 标题、3 段落）得分 102+0+0−160 = **−58** → 返回 `""` →
pr_review.py ~L592 判 "no result" → PhaseResult.failed（本次 failed 直接原因）。

**闸门 2（解析层）**：`orchestrator._parse_review_result`（~L1885）对同一文本
返回 None、`_review_is_approved` False，两个独立缺陷：
- 摘要行识别 `^TL;DR\s*:` 不认粗体 `**TL;DR**:` → 候选行错位；
- 契约行与 TL;DR 之间的独立 `---` 分隔行成为"最后一个非摘要行" → 索引不匹配。

只修闸门 1 的后果：APPROVE 被读成未通过 → 对已批准 PR 反复修复轮（静默走错方向）。

实测矩阵（main 78ae2f8c，独立审查者复跑一致）：

| 输入 | `_parse_review_result` | 期望 |
|---|---|---|
| 契约行+`---`+`**TL;DR**:`（本案原文） | None | APPROVE |
| 契约行+`---`+`TL;DR:` | None | APPROVE |
| 契约行+`**TL;DR**:`（无分隔） | None | APPROVE |
| `All good.`+契约行（对照） | 正常解析 | APPROVE ✓ |
| `pick_best_artifact_text(本案原文)` | `""` | 非空 |

边界（实测）：F2 = findings 文本含 ≥2 过程词时 `_is_process_paragraph` 丢契约段；
F1 = 过程性开头 + 后文标题时切片层（`_slice_from_structured_start` **和**
`clean_agent_text` 的标题切片）删掉其前的契约行（后者为实现时自查发现的第三处
同型切片，v2 审查未点名，由端到端 F1 测试锁定）。

- `_review_is_approved`（orchestrator.py:6923）纯粹以 `_parse_review_result` verdict
  为准——两道闸门都修好全链路才通。
- JSONL 主证据：`/run/openace-agent-tasks/be3dbf69-*.claude-preserve/projects/
  -home-qlfan-openace-pr----worktrees-02dae370-*/f149678b-*.jsonl` 最后一条 assistant。
- origin/main（8d1e4aeb）pr_review.py 已有空文本时 fresh-session 重试 band-aid
  （归因"resume no-op"）；与本修复正交共存。

## 2. 方案（选定 A，弃 B/C）

**A. 契约行在清洗/评分/解析三层都被视为一等结构化内容**。弃 B（phase 层再叠
fallback）与 C（唯一候选不判空，废噪声门）。

已知限制（接受，如实记录）：`_extract_tldr`/`_TLDR_RE` 对粗体 TL;DR 提取仍不
命中（外观问题，milestone tldr 优先 structured_tags）；评分放宽后，行首
`REVIEW_RESULT: {…}` 回显的候选从拒收变为可发布（实践中仅 review phase 产出）。

## 3. 改动清单

1. `constants.py`（叶子模块）新增共享契约模式（注释与 pr_review prompt 契约三方
   同步维护）：
   - `REVIEW_RESULT_LINE_FULLMATCH_RE`（orchestrator `_REVIEW_RESULT_LINE_RE`
     原样搬入，orchestrator 改导入，行为不变；全库仅 orchestrator 两处内部引用）
   - `REVIEW_RESULT_LINE_SEARCH_RE`（`(?m)` search 版，评分/结构判定用）
   - `REVIEW_TLDR_LINE_RE`（粗体容忍 `^\*{0,2}TL;DR\*{0,2}\s*:`，IGNORECASE）
   - `REVIEW_SEPARATOR_LINE_RE`（`^(?:-{3,}|\*{3,}|_{3,})$`；**按 strip 后的行
     匹配**，容忍尾随空白）
2. `artifact_text.py`（闸门 1 + F1/F2）：
   - 新增 `_first_structured_start(text)`：结构行与契约行**取最早命中位置**
     （禁用短路 or——v2 审查实测短路实现修不了 F1）；`_slice_from_structured_start`
     与 `clean_agent_text` 的标题切片都改用它（F1，覆盖三处切片中的两处）
   - `score_artifact_text`：`has_structure` 追加契约行 search 命中；TL;DR 加分改
     `re.search(r"TL;DR\*{0,2}\s*:", text)`；**头部过程词惩罚在文本含契约行时
     豁免**（F2 端到端必需：否则 findings 引用过程词时 −400 惩罚仍压到 −116 判空）
   - `_is_process_paragraph`：契约行 fullmatch 命中 → 不判过程段（F2 sanitize 层）
3. `orchestrator.py` `_parse_review_result`（闸门 2）：
   - 候选行选择：剔除纯分隔行后取最后一个非摘要行；摘要行识别换
     `REVIEW_TLDR_LINE_RE`；**候选索引与 `result_line_indexes` 必须同一索引空间**
     （v2 审查提示：原始索引或过滤重建二选一，不可混用）
   - fence 检查/fullmatch/verdict+blockers 一致性校验全部保留（fail-closed 不变）
   - 删除本地 `_REVIEW_RESULT_LINE_RE` 定义，改从 constants 导入

## 4. 测试（required lane，先红后绿）

`tests/unit/test_autonomous_review_verdict.py` 新增：
- 三例决定性红测：契约行+`---`+`**TL;DR**:` / +plain `TL;DR:` / 无分隔粗体 →
  `_review_is_approved == True`
- 守门：`REQUEST_CHANGES` + 分隔 + 粗体 TL;DR 仍 False
- 守门（金丝雀，抓索引空间混用）：**多契约行 + 分隔行穿插仍 fail-closed**
- 既有 fence/多契约/旧 marker fail-closed 测试不动

`tests/unit/test_artifact_text_helpers.py` 新增 `TestMachineContractVerdicts`：
- 红：`pick_best_artifact_text(三段落最小合规)` 非空且含契约行
- 红：`score_artifact_text(sanitize(最小合规))` > −1
- 红：`AutonomousOrchestrator._artifact_text(AgentTaskResult(response_text=最小合规))`
  非空（直接覆盖 pr_review 取文路径）
- 红（F1 端到端）：过程性开头 + 契约行 + 后文标题 → 结果仍含 `REVIEW_RESULT`
- 红（F2 端到端）：findings 含 "let me"+"working directory" → `_artifact_text`
  非空
- 绿守门：纯过程噪声 `pick_best` 仍 `""`
- 绿守门：sanitize 保留契约行与 TL;DR 行

## 5. 风险与回退

- 噪声门：契约行需完整 JSON 形态才计结构分；纯噪声不含契约行仍被拒（守门测试
  锁定）。头部惩罚豁免仅在文本含契约行时生效，且此类文本仅 review phase 产出。
- 解析器：分隔行剔除只影响候选行选择；fence/形态/verdict 校验保留，fail-closed
  语义不变（多契约行金丝雀 + REQUEST_CHANGES 守门锁定）。
- 回退：三层各自单点收紧即可，无 schema/接口变更。

## 6. 流程约束

- 隔离 worktree `/private/tmp/fix-review-verdict-artifact-gate`
  （branch `fix/pr-review-verdict-artifact-gate`，base origin/main 8d1e4aeb）
- TDD：先写失败测试看红 → 最小实现看绿 → 重构
- PR 文本禁 `closes|fixes|resolves` + #N（`fix(#N)` 前缀也算）
- CI required：lint / test(3.10) / test(3.11) / test(3.12) / build 全绿后 merge
- 部署：app/ 热补丁 cp+chown openace + `systemctl restart openace-scheduler.service`
  （无 DB 迁移，不动 alembic）
- 部署后 reset workflow `02dae370`：status=`pr_review`（pickup 态），恢复
  worktree_path（COALESCE preferred），清 ci_repair 计数/锁/error
- PR #2578 lint/test 全红属第一类（预先存在的基础设施问题）：reset 后交自主系统
  CI-repair，不手动改 PR
