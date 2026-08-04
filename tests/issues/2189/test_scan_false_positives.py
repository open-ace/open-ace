"""Tests for scripts/scan_test_false_positives.py (Issue #2189).

Covers the semantic, 4-pattern scanner (Scope #6): broad_except_swallow,
no_assertion, return_true, erroneous_skip. Detection is AST-semantic so it
cannot be gamed by replacing ``pass`` with a ``print`` (round-2 review).
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SCANNER = REPO_ROOT / "scripts" / "scan_test_false_positives.py"


def _load_scanner():
    spec = importlib.util.spec_from_file_location("scan_test_false_positives", SCANNER)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _write(root: Path, rel: str, content: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)


# ---- fixtures ----
BROAD_EXCEPT_PASS = """\
def test_a():
    try:
        do()
    except Exception:
        pass
    assert True
"""
BROAD_EXCEPT_RAISE = """\
def test_a():
    try:
        do()
    except Exception:
        raise
    assert True
"""
BROAD_EXCEPT_ASSERT = """\
def test_a():
    try:
        do()
    except Exception:
        assert False, "should not happen"
    assert True
"""
# round-2 gaming attempt: print instead of pass. Must STILL be flagged.
BROAD_EXCEPT_PRINT = """\
def test_a():
    try:
        do()
    except Exception as exc:
        print(exc)
    assert True
"""
# legitimate cleanup: explicit allow-swallow marker -> not flagged.
BROAD_EXCEPT_ALLOW = """\
def test_a():
    try:
        do()
    except Exception:  # allow-swallow: best-effort cleanup
        pass
    assert True
"""
NO_ASSERT = """\
def test_b():
    do()
"""
RETURN_TRUE_NO_ASSERT = """\
def test_c():
    do()
    return True
"""
RETURN_TRUE_WITH_ASSERT = """\
def test_d():
    assert do() is True
    return True
"""
SKIP_UNCONDITIONAL = """\
import pytest

def test_e():
    pytest.skip("no env")
    assert True
"""
SKIP_CONDITIONAL = """\
import os
import pytest

def test_f():
    if not os.environ.get("X"):
        pytest.skip("needs X")
    assert True
