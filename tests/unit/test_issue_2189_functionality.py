#!/usr/bin/env python3
"""
测试 Issue #2189 相关功能

测试验证脚本和 baseline 检查功能
"""

import json
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class TestVerifyTestFiles:
    """测试 verify_test_files.py 功能"""

    def test_script_exists(self):
        """验证脚本文件存在"""
        script_path = PROJECT_ROOT / "scripts" / "verify_test_files.py"
        assert script_path.exists(), "verify_test_files.py should exist"

    def test_can_import(self):
        """验证可以导入脚本"""
        import sys

        sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
        try:
            import verify_test_files

            assert hasattr(verify_test_files, "find_e2e_test_files")
            assert hasattr(verify_test_files, "verify_test_file")
        finally:
            sys.path.pop(0)


class TestCheckTestBaseline:
    """测试 check_test_baseline.py 功能"""

    def test_script_exists(self):
        """验证脚本文件存在"""
        script_path = PROJECT_ROOT / "scripts" / "check_test_baseline.py"
        assert script_path.exists(), "check_test_baseline.py should exist"

    def test_baseline_file_exists(self):
        """验证 baseline 配置文件存在"""
        baseline_file = PROJECT_ROOT / ".github" / "test_baseline.json"
        assert baseline_file.exists(), "test_baseline.json should exist"

    def test_baseline_file_valid_json(self):
        """验证 baseline 文件是有效 JSON"""
        baseline_file = PROJECT_ROOT / ".github" / "test_baseline.json"
        content = baseline_file.read_text()
        data = json.loads(content)

        # 验证必要的键存在
        assert "default_tests" in data
        assert "critical_e2e" in data
        assert "full_e2e" in data
        assert "issue_tests" in data

        # 验证每个类别有必要的字段
        for category in ["default_tests", "critical_e2e", "full_e2e", "issue_tests"]:
            assert "hard_minimum" in data[category]
            assert "baseline" in data[category]
            assert "warning_threshold" in data[category]


class TestScanTestAntipatterns:
    """测试 scan_test_antipatterns.py 功能"""

    def test_script_exists(self):
        """验证脚本文件存在"""
        script_path = PROJECT_ROOT / "scripts" / "scan_test_antipatterns.py"
        assert script_path.exists(), "scan_test_antipatterns.py should exists"


class TestVerifyPytestConfig:
    """测试 verify_pytest_config.py 功能"""

    def test_script_exists(self):
        """验证脚本文件存在"""
        script_path = PROJECT_ROOT / "scripts" / "verify_pytest_config.py"
        assert script_path.exists(), "verify_pytest_config.py should exist"

    def test_pytest_ini_exists(self):
        """验证 pytest.ini 存在"""
        pytest_ini = PROJECT_ROOT / "pytest.ini"
        assert pytest_ini.exists(), "pytest.ini should exist"

    def test_pytest_ini_has_e2e_pattern(self):
        """验证 pytest.ini 包含 e2e_*.py pattern"""
        pytest_ini = PROJECT_ROOT / "pytest.ini"
        content = pytest_ini.read_text()
        assert "e2e_*.py" in content, "pytest.ini should include e2e_*.py pattern"

    def test_pyproject_toml_has_e2e_pattern(self):
        """验证 pyproject.toml 包含 e2e_*.py pattern"""
        pyproject_toml = PROJECT_ROOT / "pyproject.toml"
        content = pyproject_toml.read_text()
        assert "e2e_*.py" in content, "pyproject.toml should include e2e_*.py pattern"


class TestTestLogoutFix:
    """测试 test_logout 修复"""

    def test_logout_function_exists(self):
        """验证 test_logout 函数存在"""
        test_file = PROJECT_ROOT / "tests" / "e2e" / "regression" / "test_login.py"
        content = test_file.read_text()

        # 验证没有 except Exception: pass
        assert (
            "except Exception:\n                    pass" not in content
        ), "test_logout should not have 'except Exception: pass'"

        # 验证有 pytest.fail 调用
        assert "pytest.fail" in content, "test_logout should use pytest.fail for explicit failures"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
