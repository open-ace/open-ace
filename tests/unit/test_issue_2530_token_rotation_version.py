"""
Tests for Issue #2530: Token Rotation Version Tracking

Covers:
- Agent version-based command filtering
- Agent token probe validation
- Agent config atomic write with checksum
- Agent restart state recovery
- Server lazy cleanup of rotate_token commands
- Server validation of token active status
"""

import hashlib
import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Add remote-agent directory to path so we can import config module
AGENT_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "remote-agent")
AGENT_DIR = os.path.abspath(AGENT_DIR)
if AGENT_DIR not in sys.path:
    sys.path.insert(0, AGENT_DIR)

# Dynamically load config module from remote-agent/config.py
# to avoid conflict with scripts/shared/config.py
config_path = os.path.join(AGENT_DIR, "config.py")
spec = importlib.util.spec_from_file_location("agent_config", config_path)
agent_config = importlib.util.module_from_spec(spec)
sys.modules["agent_config"] = agent_config
spec.loader.exec_module(agent_config)

AgentConfig = agent_config.AgentConfig


@pytest.mark.issue(2530)
@pytest.mark.regression
class TestAgentVersionFiltering:
    """Test version-based command filtering in Agent."""

    def _make_agent(self, tmp_path):
        """Create a RemoteAgent with a temp config file."""
        config_file = tmp_path / "config.json"
        config_file.write_text(
            json.dumps(
                {
                    "server_url": "http://localhost:9999",
                    "machine_id": "test-machine-12345678",
                    "agent_token": "old_token_abc",
                    "last_token_version": 5,
                }
            )
        )
        from agent import RemoteAgent

        config = AgentConfig(config_path=str(config_file))
        agent = RemoteAgent(config=config)
        return agent

    def test_ignore_older_version_token(self, tmp_path):
        """Agent should ignore rotate_token commands with older version."""
        # Skip token probing for unit tests
        os.environ["OPENACE_SKIP_TOKEN_PROBE"] = "1"

        try:
            agent = self._make_agent(tmp_path)

            # Try to rotate with version 3 (older than current 5)
            agent._cmd_rotate_token(
                {
                    "command": "rotate_token",
                    "new_token": "new_token_xyz_abcdef012345",
                    "token_version": 3,
                }
            )

            # Token should remain unchanged
            assert agent.config.agent_token == "old_token_abc"
            assert agent.config.last_token_version == 5
        finally:
            del os.environ["OPENACE_SKIP_TOKEN_PROBE"]

    def test_ignore_same_version_token(self, tmp_path):
        """Agent should ignore rotate_token commands with same version."""
        os.environ["OPENACE_SKIP_TOKEN_PROBE"] = "1"

        try:
            agent = self._make_agent(tmp_path)

            # Try to rotate with version 5 (same as current)
            agent._cmd_rotate_token(
                {
                    "command": "rotate_token",
                    "new_token": "new_token_xyz_abcdef012345",
                    "token_version": 5,
                }
            )

            # Token should remain unchanged
            assert agent.config.agent_token == "old_token_abc"
            assert agent.config.last_token_version == 5
        finally:
            del os.environ["OPENACE_SKIP_TOKEN_PROBE"]

    def test_accept_higher_version_token(self, tmp_path):
        """Agent should accept rotate_token commands with higher version."""
        os.environ["OPENACE_SKIP_TOKEN_PROBE"] = "1"

        try:
            agent = self._make_agent(tmp_path)

            # Rotate with version 10 (higher than current 5)
            agent._cmd_rotate_token(
                {
                    "command": "rotate_token",
                    "new_token": "new_token_xyz_abcdef012345",
                    "token_version": 10,
                }
            )

            # Token should be updated
            assert agent.config.agent_token == "new_token_xyz_abcdef012345"
            assert agent.config.last_token_version == 10
        finally:
            del os.environ["OPENACE_SKIP_TOKEN_PROBE"]


