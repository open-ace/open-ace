"""Tests for transient-API-error detection, retry, and fail semantics (#1001).

Covers:
  - ``_is_transient_api_error`` detects 429 / 5xx / 529 overload and does not
    false-positive on a normal plan.
  - ``_run_agent`` retries a transient error then succeeds.
  - ``_run_agent`` does NOT retry a normal response.
  - After retries are exhausted, a transient-error body returned as a
    "successful" response (0 tokens) is synthesized into a failure
    (success=False, response_text cleared, error set) so callers mark the
    milestone failed and don't store the error body as plan/review content.
"""

from unittest.mock import MagicMock, patch

import pytest

from app.modules.workspace.autonomous import orchestrator as orch_module
from app.modules.workspace.autonomous.models import AgentTaskResult
from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator


def _make_workflow(**overrides):
    base = {
        "workflow_id": "test-wf-1001",
        "title": "Test 1001",
        "cli_tool": "claude-code",
        "model": "",
        "worktree_path": "/tmp/wf1001",
        "project_path": "/tmp/wf1001",
        "workspace_type": "local",
        "remote_machine_id": "",
        "permission_mode": "auto-edit",
    }
    base.update(overrides)
    return base


def _make_orchestrator(wf):
    with (
        patch("app.modules.workspace.autonomous.orchestrator.Database"),
        patch(
            "app.modules.workspace.autonomous.orchestrator.AutonomousWorkflowRepository"
        ) as mock_repo_cls,
    ):
        mock_repo = MagicMock()
        mock_repo.get_workflow.return_value = wf
        mock_repo_cls.return_value = mock_repo
        o = AutonomousOrchestrator(wf["workflow_id"])
        o.repo = mock_repo
    o.emitter = MagicMock()
    # Stub _run_agent's collaborators so we test IT, not its dependencies.
    o._resolve_session_line = MagicMock(return_value=("sess-track", None, False))
    o._snapshot_repo_context = MagicMock(
        return_value={
            "context": {
                "repo_path": wf["worktree_path"],
                "project_path": wf["project_path"],
                "strategy": "worktree",
                "expected_branch": "auto-dev/test",
            },
            "effective": {
                "repo_path": wf["worktree_path"],
                "top_level": wf["worktree_path"],
                "git_dir": f"{wf['worktree_path']}/.git",
                "git_identity": "1:1",
                "common_dir": f"{wf['worktree_path']}/.git",
                "common_identity": "1:1",
                "origin": "git@github.com:open-ace/open-ace.git",
            },
        }
    )
    o._validate_repo_context_after_run = MagicMock(return_value="")
    o._get_gh = MagicMock()
    o._link_session_to_current_milestone = MagicMock()
    o._write_phase_usage = MagicMock()
    o._update_workflow = MagicMock()
    o._emit = MagicMock()
    o._accumulate_tokens = MagicMock()
    o._runner = MagicMock()
    o._runner._uses_sidebar_session_source = MagicMock(return_value=False)
    return o


# ── _is_transient_api_error ──────────────────────────────────────────────


class TestIsTransientApiError:
    def test_detects_529_overload(self):
        body = "API Error: 529 [1305][The service may be temporarily overloaded, please try again later]"
        assert AutonomousOrchestrator._is_transient_api_error(body)

    def test_detects_429_rate_limit(self):
        assert AutonomousOrchestrator._is_transient_api_error("429 Too Many Requests")

    def test_detects_503_service_unavailable(self):
        assert AutonomousOrchestrator._is_transient_api_error("API Error: 503 Service Unavailable")

    def test_detects_overloaded_phrase(self):
        assert AutonomousOrchestrator._is_transient_api_error(
            "The service may be temporarily overloaded"
        )

    def test_detects_bad_gateway(self):
        assert AutonomousOrchestrator._is_transient_api_error("502 Bad Gateway")

    def test_no_false_positive_on_normal_plan(self):
        plan = "## 实现方案\n构建一个用户登录系统，包含 JWT 认证、密码重置和审计日志功能。"
        assert not AutonomousOrchestrator._is_transient_api_error(plan)

    def test_4xx_permanent_errors_not_transient(self):
        # 400/401/403/404/422 are permanent client errors — must NOT retry.
        for body in [
            "API Error: 400 Bad Request",
            "API Error: 401 Unauthorized",
            "API Error: 403 Forbidden",
            "API Error: 404 Not Found",
            "API Error: 422 Unprocessable Entity",
        ]:
            assert not AutonomousOrchestrator._is_transient_api_error(body), body

    def test_empty_and_none(self):
        assert not AutonomousOrchestrator._is_transient_api_error("")
        assert not AutonomousOrchestrator._is_transient_api_error(None)


