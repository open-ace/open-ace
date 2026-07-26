"""``_wrap_agent_cmd`` threads ``--task-id`` to the launcher (Issue #2020).

The cross-user launcher keys per-task HOME/TMP/XDG/flock/cgroup off
``--task-id``. The runner must pass a sanitized task_id so the launcher and the
Python env builder resolve identical per-attempt paths.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest


def _guard_bin(monkeypatch, tmp_path):
    from app.modules.workspace.autonomous import agent_runner

    guard_dir = tmp_path / "agent-bin"
    guard_dir.mkdir()
    for name in agent_runner._AGENT_GUARD_EXECUTABLES:
        guard = guard_dir / name
        guard.write_text("#!/bin/sh\n", encoding="utf-8")
        guard.chmod(0o755)
    monkeypatch.setattr(agent_runner, "_OPENACE_AGENT_GUARD_BIN", str(guard_dir))
    return guard_dir


def _wrap(monkeypatch, tmp_path, task_id):
    from app.modules.workspace.autonomous import agent_runner

    _guard_bin(monkeypatch, tmp_path)
    env = {
        "PATH": f"{tmp_path / 'agent-bin'}:/usr/bin",
        "OPENACE_REAL_GIT": "/usr/bin/git",
    }
    with (
        patch.object(agent_runner.AutonomousAgentRunner, "_is_cross_user", return_value=True),
        patch.object(agent_runner.AutonomousAgentRunner, "_validate_cross_user_guard_bin"),
    ):
        command, cwd = agent_runner.AutonomousAgentRunner._wrap_agent_cmd(
            ["/usr/bin/claude"], "/private/repo", "repo-user", env, task_id=task_id
        )
    return command, cwd


def test_wrap_passes_task_id_flag(monkeypatch, tmp_path):
    command, cwd = _wrap(monkeypatch, tmp_path, "abc-123")
    assert cwd is None
    assert "--isolated" in command
    idx = command.index("--isolated")
    # --task-id <value> appears in the launcher argv, after --isolated.
    assert "--task-id" in command
    tid_idx = command.index("--task-id")
    assert command[tid_idx + 1] == "abc-123"
    # Ordering sanity: both flags precede the positional args.
    assert idx < tid_idx


def test_wrap_sanitizes_task_id(monkeypatch, tmp_path):
    command, _ = _wrap(monkeypatch, tmp_path, "a/b c")
    tid_idx = command.index("--task-id")
    value = command[tid_idx + 1]
    assert "/" not in value and " " not in value


def test_wrap_omits_task_id_flag_when_none(monkeypatch, tmp_path):
    """Legacy callers (no task_id) must not emit a broken ``--task-id`` with
    an empty value; the launcher then falls back to its legacy shared runtime."""
    from app.modules.workspace.autonomous import agent_runner

    _guard_bin(monkeypatch, tmp_path)
    env = {"PATH": f"{tmp_path / 'agent-bin'}:/usr/bin"}
    from unittest.mock import patch

    with (
        patch.object(agent_runner.AutonomousAgentRunner, "_is_cross_user", return_value=True),
        patch.object(agent_runner.AutonomousAgentRunner, "_validate_cross_user_guard_bin"),
    ):
        command, _ = agent_runner.AutonomousAgentRunner._wrap_agent_cmd(
            ["/usr/bin/claude"], "/private/repo", "repo-user", env
        )
    assert "--task-id" not in command
