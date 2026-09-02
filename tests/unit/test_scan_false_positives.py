"""Tests for scripts/scan_test_false_positives.py (Issue #2189).

Covers the semantic, 4-pattern scanner (Scope #6): broad_except_swallow,
no_assertion, return_true, erroneous_skip. Detection is AST-semantic so it
cannot be gamed by replacing ``pass`` with a ``print`` (round-2 review).

Migrated from tests/issues/2189/test_scan_false_positives.py — straight
move; only the REPO_ROOT anchor depth changed (tests/unit is one level
shallower than tests/issues/2189). The embedded ``def test_a…f`` snippet
strings below are scanner corpus, not tests.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCANNER = REPO_ROOT / "scripts" / "scan_test_false_positives.py"

pytestmark = [pytest.mark.regression, pytest.mark.issue(2189)]


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
# deliberate test outcome (skip) in the except body -> not a swallow.
BROAD_EXCEPT_SKIP = """\
import pytest

def test_a():
    try:
        do()
    except Exception:
        pytest.skip("db unavailable")
"""
# deliberate failure (pytest.fail raises) in the except body -> not a swallow.
BROAD_EXCEPT_FAIL = """\
import pytest

def test_a():
    try:
        do()
    except Exception:
        pytest.fail("unexpected")
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


def test_broad_except_allows_pytest_skip(tmp_path: Path) -> None:
    """except -> pytest.skip() is a deliberate outcome, not a swallow."""
    mod = _load_scanner()
    assert "broad_except_swallow" not in _patterns(mod, tmp_path, BROAD_EXCEPT_SKIP)


def test_broad_except_allows_pytest_fail(tmp_path: Path) -> None:
    """except -> pytest.fail() raises, so it is not a swallow."""
    mod = _load_scanner()
    assert "broad_except_swallow" not in _patterns(mod, tmp_path, BROAD_EXCEPT_FAIL)


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


def test_annotation_template_validation() -> None:
    """Test annotation content validation (Issue #2306, REQ-6)."""
    mod = _load_scanner()
    validate_annotation_content = mod.validate_annotation_content

    # Valid annotations for allow-no-assert
    assert validate_annotation_content("smoke test - visual verification only", "allow-no-assert")
    assert validate_annotation_content(
        "screenshot regression test - TODO review 2026-Q4", "allow-no-assert"
    )
    assert validate_annotation_content(
        "auto-generated test - CI app selector alignment", "allow-no-assert"
    )
    assert validate_annotation_content("playwright script - visual verification", "allow-no-assert")

    # Valid annotations for allow-swallow
    assert validate_annotation_content("UI element may not exist", "allow-swallow")
    assert validate_annotation_content("transient timeout", "allow-swallow")
    assert validate_annotation_content("screenshot failure, non-critical", "allow-swallow")
    assert validate_annotation_content("optional UI element", "allow-swallow")
    assert validate_annotation_content("test framework error handling", "allow-swallow")

    # Valid annotations for allow-skip
    assert validate_annotation_content("requires external service", "allow-skip")
    assert validate_annotation_content("manual-only test", "allow-skip")

    # Invalid annotations
    assert not validate_annotation_content("just because", "allow-no-assert")
    assert not validate_annotation_content("allow everything", "allow-no-assert")
    assert not validate_annotation_content("", "allow-swallow")
    assert not validate_annotation_content("no reason", "allow-skip")

    # Unknown pattern type
    assert not validate_annotation_content("any reason", "unknown-pattern")


# ---- known-debt ledger mode (#3186 Phase A) ----
#
# The ledger compares per-finding IDENTITIES (pattern + file + class-qualified
# function) count-exactly, so new debt is red, fixed debt must be pruned, and
# pruning can never enroll anything.


def _run_ledger(tmp_path: Path, *extra: str, scan: str | None = None):
    return subprocess.run(
        [sys.executable, str(SCANNER), str(tmp_path if scan is None else scan), *extra],
        capture_output=True,
        text=True,
    )


def _entry(tmp_path: Path, rel: str, pattern: str, function: str, count: int = 1) -> dict:
    return {
        "pattern": pattern,
        "file": str(tmp_path / rel),
        "function": function,
        "count": count,
    }


def _write_ledger(tmp_path: Path, entries: list[dict]) -> Path:
    import json

    ledger = tmp_path / "ledger.json"
    ledger.write_text(json.dumps({"version": 1, "entries": entries}, indent=2))
    return ledger


TWO_CLASS_SAME_NAME = """\
class TestA:
    def test_foo(self):
        do_thing()

class TestB:
    def test_foo(self):
        do_other()
"""

MODULE_LEVEL_SWALLOW = """\
try:
    setup()
except Exception:
    pass

def test_ok():
    assert setup() is not None
"""


def test_ledger_green_when_exact(tmp_path: Path) -> None:
    _write(tmp_path, "test_x.py", NO_ASSERT)
    ledger = _write_ledger(tmp_path, [_entry(tmp_path, "test_x.py", "no_assertion", "test_b")])
    proc = _run_ledger(tmp_path, "--ledger", str(ledger))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "mirrors reality" in proc.stdout


