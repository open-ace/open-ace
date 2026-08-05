"""
Test for Issue #2298: WebUI environment variable isolation.

Verify that WebUI process receives minimal environment with:
- Required variables for LLM requests
- No sensitive variables (DATABASE_URL, TOKEN_SECRET, etc.)
- Dynamic envKey support for custom modelProviders
"""

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.services.webui_manager import (
    _WEBUI_ENV_SUDO_KNOWN_KEYS,
    _WEBUI_LAUNCH_WRAPPER,
    WebUIManager,
)


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
                assert (
                    "ANTHROPIC_API_KEY" not in env
                ), "ANTHROPIC_API_KEY should NOT be in WebUI env"

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
                assert env["PATH"].startswith(
                    "/usr/local/bin:/usr/bin:/bin"
                ), "PATH should prepend system directories"

    def test_build_webui_env_preserves_inherited_path(self, manager):
        """Verify inherited PATH is preserved (Issue #1141 regression check).

        macOS Apple Silicon installs node in /opt/homebrew/bin which is not
        in the hardcoded PATH. Preserving inherited PATH ensures custom node
        installations are found, preventing Issue #1083 regression.
        """
        mock_pool = {"proxy_token": "test-token"}

        # Simulate macOS environment with custom PATH
        custom_path = "/opt/homebrew/bin:/custom/path"
        with patch.dict(os.environ, {"PATH": custom_path}):
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

                # Should preserve inherited paths
                assert (
                    "/opt/homebrew/bin" in env["PATH"]
                ), "Inherited PATH should be preserved for macOS"
                assert "/custom/path" in env["PATH"], "Inherited PATH should be preserved"

                # System dirs should be prepended
                assert env["PATH"].startswith(
                    "/usr/local/bin:/usr/bin:/bin"
                ), "System dirs should be prepended"

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
            assert "BAILIAN_CODING_PLAN_API_KEY" in env, "Dynamic envKey should be in environment"
            assert (
                env["BAILIAN_CODING_PLAN_API_KEY"] == "test-proxy-token"
            ), "Dynamic envKey should equal proxy_token"

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


