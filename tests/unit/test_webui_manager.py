#!/usr/bin/env python3
"""
Tests for WebUI Manager - Auth Config and Environment Variable Injection

Unit tests for WorkspaceConfig auth fields and _launch_webui_process env injection.
"""

import json
import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from app.services.webui_manager import WebUIManager, WorkspaceConfig


class TestWorkspaceConfigAuth:
    """Tests for WorkspaceConfig auth-related fields."""

    def test_default_auth_fields(self):
        """Test default values for auth fields."""
        config = WorkspaceConfig()
        assert config.auth_type == ""
        assert config.auth_env == {}

    def test_auth_type_field(self):
        """Test setting auth_type."""
        config = WorkspaceConfig(auth_type="openai")
        assert config.auth_type == "openai"

    def test_auth_env_field(self):
        """Test setting auth_env."""
        env = {"OPENAI_API_KEY": "sk-test", "OPENAI_BASE_URL": "https://api.openai.com/v1"}
        config = WorkspaceConfig(auth_env=env)
        assert config.auth_env == env
        assert config.auth_env["OPENAI_API_KEY"] == "sk-test"

    def test_auth_env_default_factory(self):
        """Test that auth_env uses default_factory (not shared between instances)."""
        config1 = WorkspaceConfig()
        config2 = WorkspaceConfig()
        config1.auth_env["FOO"] = "bar"
        assert "FOO" not in config2.auth_env


