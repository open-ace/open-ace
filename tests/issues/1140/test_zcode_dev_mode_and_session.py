"""Tests for ZCode dev-mode fix, session-line update, and dev timeout.

Three blocking issues from workflow #830 (id=525):

P0-1: ZCode --mode edit/build stalls on tool-approval-request in autonomous
      mode (no human to approve). Fix: force yolo mode for all zcode sessions.

P0-2: main_session_id not updated after a resume fallback. When planning
      retry resumes a dead session, the agent falls back to session/create
      and returns a new id, but the old condition `if not resume` prevented
      updating the stored id. Dev then resumed the stale dead id forever.

P1-1: Dev phase had no explicit timeout (task_timeout NULL). A stalled agent
      would hang indefinitely. Fix: pass DEFAULT_TASK_TIMEOUT to dev _run_agent.
"""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

_REMOTE_AGENT_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "remote-agent")
)


# ── P0-1: ZCode forces yolo mode in autonomous ────────────────────────────


def test_zcode_adapter_maps_yolo_correctly():
    """The yolo permission_mode must map to zcode --mode yolo."""
    if _REMOTE_AGENT_DIR not in sys.path:
        sys.path.insert(0, _REMOTE_AGENT_DIR)
    from cli_adapters.zcode import ZCodeAdapter

    adapter = ZCodeAdapter()
    args = adapter.build_start_args(
        "test-sid",
        "/tmp/proj",
        "glm-5.2",
        permission_mode="yolo",
    )
    assert "--mode" in args
    idx = args.index("--mode")
    assert args[idx + 1] == "yolo"


def test_zcode_run_zcode_appserver_forces_yolo():
    """_run_zcode_appserver must pass permission_mode='yolo' to build_start_args,
    regardless of the workflow's permission_mode (auto-edit/plan/etc).

    This prevents tool-approval-request stalls in autonomous mode.
    """
    from app.modules.workspace.autonomous.agent_runner import AutonomousAgentRunner

    runner = AutonomousAgentRunner(
        session_manager=MagicMock(),
        on_pid_registered=MagicMock(),
        on_pid_cleared=MagicMock(),
    )

    captured_mode = {}

    def fake_build_start_args(sid, path, model, permission_mode=None, **kw):
        captured_mode["mode"] = permission_mode
        return ["node", "/fake/engine.cjs", "app-server", "--cwd", path, "--mode", permission_mode]

    mock_adapter = MagicMock()
    mock_adapter.build_start_args = fake_build_start_args

    mock_zc_cls = MagicMock()
    mock_zc_instance = MagicMock()
    mock_zc_instance.start.return_value = True
    mock_zc_instance._cli_session_id = "sess_test"
    mock_zc_instance.send_message.return_value = True
    mock_zc_instance.wait_turn.return_value = True
    mock_zc_instance.stop = MagicMock()
    mock_zc_cls.return_value = mock_zc_instance

    mock_proc = MagicMock(returncode=None, pid=12345)
    mock_proc.stdin = MagicMock()
    mock_proc.stdout = MagicMock()
    mock_proc.stderr = MagicMock()

    with patch(
        "app.modules.workspace.autonomous.agent_runner.subprocess.Popen", return_value=mock_proc
    ):
        with patch("cli_adapters.get_adapter", return_value=mock_adapter):
            with patch("zcode_app_server.ZCodeAppServerSession", mock_zc_cls):
                runner._run_zcode_appserver(
                    session_id="test-yolo",
                    cli_tool="zcode",
                    model="glm-5.2",
                    project_path="/tmp/proj",
                    prompt="do something",
                    permission_mode="auto-edit",  # workflow says auto-edit
                    timeout=60,
                    workflow_id="wf-1",
                    user_id=1,
                    workspace_type="local",
                )

    # The adapter must have received yolo, NOT auto-edit
    assert (
        captured_mode["mode"] == "yolo"
    ), f"Expected yolo (no approval prompts), got {captured_mode['mode']}"


# ── P0-2: Session line always updates on success ──────────────────────────


def test_session_line_updates_even_on_resume():
    """When _run_agent succeeds via resume, the session line field must be
    updated with result.session_id — even if resume=True.

    This covers the case where resume fails silently (dead session) and the
    agent falls back to a fresh session/create. Without this update, subsequent
    milestones resume the stale dead id forever (#525).
    """
    from app.modules.workspace.autonomous.orchestrator import (
        SESSION_LINE_FIELDS,
        AutonomousOrchestrator,
    )

    # Verify the field mapping exists
    assert SESSION_LINE_FIELDS["main"] == "main_session_id"

    # We can't easily unit-test _run_agent in isolation (it's deeply coupled
    # to the orchestrator's DB/session state), but we can verify the code
    # path by checking that the update condition no longer has `not resume`.
    # This is a static assertion on the source to prevent regression.
    import inspect

    source = inspect.getsource(AutonomousOrchestrator._run_agent)
    # The old buggy condition was: `if field and not resume and not ...`
    # The fix removes the `not resume` guard so resume also updates.
    assert "not resume" not in source, (
        "_run_agent must not skip session line update on resume — "
        "see #525 where dev resumed a dead planning session forever"
    )


# ── P1-1: Dev phase has explicit timeout ──────────────────────────────────


def test_default_task_timeout_is_imported_in_orchestrator():
    """orchestrator.py must import DEFAULT_TASK_TIMEOUT from agent_runner."""
    from app.modules.workspace.autonomous import orchestrator

    assert hasattr(orchestrator, "DEFAULT_TASK_TIMEOUT")
    assert orchestrator.DEFAULT_TASK_TIMEOUT > 0


def test_dev_phase_passes_timeout():
    """The dev_started _run_agent call must include an explicit timeout=
    parameter, not rely on the task_timeout DB column (which may be NULL)."""
    import inspect

    from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

    source = inspect.getsource(AutonomousOrchestrator._run_development_agent)
    assert "timeout=" in source, (
        "Dev phase _run_agent must have an explicit timeout to prevent "
        "infinite hang when agent stalls (#525)"
    )
