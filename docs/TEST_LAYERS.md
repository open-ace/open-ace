# Open ACE 测试结构与 CI 策略

本文是项目测试分类、存放和执行语义的唯一规范。Issue #2429 之前，
`tests/issues/<number>/` 同时充当来源记录和测试分类，导致不同运行条件的
测试混在一起，并被默认 CI 整体排除。

## 设计结论

测试只保留一份，目录回答“它需要什么环境运行”，marker 回答“为什么存在、
优先级是什么”。因此：

- 不创建顶层 `tests/regression/` 或 `tests/security/`。回归与安全都是
  测试属性，不是运行环境。
- 不把同一个测试复制到 `unit/`、`integration/` 等多个目录。
  副本会产生漂移、重复执行和不同修复状态。
- `tests/issues/` legacy quarantine 已随 #2429 最终批次整体退役并删除，
  不得重建；回归测试直接进入规范目录并打 `regression`/`issue` marker。
- GitHub issue 追踪使用 `@pytest.mark.issue(number)`；缺陷回归同时使用
  `@pytest.mark.regression`。

## 规范目录

| 目录 | 运行契约 | 默认时机 |
|---|---|---|
| `tests/unit/` | 进程内、快速、无网络/真实数据库/子进程/服务器 | 每个 PR，required |
| `tests/integration/` | 跨数据库、文件系统、子进程或组件边界；逐步细分 `sqlite/`、`postgres/`、`filesystem/`、`subprocess/` | 每个 PR；PostgreSQL 独立 lane |
| `tests/e2e/` | 需要运行中的 Open ACE、浏览器或远端服务 | critical 子集按路径/标签；全量定时 |
| `tests/performance/` | 有时间或资源阈值，可能受 runner 噪声影响 | 独立非阻塞 lane |

路由边界测试和并发测试已分别归入 `tests/integration/routes/` 与
`tests/integration/concurrency/`。tests 根层与 autonomous 域的历史存量已
全部迁毕（#3185），grandfather inventory 随之退役并由 layout policy 钉住
终态（根层仅 conftest/`__init__`）；不要再新增新的“按功能域”顶层目录或
tests 根目录测试文件。

**测试数据库隔离（#2869）**：`tests/unit/conftest.py` 的 `_isolated_unit_db`
autouse fixture 给每个 unit 测试指向自己的一次性 sqlite 库
（`DATABASE_URL=sqlite:///<tmp>/unit-test.db`），非 `@pytest.mark.postgres` 测试
一律如此。这样走 `create_app` 的测试不再共享工作区级 `app.db`——早先某个测试留下
列形态不一致的同名表会让后续 `create_app` 的 schema 重放随机崩
（`no such column`），本 fixture 从根上消除该共享态，并使测试默认库选择显式化
（不静默继承开发机的 `DATABASE_URL`）。`tests/integration/conftest.py` 早已用
每测试 `tmp_path` 库。需要 Postgres 的测试打 `@pytest.mark.postgres` 并放入
`tests/integration/`（由独立 postgres lane 执行）。

## 回归测试写法

新回归测试直接写到其规范目录，并在模块或测试函数上记录来源：

```python
import pytest

pytestmark = [pytest.mark.security, pytest.mark.regression, pytest.mark.issue(2429)]
```

常用命令：

```bash
# 默认 required suite（与 GitHub Actions 共用定义，并隔离 HOME/数据库环境）
python scripts/ci.py run default-collection python-core

# 按 issue 运行已经迁移到任意规范目录的测试
pytest --issue=2429

# 扩展测试
python scripts/run_extended_tests.py --category e2e --isolated-home
```

## CI 保证

目录本身不构成保证，CI 的消费关系才构成保证：

1. 默认 suite 在生产 Python 3.11 上执行 unit、integration 等可确定测试；
   `security` marker 包含在同一 required suite 中。
2. Python 3.11 的 required job 对 `tests/` 做 pytest collection，收集错误或
   item 数低于 `.test-baseline.json` 立即失败。历史上已进入 PR 门禁的
   issue 目录早已全部迁入 canonical 层（#2429 批次 1/2），随后的
   `legacy-pr` required suite、`tests/issues/pr-gate-directories.txt`、
   issue-collection 收集门禁与 `tests/issues/` quarantine 树已随 #2429
   最终批次整体退役；这些回归由 `python-core`/`python-min` required lane
   按目录自动执行。
3. critical E2E 按变更路径或标签执行，稳定前不作为 required check；完整 E2E
   每夜执行。
4. PostgreSQL 和 performance 使用独立 lane，避免把环境需求隐藏成 skip。
5. `tests/unit/test_test_layout_policy.py` 禁止新增编号目录、顶层功能域目录、
   tests 根目录测试文件，以及 `tests/regression/`、`tests/security/`。

