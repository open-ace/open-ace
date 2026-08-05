# Open ACE 测试层级说明（Issue #2189）

本文档说明 Open ACE 项目的测试层级、运行时机、本地复现方法，以及 baseline 更新流程。

## 测试层级概览

Open ACE 采用分层测试策略，平衡 PR 反馈速度与代码质量保证：

| 层级 | 名称 | 运行时机 | 测试范围 | 预期时长 |
|-----|------|---------|---------|---------|
| **L1** | Default Tests | 每个 PR | tests/（排除 e2e/issues） | ~5 分钟 |
| **L2** | Critical E2E | 每个 PR | tests/e2e/regression（关键路径） | ~10 分钟 |
| **L3** | Full E2E | 定时/手动/标签触发 | tests/e2e/** | ~2 小时 |
| **L4** | Issue Tests | 定时/手动/标签触发 | tests/issues/** | ~3 小时 |

## 各层级详细说明

### L1: Default Tests

**运行时机**：
- 每个 PR（push 和 pull_request 事件）
- 每次 merge 到 main 分支

**测试范围**：
- 路径：`tests/`（排除 `tests/e2e/` 和 `tests/issues/`）
- 文件模式：`test_*.py`（由 pytest.ini 的 `norecursedirs` 配置）
- Marker：无特殊 marker

**本地复现**：
```bash
# 运行所有 default tests
pytest tests/ -v --cov --cov-report=html

# 运行特定文件
pytest tests/test_example.py -v

# 收集测试数量
pytest tests/ --collect-only --quiet
```

**CI 配置**：
- Workflow: `.github/workflows/ci.yml` (test job)
- Python 版本：3.10, 3.11, 3.12（矩阵）

### L2: Critical E2E Tests

**运行时机**：
- 每个 PR（除非标记 `skip-extended-tests`）
- PR 标记 `run-critical-e2e` 时强制运行

**测试范围**：
- 路径：`tests/e2e/regression/test_login.py`, `tests/e2e/regression/test_navigation.py`
- 文件模式：`test_*.py`
- Marker：`@pytest.mark.regression`, `@pytest.mark.priority_p0`

**本地复现**：
```bash
# 需要启动 Open ACE 服务器
python scripts/run_extended_tests.py --category critical

# 或手动运行
pytest tests/e2e/regression/test_login.py tests/e2e/regression/test_navigation.py -v
```

**CI 配置**：
- Workflow: `.github/workflows/extended-tests.yml` (critical-pr job)
- 超时：30 分钟

### L3: Full E2E Tests

**运行时机**：
- 定时：每周日凌晨 2:00（UTC）
- 手动触发：workflow_dispatch + category=e2e
- PR 标记 `run-full-e2e` 时触发

**测试范围**：
- 路径：`tests/e2e/**`
- 文件模式：`test_*.py`, `e2e_*.py`
- Marker：`@pytest.mark.ui`, `@pytest.mark.regression`

**本地复现**：
```bash
# 运行全部 E2E 测试（不分片）
python scripts/run_extended_tests.py --category e2e

# 运行特定分片（4 分片中的第 1 个）
python scripts/run_extended_tests.py --category e2e --split-total 4 --split-group 1
```

**CI 配置**：
- Workflow: `.github/workflows/extended-tests.yml` (full-e2e job)
- 超时：120 分钟

### L4: Issue Tests

**运行时机**：
- 定时：每周日凌晨 2:00（UTC）
- 手动触发：workflow_dispatch + category=issues
- PR 标记 `run-issue-tests` 时触发

**测试范围**：
- 路径：`tests/issues/**`
- 文件模式：`test_*.py`, `e2e_*.py`
- Marker：`@pytest.mark.issue`

**本地复现**：
```bash
# 运行全部 issue 测试（4 分片）
python scripts/run_extended_tests.py --category issues --split-total 4 --split-group 1

# 运行特定 issue 测试
python scripts/run_extended_tests.py --category issues --issue 144
```

**CI 配置**：
- Workflow: `.github/workflows/extended-tests.yml` (issue-tests job)
- 分片：4 个并行分片
- 超时：180 分钟

## 混合模式文件处理

### 问题背景

部分 `e2e_*.py` 文件采用混合模式：
- 包含 `test_*` 函数（pytest 收集）
- 包含 `if __name__ == "__main__"` 块（独立运行）

### 运行优先级

**pytest 优先**：
- CI 流程统一使用 pytest 命令运行
- pytest 收集 `test_*` 函数时，不会执行 `if __name__` 块
- 因此不会有重复执行问题

**独立运行仅用于本地调试**：
- 开发者可以 `python tests/e2e/e2e_xxx.py` 快速验证
- 但 CI 不会直接运行脚本

### 混合文件示例

```python
#!/usr/bin/env python3
"""
E2E test example

Run:
  pytest tests/e2e/e2e_xxx.py -v  # pytest 收集（推荐，CI 使用）
  python tests/e2e/e2e_xxx.py     # 脚本运行（仅用于本地调试）
"""

def test_feature_xxx(page):
    """pytest 会收集此函数"""
    assert page.locator("#element").is_visible()

if __name__ == "__main__":
    # 此块仅在直接运行时执行，pytest 不会执行
    with sync_playwright() as p:
        test_feature_xxx(p.chromium.launch().new_page())
```

## Baseline 管理

### Baseline 文件

文件：`.test-baseline.json`

目的：
- 记录各测试层的测试数量基线
- 检测测试数量异常下降
- 确保测试门禁有效性

### Baseline 结构

```json
{
  "version": "1.0",
  "layers": {
    "default": {
      "min_tests": 3566,
      "min_files": 90,
      "threshold_percent": 90
    },
    "critical": {
      "min_tests": 5,
      "min_files": 2
    },
    "e2e_pytest": {
      "min_tests": 1,
      "min_files": 1
    },
    "issues": {
      "min_tests": 1793,
      "min_files": 80
    }
  },
  "tolerance": {
    "allowed_decrease_percent": 5,
    "require_review_threshold": 10
  }
}
```

### Baseline 更新流程

#### 何时需要更新

1. **正常更新**：
   - 添加新测试模块，测试数量增加
   - 重构测试结构，测试数量变化
   - 修复 flaky 测试，删除冗余测试

2. **异常情况**：
   - 测试数量下降 > 5%：需要调查原因
   - 测试数量下降 > 10%：必须 review 并更新 baseline

#### 更新步骤

1. **收集当前测试数量**：
   ```bash
   pytest tests/ --collect-only --quiet | grep "tests collected"
   pytest tests/e2e/ --collect-only --quiet | grep "tests collected"
   pytest tests/issues/ --collect-only --quiet | grep "tests collected"
   ```

2. **更新 baseline 文件**：
   - 修改 `.test-baseline.json` 中相应层的 `min_tests` 和 `min_files`
   - 更新 `last_updated` 和 `update_reason` 字段

3. **提交审查**：
   - Baseline 更新需要 PR review
   - 说明更新原因（如"添加新测试模块"）

### Baseline 检查逻辑

`scripts/run_extended_tests.py` 在运行测试前会检查 baseline：
1. 读取 `.test-baseline.json`
2. 比较实际收集的测试数量与 baseline
3. 如果数量低于阈值：
   - 下降 < 10%：警告，但继续运行
   - 下降 ≥ 10%：失败，拒绝运行

## CI 门禁机制

### Collect-only 门禁

每个测试层在运行前都会执行 collect-only 检查：
- 收集测试数量 = 0：CI 失败
- 收集测试数量 < baseline 阈值：警告或失败

### Coverage 门禁

Default tests 运行时会检查覆盖率：
- 使用 `--cov-fail-under=30` 参数
- 覆盖率 < 30%：pytest 命令失败，CI 失败

### 零测试检测

如果某个测试层意外收集 0 个测试，CI 会立即失败：
- 避免测试静默丢失
- 确保 CI 门禁有效性

## 故障排查

### 测试收集为 0

可能原因：
1. pytest.ini 配置错误（检查 `python_files` 和 `norecursedirs`）
2. 测试文件命名不符合模式
3. 测试目录结构变化

排查步骤：
```bash
# 验证 pytest.ini 配置
cat pytest.ini

# 尝试手动收集
pytest tests/ --collect-only -v

# 检查测试文件是否匹配模式
find tests/ -name "test_*.py"
find tests/ -name "e2e_*.py"
```

### Baseline 检查失败

可能原因：
1. 测试文件被误删除
2. 测试函数被重命名（不再匹配 `test_*` 模式）
3. pytest 配置变更

排查步骤：
```bash
# 对比修改前后测试数量
pytest tests/ --collect-only --quiet > baseline_current.txt
diff baseline_before.txt baseline_current.txt

# 检查 baseline 文件
cat .test-baseline.json
```

### Coverage 检查失败

可能原因：
1. 代码覆盖率低于阈值（30%）
2. Coverage 配置错误

排查步骤：
```bash
# 本地运行 coverage
pytest tests/ --cov --cov-report=html

# 查看 coverage 报告
open htmlcov/index.html

# 检查 coverage 配置
cat pyproject.toml | grep -A 20 "\[tool.coverage\]"
```

## 相关文件

| 文件 | 用途 |
|-----|------|
| `pytest.ini` | pytest 配置（文件模式、markers、norecursedirs） |
| `scripts/run_extended_tests.py` | Extended tests runner（统一入口） |
| `.test-baseline.json` | 测试数量基线 |
| `.github/workflows/ci.yml` | Default tests CI 配置 |
| `.github/workflows/extended-tests.yml` | E2E/Issue tests CI 配置 |
| `scripts/scan_test_false_positives.py` | 假阳性扫描脚本 |

## 参考资料

- Issue #2189: 测试门禁改进
- Issue #1856: Coverage 配置优化
- pytest 文档: https://docs.pytest.org/
