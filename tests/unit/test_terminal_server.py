#!/usr/bin/env python3
"""Unit tests for the remote terminal server."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path


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

    server = terminal_server.SinglePtyTerminalServer()
    monkeypatch.setattr(server, "_update_shell_profile", lambda: None)

    assert server.spawn_pty() is True
    assert server.process is not None
    assert server.process.pid == 4321
    assert server.master_fd is None
    assert calls["work_dir"] == "C:/repo"
    assert calls["cmd"][0] == terminal_server.sys.executable
    assert calls["cmd"][1].endswith("terminal_menu.py")


class _FakeStdin:
    def __init__(self) -> None:
        self.written: list[bytes] = []

    def write(self, data: bytes) -> int:
        self.written.append(data)
        return len(data)

    def flush(self) -> None:
        return None


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


def test_handle_websocket_input_writes_to_pipe_on_windows(monkeypatch):
    """Windows pipe path: keystrokes must reach process.stdin.

    On the pipe path ``master_fd`` is never assigned (stays None) while
    ``self.process`` holds the subprocess. The top-of-loop guard must be
    path-aware so the loop body can fall through to the pipe write.
    """
    terminal_server = load_terminal_server()

    server = terminal_server.SinglePtyTerminalServer()
    # Reproduce the real Windows state: pipe model, no master_fd, live process.
    monkeypatch.setattr(server, "_uses_pty", False)
    monkeypatch.setattr(server, "master_fd", None)
    monkeypatch.setattr(server, "_pty_alive", True)
    monkeypatch.setattr(server, "process", _FakeProc())

    ws = _FakeWebSocket([b"ls\r", b"exit\r"])
    asyncio.run(server.handle_websocket_input(ws))

    assert server.process.stdin.written == [b"ls\r", b"exit\r"]


def test_handle_websocket_input_writes_to_master_fd_on_pty(monkeypatch):
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
        asyncio.run(server.handle_websocket_input(ws))
    finally:
        terminal_server.os.close(read_fd)
        terminal_server.os.close(write_fd)

    assert [data for _fd, data in written] == [b"pwd\n", b"ls\n"]
