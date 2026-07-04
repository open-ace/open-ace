"""Tests for test-skip false-positive and retry-counter persistence (issue #1277).

Two bugs chained to false-fail autonomous workflows in the development phase:

Bug B (upstream trigger): ``_run_test_phase`` detected "tests skipped" via a
bare substring match on ``"TEST_STATUS: skipped"``. An agent that *explains*
"``TEST_STATUS: skipped`` 不适用" (asserting tests DID run) was false-positive
matched, so the workflow entered the skip-retry path even though tests passed.

Bug A (downstream amplifier): ``skip_retries`` / ``test_retries`` /
``dev_retries_on_test_fail`` were written via ``_update_workflow`` but were
absent from both the DB schema and ``ALLOWED_WORKFLOW_FIELDS``, so the writes
were silently filtered. The scheduler re-read 0 on the next ``advance()``,
re-ran the dev agent on a test-only retry, and false-failed with
"agent produced no code changes" (SHA unchanged).
"""

from unittest.mock import MagicMock, patch

import pytest

from app.modules.workspace.autonomous.models import AgentTaskResult

# ── helpers (mirror tests/issues/723/test_orchestrator_dev_salvage.py) ───


def _make_workflow(**overrides):
    base = {
        "workflow_id": "wf-1277",
        "user_id": 1,
        "title": "Test",
        "status": "developing",
        "requirements_text": "Build feature",
        "requirements_issue_url": "",
        "project_path": "/tmp/p",
        "project_repo_url": "",
        "is_new_project": False,
        "cli_tool": "claude-code",
        "model": "claude-sonnet-4-6",
        "permission_mode": "auto-edit",
        "branch_name": "auto-dev/x",
        "branch_strategy": "new-branch",
        "workspace_type": "local",
        "remote_machine_id": "",
        "worktree_path": "/tmp/p",
        "github_issue_number": None,
        "github_pr_number": None,
        "github_pr_url": "",
        "current_phase": "development",
        "current_round": 1,
        "dev_round": 1,
        "max_plan_rounds": 3,
        "max_pr_review_rounds": 5,
        "total_tokens": 0,
        "total_input_tokens": 0,
        "total_output_tokens": 0,
        "total_requests": 0,
        "error_message": "",
    }
    base.update(overrides)
    return base


def _agent_result(success=True, error=None, text="dev output"):
    return AgentTaskResult(
        session_id="sess",
        response_text=text,
        visible_response_text=text,
        success=success,
        error=error,
    )


def _make_orchestrator(wf_data, milestones=None):
    from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

    with (
        patch("app.modules.workspace.autonomous.orchestrator.Database"),
        patch(
            "app.modules.workspace.autonomous.orchestrator.AutonomousWorkflowRepository"
        ) as mock_repo_cls,
    ):
        mock_repo = MagicMock()
        mock_repo.get_workflow.return_value = wf_data
        mock_repo.list_milestones.return_value = milestones or []
        mock_repo.create_milestone.return_value = {
            "milestone_id": "ms-new",
            "workflow_id": wf_data["workflow_id"],
        }
        mock_repo.create_event.return_value = {"id": 1}
        mock_repo.update_workflow.return_value = wf_data
        mock_repo.update_milestone.return_value = {}
        mock_repo.update_workflow_tokens.return_value = None
        mock_repo_cls.return_value = mock_repo

        orch = AutonomousOrchestrator(wf_data["workflow_id"])
        orch.repo = mock_repo
        orch.emitter = MagicMock()
        orch._gh = MagicMock()
        orch._gh.get_current_commit.return_value = "abc1234"
        orch._gh.get_commit_diff_stats.return_value = {
            "additions": 10,
            "deletions": 0,
            "files": 1,
            "commits": 1,
        }
        orch._gh.get_diff_stats.return_value = {}
        orch._gh.has_uncommitted_changes.return_value = False
        return orch, mock_repo


# ── Bug B: TEST_STATUS marker must not match explanatory text ────────────


