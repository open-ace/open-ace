#!/usr/bin/env python3
"""
Pytest fixtures for Open ACE tests.

IMPORTANT: All database tests use isolated temporary databases.
NO test data should ever be written to the production database.
"""

import pytest
import tempfile
import os
import sys
import sqlite3

# Add scripts/shared to path for imports
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
shared_path = os.path.join(project_root, 'scripts', 'shared')
if shared_path not in sys.path:
    sys.path.insert(0, shared_path)


class TestConfig:
    """Test configuration that mimics the real config module."""
    def __init__(self, temp_dir):
        self.CONFIG_DIR = temp_dir
        self.CONFIG_PATH = os.path.join(temp_dir, 'config.json')
        self.DB_DIR = temp_dir
        self.DB_PATH = os.path.join(temp_dir, 'test_usage.db')
        self.REMOTE_USER = 'test_user'
        self.REMOTE_CONFIG_DIR = '/tmp/test-config'
        self.REMOTE_DB_PATH = '/tmp/test-config/usage.db'


@pytest.fixture
def isolated_db(tmp_path):
    """
    Create a completely isolated database for testing.
    
    This fixture ensures tests NEVER touch the production database.
    It creates a fresh database in a temporary directory for each test.
    """
    temp_dir = str(tmp_path)
    
    # Create a test config
    test_config = TestConfig(temp_dir)
    
    # Import db module and override its paths
    import db as db_module
    import config as config_module
    
    # Store original values
    original_db_dir = db_module.DB_DIR
    original_db_path = db_module.DB_PATH
    original_config_dir = config_module.CONFIG_DIR
    original_config_db_dir = config_module.DB_DIR
    original_config_db_path = config_module.DB_PATH
    
    # Override with test paths
    db_module.DB_DIR = temp_dir
    db_module.DB_PATH = test_config.DB_PATH
    config_module.CONFIG_DIR = temp_dir
    config_module.DB_DIR = temp_dir
    config_module.DB_PATH = test_config.DB_PATH
    
    # Also override the config reference inside db module
    db_module.config = test_config
    
    try:
        # Initialize the test database
        db_module.init_database()
        
        # Verify we're using the test database, not production
        assert db_module.DB_PATH == test_config.DB_PATH
        assert not db_module.DB_PATH.endswith('.open-ace/usage.db')
        
        yield db_module
        
    finally:
        # Restore original values
        db_module.DB_DIR = original_db_dir
        db_module.DB_PATH = original_db_path
        config_module.CONFIG_DIR = original_config_dir
        config_module.DB_DIR = original_config_db_dir
        config_module.DB_PATH = original_config_db_path
        
        # Close any open connections
        try:
            conn = sqlite3.connect(test_config.DB_PATH)
            conn.close()
        except:
            pass


@pytest.fixture
def sample_usage_data():
    """Sample usage data for testing."""
    return {
        'date': '2026-03-09',
        'tool_name': 'test_tool',
        'tokens_used': 1000,
        'input_tokens': 800,
        'output_tokens': 200,
        'cache_tokens': 100,
        'request_count': 5,
        'models_used': ['gpt-4', 'claude-3'],
        'host_name': 'test-host'
    }


@pytest.fixture
def sample_message_data():
    """Sample message data for testing."""
    return {
        'date': '2026-03-09',
        'tool_name': 'test_tool',
        'message_id': 'msg_001',
        'parent_id': None,
        'role': 'user',
        'content': 'Hello, this is a test message.',
        'tokens_used': 50,
        'input_tokens': 40,
        'output_tokens': 10,
        'model': 'gpt-4',
        'timestamp': '2026-03-09T10:30:00Z',
        'sender_id': 'user_001',
        'sender_name': 'Test User',
        'host_name': 'test-host'
    }