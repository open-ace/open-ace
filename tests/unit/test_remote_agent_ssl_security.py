"""
Tests for SSL/TLS security enhancements (Issue #1892).

Tests cover:
- Default configuration values
- localhost URL detection
- CA bundle configuration
- Security warnings
- WebSocket SSL configuration
"""

import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Add remote-agent to path for imports
import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "remote-agent"))

from config import AgentConfig, DEFAULTS


class TestDefaultConfiguration:
    """Test default SSL configuration values."""

    def test_default_skip_ssl_verify_is_false(self):
        """Verify that skip_ssl_verify defaults to False (secure)."""
        assert DEFAULTS["skip_ssl_verify"] is False, \
            "skip_ssl_verify should default to False for security"

    def test_default_ca_bundle_path_is_none(self):
        """Verify that ca_bundle_path defaults to None."""
        assert DEFAULTS["ca_bundle_path"] is None, \
            "ca_bundle_path should default to None"

    def test_default_insecure_mode_allowed_is_true(self):
        """Verify that insecure_mode_allowed defaults to True."""
        assert DEFAULTS["insecure_mode_allowed"] is True, \
            "insecure_mode_allowed should default to True for backward compatibility"

    def test_config_loads_defaults(self, tmp_path):
        """Verify AgentConfig loads correct defaults."""
        config = AgentConfig()
        assert config.skip_ssl_verify is False
        assert config.ca_bundle_path is None
        assert config.insecure_mode_allowed is True


class TestLocalhostDetection:
    """Test localhost URL detection."""

    def test_ipv4_loopback_detection(self):
        """Test detection of IPv4 loopback addresses."""
        # Import the function from agent.py
        from agent import _is_localhost_url

        assert _is_localhost_url("http://127.0.0.1:19888") is True
        assert _is_localhost_url("https://127.0.0.1:443") is True
        assert _is_localhost_url("http://127.0.0.1") is True
        # Other 127.x.x.x addresses
        assert _is_localhost_url("http://127.1.2.3:8080") is True
        assert _is_localhost_url("https://127.255.255.255") is True

    def test_ipv6_loopback_detection(self):
        """Test detection of IPv6 loopback address."""
        from agent import _is_localhost_url

        assert _is_localhost_url("http://[::1]:19888") is True
        assert _is_localhost_url("https://[::1]:443") is True
        assert _is_localhost_url("http://[::1]") is True

    def test_localhost_domain_detection(self):
        """Test detection of localhost domain."""
        from agent import _is_localhost_url

        assert _is_localhost_url("http://localhost:19888") is True
        assert _is_localhost_url("https://localhost") is True
        assert _is_localhost_url("http://test.localhost:8080") is True
        assert _is_localhost_url("https://any.localhost") is True

    def test_non_localhost_detection(self):
        """Test that non-localhost URLs return False."""
        from agent import _is_localhost_url

        assert _is_localhost_url("https://example.com") is False
        assert _is_localhost_url("http://192.168.1.1:8080") is False
        assert _is_localhost_url("https://10.0.0.1") is False
        assert _is_localhost_url("http://[2001:db8::1]:8080") is False

    def test_invalid_url_handling(self):
        """Test that invalid URLs return False."""
        from agent import _is_localhost_url

        # These should not raise exceptions
        assert _is_localhost_url("") is False
        assert _is_localhost_url("not a url") is False
        assert _is_localhost_url("://broken") is False


class TestCaBundleConfiguration:
    """Test CA bundle configuration."""

    def test_ca_bundle_from_config_file(self, tmp_path):
        """Test loading CA bundle path from config file."""
        ca_file = tmp_path / "ca-bundle.crt"
        ca_file.write_text("mock CA certificate")

        config_file = tmp_path / "config.json"
        config_file.write_text(f'{{"ca_bundle_path": "{ca_file}"}}')

        with patch.object(AgentConfig, "_save_machine_id"):
            config = AgentConfig(config_path=str(config_file))

        assert config.ca_bundle_path == str(ca_file)

    def test_ca_bundle_from_env_var(self, tmp_path):
        """Test loading CA bundle path from environment variable."""
        ca_file = tmp_path / "ca-bundle.crt"
        ca_file.write_text("mock CA certificate")

        with patch.dict(os.environ, {"OPENACE_CA_BUNDLE_PATH": str(ca_file)}):
            config = AgentConfig()
            assert config.ca_bundle_path == str(ca_file)

    def test_ca_bundle_env_var_priority(self, tmp_path):
        """Test that OPENACE_CA_BUNDLE_PATH takes priority over config file."""
        ca_file_env = tmp_path / "ca-env.crt"
        ca_file_env.write_text("env CA")

        ca_file_config = tmp_path / "ca-config.crt"
        ca_file_config.write_text("config CA")

        config_file = tmp_path / "config.json"
        config_file.write_text(f'{{"ca_bundle_path": "{ca_file_config}"}}')

        with patch.dict(os.environ, {"OPENACE_CA_BUNDLE_PATH": str(ca_file_env)}):
            with patch.object(AgentConfig, "_save_machine_id"):
                config = AgentConfig(config_path=str(config_file))

        # Environment variable should take priority
        assert config.ca_bundle_path == str(ca_file_env)

    def test_get_ssl_verify_setting_with_ca_bundle(self, tmp_path):
        """Test get_ssl_verify_setting returns CA bundle path."""
        ca_file = tmp_path / "ca-bundle.crt"
        ca_file.write_text("mock CA certificate")

        config_file = tmp_path / "config.json"
        config_file.write_text(f'{{"ca_bundle_path": "{ca_file}"}}')

        with patch.object(AgentConfig, "_save_machine_id"):
            config = AgentConfig(config_path=str(config_file))

        verify = config.get_ssl_verify_setting()
        assert verify == str(ca_file)

    def test_get_ssl_verify_setting_with_missing_ca_bundle(self, tmp_path):
        """Test get_ssl_verify_setting falls back when CA bundle is missing."""
        config_file = tmp_path / "config.json"
        config_file.write_text('{"ca_bundle_path": "/nonexistent/path.crt"}')

        with patch.object(AgentConfig, "_save_machine_id"):
            config = AgentConfig(config_path=str(config_file))

        verify = config.get_ssl_verify_setting()
        # Should fall back to True (system default)
        assert verify is True


