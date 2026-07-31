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
            "tracking-sess-1", {"cli_session_id": ""}, require_tenant=False
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


class TestRunAgentSessionResumeRecoveryFollowup:
    """Follow-up tests for #2035 covering edge cases flagged in review:

    - Token usage accumulated across failed+retry
    - Recovery skipped for 'cancelled' and 'paused' workflow states
    - Recovery retry that also fails does not loop
    - ``_emit('session_resume_recovery', ...)`` event parameters
    - ``kwargs['session_id']`` refreshed before the recovery retry
    """

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

    def _make_wf(self, status="planning"):
        return {
            "workflow_id": "wf-test",
            "cli_tool": "claude-code",
            "model": "claude-sonnet-4-6",
            "main_session_id": "tracking-sess-1",
            "current_phase": "planning",
            "status": status,
        }

    def _run_default(self, orch, wf):
        """Invoke _run_agent with the standard planning-phase args."""
        return orch._run_agent(
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

    def test_token_usage_accumulated_across_failed_and_retry(self):
        """The failed attempt's token usage must be accumulated so
        _write_phase_usage receives the combined totals (#2035 follow-up)."""
        wf = self._make_wf()
        orch, _ = self._make_orchestrator_with_mock_runner(wf)

        failed_result = AgentTaskResult(
            session_id="tracking-sess-1",
            tracking_session_id="tracking-sess-1",
            success=False,
            error="Failed to resume session: EPERM",
            error_code="resume_session_failed",
            total_tokens=120,
            total_input_tokens=80,
            total_output_tokens=40,
        )
        success_result = AgentTaskResult(
            session_id="tracking-sess-1",
            tracking_session_id="tracking-sess-1",
            source_session_id="new-cli-sess",
            response_text="Plan: do the thing",
            success=True,
            total_tokens=500,
            total_input_tokens=350,
            total_output_tokens=150,
        )
        orch._runner.run_agent_task.side_effect = [failed_result, success_result]

        self._run_default(orch, wf)

        # _write_phase_usage is called as (milestone_id, result, retry_usage)
        # and computes prior_usage + result.total_tokens. The final call must
        # carry the failed attempt's usage in retry_usage (arg index 2) so it
        # is not lost — the retry result's own usage is added from `result`.
        assert orch._write_phase_usage.called
        final_call = orch._write_phase_usage.call_args
        prior_usage = final_call.args[2]
        final_result = final_call.args[1]
        # retry_usage accumulated the failed attempt (120 in, 80/40 split)
        assert prior_usage["total_tokens"] == 120
        assert prior_usage["total_input_tokens"] == 80
        assert prior_usage["total_output_tokens"] == 40
        # The retry result carries its own usage (500)
        assert final_result.total_tokens == 500
        # The effective total written = prior_usage + result = 620
        assert prior_usage["total_tokens"] + final_result.total_tokens == 620

    def test_recovery_skipped_for_cancelled_workflow(self):
        """Guard checks 'cancelled' — must suppress the recovery retry
        (#2035 follow-up). 'paused' is covered separately because it raises
        WorkflowPaused inside the retry loop before reaching the recovery
        block."""
        wf = self._make_wf(status="planning")
        orch, mock_repo = self._make_orchestrator_with_mock_runner(wf)

        orch._runner.run_agent_task.return_value = _make_resume_failure_result(
            "resume_session_failed"
        )
        # Workflow status flips to cancelled before recovery check
        mock_repo.get_workflow.return_value = {**wf, "status": "cancelled"}

        self._run_default(orch, wf)

        assert orch._runner.run_agent_task.call_count == 1
        orch._runner.session_manager.update_session_fields.assert_not_called()

    def test_paused_workflow_raises_before_recovery_block(self):
        """A 'paused' workflow status is caught earlier by abort_paused_retry()
        inside the retry loop, which raises WorkflowPaused before the recovery
        block is reached. This confirms 'paused' workflows never trigger a
        fresh-session retry (#2035 follow-up)."""
        from app.modules.workspace.autonomous.orchestrator import WorkflowPaused

        wf = self._make_wf(status="planning")
        orch, mock_repo = self._make_orchestrator_with_mock_runner(wf)

        orch._runner.run_agent_task.return_value = _make_resume_failure_result(
            "resume_session_failed"
        )
        mock_repo.get_workflow.return_value = {**wf, "status": "paused"}

        with pytest.raises(WorkflowPaused):
            self._run_default(orch, wf)

        # Only the original attempt ran — no recovery retry
        assert orch._runner.run_agent_task.call_count == 1
        orch._runner.session_manager.update_session_fields.assert_not_called()

    def test_recovery_retry_failure_does_not_loop(self):
        """If the fresh-session retry ALSO fails (with a non-resume error),
        the recovery block must not retry again — exactly 2 calls total."""
        wf = self._make_wf()
        orch, _ = self._make_orchestrator_with_mock_runner(wf)

        first_failure = _make_resume_failure_result("resume_session_failed")
        second_failure = AgentTaskResult(
            session_id="tracking-sess-1",
            tracking_session_id="tracking-sess-1",
            success=False,
            error="Some other CLI error after fresh start",
            error_code="unknown_cli_error",
        )
        orch._runner.run_agent_task.side_effect = [first_failure, second_failure]

        result = self._run_default(orch, wf)

        assert orch._runner.run_agent_task.call_count == 2
        assert result.success is False
        assert result.error_code == "unknown_cli_error"

    def test_session_resume_recovery_event_emitted_with_correct_params(self):
        """The ``session_resume_recovery`` event must carry the error_code and
        session_line for observability (#2035 follow-up)."""
        wf = self._make_wf()
        orch, _ = self._make_orchestrator_with_mock_runner(wf)

        orch._runner.run_agent_task.side_effect = [
            _make_resume_failure_result("resume_session_failed"),
            _make_success_result(),
        ]

        self._run_default(orch, wf)

        orch._emit.assert_any_call(
            "session_resume_recovery",
            {"error_code": "resume_session_failed", "session_line": "main"},
        )

    def test_recovery_refreshes_session_id_kwarg_before_retry(self):
        """The recovery retry must set kwargs['session_id'] to the tracking
        id so the app-server adapter path uses the correct session row
        (#2035 follow-up)."""
        wf = self._make_wf()
        orch, _ = self._make_orchestrator_with_mock_runner(wf)

        orch._runner.run_agent_task.side_effect = [
            _make_resume_failure_result("resume_session_failed"),
            _make_success_result(),
        ]

        self._run_default(orch, wf)

        second_call_kwargs = orch._runner.run_agent_task.call_args_list[1].kwargs
        assert second_call_kwargs["session_id"] == "tracking-sess-1"
        assert second_call_kwargs["resume"] is False
        assert second_call_kwargs["resume_session_id"] is None

    def test_recovery_updates_session_usage_offsets_and_current_session_id(self):
        """The recovery block must update _session_usage_offsets and
        _current_session_id before the retry call, matching the retry-loop
        pattern. Verify by directly asserting _session_usage_offsets carries
        the accumulated retry_usage from the failed attempt
        (#2035 follow-up, Nit 2).

        Note: usage_session_ids is initialized with tracking_session_id at
        line 4313, so checking membership in _clear_session_usage_offsets is
        trivially true. Instead, assert the _session_usage_offsets dict
        content — it is initialized to all-zeros at line 4446, then the
        recovery block (line 4756-4757) accumulates the failed attempt's
        tokens and writes the updated dict at line 4768. Because
        _clear_session_usage_offsets is mocked, the value persists."""
        wf = self._make_wf()
        orch, _ = self._make_orchestrator_with_mock_runner(wf)

        # Use a failure result with non-zero tokens so the accumulation is
        # observable in _session_usage_offsets.
        failed_with_tokens = AgentTaskResult(
            session_id="tracking-sess-1",
            tracking_session_id="tracking-sess-1",
            success=False,
            error="Failed to resume session: EPERM",
            error_code="resume_session_failed",
            total_tokens=120,
            total_input_tokens=80,
            total_output_tokens=40,
        )
        orch._runner.run_agent_task.side_effect = [
            failed_with_tokens,
            _make_success_result(),
        ]

        self._run_default(orch, wf)

        # _session_usage_offsets[tracking_session_id] must reflect the
        # accumulated retry_usage (120 tokens from the failed attempt).
        # _clear_session_usage_offsets is mocked so the entry is not removed.
        offsets = getattr(orch, "_session_usage_offsets", {})
        assert "tracking-sess-1" in offsets
        assert offsets["tracking-sess-1"]["total_tokens"] == 120
        assert offsets["tracking-sess-1"]["total_input_tokens"] == 80
        assert offsets["tracking-sess-1"]["total_output_tokens"] == 40

    def test_recovery_block_guard_directly_blocks_failed_status(self):
        """Directly exercise the recovery block's own defensive guard
        (line 4724: status not in ('failed', 'cancelled', 'paused')) rather
        than the retry loop's status check (line 4581-4597).

        The retry loop checks status BEFORE the transient-error break
        decision. When the first call fails with resume_session_failed (not a
        transient API error), the loop breaks immediately. If the workflow
        status flips to 'failed' between the retry-loop break and the recovery
        block, the recovery block's own guard must suppress the retry
        (#2035 follow-up, Nit 3)."""
        wf = self._make_wf(status="planning")
        orch, mock_repo = self._make_orchestrator_with_mock_runner(wf)

        orch._runner.run_agent_task.return_value = _make_resume_failure_result(
            "resume_session_failed"
        )

        # self.workflow is a @property that calls repo.get_workflow on every
        # access. In this scenario get_workflow is called exactly twice:
        #   call #1: retry loop status check (line 4581) → 'planning' (no abort)
        #   call #2: recovery block guard (line 4724) → 'failed' (suppress retry)
        call_count = [0]

        def get_workflow_side_effect(_wf_id):
            call_count[0] += 1
            if call_count[0] == 1:
                return {**wf, "status": "planning"}
            return {**wf, "status": "failed"}

        mock_repo.get_workflow.side_effect = get_workflow_side_effect

        self._run_default(orch, wf)

        # Only one call — recovery block guard suppressed the retry
        assert orch._runner.run_agent_task.call_count == 1
        orch._runner.session_manager.update_session_fields.assert_not_called()
        # Confirm get_workflow was called exactly twice (retry-loop check +
        # recovery-block guard), verifying we exercised the recovery block's
        # own guard rather than the retry loop's status check.
        assert call_count[0] == 2
