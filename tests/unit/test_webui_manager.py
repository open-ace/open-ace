#!/usr/bin/env python3
"""
Tests for WebUI Manager — local proxy environment wiring.

Unit tests for WorkspaceConfig (without auth fields) and
_configure_local_openai_proxy database-driven proxy wiring.
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
                    "port_range_start": 3100,
                    "port_range_end": 3200,
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


class TestConfigureLocalOpenAIProxy:
    """Tests for local proxy environment wiring in multi-user mode."""

    @patch("app.services.webui_manager.WebUIManager._load_config")
    def test_configure_openai_proxy(self, mock_load):
        """Test that local qwen traffic is routed through Open ACE proxy."""
        config = WorkspaceConfig(enabled=True, token_secret="secret")
        mock_load.return_value = config

        manager = WebUIManager()

        with patch(
            "app.modules.workspace.api_key_proxy.get_api_key_proxy_service"
        ) as mock_get_proxy:
            mock_proxy = MagicMock()
            mock_proxy.get_tool_model_pool.return_value = {
                "models": [{"id": "gpt-4.1", "name": "GPT-4.1"}],
                "candidate_keys": [{"key_id": 42, "priority": 100, "weight": 100}],
                "model_key_ids": {"gpt-4.1": [42]},
                "settings": {"modelProviders": {"openai": [{"id": "gpt-4.1", "name": "GPT-4.1"}]}},
                "empty_reason": None,
            }
            mock_proxy.generate_proxy_token.return_value = "proxy-token"
            mock_get_proxy.return_value = mock_proxy

            env = {}
            pool = manager._configure_local_openai_proxy(7, env, "http://openace.example:19888")

            assert env["OPENAI_API_KEY"] == "proxy-token"
            assert (
                env["OPENAI_BASE_URL"] == "http://openace.example:19888/api/workspace/llm-proxy/v1"
            )
            assert (
                env["OPENACE_PROXY_URL"] == "http://openace.example:19888/api/workspace/llm-proxy"
            )
            assert pool["proxy_token"] == "proxy-token"
            mock_proxy.get_tool_model_pool.assert_called_once_with(
                tenant_id=1,
                tool_name="qwen-code",
                scope="local",
                provider="openai",
            )

    @patch("app.services.webui_manager.WebUIManager._load_config")
    def test_configure_no_keys_available(self, mock_load):
        """Test that empty model pools still produce a safe local proxy env."""
        config = WorkspaceConfig(enabled=True, token_secret="secret")
        mock_load.return_value = config

        manager = WebUIManager()

        with patch(
            "app.modules.workspace.api_key_proxy.get_api_key_proxy_service"
        ) as mock_get_proxy:
            mock_proxy = MagicMock()
            mock_proxy.get_tool_model_pool.return_value = {
                "models": [],
                "candidate_keys": [],
                "model_key_ids": {},
                "settings": {},
                "empty_reason": "No models",
            }
            mock_proxy.generate_proxy_token.return_value = "proxy-token"
            mock_get_proxy.return_value = mock_proxy

            env = {}
            pool = manager._configure_local_openai_proxy(7, env, "http://openace.example:19888")

            assert env["OPENAI_API_KEY"] == "proxy-token"
            assert pool["models"] == []
            assert pool["empty_reason"] == "No models"

    @patch("app.services.webui_manager.WebUIManager._load_config")
    def test_configure_handles_exception(self, mock_load):
        """Test that exceptions during model-pool lookup are handled gracefully."""
        config = WorkspaceConfig(enabled=True, token_secret="secret")
        mock_load.return_value = config

        manager = WebUIManager()

        with patch(
            "app.modules.workspace.api_key_proxy.get_api_key_proxy_service",
            side_effect=Exception("DB error"),
        ):
            env = {}
            # Should not raise
            pool = manager._configure_local_openai_proxy(7, env, "http://openace.example:19888")
            assert "OPENAI_API_KEY" not in env
            assert pool["models"] == []
            assert pool["proxy_token"] == ""

    @patch("app.services.webui_manager.WebUIManager._load_config")
    def test_launch_process_calls_inject(self, mock_load):
        """Test that _launch_webui_process configures local proxy env."""
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
            patch.object(manager, "_load_server_config", return_value={"web_port": 19888}),
            patch.object(
                manager, "_configure_local_openai_proxy", return_value={"models": []}
            ) as mock_proxy_setup,
            patch("subprocess.Popen") as mock_popen,
            patch("app.services.webui_manager.run_as_root_if_needed") as mock_run_as_root,
            # The launch path looks up the current OS user to decide sudo vs. direct
            # execution. Mock it so the test stays hermetic and does not depend on the
            # running uid existing in the host passwd database.
            patch("app.services.webui_manager.pwd") as mock_pwd,
            patch("os.open", return_value=123),
            patch("os.close"),
        ):
            mock_pwd.getpwuid.return_value.pw_name = "testuser"
            mock_popen.return_value = MagicMock(pid=12345)
            mock_run_as_root.return_value = MagicMock(returncode=0, stdout="", stderr="")
            _, model_pool = manager._launch_webui_process(
                1, "testuser", 9000, "http://192.168.1.87"
            )

            # Verify local proxy setup was called
            mock_proxy_setup.assert_called_once()
            # Verify env was passed to Popen
            call_kwargs = mock_popen.call_args[1]
            assert "env" in call_kwargs
            assert model_pool == {"models": []}


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
