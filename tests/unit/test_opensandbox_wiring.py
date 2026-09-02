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


def _run_local_against(
    provider,
    worktree,
    monkeypatch,
    gate_spy=None,
    on_sandbox_created=None,
    *,
    resume=False,
    resume_session_id=None,
    agent_state_store=None,
    session_id="s-1",
    uses_sidebar=False,
    runner_spy=None,
):
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

    # Under uses_sidebar the runner insists on a resolved sidebar session, and
    # there is no real ~/.claude tree here — so stand in for the id the stream
    # already reported. What is under test is the transcript's journey, not
    # claude-code's own JSONL discovery.
    def _resolve_sidebar(session_obj, **_kw):
        # It SETS persisted_session_id; it does not return one.
        if uses_sidebar:
            session_obj.persisted_session_id = session_obj.cli_session_id or "cli-1"
        return ""

    runner._resolve_sidebar_session = _resolve_sidebar
    # Sidebar JSONL discovery is claude-code's own session-id mechanism and is
    # orthogonal to the sandbox lifecycle under test; without a real ~/.claude
    # tree it fails and masks every assertion below it.
    # #3237: claude-code's real value here is True, and that is what gates
    # `_capture_cli_session_id` — so a test about carrying the transcript has to
    # run in the production shape or the export silently has no id to export.
    runner._uses_sidebar_session_source = lambda *a, **k: uses_sidebar
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
    # #3237: the agent-state store is a lazy property, so a test that does not
    # care simply gets the real one pointed nowhere in particular. Tests about
    # carrying the transcript inject their own.
    if agent_state_store is not None:
        runner._agent_state_store = agent_state_store
    if runner_spy is not None:
        runner_spy(runner)
    return runner._run_local(
        session_id=session_id,
        cli_tool="claude-code",
        model="claude-sonnet-4",
        project_path=str(worktree),
        prompt="do the thing",
        permission_mode="default",
        timeout=30,
        workflow_id="wf-1",
        user_id=None,
        workspace_type="local",
        resume=resume,
        resume_session_id=resume_session_id,
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
    assert "upgrade refused" in (
        result.error or ""
    ), f"the destroy failure replaced the original error: {result.error!r}"


# ── #3237: carrying the CLI transcript across the sandbox boundary ────
#
# These drive the REAL _run_local. The unit tests elsewhere cover
# _plan_agent_state, _build_agent_argv, the store and the provider methods in
# isolation — all of which passed while the wiring in _run_local was absent.
# An independent review proved that by deleting the import block, the export
# block and `resume = state_plan.resume` and watching all 10,250 tests stay
# green. These are the tests that fail when that happens.


def _agent_state_store(tmp_path):
    from app.modules.workspace.autonomous.sandbox.agent_state_store import AgentStateStore

    return AgentStateStore(root=str(tmp_path / "agent-state"))


def _provider_for(api, worktree, conn):
    from app.modules.workspace.autonomous.sandbox.opensandbox.provider import OpenSandboxProvider

    provider = OpenSandboxProvider(
        _load_config(),
        api_factory=lambda endpoint: api,
        tenant="1",
        project_path=str(worktree),
        connect_factory=lambda url, headers: conn,
    )
    api.set_manifest({"app.py": b"edited\n"})
    return provider


def _turn_frames(session_id="cli-1"):
    return [
        _stdout_frame({"type": "system", "subtype": "init", "session_id": session_id}),
        _stdout_frame(
            {"type": "result", "subtype": "success", "result": "done", "is_error": False}
        ),
    ]


def _worktree_at(tmp_path, name="wt"):
    worktree = tmp_path / name
    worktree.mkdir()
    (worktree / "app.py").write_text("original\n", encoding="utf-8")
    return worktree


def test_run_local_exports_the_transcript_before_destroy(api, tmp_path, monkeypatch):
    """Turn 1 must leave the next turn something to resume from.

    Deleting the export block in _run_local passes every other test in the
    repository. This is the one that notices.
    """
    worktree = _worktree_at(tmp_path)
    provider = _provider_for(api, worktree, _ScriptedPtyConnection(_turn_frames()))
    store = _agent_state_store(tmp_path)

    order: list[str] = []
    real_export = provider.export_agent_state
    real_destroy = provider.destroy

    def _export(handle, *, cli_session_id):
        order.append("export")
        # Stand in for the CLI having written its transcript during the turn.
        provider.import_agent_state(
            handle, cli_session_id=cli_session_id, blob=b"TURN-ONE-TRANSCRIPT\n"
        )
        return real_export(handle, cli_session_id=cli_session_id)

    def _destroy(handle):
        order.append("destroy")
        return real_destroy(handle)

    monkeypatch.setattr(provider, "export_agent_state", _export)
    monkeypatch.setattr(provider, "destroy", _destroy)

    result = _run_local_against(
        provider,
        worktree,
        monkeypatch,
        agent_state_store=store,
        session_id="s-1",
        uses_sidebar=True,
    )

    assert result.success, result.error
    assert order == ["export", "destroy"], (
        f"export must run BEFORE destroy — after destroy the pod is gone and the "
        f"transcript with it. Got {order}"
    )
    assert store.get("wf-1", "s-1") == b"TURN-ONE-TRANSCRIPT\n", (
        "the transcript was never written to the control-plane store, so the "
        "next turn on this line has nothing to resume from"
    )


def test_run_local_imports_the_transcript_and_emits_resume(api, tmp_path, monkeypatch):
    """Turn 2: the stored transcript is placed, and --resume goes into argv.

    Both halves matter. Placing the file without --resume resumes nothing;
    --resume without the file is the #3237 defect itself.
    """
    worktree = _worktree_at(tmp_path)
    provider = _provider_for(api, worktree, _ScriptedPtyConnection(_turn_frames("cli-1")))
    store = _agent_state_store(tmp_path)
    store.put("wf-1", "s-1", b"CARRIED\n")

    imported: list = []
    real_import = provider.import_agent_state

    def _import(handle, *, cli_session_id, blob):
        imported.append((cli_session_id, blob))
        return real_import(handle, cli_session_id=cli_session_id, blob=blob)

    monkeypatch.setattr(provider, "import_agent_state", _import)

    seen_cmd: list = []
    real_exec = provider.exec

    def _exec(handle, command, env, exec_policy):
        seen_cmd.append(list(command))
        return real_exec(handle, command=command, env=env, exec_policy=exec_policy)

    monkeypatch.setattr(provider, "exec", _exec)

    result = _run_local_against(
        provider,
        worktree,
        monkeypatch,
        agent_state_store=store,
        resume=True,
        resume_session_id="cli-1",
        uses_sidebar=True,
    )

    assert result.success, result.error
    assert imported and imported[0][1] == b"CARRIED\n", "the stored transcript was never placed"
    assert imported[0][0] == "cli-1", (
        "the transcript must land under the id --resume will look for, not the "
        f"tracking id; got {imported[0][0]!r}"
    )
    assert "--resume" in seen_cmd[0], f"--resume missing from argv: {seen_cmd[0]}"
    assert "cli-1" in seen_cmd[0]


def test_run_local_drops_resume_when_nothing_is_stored(api, tmp_path, monkeypatch):
    """An ABSENT slot starts a fresh session — it is not a failure.

    First turn on a line, or a control-plane restart that cleared tmpfs. This
    is openace-run-as.sh's `if [ -d "$preserve_claude_dir" ]` guard: skip the
    restore and carry on. Sending --resume here would produce exactly the
    wasted invocation #3237 exists to remove.
    """
    worktree = _worktree_at(tmp_path)
    provider = _provider_for(api, worktree, _ScriptedPtyConnection(_turn_frames()))
    store = _agent_state_store(tmp_path)  # empty

    seen_cmd: list = []
    real_exec = provider.exec

    def _exec(handle, command, env, exec_policy):
        seen_cmd.append(list(command))
        return real_exec(handle, command=command, env=env, exec_policy=exec_policy)

    monkeypatch.setattr(provider, "exec", _exec)

    result = _run_local_against(
        provider,
        worktree,
        monkeypatch,
        agent_state_store=store,
        resume=True,
        resume_session_id="cli-1",
        uses_sidebar=True,
    )

    assert result.success, result.error
    assert "--resume" not in seen_cmd[0], (
        "--resume was sent with no transcript in place; the CLI would answer "
        f"'No conversation found with session ID' and waste the turn: {seen_cmd[0]}"
    )


def test_run_local_drops_resume_when_the_import_fails(api, tmp_path, monkeypatch):
    """Restore is best-effort, and the fallback argv must still be launchable.

    openace-run-as.sh's restore is `mv ... || true`. Dropping --resume is the
    right response — but the rebuilt argv has to keep the resolved executable,
    or the turn dies on argv[0] instead.
    """
    worktree = _worktree_at(tmp_path)
    provider = _provider_for(api, worktree, _ScriptedPtyConnection(_turn_frames()))
    store = _agent_state_store(tmp_path)
    store.put("wf-1", "s-1", b"CARRIED\n")

    def _boom(handle, *, cli_session_id, blob):
        raise RuntimeError("execd upload failed")

    monkeypatch.setattr(provider, "import_agent_state", _boom)

    seen_cmd: list = []
    real_exec = provider.exec

    def _exec(handle, command, env, exec_policy):
        seen_cmd.append(list(command))
        return real_exec(handle, command=command, env=env, exec_policy=exec_policy)

    monkeypatch.setattr(provider, "exec", _exec)

    result = _run_local_against(
        provider,
        worktree,
        monkeypatch,
        agent_state_store=store,
        resume=True,
        resume_session_id="cli-1",
        uses_sidebar=True,
    )

    assert result.success, result.error
    assert "--resume" not in seen_cmd[0], f"--resume survived a failed import: {seen_cmd[0]}"
    assert seen_cmd[0][0] == "/usr/local/bin/claude", (
        "the fallback rebuilt argv without the resolved executable, so argv[0] "
        f"changed from the normal path: {seen_cmd[0][0]!r}"
    )


def test_run_local_reports_the_fresh_id_when_the_import_fails(api, tmp_path, monkeypatch):
    """Dropping --resume is only half the fallback; the flag drives more than argv.

    `resume` also gates the session pre-seed, which pins
    `persisted_session_id` to the id being resumed. Leaving it set after a
    failed import makes the pre-seed claim an id the CLI is not using, and
    that pin SKIPS `_resolve_sidebar_session` — so the fresh id the CLI mints
    is never picked up. The turn then SUCCEEDS while reporting the stale id,
    which is the damaging part: the transcript it exports is filed against the
    previous turn's mapping, so the next turn resumes from the wrong slot.

    The stream here reports `cli-2` while the caller asked to resume `cli-1`.
    """
    worktree = _worktree_at(tmp_path)
    provider = _provider_for(api, worktree, _ScriptedPtyConnection(_turn_frames("cli-2")))
    store = _agent_state_store(tmp_path)
    store.put("wf-1", "s-1", b"CARRIED\n")

    def _boom(handle, *, cli_session_id, blob):
        raise RuntimeError("execd upload failed")

    monkeypatch.setattr(provider, "import_agent_state", _boom)

    result = _run_local_against(
        provider,
        worktree,
        monkeypatch,
        agent_state_store=store,
        resume=True,
        resume_session_id="cli-1",
        uses_sidebar=True,
    )

    assert result.success, result.error
    assert result.source_session_id == "cli-2", (
        "the turn reported the id it FAILED to resume rather than the one the "
        f"CLI actually minted: {result.source_session_id!r}. The next turn "
        "would resume from a transcript filed under the wrong session."
    )


def test_run_local_survives_an_export_failure_without_losing_the_turn(api, tmp_path, monkeypatch):
    """Export failure is LOG-ONLY. The agent already did the work.

    openace-run-as.sh's exit-trap capture logs rather than exits, because "an
    exit here would rewrite the status". Discarding a completed milestone
    because its transcript could not be saved trades a quality loss for a
    correctness loss.
    """
    worktree = _worktree_at(tmp_path)
    provider = _provider_for(api, worktree, _ScriptedPtyConnection(_turn_frames()))
    store = _agent_state_store(tmp_path)
    store.put("wf-1", "s-1", b"STALE-FROM-A-PREVIOUS-TURN\n")

    def _boom(handle, *, cli_session_id):
        raise RuntimeError("execd download failed")

    monkeypatch.setattr(provider, "export_agent_state", _boom)

    result = _run_local_against(
        provider, worktree, monkeypatch, agent_state_store=store, uses_sidebar=True
    )

    assert result.success, f"an export failure must not fail the turn: {result.error}"
    assert store.get("wf-1", "s-1") is None, (
        "the stale slot survived a failed export, so the next turn would resume "
        "a transcript that no longer matches the session"
    )


def test_two_turns_on_one_line_carry_the_conversation(api, tmp_path, monkeypatch):
    """The #3237 regression, end to end: turn 1 captures, turn 2 resumes it.

    This is the chain the issue describes. Before this change turn 2 sent
    --resume into an empty HOME, the CLI answered "No conversation found with
    session ID", and the #2035 recovery burned the invocation and retried cold.
    """
    store = _agent_state_store(tmp_path)

    # ── turn 1 ──
    wt1 = _worktree_at(tmp_path, "wt1")
    p1 = _provider_for(api, wt1, _ScriptedPtyConnection(_turn_frames("cli-1")))
    real_export = p1.export_agent_state

    def _export(handle, *, cli_session_id):
        p1.import_agent_state(handle, cli_session_id=cli_session_id, blob=b"HISTORY\n")
        return real_export(handle, cli_session_id=cli_session_id)

    monkeypatch.setattr(p1, "export_agent_state", _export)
    r1 = _run_local_against(p1, wt1, monkeypatch, agent_state_store=store, uses_sidebar=True)
    assert r1.success, r1.error

    # ── turn 2, same session line ──
    wt2 = _worktree_at(tmp_path, "wt2")
    p2 = _provider_for(api, wt2, _ScriptedPtyConnection(_turn_frames("cli-1")))

    placed: list = []
    real_import = p2.import_agent_state

    def _import(handle, *, cli_session_id, blob):
        placed.append(blob)
        return real_import(handle, cli_session_id=cli_session_id, blob=blob)

    monkeypatch.setattr(p2, "import_agent_state", _import)

    seen_cmd: list = []
    real_exec = p2.exec

    def _exec(handle, command, env, exec_policy):
        seen_cmd.append(list(command))
        return real_exec(handle, command=command, env=env, exec_policy=exec_policy)

    monkeypatch.setattr(p2, "exec", _exec)

    r2 = _run_local_against(
        p2,
        wt2,
        monkeypatch,
        agent_state_store=store,
        resume=True,
        resume_session_id="cli-1",
        uses_sidebar=True,
    )

    assert r2.success, r2.error
    assert placed == [b"HISTORY\n"], (
        "turn 2 did not receive turn 1's transcript — the conversation did not "
        f"survive the sandbox boundary: {placed}"
    )
    assert "--resume" in seen_cmd[0], "turn 2 did not resume the carried session"

    # A RESUMED turn must store under the tracking id, not the CLI id. Turn 1
    # cannot distinguish the two (resume_target == session_id when not
    # resuming); turn 2 can, because resume_target is "cli-1" and the tracking
    # id is "s-1". Keying on the CLI id would make turn 3 look up "s-1", find
    # nothing, and silently start cold — the exact bug this feature removes.
    assert store.get("wf-1", "s-1") is not None, (
        "after a resumed turn the transcript is not under the tracking id, so "
        "the next turn on this line would find nothing"
    )
    assert store.get("wf-1", "cli-1") is None, (
        "the transcript was stored under the CLI session id; that id changes on "
        "every force-fresh, so the line would lose its history"
    )


def _route_side(store, workflow_id, status, rows):
    """Do what the web process does: write the terminal status, then purge.

    It does NOT touch the runner's in-memory session, because in the shipped
    topology it cannot: the routes run in the web process and the runner in the
    scheduler process. Modelling the stop by setting `_stopped` directly is
    what hid this failure the first time.
    """
    rows[workflow_id] = None if status is None else {"workflow_id": workflow_id, "status": status}
    store.purge(workflow_id)


def _patch_workflow_rows(monkeypatch, rows):
    class _Repo:
        def __init__(self, *a, **k):
            pass

        def get_workflow(self, workflow_id):
            return rows.get(workflow_id)

    monkeypatch.setattr(
        "app.repositories.autonomous_repo.AutonomousWorkflowRepository", _Repo, raising=False
    )


@pytest.mark.parametrize(
    ("status", "label"),
    [("cancelled", "stopped from the web pod"), (None, "deleted from the web pod")],
)
def test_a_terminal_turn_does_not_resurrect_the_purged_state(
    api, tmp_path, monkeypatch, status, label
):
    """The cross-process shape: the route never touches this thread's Event.

    `_stop_running_task`'s three strategies all resolve through the in-process
    `AutonomousScheduler` singleton or a local PID, so a web-pod stop cannot
    reach a scheduler-pod session — and `delete_workflow` / `delete_batch` do
    not call it at all. The Event therefore stays FALSE in production while the
    route purges the shared directory, and the still-unwinding turn exports on
    top of it.

    So this test deliberately never sets `_stopped`. The only signal is the
    workflow row, which is what the gate must actually consult.
    """
    worktree = _worktree_at(tmp_path)
    provider = _provider_for(api, worktree, _ScriptedPtyConnection(_turn_frames()))
    store = _agent_state_store(tmp_path)
    store.put("wf-1", "s-1", b"EARLIER TURN\n")

    rows = {"wf-1": {"workflow_id": "wf-1", "status": "developing"}}
    _patch_workflow_rows(monkeypatch, rows)

    put_attempted: list[bytes] = []
    real_put = store.put

    def _recording_put(workflow_id, line_id, blob):
        put_attempted.append(blob)
        return real_put(workflow_id, line_id, blob)

    store.put = _recording_put

    real_apply = provider.apply_changes

    def _apply_then_route_acts(handle, project_path):
        result = real_apply(handle, project_path)
        # Give the export something real to find, so a pass here means the gate
        # held rather than that there was nothing to write.
        provider.import_agent_state(
            handle, cli_session_id="cli-1", blob=b"TRANSCRIPT WRITTEN THIS TURN\n"
        )
        _route_side(store, "wf-1", status, rows)
        return result

    monkeypatch.setattr(provider, "apply_changes", _apply_then_route_acts)

    _run_local_against(
        provider,
        worktree,
        monkeypatch,
        agent_state_store=store,
        uses_sidebar=True,
    )

    assert not put_attempted, (
        f"a turn {label} wrote state back AFTER the route purged it, so the "
        f"workflow keeps a transcript nothing will ever reclaim: {put_attempted}"
    )
    assert (
        store.get("wf-1", "s-1") is None
    ), "the purged slot was re-created by the terminal turn's export"


def test_a_live_workflow_still_exports(api, tmp_path, monkeypatch):
    """The gate must not swallow the ordinary case it sits in front of.

    Gating on workflow state is only safe if a RUNNING workflow still writes
    its transcript — otherwise every turn would start cold and the gate would
    have replaced one silent failure with a worse one.
    """
    worktree = _worktree_at(tmp_path)
    provider = _provider_for(api, worktree, _ScriptedPtyConnection(_turn_frames()))
    store = _agent_state_store(tmp_path)

    _patch_workflow_rows(monkeypatch, {"wf-1": {"workflow_id": "wf-1", "status": "developing"}})

    real_apply = provider.apply_changes

    def _apply(handle, project_path):
        result = real_apply(handle, project_path)
        provider.import_agent_state(handle, cli_session_id="cli-1", blob=b"KEEP ME\n")
        return result

    monkeypatch.setattr(provider, "apply_changes", _apply)

    _run_local_against(provider, worktree, monkeypatch, agent_state_store=store, uses_sidebar=True)

    assert store.get("wf-1", "s-1") == b"KEEP ME\n", (
        "a running workflow's transcript was not carried, so its next turn " "would start cold"
    )


def test_an_unreadable_workflow_row_still_exports(api, tmp_path, monkeypatch):
    """Fail-safe direction: unknown means export.

    A resurrected transcript is bounded — the reaper and the next delete both
    reclaim it. A skipped export is not: it loses history the next turn needs
    and starts it cold, which is the failure this change exists to remove. So a
    database that cannot be read must not silently disable the carry.
    """
    worktree = _worktree_at(tmp_path)
    provider = _provider_for(api, worktree, _ScriptedPtyConnection(_turn_frames()))
    store = _agent_state_store(tmp_path)

    class _BrokenRepo:
        def __init__(self, *a, **k):
            raise RuntimeError("database unavailable")

    monkeypatch.setattr(
        "app.repositories.autonomous_repo.AutonomousWorkflowRepository",
        _BrokenRepo,
        raising=False,
    )

    real_apply = provider.apply_changes

    def _apply(handle, project_path):
        result = real_apply(handle, project_path)
        provider.import_agent_state(handle, cli_session_id="cli-1", blob=b"KEEP ME\n")
        return result

    monkeypatch.setattr(provider, "apply_changes", _apply)

    _run_local_against(provider, worktree, monkeypatch, agent_state_store=store, uses_sidebar=True)

    assert store.get("wf-1", "s-1") == b"KEEP ME\n", (
        "an unreadable workflow row disabled the carry, turning a database "
        "blip into a silent cold start"
    )
