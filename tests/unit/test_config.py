#!/usr/bin/env python3
"""
Unit tests for config.py module.
"""

import importlib.util
import os
import sys

# Issue #2588: Ensure we import scripts/shared/config.py, not remote-agent/config.py
# Other tests (e.g., test_remote_agent_tls.py) may have added remote-agent to sys.path,
# and imported it into sys.modules['config']. We need to import the correct module directly.

# Use __file__ to get the current test file path, then navigate to project root
# test_config.py -> unit -> tests -> project_root
_current_file = os.path.abspath(__file__)
unit_dir = os.path.dirname(_current_file)  # tests/unit
tests_dir = os.path.dirname(unit_dir)  # tests
project_root = os.path.dirname(tests_dir)  # project root
config_path = os.path.join(project_root, "scripts", "shared", "config.py")

# Load the module directly from its file path
# Use a unique name to avoid conflicts with sys.modules['config']
spec = importlib.util.spec_from_file_location("_shared_config", config_path)
if spec is None or spec.loader is None:
    raise ImportError(f"Could not load config module from {config_path}")
_shared_config = importlib.util.module_from_spec(spec)
spec.loader.exec_module(_shared_config)

# For backwards compatibility with the test code, use 'config' as the local variable
config = _shared_config


class TestConfigPaths:
    """Tests for configuration paths."""

    def test_config_dir_is_expanded(self):
        """Test that CONFIG_DIR path is expanded from ~."""
        # Should not contain ~
        assert "~" not in config.CONFIG_DIR
        assert config.CONFIG_DIR.endswith(".open-ace")

    def test_db_path_in_config_dir(self):
        """Test that DB_PATH is within CONFIG_DIR."""
        assert config.DB_DIR == config.CONFIG_DIR
        assert config.DB_PATH.startswith(config.DB_DIR)

    def test_remote_user_default(self):
        """Test default remote user."""
        assert config.REMOTE_USER == "openclaw"

    def test_remote_paths(self):
        """Test remote path configuration."""
        assert "/.open-ace" in config.REMOTE_CONFIG_DIR
        assert "ace.db" in config.REMOTE_DB_PATH


class TestConfigFunctions:
    """Tests for config helper functions."""

    def test_ensure_config_dir(self, tmp_path):
        """Test ensure_config_dir creates directory."""
        test_dir = str(tmp_path / "test_config")
        original_dir = config.CONFIG_DIR
        config.CONFIG_DIR = test_dir

        try:
            config.ensure_config_dir()
            assert os.path.exists(test_dir)
        finally:
            config.CONFIG_DIR = original_dir

    def test_ensure_db_dir(self, tmp_path):
        """Test ensure_db_dir creates directory."""
        test_dir = str(tmp_path / "test_db")
        original_dir = config.DB_DIR
        config.DB_DIR = test_dir

        try:
            config.ensure_db_dir()
            assert os.path.exists(test_dir)
        finally:
            config.DB_DIR = original_dir
