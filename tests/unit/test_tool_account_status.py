"""
Unit tests for Issue #2761: Tool Account Mapping Source/Status Support

Tests for new mapping source/status fields, conflict tracking, and activation.
"""

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from app.models.user_tool_account import MappingSource, MappingStatus, UserToolAccount
from app.repositories.user_tool_account_repo import UserToolAccountRepository


class TestMappingSourceAndStatus:
    """Tests for mapping_source and mapping_status fields."""

    def test_mapping_source_enum_values(self):
        """Test MappingSource enum has correct values."""
        assert MappingSource.MANUAL.value == "manual"
        assert MappingSource.AUTO.value == "auto"
        assert MappingSource.PREDECLARED.value == "predeclared"
        assert MappingSource.DISCOVERED.value == "discovered"
        assert MappingSource.LEGACY_PREDECLARED.value == "legacy_predeclared"

    def test_mapping_status_enum_values(self):
        """Test MappingStatus enum has correct values."""
        assert MappingStatus.PENDING.value == "pending"
        assert MappingStatus.ACTIVE.value == "active"
        assert MappingStatus.STALE.value == "stale"
        assert MappingStatus.CONFLICT_TYPE.value == "conflict_type"
        assert MappingStatus.CONFLICT_OWNER.value == "conflict_owner"
        assert MappingStatus.CONFLICT_TENANT.value == "conflict_tenant"

    def test_user_tool_account_new_fields(self):
        """Test UserToolAccount model has new fields."""
        account = UserToolAccount(
            id=1,
            user_id=1,
            tool_account="test-account",
            tool_type="qwen",
            mapping_source="predeclared",
            mapping_status="pending",
            discovered_at=datetime.now(),
            last_activity_at=datetime.now(),
            observed_message_count=0,
            created_by=1,
            tenant_id=1,
            version=1,
        )

        assert account.mapping_source == "predeclared"
        assert account.mapping_status == "pending"
        assert account.discovered_at is not None
        assert account.last_activity_at is not None
        assert account.observed_message_count == 0
        assert account.created_by == 1
        assert account.tenant_id == 1
        assert account.version == 1

    def test_user_tool_account_to_dict_includes_new_fields(self):
        """Test to_dict includes new fields."""
        account = UserToolAccount(
            id=1,
            user_id=1,
            tool_account="test-account",
            mapping_source="manual",
            mapping_status="active",
            version=2,
        )

        data = account.to_dict()

        assert "mapping_source" in data
        assert "mapping_status" in data
        assert "version" in data
        assert data["mapping_source"] == "manual"
        assert data["mapping_status"] == "active"
        assert data["version"] == 2


class TestGetByStatus:
    """Tests for get_by_status method."""

    def setup_method(self):
        self.db = MagicMock()
        self.repo = UserToolAccountRepository(db=self.db)

    def test_get_by_status_without_tenant(self):
        """Test get_by_status without tenant filter."""
        self.db.fetch_all.return_value = [
            {
                "id": 1,
                "user_id": 1,
                "tool_account": "test-account",
                "tool_type": "qwen",
                "description": None,
                "created_at": None,
                "updated_at": None,
                "mapping_source": "predeclared",
                "mapping_status": "pending",
                "discovered_at": None,
                "last_activity_at": None,
                "observed_message_count": 0,
                "created_by": None,
                "tenant_id": None,
                "version": 1,
            }
        ]

        with patch("app.repositories.user_tool_account_repo.is_postgresql", return_value=False):
            result = self.repo.get_by_status("pending")

        assert len(result) == 1
        assert result[0].mapping_status == "pending"

        # Verify query structure
        call_args = self.db.fetch_all.call_args
        query = call_args[0][0]
        assert "mapping_status" in query
        params = call_args[0][1]
        assert params[0] == "pending"

    def test_get_by_status_with_tenant(self):
        """Test get_by_status with tenant filter."""
        self.db.fetch_all.return_value = []

        with patch("app.repositories.user_tool_account_repo.is_postgresql", return_value=False):
            result = self.repo.get_by_status("pending", tenant_id=1)

        assert result == []

        # Verify query includes tenant_id
        call_args = self.db.fetch_all.call_args
        query = call_args[0][0]
        assert "tenant_id" in query
        params = call_args[0][1]
        assert params == ("pending", 1)


