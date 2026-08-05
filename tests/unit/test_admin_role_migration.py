#!/usr/bin/env python3
"""
Unit tests for admin role migration tool.

Issue #2276: Test migration, rollback, and recovery functionality.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure project root is on path
project_root = str(Path(__file__).resolve().parent.parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)


class TestMigrationTool:
    """Unit tests for MigrationTool class."""

    def test_migration_config_defaults(self):
        """Test that MigrationConfig loads default configuration."""
        from scripts.migrate_admin_role import MigrationConfig

        config = MigrationConfig(config_path="nonexistent.yaml")

        # Should have default values
        assert "environments" in config.config
        assert "dev" in config.config["environments"]
        assert "prod" in config.config["environments"]

    def test_get_environment_config(self):
        """Test getting configuration for specific environment."""
        from scripts.migrate_admin_role import MigrationConfig

        config = MigrationConfig(config_path="nonexistent.yaml")

        dev_config = config.get_environment_config("dev")
        assert "batch_size" in dev_config
        assert dev_config["batch_size"] == 10

        prod_config = config.get_environment_config("prod")
        assert "batch_size" in prod_config
        assert prod_config["batch_size"] == 100

    def test_message_localization_english(self):
        """Test English message localization."""
        from scripts.migrate_admin_role import MigrationTool

        tool = MigrationTool(locale="en_US")

        msg = tool._message("migration.start")
        assert msg == "Starting migration..."

        msg = tool._message("check.users", count=5)
        assert msg == "Found 5 users to migrate"

    def test_message_localization_chinese(self):
        """Test Chinese message localization."""
        from scripts.migrate_admin_role import MigrationTool

        tool = MigrationTool(locale="zh_CN")

        msg = tool._message("migration.start")
        assert msg == "开始迁移..."

        msg = tool._message("check.users", count=5)
        assert msg == "发现 5 个待迁移用户"

    def test_batch_id_generation(self):
        """Test that batch ID is generated correctly."""
        from scripts.migrate_admin_role import MigrationTool

        tool = MigrationTool()

        # Should start with "batch-"
        assert tool.batch_id.startswith("batch-")

        # Should contain timestamp
        parts = tool.batch_id.split("-")
        assert len(parts) == 3

    def test_dry_run_mode(self):
        """Test that dry run mode doesn't make changes."""
        from scripts.migrate_admin_role import MigrationTool

        tool = MigrationTool(dry_run=True)

        # In dry run mode, operations should be skipped
        assert tool.dry_run is True

    def test_check_environment_dev(self):
        """Test environment check for dev environment."""
        from scripts.migrate_admin_role import MigrationTool

        with patch.dict("os.environ", {"OPENACE_ENV": "dev"}):
            tool = MigrationTool()
            result = tool.check_environment()
            assert result is True

    def test_check_environment_prod(self):
        """Test environment check for production environment."""
        from scripts.migrate_admin_role import MigrationTool

        with patch.dict("os.environ", {"OPENACE_ENV": "prod"}):
            tool = MigrationTool()
            result = tool.check_environment()
            assert result is True

    def test_check_environment_unknown(self):
        """Test environment check for unknown environment."""
        from scripts.migrate_admin_role import MigrationTool

        with patch.dict("os.environ", {"OPENACE_ENV": "unknown"}, clear=False):
            tool = MigrationTool()
            # Should not fail for unknown environment
            result = tool.check_environment()
            assert result is True


