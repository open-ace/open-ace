"""
Unit tests for TenantResolver (Issue #2163).
"""

from unittest.mock import Mock

import pytest

from app.utils.tenant_resolver import TenantResolutionError, TenantResolver


class TestTenantResolver:
    """Test suite for TenantResolver."""

    def test_normalize_none(self):
        """Test normalizing None value."""
        assert TenantResolver.normalize(None) is None

    def test_normalize_empty_string(self):
        """Test normalizing empty string."""
        assert TenantResolver.normalize("") is None

    def test_normalize_zero(self):
        """Test normalizing zero."""
        assert TenantResolver.normalize(0) is None
        assert TenantResolver.normalize("0") is None

    def test_normalize_positive_integer(self):
        """Test normalizing positive integer."""
        assert TenantResolver.normalize(123) == 123

    def test_normalize_positive_string(self):
        """Test normalizing positive integer string."""
        assert TenantResolver.normalize("456") == 456

    def test_normalize_negative_integer(self):
        """Test normalizing negative integer returns None."""
        assert TenantResolver.normalize(-1) is None

    def test_normalize_invalid_string(self):
        """Test normalizing invalid string."""
        assert TenantResolver.normalize("invalid") is None

    def test_resolve_explicit_tenant_id(self):
        """Test resolve with explicit tenant ID."""
        result = TenantResolver.resolve(tenant_id=5)
        assert result == 5

    def test_resolve_from_user_id(self):
        """Test resolve from user ID lookup."""
        mock_db = Mock()
        mock_db.fetch_one.return_value = {"tenant_id": 3}

        result = TenantResolver.resolve(user_id=1, db=mock_db)

        assert result == 3
        mock_db.fetch_one.assert_called_once()

    def test_resolve_user_not_found(self):
        """Test resolve when user not found."""
        mock_db = Mock()
        mock_db.fetch_one.return_value = None

        # With default, should return default
        result = TenantResolver.resolve(user_id=999, db=mock_db, default=1, fail_closed=False)
        assert result == 1

    def test_resolve_fail_closed_no_default(self):
        """Test fail-closed mode without default."""
        mock_db = Mock()
        mock_db.fetch_one.return_value = None

        with pytest.raises(TenantResolutionError):
            TenantResolver.resolve(tenant_id=None, user_id=999, db=mock_db, fail_closed=True)

    def test_resolve_fail_open_with_default(self):
        """Test fail-open mode with default."""
        result = TenantResolver.resolve(
            tenant_id=None, user_id=None, db=None, default=42, fail_closed=False
        )
        assert result == 42

    def test_resolve_priority_explicit_over_user(self):
        """Test that explicit tenant_id takes priority over user lookup."""
        mock_db = Mock()

        result = TenantResolver.resolve(tenant_id=10, user_id=1, db=mock_db)

        assert result == 10
        # Should not query database
        assert not mock_db.fetch_one.called

    def test_resolve_for_write_success(self):
        """Test resolve_for_write with valid tenant."""
        mock_db = Mock()
        mock_db.fetch_one.return_value = {"tenant_id": 5}

        result = TenantResolver.resolve_for_write(user_id=1, db=mock_db)

        assert result == 5

    def test_resolve_for_write_fails(self):
        """Test resolve_for_write fails when cannot resolve."""
        mock_db = Mock()
        mock_db.fetch_one.return_value = None

        with pytest.raises(TenantResolutionError):
            TenantResolver.resolve_for_write(user_id=999, db=mock_db)

    def test_resolve_for_read_success(self):
        """Test resolve_for_read with valid tenant."""
        mock_db = Mock()
        mock_db.fetch_one.return_value = {"tenant_id": 7}

        result = TenantResolver.resolve_for_read(user_id=1, db=mock_db)

        assert result == 7

    def test_resolve_for_read_default(self):
        """Test resolve_for_read returns default when cannot resolve."""
        mock_db = Mock()
        mock_db.fetch_one.return_value = None

        result = TenantResolver.resolve_for_read(user_id=999, db=mock_db, default=10)

        assert result == 10

    def test_resolve_for_read_default_value(self):
        """Test resolve_for_read uses default value of 1."""
        mock_db = Mock()
        mock_db.fetch_one.return_value = None

        result = TenantResolver.resolve_for_read(user_id=999, db=mock_db)

        assert result == 1


class TestTenantResolutionError:
    """Test suite for TenantResolutionError."""

    def test_error_creation(self):
        """Test creating TenantResolutionError."""
        error = TenantResolutionError("Test error")

        assert str(error) == "Test error"

    def test_error_inheritance(self):
        """Test TenantResolutionError inherits from Exception."""
        error = TenantResolutionError("Test")

        assert isinstance(error, Exception)
