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


class _ScriptedStdout:
    """Return canned lines from readline(), then EOF (empty bytes)."""

    def __init__(self, lines):
        self._lines = list(lines)

    def readline(self):
        if not self._lines:
            return b""
        return self._lines.pop(0)


def _make_agent(agent_module):
    agent = agent_module.RemoteAgent.__new__(agent_module.RemoteAgent)
    return agent


def test_read_terminal_port_returns_ready_port():
    agent_module = load_agent_module()
    agent = _make_agent(agent_module)

    proc = type("Proc", (), {"stdout": _ScriptedStdout([b"READY:31337\n"])})()

    assert agent._read_terminal_port(proc, "terminal-123") == 31337


def test_read_terminal_port_rejects_non_ready_line():
    """A single non-READY banner line followed by EOF yields None."""
    agent_module = load_agent_module()
    agent = _make_agent(agent_module)

    proc = type("Proc", (), {"stdout": _ScriptedStdout([b"BOOTING\n"])})()

    assert agent._read_terminal_port(proc, "terminal-123") is None


def test_read_terminal_port_skips_banners_to_ready():
    """Startup banners before READY:<port> must be skipped, not misread as
    failure (the old single-readline() captured only the first line)."""
    agent_module = load_agent_module()
    agent = _make_agent(agent_module)

    proc = type(
        "Proc",
        (),
        {
            "stdout": _ScriptedStdout(
                [b"BOOTING terminal server...\n", b"loading profile...\n", b"READY:31337\n"]
            )
        },
    )()

    assert agent._read_terminal_port(proc, "terminal-123") == 31337


def test_read_terminal_port_eof_before_ready_returns_none():
    """Process closes stdout (EOF) before printing READY -> give up, None."""
    agent_module = load_agent_module()
    agent = _make_agent(agent_module)

    proc = type(
        "Proc",
        (),
        {"stdout": _ScriptedStdout([b"BOOTING\n", b"almost there\n"])},
    )()  # two banners then EOF

    assert agent._read_terminal_port(proc, "terminal-123") is None


def test_read_terminal_port_rejects_non_numeric_ready():
    """A line beginning with READY: but carrying non-numeric text is invalid;
    int() raises and the caller returns None."""
    agent_module = load_agent_module()
    agent = _make_agent(agent_module)

    proc = type("Proc", (), {"stdout": _ScriptedStdout([b"READY:abc\n"])})()

    assert agent._read_terminal_port(proc, "terminal-123") is None


def test_read_terminal_port_none_stdout_returns_none():
    """No stdout pipe at all -> immediate None, no crash."""
    agent_module = load_agent_module()
    agent = _make_agent(agent_module)

    proc = type("Proc", (), {"stdout": None})()

    assert agent._read_terminal_port(proc, "terminal-123") is None
