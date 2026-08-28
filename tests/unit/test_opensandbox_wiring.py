"""Production wiring for the OpenSandbox backend (#2023).

These are the tests that would have caught a provider that exists, is correct,
and is never actually reached — the failure mode that made both the isolation
gate and the orphan sweep pass green while production kept running Legacy.
"""

from __future__ import annotations

import json

import pytest

from app.modules.workspace.autonomous.sandbox import registry
from app.modules.workspace.autonomous.sandbox.opensandbox.fake_server import FakeOpenSandboxApi
from app.services.autonomous_scheduler import _destroy_orphan_sandbox

pytestmark = [pytest.mark.regression, pytest.mark.issue(2023)]

_DIGEST = "ghcr.io/open-ace/agent@sha256:" + "a" * 64


@pytest.fixture
def api(tmp_path, monkeypatch):
    raw = {
        "default_tier": "gvisor",
        "endpoints": {
            "gvisor": {
                "base_url": "http://osb.open-ace.svc.cluster.local:8080/v1",
                "api_key_env": "OSB_KEY",
                "execd_token_env": "OSB_EXECD_TOKEN",
                "runtime_class": "gvisor",
                "default_image": _DIGEST,
                "egress_allow_hosts": ["api.anthropic.com"],
                "attestations": {
                    "egress_enforced": True,
                    "egress_mode_dns_nft": True,
                    "metadata_cidr_blocked": True,
                    "execd_token_required": True,
                    "secure_access_required": True,
                    "nonroot_enforced": True,
                    "readonly_rootfs": True,
                    "seccomp_runtime_default": True,
                    "dedicated_service_account": True,
                    "pod_pids_limit": 512,
                },
            }
        },
        "image_allowlist": [_DIGEST],
    }
    path = tmp_path / "sandbox-backends.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    monkeypatch.setenv("OSB_KEY", "k")
    monkeypatch.setenv("OSB_EXECD_TOKEN", "t")
    monkeypatch.setenv("OPENACE_SANDBOX_BACKENDS", str(path))
    fake = FakeOpenSandboxApi()
    fake.sandboxes["sb-1"] = {"id": "sb-1", "status": {"state": "Running"}, "metadata": {}}
    monkeypatch.setattr(registry, "_default_api_factory", lambda endpoint: fake)
    return fake


def test_node_and_control_plane_restart_reconcile_sandbox(api):
    # Asserted at the SCHEDULER layer, not on the provider method. The provider
    # method alone would pass green while production leaked: _destroy_orphan_sandbox
    # returned early for every provider except remote_machine, and
    # _reconcile_orphan_sandboxes then marked the row destroyed regardless.
    wf = {
        "workflow_id": "w1",
        "sandbox_provider": "opensandbox",
        "sandbox_id": "sb-1",
        "sandbox_remote_session_id": None,
    }
    _destroy_orphan_sandbox(wf, remote_session_manager=None)
    assert "sb-1" in api.deleted


def test_opensandbox_row_without_a_sandbox_id_is_a_no_op(api):
    wf = {
        "workflow_id": "w1",
        "sandbox_provider": "opensandbox",
        "sandbox_id": None,
        "sandbox_remote_session_id": None,
    }
    _destroy_orphan_sandbox(wf, remote_session_manager=None)
    assert not api.deleted


def test_legacy_row_still_no_ops(api):
    wf = {
        "workflow_id": "w1",
        "sandbox_provider": "legacy_posix",
        "sandbox_id": "sb-1",
        "sandbox_remote_session_id": None,
    }
    _destroy_orphan_sandbox(wf, remote_session_manager=None)
    assert not api.deleted


def test_remote_machine_reconcile_path_is_unchanged():
    stopped: list[str] = []

    class _Manager:
        def stop_session(self, session_id):
            stopped.append(session_id)

    wf = {
        "workflow_id": "w1",
        "sandbox_provider": "remote_machine",
        "sandbox_id": "sb-1",
        "sandbox_remote_session_id": "rs-9",
    }
    _destroy_orphan_sandbox(wf, remote_session_manager=_Manager())
    assert stopped == ["rs-9"]


def test_sweep_survives_a_provider_failure_on_one_row(api, monkeypatch):
    def _boom(endpoint):
        raise RuntimeError("registry exploded")

    monkeypatch.setattr(registry, "_default_api_factory", _boom)
    wf = {
        "workflow_id": "w1",
        "sandbox_provider": "opensandbox",
        "sandbox_id": "sb-1",
        "sandbox_remote_session_id": None,
    }
    # One bad row must never abort a sweep that walks many.
    _destroy_orphan_sandbox(wf, remote_session_manager=None)


# ── agent-runner wiring (spec §6.5, §6.6) ─────────────────────────────


class _PidlessTransport:
    """A container-backend transport: no pid, no local process."""

    def __init__(self, stdout_lines=()):
        self.written: list[bytes] = []
        self._stdout = list(stdout_lines)
        self.stdin_closed = False
        self.shutdown_calls: list[float] = []
        self.returncode = None

    def write_stdin(self, data: bytes) -> None:
        self.written.append(data)

    def close_stdin(self) -> None:
        self.stdin_closed = True

    def readline_stdout(self) -> bytes:
        return self._stdout.pop(0) if self._stdout else b""

    def readline_stderr(self) -> bytes:
        return b""

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        return self.returncode

    def shutdown(self, grace: float = 5.0) -> None:
        self.shutdown_calls.append(grace)
        self.returncode = 0

    @property
    def pid(self):
        return None


