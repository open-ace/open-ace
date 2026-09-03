"""#3323: a Popen-shaped adapter lets ZCodeAppServerSession drive a sandboxed
app-server over PtyWebSocketTransport unchanged.

``ZCodeAppServerSession`` was written against ``subprocess.Popen``: it writes
str and bytes to ``.stdin``, iterates ``.stdout`` / ``.stderr`` line by line,
reads ``.pid`` / ``.returncode``, and calls ``.terminate()`` / ``.kill()`` /
``.wait()``. The adapter presents exactly that surface over the transport.
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.regression, pytest.mark.issue(3323)]


class _FakeTransport:
    """Minimal stand-in for PtyWebSocketTransport's Popen-facing surface."""

    def __init__(self, stdout_lines=(), stderr_lines=()):
        self._out = list(stdout_lines)
        self._err = list(stderr_lines)
        self.written: list[bytes] = []
        self.stdin_closed = False
        self.shutdowns = 0
        self._exit_code = None
        self.waited_with: list = []

    # IO
    def write_stdin(self, data: bytes) -> None:
        assert isinstance(data, bytes), "transport only accepts bytes"
        self.written.append(data)

    def close_stdin(self) -> None:
        self.stdin_closed = True

    def readline_stdout(self) -> bytes:
        return self._out.pop(0) if self._out else b""  # b"" == EOF

    def readline_stderr(self) -> bytes:
        return self._err.pop(0) if self._err else b""

    # completion
    @property
    def pid(self):
        return None

    @property
    def returncode(self):
        return self._exit_code

    def poll(self):
        return self._exit_code

    def wait(self, timeout=None):
        self.waited_with.append(timeout)
        return self._exit_code

    def shutdown(self, grace: float = 5.0) -> None:
        self.shutdowns += 1
        self._exit_code = 0


def _adapter(transport):
    from app.modules.workspace.autonomous.sandbox.opensandbox.popen_adapter import (
        TransportPopenAdapter,
    )

    return TransportPopenAdapter(transport)


def test_stdin_write_accepts_both_str_and_bytes_as_bytes():
    """ZCode writes JSON as str (:952) and encoded payloads as bytes (:969)."""
    t = _FakeTransport()
    proc = _adapter(t)

    proc.stdin.write('{"a":1}\n')  # str
    proc.stdin.write(b"raw-bytes")  # bytes
    proc.stdin.flush()  # must not raise

    assert t.written == [b'{"a":1}\n', b"raw-bytes"]


def test_stdout_iterates_lines_then_stops_on_eof():
    """`for raw in process.stdout` must yield each line and terminate on EOF."""
    t = _FakeTransport(stdout_lines=[b"one\n", b"two\n"])
    proc = _adapter(t)

    assert list(proc.stdout) == [b"one\n", b"two\n"]


def test_stderr_iterates_lines_then_stops_on_eof():
    t = _FakeTransport(stderr_lines=[b"err\n"])
    proc = _adapter(t)

    assert list(proc.stderr) == [b"err\n"]


def test_pid_is_none_and_returncode_reflects_transport():
    t = _FakeTransport()
    proc = _adapter(t)

    assert proc.pid is None
    assert proc.returncode is None
    t._exit_code = 3
    assert proc.returncode == 3


def test_terminate_and_kill_shut_the_transport_down():
    t = _FakeTransport()
    proc = _adapter(t)

    proc.terminate()
    assert t.shutdowns == 1
    # kill is the escalation; shutdown is idempotent so a second call is safe.
    proc.kill()
    assert t.shutdowns == 2


def test_wait_delegates_to_the_transport():
    t = _FakeTransport()
    t._exit_code = 0
    proc = _adapter(t)

    assert proc.wait(timeout=5) == 0
    assert t.waited_with == [5]


# ── the _run_zcode_appserver sandbox lifecycle wiring ─────────────────

import sys  # noqa: E402
import types  # noqa: E402
from pathlib import Path  # noqa: E402
from unittest.mock import MagicMock  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parents[2]


class _FakeExecHandle:
    def __init__(self):
        self.sandbox_id = "sb-1"


class _FakeSandboxProvider:
    """Records the ephemeral lifecycle and hands back a pidless transport."""

    from app.modules.workspace.autonomous.sandbox.provider import AGENT_STATE_CARRIED

    agent_state_persistence = AGENT_STATE_CARRIED

    def __init__(self, transport):
        self._transport = transport
        self.handle = types.SimpleNamespace(sandbox_id="sb-1", generation=1)
        self.exec_handle = _FakeExecHandle()
        self.calls: list[str] = []

    def create(self, spec):
        self.calls.append("create")
        return self.handle

    def upload_workspace(self, handle, changeset):
        self.calls.append("upload_workspace")

    def agent_turn_policy(self, *, prompt, model, env):
        return object()

    def exec(self, handle, *, command, env, exec_policy):
        self.calls.append("exec")
        return self.exec_handle

    def get_transport(self, exec_handle):
        return self._transport

    def apply_changes(self, handle, project_path):
        self.calls.append("apply_changes")

    def destroy(self, handle):
        self.calls.append("destroy")

    def stop(self, exec_handle):
        self.calls.append("stop")


def _sandbox_zcode_runner(monkeypatch, provider, captured):
    """A runner whose isolation gate selects *provider* for a zcode turn."""
    from app.modules.workspace.autonomous import agent_runner as ar

    runner = ar.AutonomousAgentRunner.__new__(ar.AutonomousAgentRunner)
    runner._local_sessions = {}
    runner._activity_callback = None
    runner._on_pid_registered = None
    runner._on_pid_cleared = None
    runner._sandbox_provider = provider
    runner.remote_session_manager = None
    runner.session_manager = None
    runner._resolve_tenant_for_isolation = lambda user_id, *, cli_tool, adapter: 42
    runner._select_sandbox_provider = lambda *a, **k: provider
    runner._resolve_sandbox_generation = lambda workflow_id: 1
    runner._load_task_policy = lambda: None
    runner._notify_sandbox_created = lambda *a, **k: None
    runner._create_workflow_session = lambda *a, **k: None
    runner._encode_project_path = lambda p: "-workspace"
    runner._build_agent_env = lambda *a, **k: {"HOME": "/home/agent"}
    runner._is_cross_user = lambda system_account: False
    runner._wrap_agent_cmd = lambda cmd, project_path, system_account, *a, **k: (cmd, project_path)

    fake_adapter = MagicMock()
    fake_adapter.build_start_args.return_value = ["node", "zcode.cjs"]
    fake_adapter.supports_stdin_input.return_value = False

    class _StubZCodeSession:
        def __init__(self, **kwargs):
            captured["process"] = kwargs.get("process")
            self._cli_session_id = "sess-zcode-1"

        def start(self, **kwargs):
            return True

        def send_message(self, prompt, timeout=None):
            return True

        def wait_turn(self, timeout=None):
            return True

        def stop(self):
            pass

    remote_agent_dir = str(_REPO_ROOT / "remote-agent")
    if remote_agent_dir not in sys.path:
        sys.path.insert(0, remote_agent_dir)
    cli_adapters_mod = types.ModuleType("cli_adapters")
    cli_adapters_mod.get_adapter = lambda name: fake_adapter
    monkeypatch.setitem(sys.modules, "cli_adapters", cli_adapters_mod)
    zcode_mod = types.ModuleType("zcode_app_server")
    zcode_mod.ZCodeAppServerSession = _StubZCodeSession
    monkeypatch.setitem(sys.modules, "zcode_app_server", zcode_mod)
    return runner


def test_sandbox_zcode_runs_the_full_provider_lifecycle_in_order(monkeypatch):
    """create → upload_workspace → exec → apply_changes → destroy, in order.

    Must fail if the sandbox wiring is removed — a run that stayed on the local
    Popen path would call none of these.
    """
    transport = _FakeTransport(stdout_lines=[], stderr_lines=[])
    provider = _FakeSandboxProvider(transport)
    captured: dict = {}
    runner = _sandbox_zcode_runner(monkeypatch, provider, captured)

    result = runner._run_zcode_appserver(
        session_id="wf-zc",
        cli_tool="zcode",
        model="GLM-5",
        project_path="/workspace",
        prompt="do it",
        permission_mode="yolo",
        timeout=30,
        workflow_id="wf-1",
        user_id=1,
        workspace_type="local",
    )

    assert result.success, result.error
    assert provider.calls[:3] == ["create", "upload_workspace", "exec"], provider.calls
    assert "apply_changes" in provider.calls and "destroy" in provider.calls
    assert provider.calls.index("apply_changes") < provider.calls.index("destroy"), provider.calls


def test_sandbox_zcode_session_is_driven_over_the_transport_adapter(monkeypatch):
    """The session gets a Popen-shaped adapter (pidless), not a real Popen."""
    from app.modules.workspace.autonomous.sandbox.opensandbox.popen_adapter import (
        TransportPopenAdapter,
    )

    transport = _FakeTransport()
    provider = _FakeSandboxProvider(transport)
    captured: dict = {}
    runner = _sandbox_zcode_runner(monkeypatch, provider, captured)

    runner._run_zcode_appserver(
        session_id="wf-zc",
        cli_tool="zcode",
        model="GLM-5",
        project_path="/workspace",
        prompt="do it",
        permission_mode="yolo",
        timeout=30,
        workflow_id="wf-1",
        user_id=1,
        workspace_type="local",
    )

    assert isinstance(captured["process"], TransportPopenAdapter)
    assert captured["process"].pid is None


def test_sandbox_zcode_tracker_routes_stop_through_the_provider(monkeypatch):
    """The tracker carries provider+exec_handle and NO local process, so
    stop_session reaches provider.stop instead of os.getpgid(None)."""
    seen_tracker: dict = {}
    transport = _FakeTransport()
    provider = _FakeSandboxProvider(transport)
    captured: dict = {}
    runner = _sandbox_zcode_runner(monkeypatch, provider, captured)

    # Capture the tracker the runner registers, from INSIDE the turn.
    class _Spy(dict):
        def __setitem__(self, k, v):
            seen_tracker["t"] = v
            super().__setitem__(k, v)

    runner._local_sessions = _Spy()

    runner._run_zcode_appserver(
        session_id="wf-zc",
        cli_tool="zcode",
        model="GLM-5",
        project_path="/workspace",
        prompt="do it",
        permission_mode="yolo",
        timeout=30,
        workflow_id="wf-1",
        user_id=1,
        workspace_type="local",
    )

    tracker = seen_tracker["t"]
    assert tracker.process is None, "a real process on the tracker would hit os.getpgid(None)"
    assert tracker.sandbox_provider is provider
    assert tracker.exec_handle is provider.exec_handle


def test_sandbox_zcode_refuses_resume_rather_than_cold_starting(monkeypatch):
    """ZCode carry is deferred (#3323): a resuming sandbox turn is refused
    before the pod is even created, never a silent cold start."""
    transport = _FakeTransport()
    provider = _FakeSandboxProvider(transport)
    captured: dict = {}
    runner = _sandbox_zcode_runner(monkeypatch, provider, captured)

    result = runner._run_zcode_appserver(
        session_id="wf-zc",
        cli_tool="zcode",
        model="GLM-5",
        project_path="/workspace",
        prompt="continue",
        permission_mode="yolo",
        timeout=30,
        workflow_id="wf-1",
        user_id=1,
        workspace_type="local",
        resume=True,
        resume_session_id="sess-prev",
    )

    assert result.success is False
    assert result.error_code == "agent_state_unavailable"
    assert provider.calls == [], "no pod may be created for a refused resume"