class TestGetPendingForActivation:
    """Tests for get_pending_for_activation method."""

    def setup_method(self):
        self.db = MagicMock()
        self.repo = UserToolAccountRepository(db=self.db)

    def test_get_pending_for_activation_empty_list(self):
        """Test with empty sender_names list."""
        result = self.repo.get_pending_for_activation([])
        assert result == []

    def test_get_pending_for_activation_single_sender(self):
        """Test with single sender_name."""
        self.db.fetch_all.return_value = [
            {
                "id": 1,
                "user_id": 1,
                "tool_account": "alice-macbook-qwen",
                "tool_type": "qwen",
                "description": None,
                "created_at": None,
                "updated_at": None,
                "mapping_source": "predeclared",
                "mapping_status": "pending",
                "discovered_at": None,
                "last_activity_at": None,
                "observed_message_count": 0,
                "created_by": 1,
                "tenant_id": None,
                "version": 1,
            }
        ]

        with patch("app.repositories.user_tool_account_repo.is_postgresql", return_value=False):
            result = self.repo.get_pending_for_activation(["alice-macbook-qwen"])

        assert len(result) == 1
        assert result[0].tool_account == "alice-macbook-qwen"
        assert result[0].mapping_status == "pending"

        # Verify query structure
        call_args = self.db.fetch_all.call_args
        query = call_args[0][0]
        assert "IN (" in query
        assert "mapping_status = 'pending'" in query
        params = call_args[0][1]
        assert params == ("alice-macbook-qwen",)

    def test_get_pending_for_activation_multiple_senders(self):
        """Test with multiple sender_names."""
        self.db.fetch_all.return_value = []

        with patch("app.repositories.user_tool_account_repo.is_postgresql", return_value=False):
            result = self.repo.get_pending_for_activation(["sender1", "sender2"])

        assert result == []

        # Verify query uses IN clause
        call_args = self.db.fetch_all.call_args
        query = call_args[0][0]
        assert "IN (?,?,)" in query.replace("?, ?", "?,?")


class TestUpdateStatusWithVersion:
    """Tests for update_status_with_version method (optimistic locking)."""

    def setup_method(self):
        self.db = MagicMock()
        self.repo = UserToolAccountRepository(db=self.db)

    def test_update_status_success(self):
        """Test successful status update with correct version."""
        self.db.fetch_one.return_value = {
            "id": 1,
            "user_id": 1,
            "tool_account": "test-account",
            "tool_type": "qwen",
            "description": None,
            "created_at": None,
            "updated_at": None,
            "mapping_source": "predeclared",
            "mapping_status": "active",
            "discovered_at": None,
            "last_activity_at": None,
            "observed_message_count": 0,
            "created_by": None,
            "tenant_id": None,
            "version": 2,
        }

        with patch("app.repositories.user_tool_account_repo.is_postgresql", return_value=False):
            result = self.repo.update_status_with_version(1, "active", expected_version=1)

        assert result is not None
        assert result.mapping_status == "active"

    def test_update_status_version_mismatch(self):
        """Test status update fails when version doesn't match."""
        self.db.fetch_one.return_value = None  # No row returned due to version mismatch

        with patch("app.repositories.user_tool_account_repo.is_postgresql", return_value=False):
            result = self.repo.update_status_with_version(1, "active", expected_version=999)

        assert result is None


class TestActivateMapping:
    """Tests for activate_mapping method."""

    def setup_method(self):
        self.db = MagicMock()
        self.repo = UserToolAccountRepository(db=self.db)

    def test_activate_mapping_success(self):
        """Test successful activation of pending mapping."""
        self.db.fetch_one.return_value = {
            "id": 1,
            "user_id": 1,
            "tool_account": "test-account",
            "tool_type": "qwen",
            "description": None,
            "created_at": None,
            "updated_at": None,
            "mapping_source": "discovered",
            "mapping_status": "active",
            "discovered_at": datetime.now(),
            "last_activity_at": datetime.now(),
            "observed_message_count": 0,
            "created_by": None,
            "tenant_id": None,
            "version": 2,
        }

        with patch("app.repositories.user_tool_account_repo.is_postgresql", return_value=False):
            result = self.repo.activate_mapping(1, expected_version=1)

        assert result is not None
        assert result.mapping_status == "active"


