#!/usr/bin/env python3
"""
Unit tests for db.py module.

Uses monkeypatch to properly isolate database operations.
"""

import pytest
import os
import sys
import tempfile

# Add scripts/shared to path for imports
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
shared_path = os.path.join(project_root, 'scripts', 'shared')
if shared_path not in sys.path:
    sys.path.insert(0, shared_path)


class TestDatabaseInit:
    """Tests for database initialization."""
    
    def test_init_database(self, tmp_path, monkeypatch):
        """Test database initialization creates tables."""
        import config
        monkeypatch.setattr(config, 'CONFIG_DIR', str(tmp_path))
        monkeypatch.setattr(config, 'DB_DIR', str(tmp_path))
        monkeypatch.setattr(config, 'DB_PATH', str(tmp_path / "test.db"))
        
        # Import db after patching config
        import db
        monkeypatch.setattr(db, 'DB_DIR', str(tmp_path))
        monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / "test.db"))
        
        db.init_database()
        
        assert os.path.exists(str(tmp_path / "test.db"))


class TestUsageOperations:
    """Tests for usage data operations."""
    
    @pytest.fixture
    def isolated_db(self, tmp_path, monkeypatch):
        """Create isolated database for each test."""
        import config
        monkeypatch.setattr(config, 'CONFIG_DIR', str(tmp_path))
        monkeypatch.setattr(config, 'DB_DIR', str(tmp_path))
        monkeypatch.setattr(config, 'DB_PATH', str(tmp_path / "test.db"))
        
        import db
        monkeypatch.setattr(db, 'DB_DIR', str(tmp_path))
        monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / "test.db"))
        
        db.init_database()
        return db
    
    def test_save_usage(self, isolated_db):
        """Test saving usage data."""
        result = isolated_db.save_usage(
            date='2026-03-09',
            tool_name='test_tool',
            tokens_used=1000,
            input_tokens=800,
            output_tokens=200,
            request_count=5,
            models_used=['gpt-4'],
            host_name='test-host'
        )
        assert result is True
    
    def test_get_usage_by_date(self, isolated_db):
        """Test retrieving usage by date."""
        isolated_db.save_usage(
            date='2026-03-09',
            tool_name='test_tool',
            tokens_used=1000,
            input_tokens=800,
            output_tokens=200,
            request_count=5,
            models_used=['gpt-4'],
            host_name='test-host'
        )
        
        results = isolated_db.get_usage_by_date('2026-03-09')
        
        assert len(results) == 1
        assert results[0]['tool_name'] == 'test_tool'
        assert results[0]['tokens_used'] == 1000
        assert results[0]['models_used'] == ['gpt-4']
    
    def test_get_usage_by_tool(self, isolated_db):
        """Test retrieving usage by tool."""
        isolated_db.save_usage(date='2026-03-09', tool_name='claude', tokens_used=1000, host_name='host1')
        isolated_db.save_usage(date='2026-03-08', tool_name='claude', tokens_used=2000, host_name='host1')
        isolated_db.save_usage(date='2026-03-09', tool_name='gpt', tokens_used=500, host_name='host1')
        
        results = isolated_db.get_usage_by_tool('claude', days=7)
        
        assert len(results) == 2
    
    def test_get_all_tools(self, isolated_db):
        """Test retrieving all tools."""
        isolated_db.save_usage(date='2026-03-09', tool_name='claude', tokens_used=100, host_name='h1')
        isolated_db.save_usage(date='2026-03-09', tool_name='gpt', tokens_used=200, host_name='h1')
        
        tools = isolated_db.get_all_tools()
        
        assert 'claude' in tools
        assert 'gpt' in tools


class TestMessageOperations:
    """Tests for message data operations."""
    
    @pytest.fixture
    def isolated_db(self, tmp_path, monkeypatch):
        """Create isolated database for each test."""
        import config
        monkeypatch.setattr(config, 'CONFIG_DIR', str(tmp_path))
        monkeypatch.setattr(config, 'DB_DIR', str(tmp_path))
        monkeypatch.setattr(config, 'DB_PATH', str(tmp_path / "test.db"))
        
        import db
        monkeypatch.setattr(db, 'DB_DIR', str(tmp_path))
        monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / "test.db"))
        
        db.init_database()
        return db
    
    def test_save_message(self, isolated_db):
        """Test saving a message."""
        result = isolated_db.save_message(
            date='2026-03-09',
            tool_name='test_tool',
            message_id='msg_001',
            role='user',
            content='Hello',
            tokens_used=10,
            host_name='test-host'
        )
        assert result is True
    
    def test_get_messages_by_date(self, isolated_db):
        """Test retrieving messages by date."""
        isolated_db.save_message(
            date='2026-03-09',
            tool_name='claude',
            message_id='msg_001',
            role='user',
            content='Hello',
            tokens_used=10,
            host_name='h1'
        )
        isolated_db.save_message(
            date='2026-03-09',
            tool_name='claude',
            message_id='msg_002',
            role='assistant',
            content='Hi there!',
            tokens_used=20,
            host_name='h1'
        )
        
        result = isolated_db.get_messages_by_date('2026-03-09')
        
        assert result['total'] == 2
        assert len(result['messages']) == 2
    
    def test_get_messages_by_date_with_filters(self, isolated_db):
        """Test retrieving messages with filters."""
        isolated_db.save_message(
            date='2026-03-09',
            tool_name='claude',
            message_id='msg_001',
            role='user',
            content='Hello',
            tokens_used=10,
            host_name='h1'
        )
        isolated_db.save_message(
            date='2026-03-09',
            tool_name='gpt',
            message_id='msg_002',
            role='assistant',
            content='Hi there!',
            tokens_used=20,
            host_name='h1'
        )
        
        # Filter by tool
        result = isolated_db.get_messages_by_date('2026-03-09', tool_name='claude')
        assert result['total'] == 1
        assert result['messages'][0]['tool_name'] == 'claude'


class TestTimestampFormatting:
    """Tests for timestamp formatting functions."""
    
    def test_format_timestamp_to_cst_standard(self):
        """Test formatting standard ISO timestamp."""
        import db
        result = db.format_timestamp_to_cst("2026-03-03T12:21:31.917Z")
        # UTC 12:21 + 8 hours = CST 20:21
        assert result == "2026-03-03 20:21:31"
    
    def test_format_timestamp_to_cst_with_space(self):
        """Test formatting timestamp with space instead of T."""
        import db
        result = db.format_timestamp_to_cst("2026-03-03 04:21:31.917Z")
        # UTC 04:21 + 8 hours = CST 12:21
        assert result == "2026-03-03 12:21:31"
    
    def test_format_timestamp_empty(self):
        """Test formatting empty timestamp."""
        import db
        assert db.format_timestamp_to_cst("") == ""
        assert db.format_timestamp_to_cst(None) == ""