"""Tests for CI repair fingerprint and failure-excerpt extraction (issue #1811).

Two bugs caused the merge-phase CI repair to give up after a single attempt
even though only a subset of the failing tools had been fixed:

Bug 1 (coarse signature): ``_ci_failure_signature`` only used the CI check NAME
(e.g. ``lint``). A single Actions job bundles black/isort/ruff/mypy, so fixing
black+ruff while mypy still fails left the signature identical — the
exhausted-unchanged guard wrongly marked the workflow failed.

Bug 2 (tail-only excerpt): ``get_check_failure_excerpt`` took the last 80 lines
of the log. pre-commit prints passing hooks *after* failing ones, so the mypy
error in the middle was truncated away and the agent never saw it.
"""

from unittest.mock import MagicMock

from app.modules.workspace.autonomous.github_ops import _extract_failure_lines

# ── Bug 2: _extract_failure_lines finds mid-log errors ───────────────────


class TestExtractFailureLines:
    """The failure extractor must surface error lines that sit in the middle of
    a long pre-commit log, not just the tail."""

    def test_finds_mypy_error_in_middle(self):
        # pre-commit runs black (pass) → isort (pass) → ruff (pass) → mypy (FAIL)
        # → bandit (pass). mypy's error is NOT in the last 3 lines.
        lines = [
            "black....................................................Passed",
            "isort....................................................Passed",
            "ruff.....................................................Passed",
            "mypy.....................................................Failed",
            "app/modules/workspace/api_key_proxy.py:1818:9: error: Returning Any",
            "Found 1 error in 1 file (checked 14 source files)",
            "bandit...................................................Passed",
            "trim trailing whitespace................................Passed",
            "check for added large files..............................Passed",
        ]
        result = _extract_failure_lines(lines, max_lines=80)
        text = "\n".join(result)
        # The mypy error line must be present even though it's not in the tail.
        assert "Returning Any" in text, "mid-log mypy error must be extracted"
        assert "mypy" in text

    def test_finds_multiple_distinct_errors(self):
        lines = [
            "black....................................................Failed",
            "would reformat app/foo.py",
            "ruff.....................................................Failed",
            "app/bar.py:10:5: F841 local variable 'x' is assigned to but never used",
            "mypy.....................................................Failed",
            "app/baz.py:5: error: no-any-return",
            "some passing hook........................................Passed",
        ]
        result = _extract_failure_lines(lines, max_lines=80)
        text = "\n".join(result)
        assert "would reformat" in text
        assert "F841" in text
        assert "no-any-return" in text

    def test_fallback_to_tail_when_no_markers(self):
        # Log with no recognizable failure markers → fall back to tail.
        lines = [f"line {i} nothing interesting" for i in range(100)]
        result = _extract_failure_lines(lines, max_lines=10)
        assert len(result) == 10
        assert result[-1] == "line 99 nothing interesting"

    def test_respects_max_lines_limit(self):
        lines = [f"file{i}.py:{i}: error: something {i}" for i in range(200)]
        result = _extract_failure_lines(lines, max_lines=20)
        assert len(result) <= 20


# ── Bug 1: fine-grained fingerprint reacts to partial fixes ──────────────


def _make_orchestrator():
    from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

    orch = AutonomousOrchestrator.__new__(AutonomousOrchestrator)
    return orch


class TestCiFailureFingerprint:
    """The fingerprint must differ when the set of failing sub-tools changes,
    so that fixing black+ruff (while mypy still fails) is NOT treated as
    'no change' by the exhausted-unchanged guard."""

    def test_different_excerpts_produce_different_fingerprints(self):
        orch = _make_orchestrator()
        gh = MagicMock()

        # Round 1: black + ruff + mypy all fail
        gh.get_check_failure_excerpt.return_value = (
            "black....Failed\nwould reformat app/foo.py\n"
            "ruff....Failed\napp/bar.py:10 F841\n"
            "mypy....Failed\napp/baz.py:5 error: no-any-return\n"
        )
        checks = [{"name": "lint", "state": "failure", "bucket": "fail"}]
        fp1 = orch._ci_failure_fingerprint(gh, checks)

        # Round 2: black + ruff fixed, only mypy fails
        gh.get_check_failure_excerpt.return_value = (
            "mypy....Failed\napp/baz.py:5 error: no-any-return\n"
        )
        fp2 = orch._ci_failure_fingerprint(gh, checks)

        assert fp1 != fp2, (
            "fingerprint must change when the failing sub-tools change "
            "(partial fix should not be treated as 'no change')"
        )

    def test_same_excerpt_produces_same_fingerprint(self):
        orch = _make_orchestrator()
        gh = MagicMock()
        excerpt = "mypy....Failed\napp/baz.py:5 error: no-any-return\n"
        gh.get_check_failure_excerpt.return_value = excerpt
        checks = [{"name": "lint", "state": "failure", "bucket": "fail"}]
        fp1 = orch._ci_failure_fingerprint(gh, checks)
        fp2 = orch._ci_failure_fingerprint(gh, checks)
        assert fp1 == fp2, "identical failures must produce identical fingerprint"

    def test_empty_excerpt_falls_back_to_name_only(self):
        orch = _make_orchestrator()
        gh = MagicMock()
        gh.get_check_failure_excerpt.return_value = ""
        checks = [{"name": "lint", "state": "failure", "bucket": "fail"}]
        fp = orch._ci_failure_fingerprint(gh, checks)
        # Still has the check name, just with an empty-content hash.
        assert "lint" in fp

    def test_path_normalization_in_fingerprint(self):
        """The fingerprint should ignore line-number/path noise so the same
        logical error is stable across re-runs."""
        orch = _make_orchestrator()
        gh = MagicMock()

        # Same error, different line number and absolute path prefix
        gh.get_check_failure_excerpt.return_value = (
            "/home/runner/work/open-ace/app/modules/x.py:10:5: error: Returning Any"
        )
        n1 = orch._normalize_failure_excerpt(
            "/home/runner/work/open-ace/app/modules/x.py:10:5: error: Returning Any"
        )
        n2 = orch._normalize_failure_excerpt("x.py:99: error: Returning Any")
        # After normalization both should collapse to the same error text
        # (path → basename, line:col stripped).
        assert n1 == n2, "same error at different line/path must normalize equal"
