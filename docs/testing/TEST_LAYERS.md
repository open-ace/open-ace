# 测试分层说明

本文档说明 Open ACE 项目的测试分层架构、运行时机、本地复现方法和 Baseline 管理流程。

**Issue #2189**: 测试门禁失真修复

## 测试层概览

Open ACE 采用四层测试架构，确保代码质量和测试效率的平衡：

| 测试层 | 收集规则 | 目标目录 | norecursedirs 影响 | 运行时机 | 本地复现命令 |
|--------|---------|---------|-------------------|---------|-------------|
| Default Tests | test_*.py, *test.py, e2e_*.py | tests/（排除 tests/e2e, tests/issues） | 生效（排除 e2e, issues） | PR + Push | pytest tests/ -v |
| Critical E2E | 显式文件列表 | tests/e2e/regression/test_login.py, test_navigation.py::test_* | 绕过（显式路径） | PR（除非 skip-extended-tests label） | python scripts/run_extended_tests.py --category critical |
| Full E2E | 显式文件列表 | tests/e2e/** | 绕过（显式路径） | Schedule（每周日 02:00） + workflow_dispatch | python scripts/run_extended_tests.py --category e2e |
| Issue Tests | 显式文件列表 | tests/issues/** | 绕过（显式路径） | Schedule + workflow_dispatch + run-issue-tests label | python scripts/run_extended_tests.py --category issues |

## Default Tests

### 运行时机
- 每次 PR 创建/更新
- 每次 Push 到 main/master 分支
- 触发条件：所有 PR（无例外）

### 收集规则
- 文件模式：test_*.py, *test.py, e2e_*.py
- 目标目录：tests/（排除 tests/e2e, tests/issues）
- 排除规则：通过 pytest.ini 的 norecursedirs

### 本地复现
```bash
# 运行所有 default tests
pytest tests/ -v

# 仅运行单元测试
pytest tests/unit/ -v

# 仅运行特定测试文件
pytest tests/unit/test_example.py -v

# 运行带覆盖率
pytest tests/ -v --cov=scripts/shared --cov=app/auth
```

### CI Workflow
- 文件：.github/workflows/ci.yml
- Job: test
- Matrix: Python 3.10, 3.11, 3.12

## Critical E2E Tests

### 运行时机
- 每次 PR 创建/更新
- 例外：PR 带 `skip-extended-tests` label
- 触发条件：PR event (opened, synchronize, reopened)

### 收集规则
- 方式：显式文件列表
- 目标：
  - tests/e2e/regression/test_login.py
  - tests/e2e/regression/test_navigation.py::test_sidebar_menu_visible
  - tests/e2e/regression/test_navigation.py::test_menu_navigation

### 本地复现
```bash
# 运行 critical E2E tests
python scripts/run_extended_tests.py --category critical

# 使用特定服务器
python scripts/run_extended_tests.py --category critical --base-url http://localhost:8080

# 使用隔离环境
python scripts/run_extended_tests.py --category critical --isolated-home
```

### CI Workflow
- 文件：.github/workflows/extended-tests.yml
- Job: critical-pr

## Full E2E Tests

### 运行时机
- Schedule：每周日 02:00
- Manual：workflow_dispatch
- Label：run-full-e2e

### 收集规则
- 方式：显式文件列表
- 目标：tests/e2e/** 所有文件

### 本地复现
```bash
# 运行 full E2E tests
python scripts/run_extended_tests.py --category e2e

# Dry-run 模式（仅输出命令）
python scripts/run_extended_tests.py --category e2e --dry-run
```

### CI Workflow
- 文件：.github/workflows/extended-tests.yml
- Job: full-e2e
- Timeout: 120 minutes

## Issue Tests

### 运行时机
- Schedule：每周日 02:00
- Manual：workflow_dispatch + issue_numbers
- Label：run-issue-tests

### 收集规则
- 方式：显式文件列表
- 目标：tests/issues/** 所有文件
- 分片：4 shards

### 本地复现
```bash
# 运行所有 issue tests
python scripts/run_extended_tests.py --category issues

# 运行特定 issue
python scripts/run_extended_tests.py --category issues --issue 1001
python scripts/run_extended_tests.py --category issues --issue-numbers "1001,1002"

# 运行特定分片
python scripts/run_extended_tests.py --category issues --split-total 4 --split-group 1
```

### CI Workflow
- 文件：.github/workflows/extended-tests.yml
- Job: issue-tests
- Matrix: 4 shards
- Timeout: 180 minutes

## Baseline 管理

### 查看当前 baseline
```bash
cat .github/test_baseline.json
```

### Baseline 检查逻辑
- **低于 hard_minimum**：CI 失败
- **低于 warning_threshold**：警告但不失败
- **正常范围**：通过

### 自动更新（default_tests）
- 每周一自动检测测试数量变化
- 增长超过 5% 但不超过 10%：自动创建 PR
- 测试数量下降：禁止自动更新，创建 issue 要求人工 review

### 手动更新
1. 获取当前测试数量：
   ```bash
   # Default tests
   pytest --co tests/ -q | grep "test session starts"

   # Extended tests
   python scripts/run_extended_tests.py --dry-run --category critical
   ```

2. 更新 baseline 文件：
   - 编辑 .github/test_baseline.json
   - 更新对应测试层的 baseline 值
   - 更新 last_updated 字段

3. 创建 PR：
   - 标题：`chore: update test baseline`
   - 说明原因：新增测试/删除测试/其他
   - 等待 review 后合并

### 运行 baseline 检查
```bash
# 检查单个类别
python scripts/check_test_baseline.py --category default_tests --actual-count 7254

# 检查所有类别（使用默认值）
python scripts/check_test_baseline.py --all-categories
```

## 测试工具脚本

### 验证 e2e_*.py 文件
```bash
# 验证所有 e2e_*.py 文件包含测试函数
python scripts/verify_test_files.py

# 详细输出
python scripts/verify_test_files.py --verbose
```

### 扫描测试反模式
```bash
# 扫描所有反模式
python scripts/scan_test_antipatterns.py

# 仅显示高严重程度问题
python scripts/scan_test_antipatterns.py --severity high

# 输出 JSON 格式
python scripts/scan_test_antipatterns.py --output json
```

### 验证配置一致性
```bash
# 验证 pytest.ini 和 pyproject.toml 配置一致
python scripts/verify_pytest_config.py

# 严格模式（不一致时返回错误码）
python scripts/verify_pytest_config.py --strict
```

### 验证分片一致性
```bash
# 验证 issue tests 分片一致性
python scripts/run_extended_tests.py --category issues --verify-shard
```

## 故障排查

### 问题：测试收集为 0

**症状**：
- CI 输出 "0 tests collected"
- 测试运行立即结束

**排查步骤**：
1. 检查测试文件命名是否符合规范（test_*.py 或 e2e_*.py）
2. 检查测试函数命名是否以 test_ 开头
3. 运行验证脚本：
   ```bash
   python scripts/verify_test_files.py
   ```
4. 检查 pytest.ini 配置是否正确

### 问题：Baseline 检查失败

**症状**：
- CI 输出 "低于 hard_minimum"
- 测试数量异常下降

**排查步骤**：
1. 检查是否有测试文件被误删除
2. 运行 baseline 检查脚本：
   ```bash
   python scripts/check_test_baseline.py --category default_tests --actual-count <当前数量>
   ```
3. 如果测试数量确实下降，确认原因后更新 baseline
4. 如果测试数量正常，检查收集规则是否正确

### 问题：Coverage 低于阈值

**症状**：
- CI 输出 "Coverage X% below threshold Y%"
- 测试通过但 coverage 检查失败

**排查步骤**：
1. 运行覆盖率报告：
   ```bash
   pytest tests/ --cov=scripts/shared --cov-report=term-missing
   ```
2. 检查覆盖率是否确实低于阈值
3. 如果覆盖率低，增加测试覆盖
4. 如果覆盖率正常，检查阈值配置是否合理

### 问题：e2e_*.py 文件未被收集

**症状**：
- Extended tests 输出文件列表为空
- e2e_*.py 文件存在但未被运行

**排查步骤**：
1. 验证文件命名是否符合规范（e2e_*.py）
2. 验证文件是否在正确目录（tests/e2e 或 tests/issues）
3. 运行验证脚本：
   ```bash
   python scripts/verify_test_files.py --verbose
   ```
4. 检查 run_extended_tests.py 的 category 配置

### 问题：分片不一致

**症状**：
- --verify-shard 检查失败
- 不同分片运行结果不一致

**排查步骤**：
1. 运行分片验证：
   ```bash
   python scripts/run_extended_tests.py --category issues --verify-shard
   ```
2. 检查是否有文件被遗漏
3. 检查分片算法是否稳定（使用 sorted 排序）
4. 检查文件数量是否与预期一致

## 附录：配置文件说明

### pytest.ini
- 主配置文件（优先级高）
- 包含 python_files pattern（含 e2e_*.py）
- 定义 norecursedirs（排除 tests/e2e, tests/issues）
- 定义测试 markers

### pyproject.toml
- 统一配置文件
- 包含 pytest 配置（与 pytest.ini 一致）
- 包含 coverage 配置
- 包含其他工具配置

### .github/test_baseline.json
- Baseline 配置文件
- 定义各测试层的阈值
- 记录最后更新时间

---

**最后更新**: 2026-08-03
**Issue**: #2189
