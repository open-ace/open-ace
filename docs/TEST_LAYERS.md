# Open ACE 测试结构与 CI 策略

本文是项目测试分类、存放和执行语义的唯一规范。Issue #2429 之前，
`tests/issues/<number>/` 同时充当来源记录和测试分类，导致不同运行条件的
测试混在一起，并被默认 CI 整体排除。

## 设计结论

测试只保留一份，目录回答“它需要什么环境运行”，marker 回答“为什么存在、
优先级是什么”。因此：

- 不创建顶层 `tests/regression/` 或 `tests/security/`。回归与安全都是
  测试属性，不是运行环境。
- 不把同一个测试复制到 `unit/`、`integration/`、`issues/` 等多个目录。
  副本会产生漂移、重复执行和不同修复状态。
- `tests/issues/` 是 legacy quarantine，不再接收新目录。现有测试按价值
  逐步迁移，迁移完成后删除旧文件。
- GitHub issue 追踪使用 `@pytest.mark.issue(number)`；缺陷回归同时使用
  `@pytest.mark.regression`。

## 规范目录

| 目录 | 运行契约 | 默认时机 |
|---|---|---|
| `tests/unit/` | 进程内、快速、无网络/真实数据库/子进程/服务器 | 每个 PR，required |
| `tests/integration/` | 跨数据库、文件系统、子进程或组件边界；逐步细分 `sqlite/`、`postgres/`、`filesystem/`、`subprocess/` | 每个 PR；PostgreSQL 独立 lane |
| `tests/e2e/` | 需要运行中的 Open ACE、浏览器或远端服务 | critical 子集按路径/标签；全量定时 |
| `tests/performance/` | 有时间或资源阈值，可能受 runner 噪声影响 | 独立非阻塞 lane |
| `tests/issues/` | 尚未分类的历史测试 | PR 只做全量收集；执行为定时/手动 |

路由边界测试和并发测试已分别归入 `tests/integration/routes/` 与
`tests/integration/concurrency/`。`tests/autonomous/` 和 tests 根目录中的
历史文件继续按 inventory 逐步迁移；不要再新增新的“按功能域”顶层目录或
tests 根目录测试文件。

## 回归测试写法

新回归测试直接写到其规范目录，并在模块或测试函数上记录来源：

```python
import pytest

pytestmark = [pytest.mark.security, pytest.mark.regression, pytest.mark.issue(2429)]
```

常用命令：

```bash
# 默认 required suite（与 GitHub Actions 共用定义，并隔离 HOME/数据库环境）
python scripts/ci.py run default-collection issue-collection legacy-pr python-core

# 按 issue 运行已经迁移到任意规范目录的测试
pytest --issue=2429

# 按 issue 运行 legacy quarantine 中的测试
pytest tests/issues --issue=517

# 验证整个 legacy tree 仍可被 pytest 收集
pytest tests/issues --collect-only -q

# 扩展测试
python scripts/run_extended_tests.py --category e2e --isolated-home
python scripts/run_extended_tests.py --category issues --split-total 4 --split-group 1 --isolated-home
```

## CI 保证

目录本身不构成保证，CI 的消费关系才构成保证：

1. 默认 suite 在生产 Python 3.11 上执行 unit、integration 等可确定测试；
   `security` marker 包含在同一 required suite 中。
2. Python 3.11 的 required job 对 `tests/issues/` 做全量 pytest collection，
   收集错误或 item 数低于 `.test-baseline.json` 立即失败。
   历史上已进入 PR 门禁的 8 个 issue 目录继续由 `legacy-pr`
   required suite 执行，不回退既有覆盖。
3. critical E2E 按变更路径或标签执行，稳定前不作为 required check；完整 E2E
   和 legacy issue shards 每夜执行。
4. PostgreSQL 和 performance 使用独立 lane，避免把环境需求隐藏成 skip。
5. `tests/unit/test_test_layout_policy.py` 禁止新增编号目录、顶层功能域目录、
   tests 根目录测试文件，以及 `tests/regression/`、`tests/security/`。

所有 suite 的命令、超时和工具链版本以 `ci/suites.json` 为唯一来源；本地和
GitHub Actions 都通过 `python scripts/ci.py` 执行。PR 使用生产 Python 3.11
做全量确定性测试；为兼容当前 ruleset，3.10、3.12、3.14 的同名 check 只执行
compileall 和 unit smoke，并不代表全量跨版本覆盖。Python 3.13 仍是声明支持版本，
但不在 PR 矩阵中。定时工作流在 3.10、3.11、3.14 上执行完整 Python suite，
并承担 E2E、legacy shards 和易受 runner 噪声影响的检查。

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

收集成功只证明测试“存在且能导入”，不证明断言是绿的。Legacy suite 的定时
结果用于迁移盘点；只有迁移到 required lane 的测试才能作为合并门禁。

## Legacy issue 测试迁移

