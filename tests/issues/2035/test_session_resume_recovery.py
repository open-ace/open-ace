"""Tests for session-resume failure recovery (issue #2035).

When the Claude CLI fails to resume a session — either because the session
file is inaccessible (EPERM on macOS ``com.apple.provenance`` xattr) or because
the session ID no longer exists ("No conversation found") — the orchestrator
must clear the stale ``cli_session_id`` mapping and retry once with a fresh
session on the same tracking line, rather than permanently failing the
workflow.

These tests cover:
1. ``_extract_cli_result_error`` classifies EPERM resume failures as
   ``resume_session_failed`` (not ``unknown_cli_error``).
2. ``_run_agent`` detects session-resume failures and retries with
   ``resume=False`` after clearing the stale mapping.
"""

from unittest.mock import MagicMock, patch

import pytest

from app.modules.workspace.autonomous.agent_runner import _extract_cli_result_error
from app.modules.workspace.autonomous.models import AgentTaskResult


class TestExtractCliResultErrorResumeSessionFailed:
    """``_extract_cli_result_error`` must classify EPERM and "Failed to resume
    session" errors as ``resume_session_failed`` so the orchestrator can
    recover."""

    def test_eperm_resume_failure_classified_as_resume_session_failed(self):
        """The exact #2035 macOS scenario: EPERM opening the session file."""
        result = _extract_cli_result_error(
            {
                "is_error": True,
                "errors": [
                    "Failed to resume session: EPERM: operation not permitted, "
                    "open '/Users/rhuang/.claude/projects/.../abc.jsonl'"
                ],
            },
        )
        assert result[0] == "resume_session_failed"
        assert "EPERM" in result[1]

    def test_failed_to_resume_session_classified_as_resume_session_failed(self):
        """Generic 'Failed to resume session' without EPERM also recovers."""
        result = _extract_cli_result_error(
            {
                "is_error": True,
                "error": "Failed to resume session: session file corrupted",
            },
        )
        assert result[0] == "resume_session_failed"

    def test_eperm_without_resume_keyword_stays_unknown(self):
        """EPERM in a non-resume context is not a session-resume failure."""
        result = _extract_cli_result_error(
            {"is_error": True, "error": "EPERM: operation not permitted, mkdir '/tmp'"},
        )
        assert result[0] != "resume_session_failed"

    def test_no_conversation_found_still_resume_session_not_found(self):
        """Existing classification for 'No conversation found' is unchanged."""
        result = _extract_cli_result_error(
            {"is_error": True, "errors": ["No conversation found with session id abc"]},
        )
        assert result[0] == "resume_session_not_found"

    def test_eperm_via_stderr_hint_classified(self):
        """EPERM surfaced through stderr_hint is also classified."""
        result = _extract_cli_result_error(
            {"is_error": True, "subtype": "error"},
            stderr_hint="Failed to resume session: EPERM: operation not permitted",
        )
        assert result[0] == "resume_session_failed"


def _make_resume_failure_result(error_code="resume_session_failed"):
    """Create a failed AgentTaskResult from a session-resume failure."""
    return AgentTaskResult(
        session_id="tracking-sess-1",
        tracking_session_id="tracking-sess-1",
        success=False,
        error="Failed to resume session: EPERM: operation not permitted",
        error_code=error_code,
        total_tokens=0,
    )


def _make_success_result():
    """Create a successful AgentTaskResult after fresh-session retry."""
    return AgentTaskResult(
        session_id="tracking-sess-1",
        tracking_session_id="tracking-sess-1",
        source_session_id="new-cli-sess-xyz",
        response_text="Plan: do the thing",
        success=True,
        total_tokens=500,
    )