def test_ledger_green_covers_known_p0_debt(tmp_path: Path) -> None:
    """The known P0s must pass under the ledger (early return before the P0 rule)."""
    _write(tmp_path, "test_x.py", NO_ASSERT)
    ledger = _write_ledger(tmp_path, [_entry(tmp_path, "test_x.py", "no_assertion", "test_b")])
    proc = _run_ledger(tmp_path, "--ledger", str(ledger))
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_ledger_fails_on_unknown_finding(tmp_path: Path) -> None:
    _write(tmp_path, "test_x.py", NO_ASSERT)
    ledger = _write_ledger(tmp_path, [])
    proc = _run_ledger(tmp_path, "--ledger", str(ledger))
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "unknown finding" in proc.stdout


def test_ledger_fails_on_stale_entry(tmp_path: Path) -> None:
    """A fixed finding without ledger pruning is red (ledger mirrors reality)."""
    ledger = _write_ledger(tmp_path, [_entry(tmp_path, "test_x.py", "no_assertion", "test_b")])
    _write(tmp_path, "test_clean.py", "def test_ok():\n    assert True\n")
    proc = _run_ledger(tmp_path, "--ledger", str(ledger))
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "stale ledger entry" in proc.stdout


def test_ledger_count_exact_same_identity(tmp_path: Path) -> None:
    """Two hits under one identity need count 2; a second silent hit is red."""
    two_swallows = (
        "def test_double():\n"
        "    try:\n        a()\n    except Exception:\n        pass\n"
        "    try:\n        b()\n    except Exception:\n        pass\n"
        "    assert True\n"
    )
    _write(tmp_path, "test_double.py", two_swallows)
    short = _write_ledger(
        tmp_path, [_entry(tmp_path, "test_double.py", "broad_except_swallow", "test_double", 1)]
    )
    proc = _run_ledger(tmp_path, "--ledger", str(short))
    assert proc.returncode == 1, proc.stdout + proc.stderr
    exact = _write_ledger(
        tmp_path, [_entry(tmp_path, "test_double.py", "broad_except_swallow", "test_double", 2)]
    )
    proc = _run_ledger(tmp_path, "--ledger", str(exact))
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_ledger_identity_is_class_qualified(tmp_path: Path) -> None:
    """Same-named methods in different classes are distinct identities."""
    _write(tmp_path, "test_cls.py", TWO_CLASS_SAME_NAME)
    half = _write_ledger(
        tmp_path, [_entry(tmp_path, "test_cls.py", "no_assertion", "TestA.test_foo")]
    )
    proc = _run_ledger(tmp_path, "--ledger", str(half))
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "TestB.test_foo" in proc.stdout
    full = _write_ledger(
        tmp_path,
        [
            _entry(tmp_path, "test_cls.py", "no_assertion", "TestA.test_foo"),
            _entry(tmp_path, "test_cls.py", "no_assertion", "TestB.test_foo"),
        ],
    )
    proc = _run_ledger(tmp_path, "--ledger", str(full))
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_ledger_module_level_identity(tmp_path: Path) -> None:
    _write(tmp_path, "test_mod.py", MODULE_LEVEL_SWALLOW)
    ledger = _write_ledger(
        tmp_path, [_entry(tmp_path, "test_mod.py", "broad_except_swallow", "<module>")]
    )
    proc = _run_ledger(tmp_path, "--ledger", str(ledger))
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_prune_removes_stale_entries(tmp_path: Path) -> None:
    _write(tmp_path, "test_x.py", NO_ASSERT)
    ledger = _write_ledger(
        tmp_path,
        [
            _entry(tmp_path, "test_x.py", "no_assertion", "test_b"),
            _entry(tmp_path, "test_gone.py", "no_assertion", "test_b"),
        ],
    )
    proc = _run_ledger(tmp_path, "--prune-ledger", str(ledger))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    import json

    data = json.loads(ledger.read_text())
    assert [e["function"] for e in data["entries"]] == ["test_b"]
    assert "removed (fixed)" in proc.stdout


def test_prune_refuses_unknown_finding(tmp_path: Path) -> None:
    _write(tmp_path, "test_x.py", NO_ASSERT)
    ledger = _write_ledger(tmp_path, [])
    proc = _run_ledger(tmp_path, "--prune-ledger", str(ledger))
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "cannot enroll" in proc.stdout
    import json

    assert json.loads(ledger.read_text())["entries"] == []


def test_prune_refuses_count_increase(tmp_path: Path) -> None:
    module_two = (
        "try:\n    a()\nexcept Exception:\n    pass\n"
        "try:\n    b()\nexcept Exception:\n    pass\n"
        "def test_ok():\n    assert True\n"
    )
    _write(tmp_path, "test_mod2.py", module_two)
    ledger = _write_ledger(
        tmp_path, [_entry(tmp_path, "test_mod2.py", "broad_except_swallow", "<module>", 1)]
    )
    proc = _run_ledger(tmp_path, "--prune-ledger", str(ledger))
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "cannot raise counts" in proc.stdout


def test_prune_noop_when_mirroring(tmp_path: Path) -> None:
    _write(tmp_path, "test_x.py", NO_ASSERT)
    ledger = _write_ledger(tmp_path, [_entry(tmp_path, "test_x.py", "no_assertion", "test_b")])
    before = ledger.read_text()
    proc = _run_ledger(tmp_path, "--prune-ledger", str(ledger))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert ledger.read_text() == before
    assert "no changes" in proc.stdout


def test_ledger_and_prune_are_mutually_exclusive(tmp_path: Path) -> None:
    ledger = _write_ledger(tmp_path, [])
    proc = _run_ledger(tmp_path, "--ledger", str(ledger), "--prune-ledger", str(ledger))
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "mutually exclusive" in proc.stdout