按以下顺序迁移，而不是一次性移动 441 个文件：

1. 先迁移当前在 `.github/workflows/ci.yml` 中手工 opt-in 的 issue suites；
   它们已经被认为值得阻止合并。
2. 再迁移无网络、无真实数据库、无 sleep 的 unit-like 测试。
3. 将数据库/文件系统/子进程测试迁入 integration，并补齐隔离 fixture。
4. 将 Playwright、HTTP、WebSocket、远端主机测试迁入 E2E 的对应子目录。
5. 性能测试进入 performance；只打印结果、没有有效断言、依赖个人数据或已经
   被更强测试覆盖的脚本应修复、改为手动工具或删除。

每个被提升的测试都必须满足：

- 能在干净 checkout 中独立执行；
- 修复前失败、修复后通过，断言验证行为而不是源码字符串；
- 不写开发者 HOME、生产数据库或固定远端资源；
- 在所属 CI lane 中被自动发现，不需要为每个 issue 修改 workflow YAML；
- 移走后删除 legacy 原件，并从 inventory 中移除对应空目录。

## Baseline

`.test-baseline.json` 分别记录 item 和文件数量。默认 CI 用真实 pytest
collection 检查 legacy item 数；extended runner 的分片只能按文件分配，因此
按 `split_total` 等比例检查文件 baseline。针对单个 issue 的本地运行不与
全量 baseline 比较。

Baseline 是防止测试静默消失的下限，不是覆盖率指标。降低 baseline 必须在 PR
中说明迁移、删除或合并测试的原因；新增测试后应定期向上收紧。

更新流程：先分别运行 `python scripts/ci.py run default-collection` 和
`python scripts/ci.py run issue-collection` 记录实际 item/file 数；然后更新
`.test-baseline.json` 的 `actual_*`。只有有意删除、合并或迁移测试时才降低
`min_*`，并在 PR 中解释原因；新增测试只更新 `actual_*`，定期将 `min_*`
向实际值收紧。最后重跑两个 collection suite。`ci/suites.json` 的
`baseline_runbook` 字段固定指向本节，确保从 suite 清单可以发现本流程。

### Legacy 失败基线（`ci/legacy-issue-failures.json`）

这是与上面**完全不同**的另一类 baseline：它记录 `tests/issues/` 当前已审查的
历史失败（assertion/error），不是 item/file 数量下限。两者必须同时生效，互不
替代。详见 `docs/issue-2457-agent-handoff.md`。

- 身份键 = `(nodeid, outcome, category)`。`nodeid` 由 `tests/issues/conftest.py`
  的 `record_property("openace_nodeid", ...)` 注入（xunit2 不带 `file` 属性，故
  必须由 conftest 提供权威 nodeid）。因此**生成 baseline 的 reference run 必须
  在包含该 conftest 的提交上触发**。
- `compare`（只读）是权威 gate：仅有 `known` 且完整 → exit 0（summary 仍列债务）；
  出现 `new`/`changed`/`resolved`/任何 `collection_error`/`invalid` → 非零退出。
  `collection_error` 永不进 baseline（collection gate 必须保持为零）；`resolved`
  强制从 baseline 删除该 entry（包括 rerun 后转绿的失败），保证 baseline 持续收缩。
- `snapshot`（显式 `--output`）生成候选 baseline 供人工 review，**拒绝**包含
  `collection_error`。

更新流程（只能由 PR 显式更新，CI 不得自动写回）：

1. 在包含 `tests/issues/conftest.py` 的分支上触发
   `workflow_dispatch category=issues`，等全部 4 shard 完成。
2. 下载 `issue-tests-*` artifact，运行：

   ```bash
   python scripts/legacy_issue_baseline.py snapshot \
     --junit '<下载的 test-results/issues-*.xml>' \
     --source-run <run-id> --reference-commit <sha> \
     --run-contract 'extended-tests issue-tests, --isolated-home --reruns 1 --timeout 240, 4 shards' \
     --output ci/legacy-issue-failures.json
   ```

3. 在 PR 中说明全部新增 entries 来自哪次 reference run、按 outcome/category/issue
   的分布，以及异常大户。降低/删除 entry 时同样在 PR 中逐条说明。
4. 重跑一次 `category=issues`：仅已知失败时 `compare` exit 0。

`run_extended_tests.py` 的 `require_review_threshold`（10%）同时是 comparator 的
文件数地板来源，避免两套门禁口径不一致。Targeted（`--issue-numbers`）运行只在
summary 标为 targeted，不冒充完整 nightly gate。

## E2E 治理基线（Issue #2491）

`tests/e2e/` 的文件级 disposition、nodeid 级 debt/promotion 状态与互斥 lane
selection 由 `scripts/e2e/` 的纯 stdlib 工具族治理（与 #2457 的
`legacy_issue_baseline` 同一“本地与 CI 同一实现”模式），治理数据在 `ci/e2e-*.json`：

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
