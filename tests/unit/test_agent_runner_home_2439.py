"""Issue #2439: AgentRunner must read Claude history from the per-task HOME.

The CLI runs under a per-attempt HOME (``/run/openace-agent-tasks/<task_id>/home``,
set in ``_build_env`` + the cross-user launcher — #2020 isolation), so its
session JSONL lives at ``<per-task home>/.claude/projects/...``. But
``_resolve_home_dir`` / ``_claude_projects_root`` resolved the SYSTEM account's
passwd home, so:

- ``_find_latest_claude_session_id`` (the mtime fallback when the SDK
  control_response is missed) scanned the wrong dir → empty/stale session id →
  wrong ``--resume`` target;
- ``_replay_usage_from_jsonl`` (the timeout path) read the wrong dir → silent
  OSError → usage stays 0/0 (#723-style zero-cost round).

Fix: thread ``task_id`` to the per-task home; fall back to the system home only
for legacy callers without a task_id.
"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.modules.workspace.autonomous.agent_runner import AutonomousAgentRunner, _LocalSession
from app.modules.workspace.autonomous.task_isolation import DEFAULT_TASK_ROOT, task_runtime_dirs

pytestmark = [pytest.mark.regression, pytest.mark.issue(2439)]


def test_resolve_home_dir_uses_per_task_home_when_task_id_set():
    home = AutonomousAgentRunner._resolve_home_dir("openace", task_id="wf-2439")
    assert home == Path(task_runtime_dirs("wf-2439")["home"])
    assert str(home).startswith(DEFAULT_TASK_ROOT)


def test_task_id_takes_precedence_over_system_account():
    # Even with a system_account, a task_id resolves to the per-task HOME (where
    # the CLI actually writes), not the passwd home.
    home = AutonomousAgentRunner._resolve_home_dir("root", task_id="abc-123")
    assert home == Path(task_runtime_dirs("abc-123")["home"])


def test_resolve_home_dir_falls_back_without_task_id():
    assert AutonomousAgentRunner._resolve_home_dir(None) == Path.home()


def test_claude_projects_root_uses_per_task_home():
    root = AutonomousAgentRunner._claude_projects_root("openace", task_id="wf-2439")
    assert root == Path(task_runtime_dirs("wf-2439")["home"]) / ".claude" / "projects"


def test_claude_projects_root_falls_back_without_task_id():
    expected = Path.home() / ".claude" / "projects"
    assert AutonomousAgentRunner._claude_projects_root(None) == expected


def test_local_session_carries_task_id_default_empty():
    assert _LocalSession(session_id="s1", process=None).task_id == ""
    assert _LocalSession(session_id="s2", process=None, task_id="wf-9").task_id == "wf-9"


def test_find_latest_session_reads_per_task_projects_root():
    """The mtime fallback must scan the per-task projects root, not the system home."""
    runner = AutonomousAgentRunner.__new__(AutonomousAgentRunner)
    captured = {}

    def fake_list(project_dir, system_account):
        captured["dir"] = project_dir
        return []

    runner._list_jsonl_files = MagicMock(side_effect=fake_list)
    runner._find_latest_claude_session_id(
        "encoded-proj", 0.0, system_account="openace", task_id="wf-2439"
    )
    assert captured["dir"] == (
        Path(task_runtime_dirs("wf-2439")["home"]) / ".claude" / "projects" / "encoded-proj"
    )


def test_replay_usage_reads_per_task_projects_root():
    """The timeout usage-replay must read the per-task JSONL path."""
    runner = AutonomousAgentRunner.__new__(AutonomousAgentRunner)
    captured = {}

    def fake_read(path, system_account):
        captured["path"] = path
        raise OSError("stop")  # _replay swallows OSError → returns

    runner._read_text_as_user = MagicMock(side_effect=fake_read)
    session = _LocalSession(
        session_id="s1",
        process=None,
        system_account="openace",
        encoded_project_path="encoded-proj",
        started_at_epoch=0.0,
        task_id="wf-2439",
    )
    runner._replay_usage_from_jsonl(session, "cli-sid")
    assert captured["path"] == (
        Path(task_runtime_dirs("wf-2439")["home"])
        / ".claude"
        / "projects"
        / "encoded-proj"
        / "cli-sid.jsonl"
    )
