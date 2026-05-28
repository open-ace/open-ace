#!/usr/bin/env python3
"""
Tests for WebUI Manager — API key injection from database.

Unit tests for WorkspaceConfig (without auth fields) and
_inject_local_api_keys database-driven key injection.
"""

import json
import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from app.services.webui_manager import WebUIManager, WorkspaceConfig


class TestWorkspaceConfig:
    """Tests for WorkspaceConfig fields."""

    def test_default_values(self):
        """Test default values for config fields."""
        config = WorkspaceConfig()
        assert config.enabled is False
        assert config.auth_type == "" if hasattr(config, "auth_type") else True
        assert config.webui_path == ""

    def test_no_auth_env_field(self):
        """WorkspaceConfig should not have auth_env field after refactor."""
        config = WorkspaceConfig()
        assert not hasattr(config, "auth_env")

    def test_no_auth_type_field(self):
        """WorkspaceConfig should not have auth_type field after refactor."""
        config = WorkspaceConfig()
        assert not hasattr(config, "auth_type")


class TestLoadConfig:
    """Tests for _load_config() parsing."""

    @patch("app.services.webui_manager.WebUIManager._load_config")
    def test_load_config_basic(self, mock_load):
        """Test loading basic workspace config."""
        config = WorkspaceConfig(
            enabled=True,
            multi_user_mode=True,
        )
        mock_load.return_value = config

        manager = WebUIManager()
        assert manager.config.enabled is True
        assert manager.config.multi_user_mode is True

    def test_load_config_from_file_without_auth(self):
        """Test actual file parsing — auth section should be ignored."""
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
                    },
                },
            }

            config_path = os.path.join(tmpdir, "config.json")
            with open(config_path, "w") as f:
                json.dump(config_data, f)

            with patch("app.repositories.database.CONFIG_DIR", tmpdir):
                manager = WebUIManager.__new__(WebUIManager)
                manager.config = manager._load_config()

            # Config should have workspace fields but NOT auth fields
            assert manager.config.enabled is True
            assert manager.config.token_secret == "test-secret"
            assert not hasattr(manager.config, "auth_env")
            assert not hasattr(manager.config, "auth_type")


class TestInjectLocalApiKeys:
    """Tests for _inject_local_api_keys database-driven key injection."""

    @patch("app.services.webui_manager.WebUIManager._load_config")
    def test_inject_openai_key(self, mock_load):
        """Test that OpenAI key from database is injected into env."""
        config = WorkspaceConfig(enabled=True, token_secret="secret")
        mock_load.return_value = config

        manager = WebUIManager()

        with patch(
            "app.modules.workspace.api_key_proxy.get_api_key_proxy_service"
        ) as mock_get_proxy:
            mock_proxy = MagicMock()
            mock_proxy.resolve_api_key_for_scope.return_value = (
                "sk-db-key",
                "https://api.example.com/v1",
                42,
            )
            mock_get_proxy.return_value = mock_proxy

            env = {}
            manager._inject_local_api_keys(env)

            assert env["OPENAI_API_KEY"] == "sk-db-key"
            assert env["OPENAI_BASE_URL"] == "https://api.example.com/v1"
            # _inject_local_api_keys calls resolve for both openai and anthropic
            assert mock_proxy.resolve_api_key_for_scope.call_count == 2

    @patch("app.services.webui_manager.WebUIManager._load_config")
    def test_inject_no_keys_available(self, mock_load):
        """Test that env is unchanged when no keys are found in database."""
        config = WorkspaceConfig(enabled=True, token_secret="secret")
        mock_load.return_value = config

        manager = WebUIManager()

        with patch(
            "app.modules.workspace.api_key_proxy.get_api_key_proxy_service"
        ) as mock_get_proxy:
            mock_proxy = MagicMock()
            mock_proxy.resolve_api_key_for_scope.return_value = None
            mock_get_proxy.return_value = mock_proxy

            env = {}
            manager._inject_local_api_keys(env)

            assert "OPENAI_API_KEY" not in env
            assert "ANTHROPIC_API_KEY" not in env

    @patch("app.services.webui_manager.WebUIManager._load_config")
    def test_inject_handles_exception(self, mock_load):
        """Test that exceptions during key injection are handled gracefully."""
        config = WorkspaceConfig(enabled=True, token_secret="secret")
        mock_load.return_value = config

        manager = WebUIManager()

        with patch(
            "app.modules.workspace.api_key_proxy.get_api_key_proxy_service",
            side_effect=Exception("DB error"),
        ):
            env = {}
            # Should not raise
            manager._inject_local_api_keys(env)
            assert "OPENAI_API_KEY" not in env

    @patch("app.services.webui_manager.WebUIManager._load_config")
    def test_launch_process_calls_inject(self, mock_load):
        """Test that _launch_webui_process calls _inject_local_api_keys."""
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
            patch.object(manager, "_ensure_system_user", return_value=True),
            patch.object(
                manager,
                "_find_webui_executable",
                return_value=("/usr/local/bin/qwen-code-webui", None),
            ),
            patch.object(manager, "_load_server_config", return_value={"web_port": 5000}),
            patch.object(manager, "_inject_local_api_keys") as mock_inject,
            patch("subprocess.Popen") as mock_popen,
        ):
            mock_popen.return_value = MagicMock(pid=12345)
            manager._launch_webui_process(1, "testuser", 9000)

            # Verify _inject_local_api_keys was called
            mock_inject.assert_called_once()
            # Verify env was passed to Popen
            call_kwargs = mock_popen.call_args[1]
            assert "env" in call_kwargs


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