class TestInsecureMode:
    """Test insecure mode configuration."""

    def test_skip_ssl_verify_from_config(self, tmp_path):
        """Test skip_ssl_verify from config file."""
        config_file = tmp_path / "config.json"
        config_file.write_text('{"skip_ssl_verify": true}')

        with patch.object(AgentConfig, "_save_machine_id"):
            config = AgentConfig(config_path=str(config_file))

        assert config.skip_ssl_verify is True

    def test_skip_ssl_verify_from_env_var(self):
        """Test skip_ssl_verify from environment variable."""
        with patch.dict(os.environ, {"OPENACE_SKIP_SSL_VERIFY": "true"}):
            config = AgentConfig()
            assert config.skip_ssl_verify is True

    def test_insecure_mode_allowed_from_config(self, tmp_path):
        """Test insecure_mode_allowed from config file."""
        config_file = tmp_path / "config.json"
        config_file.write_text('{"insecure_mode_allowed": false}')

        with patch.object(AgentConfig, "_save_machine_id"):
            config = AgentConfig(config_path=str(config_file))

        assert config.insecure_mode_allowed is False

    def test_get_ssl_verify_setting_when_skip(self, tmp_path):
        """Test get_ssl_verify_setting returns False when skip_ssl_verify is true."""
        config_file = tmp_path / "config.json"
        config_file.write_text('{"skip_ssl_verify": true}')

        with patch.object(AgentConfig, "_save_machine_id"):
            config = AgentConfig(config_path=str(config_file))

        verify = config.get_ssl_verify_setting()
        assert verify is False


class TestTerminalRelaySslConfig:
    """Test terminal_relay.py SSL configuration."""

    def test_is_localhost_url_in_terminal_relay(self):
        """Test _is_localhost_url function in terminal_relay.py."""
        # Import from terminal_relay
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "terminal_relay",
            Path(__file__).parent.parent.parent / "remote-agent" / "terminal_relay.py"
        )
        terminal_relay = importlib.util.module_from_spec(spec)

        # Mock websockets import
        with patch.dict(sys.modules, {"websockets": MagicMock()}):
            spec.loader.exec_module(terminal_relay)

            # Test the function
            assert terminal_relay._is_localhost_url("ws://127.0.0.1:8080") is True
            assert terminal_relay._is_localhost_url("ws://[::1]:8080") is True
            assert terminal_relay._is_localhost_url("ws://localhost:8080") is True
            assert terminal_relay._is_localhost_url("ws://192.168.1.1:8080") is False


class TestWebsocketProxySslConfig:
    """Test websocket_proxy.py SSL configuration."""

    def test_ssl_verify_setting_function_exists(self):
        """Test _get_ssl_verify_setting function exists in websocket_proxy.py."""
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "websocket_proxy",
            Path(__file__).parent.parent.parent / "remote-agent" / "websocket_proxy.py"
        )
        websocket_proxy = importlib.util.module_from_spec(spec)

        # Mock websockets import
        with patch.dict(sys.modules, {"websockets": MagicMock()}):
            spec.loader.exec_module(websocket_proxy)

            # Check function exists
            assert hasattr(websocket_proxy, "_get_ssl_verify_setting")
            assert callable(websocket_proxy._get_ssl_verify_setting)


class TestSslErrorDiagnostics:
    """Test SSL error diagnostic logging."""

    def test_log_ssl_error_function_exists(self):
        """Test _log_ssl_error function exists in agent.py."""
        from agent import RemoteAgent

        # Check method exists
        assert hasattr(RemoteAgent, "_log_ssl_error")
        assert callable(getattr(RemoteAgent, "_log_ssl_error"))