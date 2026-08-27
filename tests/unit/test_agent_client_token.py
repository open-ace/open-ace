"""
Tests for Issue #883: Remote Agent client token management.

Covers:
- config.py: save_agent_token() persistence
- agent.py: 401 revoked detection and reconnect stop
- agent.py: rotate_token command handling
"""

import importlib.util
import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

pytestmark = [pytest.mark.regression, pytest.mark.issue(883)]

# remote-agent directory (two levels up from tests/unit).
AGENT_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "remote-agent")
AGENT_DIR = os.path.abspath(AGENT_DIR)

# Dynamically load config module from remote-agent/config.py under a unique
# name (tests/unit/test_issue_2530_token_rotation_version.py pattern) instead
# of adding remote-agent to sys.path and importing it as top-level ``config``:
# that collision shadows the repo-level ``config`` package every later test
# in this worker sees (tests/unit/test_db.py imports the scripts-side one).
_config_spec = importlib.util.spec_from_file_location("agent_config_883", AGENT_DIR + "/config.py")
agent_config = importlib.util.module_from_spec(_config_spec)
sys.modules["agent_config_883"] = agent_config
_config_spec.loader.exec_module(agent_config)

AgentConfig = agent_config.AgentConfig


def _load_agent_module():
    """Exec remote-agent/agent.py under a unique name.

    Like the config load above, no bare ``agent``/``config`` entry is left in
    sys.modules and remote-agent only sits on sys.path for the duration of the
    exec (agent.py imports its siblings — config, executor, ... — as bare
    names). The module is loaded once at import time because the
    ``@patch.object(agent_module.requests, "post")`` decorators below need a
    registered module object before the tests run.
    """
    saved_path = list(sys.path)
    saved_config = sys.modules.pop("config", None)
    if AGENT_DIR in sys.path:
        sys.path.remove(AGENT_DIR)
    sys.path.insert(0, AGENT_DIR)
    spec = importlib.util.spec_from_file_location("remote_agent_883", AGENT_DIR + "/agent.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["remote_agent_883"] = module
    assert spec and spec.loader
    try:
        spec.loader.exec_module(module)
        return module
    finally:
        sys.modules.pop("config", None)
        if saved_config is not None:
            sys.modules["config"] = saved_config
        sys.path[:] = saved_path


agent_module = _load_agent_module()
RemoteAgent = agent_module.RemoteAgent

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
            content = f.read()

        # Issue #2530: Config file now has checksum line
        # Parse without checksum line
        lines = content.strip().split("\n")
        if lines[-1].startswith("# checksum: "):
            json_content = "\n".join(lines[:-1])
        else:
            json_content = content

        data = json.loads(json_content)
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
            content = f.read()

        # Issue #2530: Config file now has checksum line
        lines = content.strip().split("\n")
        if lines[-1].startswith("# checksum: "):
            json_content = "\n".join(lines[:-1])
        else:
            json_content = content

        data = json.loads(json_content)
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
            content = f.read()

        # Issue #2530: Config file now has checksum line
        lines = content.strip().split("\n")
        if lines[-1].startswith("# checksum: "):
            json_content = "\n".join(lines[:-1])
        else:
            json_content = content

        data = json.loads(json_content)

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
        config = AgentConfig(config_path=str(config_file))
        agent = RemoteAgent(config=config)
        return agent

    @patch.object(agent_module.requests, "post")
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

    @patch.object(agent_module.requests, "post")
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

    @patch.object(agent_module.requests, "post")
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
        config = AgentConfig(config_path=str(config_file))
        agent = RemoteAgent(config=config)
        return agent

    def test_cmd_rotate_token_updates_config(self, tmp_path):
        """_cmd_rotate_token should update agent_token in config."""
        # Issue #2530: Skip token probing for unit tests
        os.environ["OPENACE_SKIP_TOKEN_PROBE"] = "1"

        try:
            agent = self._make_agent(tmp_path)
            config_file = tmp_path / "config.json"

            agent._cmd_rotate_token(
                {"command": "rotate_token", "new_token": "new_token_xyz_abcdef012345"}
            )

            assert agent.config.agent_token == "new_token_xyz_abcdef012345"

            # Verify persisted to disk
            with open(config_file) as f:
                content = f.read()

            # Issue #2530: Config file now has checksum line
            lines = content.strip().split("\n")
            if lines[-1].startswith("# checksum: "):
                json_content = "\n".join(lines[:-1])
            else:
                json_content = content

            data = json.loads(json_content)
            assert data["agent_token"] == "new_token_xyz_abcdef012345"
        finally:
            del os.environ["OPENACE_SKIP_TOKEN_PROBE"]

    def test_cmd_rotate_token_missing_new_token(self, tmp_path):
        """_cmd_rotate_token should log warning if new_token missing."""
        # Issue #2530: Skip token probing for unit tests
        os.environ["OPENACE_SKIP_TOKEN_PROBE"] = "1"

        try:
            agent = self._make_agent(tmp_path)

            # Should not crash, token should remain unchanged
            agent._cmd_rotate_token({"command": "rotate_token"})
            assert agent.config.agent_token == "old_token_abc"
        finally:
            del os.environ["OPENACE_SKIP_TOKEN_PROBE"]

    def test_handle_command_dispatches_rotate_token(self, tmp_path):
        """_handle_command should dispatch rotate_token correctly."""
        # Issue #2530: Skip token probing for unit tests
        os.environ["OPENACE_SKIP_TOKEN_PROBE"] = "1"

        try:
            agent = self._make_agent(tmp_path)

            agent._handle_command(
                {
                    "command": "rotate_token",
                    "new_token": "dispatched_new_token_abcdef",
                }
            )

            assert agent.config.agent_token == "dispatched_new_token_abcdef"
        finally:
            del os.environ["OPENACE_SKIP_TOKEN_PROBE"]

    def test_cmd_rotate_token_too_short_rejected(self, tmp_path):
        """_cmd_rotate_token should reject tokens shorter than 16 chars."""
        # Issue #2530: Skip token probing for unit tests
        os.environ["OPENACE_SKIP_TOKEN_PROBE"] = "1"

        try:
            agent = self._make_agent(tmp_path)

            agent._cmd_rotate_token({"command": "rotate_token", "new_token": "short"})
            assert agent.config.agent_token == "old_token_abc"
        finally:
            del os.environ["OPENACE_SKIP_TOKEN_PROBE"]
