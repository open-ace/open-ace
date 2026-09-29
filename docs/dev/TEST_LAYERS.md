# Open ACE Test Structure and CI Strategy — Open ACE 测试结构与 CI 策略

[English](#english) | [中文](#中文)

---

## English

This document is the single source of truth for the project's test classification,
placement, and execution semantics. Before Issue #2429, `tests/issues/<number>/`
doubled as both the provenance record and the test taxonomy, which mixed tests with
different runtime requirements together and excluded them wholesale from the
default CI.

## Design Conclusions

Tests are kept in exactly one copy: the directory answers "what environment it
needs to run in", and the marker answers "why it exists and what its priority
is". Therefore:

- No top-level `tests/regression/` or `tests/security/` is created. Regression
  and security are test attributes, not runtime environments.
- The same test is never copied into multiple directories such as `unit/` or
  `integration/`. Copies drift, execute redundantly, and end up in divergent
  fix states.
- The `tests/issues/` legacy quarantine was retired and deleted wholesale with
  the final #2429 batch and must not be recreated; regression tests go straight
  into the canonical directories with the `regression`/`issue` markers.
- GitHub issue tracking uses `@pytest.mark.issue(number)`; defect regressions
  additionally use `@pytest.mark.regression`.

## Canonical Directories

| Directory | Runtime contract | Default timing |
|---|---|---|
| `tests/unit/` | In-process, fast, no network/real database/subprocesses/servers | Every PR, required |
| `tests/integration/` | Crosses database, filesystem, subprocess, or component boundaries; progressively subdivided into `sqlite/`, `postgres/`, `filesystem/`, `subprocess/` | Every PR; PostgreSQL on a dedicated lane |
| `tests/e2e/` | Requires a running Open ACE, a browser, or remote services | Critical subset by path/label; full set on a schedule |
| `tests/performance/` | Has time or resource thresholds; may be affected by runner noise | Separate non-blocking lane |

Route-boundary tests and concurrency tests have been placed into
`tests/integration/routes/` and `tests/integration/concurrency/` respectively.
The historical inventory at the tests root and in the autonomous domain has been
fully migrated (#3185); the grandfather inventory was retired with it, and the
layout policy pins the end state (only conftest/`__init__` at the root). Do not
add new "per functional domain" top-level directories or test files in the tests
root.

**Test database isolation (#2869)**: the `_isolated_unit_db` autouse fixture in
`tests/unit/conftest.py` points every unit test at its own throwaway sqlite
database (`DATABASE_URL=sqlite:///<tmp>/unit-test.db`), and this holds
unconditionally for any test not marked `@pytest.mark.postgres`. Tests that go
through `create_app` therefore no longer share the workspace-level `app.db` —
previously, a same-named table with an inconsistent column shape left behind by
an earlier test could make a later `create_app`'s schema replay crash at random
(`no such column`); the fixture eliminates that shared state at the root and
makes the default test-database choice explicit (no silent inheritance of the
development machine's `DATABASE_URL`). `tests/integration/conftest.py` already
used a per-test `tmp_path` database. Tests that need Postgres carry
`@pytest.mark.postgres` and live under `tests/integration/` (executed by the
dedicated postgres lane).

## Writing Regression Tests

New regression tests are written directly into their canonical directory, with
the origin recorded on the module or the test function:

```python
import pytest

pytestmark = [pytest.mark.security, pytest.mark.regression, pytest.mark.issue(2429)]
```

Common commands:

```bash
# 默认 required suite（与 GitHub Actions 共用定义，并隔离 HOME/数据库环境）
python scripts/ci.py run default-collection python-core

# 按 issue 运行已经迁移到任意规范目录的测试
pytest --issue=2429

# 扩展测试
python scripts/run_extended_tests.py --category e2e --isolated-home
```

## CI Guarantees

Directories alone constitute no guarantee; what the CI consumes is the
guarantee:

1. The default suite runs deterministic tests such as unit and integration on
   the production Python 3.11; the `security` marker is included in that same
   required suite.
2. The Python 3.11 required job runs pytest collection over `tests/` and fails
   immediately on collection errors or an item count below
   `.test-baseline.json`. The issue directories that had historically entered
   the PR gate were long since fully migrated into the canonical layers
   (#2429 batches 1/2); the subsequent `legacy-pr` required suite,
   `tests/issues/pr-gate-directories.txt`, the issue-collection collection
   gate, and the `tests/issues/` quarantine tree were retired wholesale with
   the final #2429 batch; those regressions are now executed automatically,
   by directory, by the `python-core`/`python-min` required lanes.
3. Critical E2E runs by changed path or label and is not a required check
   until stabilized; the full E2E runs nightly.
4. PostgreSQL and performance use dedicated lanes, so environment requirements
   are not hidden as skips.
5. `tests/unit/test_test_layout_policy.py` forbids adding numbered directories,
   top-level functional-domain directories, test files in the tests root, and
   `tests/regression/` or `tests/security/`.

Commands, timeouts, and toolchain versions for all suites come solely from
`ci/suites.json`; both local runs and GitHub Actions execute through
`python scripts/ci.py`. The PR matrix divides work by version (#2868):

- **3.11 (production runtime)**: `python-core` — the full `pytest tests/`
  (including integration) plus coverage, run on every non-documents change.
  This suite's budget was likewise raised from 600s to 1200s due to GitHub
  runner variance — healthy runs on the same branch have oscillated between
  289s and 535s, and a slow runner's tail can blow straight through 600s
  (#3280). The pytest commands of both `python-core` and `python-min` carry
  `--timeout 300 --timeout-method thread --durations 20`: when a single test
  hangs, all thread stacks are dumped at the 300s mark and it fails loudly,
  instead of burning through the whole suite budget and leaving only an
  undiagnosable "Command exceeded"; `--durations` makes the shrinkage of
  budget headroom visible in the logs before the timeout.
- **false-positive-scan**: the test-code false-positive scan (Issue #2189,
  Scope #6), run on every non-documents change (`ci.py select_pr_suites`
  default set + consumed by `PR Gate`, #3186 Phase A), independent of
  `python-core` to avoid timeouts. Known debt is expressed as a
  **precise-identity ledger** (`ci/false-positive-ledger.json`: pattern +
  file + class-qualified function name) — any new or replaced finding goes
  red; fixes may only **shrink** it (`--prune-ledger` only deletes, never
  adds), and its size is pinned by a contract test (only ever decreasing).
  The old count-based baseline has been retired.
- **3.10 (minimum supported version)**: `python-min` — `compileall` + the full
  `pytest tests/unit/`, run on every non-documents change. Version-specific
  regressions almost always surface on the oldest interpreter first (for
  example, before 3.11 `datetime.fromisoformat` did not accept the `Z`
  suffix); the old matrix ran only a 7-file smoke on 3.10, letting such
  **unit-level** regressions slip through both PRs and post-merge main
  pushes — one red main went unnoticed for 75 minutes. Running only unit
  keeps it fast (~2min) and deterministic (no integration flake, no coverage
  overhead; the suite's budget was likewise raised from 600s to 1200s due to
  GitHub runner variance — the same commit has oscillated between 183s and
  652s (#3240) — but running the full `tests/` directly on the slower 3.10
  would still stretch wall-clock time and amplify flake).
- **3.12, 3.14 (forward compatibility)**: `compatibility-smoke` —
  `compileall` + a small set of critical unit files, selected on dependency
  changes. Like the `postgres` lane it carries `--timeout 300
  --timeout-method thread --durations 20` (#3282): hang protection and
  slow-test visibility no longer belong to the unit lane alone; the
  `performance` lane deliberately carries **no** per-test timeout — slow
  wall-clock benchmarks are the design intent. There is also a non-fatal
  **budget-erosion warning** (#3282): when any suite finishes successfully
  having consumed more than 75% of its budget, `scripts/ci.py` prints
  `::warning::...completed in ...s, ...% of its ...s budget` to the log
  (which also becomes a Checks UI annotation on GitHub Actions) and (when
  the nightly metrics stream is enabled) records a `suite_budget_warning`
  event — this restores the gradual-slowdown signal after #3281 retired the
  600s hard tripwire, letting the shrinkage of budget headroom be seen
  before it turns into intermittent timeouts.

Both `python-min` and `python-core` apply to every code change, so changes to
`app/**` always truly run the full unit suite on the minimum supported
version. Python 3.13 remains a declared supported version but is not in the
PR matrix. Scheduled workflows run the complete Python suite on 3.10, 3.11,
and 3.14, and carry the E2E and the checks most susceptible to runner noise.
`tests/unit/test_ci_runner.py::test_min_supported_python_runs_the_full_unit_suite`
locks "the minimum supported version must run the full tests/unit" into the
gate. Post-merge verification of main is carried by the existing
`push: [main]` (`ci.yml` and `schema-sync.yml`); this change makes it
genuinely effective for the minimum version — a red main is therefore an
honest, visible red check on the commit. (Note: the current ruleset does not
enable "require branches up to date", so stale baselines across PRs can
still redden main; that is a separate ruleset setting.)

Before submitting you can run `python scripts/ci.py doctor --strict` to
verify that the local Python/Node major versions match the PR, then use
`python scripts/ci.py pr --base origin/main` to select suites under the same
path rules. The PostgreSQL and E2E suites still require their declared
services/browsers to be provided locally. `.python-version` and `.nvmrc` are
pinned to 3.11 and 20 respectively, letting tools such as uv/pyenv/nvm
automatically select the same major versions as Actions. `requirements-ci.lock`
is universally resolved from the minimum supported Python 3.10, and every
local and GitHub test job installs that file. Production dependencies stay
in `requirements.txt`; the test, lint, build, and audit tooling needed for
development and CI stays in `requirements-ci.in` and must not be added to the
production-install `requirements.txt`; the corresponding `dev` extra must
stay consistent with that input — policy tests automatically check both plus
the production dependency boundary. After changing either input you must
regenerate and commit the lock using the commands in `CONTRIBUTING.md`.

Successful collection only proves that the tests "exist and import"; it does
not prove the assertions are green. Only tests in required lanes count as
merge gates.

## CI Health Metrics Timing Validity

`ci-health-metrics.yml` collects on a GitHub schedule and also supports
manual dispatch on the default branch. The collector bounds the time window,
sample size, and API request budget per `ci/ci-health-policy.json`; when the
report has too few same-contract samples, the p95 must be flagged
`insufficient_data` and must not be used to promote Critical E2E from
advisory to required.

Report schema/timing-derivation version 2 (#3358) marks negative queue
durations of GitHub workflows/attempts individually as unavailable:
`seconds=null`, preserving the raw delta, both endpoint timestamps, and the
reason. Such values are neither clamped to zero seconds, nor do they cause
the whole run/attempt or its success/failure/cancelled verdict to be dropped.
The JSON `workflow_queue_anomalies` and the Markdown provide run/attempt
locating information; queue quantiles use only valid values, and the p95
minimum sample count is judged by the number of distinct runs with valid
queue data — repeated reruns or already-excluded samples cannot pad it.
Consumers reading the report must check `schema_version` first; queue
statistics from versions 1 and 2 must not be mixed directly.

This tolerance applies only to chronology anomalies in the workflow queue's
source data. Missing or malformed timestamps still error out; job queue, job
execution, attempt wall, and the like keep the existing fail-closed
validation and the small clock-skew allowance of at most 2 seconds, and the
inherited/skipped job rules are unchanged.

## Legacy Issue Test Migration (Completed and Retired)

#2429 migrated `tests/issues/` into the canonical directories in batches
(unit-like → integration → e2e/performance); the final batch (17) moved out
the remaining e2e and deleted the entire quarantine tree, the
`legacy-directories.txt` inventory, the issue-collection collection gate,
the issue-tests nightly shards, `ci/legacy-issue-{quarantine,failures}.json`,
and the `scripts/legacy_issue_baseline.py` comparator. Every promoted test
satisfied:

- it can run independently in a clean checkout;
- it failed before the fix and passed after it, with assertions verifying
  behavior rather than source strings;
- it writes nothing to the developer HOME, production databases, or fixed
  remote resources;
- it is discovered automatically by its CI lane, with no per-issue workflow
  YAML edits.

## Baseline

`.test-baseline.json` records item counts and file counts separately. The
default CI checks the item count with a real pytest collection; the extended
runner's shards can only be allocated per file, so the file baseline is
checked proportionally by `split_total`.

The baseline is a lower bound against tests silently disappearing, not a
coverage metric. Lowering the baseline requires the PR to explain why tests
were migrated, deleted, or merged; after adding tests it should be tightened
upward periodically.

Update procedure: first run `python scripts/ci.py run default-collection` to
record the actual item/file counts; then update `actual_*` in
`.test-baseline.json`. Lower `min_*` only when tests are deliberately
deleted, merged, or migrated, and explain why in the PR; when adding tests,
update only `actual_*` and periodically tighten `min_*` toward the actual
values. Finally rerun the collection suite. The `baseline_runbook` field in
`ci/suites.json` is pinned to this section, so the procedure is discoverable
from the suite inventory.

## E2E Governance Baseline (Issue #2491)

The file-level disposition of `tests/e2e/`, the nodeid-level debt/promotion
state, and the mutually exclusive lane selection are governed by the
pure-stdlib tool family in `scripts/e2e/` (carrying over the same "one
implementation for local and CI" pattern as the `legacy_issue_baseline` of
the retired #2457 failure baseline); governance data lives in
`ci/e2e-*.json`:

- **inventory** (`ci/e2e-inventory.json`): every `.py` under the managed
  roots (disk enumeration is authoritative, including helper/demo scripts)
  must have a unique disposition (`pytest-automated |
  standalone-automated | manual-demo`) and a home lane; manual entries have
  `executor=none` and do not count toward automated coverage. The `collects`
  flag declares whether the file currently produces pytest nodeids; a
  collection change is judged as manifest drift rather than silently
  absorbed.
- **expected nodeids** (`ci/e2e-expected-nodeids.json`): derived by
  `python scripts/e2e/manifest.py snapshot` from
  `pytest --collect-only -q -o addopts=` (no server/frontend build needed;
  `-o addopts=` cancels pytest.ini's `-v`, otherwise the output is an
  unparseable collection tree). **Contract**: `test_e2e_inventory_manifest.py`
  / `test_e2e_governance.py` in the required unit lane re-collect live and
  compare — if `tests/e2e`'s conftest pulls in heavy dependencies or
  regresses collection, it reddens the PR lane directly; that gate is
  intentional, not a false positive.
- **debt / promotion state** (`ci/e2e-state.json` /
  `ci/e2e-promotion.json`): stored keyed by normalized nodeid/entry,
  defaulting to `unclassified + observing` when missing (the observation
  lane's starting point); every change must go through the write
  subcommands of `python scripts/e2e/governance.py` atomically (the only
  legitimate write path), with the command line attached to the PR
  description.
- **selector**: `python scripts/e2e/selector.py --event {pr,nightly,weekly}`
  outputs the four mutually exclusive and exhaustive sets
  normal/advisory/probe/invalid plus selection.json (`--shadow` is the
  record-only mode for P1–P3).
- **attempt evidence**: `pytest -p pytest_attempts --e2e-attempts=<path>`
  records every phase of every attempt (JUnit keeps only the final outcome;
  see `docs/dev-notes/2491-rerunfailures-junit-probe.md`).

A new E2E file must be registered via `governance.py set-disposition` before
merging into the managed roots; otherwise the inventory completeness check
(both the local and the `tests/unit` paths) exits non-zero.

## Appendix: Local shell prerequisites

`ci/suites.json` declares Bash minimum major version 4; `python-core` and
`python-min` include real subprocess tests of the fetch wrapper that need
Bash associative arrays. `python scripts/ci.py doctor` reports the Bash path
actually selected by PATH, its version, and its capabilities;
`doctor --strict` returns non-zero for missing, incompatible, or failed
probes. The strict doctor additionally checks the production Python 3.11 /
Node 20; it is not a launch condition for the Python 3.10 minimum-version
lane.

`run` / `pr` check the Bash prerequisites of the whole execution plan before
any selected suite starts, avoiding spending minutes on collection or tests
before an environment problem surfaces. Standalone scans, `list`, and
`detect` do not require Bash; running `pytest` directly bypasses this
entry-precondition check but does not change the tests' own requirements or
passing criteria.

macOS `/bin/bash` 3.2 does not satisfy the requirement. You can install a
standalone modern Bash and adjust PATH for the current terminal only; there
is no need to replace the system Bash, change the default login shell, or
touch wrapper shebangs:

```bash
brew install bash
# 先激活装有 requirements-ci.lock 的环境（重复激活会恢复旧 PATH）
source venv/bin/activate
# 保持 venv Python 第一、现代 Bash 随后，不丢弃其余 CLI 路径
export PATH="$VIRTUAL_ENV/bin:$(brew --prefix)/bin:$PATH"
command -v bash
bash --version
command -v python python3
python scripts/ci.py doctor --strict
python scripts/ci.py run default-collection python-core
```

Keep the PATH directories that existing dependencies such as the Docker
Compose CLI live in; do not clobber them with a reduced PATH just to select
Bash. The related script tests invoke `python3` and `docker compose version`,
so launching the entry point with the absolute path of the venv Python alone
does not guarantee those subprocesses use the same dependency set.

Probes and suite subprocesses uniformly strip `BASH_ENV` / `ENV`, so
developer startup scripts cannot inject into and affect CI; the parent
process environment is unchanged. PATH keeps its original order, and
relative/empty directory entries are converted to absolute paths against the
working directory at invocation time, preventing a different Bash from being
selected after a suite switches working directories. Do not rely on personal
shell startup scripts to provide CI dependencies; prepare PATH and the
locked dependency environment explicitly.

A modern Bash is a necessary condition, not an equivalent guarantee of the
full Linux CI on macOS. The canonical execution environment remains the
Ubuntu 24.04 of `ci/suites.json`, the lane's Python, the locked
dependencies, and the required services. Cross-platform acceptance should
separately record the OS, the actual Python/Bash versions, the identical
suite commands, the test results, and the GitHub run link; a passing
precondition check or a passing targeted test must not be written up as a
full pass. This change adds no time-consuming macOS PR lane and skips no
wrapper regression.

---

## 中文

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

## CI Health Metrics 计时有效性

`ci-health-metrics.yml` 在 GitHub 定时采集，也支持默认分支手动 dispatch。
采集器按 `ci/ci-health-policy.json` 限制时间窗口、样本量与 API 请求预算；
报告中的同契约样本不足时，p95 必须标记 `insufficient_data`，不得据此将
Critical E2E 从 advisory 提升为 required。

报告 schema/计时推导版本 2（#3358）将 GitHub workflow/attempt 的负排队
时间单独标记为不可用：`seconds=null`，保留原始差值、两端时间戳及原因。
它不会被夹成 0 秒，也不会导致丢弃整个 run/attempt 或其 success/failure/
cancelled 结论。JSON `workflow_queue_anomalies` 和 Markdown 提供 run/attempt
定位信息；queue 分位数只使用有效值，p95 最小样本数按有有效 queue 的独立
run 数判断，不能用多次重跑或已排除样本凑足。读取报告的消费者须先检查
`schema_version`；版本 1/2 的 queue 统计不可直接混合。

此容错仅适用于 workflow queue 的源数据时序异常。缺失或格式错误时间戳
仍报错；job queue、job execution、attempt wall 等保留既有的 fail-closed
校验与最多 2 秒的小幅时钟偏差规则，inherited/skipped job 规则不变。

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

## 附录：本地 shell 前置条件

`ci/suites.json` 声明 Bash 最低主版本为 4；`python-core` 和 `python-min`
包含 fetch wrapper 的真实子进程测试，需要 Bash associative arrays。
`python scripts/ci.py doctor` 报告 PATH 实际选中的 Bash 路径、版本和能力；
`doctor --strict` 对缺失、不兼容或探测失败返回非零。严格 doctor 同时检查
生产 Python 3.11 / Node 20；它不是 Python 3.10 最低版本 lane 的启动条件。

`run` / `pr` 在任何选中 suite 开始前检查整个执行计划的 Bash 前置条件，
避免先花数分钟跑 collection 或测试再暴露环境问题。单独扫描、`list`、
`detect` 不要求 Bash；直接运行 `pytest` 会绕过这个入口前置检查，但不改变
测试自身的要求或通过标准。

macOS 的 `/bin/bash` 3.2 不满足要求。可安装独立现代 Bash，并仅为当前终端
调整 PATH；不需要替换系统 Bash、修改默认登录 shell 或 wrapper shebang：

```bash
brew install bash
# 先激活装有 requirements-ci.lock 的环境（重复激活会恢复旧 PATH）
source venv/bin/activate
# 保持 venv Python 第一、现代 Bash 随后，不丢弃其余 CLI 路径
export PATH="$VIRTUAL_ENV/bin:$(brew --prefix)/bin:$PATH"
command -v bash
bash --version
command -v python python3
python scripts/ci.py doctor --strict
python scripts/ci.py run default-collection python-core
```

保留 Docker Compose CLI 等已有依赖所在的 PATH 目录；不要为选择 Bash
而用一份缩减 PATH 覆盖它们。相关脚本测试会调用 `python3` 和
`docker compose version`，仅用 venv Python 的绝对路径启动入口并不足以
保证这些子进程也使用同一套依赖。

探测和 suite 子进程统一移除 `BASH_ENV` / `ENV`，避免开发者启动脚本注入
影响 CI；父进程环境不变。PATH 保持原顺序，相对/空目录项按调用时工作目录
转为绝对路径，避免 suite 切换工作目录后选中另一个 Bash。不要依赖个人
shell 启动脚本提供 CI 依赖，应显式准备 PATH 和锁定依赖环境。

现代 Bash 是必要条件，并不代表 macOS 已获得全量 Linux CI 等价保证。
规范执行环境仍是 `ci/suites.json` 的 Ubuntu 24.04、对应 lane 的 Python、
锁定依赖及所需服务。跨平台验收应分别记录操作系统、Python/Bash 实际版本、
相同 suite 命令、测试结果和 GitHub run 链接；不能把前置检查通过或定向测试
通过写成全量通过。本改动不新增耗时 macOS PR lane，不跳过 wrapper 回归。