class TestSkipMarkerNotFalsePositive:
    """The skip tag ``TEST_STATUS: skipped`` must only match as a standalone
    line, not when the agent references it in an explanation. Issue #1277's
    agent wrote ``TEST_STATUS: skipped 不适用`` (tests passed) and was
    false-positive flagged."""

    def test_explanatory_reference_not_matched(self):
        import re

        # The exact text from issue #1277 that caused the false positive.
        text = "2423 passed, 1 skipped\n- `TEST_STATUS: skipped` 不适用，因为测试已实际执行并通过。"
        # The fix's regex: standalone line, case-insensitive.
        pat = re.compile(r"(?mi)^\s*TEST_STATUS:\s*skipped\s*$")
        assert not pat.search(text), "explanatory reference to TEST_STATUS: skipped must NOT match"

    def test_real_skip_tag_matched(self):
        import re

        # A genuine skip: agent emits the tag on its own line at the end.
        text = "Could not find pytest.\n\nTEST_STATUS: skipped"
        pat = re.compile(r"(?mi)^\s*TEST_STATUS:\s*skipped\s*$")
        assert pat.search(text), "standalone TEST_STATUS: skipped must match"

    def test_cn_marker_in_sentence_not_matched(self):
        import re

        # "跳过测试" embedded in a longer explanation sentence.
        text = "本次跳过测试是因为环境缺少 PostgreSQL 连接，但其他测试全通过。"
        pat = re.compile(r"(?m)^\s*(测试被跳过|跳过测试)\s*[。.]?\s*$")
        assert not pat.search(text), "inline 跳过测试 must NOT match"

    def test_cn_marker_standalone_matched(self):
        import re

        text = "所有测试框架都不可用。\n跳过测试。"
        pat = re.compile(r"(?m)^\s*(测试被跳过|跳过测试)\s*[。.]?\s*$")
        assert pat.search(text), "standalone 跳过测试 must match"


# ── Bug A: retry counters must persist via ALLOWED_WORKFLOW_FIELDS ───────


class TestRetryCounterPersistence:
    """``skip_retries`` / ``test_retries`` / ``dev_retries_on_test_fail``
    must be in ALLOWED_WORKFLOW_FIELDS so _update_workflow actually writes
    them instead of silently filtering."""

    def test_retry_fields_in_allowed_workflow_fields(self):
        from app.repositories.autonomous_repo import AutonomousWorkflowRepository

        fields = AutonomousWorkflowRepository.ALLOWED_WORKFLOW_FIELDS
        for col in ("test_retries", "skip_retries", "dev_retries_on_test_fail"):
            assert col in fields, f"{col} missing from ALLOWED_WORKFLOW_FIELDS"

    def test_skip_retries_persisted_on_test_skip(self):
        """When tests are skipped, _run_test_phase must write skip_retries=1
        to the workflow (not have it filtered out). Verified by checking the
        update_workflow call includes the field."""
        plan_ms = {
            "milestone_id": "ms-plan",
            "plan_content": "1. Implement",
            "status": "completed",
        }
        wf = _make_workflow()
        orch, mock_repo = _make_orchestrator(wf, milestones=[plan_ms])

        orch._runner = MagicMock()
        # Dev: success with a commit (sha changes).
        # Test: success but agent genuinely skipped (standalone tag).
        orch._runner.run_agent_task.side_effect = [
            _agent_result(success=True, text="Implementation done"),
            _agent_result(
                success=True,
                text="No test framework found.\n\nTEST_STATUS: skipped",
            ),
        ]
        orch._gh.get_current_commit.side_effect = ["aaa1111", "bbb2222"]

        orch._do_development(wf)

        # The skip-retry path must have written skip_retries=1.
        update_calls = mock_repo.update_workflow.call_args_list
        fields_list = [c[0][1] for c in update_calls if len(c[0]) > 1 and isinstance(c[0][1], dict)]
        assert any(
            f.get("skip_retries") == 1 for f in fields_list
        ), "skip_retries=1 must be persisted when tests are skipped"

    def test_real_tests_not_flagged_skipped(self):
        """When the test agent runs real tests (output has 'passed') and
        merely references the skip tag in explanation, it must NOT be flagged
        as skipped. This is the core #1277 regression: tests passed but the
        workflow was sent into the skip-retry loop."""
        plan_ms = {
            "milestone_id": "ms-plan",
            "plan_content": "1. Implement",
            "status": "completed",
        }
        wf = _make_workflow()
        orch, mock_repo = _make_orchestrator(wf, milestones=[plan_ms])

        orch._runner = MagicMock()
        orch._runner.run_agent_task.side_effect = [
            _agent_result(success=True, text="Implementation done"),
            _agent_result(
                success=True,
                text=(
                    "## 测试报告\n2423 passed, 1 skipped\n"
                    "- `TEST_STATUS: skipped` 不适用，因为测试已实际执行并通过。"
                ),
            ),
        ]
        orch._gh.get_current_commit.side_effect = ["aaa1111", "bbb2222"]

        orch._do_development(wf)

        # Must NOT enter skip-retry path: no skip_retries=1 write (only the
        # success-reset to 0 at line ~2834 is acceptable), no test milestone
        # marked "skipped by agent".
        update_calls = mock_repo.update_workflow.call_args_list
        fields_list = [c[0][1] for c in update_calls if len(c[0]) > 1 and isinstance(c[0][1], dict)]
        assert not any(
            f.get("skip_retries") == 1 for f in fields_list
        ), "must not flag skip_retries=1 when tests actually ran"

        ms_updates = mock_repo.update_milestone.call_args_list
        ms_fields = [c[0][1] for c in ms_updates if len(c[0]) > 1 and isinstance(c[0][1], dict)]
        assert not any(
            "skipped by agent" in (f.get("error_message") or "") for f in ms_fields
        ), "test milestone must not be marked 'skipped by agent'"


