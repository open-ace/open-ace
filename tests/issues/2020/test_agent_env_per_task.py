"""Per-task HOME/TMP/XDG wiring in ``_build_agent_env`` (Issue #2020 Phase A).

The agent subprocess must inherit a per-attempt HOME/TMP/XDG tree derived from
``task_id``, never the shared Agent account home or ``/tmp``. This holds for
both launch paths: on the same-user path the Python env is authoritative; on
the cross-user path the launcher re-derives the same paths from ``--task-id``
(its own ``env -i`` is tested separately). These tests pin the Python side.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REMOTE = Path(__file__).resolve().parents[3] / "remote-agent"
if str(_REMOTE) not in sys.path:
    sys.path.insert(0, str(_REMOTE))


class _FakeAdapter:
    def get_env_vars(self, proxy_url, proxy_token):
        return {"ANTHROPIC_API_KEY": proxy_token, "ANTHROPIC_BASE_URL": proxy_url}

    def get_settings_path(self):
        return None


class _FakeProxy:
    def generate_proxy_token(self, **kwargs):
        return "proxy-token-xyz"


def _patch_deps(monkeypatch):
    for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GH_TOKEN"):
        monkeypatch.setenv(key, "raw-" + key.lower())
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.delenv("OPENACE_ALLOW_RAW_KEY_FALLBACK", raising=False)
    monkeypatch.setattr(
        "app.modules.workspace.api_key_proxy.get_api_key_proxy_service",
        lambda: _FakeProxy(),
    )

    def fake_get_config_value(*args, **kwargs):
        if args and args[-1] == "web_port":
            return 5000
        return "http://test"

    monkeypatch.setattr("app.utils.config.get_config_value", fake_get_config_value)


def _build(monkeypatch, task_id, *, task_root=None):
    from app.modules.workspace.autonomous.agent_runner import AutonomousAgentRunner

    _patch_deps(monkeypatch)
    if task_root is not None:
        monkeypatch.setenv("OPENACE_AGENT_TASK_ROOT", task_root)
    else:
        monkeypatch.delenv("OPENACE_AGENT_TASK_ROOT", raising=False)
    return AutonomousAgentRunner._build_agent_env(
        _FakeAdapter(), "claude-code", None, f"sess-{task_id}", "model-x", task_id=task_id
    )


def test_build_agent_env_sets_per_task_home_tmp_xdg(monkeypatch, tmp_path):
    env = _build(monkeypatch, "abc-123", task_root=str(tmp_path))

    base = tmp_path / "abc-123"
    assert env["HOME"] == str(base / "home")
    assert env["TMPDIR"] == str(base / "tmp")
    assert env["XDG_CACHE_HOME"] == str(base / "cache")
    assert env["XDG_CONFIG_HOME"] == str(base / "config")
    assert env["XDG_DATA_HOME"] == str(base / "data")


def test_build_agent_env_relocates_git_cache_root_to_task_cache(monkeypatch, tmp_path):
    # Issue decision (2026-07-26): OPENACE_GIT_CACHE_ROOT is per-task.
    env = _build(monkeypatch, "abc-123", task_root=str(tmp_path))
    assert env["OPENACE_GIT_CACHE_ROOT"] == str(tmp_path / "abc-123" / "cache" / "pre-commit")


def test_build_agent_env_two_tasks_get_disjoint_homes(monkeypatch, tmp_path):
    a = _build(monkeypatch, "task-a", task_root=str(tmp_path))
    b = _build(monkeypatch, "task-b", task_root=str(tmp_path))
    assert a["HOME"] != b["HOME"]
    assert a["TMPDIR"] != b["TMPDIR"]
    assert a["XDG_CACHE_HOME"] != b["XDG_CACHE_HOME"]


def test_build_agent_env_skips_per_task_home_when_root_unwritable(monkeypatch):
    """Cross-user path: the launcher (root) creates /run, not the service
    user. _build_agent_env must NOT point HOME at a non-existent /run tree —
    it leaves HOME untouched so the launcher's env -i sets it instead."""
    env = _build(monkeypatch, "abc-123", task_root="/run/openace-agent-tasks")
    assert "HOME" not in env or "/run/openace-agent-tasks" not in env.get("HOME", "")
    assert "TMPDIR" not in env or "/run/openace-agent-tasks" not in env.get("TMPDIR", "")


def test_build_agent_env_task_root_is_configurable(monkeypatch, tmp_path):
    env = _build(monkeypatch, "t1", task_root=str(tmp_path))
    assert env["HOME"] == str(tmp_path / "t1" / "home")


def test_build_agent_env_without_task_id_keeps_legacy_shared_home(monkeypatch):
    """Backward compat: callers that do not pass task_id get the legacy
    behavior (service HOME), so existing call sites / tests are not broken
    until they opt into per-task isolation."""
    from app.modules.workspace.autonomous.agent_runner import AutonomousAgentRunner

    _patch_deps(monkeypatch)
    monkeypatch.delenv("OPENACE_AGENT_TASK_ROOT", raising=False)
    env = AutonomousAgentRunner._build_agent_env(
        _FakeAdapter(), "claude-code", None, "sess-1", "model-x"
    )
    # Legacy path: HOME is NOT remapped to a per-task tree.
    assert "HOME" not in env or "/run/openace-agent-tasks" not in env.get("HOME", "")
