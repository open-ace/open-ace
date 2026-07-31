"""
Unit tests for tenant migration service (Issue #2163).
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import Mock, patch, MagicMock

from app.services.tenant_migration import (
    TenantMigrationService,
    MigrationResult,
)


class TestTenantMigrationService:
    """Test suite for TenantMigrationService."""

    @pytest.fixture
    def mock_db(self):
        """Create mock database."""
        db = Mock()
        db.transaction = MagicMock()
        db.execute = Mock()
        db.fetch_one = Mock()
        return db

    @pytest.fixture
    def service(self, mock_db):
        """Create migration service with mock database."""
        return TenantMigrationService(db=mock_db)

    def test_migrate_user_tenant_user_not_found(self, service, mock_db):
        """Test migration when user is not found."""
        mock_db.fetch_one.return_value = None

        result = service.migrate_user_tenant(
            user_id=999,
            new_tenant_id=2,
            migrated_by=1
        )

        assert not result.success
        assert result.error == "User not found"
        assert result.user_id == 999

    def test_migrate_user_tenant_same_tenant(self, service, mock_db):
        """Test migration to same tenant (no-op)."""
        mock_db.fetch_one.return_value = {
            "tenant_id": 1,
            "tenant_version": 1
        }

        result = service.migrate_user_tenant(
            user_id=1,
            new_tenant_id=1,
            migrated_by=2
        )

        assert result.success
        assert result.old_tenant_id == 1
        assert result.new_tenant_id == 1
        assert result.affected_sessions == 0
        assert result.affected_projects == 0

    def test_migrate_user_tenant_dry_run(self, service, mock_db):
        """Test dry run migration."""
        mock_db.fetch_one.side_effect = [
            {"tenant_id": 1, "tenant_version": 1},  # user query
            {"count": 5},  # sessions count
            {"count": 2},  # projects count
        ]

        result = service.migrate_user_tenant(
            user_id=1,
            new_tenant_id=2,
            migrated_by=2,
            dry_run=True
        )

        assert result.success
        assert result.affected_sessions == 5
        assert result.affected_projects == 2
        # Should not execute actual migration
        assert not mock_db.execute.called

    def test_migrate_user_tenant_success(self, service, mock_db):
        """Test successful user migration."""
        # Mock user query
        mock_db.fetch_one.return_value = {
            "tenant_id": 1,
            "tenant_version": 1
        }

        # Mock database type detection
        service._get_database_type = Mock(return_value='sqlite')

        # Mock transaction context
        mock_transaction = MagicMock()
        mock_transaction.__enter__ = Mock()
        mock_transaction.__exit__ = Mock()
        mock_db.transaction.return_value = mock_transaction

        # Mock execute
        mock_db.execute.return_value = None

        result = service.migrate_user_tenant(
            user_id=1,
            new_tenant_id=2,
            migrated_by=2
        )

        assert result.success
        assert result.old_tenant_id == 1
        assert result.new_tenant_id == 2
        # Verify transaction was used
        assert mock_db.transaction.called

    def test_migrate_users_batch_success(self, service, mock_db):
        """Test batch migration."""
        # Mock user queries
        mock_db.fetch_one.return_value = {
            "tenant_id": 1,
            "tenant_version": 1
        }

        # Mock database type detection
        service._get_database_type = Mock(return_value='sqlite')

        # Mock transaction
        mock_transaction = MagicMock()
        mock_transaction.__enter__ = Mock()
        mock_transaction.__exit__ = Mock()
        mock_db.transaction.return_value = mock_transaction

        mock_db.execute.return_value = None

        user_ids = [1, 2, 3]
        results = service.migrate_users_batch(
            user_ids=user_ids,
            new_tenant_id=2,
            migrated_by=10,
            batch_size=2
        )

        assert len(results) == 3
        assert all(r.success for r in results)

    def test_migrate_users_batch_stops_on_failure(self, service, mock_db):
        """Test batch migration stops on first failure."""
        # Mock database type detection
        service._get_database_type = Mock(return_value='sqlite')

        mock_db.fetch_one.side_effect = [
            {"tenant_id": 1, "tenant_version": 1},  # success
            None,  # user not found - failure
        ]

        # Mock transaction
        mock_transaction = MagicMock()
        mock_transaction.__enter__ = Mock()
        mock_transaction.__exit__ = Mock()
        mock_db.transaction.return_value = mock_transaction

        mock_db.execute.return_value = None

        user_ids = [1, 2, 3]
        results = service.migrate_users_batch(
            user_ids=user_ids,
            new_tenant_id=2,
            migrated_by=10,
            batch_size=10
        )

        assert len(results) == 3
        assert results[0].success
        assert not results[1].success
        assert not results[2].success
        assert "Batch migration stopped" in results[2].error

    def test_get_migration_progress_not_found(self, service, mock_db):
        """Test getting progress for non-existent migration."""
        mock_db.fetch_one.return_value = None

        result = service.get_migration_progress(999)

        assert result is None

    def test_get_migration_progress_success(self, service, mock_db):
        """Test getting migration progress."""
        mock_db.fetch_one.return_value = {
            "id": 1,
            "user_id": 123,
            "old_tenant_id": 1,
            "new_tenant_id": 2,
            "status": "completed"
        }

        result = service.get_migration_progress(1)

        assert result is not None
        assert result["id"] == 1
        assert result["user_id"] == 123

    def test_validate_migration_possible_user_not_found(self, service, mock_db):
        """Test validation when user not found."""
        mock_db.fetch_one.return_value = None

        is_possible, error = service.validate_migration_possible(
            user_id=999,
            new_tenant_id=2
        )

        assert not is_possible
        assert "User not found" in error

    def test_validate_migration_possible_tenant_not_found(self, service, mock_db):
        """Test validation when tenant not found."""
        mock_db.fetch_one.side_effect = [
            {"id": 1, "tenant_id": 1},  # user exists
            None,  # tenant not found
        ]

        is_possible, error = service.validate_migration_possible(
            user_id=1,
            new_tenant_id=999
        )

        assert not is_possible
        assert "Target tenant not found" in error

    def test_validate_migration_possible_success(self, service, mock_db):
        """Test successful validation."""
        mock_db.fetch_one.side_effect = [
            {"id": 1, "tenant_id": 1},  # user exists
            {"id": 2},  # tenant exists
        ]

        is_possible, error = service.validate_migration_possible(
            user_id=1,
            new_tenant_id=2
        )

        assert is_possible
        assert error == ""

    def test_rollback_migration_not_found(self, service, mock_db):
        """Test rollback when migration not found."""
        mock_db.fetch_one.return_value = None

        result = service.rollback_migration(999)

        assert not result

    def test_rollback_migration_not_completed(self, service, mock_db):
        """Test rollback when migration not in completed state."""
        mock_db.fetch_one.return_value = {
            "id": 1,
            "user_id": 1,
            "old_tenant_id": 1,
            "new_tenant_id": 2,
            "status": "pending",
            "migrated_by": 2
        }

        result = service.rollback_migration(1)

        assert not result

    def test_rollback_migration_success(self, service, mock_db):
        """Test successful rollback."""
        # Mock get_migration_progress
        mock_db.fetch_one.side_effect = [
            {
                "id": 1,
                "user_id": 1,
                "old_tenant_id": 1,
                "new_tenant_id": 2,
                "status": "completed",
                "migrated_by": 2
            },
            {"tenant_id": 2, "tenant_version": 2},  # user query for reverse migration
            {"tenant_id": 2, "tenant_version": 2},  # user query in migrate_user_tenant
        ]

        # Mock database type detection
        service._get_database_type = Mock(return_value='sqlite')

        # Mock transaction
        mock_transaction = MagicMock()
        mock_transaction.__enter__ = Mock()
        mock_transaction.__exit__ = Mock()
        mock_db.transaction.return_value = mock_transaction

        mock_db.execute.return_value = None

        result = service.rollback_migration(1)

        # Should execute rollback
        assert mock_db.execute.called


class TestMigrationResult:
    """Test suite for MigrationResult dataclass."""

    def test_migration_result_creation(self):
        """Test creating MigrationResult."""
        result = MigrationResult(
            success=True,
            user_id=1,
            old_tenant_id=1,
            new_tenant_id=2,
            affected_sessions=5,
            affected_projects=3
        )

        assert result.success
        assert result.user_id == 1
        assert result.affected_sessions == 5
        assert result.error is None

    def test_migration_result_with_error(self):
        """Test MigrationResult with error."""
        result = MigrationResult(
            success=False,
            user_id=1,
            old_tenant_id=1,
            new_tenant_id=2,
            error="Test error"
        )

        assert not result.success
        assert result.error == "Test error"