def _runner():
    from app.modules.workspace.autonomous.agent_runner import AutonomousAgentRunner

    runner = AutonomousAgentRunner.__new__(AutonomousAgentRunner)
    runner._local_sessions = {}
    runner._activity_callback = None
    runner._resolve_sidebar_session = lambda *a, **k: ""
    return runner


def _session(transport):
    from app.modules.workspace.autonomous.agent_runner import _LocalSession

    session = _LocalSession(session_id="s-1", process=None, transport=transport)
    session.workflow_id = "wf-1"
    return session


def test_agent_receives_the_prompt_over_a_pidless_transport():
    # The end-to-end assertion. A call-site-swap test would pass while the
    # `session.process is None` guards silently swallowed every write, leaving
    # the agent launched and never spoken to.
    runner = _runner()
    transport = _PidlessTransport()
    session = _session(transport)
    assert runner._write_stdin(session, '{"type":"user"}') is True
    assert transport.written and b'"type":"user"' in transport.written[0]


def test_reader_consumes_stdout_over_a_pidless_transport():
    runner = _runner()
    transport = _PidlessTransport(
        [json.dumps({"type": "system", "subtype": "init", "session_id": "cli-1"}).encode()]
    )
    session = _session(transport)
    runner._read_stdout(session)
    assert session.cli_session_id == "cli-1"


def test_local_session_derives_a_transport_from_a_raw_popen():
    # Sessions built directly from a Popen — several suites and the remote
    # tracker do this — must still have a working seam.
    from types import SimpleNamespace

    from app.modules.workspace.autonomous.agent_runner import _LocalSession
    from app.modules.workspace.autonomous.sandbox.transport import LocalProcessTransport

    proc = SimpleNamespace(stdout=None, stderr=None, stdin=None, returncode=0, pid=42)
    session = _LocalSession(session_id="s-1", process=proc)
    assert isinstance(session.transport, LocalProcessTransport)
    assert session.transport.process is proc


def test_pause_and_resume_reach_the_provider_for_a_pidless_transport():
    # The old guard was `not session.process`, which returned False before the
    # provider branch could run — making pause permanently unavailable for a
    # container backend, while acceptance criterion 2 requires it.
    from app.modules.workspace.autonomous.sandbox.types import ExecHandle

    paused: list[str] = []

    class _Provider:
        def pause(self, exec_handle):
            paused.append("pause")

        def resume(self, exec_handle):
            paused.append("resume")

    runner = _runner()
    session = _session(_PidlessTransport())
    session.sandbox_provider = _Provider()
    session.exec_handle = ExecHandle(sandbox_id="sb-1", command_id="cmd-1")
    runner._local_sessions["s-1"] = session

    assert runner.pause_session("s-1") is True
    assert runner.resume_session("s-1") is True
    assert paused == ["pause", "resume"]


def test_pause_still_refuses_a_finished_session():
    runner = _runner()
    transport = _PidlessTransport()
    transport.returncode = 0
    session = _session(transport)
    runner._local_sessions["s-1"] = session
    assert runner.pause_session("s-1") is False


def test_select_sandbox_provider_returns_the_injected_one_without_config(monkeypatch, tmp_path):
    from app.modules.workspace.autonomous.agent_runner import AutonomousAgentRunner
    from app.modules.workspace.autonomous.sandbox.legacy_posix import LegacyPosixProvider

    monkeypatch.delenv("OPENACE_SANDBOX_BACKENDS", raising=False)
    monkeypatch.setattr(
        "app.modules.workspace.autonomous.sandbox.opensandbox.config."
        "DEFAULT_BACKEND_CONFIG_PATH",
        str(tmp_path / "etc.json"),
    )
    monkeypatch.setattr(
        "app.modules.workspace.autonomous.sandbox.opensandbox.config.USER_BACKEND_CONFIG_PATH",
        str(tmp_path / "user.json"),
    )
    runner = AutonomousAgentRunner.__new__(AutonomousAgentRunner)
    injected = LegacyPosixProvider()
    runner._sandbox_provider = injected
    runner.remote_session_manager = None
    assert runner._select_sandbox_provider("local", tenant_id=1) is injected


def test_required_production_policy_cannot_fallback_to_legacy(api, monkeypatch):
    # Through the documented single branch point, with a config present.
    from app.modules.workspace.autonomous.agent_runner import AutonomousAgentRunner
    from app.modules.workspace.autonomous.sandbox.legacy_posix import LegacyPosixProvider
    from app.modules.workspace.autonomous.sandbox.opensandbox.provider import OpenSandboxProvider

    runner = AutonomousAgentRunner.__new__(AutonomousAgentRunner)
    runner._sandbox_provider = LegacyPosixProvider()
    runner.remote_session_manager = None
    selected = runner._select_sandbox_provider("local", tenant_id=1, project_path="/workspace")
    assert isinstance(selected, OpenSandboxProvider)
