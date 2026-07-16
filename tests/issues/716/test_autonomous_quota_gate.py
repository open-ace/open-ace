"""Tests for the user-quota gate on autonomous workflows.

Covers the three behaviours introduced for strict quota control:
  1. Over-quota users cannot create workflows (fail-closed 429).
  2. A running workflow whose owner goes over quota is paused (never advanced).
  3. A workflow paused by the quota gate auto-resumes once quota recovers —
     but a user's manual pause is never auto-resumed.

Quota is enforced here (not at the LLM proxy) because local autonomous agents
connect to the model API directly and bypass the proxy's 429.
"""

from typing import Optional
from unittest.mock import MagicMock, patch

import pytest

from app.services.autonomous_scheduler import (
    QUOTA_PAUSE_REASON_PREFIX,
    AutonomousScheduler,
    _is_quota_paused,
)

# ── helpers ──────────────────────────────────────────────────────────────

_QUOTA_PATH = "app.modules.governance.quota_manager.QuotaManager"


def _quota(allowed: bool, reason: Optional[str] = None):
    """Build a MagicMock QuotaManager whose check_quota returns *allowed*."""
    mock = MagicMock()
    mock.return_value.check_quota.return_value = {
        "allowed": allowed,
        "reason": reason or ("Quota exceeded" if not allowed else None),
    }
    return mock


def _make_workflow(**overrides):
    """Minimal workflow dict for scheduler tests."""
    base = {
        "workflow_id": "wf-1",
        "status": "planning",
        "user_id": 1,
        "batch_id": None,
        "worktree_path": "/tmp/wt-1",
        "branch_name": "auto-dev/wf-1",
        "project_path": "/tmp/proj",
        "current_phase": "planning",
    }
    base.update(overrides)
    return base


@pytest.fixture(autouse=True)
def _reset_scheduler_singleton():
    AutonomousScheduler._instance = None
    yield
    AutonomousScheduler._instance = None


# ── _is_quota_paused predicate ───────────────────────────────────────────


class TestIsQuotaPaused:
    def test_quota_paused_detected(self):
        wf = {"status": "paused", "error_message": f"{QUOTA_PAUSE_REASON_PREFIX}: daily tokens"}
        assert _is_quota_paused(wf) is True

    def test_manual_pause_not_detected(self):
        # A user's manual pause has no error_message (or unrelated text).
        assert _is_quota_paused({"status": "paused", "error_message": ""}) is False
        assert _is_quota_paused({"status": "paused", "error_message": "manual"}) is False

    def test_non_paused_status_ignored(self):
        # Even with the prefix text, a non-paused workflow is not "quota paused".
        wf = {"status": "planning", "error_message": f"{QUOTA_PAUSE_REASON_PREFIX}: x"}
        assert _is_quota_paused(wf) is False


# ── runtime gate: over-quota → pause ─────────────────────────────────────


