"""
Test for Issue #2298: WebUI environment variable isolation.

Verify that WebUI process receives minimal environment with:
- Required variables for LLM requests
- No sensitive variables (DATABASE_URL, TOKEN_SECRET, etc.)
- Dynamic envKey support for custom modelProviders
"""

import os
from unittest.mock import MagicMock, patch

import pytest

from app.services.webui_manager import WebUIManager


class TestWebUIEnvIsolation:
    """Test WebUI environment isolation (Issue #2298)."""

    @pytest.fixture
    def manager(self):
        """Create WebUIManager instance."""
        config = MagicMock()
        config.token_secret = "test-secret"
        config.webui_callback_url = ""
        return WebUIManager(config)

    def test_build_webui_env_minimal(self, manager):
        """Verify _build_webui_env creates minimal environment."""
        # Mock _build_local_session_model_pool to return valid pool
        mock_pool = {
            "provider": "openai",
            "models": [],
            "proxy_token": "test-proxy-token",
        }

        # Simulate service environment with sensitive variables
        with patch.dict(
            os.environ,
            {
                "DATABASE_URL": "postgresql://user:pass@localhost/db",
                "TOKEN_SECRET": "super-secret-key",
                "GH_TOKEN": "ghp_xxx",
                "ANTHROPIC_API_KEY": "sk-ant-xxx",
                "LANG": "en_US.UTF-8",
                "LC_ALL": "",
                "PATH": "/usr/bin:/bin",
            },
        ):
            with patch.object(
                manager,
                "_build_local_session_model_pool",
                return_value=mock_pool,
            ):
                env, pool = manager._build_webui_env(
                    user_id=1,
                    system_account="testuser",
                    openace_api_url="http://localhost:19888",
                )

                # Assert required variables exist
                assert "PATH" in env, "PATH should be set"
                assert "LANG" in env, "LANG should be set"
                assert "OPENAI_API_KEY" in env, "OPENAI_API_KEY should be set"
                assert "OPENAI_BASE_URL" in env, "OPENAI_BASE_URL should be set"
                assert "OPENACE_PROXY_TOKEN" in env, "OPENACE_PROXY_TOKEN should be set"
                assert "OPENACE_PROXY_URL" in env, "OPENACE_PROXY_URL should be set"

                # Assert sensitive variables are NOT present
                assert "DATABASE_URL" not in env, "DATABASE_URL should NOT be in WebUI env"
                assert "TOKEN_SECRET" not in env, "TOKEN_SECRET should NOT be in WebUI env"
                assert "GH_TOKEN" not in env, "GH_TOKEN should NOT be in WebUI env"
                assert "ANTHROPIC_API_KEY" not in env, (
                    "ANTHROPIC_API_KEY should NOT be in WebUI env"
                )

    def test_build_webui_env_path_prepend(self, manager):
        """Verify PATH is prepended with system directories."""
        mock_pool = {"proxy_token": "test-token"}

        with patch.dict(os.environ, {"PATH": "/custom/path"}):
            with patch.object(
                manager,
                "_build_local_session_model_pool",
                return_value=mock_pool,
            ):
                env, _ = manager._build_webui_env(
                    user_id=1,
                    system_account="testuser",
                    openace_api_url="http://localhost:19888",
                )

                # PATH should start with system directories
                assert env["PATH"].startswith("/usr/local/bin:/usr/bin:/bin"), (
                    "PATH should prepend system directories"
                )

    def test_build_webui_env_proxy_passthrough(self, manager):
        """Verify HTTP proxy settings are passed through if configured."""
        mock_pool = {"proxy_token": "test-token"}

        with patch.dict(
            os.environ,
            {
                "HTTP_PROXY": "http://proxy.example.com:8080",
                "HTTPS_PROXY": "http://proxy.example.com:8080",
                "NO_PROXY": "localhost,127.0.0.1",
            },
        ):
            with patch.object(
                manager,
                "_build_local_session_model_pool",
                return_value=mock_pool,
            ):
                env, _ = manager._build_webui_env(
                    user_id=1,
                    system_account="testuser",
                    openace_api_url="http://localhost:19888",
                )

                # Proxy variables should be passed through
                assert env.get("HTTP_PROXY") == "http://proxy.example.com:8080"
                assert env.get("HTTPS_PROXY") == "http://proxy.example.com:8080"
                assert env.get("NO_PROXY") == "localhost,127.0.0.1"

    def test_build_webui_env_dynamic_envkey(self, manager):
        """Verify dynamic envKey from modelProviders is collected."""
        # Mock database response with custom envKey
        mock_pool = {
            "provider": "bailian",
            "models": [
                {
                    "name": "qwen-coding",
                    "envKey": "BAILIAN_CODING_PLAN_API_KEY",
                }
            ],
            "proxy_token": "test-proxy-token",
        }

        with patch.object(
            manager,
            "_build_local_session_model_pool",
            return_value=mock_pool,
        ):
            env, pool = manager._build_webui_env(
                user_id=1,
                system_account="testuser",
                openace_api_url="http://localhost:19888",
            )

            # Dynamic envKey should be collected
            assert "BAILIAN_CODING_PLAN_API_KEY" in env, (
                "Dynamic envKey should be in environment"
            )
            assert env["BAILIAN_CODING_PLAN_API_KEY"] == "test-proxy-token", (
                "Dynamic envKey should equal proxy_token"
            )

    def test_build_webui_env_no_proxy_when_not_configured(self, manager):
        """Verify proxy variables are not set if not configured."""
        mock_pool = {"proxy_token": "test-token"}

        # Ensure no proxy variables in environment
        with patch.dict(os.environ, {}, clear=True):
            with patch.object(
                manager,
                "_build_local_session_model_pool",
                return_value=mock_pool,
            ):
                env, _ = manager._build_webui_env(
                    user_id=1,
                    system_account="testuser",
                    openace_api_url="http://localhost:19888",
                )

                # Proxy variables should NOT be present
                assert "HTTP_PROXY" not in env
                assert "HTTPS_PROXY" not in env
                assert "NO_PROXY" not in env

    def test_sensitive_variable_list(self, manager):
        """Comprehensive test for all forbidden sensitive variables."""
        mock_pool = {"proxy_token": "test-token"}
        forbidden_vars = [
            "DATABASE_URL",
            "TOKEN_SECRET",
            "GH_TOKEN",
            "ANTHROPIC_API_KEY",
            "OPENCLAW_TOKEN",
            "GEMINI_API_KEY",
            "AWS_SECRET_ACCESS_KEY",
            "MYSQL_PASSWORD",
            "POSTGRES_PASSWORD",
        ]

        # Set all forbidden variables in service environment
        env_dict = {var: f"{var.lower()}_value" for var in forbidden_vars}
        env_dict["LANG"] = "C.UTF-8"

        with patch.dict(os.environ, env_dict):
            with patch.object(
                manager,
                "_build_local_session_model_pool",
                return_value=mock_pool,
            ):
                env, _ = manager._build_webui_env(
                    user_id=1,
                    system_account="testuser",
                    openace_api_url="http://localhost:19888",
                )

                # Assert none of the forbidden variables leak
                for var in forbidden_vars:
                    assert var not in env, f"{var} should NOT be in WebUI environment"
