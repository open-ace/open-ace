"""remote executor _build_env 的敏感/拓扑 env 剥离（#2680 跟进）。"""

import sys
from pathlib import Path

import pytest

_project_root = Path(__file__).resolve().parent.parent.parent
_remote_agent_str = str(_project_root / "remote-agent")
if _remote_agent_str not in sys.path:
    sys.path.insert(0, _remote_agent_str)

from executor import ProcessExecutor  # noqa: E402


def _make_executor():
    return ProcessExecutor(
        server_url="http://localhost:5000",
        output_callback=None,
        permission_callback=None,
        usage_callback=None,
    )


@pytest.mark.regression
@pytest.mark.issue(2680)
def test_executor_env_scrubs_scheduler_mode(monkeypatch):
    monkeypatch.setenv("SCHEDULER_MODE", "scheduler")
    env = _make_executor()._build_env("claude-code", "proxy-token")
    assert "SCHEDULER_MODE" not in env


@pytest.mark.regression
@pytest.mark.issue(2680)
def test_executor_env_scrubs_anthropic_auth_token(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "sk-raw-leak-probe")
    env = _make_executor()._build_env("claude-code", "proxy-token")
    assert "ANTHROPIC_AUTH_TOKEN" not in env
