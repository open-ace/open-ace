"""Production wiring for the OpenSandbox backend (#2023).

These are the tests that would have caught a provider that exists, is correct,
and is never actually reached — the failure mode that made both the isolation
gate and the orphan sweep pass green while production kept running Legacy.
"""

from __future__ import annotations

import json
import threading

import pytest

from app.modules.workspace.autonomous.sandbox import registry
from app.modules.workspace.autonomous.sandbox.opensandbox.fake_server import FakeOpenSandboxApi
from app.services.autonomous_scheduler import _destroy_orphan_sandbox

pytestmark = [pytest.mark.regression, pytest.mark.issue(2023)]

_DIGEST = "ghcr.io/open-ace/agent@sha256:" + "a" * 64


@pytest.fixture
def api(tmp_path, monkeypatch):
    raw = {
        "installation_id": "openace-test",
        "default_tier": "gvisor",
        "endpoints": {
            "gvisor": {
                "base_url": "http://osb.open-ace.svc.cluster.local:8080/v1",
                "api_key_env": "OSB_KEY",
                "execd_token_env": "OSB_EXECD_TOKEN",
                "runtime_class": "kata-qemu",
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
    # One bad row must never abort a sweep that walks many — and the caller
    # must LEARN the teardown failed (False) so it keeps the persisted ids
    # for a retry instead of stranding a live sandbox.
    assert _destroy_orphan_sandbox(wf, remote_session_manager=None) is False


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
        "app.modules.workspace.autonomous.sandbox.opensandbox.config.DEFAULT_BACKEND_CONFIG_PATH",
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


# ── attribution survives a failed teardown (spec §6.7) ────────────────


def test_a_failed_teardown_reports_false_so_the_row_keeps_its_attribution(api, monkeypatch):
    """The scheduler clears sandbox_id on the strength of this return value.

    _destroy_orphan_sandbox used to return None whatever happened, and the
    caller then marked the row destroyed and nulled both external ids. A
    transient outage therefore left a live sandbox billing until its TTL with
    nothing in the database able to name it again.
    """

    def _boom(sandbox_id):
        raise RuntimeError("lifecycle server unreachable")

    api.delete_sandbox = _boom  # type: ignore[assignment]
    wf = {
        "workflow_id": "w1",
        "sandbox_provider": "opensandbox",
        "sandbox_id": "sb-1",
        "sandbox_remote_session_id": None,
    }
    assert _destroy_orphan_sandbox(wf, remote_session_manager=None) is False


def test_a_successful_teardown_reports_true(api):
    wf = {
        "workflow_id": "w1",
        "sandbox_provider": "opensandbox",
        "sandbox_id": "sb-1",
        "sandbox_remote_session_id": None,
    }
    assert _destroy_orphan_sandbox(wf, remote_session_manager=None) is True


def test_a_row_with_nothing_to_destroy_is_not_reported_as_a_failure(api):
    """ "Nothing to tear down" must not look like "teardown failed"."""
    wf = {
        "workflow_id": "w1",
        "sandbox_provider": "legacy_posix",
        "sandbox_id": "sb-1",
        "sandbox_remote_session_id": None,
    }
    assert _destroy_orphan_sandbox(wf, remote_session_manager=None) is True


def test_a_registry_failure_is_reported_as_a_failed_teardown(api, monkeypatch):
    def _boom(endpoint):
        raise RuntimeError("registry exploded")

    monkeypatch.setattr(registry, "_default_api_factory", _boom)
    wf = {
        "workflow_id": "w1",
        "sandbox_provider": "opensandbox",
        "sandbox_id": "sb-1",
        "sandbox_remote_session_id": None,
    }
    assert _destroy_orphan_sandbox(wf, remote_session_manager=None) is False


# ── the full _run_local round trip (spec §6.5) ────────────────────────
#
# The test the previous round was missing. Every provider test drove the
# provider directly, so the backend could be entirely correct and still never
# be *used* correctly: the runner passed exec_policy=None, which sent every
# OpenSandbox run down the foreground /command branch, and the get_transport()
# call immediately after refused that state with `not_an_agent_turn`. Nothing
# called upload_workspace or apply_changes either, so even once the agent
# started it would have received an empty tree and had its edits dropped on
# destroy. Only a test that goes through _run_local can see any of that.


def _load_config():
    from app.modules.workspace.autonomous.sandbox.opensandbox.config import load_backend_config

    config = load_backend_config()
    assert config is not None
    return config


def _run_local_against(provider, worktree, monkeypatch, gate_spy=None, on_sandbox_created=None):
    """Invoke the REAL _run_local with *provider* selected by the gate.

    Only the boundaries are faked — the lifecycle/execd API and the PTY
    WebSocket. Everything between them is production code: the isolation gate,
    the provider lifecycle calls, the stream-json reader threads, the transport
    seam, and the teardown ordering.
    """
    import shutil

    from app.modules.workspace.autonomous.agent_runner import AutonomousAgentRunner

    runner = AutonomousAgentRunner.__new__(AutonomousAgentRunner)
    runner._local_sessions = {}
    runner._activity_callback = None
    runner._on_pid_registered = None
    runner._on_pid_cleared = None
    runner._on_sandbox_created = on_sandbox_created
    runner._sandbox_provider = provider
    runner.session_manager = None
    runner._resolve_sidebar_session = lambda *a, **k: ""
    # Sidebar JSONL discovery is claude-code's own session-id mechanism and is
    # orthogonal to the sandbox lifecycle under test; without a real ~/.claude
    # tree it fails and masks every assertion below it.
    runner._uses_sidebar_session_source = lambda *a, **k: False
    # The agent CLI binary is not installed on CI runners, and _run_local
    # returns "CLI tool not found" before it reaches the provider at all —
    # which makes this test silently vacuous there rather than red. Whether
    # `claude` is on PATH is not what is under test; the provider lifecycle is.
    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/local/bin/{name}")
    runner._select_sandbox_provider = lambda *a, **k: provider
    # Left REAL by default so the round trip exercises the production gate;
    # a spy replaces it only where the test is about the call itself.
    if gate_spy is not None:
        runner._resolve_tenant_for_isolation = gate_spy
    runner._resolve_sandbox_generation = lambda workflow_id: 1
    runner._load_task_policy = lambda: None
    runner._resource_policy_configured = lambda: False
    runner._build_agent_env = lambda *a, **k: {"HOME": "/home/agent"}
    return runner._run_local(
        session_id="s-1",
        cli_tool="claude-code",
        model="claude-sonnet-4",
        project_path=str(worktree),
        prompt="do the thing",
        permission_mode="default",
        timeout=30,
        workflow_id="wf-1",
        user_id=None,
        workspace_type="local",
    )


class _ScriptedPtyConnection:
    """A PTY WebSocket that answers only AFTER the prompt arrives.

    The gate is the point. A connection that replays its frames immediately
    finishes the turn before the runner writes anything, and the transport then
    correctly drops those writes as post-terminal — so the test would pass with
    a runner that never spoke to the agent at all. Blocking until the prompt is
    written is what makes "the agent was actually driven" observable.
    """

    def __init__(self, frames):
        self.sent: list = []
        self._frames = list(frames)
        self._prompted = threading.Event()

    def send(self, data):
        self.sent.append(data)
        payload = data if isinstance(data, bytes) else str(data).encode()
        if b'"type": "user"' in payload or b'"type":"user"' in payload:
            self._prompted.set()

    def recv(self, timeout=None):
        if not self._prompted.wait(timeout=5):
            raise TimeoutError("the runner never sent a prompt over the PTY socket")
        if self._frames:
            return self._frames.pop(0)
        return json.dumps({"type": "exit", "exit_code": 0})

    def close(self):
        self._prompted.set()


def _stdout_frame(payload: dict) -> bytes:
    return b"\x01" + (json.dumps(payload) + "\n").encode()


def test_run_local_drives_create_upload_pty_collect_apply_destroy(api, tmp_path, monkeypatch):
    from app.modules.workspace.autonomous.sandbox.opensandbox.provider import (
        OpenSandboxProvider,
        OpenSandboxTurnSpec,
    )

    worktree = tmp_path / "wt"
    worktree.mkdir()
    (worktree / "app.py").write_text("original\n", encoding="utf-8")

    # The agent's turn: an init frame so the runner learns the CLI session id,
    # then a terminal result frame.
    conn = _ScriptedPtyConnection(
        [
            _stdout_frame({"type": "system", "subtype": "init", "session_id": "cli-1"}),
            _stdout_frame(
                {"type": "result", "subtype": "success", "result": "done", "is_error": False}
            ),
        ]
    )
    provider = OpenSandboxProvider(
        _load_config(),
        api_factory=lambda endpoint: api,
        tenant="1",
        project_path=str(worktree),
        connect_factory=lambda url, headers: conn,
    )
    # The agent "edited" app.py: the manifest producer reports the new content.
    api.set_manifest({"app.py": b"edited\n"})

    calls: list[str] = []
    for name in ("create", "upload_workspace", "exec", "apply_changes", "destroy"):
        original = getattr(provider, name)

        def _record(*a, _n=name, _o=original, **k):
            calls.append(_n)
            return _o(*a, **k)

        monkeypatch.setattr(provider, name, _record)

    seen_policy: list = []
    original_exec = provider.exec

    def _capture_exec(handle, command, env, exec_policy):
        seen_policy.append(exec_policy)
        return original_exec(handle, command=command, env=env, exec_policy=exec_policy)

    monkeypatch.setattr(provider, "exec", _capture_exec)

    result = _run_local_against(provider, worktree, monkeypatch)

    # The lifecycle actually ran, in order.
    assert calls[:3] == ["create", "upload_workspace", "exec"]
    assert "apply_changes" in calls and "destroy" in calls
    assert calls.index("apply_changes") < calls.index("destroy"), (
        "apply_changes must run BEFORE destroy — after destroy the ephemeral "
        "filesystem is gone and the agent's entire work product with it"
    )

    # The turn took the PTY branch, not the foreground /command branch.
    assert isinstance(seen_policy[0], OpenSandboxTurnSpec), (
        "exec_policy was None, so the run took the foreground /command branch "
        "and the get_transport() that follows refuses it as not_an_agent_turn"
    )
    # The PTY session itself is closed during teardown, so assert on what the
    # socket carried instead of on the live session table: the runner really
    # spoke stream-json to the agent over this transport.
    assert conn.sent, "nothing was ever written to the PTY socket"
    written = b"".join(f if isinstance(f, bytes) else f.encode() for f in conn.sent)
    assert b"do the thing" in written

    # The work product came back into the trusted worktree.
    assert (worktree / "app.py").read_text(encoding="utf-8") == "edited\n"
    assert result.success, result.error


def test_run_local_actually_calls_the_isolation_gate(api, tmp_path, monkeypatch):
    """Mutation-proved gap: deleting the gate call left 94 tests green.

    Round 2 found the isolation gate routed through a method `_run_local` never
    called. The fix added the call — but nothing asserted the call exists, so
    the identical regression could land again silently.
    """
    from app.modules.workspace.autonomous.sandbox.opensandbox.provider import OpenSandboxProvider

    worktree = tmp_path / "wt"
    worktree.mkdir()
    (worktree / "app.py").write_text("x\n", encoding="utf-8")
    conn = _ScriptedPtyConnection(
        [
            _stdout_frame({"type": "system", "subtype": "init", "session_id": "cli-1"}),
            _stdout_frame({"type": "result", "subtype": "success", "result": "ok"}),
        ]
    )
    provider = OpenSandboxProvider(
        _load_config(),
        api_factory=lambda endpoint: api,
        tenant="1",
        project_path=str(worktree),
        connect_factory=lambda url, headers: conn,
    )
    api.set_manifest({"app.py": b"x\n"})

    seen: list = []
    _run_local_against(
        provider,
        worktree,
        monkeypatch,
        gate_spy=lambda *a, **k: (seen.append(True), 1)[1],
    )
    assert seen, "_run_local never consulted the isolation gate"


def test_a_failed_apply_fails_the_run(api, tmp_path, monkeypatch):
    """Mutation-proved gap: removing the error assignment left 54 tests green.

    An ephemeral backend whose work product cannot be applied has produced
    nothing. Reporting that run as a success is the silent data loss this
    backend exists to prevent, so the claim needs a test of its own.
    """
    from app.modules.workspace.autonomous.sandbox.opensandbox.provider import OpenSandboxProvider

    worktree = tmp_path / "wt"
    worktree.mkdir()
    (worktree / "app.py").write_text("original\n", encoding="utf-8")
    conn = _ScriptedPtyConnection(
        [
            _stdout_frame({"type": "system", "subtype": "init", "session_id": "cli-1"}),
            _stdout_frame({"type": "result", "subtype": "success", "result": "done"}),
        ]
    )
    provider = OpenSandboxProvider(
        _load_config(),
        api_factory=lambda endpoint: api,
        tenant="1",
        project_path=str(worktree),
        connect_factory=lambda url, headers: conn,
    )
    # The producer never wrote a manifest: collection fails, so the run's work
    # product cannot come back.
    api.set_manifest(None)

    result = _run_local_against(provider, worktree, monkeypatch)
    assert result.success is False
    assert "work product" in (result.error or "").lower()
    assert result.error_code


def test_the_effective_policy_records_the_running_providers_capabilities(
    api, tmp_path, monkeypatch
):
    """The row must describe the provider that RAN the task, not the injected default.

    `_notify_sandbox_created` read `self._sandbox_provider` — the Legacy
    provider the runner is constructed with — while stamping
    `provider_name="opensandbox"`. Legacy's capability set always contains
    CPU_MEM_PIDS_TIME_QUOTA, so an OpenSandbox row claimed quota enforcement
    even for a tier attesting no pod pids limit, and dropped the namespace and
    egress isolation it actually had. effective_policy's contract is that the
    map cannot lie.
    """
    from app.modules.workspace.autonomous.sandbox.opensandbox.provider import OpenSandboxProvider
    from app.modules.workspace.autonomous.sandbox.types import SandboxCapability

    worktree = tmp_path / "wt"
    worktree.mkdir()
    (worktree / "app.py").write_text("x\n", encoding="utf-8")
    conn = _ScriptedPtyConnection(
        [
            _stdout_frame({"type": "system", "subtype": "init", "session_id": "cli-1"}),
            _stdout_frame({"type": "result", "subtype": "success", "result": "ok"}),
        ]
    )
    provider = OpenSandboxProvider(
        _load_config(),
        api_factory=lambda endpoint: api,
        tenant="1",
        project_path=str(worktree),
        connect_factory=lambda url, headers: conn,
    )
    api.set_manifest({"app.py": b"x\n"})

    recorded: list[dict] = []

    def _on_created(session_id, sandbox_id, provider_name, remote_session_id, effective_policy):
        recorded.append({"provider_name": provider_name, "effective_policy": effective_policy})

    _run_local_against(provider, worktree, monkeypatch, on_sandbox_created=_on_created)

    assert recorded, "on_sandbox_created never fired"
    row = recorded[0]
    assert row["provider_name"] == "opensandbox"

    # The row must match what THIS provider declares, and differ from what the
    # injected Legacy provider would have contributed.
    from app.modules.workspace.autonomous.sandbox.effective_policy import build_effective_policy
    from app.modules.workspace.autonomous.sandbox.legacy_posix import LegacyPosixProvider

    expected = build_effective_policy("opensandbox", provider.capabilities(), None)
    legacy = build_effective_policy("opensandbox", LegacyPosixProvider().capabilities(), None)
    assert row["effective_policy"] == expected
    assert row["effective_policy"] != legacy, (
        "the row is indistinguishable from Legacy's capability set — the source "
        "of declared_caps is not observable from this assertion"
    )
    assert SandboxCapability.NAMESPACE_ISOLATION in provider.capabilities()


def test_a_rejected_pty_upgrade_destroys_the_sandbox_it_created(api, tmp_path, monkeypatch):
    """websockets raises InvalidStatus on a 401/403 handshake — not OSError.

    The post-create handler caught only (OSError, SubprocessError, SandboxError),
    so a rejected upgrade escaped it. The sandbox was already created and
    attribution was not persisted until after attach succeeded, leaving a live
    sandbox that nothing in the database could name.
    """
    from app.modules.workspace.autonomous.sandbox.opensandbox.provider import OpenSandboxProvider

    class _Rejected(Exception):
        """Stands in for websockets.exceptions.InvalidStatus."""

    # The fixture pre-seeds "sb-1" and create() mints the same id, which would
    # make "which ids did create() produce" unanswerable.
    api.sandboxes.clear()

    worktree = tmp_path / "wt"
    worktree.mkdir()
    (worktree / "app.py").write_text("x\n", encoding="utf-8")

    def _refuse(url, headers):
        raise _Rejected("server rejected WebSocket connection: HTTP 403")

    provider = OpenSandboxProvider(
        _load_config(),
        api_factory=lambda endpoint: api,
        tenant="1",
        project_path=str(worktree),
        connect_factory=_refuse,
    )
    result = _run_local_against(provider, worktree, monkeypatch)

    assert result.success is False
    created = set(api.sandboxes)
    assert created, f"no sandbox created; result.error={result.error!r}"
    assert created <= api.deleted, f"leaked sandbox(es): {created - api.deleted}"


def test_attribution_is_persisted_before_the_agent_is_attached(api, tmp_path, monkeypatch):
    """A crash between create and attach must still leave a nameable row.

    provider.create() returns a live sandbox before upload, exec and PTY attach
    run. Recording attribution only after attach left that whole window with no
    row the startup reconciler could act on.
    """
    from app.modules.workspace.autonomous.sandbox.opensandbox.provider import OpenSandboxProvider

    class _Rejected(Exception):
        pass

    worktree = tmp_path / "wt"
    worktree.mkdir()
    (worktree / "app.py").write_text("x\n", encoding="utf-8")

    recorded: list[str] = []

    def _on_created(session_id, sandbox_id, provider_name, remote_session_id, effective_policy):
        recorded.append(sandbox_id)

    def _refuse(url, headers):
        raise _Rejected("rejected")

    provider = OpenSandboxProvider(
        _load_config(),
        api_factory=lambda endpoint: api,
        tenant="1",
        project_path=str(worktree),
        connect_factory=_refuse,
    )
    _run_local_against(provider, worktree, monkeypatch, on_sandbox_created=_on_created)
    assert recorded, "the sandbox existed but was never recorded before attach failed"


def test_a_failing_destroy_does_not_replace_the_original_error(api, tmp_path, monkeypatch):
    """Cleanup must not become the thing that escapes.

    The catch-all handler calls provider.destroy(), and the server that just
    failed the exec is the same one being asked to delete. OpenSandboxProvider
    happens to be safe here — its _safe_destroy swallows — so this drives a
    provider whose destroy() really raises, which is what the guard is for and
    what any other backend may do. Unguarded, the cleanup error replaced the
    original one and escaped _run_local with no AgentTaskResult at all.
    """
    from app.modules.workspace.autonomous.sandbox.opensandbox.provider import OpenSandboxProvider

    class _Rejected(Exception):
        pass

    api.sandboxes.clear()
    worktree = tmp_path / "wt"
    worktree.mkdir()
    (worktree / "app.py").write_text("x\n", encoding="utf-8")

    inner = OpenSandboxProvider(
        _load_config(),
        api_factory=lambda endpoint: api,
        tenant="1",
        project_path=str(worktree),
        connect_factory=lambda url, headers: (_ for _ in ()).throw(_Rejected("upgrade refused")),
    )

    class _DestroyRaises:
        """Delegates everything, except destroy() blows up."""

        def __init__(self, wrapped):
            self._wrapped = wrapped

        def __getattr__(self, name):
            return getattr(self._wrapped, name)

        def destroy(self, handle):
            raise RuntimeError("lifecycle server unreachable")

    result = _run_local_against(_DestroyRaises(inner), worktree, monkeypatch)
    assert result.success is False
    assert "upgrade refused" in (result.error or ""), (
        f"the destroy failure replaced the original error: {result.error!r}"
    )
