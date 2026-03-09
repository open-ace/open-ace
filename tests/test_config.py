#!/usr/bin/env python3
"""
Unit tests for config.py module.
"""

import pytest
import os
import sys

# Add scripts/shared to path for imports
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
shared_path = os.path.join(project_root, 'scripts', 'shared')
if shared_path not in sys.path:
    sys.path.insert(0, shared_path)

import config


class TestConfigPaths:
    """Tests for configuration paths."""
    
    def test_config_dir_is_expanded(self):
        """Test that CONFIG_DIR path is expanded from ~."""
        # Should not contain ~
        assert '~' not in config.CONFIG_DIR
        assert config.CONFIG_DIR.endswith('.ai-token-analyzer')
    
    def test_db_path_in_config_dir(self):
        """Test that DB_PATH is within CONFIG_DIR."""
        assert config.DB_DIR == config.CONFIG_DIR
        assert config.DB_PATH.startswith(config.DB_DIR)
    
    def test_remote_user_default(self):
        """Test default remote user."""
        assert config.REMOTE_USER == 'openclaw'
    
    def test_remote_paths(self):
        """Test remote path configuration."""
        assert '/.ai-token-analyzer' in config.REMOTE_CONFIG_DIR
        assert 'usage.db' in config.REMOTE_DB_PATH


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