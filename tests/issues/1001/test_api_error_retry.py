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
        assert result.success is True
        assert result.response_text == "## Plan\nrecovered plan"

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