class TestRunAgentSessionResumeRecovery:
    """``_run_agent`` must retry with a fresh session after a resume failure."""

    def _make_orchestrator_with_mock_runner(self, wf_data):
        """Create an orchestrator whose _runner returns controlled results."""
        from app.modules.workspace.autonomous.github_ops import GitHubOps
        from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

        with (
            patch("app.modules.workspace.autonomous.orchestrator.Database"),
            patch(
                "app.modules.workspace.autonomous.orchestrator.AutonomousWorkflowRepository"
            ) as mock_repo_cls,
        ):
            mock_repo = MagicMock()
            mock_repo.get_workflow.return_value = wf_data
            mock_repo.list_milestones.return_value = []
            mock_repo.create_milestone.return_value = {
                "milestone_id": "ms-1",
                "workflow_id": wf_data["workflow_id"],
            }
            mock_repo.create_event.return_value = {"id": 1}
            mock_repo.update_workflow.return_value = wf_data
            mock_repo_cls.return_value = mock_repo

            orch = AutonomousOrchestrator(wf_data["workflow_id"])
            orch.repo = mock_repo

        orch.emitter = MagicMock()
        orch._runner = MagicMock()
        orch._runner.session_manager = MagicMock()
        orch._runner._uses_sidebar_session_source.return_value = False
        orch._link_session_to_current_milestone = MagicMock()
        orch._write_phase_usage = MagicMock()
        orch._clear_session_usage_offsets = MagicMock()
        orch._synthesize_transient_failure = MagicMock()
        orch._validate_repo_context_after_run = MagicMock(return_value="")
        orch._is_shutdown_requested = MagicMock(return_value=False)
        orch._is_upstream_hard_quota_exhausted = MagicMock(return_value=False)
        orch._resolve_effective_repo_context = MagicMock(
            return_value={"repo_path": "/tmp/test-project"}
        )
        orch._resolve_system_account = MagicMock(return_value=None)
        orch._update_workflow = MagicMock()
        orch._emit = MagicMock()
        orch._accumulate_tokens = MagicMock()
        orch._artifact_text = MagicMock(return_value="plan text")
        orch._artifact_tldr = MagicMock(return_value="tldr")
        orch._artifact_visible_text = MagicMock(return_value="visible")
        orch._post_github_comment = MagicMock()
        orch._create_milestone = MagicMock(
            return_value={"milestone_id": "ms-1", "workflow_id": wf_data["workflow_id"]}
        )
        orch._update_milestone = MagicMock()
        # Bypass trusted-git-context snapshot — tests use synthetic /tmp paths
        # that don't have a real .git directory. The snapshot fails closed in
        # production, so provide a trusted boundary here (same pattern as
        # tests/issues/716/test_orchestrator.py).
        orch._snapshot_repo_context = MagicMock(
            return_value={
                "context": {"repo_path": "/tmp/test-project"},
                "effective": {
                    "repo_path": "/tmp/test-project",
                    "top_level": "/tmp/test-project",
                    "git_dir": "/tmp/test-project/.git",
                    "git_identity": "1:1",
                    "common_dir": "/tmp/test-project/.git",
                    "common_identity": "1:1",
                    "origin": "",
                },
            }
        )
        orch._get_gh = MagicMock(return_value=None)
        orch._select_project_python_runtime = MagicMock(return_value=(None, None))
        return orch, mock_repo

    def test_resume_session_failed_triggers_fresh_session_retry(self):
        """When the first attempt fails with resume_session_failed, _run_agent
        clears cli_session_id and retries with resume=False."""
        wf = {
            "workflow_id": "wf-test",
            "cli_tool": "claude-code",
            "model": "claude-sonnet-4-6",
            "main_session_id": "tracking-sess-1",
            "current_phase": "planning",
            "status": "planning",
        }
        orch, _ = self._make_orchestrator_with_mock_runner(wf)

        # First call: resume fails. Second call (fresh session): success.
        orch._runner.run_agent_task.side_effect = [
            _make_resume_failure_result("resume_session_failed"),
            _make_success_result(),
        ]

        result = orch._run_agent(
            wf=wf,
            workflow_id="wf-test",
            cli_tool="claude-code",
            model="claude-sonnet-4-6",
            project_path="/tmp/test-project",
            prompt="Plan this",
            workspace_type="local",
            permission_mode="read-only",
            allowed_tools=[],
            session_line="main",
            milestone_id="ms-1",
        )

        assert result.success is True
        assert orch._runner.run_agent_task.call_count == 2

        # Verify the second call used resume=False
        second_call_kwargs = orch._runner.run_agent_task.call_args_list[1].kwargs
        assert second_call_kwargs["resume"] is False
        assert second_call_kwargs["resume_session_id"] is None

    def test_resume_session_not_found_triggers_fresh_session_retry(self):
        """When the first attempt fails with resume_session_not_found, _run_agent
        clears cli_session_id and retries with resume=False."""
        wf = {
            "workflow_id": "wf-test",
            "cli_tool": "claude-code",
            "model": "claude-sonnet-4-6",
            "main_session_id": "tracking-sess-1",
            "current_phase": "planning",
            "status": "planning",
        }
        orch, _ = self._make_orchestrator_with_mock_runner(wf)

        orch._runner.run_agent_task.side_effect = [
            _make_resume_failure_result("resume_session_not_found"),
            _make_success_result(),
        ]

        result = orch._run_agent(
            wf=wf,
            workflow_id="wf-test",
            cli_tool="claude-code",
            model="claude-sonnet-4-6",
            project_path="/tmp/test-project",
            prompt="Plan this",
            workspace_type="local",
            permission_mode="read-only",
            allowed_tools=[],
            session_line="main",
            milestone_id="ms-1",
        )

        assert result.success is True
        assert orch._runner.run_agent_task.call_count == 2
        second_call_kwargs = orch._runner.run_agent_task.call_args_list[1].kwargs
        assert second_call_kwargs["resume"] is False

    def test_resume_failure_clears_cli_session_id_mapping(self):
        """The stale cli_session_id mapping must be cleared so the next
        advance() doesn't try to resume the same broken session."""
        wf = {
            "workflow_id": "wf-test",
            "cli_tool": "claude-code",
            "model": "claude-sonnet-4-6",
            "main_session_id": "tracking-sess-1",
            "current_phase": "planning",
            "status": "planning",
        }
        orch, _ = self._make_orchestrator_with_mock_runner(wf)

        orch._runner.run_agent_task.side_effect = [
            _make_resume_failure_result("resume_session_failed"),
            _make_success_result(),
        ]

        orch._run_agent(
            wf=wf,
            workflow_id="wf-test",
            cli_tool="claude-code",
            model="claude-sonnet-4-6",
            project_path="/tmp/test-project",
            prompt="Plan this",
            workspace_type="local",
            permission_mode="read-only",
            allowed_tools=[],
            session_line="main",
            milestone_id="ms-1",
        )

        # Verify cli_session_id was cleared via session_manager
        orch._runner.session_manager.update_session_fields.assert_called_once_with(
            "tracking-sess-1", {"cli_session_id": ""}
        )

    def test_resume_failure_recovery_skipped_when_workflow_failed(self):
        """When the workflow status is 'failed', the recovery must not run —
        a concurrent failure path may have already marked the workflow."""
        wf = {
            "workflow_id": "wf-test",
            "cli_tool": "claude-code",
            "model": "claude-sonnet-4-6",
            "main_session_id": "tracking-sess-1",
            "current_phase": "planning",
            "status": "planning",
        }
        orch, mock_repo = self._make_orchestrator_with_mock_runner(wf)

        orch._runner.run_agent_task.return_value = _make_resume_failure_result(
            "resume_session_failed"
        )
        # Simulate a concurrent failure: workflow status flips to 'failed'
        # by the time the recovery check runs.
        mock_repo.get_workflow.return_value = {**wf, "status": "failed"}

        orch._run_agent(
            wf=wf,
            workflow_id="wf-test",
            cli_tool="claude-code",
            model="claude-sonnet-4-6",
            project_path="/tmp/test-project",
            prompt="Plan this",
            workspace_type="local",
            permission_mode="read-only",
            allowed_tools=[],
            session_line="main",
            milestone_id="ms-1",
        )

        # Only one call — no recovery retry when workflow already failed
        assert orch._runner.run_agent_task.call_count == 1
        orch._runner.session_manager.update_session_fields.assert_not_called()

    def test_non_resume_failure_does_not_trigger_recovery(self):
        """A generic CLI error (unknown_cli_error) must not trigger the
        fresh-session recovery."""
        wf = {
            "workflow_id": "wf-test",
            "cli_tool": "claude-code",
            "model": "claude-sonnet-4-6",
            "main_session_id": "tracking-sess-1",
            "current_phase": "planning",
            "status": "planning",
        }
        orch, _ = self._make_orchestrator_with_mock_runner(wf)

        orch._runner.run_agent_task.return_value = AgentTaskResult(
            session_id="tracking-sess-1",
            tracking_session_id="tracking-sess-1",
            success=False,
            error="Some random CLI error",
            error_code="unknown_cli_error",
        )

        orch._run_agent(
            wf=wf,
            workflow_id="wf-test",
            cli_tool="claude-code",
            model="claude-sonnet-4-6",
            project_path="/tmp/test-project",
            prompt="Plan this",
            workspace_type="local",
            permission_mode="read-only",
            allowed_tools=[],
            session_line="main",
            milestone_id="ms-1",
        )

        # Only one call — no recovery retry for non-resume errors
        assert orch._runner.run_agent_task.call_count == 1
        # cli_session_id was NOT cleared
        orch._runner.session_manager.update_session_fields.assert_not_called()

    def test_recovery_not_attempted_for_fresh_session_line(self):
        """When session_line has no field (one-off call, not main/review/test),
        recovery must not run — there's no mapping to clear."""
        wf = {
            "workflow_id": "wf-test",
            "cli_tool": "claude-code",
            "model": "claude-sonnet-4-6",
            "main_session_id": "",
            "current_phase": "planning",
            "status": "planning",
        }
        orch, _ = self._make_orchestrator_with_mock_runner(wf)

        orch._runner.run_agent_task.return_value = _make_resume_failure_result(
            "resume_session_failed"
        )

        orch._run_agent(
            wf=wf,
            workflow_id="wf-test",
            cli_tool="claude-code",
            model="claude-sonnet-4-6",
            project_path="/tmp/test-project",
            prompt="Plan this",
            workspace_type="local",
            permission_mode="read-only",
            allowed_tools=[],
            session_line="other",  # not in SESSION_LINE_FIELDS
            milestone_id="ms-1",
        )

        assert orch._runner.run_agent_task.call_count == 1
        orch._runner.session_manager.update_session_fields.assert_not_called()
