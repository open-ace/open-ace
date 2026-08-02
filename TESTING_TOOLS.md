# Issue #2189 测试门禁工具使用指南

本目录包含 Issue #2189 相关的测试门禁工具。

## 快速开始

### 1. 验证测试文件
```bash
# 验证所有 e2e_*.py 文件
python scripts/verify_test_files.py

# 详细输出
python scripts/verify_test_files.py --verbose
```

### 2. 检查 baseline
```bash
# 检查所有类别
python scripts/check_test_baseline.py --all-categories

# 检查特定类别
python scripts/check_test_baseline.py --category default_tests --actual-count 7254
```

### 3. 扫描反模式
```bash
# 扫描所有反模式
python scripts/scan_test_antipatterns.py

# 仅显示高严重程度问题
python scripts/scan_test_antipatterns.py --severity high

# 输出 JSON 格式
python scripts/scan_test_antipatterns.py --output json > report.json
```

### 4. 验证配置
```bash
# 验证 pytest 配置一致性
python scripts/verify_pytest_config.py

# 严格模式
python scripts/verify_pytest_config.py --strict
```

### 5. 验证分片一致性
```bash
# 验证 issue tests 分片
python scripts/run_extended_tests.py --category issues --verify-shard
```

## 文档

- [测试分层说明](docs/testing/TEST_LAYERS.md) - 详细的测试层定义和运行指南
- [实施总结](docs/testing/ISSUE_2189_IMPLEMENTATION.md) - Issue #2189 实施细节

## Baseline 配置

配置文件：`.github/test_baseline.json`

```json
{
  "default_tests": {
    "hard_minimum": 500,
    "baseline": 7254,
    "warning_threshold": 6500
  },
  ...
}
```

### 更新 Baseline

1. 运行测试获取当前数量：
   ```bash
   pytest --co tests/ -q | grep "test session starts"
   ```

2. 更新配置文件：`.github/test_baseline.json`

3. 创建 PR 并说明原因

## 常见问题

### Q: e2e_*.py 文件未被收集？

运行验证：
```bash
python scripts/verify_test_files.py --verbose
```

检查：
- 文件命名是否符合规范（e2e_*.py）
- 文件是否在正确目录（tests/e2e 或 tests/issues）
- 文件是否包含 test_* 函数

### Q: Baseline 检查失败？

检查：
- 是否有测试文件被删除
- 运行 `python scripts/check_test_baseline.py --all-categories`
- 确认原因后更新 baseline 配置

### Q: 如何运行 Extended Tests？

```bash
# Critical E2E
python scripts/run_extended_tests.py --category critical

# Full E2E
python scripts/run_extended_tests.py --category e2e

# Issue Tests
python scripts/run_extended_tests.py --category issues

# Dry-run 模式
python scripts/run_extended_tests.py --category critical --dry-run
```

## 更多信息

详见 [测试分层文档](docs/testing/TEST_LAYERS.md)