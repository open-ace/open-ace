#!/usr/bin/env python3
"""
Verify Issue #2189 implementation (test gate improvements).

This script checks:
1. pytest.ini has correct configuration
2. run_extended_tests.py has correct modifications
3. test_logout has been fixed
4. Baseline file exists
5. CI configuration has coverage threshold
"""

import json
import sys
from pathlib import Path


def verify_pytest_ini() -> bool:
    """Verify pytest.ini has correct configuration."""
    pytest_ini_path = Path("pytest.ini")
    if not pytest_ini_path.exists():
        print("❌ pytest.ini not found")
        return False

    content = pytest_ini_path.read_text()

    # Check for e2e_*.py pattern
    if "e2e_*.py" not in content:
        print("❌ pytest.ini missing e2e_*.py pattern")
        return False

    # Check that python_functions is not modified
    if "python_functions = e2e_*" in content:
        print("❌ pytest.ini has incorrect python_functions = e2e_*")
        return False

    print("✅ pytest.ini configuration correct")
    return True


def verify_run_extended_tests() -> bool:
    """Verify run_extended_tests.py has correct modifications."""
    runner_path = Path("scripts/run_extended_tests.py")
    if not runner_path.exists():
        print("❌ scripts/run_extended_tests.py not found")
        return False

    content = runner_path.read_text()

    # Check for always expanding files
    if "Always expand to file list for consistency" not in content:
        print("❌ run_extended_tests.py missing file expansion logic")
        return False

    # Check for collection manifest
    if "print_collection_manifest" not in content:
        print("❌ run_extended_tests.py missing collection manifest output")
        return False

    # Check for baseline check
    if "check_baseline" not in content:
        print("❌ run_extended_tests.py missing baseline check")
        return False

    print("✅ run_extended_tests.py modifications correct")
    return True


def verify_test_logout() -> bool:
    """Verify test_logout has been fixed."""
    test_path = Path("tests/e2e/regression/test_login.py")
    if not test_path.exists():
        print("❌ tests/e2e/regression/test_login.py not found")
        return False

    content = test_path.read_text()

    # Check that except Exception: pass is removed
    if "except Exception:" in content and "pass" in content:
        # Check if it's a problematic pattern
        lines = content.split("\n")
        for i, line in enumerate(lines):
            if "except Exception:" in line:
                # Check next few lines for pass
                for j in range(i + 1, min(i + 5, len(lines))):
                    if "pass" in lines[j] and "pytest.fail" not in lines[j]:
                        print("❌ test_logout still has broad except: pass")
                        return False

    # Check for pytest.fail
    if "pytest.fail" not in content:
        print("❌ test_logout missing pytest.fail for explicit failures")
        return False

    # Check for Issue #2189 reference
    if "Issue #2189" not in content:
        print("⚠ test_logout missing Issue #2189 reference (optional)")

    print("✅ test_logout fixed correctly")
    return True


def verify_baseline_file() -> bool:
    """Verify baseline file exists and is valid."""
    baseline_path = Path(".test-baseline.json")
    if not baseline_path.exists():
        print("❌ .test-baseline.json not found")
        return False

    try:
        with open(baseline_path) as f:
            baseline = json.load(f)
    except json.JSONDecodeError:
        print("❌ .test-baseline.json is not valid JSON")
        return False

    # Check required fields
    if "layers" not in baseline:
        print("❌ .test-baseline.json missing 'layers' field")
        return False

    required_layers = ["default", "critical", "e2e_pytest", "issues"]
    for layer in required_layers:
        if layer not in baseline["layers"]:
            print(f"❌ .test-baseline.json missing layer: {layer}")
            return False

    print("✅ .test-baseline.json valid")
    return True


def verify_ci_coverage() -> bool:
    """Verify CI configuration has coverage threshold."""
    ci_path = Path(".github/workflows/ci.yml")
    if not ci_path.exists():
        print("❌ .github/workflows/ci.yml not found")
        return False

    content = ci_path.read_text()

    # Check for cov-fail-under
    if "--cov-fail-under" not in content:
        print("❌ CI missing --cov-fail-under parameter")
        return False

    print("✅ CI coverage configuration correct")
    return True


def main() -> int:
    """Run all verification checks."""
    print("Verifying Issue #2189 implementation...")
    print("=" * 60)

    all_passed = True

    all_passed &= verify_pytest_ini()
    all_passed &= verify_run_extended_tests()
    all_passed &= verify_test_logout()
    all_passed &= verify_baseline_file()
    all_passed &= verify_ci_coverage()

    print("=" * 60)
    if all_passed:
        print("✅ All verification checks passed!")
        return 0
    else:
        print("❌ Some verification checks failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
