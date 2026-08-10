"""
Unit tests for Issue #2411: send_message command should send command_response ack.

Tests verify that _cmd_send_message sends a command_response ack after
handling the send_message command, preventing duplicate message delivery.
"""

from __future__ import annotations

import importlib.util as _importlib_util
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

# Make the remote-agent package importable without installing it.
# NOTE: Insert at position 0 so remote-agent modules take precedence
# over scripts/shared/config.py which conflicts on "config" module name.
_agent_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "remote-agent"))
sys.path.insert(0, _agent_path)

# Patch heavy / external dependencies before importing agent.py
sys.modules.setdefault("requests", MagicMock())
sys.modules.setdefault("cli_settings", MagicMock())
sys.modules.setdefault("executor", MagicMock())
sys.modules.setdefault("session_sync", MagicMock())
sys.modules.setdefault("system_info", MagicMock())

# Pre-import config from remote-agent to avoid conflict with scripts/shared/config.py
_config_path = os.path.join(_agent_path, "config.py")
_config_spec = _importlib_util.spec_from_file_location("config", _config_path)
assert _config_spec is not None and _config_spec.loader is not None
_config_module = _importlib_util.module_from_spec(_config_spec)
sys.modules["config"] = _config_module
_config_spec.loader.exec_module(_config_module)

from agent import RemoteAgent  # noqa: E402


def _make_agent() -> RemoteAgent:
    """Create a RemoteAgent with a mock config, bypassing real __init__."""
    config = MagicMock()
    config.machine_id = "test-machine-id"
    config.server_url = "http://localhost:19888"
    config.hostname = "testhost"
    config.reconnect_base_delay = 1
    config.reconnect_max_delay = 60

    # Bypass __init__ entirely to avoid subprocess / socket side-effects
    with patch.object(RemoteAgent, "__init__", lambda self: None):
        agent = RemoteAgent()

    agent.config = config
    agent._http_send = MagicMock()
    agent._vscode_processes = {}
    agent._vscode_tokens = {}
    agent._vscode_ports = {}
    agent._vscode_passwords = {}

    # Mock executor for send_message
    agent._executor = MagicMock()

    return agent


def _get_http_send_calls(agent: RemoteAgent) -> list[dict]:
    """Return all call args to _http_send as message dicts."""
    if not agent._http_send.called:
        return []
    return [call[0][0] for call in agent._http_send.call_args_list]


class TestCmdSendMessageAck:
    """Tests for RemoteAgent._cmd_send_message command_response ack."""

    def test_send_message_sends_command_response_on_success(self):
        """Verify command_response ack is sent after successful send_message."""
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
        # Should have exactly one command_response
        ack_calls = [c for c in calls if c.get("type") == "command_response"]
        assert len(ack_calls) == 1, f"Expected 1 command_response, got {len(ack_calls)}"

        ack = ack_calls[0]
        assert ack["machine_id"] == "test-machine-id"
        assert ack["request_id"] == "cmd-123"
        assert ack["result"]["success"] is True
        assert ack["result"].get("error") is None

    def test_send_message_sends_command_response_on_failure(self):
        """Verify command_response ack is sent even when send_message fails."""
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
        assert len(ack_calls) == 1, f"Expected 1 command_response, got {len(ack_calls)}"

        ack = ack_calls[0]
        assert ack["request_id"] == "cmd-456"
        assert ack["result"]["success"] is False
        assert ack["result"]["error"] == "Session not found"

    def test_send_message_uses_request_id_fallback(self):
        """Verify request_id is used as fallback when command_id is absent."""
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

        ack = ack_calls[0]
        assert ack["request_id"] == "req-789"

    def test_send_message_prefers_command_id_over_request_id(self):
        """Verify command_id takes precedence over request_id when both present."""
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

        ack = ack_calls[0]
        assert ack["request_id"] == "cmd-preferred"

    def test_send_message_no_ack_without_request_id(self):
        """Verify no command_response ack when neither command_id nor request_id present."""
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
        assert len(ack_calls) == 0, f"Expected 0 command_response, got {len(ack_calls)}"

    def test_send_message_ack_prevents_duplicate_delivery(self):
        """
        Verify that sending command_response ack prevents duplicate delivery.

        This is a conceptual test documenting the fix for Issue #2411:
        - Without the ack, the command remains in 'delivered' status
        - After COMMAND_CLAIM_TIMEOUT_SECONDS (5 min), the command is re-claimed
        - The agent receives the same send_message command again (duplicate)

        With the ack:
        - The server marks the command as 'responded'
        - The command is not re-claimed after the timeout
        - No duplicate message delivery
        """
        agent = _make_agent()
        agent._executor.send_message.return_value = {"success": True}

        # Simulate receiving a send_message command
        agent._cmd_send_message(
            {
                "command_id": "cmd-duplicate-test",
                "session_id": "session-test",
                "content": "test duplicate prevention",
            }
        )

        calls = _get_http_send_calls(agent)
        ack_calls = [c for c in calls if c.get("type") == "command_response"]

        # The ack being sent is the fix for Issue #2411
        # Server will mark command as 'responded', preventing re-claim
        assert (
            len(ack_calls) == 1
        ), "command_response ack must be sent to prevent duplicate delivery"
