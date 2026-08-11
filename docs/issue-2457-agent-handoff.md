# Issue #2457 Agent 交接：Legacy issue quarantine 失败基线

> 任务来源：[#2457](https://github.com/open-ace/open-ace/issues/2457)
> Umbrella：[#2429](https://github.com/open-ace/open-ace/issues/2429)
> 初始证据：[Extended Tests run 31313240896](https://github.com/open-ace/open-ace/actions/runs/31313240896)
> 本文状态：实施交接与审查契约；完成实现后由 #2429 当前负责人审查。

## 给实施 Agent 的启动指令

1. 从包含本文的 `codex/2457-agent-handoff` 分支创建自己的 worktree/工作分支；
   不要从旧的 #2455/#2456 分支继续。
2. 完整阅读本文、`docs/TEST_LAYERS.md`、`tests/issues/README.md`、
   `.test-baseline.json`、`scripts/run_extended_tests.py`、`ci/suites.json` 和
   `.github/workflows/extended-tests.yml` 后再修改代码。
3. 先在 #2457 留一条简短实施计划，明确 baseline schema、fail-closed completeness
   和 workflow 聚合方式；如需改变本文 P0 契约，等待确认后再做。
4. 在同一分支持续实现并创建 Draft PR；不要另外复制一份交接文档。
5. 完成本文第 7 节自验后，在 #2457 通知当前负责人按第 8 节审查。

## 1. 要交付的结果

为 `tests/issues/` legacy quarantine 建立机器可读、可审查、fail-closed 的
known-failure baseline。Nightly 必须同时满足：

1. 已审查的历史失败仍清楚出现在 summary 和 artifact 中，但不会让 nightly
   因同一批已知失败永远保持红色。
2. 任意新增 collection error、执行 error 或 assertion failure 都让权威 gate
   失败。
3. 已知失败恢复为绿色时，CI 要求收缩 baseline，不能长期保留过期豁免。
4. 报告缺失、XML 损坏、分片被取消或结果不完整时必须失败，不能把“没跑完”
   当成“没有新失败”。
5. Baseline 只能由经过 review 的仓库变更更新；CI 不得自动接受当前结果。

完成后请创建 Draft PR，正文包含 `Refs #2457`，并逐项附上本文验收证据。
不要在全部验收完成前关闭 #2457。

## 2. 当前事实基线

以 2026-08-09 的 `main` 为准：

- `.test-baseline.json` 记录 `tests/issues/` 共 4,473 个可收集 item、446 个文件；
  这是防止测试静默消失的 collection baseline，不是本任务要建立的失败豁免表。
- `python scripts/ci.py run issue-collection` 已是 required PR gate；collection error
  当前应为零。
- `.github/workflows/extended-tests.yml` 每夜把 legacy issues 分成 4 个 shard，使用
  `--isolated-home --reruns 1 --timeout 240 --junitxml ...` 执行。
- 首次合并后全量运行中，已完成的三个 shard 共报告 3,429 项：

  | Shard | Tests | Failures | Errors | Test step duration |
  |---|---:|---:|---:|---:|
  | 2/4 | 1,024 | 86 | 44 | 约 17m42s |
  | 3/4 | 958 | 73 | 10 | 约 15m16s |
  | 4/4 | 1,447 | 117 | 113 | 约 27m00s |

  合计为 276 failures、167 errors。Shard 1 当时仍在运行，因此这三个 artifact
  **不是完整初始 baseline**，不得直接提交为全量基线。
- 真实 JUnit 使用 pytest xUnit XML；test identity 当前主要由 `<testcase
  classname="..." name="...">` 表达。初始 artifact 未显式携带完整 pytest
  `nodeid`，实现必须可靠恢复 identity，或为后续报告增加稳定的 nodeid property，
  并兼容现有 artifact。
- 失败主要聚类为旧 SQLite schema/fixture、认证和 tenant 契约漂移、过期 mock、
  缺失 async/HTTP/Playwright fixture，以及依赖旧运行环境的 UI/API 工具。

开始编码前重新读取 #2457 及其最新评论，并确认上述 workflow run 是否已生成
shard 1 artifact。初始 baseline 必须来自同一 commit、同一运行契约下的一组完整
报告；若单个运行因超时无法完成，可用更多临时非重叠批次采集，但必须证明所有
预期 nodeid 被完整覆盖一次。

## 3. 范围边界

### 本任务包含

- 解析一个或多个 pytest JUnit XML，并归一化为稳定 failure records。
- 合并任意数量、任意分片方式的报告；结果不能依赖当前固定 4 路分片。
- 生成候选 baseline 的显式命令，以及只读 compare 命令。
- 对当前结果与 tracked baseline 做双向差异检查。
- 生成机器可读 diff、Markdown summary 和可下载 artifact。
- 把 comparator 接入完整 nightly/manual issue run，并让最终 quality gate 使用
  comparator 的结论。
- 添加足够的 unit/contract tests 与维护文档。

### 本任务不包含

- 一次性修复、删除或迁移所有 legacy tests。
- 修改测试目录分类契约；继续遵守 `docs/TEST_LAYERS.md`。
- 实施 #2458 的历史耗时分片算法。Baseline 合并必须与分片算法解耦。
- 实施 #2459 的 workflow-dispatch 去重。
- 通过放宽 timeout、删除断言、批量 `skip`/`xfail`、降低 collection baseline，
  或吞掉 pytest 退出码来制造绿色结果。
- 把 `tests/issues/` 升级成 PR required 全量执行。PR 仍只 required collection 和
  已提升的 `legacy-pr` suite；全量执行属于 nightly/manual。

如果实现确实需要触碰以上非目标，请先在 #2457 留言说明阻塞关系，不要静默扩 scope。

## 4. 必须遵守的设计契约

### 4.1 两类 baseline 不得混淆

- `.test-baseline.json`：测试 item/file 数量下限，证明测试仍能收集且没有静默消失。
- 新 failure baseline：只描述当前已审查的 legacy 非绿色结果。

两者必须同时生效。Failure comparator 不得替代 `issue-collection`，也不得通过修改
`.test-baseline.json` 掩盖缺失报告。

### 4.2 Baseline identity 与 schema

Tracked 文件应使用有版本号的 JSON，路径和命名可在 PR 中确定，但必须满足：

- 顶层有 schema `version`。
- 有 reference commit、source workflow run URL/ID、运行契约和生成命令等 provenance。
- entries 按稳定键排序，重复生成 byte-stable。
- 每条至少包含：
  - 完整 pytest `nodeid`；
  - 所属 issue number；
  - outcome（至少区分 `failure` 与 `error`）；
  - 稳定 error category；
  - 可审查的异常类型或短摘要。
- 绝对 runner 路径、时间戳、端口、临时 HOME、内存地址和大段 traceback 不得进入
  identity key。
- 不能只以 error message hash 为 identity；message 漂移不能让同一个失败无限新增。
- 同一 nodeid 的重复或冲突结果不得静默覆盖，必须明确合并或报错。

推荐 comparison key 为 `(nodeid, outcome, category)`；异常类型和归一化摘要用于
review 展示。若采用其他 key，请在 PR 中解释为什么更稳定。

### 4.3 分类至少要区分

- collection/import error；
- setup/fixture error；
- teardown error；
- timeout；
- assertion failure；
- test-body exception；
- runner/infrastructure failure（报告缺失、取消、损坏、进程异常等）。

分类规则必须版本化并由 fixture XML 测试覆盖。当前 collection gate 已清零，因此
任何 collection error 都应硬失败，不允许新增到 baseline。

### 4.4 双向 diff

Comparator 至少输出：

- `known`: 当前仍存在且与 baseline 匹配；
- `new`: 当前出现但 baseline 不存在；
- `resolved`: baseline 中存在但当前已恢复；
- `changed`: 同一 nodeid 的 outcome/category 发生变化；
- `invalid/incomplete`: 报告或覆盖证据不可信。

退出语义：

- 只有 `known`，且结果完整：成功，但 summary 仍列出债务数量和主要聚类。
- 存在 `new`、`changed`、collection error 或 `invalid/incomplete`：失败。
- 存在 `resolved`：失败并要求删除 stale baseline entry；这样才能保证 baseline
  持续收缩。若选择不同退出策略，必须提供同等强度、不可忽略的收缩门禁。

### 4.5 Fail-closed 完整性

只解析“碰巧上传成功的 XML”不够。权威 compare 必须证明结果完整：

- 所有预期 shard/batch artifact 均存在且 XML 可解析。
- 每个报告包含可信 suite totals，合并结果无意外重复。
- 执行前生成与 `-m "not postgres"` 相同选择语义的 machine-readable nodeid/file
  manifest；compare 验证预期 nodeid 均有终态结果。
- 完整运行的文件覆盖仍满足 `.test-baseline.json` 的 issues file baseline。
- job timeout、workflow cancellation、缺失 artifact、零测试、部分 XML、未知 schema
  version 全部失败。
- Targeted `--issue-numbers` 运行必须在 summary 中标为 targeted，不能冒充完整
  nightly。若支持 targeted compare，只能与所选 issue 的 baseline 子集比较。

不要依赖 shard 编号作为 baseline identity；#2458 重排文件后，相同失败仍应匹配。

### 4.6 CI 中允许的非阻塞方式

Legacy pytest step 可以使用 step-level `continue-on-error` 作为“确保 JUnit 被上传”的
传输机制，但仅当：

1. pytest 原始退出码和 manifest 一并进入 artifact；
2. 一个 `if: always()` 的后置 comparator 下载全部报告并 fail closed；
3. `Nightly Quality Gate` 依赖 comparator 结论，而不是把 shard 的非阻塞状态当成绿；
4. comparator 缺席或 skipped 时最终 gate 失败。

禁止在没有权威 comparator 的情况下给整个 job/workflow 设置 `continue-on-error`。

### 4.7 Baseline 更新

- 默认命令只读，不修改 tracked 文件。
- 生成候选 baseline 必须使用显式子命令和显式输出路径。
- CI 不得把当前结果写回仓库、自动提交或自动扩 baseline。
- Baseline 增长必须在 PR diff 中逐条可见，并在 PR 正文说明原因和 source run。
- 初始 baseline 只能在完整 reference run 后生成；不允许手工抄写聚合数字代替 entries。

## 5. 推荐实施顺序

### Phase A：纯库与 fixture tests

1. 定义 versioned schema、record 类型和稳定排序。
2. 实现 JUnit parser、nodeid 解析/兼容、error classifier。
3. 实现多报告 merge、完整性验证和双向 diff。
4. 用最小 XML fixtures 覆盖 failure/error/skip、多个 suites、损坏 XML、重复 identity、
   缺失报告、collection error 和绝对路径归一化。

这一阶段不得依赖 GitHub API，保证本地与 Actions 使用完全相同代码。

### Phase B：CLI 与报告

建议提供类似接口（名称可调整，语义不可缺失）：

```bash
# 只读比较；默认不得修改 baseline
python scripts/ci/legacy_issue_baseline.py compare \
  --baseline ci/legacy-issue-failures.json \
  --junit 'test-results/issues-*.xml' \
  --manifest 'test-results/issues-*.manifest.json' \
  --json-output test-results/legacy-issue-diff.json \
  --markdown-output test-results/legacy-issue-summary.md

# 显式生成候选文件，供人工 review
python scripts/ci/legacy_issue_baseline.py snapshot \
  --junit 'test-results/issues-*.xml' \
  --manifest 'test-results/issues-*.manifest.json' \
  --source-run 31313240896 \
  --output /tmp/legacy-issue-failures.candidate.json
```

CLI 错误必须给出可行动信息：缺哪个报告、哪个 nodeid 新增、哪个 baseline entry 已
resolved，以及应运行什么命令生成候选 diff。

### Phase C：runner 与 workflow

1. 让每个完整 issue batch 上传 JUnit、machine-readable manifest、pytest exit code 和
   server log。
2. 新增独立 comparator job，使用 `if: always()` 下载并合并所有 issue artifacts。
3. 上传 JSON diff、Markdown summary、原始 JUnit/manifest；summary 展示 known/new/
   resolved/changed/invalid 数量及按 issue/category 聚类。
4. 让 `Nightly Quality Gate` 使用 comparator 的权威结果。
5. `workflow_dispatch category=issues/all` 也执行相同 compare，以便 PR 分支做真实验证。
6. 不改变 PR required `PR Gate` 的短路径，也不把昂贵全量 issue execution 加到每个 PR。

### Phase D：初始 snapshot 与真实验证

1. 从同一 commit、相同依赖 lock、相同 runner 契约采集完整报告。
2. 生成候选 baseline，人工检查高密度 issue 与分类分布。
3. 提交 baseline，并在 PR 解释全部新增 entries 都来自哪次 reference run。
4. 在 PR head 上手动跑一次完整 `category=issues`：只有已知失败时 comparator 应成功。
5. 用自动化测试或临时测试分支注入一个新 failure，证明 comparator 和最终 gate 会失败；
   不要把故障注入提交到最终 PR。
6. 删除一个候选 baseline entry 或修复一个 synthetic known failure，证明 `new` 和
   `resolved` 两个方向都会阻断。

## 6. 最低交付物

- [ ] Versioned failure baseline JSON。
- [ ] Parser/classifier/diff/completeness 实现。
- [ ] 只读 compare 与显式 snapshot CLI。
- [ ] Machine-readable execution manifest。
- [ ] Unit XML fixtures 与 deterministic unit tests。
- [ ] Extended Tests 的聚合 comparator job。
- [ ] GitHub step summary 与可下载 JSON/Markdown/JUnit/manifest artifacts。
- [ ] `docs/TEST_LAYERS.md` 或相邻运维文档中的 baseline 更新说明。
- [ ] PR 正文中的 source run、真实验证链接、baseline diff 摘要和已知限制。

## 7. 实施 Agent 自验清单

### 数据与确定性

- [ ] 同一组 XML 不同输入顺序生成完全相同的 snapshot。
- [ ] Linux/macOS 绝对路径差异不会改变 identity。
- [ ] 参数化测试、类方法和同名测试能生成唯一稳定 nodeid。
- [ ] Baseline 每条 entry 都能映射到一个冻结 inventory 中的 issue number。
- [ ] 没有 traceback、token、用户目录、数据库内容或其他敏感数据进入 tracked baseline。

### Comparator 行为

- [ ] 只有 known failures：exit 0，但 summary 明确显示债务。
- [ ] 新 assertion failure：非零退出。
- [ ] 新 setup/fixture error：非零退出。
- [ ] outcome/category 变化：非零退出并显示 changed。
- [ ] resolved entry：非零退出并要求收缩 baseline。
- [ ] collection/import error：始终非零，不能 snapshot 为允许项。
- [ ] 缺失/损坏/空/部分 JUnit：非零退出。
- [ ] 缺 shard、取消 job、nodeid 覆盖不完整：非零退出。
- [ ] 重复或冲突 identity：非零退出或经过有测试的显式聚合，绝不 last-write-wins。
- [ ] Targeted run 不会被报告成完整全量 gate。

### CI 行为

- [ ] 原始 pytest 失败仍能上传 JUnit 和日志。
- [ ] Comparator 即使上游失败或取消也会运行，并在证据不全时失败。
- [ ] `Nightly Quality Gate` 只在 collection、deterministic suites、E2E 和 legacy
      comparator 都满足各自契约时成功。
- [ ] PR `PR Gate` 的 required 路径和预期耗时没有被 full legacy execution 拖长。
- [ ] Manual `category=issues` 真实运行产生清晰 summary 和所有声明的 artifacts。
- [ ] Workflow/job 权限保持最小化，不增加写仓库权限。

### 仓库回归

- [ ] `python scripts/ci.py doctor --strict`
- [ ] 新增 baseline 单元测试全部通过。
- [ ] `python scripts/ci.py run default-collection issue-collection legacy-pr python-core`
- [ ] `pre-commit run --all-files`
- [ ] GitHub PR 的 `PR Gate` 通过。
- [ ] 完整 issue workflow 的真实 run URL 已附在 PR。

如果本机 runtime 与 `.python-version` / `.nvmrc` 不符，不得用不匹配环境的结果声称
本地与 GitHub 一致；应修正工具链或只报告已实际完成的验证。

## 8. 我后续审查时使用的验收清单

以下任一关键项失败，我会要求修改而不会批准：

### P0：正确性与 fail-closed

- [ ] Baseline 不是由部分 shard、手抄统计或自动接受当前结果生成。
- [ ] 新 failure、error、collection error、报告缺失都会让最终权威 gate 失败。
- [ ] 已知失败没有被 `skip`/`xfail`、删除断言或吞退出码隐藏。
- [ ] Known failures 在成功 nightly 中仍可见、可下载、可定位到 nodeid/issue。
- [ ] Resolved entries 会推动 baseline 收缩，不会永久豁免。
- [ ] Comparator 对 shard 重排无感，不会与 #2458 冲突。

### P1：可维护性与审查性

- [ ] Schema 有版本、稳定排序、明确 provenance 和更新 runbook。
- [ ] 分类规则简单、确定、测试充分；没有依赖脆弱的完整错误字符串。
- [ ] CLI 默认只读，snapshot 是显式操作，baseline diff 适合人工 review。
- [ ] Workflow 中只有一个权威 legacy conclusion，summary 不会把 advisory 状态冒充 gate。
- [ ] 实现复用仓库 runner/lock，不建立第二套本地与 GitHub 行为。

### P1：证据

- [ ] Unit tests 覆盖 known/new/resolved/changed/invalid 全状态矩阵。
- [ ] PR CI 全绿。
- [ ] PR head 的真实 full issue run 证明 known-only 可通过 comparator。
- [ ] 故障注入证据证明新增失败和缺失 artifact 都会阻断。
- [ ] PR 量化 baseline 初始 entries、按 outcome/category/issue 的分布，并说明异常大户。

### P2：范围与长期治理

- [ ] PR 没有夹带 #2458/#2459 或大批 legacy test 行为修复。
- [ ] 没有降低 `.test-baseline.json`，除非逐项解释了真实迁移/删除。
- [ ] 文档明确下一批清债如何删除 baseline entry、迁移有价值测试并更新 #2429。

## 9. 完成时的交接格式

实施 Agent 在请求审查时，请在 #2457 和 PR 中同时提供：

```text
Implementation PR: <url>
Reference run / commit: <url> / <sha>
Full verification run: <url>

Baseline:
- total known entries:
- failures / errors:
- collection errors: 0
- top issue directories:
- top error categories:

Fail-closed evidence:
- new failure case:
- resolved entry case:
- missing/corrupt artifact case:

Validation:
- local commands and results:
- PR Gate:
- remaining limitations / follow-ups:
```

我会以本文第 8 节为审查标准；“脚本能运行”或“nightly 变绿”本身不构成验收。
