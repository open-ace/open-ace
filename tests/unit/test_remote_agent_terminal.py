#!/usr/bin/env python3
"""Unit tests for remote-agent terminal management helpers."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def load_agent_module():
    module_path = Path(__file__).resolve().parents[2] / "remote-agent" / "agent.py"
    agent_dir = module_path.parent
    if str(agent_dir) in sys.path:
        sys.path.remove(str(agent_dir))
    sys.path.insert(0, str(agent_dir))
    sys.modules.pop("config", None)
    spec = importlib.util.spec_from_file_location("remote_agent", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_read_terminal_port_returns_ready_port():
    agent_module = load_agent_module()
    agent = agent_module.RemoteAgent.__new__(agent_module.RemoteAgent)

    class FakeStdout:
        def readline(self):
            return b"READY:31337\n"

    proc = type("Proc", (), {"stdout": FakeStdout()})()

    assert agent._read_terminal_port(proc, "terminal-123") == 31337


def test_read_terminal_port_rejects_non_ready_line():
    agent_module = load_agent_module()
    agent = agent_module.RemoteAgent.__new__(agent_module.RemoteAgent)

    class FakeStdout:
        def readline(self):
            return b"BOOTING\n"

    proc = type("Proc", (), {"stdout": FakeStdout()})()

    assert agent._read_terminal_port(proc, "terminal-123") is None