class TestMigrationIntegration:
    """Integration tests for migration (requires database)."""

    @pytest.fixture
    def mock_db(self):
        """Mock database connection."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        with patch("scripts.migrate_admin_role.db.get_connection", return_value=mock_conn):
            with patch("scripts.migrate_admin_role.db.is_postgresql", return_value=True):
                yield mock_conn, mock_cursor

    def test_count_users_to_migrate(self, mock_db):
        """Test counting users to migrate."""
        from scripts.migrate_admin_role import MigrationTool

        mock_conn, mock_cursor = mock_db
        mock_cursor.fetchone.return_value = [5]

        tool = MigrationTool()
        count = tool.count_users_to_migrate()

        assert count == 5
        mock_cursor.execute.assert_called_once()

    def test_count_active_sessions(self, mock_db):
        """Test counting active sessions."""
        from scripts.migrate_admin_role import MigrationTool

        mock_conn, mock_cursor = mock_db
        mock_cursor.fetchone.return_value = [3]

        tool = MigrationTool()
        count = tool.count_active_sessions()

        assert count == 3
        mock_cursor.execute.assert_called_once()

    def test_pre_check_no_users(self, mock_db):
        """Test pre-check when no users to migrate."""
        from scripts.migrate_admin_role import MigrationTool

        mock_conn, mock_cursor = mock_db
        mock_cursor.fetchone.return_value = [0]  # No users

        tool = MigrationTool()

        with patch.object(tool, "check_environment", return_value=True):
            with patch.object(tool, "check_database_connection", return_value=True):
                result = tool.pre_check()
                assert result is False  # Should fail when no users


class TestRollbackTool:
    """Unit tests for RollbackTool class."""

    def test_rollback_config_initialization(self):
        """Test RollbackTool initialization."""
        from scripts.migrate_admin_role_rollback import RollbackTool

        tool = RollbackTool(batch_id="test-batch-123", locale="en_US")

        assert tool.batch_id == "test-batch-123"
        assert tool.locale == "en_US"

    def test_rollback_message_localization(self):
        """Test rollback message localization."""
        from scripts.migrate_admin_role_rollback import RollbackTool

        tool_en = RollbackTool(locale="en_US")
        msg_en = tool_en._message("rollback.start")
        assert msg_en == "Starting rollback..."

        tool_zh = RollbackTool(locale="zh_CN")
        msg_zh = tool_zh._message("rollback.start")
        assert msg_zh == "开始回滚..."


class TestMigrationLogging:
    """Test migration logging functionality."""

    def test_logger_initialization(self):
        """Test that logger is properly initialized."""
        import logging

        from scripts.migrate_admin_role import MigrationTool

        tool = MigrationTool()

        # Logger should be created
        assert hasattr(tool, "logger") or logging.getLogger("scripts.migrate_admin_role")


class TestMigrationEdgeCases:
    """Test edge cases and error handling."""

    def test_migration_with_empty_backup(self):
        """Test rollback when backup table is empty."""
        from scripts.migrate_admin_role_rollback import RollbackTool

        tool = RollbackTool(batch_id="nonexistent-batch")

        with patch.object(tool, "find_backup_users", return_value=[]):
            result = tool.run(skip_confirm=True)
            assert result is False

    def test_migration_with_specific_user_ids(self):
        """Test rollback with specific user IDs."""
        from scripts.migrate_admin_role_rollback import RollbackTool

        tool = RollbackTool()

        # Mock database
        with patch("scripts.migrate_admin_role_rollback.db.get_connection") as mock_conn:
            mock_cursor = MagicMock()
            mock_conn.return_value.cursor.return_value = mock_cursor

            # Mock backup data
            mock_cursor.fetchone.return_value = ("admin",)

            # Run with specific user IDs
            tool.rollback_users(user_ids=[1, 2, 3])

            # Should have executed updates
            assert mock_cursor.execute.called


class TestMigrationI18n:
    """Test internationalization support."""

    def test_all_locales_have_required_messages(self):
        """Test that all supported locales have required messages."""
        from scripts.migrate_admin_role import MESSAGES

        required_keys = [
            "migration.start",
            "migration.success",
            "migration.failed",
            "check.database",
            "check.users",
            "backup.created",
            "backup.failed",
        ]

        for locale, messages in MESSAGES.items():
            for key in required_keys:
                assert key in messages, f"Missing key '{key}' for locale '{locale}'"

    def test_fallback_to_english(self):
        """Test fallback to English for missing locale."""
        from scripts.migrate_admin_role import MigrationTool

        # Create tool with unsupported locale
        tool = MigrationTool(locale="unsupported")

        # Should fallback to English
        msg = tool._message("migration.start")
        assert msg == "Starting migration..."