class TestSudoInlineEnvArgs:
    """Test sudo inline env-arg passthrough for Issue #2298 / PR #2305."""

    def test_known_keys_contains_expected_vars(self):
        """_WEBUI_ENV_SUDO_KNOWN_KEYS should contain all env_keep-managed vars."""
        expected = {
            "PATH",
            "OPENACE_PROXY_TOKEN",
            "OPENACE_PROXY_URL",
            "OPENACE_MODEL",
            "OPENACE_LOG_DIR",
            "SESSION_TIMEOUT_MS",
            "KEEPALIVE_INTERVAL_MS",
            "OPENAI_API_KEY",
            "OPENAI_BASE_URL",
            "LANG",
            "LC_ALL",
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "NO_PROXY",
        }
        missing = expected - _WEBUI_ENV_SUDO_KNOWN_KEYS
        assert not missing, f"Missing keys in _WEBUI_ENV_SUDO_KNOWN_KEYS: {missing}"
        extra = _WEBUI_ENV_SUDO_KNOWN_KEYS - expected
        assert not extra, f"Unexpected keys in _WEBUI_ENV_SUDO_KNOWN_KEYS: {extra}"

    def test_known_keys_is_frozenset(self):
        """_WEBUI_ENV_SUDO_KNOWN_KEYS should be immutable frozenset."""
        assert isinstance(
            _WEBUI_ENV_SUDO_KNOWN_KEYS, frozenset
        ), "Should be frozenset for immutability"

    def test_launch_wrapper_path_constant(self):
        """_WEBUI_LAUNCH_WRAPPER should point to the secure wrapper."""
        assert _WEBUI_LAUNCH_WRAPPER == "/usr/local/bin/openace-webui-launch"

    def test_dynamic_envkey_not_in_known_keys(self):
        """Dynamic envKeys from model pool should NOT be in KNOWN_KEYS
        (they get inlined automatically in the sudo path)."""
        dynamic_envkey = "BAILIAN_CODING_PLAN_API_KEY"
        assert (
            dynamic_envkey not in _WEBUI_ENV_SUDO_KNOWN_KEYS
        ), f"{dynamic_envkey} is dynamic and should NOT be in known_keys"

    @pytest.fixture
    def manager(self):
        config = MagicMock()
        config.token_secret = "test-secret"
        config.webui_callback_url = ""
        return WebUIManager(config)

    @patch("app.services.webui_manager.pwd")
    @patch("app.services.webui_manager.subprocess.Popen")
    def test_sudo_path_cmd_includes_launch_wrapper(self, mock_popen, mock_pwd, manager):
        """Verify sudo path uses the wrapper, not raw /usr/bin/env."""
        # Set platform to linux + current_user != system_account
        manager._platform = "linux"
        mock_pwd.getpwuid.return_value.pw_name = "service_user"

        mock_pool = {"proxy_token": "test-token", "provider": "openai", "models": []}
        with patch.object(manager, "_build_local_session_model_pool", return_value=mock_pool):
            with patch.object(
                manager,
                "_build_webui_env",
                return_value=(
                    {"OPENAI_API_KEY": "tk", "OPENAI_BASE_URL": "url", "PATH": "/bin"},
                    mock_pool,
                ),
            ):
                with patch.object(
                    manager,
                    "_find_webui_executable",
                    return_value=("/opt/qwen-code-webui", None),
                ):
                    process, _ = manager._launch_webui_process(
                        user_id=1,
                        system_account="target_user",
                        port=3100,
                        base_url="http://localhost",
                    )

                call_args = mock_popen.call_args
                cmd = call_args[0][0]  # cmd list

                # The command should include the launch wrapper, not raw /usr/bin/env
                assert _WEBUI_LAUNCH_WRAPPER in cmd, "sudo path should use openace-webui-launch"
                assert "/usr/bin/env" not in cmd, "should not use raw /usr/bin/env"

    @patch("app.services.webui_manager.pwd")
    @patch("app.services.webui_manager.subprocess.Popen")
    def test_sudo_path_skips_popen_env(self, mock_popen, mock_pwd, manager):
        """Verify sudo path does NOT pass child_env to Popen (vars are inlined)."""
        manager._platform = "linux"
        mock_pwd.getpwuid.return_value.pw_name = "service_user"

        mock_pool = {"proxy_token": "test-token", "provider": "openai", "models": []}
        with patch.object(manager, "_build_local_session_model_pool", return_value=mock_pool):
            with patch.object(
                manager,
                "_build_webui_env",
                return_value=(
                    {"OPENAI_API_KEY": "tk", "PATH": "/bin", "LANG": "C.UTF-8"},
                    mock_pool,
                ),
            ):
                with patch.object(
                    manager,
                    "_find_webui_executable",
                    return_value=("/opt/qwen-code-webui", None),
                ):
                    manager._launch_webui_process(
                        user_id=1,
                        system_account="target_user",
                        port=3100,
                        base_url="http://localhost",
                    )

                call_kwargs = mock_popen.call_args[1]
                # In sudo path, env should be None (not child_env)
                assert call_kwargs.get("env") is None, (
                    "sudo path should not pass child_env to Popen "
                    "(env vars already inlined via launch wrapper)"
                )

    @patch("app.services.webui_manager.pwd")
    @patch("app.services.webui_manager.subprocess.Popen")
    def test_same_user_path_passes_child_env(self, mock_popen, mock_pwd, manager):
        """Verify same-user path still passes child_env to Popen."""
        manager._platform = "linux"
        mock_pwd.getpwuid.return_value.pw_name = "same_user"

        mock_pool = {"proxy_token": "test-token", "provider": "openai", "models": []}
        with patch.object(manager, "_build_local_session_model_pool", return_value=mock_pool):
            with patch.object(
                manager,
                "_build_webui_env",
                return_value=(
                    {"OPENAI_API_KEY": "tk", "PATH": "/bin"},
                    mock_pool,
                ),
            ):
                with patch.object(
                    manager,
                    "_find_webui_executable",
                    return_value=("/opt/qwen-code-webui", None),
                ):
                    manager._launch_webui_process(
                        user_id=1,
                        system_account="same_user",
                        port=3100,
                        base_url="http://localhost",
                    )

                call_kwargs = mock_popen.call_args[1]
                # In same-user path, env should be child_env (not None)
                assert (
                    call_kwargs.get("env") is not None
                ), "same-user path should pass child_env to Popen"
                assert "OPENAI_API_KEY" in call_kwargs["env"]

    @patch("app.services.webui_manager.pwd")
    @patch("app.services.webui_manager.subprocess.Popen")
    def test_sudo_path_inlines_dynamic_envkey(self, mock_popen, mock_pwd, manager):
        """Verify dynamic envKey is inlined in sudo path command."""
        manager._platform = "linux"
        mock_pwd.getpwuid.return_value.pw_name = "service_user"

        # Model pool with a custom envKey (dynamic)
        mock_pool = {
            "proxy_token": "test-token",
            "provider": "bailian",
            "models": [{"name": "qwen-coding", "envKey": "BAILIAN_CODING_PLAN_API_KEY"}],
        }
        with patch.object(manager, "_build_local_session_model_pool", return_value=mock_pool):
            with patch.object(
                manager,
                "_build_webui_env",
                return_value=(
                    {
                        "OPENAI_API_KEY": "tk",
                        "OPENAI_BASE_URL": "url",
                        "PATH": "/bin",
                        "BAILIAN_CODING_PLAN_API_KEY": "dyn-token",
                    },
                    mock_pool,
                ),
            ):
                with patch.object(
                    manager,
                    "_find_webui_executable",
                    return_value=("/opt/qwen-code-webui", None),
                ):
                    manager._launch_webui_process(
                        user_id=1,
                        system_account="target_user",
                        port=3100,
                        base_url="http://localhost",
                    )

                cmd = mock_popen.call_args[0][0]
                # The dynamic envKey should appear as an inline env arg
                assert any(
                    "BAILIAN_CODING_PLAN_API_KEY" in arg for arg in cmd
                ), "Dynamic envKey should be inlined in sudo cmd"

    @patch("app.services.webui_manager.pwd")
    @patch("app.services.webui_manager.subprocess.Popen")
    def test_sudo_path_no_sensitive_known_keys(self, mock_popen, mock_pwd, manager):
        """Verify known_keys does NOT contain sensitive vars that should never leak."""
        assert "DATABASE_URL" not in _WEBUI_ENV_SUDO_KNOWN_KEYS
        assert "TOKEN_SECRET" not in _WEBUI_ENV_SUDO_KNOWN_KEYS
        assert "GH_TOKEN" not in _WEBUI_ENV_SUDO_KNOWN_KEYS
        assert "ANTHROPIC_API_KEY" not in _WEBUI_ENV_SUDO_KNOWN_KEYS