class TestRuntimeQuotaGate:
    """_advance_single pauses (never advances) an over-quota workflow."""

    def test_over_quota_pauses_before_advance(self):
        scheduler = AutonomousScheduler()
        repo = MagicMock()
        repo.get_workflow.return_value = _make_workflow()
        repo.acquire_lock.return_value = True

        with (
            patch("app.routes.autonomous._get_repo", return_value=repo),
            patch("app.routes.autonomous._pause_running_task") as mock_pause_task,
            patch(_QUOTA_PATH, _quota(allowed=False, reason="Daily token quota exceeded")),
            patch(
                "app.modules.workspace.autonomous.orchestrator.AutonomousOrchestrator"
            ) as mock_orch_cls,
        ):
            scheduler._advance_single("wf-1")

            # The orchestrator must never be built or advanced.
            mock_orch_cls.assert_not_called()
            mock_orch_cls.return_value.advance.assert_not_called()
            # The agent subprocess is paused and the row flipped to paused.
            mock_pause_task.assert_called_once_with("wf-1")
            update = repo.update_workflow.call_args[0][1]
            assert update["status"] == "paused"
            # error_message always starts with the marker prefix (auto-resume
            # keys on it); the redundant "quota exceeded" tail is collapsed so
            # the banner reads "Quota exceeded: Daily token" not the doubled form.
            assert update["error_message"].startswith(QUOTA_PAUSE_REASON_PREFIX)
            assert "Daily token" in update["error_message"]
            assert "quota exceeded" not in update["error_message"].lower().replace(
                QUOTA_PAUSE_REASON_PREFIX.lower(), ""
            )
            # DB lock + in-progress slot released even on the early return.
            repo.release_lock.assert_called_once()

    def test_under_quota_advances_normally(self):
        scheduler = AutonomousScheduler()
        repo = MagicMock()
        repo.get_workflow.return_value = _make_workflow()
        repo.acquire_lock.return_value = True

        with (
            patch("app.routes.autonomous._get_repo", return_value=repo),
            patch(_QUOTA_PATH, _quota(allowed=True)),
            patch(
                "app.modules.workspace.autonomous.orchestrator.AutonomousOrchestrator"
            ) as mock_orch_cls,
        ):
            scheduler._advance_single("wf-1")
            mock_orch_cls.return_value.advance.assert_called_once()

    def test_quota_check_error_fail_closed_pauses(self):
        """If check_quota itself raises, the workflow is paused (fail-closed)."""
        scheduler = AutonomousScheduler()
        repo = MagicMock()
        repo.get_workflow.return_value = _make_workflow()
        repo.acquire_lock.return_value = True

        failing_quota = MagicMock()
        failing_quota.return_value.check_quota.side_effect = RuntimeError("db down")

        with (
            patch("app.routes.autonomous._get_repo", return_value=repo),
            patch("app.routes.autonomous._pause_running_task"),
            patch(_QUOTA_PATH, failing_quota),
            patch(
                "app.modules.workspace.autonomous.orchestrator.AutonomousOrchestrator"
            ) as mock_orch_cls,
        ):
            scheduler._advance_single("wf-1")
            mock_orch_cls.assert_not_called()
            update = repo.update_workflow.call_args[0][1]
            assert update["status"] == "paused"
            assert update["error_message"].startswith(QUOTA_PAUSE_REASON_PREFIX)


# ── auto-resume ──────────────────────────────────────────────────────────


class TestAutoResumeQuotaPaused:
    """_auto_resume_quota_paused restores quota-paused workflows only."""

    def test_quota_paused_resumed_when_quota_recovers(self):
        scheduler = AutonomousScheduler()
        repo = MagicMock()
        repo.get_paused_workflows.return_value = [
            _make_workflow(
                status="paused",
                error_message=f"{QUOTA_PAUSE_REASON_PREFIX}: daily tokens",
                current_phase="development",
            )
        ]

        with (
            patch("app.routes.autonomous._emit_event_safe"),
            patch(_QUOTA_PATH, _quota(allowed=True)),
        ):
            scheduler._auto_resume_quota_paused(repo)

            update = repo.update_workflow.call_args[0][1]
            assert update["status"] == "developing"  # restored from current_phase
            assert update["paused_at"] is None
            assert update["error_message"] == ""

    def test_manual_pause_not_resumed(self):
        """A workflow paused by the user (no quota prefix) must stay paused."""
        scheduler = AutonomousScheduler()
        repo = MagicMock()
        repo.get_paused_workflows.return_value = [
            _make_workflow(status="paused", error_message=""),
        ]

        with (
            patch("app.routes.autonomous._emit_event_safe"),
            patch(_QUOTA_PATH, _quota(allowed=True)),
        ):
            scheduler._auto_resume_quota_paused(repo)
            repo.update_workflow.assert_not_called()

    def test_still_over_quota_not_resumed(self):
        scheduler = AutonomousScheduler()
        repo = MagicMock()
        repo.get_paused_workflows.return_value = [
            _make_workflow(
                status="paused",
                error_message=f"{QUOTA_PAUSE_REASON_PREFIX}: daily tokens",
            )
        ]

        with (
            patch("app.routes.autonomous._emit_event_safe"),
            patch(_QUOTA_PATH, _quota(allowed=False)),
        ):
            scheduler._auto_resume_quota_paused(repo)
            repo.update_workflow.assert_not_called()

    def test_recovery_check_error_fail_closed_stays_paused(self):
        """If the recovery check itself errors, the workflow stays paused."""
        scheduler = AutonomousScheduler()
        repo = MagicMock()
        repo.get_paused_workflows.return_value = [
            _make_workflow(
                status="paused",
                error_message=f"{QUOTA_PAUSE_REASON_PREFIX}: daily tokens",
            )
        ]
        failing_quota = MagicMock()
        failing_quota.return_value.check_quota.side_effect = RuntimeError("db down")

        with (
            patch("app.routes.autonomous._emit_event_safe"),
            patch(_QUOTA_PATH, failing_quota),
        ):
            scheduler._auto_resume_quota_paused(repo)
            repo.update_workflow.assert_not_called()


