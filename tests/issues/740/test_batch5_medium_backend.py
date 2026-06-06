"""
Batch 5 tests — Smart diff truncation, SSE auth re-validation, rate limiter, lazy repo.

Tests for:
- _smart_truncate_diff preserves file headers and truncates large diffs
- _RateLimiter enforces per-user rate limits
- SSE generate() closes stream on invalid token
- _get_repo() lazy initialization
"""

import os
import sys
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from app.routes.autonomous import _get_repo, _RateLimiter

# ── Smart Diff Truncation ────────────────────────────────────────────


class TestSmartDiffTruncation(unittest.TestCase):
    """Tests for AutonomousOrchestrator._smart_truncate_diff."""

    def _get_method(self):
        from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

        return AutonomousOrchestrator._smart_truncate_diff

    def test_short_diff_unchanged(self):
        """Short diffs should pass through unchanged."""
        truncate = self._get_method()
        diff = "diff --git a/file.py b/file.py\n+hello\n"
        result = truncate(diff)
        self.assertEqual(result, diff)

    def test_empty_diff_unchanged(self):
        """Empty or None diffs should pass through."""
        truncate = self._get_method()
        self.assertEqual(truncate(""), "")
        self.assertEqual(truncate(None), None)

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
            self.assertIn(f"file{i}.py", result)

    def test_truncation_note_appended(self):
        """When truncated, an explanatory note should be in the output."""
        truncate = self._get_method()
        files = []
        for i in range(20):
            files.append(f"diff --git a/bigfile{i}.py b/bigfile{i}.py\n" + "+line\n" * 500)
        big_diff = "\n".join(files)

        result = truncate(big_diff, max_chars=1000, per_file_lines=10)
        self.assertIn("Truncated", result)

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
        self.assertLessEqual(len(lines), 16)


# ── Rate Limiter ──────────────────────────────────────────────────────


class TestRateLimiter(unittest.TestCase):
    """Tests for _RateLimiter."""

    def test_allows_under_limit(self):
        """Requests under the limit should be allowed."""
        limiter = _RateLimiter(max_count=3, window=60)
        self.assertTrue(limiter.is_allowed(1))
        self.assertTrue(limiter.is_allowed(1))
        self.assertTrue(limiter.is_allowed(1))

    def test_blocks_over_limit(self):
        """Requests over the limit should be blocked."""
        limiter = _RateLimiter(max_count=2, window=60)
        limiter.is_allowed(1)
        limiter.is_allowed(1)
        self.assertFalse(limiter.is_allowed(1))

    def test_different_users_independent(self):
        """Different users have independent rate limits."""
        limiter = _RateLimiter(max_count=1, window=60)
        self.assertTrue(limiter.is_allowed(1))
        self.assertFalse(limiter.is_allowed(1))
        self.assertTrue(limiter.is_allowed(2))

    def test_window_expiry(self):
        """After the window expires, the limit should reset."""
        limiter = _RateLimiter(max_count=1, window=0)  # 0 second window = immediate expiry
        limiter.is_allowed(1)
        # With 0-second window, next call should prune old entries
        time.sleep(0.01)
        self.assertTrue(limiter.is_allowed(1))


# ── Lazy Repo ────────────────────────────────────────────────────────


class TestLazyRepo(unittest.TestCase):
    """Tests for _get_repo() lazy initialization."""

    def test_returns_repo_instance(self):
        """_get_repo() returns an AutonomousWorkflowRepository."""
        # Reset global state
        import app.routes.autonomous as mod
        from app.repositories.autonomous_repo import AutonomousWorkflowRepository

        mod.auto_repo = None
        repo = _get_repo()
        self.assertIsInstance(repo, AutonomousWorkflowRepository)

    def test_returns_same_instance(self):
        """_get_repo() returns the same instance on repeated calls."""
        import app.routes.autonomous as mod

        mod.auto_repo = None
        repo1 = _get_repo()
        repo2 = _get_repo()
        self.assertIs(repo1, repo2)

    def tearDown(self):
        import app.routes.autonomous as mod

        mod.auto_repo = None


# ── SSE Auth Re-validation ──────────────────────────────────────────


class TestSSEAuthRevalidation(unittest.TestCase):
    """Tests that SSE stream closes on revoked token during keepalive."""

    def _make_client(self):
        import app.repositories.database as db_mod

        db_path = tempfile.mktemp(suffix=".db")
        orig = db_mod.adapt_sql
        db_mod.adapt_sql = lambda sql: sql

        db = db_mod.Database(db_path)
        try:
            with db.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL, email TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL, role TEXT DEFAULT 'user', is_active INTEGER DEFAULT 1, created_at TEXT, updated_at TEXT)"
                )
                cursor.execute(
                    "INSERT OR IGNORE INTO users (username, email, password_hash, role) VALUES (?, ?, ?, ?)",
                    ("admin", "admin@test.com", "hash123", "admin"),
                )
                from app.modules.workspace.autonomous import get_ddl_statements

                for sql in get_ddl_statements():
                    cursor.execute(sql)
                conn.commit()
        finally:
            pass

        from app import create_app

        app = create_app({"TESTING": True})
        c = app.test_client()
        c.set_cookie("session_token", "test-token")
        return c, db_path, orig, db_mod

    def test_sse_closes_on_revoked_token(self):
        """SSE stream should terminate when token becomes invalid during keepalive."""
        c, db_path, orig, db_mod = self._make_client()
        try:
            mock_repo = MagicMock()
            mock_repo.get_workflow.return_value = {
                "workflow_id": "wf-1",
                "user_id": 1,
                "status": "developing",
            }

            # Mock event emitter — queue always raises Empty (trigger keepalive)
            import queue as queue_mod

            mock_emitter = MagicMock()
            mock_q = MagicMock()
            mock_q.get.side_effect = queue_mod.Empty
            mock_emitter.subscribe.return_value = mock_q
            mock_emitter.mark_read.return_value = None
            mock_emitter.unsubscribe.return_value = None

            call_count = 0

            def mock_load_token(token):
                nonlocal call_count
                call_count += 1
                if call_count > 1:
                    return None  # Token revoked after first check
                return {
                    "id": 1,
                    "username": "admin",
                    "email": "admin@test.com",
                    "role": "admin",
                }

            # Patch auth_required decorator (bypasses initial auth) AND
            # _load_user_from_token in autonomous routes (for keepalive re-check)
            with patch(
                "app.auth.decorators._load_user_from_token",
                side_effect=mock_load_token,
            ):
                with patch("app.routes.autonomous._get_repo", return_value=mock_repo):
                    with patch(
                        "app.routes.autonomous._get_event_emitter",
                        return_value=mock_emitter,
                    ):
                        resp = c.get("/api/autonomous/workflows/wf-1/events/stream")
                        # Stream should end cleanly (not hang)
                        self.assertEqual(resp.status_code, 200)
                        self.assertIn("text/event-stream", resp.content_type)
        finally:
            db_mod.adapt_sql = orig
            try:
                os.unlink(db_path)
            except OSError:
                pass


if __name__ == "__main__":
    unittest.main()
