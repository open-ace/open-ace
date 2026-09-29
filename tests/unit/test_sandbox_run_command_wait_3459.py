"""Unit tests for OpenSandboxWebuiLauncher.run_command_wait (Issue #3459).

The public request-path entry wraps POST /command background:true + bounded
/command/status polling. Its deadline must come from the CALLER, never the
90s restore budget — a wedged sandbox must not hang an HTTP request.
"""

import pytest

from app.modules.workspace.autonomous.sandbox.opensandbox.config import (
    EndpointConfig,
    SandboxBackendConfig,
)
from app.services.webui_sandbox_opensandbox import OpenSandboxWebuiLauncher

pytestmark = [pytest.mark.issue(3459)]


class _FakeApi:
    def __init__(self, statuses, init_text="cmd-1"):
        self._statuses = statuses
        self._init_text = init_text
        self.bodies = []

    def run_command(self, sandbox_id, body):
        self.bodies.append(body)
        return iter([{"type": "init", "text": self._init_text}])

    def command_status(self, sandbox_id, command_id):
        return self._statuses()


def _launcher(api):
    endpoint = EndpointConfig(
        tier="t", base_url="http://x", api_key_env="X", runtime_class="runc", default_image="i"
    )
    cfg = SandboxBackendConfig(default_tier="t", endpoints={"t": endpoint}, installation_id="i")
    return OpenSandboxWebuiLauncher(
        backend_config=cfg,
        api_factory=lambda ep: api,
        poll_interval_seconds=0.01,
    )


def test_exit_zero_confirms():
    api = _FakeApi(lambda: {"running": False, "exit_code": 0})
    assert _launcher(api).run_command_wait("sbx", "mkdir -p /workspace/a") is True
    assert api.bodies[0]["background"] is True
    assert api.bodies[0]["command"] == "mkdir -p /workspace/a"


def test_nonzero_exit_fails():
    api = _FakeApi(lambda: {"running": False, "exit_code": 1})
    assert _launcher(api).run_command_wait("sbx", "false") is False


def test_timeout_is_caller_bounded():
    """Always-running status + a 50ms caller deadline must return in ~50ms,
    not inherit the 90s restore budget."""

    class _Clock:
        calls = 0

        @staticmethod
        def never_terminal():
            _Clock.calls += 1
            return {"running": True}

    api = _FakeApi(_Clock.never_terminal)
    assert _launcher(api).run_command_wait("sbx", "sleep 999", timeout_seconds=0.05) is False
    assert _Clock.calls >= 2  # actually polled, then gave up at the deadline


def test_no_command_id_fails():
    api = _FakeApi(lambda: {"running": False, "exit_code": 0}, init_text="")
    assert _launcher(api).run_command_wait("sbx", "x") is False


def test_unknown_exit_code_fails():
    api = _FakeApi(lambda: {"running": False})
    assert _launcher(api).run_command_wait("sbx", "x") is False