# ── creation gate (endpoint-level) ───────────────────────────────────────
# Self-contained Flask client + auth stubs (mirror the sibling API test module
# without cross-file fixture coupling).

import os

import app.repositories.database as db_mod
from app.repositories.database import Database


@pytest.fixture
def auto_db(tmp_path):
    with patch.object(db_mod, "is_postgresql", return_value=False):
        orig = db_mod.adapt_sql
        db_mod.adapt_sql = lambda q: q
        db_path = str(tmp_path / "quota_gate.db")
        db = Database(db_url=f"sqlite:///{db_path}")
        conn = db.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE NOT NULL,
                    email TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    role TEXT DEFAULT 'user',
                    is_active INTEGER DEFAULT 1,
                    created_at TEXT,
                    updated_at TEXT
                )
                """
            )
            cursor.execute(
                "INSERT INTO users (username, email, password_hash, role) VALUES (?, ?, ?, ?)",
                ("admin", "admin@test.com", "hash", "admin"),
            )
            conn.commit()
            from app.repositories.schema_init import load_schema_from_file

            load_schema_from_file(db_url=db.db_url, dialect="sqlite")
            conn.commit()
        finally:
            conn.close()
        yield db
        db_mod.adapt_sql = orig
        try:
            os.unlink(db_path)
        except OSError:
            pass


def _mock_auth(user_id=1, role="admin"):
    return patch(
        "app.auth.decorators._load_user_from_token",
        return_value={
            "id": user_id,
            "username": "admin",
            "email": "admin@test.com",
            "role": role,
        },
    )


def _make_repo():
    repo = MagicMock()
    repo.create_workflow.return_value = {"workflow_id": "wf-mock", "status": "pending"}
    repo.get_workflow.return_value = None
    repo.list_workflows.return_value = []
    repo.count_workflows.return_value = 0
    return repo


@pytest.fixture
def client(auto_db):
    from app import create_app

    app = create_app({"TESTING": True})
    with app.app_context():
        c = app.test_client()
        c.set_cookie("session_token", "test-token")
        yield c


class TestCreateWorkflowQuotaGate:
    """POST /api/autonomous/workflows rejects over-quota users."""

    def test_over_quota_rejected(self, client):
        repo = _make_repo()
        with _mock_auth():
            with (
                patch("app.routes.autonomous.auto_repo", repo),
                patch(
                    _QUOTA_PATH,
                    _quota(allowed=False, reason="Daily token quota exceeded"),
                ),
            ):
                resp = client.post(
                    "/api/autonomous/workflows",
                    json={
                        "title": "T",
                        "requirements_text": "Build a feature",
                        "cli_tool": "claude-code",
                        "project_path": "/tmp/test",
                    },
                )
        assert resp.status_code == 429
        assert "Daily token quota exceeded" in resp.get_json()["error"]
        repo.create_workflow.assert_not_called()

    def test_quota_check_error_fail_closed_rejected(self, client):
        repo = _make_repo()
        failing_quota = MagicMock()
        failing_quota.return_value.check_quota.side_effect = RuntimeError("db down")
        with _mock_auth():
            with (
                patch("app.routes.autonomous.auto_repo", repo),
                patch(_QUOTA_PATH, failing_quota),
            ):
                resp = client.post(
                    "/api/autonomous/workflows",
                    json={
                        "title": "T",
                        "requirements_text": "Build a feature",
                        "cli_tool": "claude-code",
                        "project_path": "/tmp/test",
                    },
                )
        assert resp.status_code == 429
        repo.create_workflow.assert_not_called()