所有 suite 的命令、超时和工具链版本以 `ci/suites.json` 为唯一来源；本地和
GitHub Actions 都通过 `python scripts/ci.py` 执行。PR 矩阵按版本分工（#2868）：

- **3.11（生产运行时）**：`python-core`——全量 `pytest tests/`（含 integration）
  + 覆盖率，每个非文档改动都跑。该 suite 的预算同样已因 GitHub runner 方差
  从 600s 抬至 1200s——同分支健康运行曾 289s↔535s 波动，慢 runner 的长尾
  会直接撞穿 600s（#3280）。`python-core` 与 `python-min` 的 pytest 命令均
  带 `--timeout 300 --timeout-method thread --durations 20`：单个测试 hang
  时在 300s 处 dump 全部线程堆栈并大声失败，而不是烧光整个 suite 预算后
  只留一句不可诊断的 "Command exceeded"；`--durations` 让预算余量的收缩
  在日志里先于超时可见。
- **false-positive-scan**：测试代码假阳性扫描（Issue #2189，Scope #6），
  每个非文档改动都跑（`ci.py select_pr_suites` 默认集 + `PR Gate` 消费，#3186
  Phase A），独立于 `python-core` 以避免超时。已知债务以**精确身份 ledger**
  （`ci/false-positive-ledger.json`：pattern + 文件 + 类限定函数名）表达——
  新增/置换 finding 即红；修复只允许**收缩**（`--prune-ledger` 只删不增），
  且规模由契约测试钉死（只减不增）。旧的按计数 baseline 已退役。
- **3.10（最低支持版本）**：`python-min`——`compileall` + 全量 `pytest tests/unit/`，
  每个非文档改动都跑。版本特有的回归几乎总先在最老解释器上暴露（例如 3.11 之前
  `datetime.fromisoformat` 不接受 `Z` 后缀），旧矩阵只在 3.10 跑 7 文件 smoke，使
  这类**单元级**回归在 PR 与合并后 main 推送上都漏网、红 main 75 分钟无人察觉。
  只跑 unit 使其快（~2min）且确定（无 integration flake、无覆盖率开销；该 suite 的
  预算已因 GitHub runner 方差从 600s 抬至 1200s——同 commit 曾 183s↔652s 波动（#3240）
  ——但直接在较慢的 3.10 上跑全量 `tests/` 仍会拉长墙钟并放大 flake）。
- **3.12、3.14（前向兼容）**：`compatibility-smoke`——`compileall` + 少量关键单元
  文件，按依赖变更选择。与 `postgres` lane 一样带 `--timeout 300
  --timeout-method thread --durations 20`（#3282）：hang 防护与慢测试可见性
  不再只属于 unit lane；`performance` lane 有意**不带** per-test timeout——
  墙钟基准慢是设计意图。另有一个非致命的**预算侵蚀警告**（#3282）：任何
  suite 成功结束时若消耗超过其预算的 75%，`scripts/ci.py` 会在日志打印
  `::warning::...completed in ...s, ...% of its ...s budget`（GitHub Actions
  上同时成为 Checks UI 注解）并（当 nightly metrics 流启用时）记录
  `suite_budget_warning` 事件——这是 #3281 退役 600s 硬绊线后恢复的渐进
  慢化信号，让预算余量的收缩在变成间歇超时之前被看见。

`python-min` 与 `python-core` 都对每个代码改动生效，故 `app/**` 改动必在最低支持
版本真跑全量单元。Python 3.13 仍是声明支持版本但不在 PR 矩阵中。定时工作流在
3.10、3.11、3.14 上执行完整 Python suite，并承担 E2E 和易受 runner
噪声影响的检查。`tests/unit/test_ci_runner.py::test_min_supported_python_runs_the_full_unit_suite`
把「最低支持版本必须跑全量 tests/unit」锁进门禁。合并后对 main 的验证由既有
`push: [main]`（`ci.yml` 与 `schema-sync.yml`）承担，本改动使其对最低版本真正
生效——红 main 因此成为提交上诚实可见的红 check。（注：当前 ruleset 未开启
"require branches up to date"，跨 PR 陈旧基线仍可能红 main，属独立 ruleset 配置项。）

提交前可运行 `python scripts/ci.py doctor --strict` 验证本地 Python/Node
主版本与 PR 一致，再用 `python scripts/ci.py pr --base origin/main` 按相同路径
规则选择 suite。PostgreSQL 和 E2E suite 仍需本地先提供其声明的服务/浏览器。
`.python-version` 与 `.nvmrc` 分别固定为 3.11 和 20，支持 uv/pyenv/nvm
等工具自动选择与 Actions 相同的主版本。
`requirements-ci.lock` 从最低支持版本 Python 3.10 做 universal 解析，所有本地
和 GitHub 测试 job 都安装该文件。生产依赖保留在 `requirements.txt`；开发和
CI 所需的测试、检查、构建及审计工具统一保留在
`requirements-ci.in`，不得加入生产安装使用的 `requirements.txt`；对应的
`dev` extra 必须与该输入保持一致，策略测试会自动检查两者及生产依赖边界。
修改任一输入后必须按 `CONTRIBUTING.md` 的命令重新生成并提交 lock。

