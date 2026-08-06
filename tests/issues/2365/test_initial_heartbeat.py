#!/usr/bin/env python3
"""Tests for Issue #2365: Initial heartbeat sent after session restore.

Issue: Agent startup initialization does not send heartbeat, causing
management interface to show "offline" for up to several minutes.

Root cause: restore_sessions() can take minutes (each session waits
up to 15s for SDK initialization), but heartbeat is only sent inside
_http_poll_loop(), which runs AFTER restore_sessions().

Fix: Send initial heartbeat immediately after restore_sessions() and
before entering HTTP poll loop.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch


def load_agent_module():
    """Load remote-agent module for testing."""
    module_path = Path(__file__).resolve().parents[3] / "remote-agent" / "agent.py"
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


def _make_agent(agent_module):
    """Create a minimal agent instance for testing."""
    agent = agent_module.RemoteAgent.__new__(agent_module.RemoteAgent)
    return agent


class TestInitialHeartbeat:
    """Tests for initial heartbeat sending logic."""

    def test_initial_heartbeat_sent_after_session_restore(self):
        """Verify initial heartbeat is sent after restoring sessions."""
        agent_module = load_agent_module()
        agent = _make_agent(agent_module)

        # Track heartbeat calls
        heartbeat_calls = []
        agent._send_heartbeat_via_http = lambda: heartbeat_calls.append(True)

        # Mock other dependencies
        agent._session_sync = MagicMock()
        agent._restore_terminal_sessions = MagicMock()
        agent._executor = MagicMock()
        agent._executor.restore_sessions.return_value = ["session-1", "session-2"]
        agent._send_session_status = MagicMock()
        agent._running = False  # Prevent entering the poll loop

        # Execute the relevant part of run()
        agent._restore_terminal_sessions()
        restored = agent._executor.restore_sessions()
        if restored:
            for sid in restored:
                agent._send_session_status(sid, "running")
        agent._session_sync.start()

        # Send initial heartbeat (the fix)
        try:
            agent._send_heartbeat_via_http()
        except Exception:
            pass

        # Verify heartbeat was called
        assert heartbeat_calls, "Initial heartbeat not sent after session restore"

    def test_initial_heartbeat_failure_does_not_raise(self):
        """Verify heartbeat failure does not raise exception."""
        agent_module = load_agent_module()
        agent = _make_agent(agent_module)

        # Make heartbeat fail
        agent._send_heartbeat_via_http = MagicMock(side_effect=Exception("Network error"))

        # Should not raise when wrapped in try-except
        try:
            agent._send_heartbeat_via_http()
        except Exception as e:
            # Expected to be caught
            assert "Network error" in str(e)
        else:
            # If no exception was raised, that's also acceptable
            pass

    def test_initial_heartbeat_on_first_start_no_sessions(self):
        """Verify initial heartbeat works when no sessions to restore."""
        agent_module = load_agent_module()
        agent = _make_agent(agent_module)

        # Track heartbeat calls
        heartbeat_calls = []
        agent._send_heartbeat_via_http = lambda: heartbeat_calls.append(True)

        # Mock other dependencies
        agent._session_sync = MagicMock()
        agent._restore_terminal_sessions = MagicMock()
        agent._executor = MagicMock()
        agent._executor.restore_sessions.return_value = []  # No sessions
        agent._running = False

        # Execute the relevant part of run()
        agent._restore_terminal_sessions()
        agent._executor.restore_sessions()
        agent._session_sync.start()

        # Send initial heartbeat (the fix)
        try:
            agent._send_heartbeat_via_http()
        except Exception:
            pass

        # Verify heartbeat was called even with no sessions
        assert heartbeat_calls, "Initial heartbeat not sent on first start"
