"""#3319: carry the qwen-code-cli transcript across ephemeral sandboxes.

qwen shares claude's stream-json path and emits its ``session_id`` on stdout
(verified against qwen 0.21.5), so the carry is the claude pattern: capture the
id from the stream, then export/import a fixed
``.qwen/projects/-workspace/chats/<id>.jsonl``. These tests pin each leg —
tool-aware provider path, stream capture, the ``_plan_agent_state`` refusal
removal, and the ``_resolve_session_line`` extension.
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest

pytestmark = [pytest.mark.regression, pytest.mark.issue(3319)]


# ── provider path is tool-aware ───────────────────────────────────────


def test_agent_state_path_qwen_uses_chats_subdir():
    """qwen's transcript path is ``.qwen/projects/-workspace/chats/<id>.jsonl``.

    Fails before #3319: ``_agent_state_path`` hardcodes the claude
    ``.claude/projects/-workspace`` dir for every tool.
    """
    from app.modules.workspace.autonomous.sandbox.opensandbox.provider import _agent_state_path

    path = _agent_state_path("1dce24ac-4388-45fa-b663-3eebf55f4b73", cli_tool="qwen-code-cli")

    assert (
        path
        == "/home/agent/.qwen/projects/-workspace/chats/1dce24ac-4388-45fa-b663-3eebf55f4b73.jsonl"
    )


def test_agent_state_path_claude_default_unchanged():
    """claude-code keeps its fixed .claude path (default arg, no behaviour change)."""
    from app.modules.workspace.autonomous.sandbox.opensandbox.provider import _agent_state_path

    assert _agent_state_path("abc123") == "/home/agent/.claude/projects/-workspace/abc123.jsonl"
    assert (
        _agent_state_path("abc123", cli_tool="claude-code")
        == "/home/agent/.claude/projects/-workspace/abc123.jsonl"
    )


def test_agent_state_path_hostile_id_refused_for_qwen_too():
    """The traversal guard applies to qwen exactly as to claude."""
    from app.modules.workspace.autonomous.sandbox.opensandbox.provider import _agent_state_path

    assert _agent_state_path("../../etc/passwd", cli_tool="qwen-code-cli") is None
    assert _agent_state_path("bad\nid", cli_tool="qwen-code-cli") is None
    assert _agent_state_path("", cli_tool="qwen-code-cli") is None


# ── stream capture (qwen emits session_id on stdout) ──────────────────


def _qwen_session():
    from app.modules.workspace.autonomous.agent_runner import _LocalSession

    return _LocalSession(
        session_id="wf-main-track",
        process=None,
        cli_tool="qwen-code-cli",
        workspace_type="remote",
        project_path="/workspace",
        encoded_project_path="-workspace",
        workflow_id="wf1",
    )


def test_capture_cli_session_id_captures_for_qwen_stream():
    """qwen's stream ``session_id`` lands in ``session.cli_session_id``.

    Fails before #3319: ``_capture_cli_session_id`` early-returns for any tool
    that is not claude-code local, so qwen's id was never captured.
    """
    from app.modules.workspace.autonomous.agent_runner import AutonomousAgentRunner

    runner = AutonomousAgentRunner()
    session = _qwen_session()
    parsed = {
        "type": "system",
        "subtype": "init",
        "session_id": "1dce24ac-4388-45fa-b663-3eebf55f4b73",
    }

    runner._capture_cli_session_id(session, parsed, "system")

    assert session.cli_session_id == "1dce24ac-4388-45fa-b663-3eebf55f4b73"


def test_capture_cli_session_id_ignores_non_stream_tool():
    """A tool that is neither claude nor qwen still captures nothing here."""
    from app.modules.workspace.autonomous.agent_runner import AutonomousAgentRunner, _LocalSession

    runner = AutonomousAgentRunner()
    session = _LocalSession(session_id="s1", process=None, cli_tool="codex", workspace_type="local")
    runner._capture_cli_session_id(session, {"session_id": "x"}, "system")
    assert session.cli_session_id == ""


# ── the refusal is lifted for qwen, kept for genuinely uncarryable tools ──


class _Carried:
    from app.modules.workspace.autonomous.sandbox.provider import AGENT_STATE_CARRIED

    agent_state_persistence = AGENT_STATE_CARRIED


def _runner_with_store(tmp_path):
    from app.modules.workspace.autonomous.agent_runner import AutonomousAgentRunner
    from app.modules.workspace.autonomous.sandbox.agent_state_store import AgentStateStore

    runner = AutonomousAgentRunner.__new__(AutonomousAgentRunner)
    runner._agent_state_store = AgentStateStore(root=str(tmp_path))
    return runner


def test_plan_agent_state_no_longer_refuses_qwen(tmp_path):
    """A carried provider with stored qwen state plans to resume, not refuse.

    Fails before #3319: ``_plan_agent_state`` refuses every non-claude tool
    under AGENT_STATE_CARRIED.
    """
    runner = _runner_with_store(tmp_path)
    runner._agent_state_store.put("wf-1", "sid-1", b"QWEN TRANSCRIPT\n")

    plan = runner._plan_agent_state(
        _Carried(),
        workflow_id="wf-1",
        tracking_session_id="sid-1",
        resume=True,
        cli_tool="qwen-code-cli",
    )

    assert plan.refuse is False
    assert plan.resume is True
    assert plan.blob == b"QWEN TRANSCRIPT\n"


def test_plan_agent_state_still_refuses_an_uncarryable_tool(tmp_path):
    """A tool the sandbox cannot address is still refused, not silently cold."""
    runner = _runner_with_store(tmp_path)
    runner._agent_state_store.put("wf-1", "sid-1", b"X\n")

    plan = runner._plan_agent_state(
        _Carried(),
        workflow_id="wf-1",
        tracking_session_id="sid-1",
        resume=True,
        cli_tool="some-future-tool",
    )

    assert plan.refuse is True
    assert plan.reason_code == "agent_state_unavailable"


# ── resolve maps qwen's tracking id to the captured cli_session_id ────


def _make_orchestrator(db_state):
    from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

    repo = MagicMock()
    repo.get_workflow = MagicMock(return_value=dict(db_state))
    orch = AutonomousOrchestrator.__new__(AutonomousOrchestrator)
    orch._workflow_id = str(uuid.uuid4())
    orch.repo = repo
    orch._session_lock = MagicMock()
    orch._current_session_id = None
    orch._runner = MagicMock()
    orch._runner.session_manager = MagicMock()
    return orch


def test_resolve_session_line_maps_qwen_cli_session_id():
    """A qwen line resumes the captured sessionId, not the tracking id."""
    from app.modules.workspace.session_manager import AgentSession

    db_state = {"main_session_id": "wf-main-track", "cli_tool": "qwen-code-cli"}
    orch = _make_orchestrator(db_state)
    orch._runner.session_manager.get_session.return_value = AgentSession(
        session_id="wf-main-track", cli_session_id="1dce24ac-real"
    )

    sid, resume_sid, resume = orch._resolve_session_line(dict(db_state), "main")

    assert resume is True
    assert sid == "wf-main-track"
    assert resume_sid == "1dce24ac-real"