# ── _run_agent retry + fail semantics ────────────────────────────────────


class TestRunAgentApiErrorRetry:
    def test_shutdown_before_dispatch_does_not_launch_agent(self):
        o = _make_orchestrator(_make_workflow())
        o._shutdown_requested.set()

        with pytest.raises(orch_module.WorkflowPaused, match="Service shutdown"):
            o._run_agent(wf=_make_workflow(), milestone_id="ms-shutdown", prompt="x")

        o._runner.run_agent_task.assert_not_called()
        o.repo.update_milestone.assert_called_with(
            "ms-shutdown",
            {
                "status": "cancelled",
                "error_message": (
                    "Service shutdown interrupted this attempt; it will retry automatically"
                ),
            },
        )

    def test_shutdown_cancels_attempt_without_returning_failure(self):
        o = _make_orchestrator(_make_workflow())
        result = AgentTaskResult(
            session_id="sess-track",
            tracking_session_id="sess-track",
            response_text="partial result",
            total_tokens=500,
            success=False,
            error="stopped",
        )

        def stop_during_run(**_kwargs):
            o._shutdown_requested.set()
            return result

        o._runner.run_agent_task = MagicMock(side_effect=stop_during_run)

        with pytest.raises(orch_module.WorkflowPaused, match="Service shutdown"):
            o._run_agent(wf=_make_workflow(), milestone_id="ms-shutdown", prompt="x")

        o.repo.update_milestone.assert_called_with(
            "ms-shutdown",
            {
                "status": "cancelled",
                "error_message": (
                    "Service shutdown interrupted this attempt; it will retry automatically"
                ),
            },
        )
        o._write_phase_usage.assert_called_once()
        assert o._current_session_id == "sess-track"

    def test_no_retry_on_normal_response(self, monkeypatch):
        monkeypatch.setattr("time.sleep", lambda *a, **k: None)
        o = _make_orchestrator(_make_workflow())
        ok = AgentTaskResult(
            session_id="s1", response_text="## Plan\nreal plan", total_tokens=500, success=True
        )
        o._runner.run_agent_task = MagicMock(return_value=ok)

        result = o._run_agent(wf=_make_workflow(), prompt="x")

        assert o._runner.run_agent_task.call_count == 1
        assert result.success is True
        assert result.response_text == "## Plan\nreal plan"

    def test_no_retry_on_token_bearing_plan_that_mentions_rate_limit(self, monkeypatch):
        """The exact issue-1891 false positive must remain a normal success."""
        monkeypatch.setattr("time.sleep", lambda *a, **k: None)
        o = _make_orchestrator(_make_workflow())
        plan = AgentTaskResult(
            session_id="sess-track",
            tracking_session_id="sess-track",
            response_text=(
                "## Security plan\n"
                "#### Step 4.2: Rate Limit\n"
                "| R6 | Rate limit | P2 | Prevent abuse |"
            ),
            total_tokens=1200,
            success=True,
        )
        o._runner.run_agent_task = MagicMock(return_value=plan)

        result = o._run_agent(wf=_make_workflow(), session_line="main", prompt="x")

        assert o._runner.run_agent_task.call_count == 1
        assert result.success is True
        assert "Rate Limit" in result.response_text

    def test_retries_transient_error_then_succeeds(self, monkeypatch):
        # Simulate time.sleep as a no-op so the backoff loop is instant.
        monkeypatch.setattr("time.sleep", lambda *a, **k: None)
        o = _make_orchestrator(_make_workflow())
        err = AgentTaskResult(
            session_id="s1",
            response_text="API Error: 529 [overloaded]",
            total_tokens=0,
            success=True,
        )
        ok = AgentTaskResult(
            session_id="s2", response_text="## Plan\nrecovered plan", total_tokens=500, success=True
        )
        o._runner.run_agent_task = MagicMock(side_effect=[err, ok])

        result = o._run_agent(wf=_make_workflow(), prompt="x")

        assert o._runner.run_agent_task.call_count == 2  # retried once
        first_call, second_call = o._runner.run_agent_task.call_args_list
        assert first_call.kwargs["session_id"] == "sess-track"
        assert second_call.kwargs["session_id"] != first_call.kwargs["session_id"]
        assert second_call.kwargs["resume"] is False
        assert second_call.kwargs["resume_session_id"] is None
        assert o._current_session_id == "s2"
        assert result.success is True
        assert result.response_text == "## Plan\nrecovered plan"

    @pytest.mark.parametrize(
        ("session_line", "workflow_field"),
        [
            ("main", "main_session_id"),
            ("review", "review_session_id"),
            ("test", "test_session_id"),
        ],
    )
    def test_named_line_retry_reuses_tracking_and_provider_session(
        self, monkeypatch, session_line, workflow_field
    ):
        """Retry usage values are per-run deltas, not resumed-session totals."""
        monkeypatch.setattr("time.sleep", lambda *a, **k: None)
        o = _make_orchestrator(_make_workflow())
        o._runner._uses_sidebar_session_source = MagicMock(return_value=True)
        err = AgentTaskResult(
            session_id="sess-track",
            tracking_session_id="sess-track",
            source_session_id="cli-main",
            error="API Error: 503 Service Unavailable",
            total_tokens=100,
            total_input_tokens=80,
            total_output_tokens=20,
            request_count=1,
            success=False,
        )
        ok = AgentTaskResult(
            session_id="sess-track",
            tracking_session_id="sess-track",
            source_session_id="cli-main",
            response_text="## Plan\nrecovered plan",
            total_tokens=200,
            total_input_tokens=150,
            total_output_tokens=50,
            request_count=2,
            success=True,
        )
        o._runner.run_agent_task = MagicMock(side_effect=[err, ok])

        result = o._run_agent(
            wf=_make_workflow(),
            session_line=session_line,
            milestone_id="ms-plan",
            prompt="x",
        )

        first_call, second_call = o._runner.run_agent_task.call_args_list
        assert first_call.kwargs["session_id"] == "sess-track"
        assert second_call.kwargs["session_id"] == "sess-track"
        assert second_call.kwargs["resume"] is True
        assert second_call.kwargs["resume_session_id"] == "cli-main"
        assert all(
            call.args[0] == "sess-track"
            for call in o._link_session_to_current_milestone.call_args_list
        )
        o._update_workflow.assert_called_with({workflow_field: "sess-track"})
        o._write_phase_usage.assert_called_once_with(
            "ms-plan",
            ok,
            {
                "total_tokens": 100,
                "total_input_tokens": 80,
                "total_output_tokens": 20,
                "request_count": 1,
            },
        )
        assert result is ok

    def test_synthesizes_failure_after_exhaustion(self, monkeypatch):
        # Retry loop disabled (0 timeout) + always-error runner -> the post-loop
        # synthesis must turn the 529 body into a failure.
        monkeypatch.setattr(orch_module, "API_RETRY_TOTAL_TIMEOUT", 0)
        monkeypatch.setattr("time.sleep", lambda *a, **k: None)
        o = _make_orchestrator(_make_workflow())
        err = AgentTaskResult(
            session_id="s1",
            response_text="API Error: 529 [1305][The service may be temporarily overloaded]",
            total_tokens=0,
            success=True,  # runner didn't flag it as an error
            error=None,
        )
        o._runner.run_agent_task = MagicMock(return_value=err)

        result = o._run_agent(wf=_make_workflow(), prompt="x")

        # synthesized failure: callers mark milestone failed, don't store the body
        assert result.success is False
        assert result.response_text == ""  # not stored as plan/review content
        assert result.error and "529" in result.error

    def test_does_not_synthesize_failure_for_real_plan_with_tokens(self, monkeypatch):
        # A legitimate plan that happens to mention an error phrase carries
        # tokens, so the tokens==0 gate keeps it from being flagged.
        monkeypatch.setattr(orch_module, "API_RETRY_TOTAL_TIMEOUT", 0)
        monkeypatch.setattr("time.sleep", lambda *a, **k: None)
        o = _make_orchestrator(_make_workflow())
        plan = AgentTaskResult(
            session_id="s1",
            response_text="## Plan\nAdd retry for API Error: 529 handling in the client.",
            total_tokens=1200,
            success=True,
        )
        o._runner.run_agent_task = MagicMock(return_value=plan)

        result = o._run_agent(wf=_make_workflow(), prompt="x")

        assert result.success is True  # not synthesized away
        assert "## Plan" in result.response_text


