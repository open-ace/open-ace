"""
Tests for Issue #883: Remote Agent client token management.

Covers:
- config.py: save_agent_token() persistence
- agent.py: 401 revoked detection and reconnect stop
- agent.py: rotate_token command handling
"""

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Add remote-agent directory to path so we can import config module
AGENT_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "remote-agent")
AGENT_DIR = os.path.abspath(AGENT_DIR)
if AGENT_DIR not in sys.path:
    sys.path.insert(0, AGENT_DIR)

from config import AgentConfig

# ==================== config.py tests ====================


class TestSaveAgentToken:
    """Test save_agent_token() in AgentConfig."""

    def test_save_agent_token_persists_to_file(self, tmp_path):
        """save_agent_token should write agent_token to config.json."""
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"server_url": "http://localhost:19888"}))

        config = AgentConfig(config_path=str(config_file))
        config.save_agent_token("abc123def456")

        # Verify in-memory
        assert config.agent_token == "abc123def456"

        # Verify persisted to disk
        with open(config_file) as f:
            data = json.load(f)
        assert data["agent_token"] == "abc123def456"

    def test_save_agent_token_overwrites_existing(self, tmp_path):
        """save_agent_token should replace an existing agent_token."""
        config_file = tmp_path / "config.json"
        config_file.write_text(
            json.dumps(
                {
                    "server_url": "http://localhost:19888",
                    "agent_token": "old_token",
                }
            )
        )

        config = AgentConfig(config_path=str(config_file))
        assert config.agent_token == "old_token"

        config.save_agent_token("new_token_123")
        assert config.agent_token == "new_token_123"

        # Verify on disk
        with open(config_file) as f:
            data = json.load(f)
        assert data["agent_token"] == "new_token_123"

    def test_save_agent_token_preserves_other_fields(self, tmp_path):
        """save_agent_token should not remove other config fields."""
        config_file = tmp_path / "config.json"
        original = {
            "server_url": "http://example.com",
            "machine_id": "test-machine-id",
            "machine_name": "test-box",
            "heartbeat_interval": 30,
        }
        config_file.write_text(json.dumps(original))

        config = AgentConfig(config_path=str(config_file))
        config.save_agent_token("tok_abc")

        with open(config_file) as f:
            data = json.load(f)

        assert data["agent_token"] == "tok_abc"
        assert data["server_url"] == "http://example.com"
        assert data["machine_id"] == "test-machine-id"
        assert data["machine_name"] == "test-box"
        assert data["heartbeat_interval"] == 30


# ==================== agent.py tests ====================


class TestAgent401Handling:
    """Test 401 handling in RemoteAgent._http_send()."""

    def _make_agent(self, tmp_path):
        """Create a RemoteAgent with a temp config file."""
        config_file = tmp_path / "config.json"
        config_file.write_text(
            json.dumps(
                {
                    "server_url": "http://localhost:9999",
                    "machine_id": "test-machine-12345678",
                    "agent_token": "valid_token",
                }
            )
        )
        # Import agent module
        from agent import RemoteAgent

        config = AgentConfig(config_path=str(config_file))
        agent = RemoteAgent(config=config)
        return agent

    @patch("agent.requests.post")
    def test_http_send_401_sets_token_revoked(self, mock_post, tmp_path):
        """Receiving 401 should set _token_revoked flag."""
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.json.return_value = {"error": "Invalid or revoked Bearer token"}
        mock_post.return_value = mock_response

        agent = self._make_agent(tmp_path)
        assert agent._token_revoked is False

        result = agent._http_send({"type": "heartbeat", "machine_id": "test"})
        assert result is None
        assert agent._token_revoked is True

    @patch("agent.requests.post")
    def test_http_send_200_does_not_set_revoked(self, mock_post, tmp_path):
        """Successful 200 response should not set _token_revoked."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"pending_commands": []}
        mock_post.return_value = mock_response

        agent = self._make_agent(tmp_path)
        result = agent._http_send({"type": "heartbeat", "machine_id": "test"})
        assert result is not None
        assert agent._token_revoked is False

    @patch("agent.requests.post")
    def test_http_send_500_does_not_set_revoked(self, mock_post, tmp_path):
        """Server error (500) should not set _token_revoked (temporary issue)."""
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"
        mock_post.return_value = mock_response

        agent = self._make_agent(tmp_path)
        result = agent._http_send({"type": "heartbeat", "machine_id": "test"})
        assert result is None
        assert agent._token_revoked is False


class TestAgentRotateToken:
    """Test rotate_token command handling in RemoteAgent."""

    def _make_agent(self, tmp_path):
        """Create a RemoteAgent with a temp config file."""
        config_file = tmp_path / "config.json"
        config_file.write_text(
            json.dumps(
                {
                    "server_url": "http://localhost:9999",
                    "machine_id": "test-machine-12345678",
                    "agent_token": "old_token_abc",
                }
            )
        )
        from agent import RemoteAgent

        config = AgentConfig(config_path=str(config_file))
        agent = RemoteAgent(config=config)
        return agent

    def test_cmd_rotate_token_updates_config(self, tmp_path):
        """_cmd_rotate_token should update agent_token in config."""
        agent = self._make_agent(tmp_path)
        config_file = tmp_path / "config.json"

        agent._cmd_rotate_token(
            {"command": "rotate_token", "new_token": "new_token_xyz_abcdef012345"}
        )

        assert agent.config.agent_token == "new_token_xyz_abcdef012345"

        # Verify persisted to disk
        with open(config_file) as f:
            data = json.load(f)
        assert data["agent_token"] == "new_token_xyz_abcdef012345"

    def test_cmd_rotate_token_missing_new_token(self, tmp_path):
        """_cmd_rotate_token should log warning if new_token missing."""
        agent = self._make_agent(tmp_path)

        # Should not crash, token should remain unchanged
        agent._cmd_rotate_token({"command": "rotate_token"})
        assert agent.config.agent_token == "old_token_abc"

    def test_handle_command_dispatches_rotate_token(self, tmp_path):
        """_handle_command should dispatch rotate_token correctly."""
        agent = self._make_agent(tmp_path)

        agent._handle_command(
            {
                "command": "rotate_token",
                "new_token": "dispatched_new_token_abcdef",
            }
        )

        assert agent.config.agent_token == "dispatched_new_token_abcdef"

    def test_cmd_rotate_token_too_short_rejected(self, tmp_path):
        """_cmd_rotate_token should reject tokens shorter than 16 chars."""
        agent = self._make_agent(tmp_path)

        agent._cmd_rotate_token({"command": "rotate_token", "new_token": "short"})
        assert agent.config.agent_token == "old_token_abc"