"""


def _patterns(mod, root: Path, content: str) -> set[str]:
    f = root / "test_x.py"
    f.write_text(content)
    return {finding.pattern for finding in mod.scan_tests(root)}


# ---- broad_except_swallow is semantic ----
def test_broad_except_flags_pass(tmp_path: Path) -> None:
    mod = _load_scanner()
    assert "broad_except_swallow" in _patterns(mod, tmp_path, BROAD_EXCEPT_PASS)


def test_broad_except_flags_print_not_just_literal_pass(tmp_path: Path) -> None:
    """Closes the round-2 gaming: print() must still be flagged."""
    mod = _load_scanner()
    assert "broad_except_swallow" in _patterns(mod, tmp_path, BROAD_EXCEPT_PRINT)


def test_broad_except_allows_raise(tmp_path: Path) -> None:
    mod = _load_scanner()
    assert "broad_except_swallow" not in _patterns(mod, tmp_path, BROAD_EXCEPT_RAISE)


def test_broad_except_allows_assert(tmp_path: Path) -> None:
    mod = _load_scanner()
    assert "broad_except_swallow" not in _patterns(mod, tmp_path, BROAD_EXCEPT_ASSERT)


def test_broad_except_allows_marker(tmp_path: Path) -> None:
    mod = _load_scanner()
    assert "broad_except_swallow" not in _patterns(mod, tmp_path, BROAD_EXCEPT_ALLOW)


# ---- the other 3 Scope #6 patterns ----
def test_no_assertion_flagged(tmp_path: Path) -> None:
    mod = _load_scanner()
    assert "no_assertion" in _patterns(mod, tmp_path, NO_ASSERT)


def test_return_true_flagged_without_assert(tmp_path: Path) -> None:
    mod = _load_scanner()
    assert "return_true" in _patterns(mod, tmp_path, RETURN_TRUE_NO_ASSERT)


def test_return_true_not_flagged_with_assert(tmp_path: Path) -> None:
    mod = _load_scanner()
    assert "return_true" not in _patterns(mod, tmp_path, RETURN_TRUE_WITH_ASSERT)


def test_erroneous_skip_flags_unconditional(tmp_path: Path) -> None:
    mod = _load_scanner()
    assert "erroneous_skip" in _patterns(mod, tmp_path, SKIP_UNCONDITIONAL)


def test_erroneous_skip_allows_conditional(tmp_path: Path) -> None:
    mod = _load_scanner()
    assert "erroneous_skip" not in _patterns(mod, tmp_path, SKIP_CONDITIONAL)


# ---- pattern filter + exclude CLI ----
def test_pattern_filter_broad_except_only(tmp_path: Path) -> None:
    mod = _load_scanner()
    _write(tmp_path, "test_mix.py", BROAD_EXCEPT_PASS + "\n\n" + NO_ASSERT)
    findings = mod.scan_tests(tmp_path, pattern="broad_except_swallow")
    assert {f.pattern for f in findings} == {"broad_except_swallow"}


def test_exclude_dirs_skips_subtree(tmp_path: Path) -> None:
    mod = _load_scanner()
    _write(tmp_path, "keep/test_keep.py", BROAD_EXCEPT_PASS)
    _write(tmp_path, "excluded/test_excl.py", BROAD_EXCEPT_PASS)
    findings = mod.scan_tests(tmp_path, pattern="broad_except_swallow", exclude_dirs=["excluded"])
    files = {Path(f.file).name for f in findings}
    assert "test_keep.py" in files
    assert "test_excl.py" not in files


def test_exclude_dirs_accepts_scan_root_prefix(tmp_path: Path) -> None:
    mod = _load_scanner()
    root = tmp_path / "tests"
    _write(root, "issues/test_auto.py", BROAD_EXCEPT_PASS)
    _write(root, "core/test_core.py", BROAD_EXCEPT_PASS)
    findings = mod.scan_tests(root, pattern="broad_except_swallow", exclude_dirs=["tests/issues"])
    files = {Path(f.file).name for f in findings}
    assert "test_core.py" in files
    assert "test_auto.py" not in files


def test_main_exit_code_zero_when_no_matching_p0(tmp_path: Path) -> None:
    _write(tmp_path, "test_only_no_assert.py", NO_ASSERT)
    proc = subprocess.run(
        [sys.executable, str(SCANNER), str(tmp_path), "--pattern", "broad_except_swallow"],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_main_exit_code_one_when_p0_present(tmp_path: Path) -> None:
    _write(tmp_path, "test_broad.py", BROAD_EXCEPT_PASS)
    proc = subprocess.run(
        [sys.executable, str(SCANNER), str(tmp_path)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr


def test_baseline_fails_when_count_exceeds(tmp_path: Path) -> None:
    """Baseline gate fails when a pattern's count exceeds the recorded baseline."""
    import json

    _write(tmp_path, "test_broad.py", BROAD_EXCEPT_PASS)
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps({"broad_except_swallow": 0}))
    proc = subprocess.run(
        [sys.executable, str(SCANNER), str(tmp_path), "--baseline", str(baseline)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "exceeds baseline" in proc.stdout


def test_baseline_passes_when_count_at_or_below(tmp_path: Path) -> None:
    import json

    _write(tmp_path, "test_broad.py", BROAD_EXCEPT_PASS)  # 1 finding
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps({"broad_except_swallow": 5}))
    proc = subprocess.run(
        [sys.executable, str(SCANNER), str(tmp_path), "--baseline", str(baseline)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_update_baseline_writes_current_counts(tmp_path: Path) -> None:
    import json

    _write(tmp_path, "test_broad.py", BROAD_EXCEPT_PASS)
    baseline = tmp_path / "baseline.json"
    proc = subprocess.run(
        [sys.executable, str(SCANNER), str(tmp_path), "--update-baseline", str(baseline)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    data = json.loads(baseline.read_text())
    assert data["broad_except_swallow"] >= 1
