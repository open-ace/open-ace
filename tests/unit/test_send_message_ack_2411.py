"""
Unit tests for Issue #2411: send_message command should send command_response ack.

Tests verify that _cmd_send_message sends a command_response ack after
handling the send_message command, preventing duplicate message delivery.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from unittest.mock import MagicMock, patch

import pytest


def _import_remote_agent():
    """Import RemoteAgent from remote-agent with proper module handling.

    This function handles the config module conflict between
    remote-agent/config.py and scripts/shared/config.py.
    """
    agent_path = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "remote-agent")
    )
    path_added = False
    if agent_path not in sys.path:
        sys.path.insert(0, agent_path)
        path_added = True

    # Save current config module
    original_config = sys.modules.get("config")

    # Track which mock modules we added
    mock_modules = ["requests", "cli_settings", "executor", "session_sync", "system_info"]
    original_modules = {name: sys.modules.get(name) for name in mock_modules}

    try:
        # Load remote-agent/config.py temporarily
        config_path = os.path.join(agent_path, "config.py")
        spec = importlib.util.spec_from_file_location("config_remote_agent_2411", config_path)
        if spec and spec.loader:
            config_module = importlib.util.module_from_spec(spec)
            sys.modules["config"] = config_module
            spec.loader.exec_module(config_module)

        # Mock heavy dependencies
        for name in mock_modules:
            sys.modules.setdefault(name, MagicMock())

        # Import agent using the already-imported importlib module
        agent = importlib.import_module("agent")
        return agent.RemoteAgent

    finally:
        # Restore original config module
        if original_config is not None:
            sys.modules["config"] = original_config
        else:
            # No original config, need to load scripts/shared/config.py
            sys.modules.pop("config", None)
            # Explicitly load scripts/shared/config.py
            shared_path = os.path.abspath(
                os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "shared")
            )
            shared_config_path = os.path.join(shared_path, "config.py")
            if os.path.exists(shared_config_path):
                shared_spec = importlib.util.spec_from_file_location("config", shared_config_path)
                if shared_spec and shared_spec.loader:
                    shared_config_module = importlib.util.module_from_spec(shared_spec)
                    sys.modules["config"] = shared_config_module
                    shared_spec.loader.exec_module(shared_config_module)

        # Restore original mock modules (remove our mocks if we added them)
        for name in mock_modules:
            if original_modules[name] is None and name in sys.modules:
                # We added this mock, remove it
                sys.modules.pop(name, None)

        # Remove agent_path from sys.path to avoid affecting other tests
        if path_added and agent_path in sys.path:
            sys.path.remove(agent_path)


# Import RemoteAgent at module level but with proper handling
RemoteAgent = _import_remote_agent()


def _make_agent():
    """Create a RemoteAgent with a mock config, bypassing real __init__."""
    config = MagicMock()
    config.machine_id = "test-machine-id"
    config.server_url = "http://localhost:19888"
    config.hostname = "testhost"
    config.reconnect_base_delay = 1
    config.reconnect_max_delay = 60

    with patch.object(RemoteAgent, "__init__", lambda self: None):
        agent = RemoteAgent()

    agent.config = config
    agent._http_send = MagicMock()
    agent._vscode_processes = {}
    agent._vscode_tokens = {}
    agent._vscode_ports = {}
    agent._vscode_passwords = {}
    agent._executor = MagicMock()

    return agent


def _get_http_send_calls(agent):
    """Return all call args to _http_send as message dicts."""
    if not agent._http_send.called:
        return []
    return [call[0][0] for call in agent._http_send.call_args_list]


class TestCmdSendMessageAck:
    """Tests for RemoteAgent._cmd_send_message command_response ack."""

    def test_send_message_sends_command_response_on_success(self):
        agent = _make_agent()
        agent._executor.send_message.return_value = {"success": True}

        agent._cmd_send_message(
            {
                "command_id": "cmd-123",
                "session_id": "session-abc",
                "content": "Hello, world!",
            }
        )

        calls = _get_http_send_calls(agent)
        ack_calls = [c for c in calls if c.get("type") == "command_response"]
        assert len(ack_calls) == 1

        ack = ack_calls[0]
        assert ack["machine_id"] == "test-machine-id"
        assert ack["request_id"] == "cmd-123"
        assert ack["result"]["success"] is True
        assert ack["result"].get("error") is None

    def test_send_message_sends_command_response_on_failure(self):
        agent = _make_agent()
        agent._executor.send_message.return_value = {
            "success": False,
            "error": "Session not found",
        }

        agent._cmd_send_message(
            {
                "command_id": "cmd-456",
                "session_id": "session-xyz",
                "content": "test message",
            }
        )

        calls = _get_http_send_calls(agent)
        ack_calls = [c for c in calls if c.get("type") == "command_response"]
        assert len(ack_calls) == 1

        ack = ack_calls[0]
        assert ack["request_id"] == "cmd-456"
        assert ack["result"]["success"] is False
        assert ack["result"]["error"] == "Session not found"

    def test_send_message_uses_request_id_fallback(self):
        agent = _make_agent()
        agent._executor.send_message.return_value = {"success": True}

        agent._cmd_send_message(
            {
                "request_id": "req-789",
                "session_id": "session-def",
                "content": "fallback test",
            }
        )

        calls = _get_http_send_calls(agent)
        ack_calls = [c for c in calls if c.get("type") == "command_response"]
        assert len(ack_calls) == 1
        assert ack_calls[0]["request_id"] == "req-789"

    def test_send_message_prefers_command_id_over_request_id(self):
        agent = _make_agent()
        agent._executor.send_message.return_value = {"success": True}

        agent._cmd_send_message(
            {
                "command_id": "cmd-preferred",
                "request_id": "req-fallback",
                "session_id": "session-ghi",
                "content": "priority test",
            }
        )

        calls = _get_http_send_calls(agent)
        ack_calls = [c for c in calls if c.get("type") == "command_response"]
        assert len(ack_calls) == 1
        assert ack_calls[0]["request_id"] == "cmd-preferred"

    def test_send_message_no_ack_without_request_id(self):
        agent = _make_agent()
        agent._executor.send_message.return_value = {"success": True}

        agent._cmd_send_message(
            {
                "session_id": "session-jkl",
                "content": "no request id",
            }
        )

        calls = _get_http_send_calls(agent)
        ack_calls = [c for c in calls if c.get("type") == "command_response"]
        assert len(ack_calls) == 0

    def test_send_message_ack_prevents_duplicate_delivery(self):
        agent = _make_agent()
        agent._executor.send_message.return_value = {"success": True}

        agent._cmd_send_message(
            {
                "command_id": "cmd-duplicate-test",
                "session_id": "session-test",
                "content": "test duplicate prevention",
            }
        )

        calls = _get_http_send_calls(agent)
        ack_calls = [c for c in calls if c.get("type") == "command_response"]
        assert len(ack_calls) == 1