收集成功只证明测试“存在且能导入”，不证明断言是绿的。只有 required lane
中的测试才能作为合并门禁。

## Legacy issue 测试迁移（已完成并退役）

#2429 分批把 `tests/issues/` 迁入规范目录（unit-like → integration →
e2e/performance），最终批次（17）迁出剩余 e2e 并删除整棵 quarantine 树、
`legacy-directories.txt` 盘点、issue-collection 收集门禁、issue-tests
nightly shards、`ci/legacy-issue-{quarantine,failures}.json` 与
`scripts/legacy_issue_baseline.py` comparator。每个被提升的测试都满足：

- 能在干净 checkout 中独立执行；
- 修复前失败、修复后通过，断言验证行为而不是源码字符串；
- 不写开发者 HOME、生产数据库或固定远端资源；
- 在所属 CI lane 中被自动发现，不需要为每个 issue 修改 workflow YAML。

## Baseline

`.test-baseline.json` 分别记录 item 和文件数量。默认 CI 用真实 pytest
collection 检查 item 数；extended runner 的分片只能按文件分配，因此
按 `split_total` 等比例检查文件 baseline。

Baseline 是防止测试静默消失的下限，不是覆盖率指标。降低 baseline 必须在 PR
中说明迁移、删除或合并测试的原因；新增测试后应定期向上收紧。

更新流程：先运行 `python scripts/ci.py run default-collection` 记录实际
item/file 数；然后更新 `.test-baseline.json` 的 `actual_*`。只有有意删除、
合并或迁移测试时才降低 `min_*`，并在 PR 中解释原因；新增测试只更新
`actual_*`，定期将 `min_*` 向实际值收紧。最后重跑 collection suite。
`ci/suites.json` 的 `baseline_runbook` 字段固定指向本节，确保从 suite 清单
可以发现本流程。

## E2E 治理基线（Issue #2491）

`tests/e2e/` 的文件级 disposition、nodeid 级 debt/promotion 状态与互斥 lane
selection 由 `scripts/e2e/` 的纯 stdlib 工具族治理（沿用 #2457 failure
baseline 已退役的 `legacy_issue_baseline` 同款“本地与 CI 同一实现”模式），
治理数据在 `ci/e2e-*.json`：

- **inventory**（`ci/e2e-inventory.json`）：受管 root 下每个 `.py`（磁盘枚举为准，
  含 helper/演示脚本）必须有唯一 disposition（`pytest-automated |
  standalone-automated | manual-demo`）与 home lane；manual 项 `executor=none`
  不计自动覆盖。`collects` 标志声明该文件当前是否产出 pytest nodeids，
  收集变化判为 manifest 漂移而非静默吸收。
- **expected nodeids**（`ci/e2e-expected-nodeids.json`）：由
  `python scripts/e2e/manifest.py snapshot` 从 `pytest --collect-only -q -o addopts=`
  派生（无需 server/frontend build；`-o addopts=` 抵消 pytest.ini 的 `-v`，否则
  输出是收集树无法解析）。**契约**：required unit lane 中的
  `test_e2e_inventory_manifest.py` / `test_e2e_governance.py` 会实时重收集并
  比对——`tests/e2e` 的 conftest 若引入重依赖或收集回归，会直接打红 PR lane，
  这是有意的门，不是误报。
- **debt / promotion state**（`ci/e2e-state.json` / `ci/e2e-promotion.json`）：
  按归一化 nodeid/entry 保存，缺失默认 `unclassified + observing`（observation
  lane 起点）；所有变更必须经 `python scripts/e2e/governance.py` 的写入子命令
  原子完成（唯一合法写入路径），PR 描述附命令行。
- **selector**：`python scripts/e2e/selector.py --event {pr,nightly,weekly}`
  输出 normal/advisory/probe/invalid 四个互斥穷尽集合与 selection.json
  （`--shadow` 为 P1–P3 只记录模式）。
- **attempt 证据**：`pytest -p pytest_attempts --e2e-attempts=<path>` 记录每次
  attempt 的每个 phase（JUnit 只保 final outcome，见
  `docs/dev-notes/2491-rerunfailures-junit-probe.md`）。

新 E2E 文件合入受管 root 前必须先经 `governance.py set-disposition` 登记，
否则 inventory completeness 校验（本地与 `tests/unit` 双路径）非零。