class TestSudoersSecurityRules:
    """Test that install.sh does not generate unrestricted sudoers rules
    for openace-webui-launch (Issue #2305 review: privilege escalation).

    The openace-webui-launch wrapper execs /usr/bin/env, so an unrestricted
    rule like ``ALL=(root) NOPASSWD: /usr/local/bin/openace-webui-launch *``
    would allow ``sudo openace-webui-launch bash`` → root shell.

    It must only appear in the restricted current_user_rules format:
    ``ALL=(ALL) NOPASSWD: /usr/local/bin/openace-webui-launch "$webui_path" *``
    """

    @pytest.fixture
    def package_install_sh(self):
        """Path to package-method install.sh."""
        return Path(__file__).parent.parent.parent.parent / (
            "scripts/install-central/package-method/install.sh"
        )

    @pytest.fixture
    def docker_install_sh(self):
        """Path to docker-method install.sh."""
        return Path(__file__).parent.parent.parent.parent / (
            "scripts/install-central/docker-method/install.sh"
        )

    def test_package_install_no_unrestricted_webui_launch_rule(self, package_install_sh):
        """Package-method install.sh must NOT put openace-webui-launch in the
        security_wrapper_rules loop (which generates unrestricted rules).

        The wrapper must only appear in current_user_rules with the
        ``"$webui_path"`` first-argument constraint.
        """
        if not package_install_sh.exists():
            pytest.skip("package-method install.sh not found")

        content = package_install_sh.read_text()

        # Find the security_wrapper_rules loop
        # It should NOT contain openace-webui-launch
        in_wrapper_loop = False
        loop_lines = []
        for line in content.split("\n"):
            stripped = line.strip()
            if "for wrapper in" in stripped and "security_wrapper" not in stripped:
                # Different loop, skip
                continue
            if "for wrapper in" in stripped:
                in_wrapper_loop = True
                loop_lines.append(stripped)
                continue
            if in_wrapper_loop:
                loop_lines.append(stripped)
                if stripped.startswith("done"):
                    in_wrapper_loop = False
                    break

        loop_text = "\n".join(loop_lines)
        assert "openace-webui-launch" not in loop_text, (
            "openace-webui-launch must NOT be in the security_wrapper_rules loop "
            "(generates unrestricted rule allowing privilege escalation). "
            "It should only appear in current_user_rules with "
            "'\"$webui_path\"' first-argument constraint."
        )

    def test_docker_install_no_unrestricted_webui_launch_rule(self, docker_install_sh):
        """Docker-method install.sh must NOT put openace-webui-launch in the
        security_wrapper_rules loop (which generates unrestricted rules)."""
        if not docker_install_sh.exists():
            pytest.skip("docker-method install.sh not found")

        content = docker_install_sh.read_text()

        # Find the security_wrapper_rules loop
        in_wrapper_loop = False
        loop_lines = []
        for line in content.split("\n"):
            stripped = line.strip()
            if "for wrapper in" in stripped and "security_wrapper" not in stripped:
                continue
            if "for wrapper in" in stripped:
                in_wrapper_loop = True
                loop_lines.append(stripped)
                continue
            if in_wrapper_loop:
                loop_lines.append(stripped)
                if stripped.startswith("done"):
                    in_wrapper_loop = False
                    break

        loop_text = "\n".join(loop_lines)
        assert "openace-webui-launch" not in loop_text, (
            "openace-webui-launch must NOT be in the security_wrapper_rules loop "
            "(generates unrestricted rule allowing privilege escalation)."
        )

    def test_package_install_has_restricted_webui_launch_rule(self, package_install_sh):
        """Package-method install.sh must have the restricted webui-launch rule
        in current_user_rules with the ``"$webui_path"`` constraint."""
        if not package_install_sh.exists():
            pytest.skip("package-method install.sh not found")

        content = package_install_sh.read_text()

        # The restricted rule must exist
        assert 'openace-webui-launch "$webui_path" *' in content, (
            "Package install.sh must contain the restricted sudoers rule "
            "for openace-webui-launch with '$webui_path' first-argument constraint"
        )

    def test_docker_install_has_restricted_webui_launch_rule(self, docker_install_sh):
        """Docker-method install.sh must have the restricted webui-launch rule
        with the ``"$webui_path"`` constraint."""
        if not docker_install_sh.exists():
            pytest.skip("docker-method install.sh not found")

        content = docker_install_sh.read_text()

        assert 'openace-webui-launch "$webui_path" *' in content, (
            "Docker install.sh must contain the restricted sudoers rule "
            "for openace-webui-launch with '$webui_path' first-argument constraint"
        )
