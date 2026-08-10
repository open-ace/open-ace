# Tests

Open ACE 测试按运行环境存放，每个测试只有一个 canonical location。
完整策略见 [`docs/TEST_LAYERS.md`](../docs/TEST_LAYERS.md)。

## 目录

- `unit/`：进程内、快速、无外部依赖。
- `integration/`：数据库、文件系统、子进程或跨组件测试。
- `e2e/`：运行中服务器、浏览器、WebSocket 或远端服务。
- `performance/`：时间或资源阈值测试。
- `issues/`：Issue #2429 之前遗留的 quarantine，只迁出、不新增。

`security`、`regression` 和 GitHub issue 编号是测试元数据，不是目录：

```python
import pytest

pytestmark = [pytest.mark.security, pytest.mark.regression, pytest.mark.issue(2429)]
```

不要将同一个测试复制到多个目录。Bug 修复测试应直接进入其运行层级，
CI 会自动消费；issue marker 保留追踪关系。

## 常用命令

```bash
# 与 GitHub Actions 相同的默认 PR suite
python scripts/ci.py run default-collection issue-collection legacy-pr python-core

# 检查本地是否使用 CI 的 Python 3.11 / Node 20
python scripts/ci.py doctor --strict

# 单元、集成和安全属性测试
pytest tests/unit
pytest tests/integration -m "not postgres"
pytest tests -m security

# PostgreSQL
pytest tests/integration -m postgres

# 根据 issue provenance 选择已迁移测试
pytest --issue=2429

# Legacy issue tests
pytest tests/issues --issue=517
pytest tests/issues --collect-only -q

# Extended suites
python scripts/run_extended_tests.py --category critical --isolated-home
python scripts/run_extended_tests.py --category e2e --isolated-home
python scripts/run_extended_tests.py --category issues --split-total 4 --split-group 1 --isolated-home
```

## 新测试的完成标准

- 文件位于唯一的规范运行层级。
- 缺陷测试带 `regression` 和 `issue(number)` markers。
- 使用临时 HOME/数据库/文件，且可在干净 checkout 独立执行。
- 断言验证外部行为；修复前应失败，修复后应通过。
- 所属 CI lane 能自动发现它，不需要把 issue 编号硬编码进 workflow。
- 测试清理创建的进程、连接、浏览器和临时资源。

Legacy 迁移细则和 CI 保证以 `docs/TEST_LAYERS.md` 为准。