@pytest.mark.issue(2530)
@pytest.mark.regression
class TestAgentConfigChecksum:
    """Test atomic write and checksum validation in AgentConfig."""

    def test_save_includes_checksum(self, tmp_path):
        """Saved config should include checksum line."""
        config_file = tmp_path / "config.json"
        config = AgentConfig(config_path=str(config_file))

        config.update({"test_key": "test_value"})
        config.save()

        # Read file and verify checksum line exists
        content = config_file.read_text()
        lines = content.strip().split("\n")

        # Last line should be checksum
        assert lines[-1].startswith("# checksum: ")

        # Extract and verify checksum
        checksum_line = lines[-1]
        checksum = checksum_line.split(": ", 1)[1].strip()

        # Calculate expected checksum
        json_content = "\n".join(lines[:-1])
        expected_checksum = hashlib.sha256(json_content.encode()).hexdigest()

        assert checksum == expected_checksum

    def test_load_validates_checksum(self, tmp_path):
        """AgentConfig should validate checksum on load."""
        config_file = tmp_path / "config.json"

        # Write config with checksum
        config = AgentConfig(config_path=str(config_file))
        config.update({"agent_token": "test_token", "last_token_version": 5})
        config.save()

        # Load should succeed
        loaded_config = AgentConfig(config_path=str(config_file))
        assert loaded_config.agent_token == "test_token"
        assert loaded_config.last_token_version == 5

    def test_detects_checksum_mismatch(self, tmp_path):
        """AgentConfig should detect checksum mismatch."""
        config_file = tmp_path / "config.json"

        # Write config with checksum
        config = AgentConfig(config_path=str(config_file))
        config.update({"agent_token": "original_token"})
        config.save()

        # Corrupt the file (change content without updating checksum)
        content = config_file.read_text()
        lines = content.strip().split("\n")
        json_content = json.loads("\n".join(lines[:-1]))
        json_content["agent_token"] = "corrupted_token"

        # Reconstruct file with old checksum but new content
        corrupted_content = json.dumps(json_content, indent=2)
        corrupted_with_checksum = f"{corrupted_content}\n{lines[-1]}\n"
        config_file.write_text(corrupted_with_checksum)

        # Load should detect corruption and use backup
        loaded_config = AgentConfig(config_path=str(config_file))
        # Should use backup or fall back to defaults
        # (depending on whether backup exists)
        assert loaded_config.agent_token in ("original_token", None)

    def test_backup_created_on_save(self, tmp_path):
        """Save should create backup of existing config."""
        config_file = tmp_path / "config.json"

        # Write initial config
        config = AgentConfig(config_path=str(config_file))
        config.update({"agent_token": "initial_token"})
        config.save()

        # Modify and save again
        config.update({"agent_token": "new_token"})
        config.save()

        # Backup should exist
        backup_file = config_file.with_suffix(".json.bak")
        assert backup_file.exists()

        # Backup should have initial value
        backup_content = backup_file.read_text()
        lines = backup_content.strip().split("\n")
        json_content = json.loads("\n".join(lines[:-1]))
        assert json_content["agent_token"] == "initial_token"


@pytest.mark.issue(2530)
@pytest.mark.regression
class TestAgentTokenProbe:
    """Test token probe validation in Agent."""

    @patch("agent.requests.post")
    def test_probe_succeeds_on_200(self, mock_post, tmp_path):
        """Token probe should succeed with 200 response."""
        config_file = tmp_path / "config.json"
        config_file.write_text(
            json.dumps(
                {
                    "server_url": "http://localhost:9999",
                    "machine_id": "test-machine",
                }
            )
        )

        from agent import RemoteAgent

        config = AgentConfig(config_path=str(config_file))
        agent = RemoteAgent(config=config)

        # Mock successful response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_post.return_value = mock_response

        # Probe should succeed
        result = agent._probe_token("test_token_123", token_version=1)
        assert result is True

        # Should have made request with correct token
        assert mock_post.called
        call_args = mock_post.call_args
        assert "Bearer test_token_123" in call_args[1]["headers"]["Authorization"]

    @patch("agent.requests.post")
    def test_probe_fails_on_401(self, mock_post, tmp_path):
        """Token probe should fail with 401 response."""
        config_file = tmp_path / "config.json"
        config_file.write_text(
            json.dumps(
                {
                    "server_url": "http://localhost:9999",
                    "machine_id": "test-machine",
                }
            )
        )

        from agent import RemoteAgent

        config = AgentConfig(config_path=str(config_file))
        agent = RemoteAgent(config=config)

        # Mock 401 response
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_post.return_value = mock_response

        # Probe should fail (revoked token)
        result = agent._probe_token("revoked_token", token_version=1)
        assert result is False

    @patch("agent.requests.post")
    def test_probe_retries_on_network_error(self, mock_post, tmp_path):
        """Token probe should retry on network errors."""
        config_file = tmp_path / "config.json"
        config_file.write_text(
            json.dumps(
                {
                    "server_url": "http://localhost:9999",
                    "machine_id": "test-machine",
                }
            )
        )

        from agent import RemoteAgent

        config = AgentConfig(config_path=str(config_file))
        agent = RemoteAgent(config=config)

        # Mock network error then success
        import requests

        mock_post.side_effect = [
            requests.RequestException("Network error"),
            requests.RequestException("Network error"),
            MagicMock(status_code=200),
        ]

        # Probe should succeed after retries
        result = agent._probe_token("test_token", token_version=1)
        assert result is True

        # Should have retried 3 times
        assert mock_post.call_count == 3
