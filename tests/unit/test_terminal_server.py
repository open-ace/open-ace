#!/usr/bin/env python3
"""Unit tests for the remote terminal server."""

from __future__ import annotations

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