# ── Legacy runtime DDL must include the new columns (#1476 review) ──────


class TestLegacyDdlIncludesRetryColumns:
    """The runtime ``get_ddl_statements()`` path (used by SQLite dev/test
    setup) must create the three retry columns, otherwise
    ``_update_workflow({"skip_retries": 1})`` fails with
    ``OperationalError: no such column: skip_retries`` on fresh DBs that
    bypass Alembic."""

    def test_create_table_has_retry_columns(self):
        from app.modules.workspace.autonomous import get_ddl_statements

        ddl = "\n".join(get_ddl_statements())
        # The CREATE TABLE block must list all three columns so fresh DBs
        # have them without relying on the ALTER TABLE fallback.
        for col in ("test_retries", "skip_retries", "dev_retries_on_test_fail"):
            assert col in ddl, f"{col} missing from get_ddl_statements()"

    def test_alter_table_adds_retry_columns_for_existing_dbs(self):
        from app.modules.workspace.autonomous import get_ddl_statements

        alters = [s for s in get_ddl_statements() if s.strip().upper().startswith("ALTER TABLE")]
        for col in ("test_retries", "skip_retries", "dev_retries_on_test_fail"):
            assert any(col in a for a in alters), f"no ALTER TABLE adds {col} for existing DBs"

    def test_update_skip_retries_succeeds_on_fresh_sqlite(self, tmp_path):
        """End-to-end: init a fresh SQLite DB via get_ddl_statements(), then
        update_workflow with skip_retries must not raise."""
        import sqlite3

        from app.modules.workspace.autonomous import get_ddl_statements
        from app.repositories.autonomous_repo import AutonomousWorkflowRepository

        db_path = str(tmp_path / "test.db")
        conn = sqlite3.connect(db_path)
        try:
            cursor = conn.cursor()
            for sql in get_ddl_statements():
                try:
                    cursor.execute(sql)
                except Exception:
                    pass
            conn.commit()
        finally:
            conn.close()

        # Minimal workflow row so update_workflow has something to update.
        conn = sqlite3.connect(db_path)
        try:
            conn.execute(
                "INSERT INTO autonomous_workflows (workflow_id, title, status, cli_tool, "
                "branch_strategy, workspace_type) VALUES (?, ?, ?, ?, ?, ?)",
                ("wf-ddl", "t", "developing", "claude-code", "new-branch", "local"),
            )
            conn.commit()
        finally:
            conn.close()

        db = MagicMock()
        db._db_path = db_path
        repo = AutonomousWorkflowRepository.__new__(AutonomousWorkflowRepository)
        repo.db = db

        # Monkeypatch a real sqlite execute for this test.
        def _execute(query, params=()):
            c = sqlite3.connect(db_path)
            c.execute(query, params)
            c.commit()
            c.close()

        def _fetch_one(query, params=()):
            c = sqlite3.connect(db_path)
            row = c.execute(query, params).fetchone()
            c.close()
            return row

        db.execute = _execute
        db.fetch_one = _fetch_one

        # This must not raise OperationalError.
        repo.update_workflow("wf-ddl", {"skip_retries": 1})
