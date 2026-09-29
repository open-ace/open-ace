"""#3321: codex's minted thread_id must round-trip capture → persist → resolve.

The adapter-level argv is covered by ``test_codex_resume_3321.py``. These tests
pin the orchestrator/runner carry: extending the claude-only
``_resolve_session_line`` mapping branch to codex, and persisting the captured
``thread_id`` into ``agent_sessions.cli_session_id`` from ``_run_single_shot``.
"""

from __future__ import annotations

import json
import uuid
from unittest.mock import MagicMock, patch

import pytest

from app.modules.workspace.autonomous.agent_runner import AutonomousAgentRunner

pytestmark = [pytest.mark.regression, pytest.mark.issue(3321)]


def _patch_codex_adapter():
    """Patch cli_adapters.get_adapter so _run_single_shot finds a codex executable."""
    adapter = MagicMock()
    adapter.get_executable_name.return_value = "codex"
    adapter.build_single_shot_args.return_value = ["codex", "exec", "--json", "prompt"]
    mod = MagicMock()
    mod.get_adapter.return_value = adapter
    return adapter, (
        patch.dict("sys.modules", {"cli_adapters": mod}),
        patch("shutil.which", return_value="/usr/bin/codex"),
    )


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


def test_resolve_session_line_maps_codex_cli_session_id():
    """A codex line resumes the captured thread_id, not the tracking id.

    Fails before #3321: the mapping branch is gated ``cli_tool == "claude-code"``,
    so codex falls through to ``(existing, existing, True)`` and resumes the
    tracking id — which names no rollout, so ``codex exec resume`` cold-starts.
    """
    from app.modules.workspace.session_manager import AgentSession

    db_state = {"main_session_id": "wf-main-track", "cli_tool": "codex"}
    orch = _make_orchestrator(db_state)
    orch._runner.session_manager.get_session.return_value = AgentSession(
        session_id="wf-main-track",
        cli_session_id="01a062a7-6e97-7c20-a694-2ebd922ea386",
    )

    sid, resume_sid, resume = orch._resolve_session_line(dict(db_state), "main")

    assert resume is True
    assert sid == "wf-main-track", "tracking id must stay stable (HOME must not rotate)"
    assert resume_sid == "01a062a7-6e97-7c20-a694-2ebd922ea386"


def test_resolve_session_line_codex_first_turn_starts_fresh():
    """Turn 1 codex has no captured id yet → cold start, not a fake resume.

    The tracking id is set from workflow creation, but ``cli_session_id`` is
    empty until turn 1 records it. Resuming the tracking id would hit
    "no rollout found"; return resume=False so turn 1 runs cold.
    """
    from app.modules.workspace.session_manager import AgentSession

    db_state = {"main_session_id": "wf-main-track", "cli_tool": "codex"}
    orch = _make_orchestrator(db_state)
    orch._runner.session_manager.get_session.return_value = AgentSession(
        session_id="wf-main-track", cli_session_id=""
    )

    sid, resume_sid, resume = orch._resolve_session_line(dict(db_state), "main")

    assert resume is False
    assert resume_sid is None
    assert sid == "wf-main-track"


def test_run_single_shot_threads_resume_into_adapter():
    """A resume single-shot call passes resume/resume_session_id to the adapter.

    Fails before #3321: ``_run_single_shot`` has no ``resume`` parameter, so the
    dispatch could not carry it even if it wanted to.
    """
    runner = AutonomousAgentRunner()
    proc = MagicMock(returncode=0, stdout="", stderr="")
    adapter, (mod_patch, which_patch) = _patch_codex_adapter()

    with mod_patch, which_patch, patch("subprocess.run", return_value=proc):
        runner._run_single_shot(
            session_id="s1",
            cli_tool="codex",
            model="m",
            project_path="/tmp/p",
            prompt="do it",
            timeout=5,
            workflow_id="wf1",
            resume=True,
            resume_session_id="01a062a7-tid",
        )

    _, kwargs = adapter.build_single_shot_args.call_args
    assert kwargs.get("resume") is True, adapter.build_single_shot_args.call_args
    assert kwargs.get("resume_session_id") == "01a062a7-tid"


def test_run_single_shot_captures_and_persists_codex_thread_id():
    """codex's first ``thread.started`` id is written to cli_session_id.

    This is the capture+persist half of the carry. Without it, the next
    milestone's ``_resolve_session_line`` reads an empty column and cold-starts.
    """
    runner = AutonomousAgentRunner()
    runner.session_manager = MagicMock()
    stdout = "\n".join(
        [
            json.dumps({"type": "thread.started", "thread_id": "01a062a7-6e97-7c20-real"}),
            json.dumps({"type": "turn.started"}),
            json.dumps(
                {"type": "assistant", "message": {"content": [{"type": "text", "text": "done"}]}}
            ),
        ]
    )
    proc = MagicMock(returncode=0, stdout=stdout, stderr="")
    _adapter, (mod_patch, which_patch) = _patch_codex_adapter()

    with mod_patch, which_patch, patch("subprocess.run", return_value=proc):
        runner._run_single_shot(
            session_id="s1",
            cli_tool="codex",
            model="m",
            project_path="/tmp/p",
            prompt="do it",
            timeout=5,
            workflow_id="wf1",
        )

    runner.session_manager.update_session_fields.assert_called_once()
    call = runner.session_manager.update_session_fields.call_args
    assert call.args[0] == "s1", call
    assert call.args[1].get("cli_session_id") == "01a062a7-6e97-7c20-real", call


