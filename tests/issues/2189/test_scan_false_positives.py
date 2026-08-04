"""Tests for scripts/scan_test_false_positives.py (Issue #2189).

Covers the CLI filtering added so the scanner can gate CI on a specific pattern
(``broad_except_pass``) while excluding auto-generated subtrees (``tests/issues``).
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


# A test function that triggers ``broad_except_pass`` (P0) but has an assertion,
# so it must NOT also trigger ``no_assertion``.
BROAD_EXCEPT = """\
def test_a():
    try:
        do()
    except Exception:
        pass
    assert True
"""

# A test function with no assertion -> triggers ``no_assertion`` (P0) only.
NO_ASSERTION = """\
def test_b():
    do()
"""


def _write(root: Path, rel: str, content: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)


def test_default_returns_all_patterns(tmp_path: Path) -> None:
    mod = _load_scanner()
    _write(tmp_path, "test_mix.py", BROAD_EXCEPT + "\n\n" + NO_ASSERTION)
    findings = mod.scan_tests(tmp_path)
    patterns = {f.pattern for f in findings}
    assert "broad_except_pass" in patterns
    assert "no_assertion" in patterns


def test_pattern_filter_broad_except_only(tmp_path: Path) -> None:
    mod = _load_scanner()
    _write(tmp_path, "test_mix.py", BROAD_EXCEPT + "\n\n" + NO_ASSERTION)
    findings = mod.scan_tests(tmp_path, pattern="broad_except_pass")
    assert {f.pattern for f in findings} == {"broad_except_pass"}


def test_pattern_filter_no_assertion_only(tmp_path: Path) -> None:
    mod = _load_scanner()
    _write(tmp_path, "test_mix.py", BROAD_EXCEPT + "\n\n" + NO_ASSERTION)
    findings = mod.scan_tests(tmp_path, pattern="no_assertion")
    assert {f.pattern for f in findings} == {"no_assertion"}


def test_exclude_dirs_skips_subtree(tmp_path: Path) -> None:
    mod = _load_scanner()
    _write(tmp_path, "keep/test_keep.py", BROAD_EXCEPT)
    _write(tmp_path, "excluded/test_excl.py", BROAD_EXCEPT)
    findings = mod.scan_tests(tmp_path, pattern="broad_except_pass", exclude_dirs=["excluded"])
    files = {Path(f.file).name for f in findings}
    assert "test_keep.py" in files
    assert "test_excl.py" not in files


def test_exclude_dirs_accepts_scan_root_prefix(tmp_path: Path) -> None:
    """Exclude dirs may be given with the scan-root prefix (e.g. 'tests/issues'
    when scanning a 'tests' root), not only relative to the scan root."""
    mod = _load_scanner()
    root = tmp_path / "tests"
    _write(root, "issues/test_auto.py", BROAD_EXCEPT)
    _write(root, "core/test_core.py", BROAD_EXCEPT)
    findings = mod.scan_tests(root, pattern="broad_except_pass", exclude_dirs=["tests/issues"])
    files = {Path(f.file).name for f in findings}
    assert "test_core.py" in files
    assert "test_auto.py" not in files


def test_main_pattern_filter_exit_code_zero_when_no_matching_p0(tmp_path: Path) -> None:
    """Exits 0 when filtering to broad_except_pass and only no_assertion P0 exists."""
    _write(tmp_path, "test_only_no_assert.py", NO_ASSERTION)
    proc = subprocess.run(
        [sys.executable, str(SCANNER), str(tmp_path), "--pattern", "broad_except_pass"],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_main_default_exit_code_one_when_p0_present(tmp_path: Path) -> None:
    _write(tmp_path, "test_broad.py", BROAD_EXCEPT)
    proc = subprocess.run(
        [sys.executable, str(SCANNER), str(tmp_path)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr
