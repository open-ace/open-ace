#!/usr/bin/env python3
"""Unit tests for the remote terminal server."""

from __future__ import annotations

import asyncio
import importlib.util
import logging
import subprocess
import sys
from pathlib import Path

import pytest


def load_terminal_server():
    module_path = Path(__file__).resolve().parents[2] / "remote-agent" / "terminal_server.py"
    agent_dir = module_path.parent
    if str(agent_dir) not in sys.path:
        sys.path.insert(0, str(agent_dir))
    spec = importlib.util.spec_from_file_location("terminal_server", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_spawn_pty_uses_pipe_process_on_windows(monkeypatch):
    terminal_server = load_terminal_server()
    monkeypatch.setattr(terminal_server.os, "name", "nt", raising=False)

    calls = {}

    class FakeProc:
        pid = 4321
        stdin = None
        stdout = None
        stderr = None

        def poll(self):
            return None

    def fake_spawn_pipe(cmd, env, work_dir):
        calls["cmd"] = cmd
        calls["env"] = env
        calls["work_dir"] = work_dir
        return FakeProc()

    monkeypatch.setattr(terminal_server, "WORK_DIR", "C:/repo")
    monkeypatch.setattr(terminal_server, "SHELL_CMD", "")
    monkeypatch.setattr(terminal_server, "_build_env", lambda: {"OPENAI_API_KEY": "token"})
    monkeypatch.setattr(terminal_server, "_spawn_pipe_process", fake_spawn_pipe)
    # Job binding must never break spawn: the POSIX stubs return None/False
    # and leave _job_handle=None without raising.
    monkeypatch.setattr(terminal_server, "_create_kill_on_close_job", lambda: None)
    monkeypatch.setattr(terminal_server, "_assign_pid_to_job", lambda handle, pid: False)

    server = terminal_server.SinglePtyTerminalServer()
    monkeypatch.setattr(server, "_update_shell_profile", lambda: None)

    assert server.spawn_pty() is True
    assert server.process is not None
    assert server.process.pid == 4321
    assert server.master_fd is None
    assert calls["work_dir"] == "C:/repo"
    assert calls["cmd"][0] == terminal_server.sys.executable
    assert calls["cmd"][1].endswith("terminal_menu.py")
    # Job binding was attempted but produced no handle (stub returned None).
    assert server._job_handle is None


class _FakeStdin:
    def __init__(self) -> None:
        self.written: list[bytes] = []

    def write(self, data: bytes) -> int:
        self.written.append(data)
        return len(data)

    def flush(self) -> None:
        return None


class _FakeStream:
    """Records close() calls so kill_pty ordering can be asserted."""

    def __init__(self) -> None:
        self.closed = False

    def write(self, data: bytes) -> int:  # for stdin reuse
        return len(data)

    def flush(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True


class _LifecycleProc:
    """Subprocess stand-in exposing the lifecycle surface kill_pty() needs.

    ``_FakeProc`` (below) is the minimal handle used by the input-write tests;
    this one additionally records wait()/stream-close ordering for the
    tree-kill tests.
    """

    def __init__(self, pid: int = 4321, wait_side_effect=None) -> None:
        self.pid = pid
        self.stdin = _FakeStream()
        self.stdout = _FakeStream()
        self.stderr = _FakeStream()
        self.wait_calls: list[float | None] = []
        self._wait_side_effect = wait_side_effect

    def wait(self, timeout=None):
        self.wait_calls.append(timeout)
        if self._wait_side_effect is not None:
            return self._wait_side_effect()
        return 0

    def poll(self):
        return 0


class _FakeProc:
    def __init__(self) -> None:
        self.pid = 4321
        self.stdin = _FakeStdin()
        self.stdout = None
        self.stderr = None


class _FakeWebSocket:
    """Async iterable yielding a fixed list of messages."""

    def __init__(self, messages: list[bytes]) -> None:
        self._messages = list(messages)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._messages:
            raise StopAsyncIteration
        return self._messages.pop(0)

    async def send(self, _data) -> None:
        return None


def _install_job_fakes(monkeypatch, terminal_server):
    """Replace the (platform-stub) Job/taskkill helpers with call recorders."""
    calls = {
        "create": 0,
        "assign": [],
        "close_handle": [],
        "taskkill": [],
    }

    def fake_create():
        calls["create"] += 1
        # Return a fresh opaque token so identity comparisons in close_handle
        # are meaningful.
        return object()

    def fake_assign(handle, pid):
        calls["assign"].append((handle, pid))

    def fake_close(handle):
        calls["close_handle"].append(handle)

    def fake_taskkill(pid):
        calls["taskkill"].append(pid)

    monkeypatch.setattr(terminal_server, "_create_kill_on_close_job", fake_create)
    monkeypatch.setattr(terminal_server, "_assign_pid_to_job", fake_assign)
    monkeypatch.setattr(terminal_server, "_close_native_handle", fake_close)
    monkeypatch.setattr(terminal_server, "_taskkill_tree", fake_taskkill)
    return calls


@pytest.mark.asyncio
async def test_handle_websocket_input_writes_to_pipe_on_windows(monkeypatch):
    """Windows pipe path: keystrokes must reach process.stdin.

    On the pipe path ``master_fd`` is never assigned (stays None) while
    ``self.process`` holds the subprocess. The top-of-loop guard must be
    path-aware so the loop body can fall through to the pipe write.

    Marked ``asyncio`` so ``SinglePtyTerminalServer()`` (whose ``__init__``
    eagerly binds an ``asyncio.Lock``) constructs inside the running event
    loop on Python 3.9, where ``asyncio.run()`` would otherwise close and
    unset the thread's loop and pollute later tests.
    """
    terminal_server = load_terminal_server()

    server = terminal_server.SinglePtyTerminalServer()
    # Reproduce the real Windows state: pipe model, no master_fd, live process.
    monkeypatch.setattr(server, "_uses_pty", False)
    monkeypatch.setattr(server, "master_fd", None)
    monkeypatch.setattr(server, "_pty_alive", True)
    monkeypatch.setattr(server, "process", _FakeProc())

    ws = _FakeWebSocket([b"ls\r", b"exit\r"])
    await server.handle_websocket_input(ws)

    assert server.process.stdin.written == [b"ls\r", b"exit\r"]


@pytest.mark.asyncio
async def test_handle_websocket_input_writes_to_master_fd_on_pty(monkeypatch):
    """PTY path regression guard: the path-aware change must keep PTY writes working."""
    terminal_server = load_terminal_server()

    server = terminal_server.SinglePtyTerminalServer()
    read_fd, write_fd = terminal_server.os.pipe()

    written: list[tuple[int, bytes]] = []
    real_os_write = terminal_server.os.write

    def fake_os_write(fd: int, data: bytes) -> int:
        # Only intercept writes to our master_fd; let everything else through.
        if fd == server.master_fd:
            written.append((fd, data))
            return len(data)
        return real_os_write(fd, data)

    try:
        monkeypatch.setattr(server, "_uses_pty", True)
        monkeypatch.setattr(server, "master_fd", write_fd)
        monkeypatch.setattr(server, "_pty_alive", True)
        monkeypatch.setattr(server, "process", None)
        monkeypatch.setattr(terminal_server.os, "write", fake_os_write)

        ws = _FakeWebSocket([b"pwd\n", b"ls\n"])
        await server.handle_websocket_input(ws)
    finally:
        terminal_server.os.close(read_fd)
        terminal_server.os.close(write_fd)

    assert [data for _fd, data in written] == [b"pwd\n", b"ls\n"]


# --- F1 + F2: kill_pty tree-kill-first, de-duplicated ----------------------


def _make_pipe_server(terminal_server, process, job_handle="unset"):
    server = terminal_server.SinglePtyTerminalServer()
    server._uses_pty = False
    server.master_fd = None
    server._pty_alive = True
    server.process = process
    if job_handle == "unset":
        server._job_handle = None
    else:
        server._job_handle = job_handle
    return server


def test_kill_pty_job_close_skips_redundant_taskkill(monkeypatch):
    """With a Job bound, kill_pty closes the Job handle and does NOT also
    call ``taskkill /T`` (de-duplication). The Job close is the lever that
    closes the stdout write-ends so the relay reader receives EOF."""
    terminal_server = load_terminal_server()
    calls = _install_job_fakes(monkeypatch, terminal_server)

    proc = _LifecycleProc(pid=4242)
    server = _make_pipe_server(terminal_server, proc, job_handle="job-handle-1")

    server.kill_pty()

    # Job handle closed exactly once, and taskkill was NOT invoked (no Job
    # failure, so no fallback needed).
    assert calls["close_handle"] == ["job-handle-1"]
    assert calls["taskkill"] == []
    # Job handle cleared (idempotency) and streams closed as cleanup.
    assert server._job_handle is None
    assert proc.stdin.closed and proc.stdout.closed and proc.stderr.closed
    # Bounded wait was issued so the tree can drain.
    assert proc.wait_calls == [5]


def test_kill_pty_falls_back_to_taskkill_when_no_job(monkeypatch):
    """No Job bound (creation failed earlier): soft-kill falls back to
    ``taskkill /T`` against the live pid."""
    terminal_server = load_terminal_server()
    calls = _install_job_fakes(monkeypatch, terminal_server)

    proc = _LifecycleProc(pid=7777)
    server = _make_pipe_server(terminal_server, proc, job_handle="unset")

    server.kill_pty()

    assert calls["taskkill"] == [7777]
    assert calls["close_handle"] == []
    assert server.process is None


def test_kill_pty_escalates_taskkill_on_wait_timeout(monkeypatch):
    """When the first wait() times out, kill_pty escalates with another
    ``taskkill /T`` (covers the dual-failure-prone soft-kill path)."""
    terminal_server = load_terminal_server()
    calls = _install_job_fakes(monkeypatch, terminal_server)

    attempts = {"n": 0}

    def wait_side_effect():
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise subprocess.TimeoutExpired(cmd="shell", timeout=5)
        return 0

    proc = _LifecycleProc(pid=8888, wait_side_effect=wait_side_effect)
    server = _make_pipe_server(terminal_server, proc, job_handle="unset")

    server.kill_pty()

    # Initial taskkill (no Job) + escalation taskkill after the bounded wait
    # timed out.
    assert calls["taskkill"] == [8888, 8888]
    # Two bounded waits issued (5s each).
    assert proc.wait_calls == [5, 5]


def test_kill_pty_with_job_then_timeout_escalates_once(monkeypatch):
    """Job present but the tree does not exit in time: one escalation taskkill
    (the Job close already happened; taskkill is the fallback signal)."""
    terminal_server = load_terminal_server()
    calls = _install_job_fakes(monkeypatch, terminal_server)

    attempts = {"n": 0}

    def wait_side_effect():
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise subprocess.TimeoutExpired(cmd="shell", timeout=5)
        return 0

    proc = _LifecycleProc(pid=9999, wait_side_effect=wait_side_effect)
    server = _make_pipe_server(terminal_server, proc, job_handle="job-handle-2")

    server.kill_pty()

    assert calls["close_handle"] == ["job-handle-2"]
    assert calls["taskkill"] == [9999]  # escalation only


def test_kill_pty_is_idempotent(monkeypatch):
    """A second kill_pty() (process already cleared) must not double-close the
    Job handle. Guards _run_server finally / signal handler / explicit calls."""
    terminal_server = load_terminal_server()
    calls = _install_job_fakes(monkeypatch, terminal_server)

    proc = _LifecycleProc(pid=4242)
    server = _make_pipe_server(terminal_server, proc, job_handle="job-handle-1")

    server.kill_pty()
    server.kill_pty()  # second call: process is None, Job already cleared

    assert calls["close_handle"] == ["job-handle-1"]
    assert calls["taskkill"] == []


def test_kill_pty_clears_job_when_process_already_none(monkeypatch):
    """If the process branch was skipped (process already torn down) but a Job
    handle lingers, the final idempotency guard must still close it once."""
    terminal_server = load_terminal_server()
    calls = _install_job_fakes(monkeypatch, terminal_server)

    server = terminal_server.SinglePtyTerminalServer()
    server._uses_pty = False
    server.master_fd = None
    server.process = None  # already gone
    server._job_handle = "lingering-handle"

    server.kill_pty()

    assert calls["close_handle"] == ["lingering-handle"]
    assert server._job_handle is None
    assert calls["taskkill"] == []


# --- F2: Job Object binding on spawn ---------------------------------------


def test_spawn_binds_shell_tree_to_job_on_windows(monkeypatch):
    terminal_server = load_terminal_server()
    monkeypatch.setattr(terminal_server.os, "name", "nt", raising=False)
    calls = _install_job_fakes(monkeypatch, terminal_server)

    proc = _LifecycleProc(pid=5555)

    monkeypatch.setattr(terminal_server, "WORK_DIR", "C:/repo")
    monkeypatch.setattr(terminal_server, "SHELL_CMD", "")
    monkeypatch.setattr(terminal_server, "_build_env", lambda: {})
    monkeypatch.setattr(terminal_server, "_spawn_pipe_process", lambda *a, **kw: proc)

    server = terminal_server.SinglePtyTerminalServer()
    monkeypatch.setattr(server, "_update_shell_profile", lambda: None)

    assert server.spawn_pty() is True
    assert calls["create"] == 1
    # Assign was called with the created handle and the spawned pid.
    assert len(calls["assign"]) == 1
    handle, pid = calls["assign"][0]
    assert pid == 5555
    # The bound handle is stored on the instance for later close().
    assert server._job_handle is handle


def test_spawn_survives_job_binding_failure(monkeypatch):
    """Job creation failure must never prevent shell startup: fall back to the
    no-Job path (later soft-kill uses taskkill /T) and clear the handle."""
    terminal_server = load_terminal_server()
    monkeypatch.setattr(terminal_server.os, "name", "nt", raising=False)
    calls = _install_job_fakes(monkeypatch, terminal_server)

    def failing_create():
        calls["create"] += 1
        raise OSError("kernel32 said no")

    monkeypatch.setattr(terminal_server, "_create_kill_on_close_job", failing_create)

    proc = _LifecycleProc(pid=6666)
    monkeypatch.setattr(terminal_server, "WORK_DIR", "C:/repo")
    monkeypatch.setattr(terminal_server, "SHELL_CMD", "")
    monkeypatch.setattr(terminal_server, "_build_env", lambda: {})
    monkeypatch.setattr(terminal_server, "_spawn_pipe_process", lambda *a, **kw: proc)

    server = terminal_server.SinglePtyTerminalServer()
    monkeypatch.setattr(server, "_update_shell_profile", lambda: None)

    assert server.spawn_pty() is True  # spawn still succeeds
    assert server._job_handle is None  # graceful fallback
    assert calls["create"] == 1
    assert calls["assign"] == []  # never reached assign


# --- F1: _run_server finally bounded-wait still cleans up ------------------


@pytest.mark.asyncio
async def test_run_server_finally_calls_kill_pty_when_relay_blocks(monkeypatch):
    """The _run_server finally must not hang on a relay that will not finish,
    and must still call kill_pty().

    The realistic blocked-reader is cancellable (cancel() unblocks it), so this
    exercises the CancelledError branch of the bounded wait. The TimeoutError
    branch is a near-unreachable safety net: an asyncio Task awaiting a
    blocking ``run_in_executor`` read is promptly cancellable, so the
    ``asyncio.TimeoutError`` path is not asserted here (forcing it requires an
    adversarial uncancelable task that would hang ``asyncio.wait_for`` on
    cancel-coalescing). The guarantee under test -- finally proceeds and
    kill_pty runs -- holds in both branches.
    """
    terminal_server = load_terminal_server()

    monkeypatch.setattr(terminal_server.SinglePtyTerminalServer, "spawn_pty", lambda self: True)
    monkeypatch.setattr(terminal_server.SinglePtyTerminalServer, "is_pty_alive", lambda self: False)

    async def _blocking_relay(self):
        # Models a relay blocked on a read that never returns EOF.
        await asyncio.sleep(3600)

    monkeypatch.setattr(
        terminal_server.SinglePtyTerminalServer, "relay_output_loop", _blocking_relay
    )

    killed = []
    monkeypatch.setattr(
        terminal_server.SinglePtyTerminalServer, "kill_pty", lambda self: killed.append(True)
    )

    class _FakeSock:
        def getsockname(self):
            return ("0.0.0.0", 9090)

    class _FakeServe:
        def __init__(self, *args, **kwargs):
            self.sockets = [_FakeSock()]

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return False

    monkeypatch.setattr(terminal_server, "serve", lambda *a, **kw: _FakeServe())

    # Should return promptly (bounded wait, no 8s hang).
    await asyncio.wait_for(terminal_server._run_server(0), timeout=5)

    assert killed == [True]


# --- F3: resize silence removal -------------------------------------------


def test_resize_non_pty_warns_once_and_does_not_write_stdin(monkeypatch, caplog):
    terminal_server = load_terminal_server()

    proc = _LifecycleProc(pid=4321)
    server = terminal_server.SinglePtyTerminalServer()
    server._uses_pty = False
    server.master_fd = None
    server.process = proc
    server._resize_warned = False

    caplog.set_level(logging.INFO, logger="openace-terminal-server")
    with caplog.at_level(logging.INFO, logger="openace-terminal-server"):
        server.resize_pty(120, 40)
        server.resize_pty(200, 50)  # second call must not re-warn

    # Bookkeeping updated to the latest request.
    assert server._pty_cols == 200
    assert server._pty_rows == 50
    # stdin is a pipe, not a tty: resize must never write to it (would pollute
    # the session as command input).
    assert proc.stdin.closed is False
    # Exactly one info-level resize notice (one-shot guard).
    resize_msgs = [
        r
        for r in caplog.records
        if r.levelno == logging.INFO and "resize" in r.getMessage().lower()
    ]
    assert len(resize_msgs) == 1
    assert server._resize_warned is True


def test_resize_pty_path_updates_bookkeeping_without_io(monkeypatch):
    """Unix PTY path with no master_fd yet: resize records the size and does
    not crash (the ioctl is guarded behind master_fd is not None)."""
    terminal_server = load_terminal_server()

    server = terminal_server.SinglePtyTerminalServer()
    server._uses_pty = True
    server.master_fd = None

    server.resize_pty(132, 43)

    assert server._pty_cols == 132
    assert server._pty_rows == 43