def test_run_single_shot_no_thread_id_persists_nothing():
    """A run with no thread.started (e.g. openclaw) records no cli_session_id."""
    runner = AutonomousAgentRunner()
    runner.session_manager = MagicMock()
    stdout = json.dumps(
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "hi"}]}}
    )
    proc = MagicMock(returncode=0, stdout=stdout, stderr="")
    _adapter, (mod_patch, which_patch) = _patch_codex_adapter()

    with mod_patch, which_patch, patch("subprocess.run", return_value=proc):
        runner._run_single_shot(
            session_id="s1",
            cli_tool="openclaw",
            model="m",
            project_path="/tmp/p",
            prompt="do it",
            timeout=5,
            workflow_id="wf1",
        )

    runner.session_manager.update_session_fields.assert_not_called()


def test_run_local_dispatch_forwards_resume_to_single_shot():
    """`_run_local` must hand resume/resume_session_id to `_run_single_shot`.

    This is the dispatch line the issue warns about: a codex run whose dispatch
    drops resume would silently cold-start even with every other piece working.
    """
    runner = AutonomousAgentRunner()
    sentinel = MagicMock(name="AgentTaskResult")

    with (
        patch.object(runner, "_run_single_shot", return_value=sentinel) as mock_ss,
        patch.object(runner, "_ensure_project_dir"),
    ):
        out = runner._run_local(
            session_id="s1",
            cli_tool="codex",
            model="m",
            project_path="/tmp/p",
            prompt="do it",
            permission_mode="auto",
            timeout=5,
            workflow_id="wf1",
            user_id=None,
            workspace_type="local",
            resume=True,
            resume_session_id="01a062a7-tid",
            tenant_id=0,  # skip _resolve_tenant_for_isolation
        )

    assert out is sentinel
    mock_ss.assert_called_once()
    kwargs = mock_ss.call_args.kwargs
    assert kwargs.get("resume") is True, mock_ss.call_args
    assert kwargs.get("resume_session_id") == "01a062a7-tid"


class _FakeSessionStore:
    """Minimal session_manager that actually round-trips cli_session_id."""

    def __init__(self):
        self.rows: dict[str, dict] = {}

    def update_session_fields(self, session_id, fields, require_tenant=True):
        self.rows.setdefault(session_id, {}).update(fields)

    def get_session(self, session_id):
        from app.modules.workspace.session_manager import AgentSession

        row = self.rows.get(session_id)
        if row is None:
            return {}
        return AgentSession(session_id=session_id, cli_session_id=row.get("cli_session_id", ""))


def test_two_turn_codex_resume_round_trip():
    """AC1: turn 1 captures+persists the thread_id; turn 2 resolves it and the
    real codex adapter builds ``codex exec resume <thread_id>``.

    Fails if the capture/persist OR the ``_resolve_session_line`` extension is
    removed — the two halves of the carry.
    """
    from cli_adapters import ADAPTERS

    store = _FakeSessionStore()

    # --- Turn 1: codex runs cold, mints a thread id; the runner persists it ---
    runner = AutonomousAgentRunner()
    runner.session_manager = store
    stdout = "\n".join(
        [
            json.dumps({"type": "thread.started", "thread_id": "01a0-turn1"}),
            json.dumps(
                {"type": "assistant", "message": {"content": [{"type": "text", "text": "ok"}]}}
            ),
        ]
    )
    proc = MagicMock(returncode=0, stdout=stdout, stderr="")
    _adapter, (mod_patch, which_patch) = _patch_codex_adapter()
    with mod_patch, which_patch, patch("subprocess.run", return_value=proc):
        runner._run_single_shot(
            session_id="wf-main-track",
            cli_tool="codex",
            model="o3",
            project_path="/workspace",
            prompt="start",
            timeout=5,
            workflow_id="wf1",
        )
    assert store.rows["wf-main-track"]["cli_session_id"] == "01a0-turn1"

    # --- Turn 2: the orchestrator resolves the line to the persisted id ---
    orch = _make_orchestrator({"main_session_id": "wf-main-track", "cli_tool": "codex"})
    orch._runner.session_manager = store
    sid, resume_sid, resume = orch._resolve_session_line(
        {"main_session_id": "wf-main-track", "cli_tool": "codex"}, "main"
    )
    assert resume is True
    assert sid == "wf-main-track"
    assert resume_sid == "01a0-turn1"

    # --- The real codex adapter turns that into a resume invocation ---
    argv = ADAPTERS["codex"]().build_single_shot_args(
        "continue", "/workspace", "o3", resume=resume, resume_session_id=resume_sid
    )
    assert argv[:3] == ["codex", "exec", "resume"]
    assert "01a0-turn1" in argv
