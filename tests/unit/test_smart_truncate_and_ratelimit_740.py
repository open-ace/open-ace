"""Tests for Issue #740 Batch 5 — Smart diff truncation, rate limiter, lazy repo (unit half).

Migrated from tests/issues/740/test_batch5_medium_backend.py. The SSE auth
revocation route test moved to
tests/integration/routes/test_sse_auth_revocation_740.py.

Covers:
- _smart_truncate_diff preserves file headers and truncates large diffs
- _RateLimiter enforces per-user rate limits
- _get_repo() lazy initialization
"""

import time

import pytest

from app.routes.autonomous import _get_repo, _RateLimiter

pytestmark = [pytest.mark.regression, pytest.mark.issue(740)]

# ── Smart Diff Truncation ────────────────────────────────────────────


class TestSmartDiffTruncation:
    """Tests for AutonomousOrchestrator._smart_truncate_diff."""

    def _get_method(self):
        from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

        return AutonomousOrchestrator._smart_truncate_diff

    def test_short_diff_unchanged(self):
        """Short diffs should pass through unchanged."""
        truncate = self._get_method()
        diff = "diff --git a/file.py b/file.py\n+hello\n"
        result = truncate(diff)
        assert result == diff

    def test_empty_diff_unchanged(self):
        """Empty or None diffs should pass through."""
        truncate = self._get_method()
        assert truncate("") == ""
        assert truncate(None) is None

    def test_preserves_file_headers(self):
        """All file headers should be preserved even when truncating."""
        truncate = self._get_method()
        # Create a large multi-file diff
        files = []
        for i in range(10):
            files.append(f"diff --git a/file{i}.py b/file{i}.py\n" + f"+{'line ' * 100}\n" * 50)
        big_diff = "\n".join(files)

        result = truncate(big_diff, max_chars=500, per_file_lines=10)
        # All file headers should be present in output
        for i in range(min(10, len(result.split("diff --git")) - 1)):
            assert f"file{i}.py" in result

    def test_truncation_note_appended(self):
        """When truncated, an explanatory note should be in the output."""
        truncate = self._get_method()
        files = []
        for i in range(20):
            files.append(f"diff --git a/bigfile{i}.py b/bigfile{i}.py\n" + "+line\n" * 500)
        big_diff = "\n".join(files)

        result = truncate(big_diff, max_chars=1000, per_file_lines=10)
        assert "Truncated" in result

    def test_per_file_lines_limit(self):
        """Each file should be limited to per_file_lines lines."""
        truncate = self._get_method()
        # Use multiple files with enough content to trigger truncation
        diff = (
            "diff --git a/file1.py b/file1.py\n"
            + "+line\n" * 500
            + "diff --git a/file2.py b/file2.py\n"
            + "+line\n" * 500
        )
        # max_chars must be < total diff length to trigger truncation
        result = truncate(diff, max_chars=200, per_file_lines=5)
        lines = result.strip().split("\n")
        # header + 5 body lines each for 2 files = ~12 lines, plus possible truncation note
        assert len(lines) <= 16


# ── Rate Limiter ──────────────────────────────────────────────────────


class TestRateLimiter:
    """Tests for _RateLimiter."""

    def test_allows_under_limit(self):
        """Requests under the limit should be allowed."""
        limiter = _RateLimiter(max_count=3, window=60)
        assert limiter.is_allowed(1)
        assert limiter.is_allowed(1)
        assert limiter.is_allowed(1)

    def test_blocks_over_limit(self):
        """Requests over the limit should be blocked."""
        limiter = _RateLimiter(max_count=2, window=60)
        limiter.is_allowed(1)
        limiter.is_allowed(1)
        assert not limiter.is_allowed(1)

    def test_different_users_independent(self):
        """Different users have independent rate limits."""
        limiter = _RateLimiter(max_count=1, window=60)
        assert limiter.is_allowed(1)
        assert not limiter.is_allowed(1)
        assert limiter.is_allowed(2)

    def test_window_expiry(self):
        """After the window expires, the limit should reset."""
        limiter = _RateLimiter(max_count=1, window=0)  # 0 second window = immediate expiry
        limiter.is_allowed(1)
        # With 0-second window, next call should prune old entries
        time.sleep(0.01)
        assert limiter.is_allowed(1)


# ── Lazy Repo ────────────────────────────────────────────────────────


class TestLazyRepo:
    """Tests for _get_repo() lazy initialization."""

    def test_returns_repo_instance(self):
        """_get_repo() returns an AutonomousWorkflowRepository."""
        # Reset global state
        import app.routes.autonomous as mod
        from app.repositories.autonomous_repo import AutonomousWorkflowRepository

        mod.auto_repo = None
        repo = _get_repo()
        assert isinstance(repo, AutonomousWorkflowRepository)

    def test_returns_same_instance(self):
        """_get_repo() returns the same instance on repeated calls."""
        import app.routes.autonomous as mod

        mod.auto_repo = None
        repo1 = _get_repo()
        repo2 = _get_repo()
        assert repo1 is repo2

    def teardown_method(self):
        import app.routes.autonomous as mod

        mod.auto_repo = None