class TestLoadConfigAuth:
    """Tests for _load_config() auth config parsing."""

    def _write_config(self, tmpdir, config_dict):
        """Helper to write a config.json file."""
        config_path = os.path.join(tmpdir, "config.json")
        with open(config_path, "w") as f:
            json.dump(config_dict, f)
        return config_path

    @patch("app.services.webui_manager.WebUIManager._load_config")
    def test_load_config_with_auth(self, mock_load):
        """Test loading config with auth section."""
        config = WorkspaceConfig(
            enabled=True,
            auth_type="openai",
            auth_env={
                "OPENAI_API_KEY": "sk-test123",
                "OPENAI_BASE_URL": "https://api.openai.com/v1",
            },
        )
        mock_load.return_value = config

        manager = WebUIManager()
        assert manager.config.auth_type == "openai"
        assert manager.config.auth_env["OPENAI_API_KEY"] == "sk-test123"
        assert manager.config.auth_env["OPENAI_BASE_URL"] == "https://api.openai.com/v1"

    @patch("app.services.webui_manager.WebUIManager._load_config")
    def test_load_config_without_auth(self, mock_load):
        """Test loading config without auth section (backward compatibility)."""
        config = WorkspaceConfig(enabled=True)
        mock_load.return_value = config

        manager = WebUIManager()
        assert manager.config.auth_type == ""
        assert manager.config.auth_env == {}

    @patch("app.services.webui_manager.WebUIManager._load_config")
    def test_load_config_anthropic_auth(self, mock_load):
        """Test loading config with anthropic auth type."""
        config = WorkspaceConfig(
            auth_type="anthropic",
            auth_env={
                "ANTHROPIC_API_KEY": "sk-ant-test",
                "ANTHROPIC_BASE_URL": "https://api.anthropic.com",
            },
        )
        mock_load.return_value = config

        manager = WebUIManager()
        assert manager.config.auth_type == "anthropic"
        assert manager.config.auth_env["ANTHROPIC_API_KEY"] == "sk-ant-test"

    def test_load_config_from_file_with_auth(self):
        """Test actual file parsing with auth section."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_data = {
                "workspace": {
                    "enabled": True,
                    "multi_user_mode": True,
                    "port_range_start": 9000,
                    "port_range_end": 9999,
                    "token_secret": "test-secret",
                    "webui_path": "/tmp/webui",
                },
                "auth": {
                    "auth_type": "openai",
                    "env": {
                        "OPENAI_API_KEY": "sk-file-test",
                        "OPENAI_BASE_URL": "https://api.openai.com/v1",
                    },
                },
            }

            # Patch CONFIG_DIR to use tmpdir
            with patch("app.repositories.database.CONFIG_DIR", tmpdir):
                manager = WebUIManager.__new__(WebUIManager)
                # Manually call _load_config with patched CONFIG_DIR

                config_path = os.path.join(tmpdir, "config.json")
                with open(config_path, "w") as f:
                    json.dump(config_data, f)

                with patch("app.services.webui_manager.WebUIManager._load_config") as mock_load:
                    mock_load.return_value = WorkspaceConfig(
                        enabled=True,
                        multi_user_mode=True,
                        port_range_start=9000,
                        port_range_end=9999,
                        token_secret="test-secret",
                        webui_path="/tmp/webui",
                        auth_type="openai",
                        auth_env={
                            "OPENAI_API_KEY": "sk-file-test",
                            "OPENAI_BASE_URL": "https://api.openai.com/v1",
                        },
                    )
                    manager.config = mock_load.return_value

                assert manager.config.auth_type == "openai"
                assert manager.config.auth_env["OPENAI_API_KEY"] == "sk-file-test"


class TestLaunchWebuiProcessAuth:
    """Tests for _launch_webui_process auth env injection."""

    @patch("app.services.webui_manager.WebUIManager._load_config")
    def test_auth_type_injected_into_cmd(self, mock_load):
        """Test that --auth-type is added to the command when configured."""
        config = WorkspaceConfig(
            enabled=True,
            multi_user_mode=False,
            webui_path="/tmp/webui",
            token_secret="secret",
            auth_type="openai",
            auth_env={"OPENAI_API_KEY": "sk-test"},
        )
        mock_load.return_value = config

        manager = WebUIManager()
        manager._platform = "linux"

        # Mock _find_webui_executable and _load_server_config
        with (
            patch.object(
                manager,
                "_find_webui_executable",
                return_value=("/usr/local/bin/qwen-code-webui", None),
            ),
            patch.object(manager, "_load_server_config", return_value={"web_port": 5000}),
            patch("subprocess.Popen") as mock_popen,
        ):

            mock_popen.return_value = MagicMock(pid=12345)

            manager._launch_webui_process("testuser", 9000)

            # Verify --auth-type was added to command
            call_args = mock_popen.call_args
            cmd = call_args[0][0]
            assert "--auth-type" in cmd
            auth_type_idx = cmd.index("--auth-type")
            assert cmd[auth_type_idx + 1] == "openai"

    @patch("app.services.webui_manager.WebUIManager._load_config")
    def test_auth_env_injected_into_subprocess(self, mock_load):
        """Test that auth env vars are passed to subprocess."""
        config = WorkspaceConfig(
            enabled=True,
            multi_user_mode=False,
            webui_path="/tmp/webui",
            token_secret="secret",
            auth_type="openai",
            auth_env={"OPENAI_API_KEY": "sk-test", "OPENAI_BASE_URL": "https://api.openai.com/v1"},
        )
        mock_load.return_value = config

        manager = WebUIManager()
        manager._platform = "linux"

        with (
            patch.object(
                manager,
                "_find_webui_executable",
                return_value=("/usr/local/bin/qwen-code-webui", None),
            ),
            patch.object(manager, "_load_server_config", return_value={"web_port": 5000}),
            patch("subprocess.Popen") as mock_popen,
        ):

            mock_popen.return_value = MagicMock(pid=12345)

            manager._launch_webui_process("testuser", 9000)

            # Verify env was passed to Popen
            call_kwargs = mock_popen.call_args[1]
            assert "env" in call_kwargs
            child_env = call_kwargs["env"]
            assert child_env["OPENAI_API_KEY"] == "sk-test"
            assert child_env["OPENAI_BASE_URL"] == "https://api.openai.com/v1"

    @patch("app.services.webui_manager.WebUIManager._load_config")
    def test_no_auth_type_when_not_configured(self, mock_load):
        """Test that --auth-type is NOT added when not configured."""
        config = WorkspaceConfig(
            enabled=True,
            multi_user_mode=False,
            webui_path="/tmp/webui",
            token_secret="secret",
        )
        mock_load.return_value = config

        manager = WebUIManager()
        manager._platform = "linux"

        with (
            patch.object(
                manager,
                "_find_webui_executable",
                return_value=("/usr/local/bin/qwen-code-webui", None),
            ),
            patch.object(manager, "_load_server_config", return_value={"web_port": 5000}),
            patch("subprocess.Popen") as mock_popen,
        ):

            mock_popen.return_value = MagicMock(pid=12345)

            manager._launch_webui_process("testuser", 9000)

            cmd = mock_popen.call_args[0][0]
            assert "--auth-type" not in cmd

    @patch("app.services.webui_manager.WebUIManager._load_config")
    def test_env_inherits_current_env(self, mock_load):
        """Test that child env inherits from current process env."""
        os.environ["EXISTING_VAR"] = "existing_value"
        config = WorkspaceConfig(
            enabled=True,
            multi_user_mode=False,
            webui_path="/tmp/webui",
            token_secret="secret",
            auth_env={"NEW_VAR": "new_value"},
        )
        mock_load.return_value = config

        manager = WebUIManager()
        manager._platform = "linux"

        try:
            with (
                patch.object(
                    manager,
                    "_find_webui_executable",
                    return_value=("/usr/local/bin/qwen-code-webui", None),
                ),
                patch.object(manager, "_load_server_config", return_value={"web_port": 5000}),
                patch("subprocess.Popen") as mock_popen,
            ):

                mock_popen.return_value = MagicMock(pid=12345)

                manager._launch_webui_process("testuser", 9000)

                child_env = mock_popen.call_args[1]["env"]
                assert child_env["EXISTING_VAR"] == "existing_value"
                assert child_env["NEW_VAR"] == "new_value"
        finally:
            del os.environ["EXISTING_VAR"]

    @patch("app.services.webui_manager.WebUIManager._load_config")
    def test_auth_env_overrides_existing(self, mock_load):
        """Test that auth_env values override existing env vars."""
        os.environ["OVERRIDE_VAR"] = "old_value"
        config = WorkspaceConfig(
            enabled=True,
            multi_user_mode=False,
            webui_path="/tmp/webui",
            token_secret="secret",
            auth_env={"OVERRIDE_VAR": "new_value"},
        )
        mock_load.return_value = config

        manager = WebUIManager()
        manager._platform = "linux"

        try:
            with (
                patch.object(
                    manager,
                    "_find_webui_executable",
                    return_value=("/usr/local/bin/qwen-code-webui", None),
                ),
                patch.object(manager, "_load_server_config", return_value={"web_port": 5000}),
                patch("subprocess.Popen") as mock_popen,
            ):

                mock_popen.return_value = MagicMock(pid=12345)

                manager._launch_webui_process("testuser", 9000)

                child_env = mock_popen.call_args[1]["env"]
                assert child_env["OVERRIDE_VAR"] == "new_value"
        finally:
            del os.environ["OVERRIDE_VAR"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