class TestRunAgentAbortOnFailedStatus:
    """Regression: a workflow marked failed/cancelled mid-retry must abort
    instead of spawning agents for the full 30-min window (#1036)."""

    def test_aborts_retry_when_workflow_marked_failed(self, monkeypatch):
        # time.sleep is a no-op so backoff is instant; retry window is large so
        # the loop WOULD keep going if not for the status check.
        monkeypatch.setattr("time.sleep", lambda *a, **k: None)
        monkeypatch.setattr(orch_module, "API_RETRY_TOTAL_TIMEOUT", 1800)
        wf_failed = _make_workflow(status="failed")
        o = _make_orchestrator(wf_failed)
        # First (and only) agent call returns a 529 transient-error body.
        err = AgentTaskResult(
            session_id="s1",
            response_text="API Error: 529 [1305][The service may be temporarily overloaded]",
            total_tokens=0,
            success=True,
        )
        o._runner.run_agent_task = MagicMock(return_value=err)

        result = o._run_agent(wf=wf_failed, prompt="x")

        # No re-spawn: run_agent_task called exactly once (the initial call).
        assert o._runner.run_agent_task.call_count == 1
        # Usage was still attributed to the milestone.
        o._write_phase_usage.assert_called_once()
        # No api_error_retry event emitted (status check bails before that).
        emitted_types = [c.args[0] for c in o._emit.call_args_list]
        assert "api_error_retry" not in emitted_types
        # The 529 body was synthesized to a failure even on early exit (#1036).
        assert result.success is False
        assert result.response_text == ""
        assert result.error and "529" in result.error

    def test_aborts_retry_when_workflow_marked_cancelled(self, monkeypatch):
        monkeypatch.setattr("time.sleep", lambda *a, **k: None)
        monkeypatch.setattr(orch_module, "API_RETRY_TOTAL_TIMEOUT", 1800)
        wf_cancelled = _make_workflow(status="cancelled")
        o = _make_orchestrator(wf_cancelled)
        err = AgentTaskResult(
            session_id="s1",
            response_text="API Error: 503 Service Unavailable",
            total_tokens=0,
            success=True,
        )
        o._runner.run_agent_task = MagicMock(return_value=err)

        result = o._run_agent(wf=wf_cancelled, prompt="x")

        assert o._runner.run_agent_task.call_count == 1  # no re-spawn
        assert result.success is False  # synthesized on early exit
        assert result.response_text == ""
