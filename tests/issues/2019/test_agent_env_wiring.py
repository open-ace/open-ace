"""Wiring tests for the autonomous env security fix (Issue #2019).

Exercises ``agent_runner._build_agent_env`` (local) and ``executor._build_env``
(remote) end-to-end through the real ``build_secure_agent_env`` policy with the
proxy service + CLI adapter faked, to confirm the integration:

  * proxy success → raw keys scrubbed, proxy token injected;
  * proxy failure in production → RuntimeError (agent does not launch);
  * proxy failure in dev without opt-in → RuntimeError;
  * proxy failure in dev with opt-in → raw keys inherited (loud fallback);
  * executor with empty proxy token → RuntimeError in production.

The pure policy is covered by ``test_env_security.py``.
"""

import sys
from pathlib import Path
from types import SimpleNamespace

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
    def __init__(self, fail=False):
        self.fail = fail

    def generate_proxy_token(self, **kwargs):
        if self.fail:
            raise RuntimeError("proxy down")
        return "proxy-token-xyz"


_RAW_KEYS = (
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GEMINI_API_KEY",
    "GH_TOKEN",
    "BAILIAN_CODING_PLAN_API_KEY",
)


def _seed_raw_env(monkeypatch):
    for key in _RAW_KEYS:
        monkeypatch.setenv(key, "raw-" + key.lower())
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.delenv("OPENACE_ALLOW_RAW_KEY_FALLBACK", raising=False)


def _patch_proxy(monkeypatch, fail=False):
    monkeypatch.setattr(
        "app.modules.workspace.api_key_proxy.get_api_key_proxy_service",
        lambda: _FakeProxy(fail=fail),
    )


def _patch_config(monkeypatch):
    def fake_get_config_value(*args, **kwargs):
        if args and args[-1] == "web_port":
            return 5000
        return "http://test"

    monkeypatch.setattr("app.utils.config.get_config_value", fake_get_config_value)


# ── agent_runner._build_agent_env ─────────────────────────────────────────


def test_build_agent_env_success_scrubs_raw_keys(monkeypatch):
    from app.modules.workspace.autonomous.agent_runner import AutonomousAgentRunner

    _seed_raw_env(monkeypatch)
    _patch_proxy(monkeypatch, fail=False)
    _patch_config(monkeypatch)

    env = AutonomousAgentRunner._build_agent_env(
        _FakeAdapter(), "claude-code", None, "sess-1", "model-x"
    )

    assert env["OPENACE_PROXY_TOKEN"] == "proxy-token-xyz"
    # The adapter's proxy-bearing var holds the token, not the raw key.
    assert env["ANTHROPIC_API_KEY"] == "proxy-token-xyz"
    # All other raw credentials are gone.
    for key in ("OPENAI_API_KEY", "GEMINI_API_KEY", "GH_TOKEN", "BAILIAN_CODING_PLAN_API_KEY"):
        assert key not in env, f"{key} leaked"


def test_build_agent_env_proxy_fail_in_production_raises(monkeypatch):
    from app.modules.workspace.autonomous.agent_runner import AutonomousAgentRunner

    _seed_raw_env(monkeypatch)
    _patch_proxy(monkeypatch, fail=True)
    _patch_config(monkeypatch)
    monkeypatch.setenv("FLASK_ENV", "production")

    with pytest.raises(RuntimeError, match="(?i)proxy|refus|launch"):
        AutonomousAgentRunner._build_agent_env(
            _FakeAdapter(), "claude-code", None, "sess-1", "model-x"
        )


def test_build_agent_env_proxy_fail_in_dev_without_opt_in_raises(monkeypatch):
    from app.modules.workspace.autonomous.agent_runner import AutonomousAgentRunner

    _seed_raw_env(monkeypatch)
    _patch_proxy(monkeypatch, fail=True)
    _patch_config(monkeypatch)
    # FLASK_ENV unset → dev; opt-in unset → must fail closed.
    with pytest.raises(RuntimeError):
        AutonomousAgentRunner._build_agent_env(
            _FakeAdapter(), "claude-code", None, "sess-1", "model-x"
        )


def test_build_agent_env_proxy_fail_in_dev_with_opt_in_keeps_raw(monkeypatch):
    from app.modules.workspace.autonomous.agent_runner import AutonomousAgentRunner

    _seed_raw_env(monkeypatch)
    _patch_proxy(monkeypatch, fail=True)
    _patch_config(monkeypatch)
    monkeypatch.setenv("OPENACE_ALLOW_RAW_KEY_FALLBACK", "1")

    env = AutonomousAgentRunner._build_agent_env(
        _FakeAdapter(), "claude-code", None, "sess-1", "model-x"
    )

    # Explicit unsafe fallback: raw keys inherited (caller logs a loud warning).
    assert env["OPENAI_API_KEY"] == "raw-openai_api_key"
    assert env["GH_TOKEN"] == "raw-gh_token"


# ── executor._build_env (remote autonomous) ───────────────────────────────


def test_executor_build_env_with_token_scrubs_raw(monkeypatch):
    import executor

    monkeypatch.setenv("OPENAI_API_KEY", "raw-openai")
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.setattr(executor, "get_adapter", lambda cli: _FakeAdapter())

    self_obj = SimpleNamespace(server_url="http://test:5000")
    env = executor.ProcessExecutor._build_env(self_obj, "claude-code", "tok", "model-x")

    assert env["OPENACE_PROXY_TOKEN"] == "tok"
    assert env["ANTHROPIC_API_KEY"] == "tok"
    assert "OPENAI_API_KEY" not in env  # raw scrubbed, not re-added by the adapter


def test_executor_build_env_empty_token_in_production_raises(monkeypatch):
    import executor

    monkeypatch.setenv("FLASK_ENV", "production")
    monkeypatch.delenv("OPENACE_ALLOW_RAW_KEY_FALLBACK", raising=False)
    monkeypatch.setattr(executor, "get_adapter", lambda cli: _FakeAdapter())

    self_obj = SimpleNamespace(server_url="http://test:5000")
    with pytest.raises(RuntimeError):
        executor.ProcessExecutor._build_env(self_obj, "claude-code", "", "model-x")