class TestTouchActivity:
    """Tests for touch_activity method."""

    def setup_method(self):
        self.db = MagicMock()
        self.repo = UserToolAccountRepository(db=self.db)

    def test_touch_activity_success(self):
        """Test successful activity timestamp update."""
        self.db.execute.return_value = None

        result = self.repo.touch_activity(1)

        assert result is True
        self.db.execute.assert_called_once()

    def test_touch_activity_exception(self):
        """Test touch_activity handles exceptions."""
        self.db.execute.side_effect = Exception("DB error")

        result = self.repo.touch_activity(1)

        assert result is False


class TestIncrementMessageCount:
    """Tests for increment_message_count method."""

    def setup_method(self):
        self.db = MagicMock()
        self.repo = UserToolAccountRepository(db=self.db)

    def test_increment_message_count_success(self):
        """Test successful message count increment."""
        self.db.execute.return_value = None

        result = self.repo.increment_message_count(1, count=10)

        assert result is True
        call_args = self.db.execute.call_args
        query = call_args[0][0]
        assert "observed_message_count = observed_message_count + ?" in query
        params = call_args[0][1]
        assert params == (10, 1)


class TestCreateOrIgnore:
    """Tests for create_or_ignore method."""

    def setup_method(self):
        self.db = MagicMock()
        self.repo = UserToolAccountRepository(db=self.db)

    def test_create_or_ignore_success(self):
        """Test successful creation when no conflict."""
        self.db.fetch_one.return_value = {
            "id": 1,
            "user_id": 1,
            "tool_account": "test-account",
            "tool_type": "qwen",
            "description": None,
            "created_at": None,
            "updated_at": None,
            "mapping_source": "predeclared",
            "mapping_status": "pending",
            "discovered_at": None,
            "last_activity_at": None,
            "observed_message_count": 0,
            "created_by": 1,
            "tenant_id": 1,
            "version": 1,
        }

        with patch("app.repositories.user_tool_account_repo.is_postgresql", return_value=False):
            result = self.repo.create_or_ignore(
                user_id=1,
                tool_account="test-account",
                tool_type="qwen",
                mapping_source="predeclared",
                mapping_status="pending",
                created_by=1,
                tenant_id=1,
            )

        assert result is not None
        assert result.mapping_status == "pending"

    def test_create_or_ignore_conflict(self):
        """Test returns None when conflict exists."""
        self.db.execute.side_effect = Exception("UNIQUE constraint failed")
        self.db.fetch_one.return_value = None

        with patch("app.repositories.user_tool_account_repo.is_postgresql", return_value=False):
            result = self.repo.create_or_ignore(
                user_id=1,
                tool_account="existing-account",
            )

        assert result is None


class TestRowToModelWithNewFields:
    """Tests for _row_to_model with new fields."""

    def setup_method(self):
        self.db = MagicMock()
        self.repo = UserToolAccountRepository(db=self.db)

    def test_row_to_model_with_new_fields(self):
        """Test _row_to_model correctly maps new fields."""
        row = {
            "id": 1,
            "user_id": 1,
            "tool_account": "test-account",
            "tool_type": "qwen",
            "description": "Test",
            "created_at": datetime.now(),
            "updated_at": datetime.now(),
            "mapping_source": "predeclared",
            "mapping_status": "pending",
            "discovered_at": None,
            "last_activity_at": None,
            "observed_message_count": 0,
            "created_by": 1,
            "tenant_id": 1,
            "version": 1,
        }

        result = self.repo._row_to_model(row)

        assert result.mapping_source == "predeclared"
        assert result.mapping_status == "pending"
        assert result.observed_message_count == 0
        assert result.created_by == 1
        assert result.tenant_id == 1
        assert result.version == 1

    def test_row_to_model_handles_null_new_fields(self):
        """Test _row_to_model handles NULL values for new fields."""
        row = {
            "id": 1,
            "user_id": 1,
            "tool_account": "test-account",
            "tool_type": None,
            "description": None,
            "created_at": None,
            "updated_at": None,
            "mapping_source": None,
            "mapping_status": None,
            "discovered_at": None,
            "last_activity_at": None,
            "observed_message_count": None,
            "created_by": None,
            "tenant_id": None,
            "version": None,
        }

        result = self.repo._row_to_model(row)

        assert result.mapping_source is None
        assert result.mapping_status is None
        assert result.observed_message_count == 0
        assert result.created_by is None
        assert result.tenant_id is None
        assert result.version == 1  # Default value