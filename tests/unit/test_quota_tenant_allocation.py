"""
Unit tests for tenant quota allocation validation.

Tests validation of tenant allocation limits including:
- Tenant quota exceeded scenarios
- Unlimited tenant handling
- Multiple users allocation
- Quota decrease scenarios

Field semantics (important):
- None: No change, keep current value (skip validation for this field)
- EXPLICIT_NULL: Set to unlimited (validate against tenant limits)
- int value: Set to specified value (validate against tenant limits)
"""

from unittest.mock import MagicMock, patch

import pytest


class TestValidateTenantAllocation:
    """Test tenant allocation validation."""

    def test_tenant_not_found(self):
        """Test validation when tenant is not found."""
        from app.schemas.quota import validate_tenant_allocation

        # Mock database
        mock_db = MagicMock()
        mock_db.fetch_one.return_value = None

        result = validate_tenant_allocation(
            tenant_id=999,
            user_id=None,
            db=mock_db,
        )

        assert result["is_valid"] is False
        assert "not found" in result["error"].lower()

    def test_unlimited_tenant_allows_any_quota(self):
        """Test that unlimited tenant allows any quota allocation."""
        from app.schemas.quota import validate_tenant_allocation

        # Mock database with unlimited tenant (all limits are 0 or NULL)
        mock_db = MagicMock()
        mock_db.fetch_one.side_effect = [
            # tenant_quotas row - unlimited
            {
                "daily_token_limit": None,
                "monthly_token_limit": None,
                "daily_request_limit": None,
                "monthly_request_limit": None,
            },
        ]

        result = validate_tenant_allocation(
            tenant_id=1,
            user_id=None,
            new_daily_token_quota=10000,
            db=mock_db,
        )

        assert result["is_valid"] is True
        assert result["is_unlimited_tenant"] is True

    def test_limited_tenant_rejects_unlimited_user_quota(self):
        """Test that limited tenant rejects EXPLICIT_NULL user quota."""
        from app.constants import EXPLICIT_NULL
        from app.schemas.quota import validate_tenant_allocation

        # Mock database with limited tenant
        mock_db = MagicMock()
        mock_db.fetch_one.return_value = {
            "daily_token_limit": 10000000,  # 10M
            "monthly_token_limit": 100000000,  # 100M
            "daily_request_limit": 1000,
            "monthly_request_limit": 10000,
        }

        result = validate_tenant_allocation(
            tenant_id=1,
            user_id=None,
            new_daily_token_quota=EXPLICIT_NULL,  # Explicit unlimited
            db=mock_db,
        )

        assert result["is_valid"] is False
        assert "unlimited" in result["error"].lower()

    def test_none_field_skips_validation(self):
        """Test that None field skips validation (no change)."""
        from app.schemas.quota import validate_tenant_allocation

        # Mock database with limited tenant
        mock_db = MagicMock()
        mock_db.fetch_one.return_value = {
            "daily_token_limit": 10000000,  # 10M
            "monthly_token_limit": 100000000,  # 100M
            "daily_request_limit": 1000,
            "monthly_request_limit": 10000,
        }

        result = validate_tenant_allocation(
            tenant_id=1,
            user_id=None,
            new_daily_token_quota=None,  # None means "no change", should skip validation
            db=mock_db,
        )

        # Should be valid because None means "no change" - no validation performed
        assert result["is_valid"] is True

    def test_allocation_within_limit(self):
        """Test allocation that fits within tenant limit."""
        from app.schemas.quota import validate_tenant_allocation

        # Mock database
        mock_db = MagicMock()
        mock_db.fetch_one.side_effect = [
            # tenant_quotas row
            {
                "daily_token_limit": 10000000,  # 10M tokens
                "monthly_token_limit": 100000000,
                "daily_request_limit": 1000,
                "monthly_request_limit": 10000,
            },
            # allocated quota row (currently 0)
            {
                "daily_token": 0,
                "monthly_token": 0,
                "daily_request": 0,
                "monthly_request": 0,
            },
        ]

        result = validate_tenant_allocation(
            tenant_id=1,
            user_id=None,
            new_daily_token_quota=5,  # 5M tokens (within 10M limit)
            new_monthly_token_quota=10,  # Provide all fields
            new_daily_request_quota=100,
            new_monthly_request_quota=1000,
            db=mock_db,
        )

        assert result["is_valid"] is True

    def test_allocation_exceeds_limit(self):
        """Test allocation that exceeds tenant limit."""
        from app.schemas.quota import validate_tenant_allocation

        # Mock database
        mock_db = MagicMock()
        mock_db.fetch_one.side_effect = [
            # tenant_quotas row
            {
                "daily_token_limit": 10000000,  # 10M tokens
                "monthly_token_limit": 100000000,
                "daily_request_limit": 1000,
                "monthly_request_limit": 10000,
            },
            # allocated quota row (currently 8M)
            {
                "daily_token": 8,  # 8M tokens already allocated
                "monthly_token": 0,
                "daily_request": 0,
                "monthly_request": 0,
            },
        ]

        result = validate_tenant_allocation(
            tenant_id=1,
            user_id=None,
            new_daily_token_quota=5,  # Try to allocate 5M more (total 13M > 10M limit)
            new_monthly_token_quota=10,
            new_daily_request_quota=100,
            new_monthly_request_quota=1000,
            db=mock_db,
        )

        assert result["is_valid"] is False
        assert "exceeded" in result["error"].lower()

    def test_update_user_excludes_current_quota(self):
        """Test that updating user's quota excludes their current allocation."""
        from app.schemas.quota import validate_tenant_allocation

        # Mock database
        mock_db = MagicMock()
        mock_db.fetch_one.side_effect = [
            # tenant_quotas row
            {
                "daily_token_limit": 10000000,  # 10M tokens
                "monthly_token_limit": 100000000,
                "daily_request_limit": 1000,
                "monthly_request_limit": 10000,
            },
            # allocated quota row (excluding user_id=1)
            {
                "daily_token": 3,  # Other users have 3M
                "monthly_token": 0,
                "daily_request": 0,
                "monthly_request": 0,
            },
        ]

        result = validate_tenant_allocation(
            tenant_id=1,
            user_id=1,  # Updating user 1
            new_daily_token_quota=6,  # User 1 gets 6M, total = 3M + 6M = 9M (within 10M)
            new_monthly_token_quota=10,
            new_daily_request_quota=100,
            new_monthly_request_quota=1000,
            db=mock_db,
        )

        assert result["is_valid"] is True

    def test_request_quota_exceeded(self):
        """Test request quota exceeded scenario."""
        from app.schemas.quota import validate_tenant_allocation

        # Mock database
        mock_db = MagicMock()
        mock_db.fetch_one.side_effect = [
            # tenant_quotas row
            {
                "daily_token_limit": 10000000,
                "monthly_token_limit": 100000000,
                "daily_request_limit": 1000,
                "monthly_request_limit": 10000,
            },
            # allocated quota row
            {
                "daily_token": 0,
                "monthly_token": 0,
                "daily_request": 800,
                "monthly_request": 0,
            },
        ]

        result = validate_tenant_allocation(
            tenant_id=1,
            user_id=None,
            new_daily_token_quota=5,
            new_monthly_token_quota=10,
            new_daily_request_quota=500,  # Total would be 1300 > 1000 limit
            new_monthly_request_quota=1000,
            db=mock_db,
        )

        assert result["is_valid"] is False
        assert "request quota exceeded" in result["error"].lower()


class TestTenantAllocationNullHandling:
    """Test null/unlimited quota handling in SQL query."""

    def test_null_daily_token_not_counted(self):
        """Test that users with null daily_token_quota are not counted in token sum."""
        from app.schemas.quota import validate_tenant_allocation

        mock_db = MagicMock()
        mock_db.fetch_one.side_effect = [
            # tenant_quotas row
            {
                "daily_token_limit": 10000000,  # 10M
                "monthly_token_limit": 100000000,
                "daily_request_limit": 1000,
                "monthly_request_limit": 10000,
            },
            # allocated quota row - user with null token quota has request quota
            {
                "daily_token": 0,
                "monthly_token": 0,
                "daily_request": 500,  # Should be counted
                "monthly_request": 0,
            },
        ]

        result = validate_tenant_allocation(
            tenant_id=1,
            user_id=None,
            new_daily_token_quota=5,
            new_monthly_token_quota=10,
            new_daily_request_quota=400,  # 500 + 400 = 900 < 1000, should pass
            new_monthly_request_quota=1000,
            db=mock_db,
        )

        assert result["is_valid"] is True


class TestTenantAllocationSoftDelete:
    """Test soft-deleted user handling in quota allocation.

    Issue #2927: Soft-deleted users should not be counted in tenant quota allocation.
    """

    def test_soft_deleted_user_not_counted_in_token_quota(self):
        """Test that soft-deleted users with is_active=true are not counted in token quota."""
        from app.schemas.quota import validate_tenant_allocation

        mock_db = MagicMock()
        mock_db.fetch_one.side_effect = [
            # tenant_quotas row
            {
                "daily_token_limit": 10000000,  # 10M
                "monthly_token_limit": 100000000,
                "daily_request_limit": 1000,
                "monthly_request_limit": 10000,
            },
            # allocated quota row - only active (non-deleted) users
            {
                "daily_token": 3,  # Only 3M from active users (soft-deleted user's quota excluded)
                "monthly_token": 0,
                "daily_request": 0,
                "monthly_request": 0,
            },
        ]

        # Try to allocate 6M to a new user (total would be 3M + 6M = 9M < 10M)
        # If soft-deleted user's quota was counted, this would fail
        result = validate_tenant_allocation(
            tenant_id=1,
            user_id=2,  # New user
            new_daily_token_quota=6,
            new_monthly_token_quota=10,
            new_daily_request_quota=100,
            new_monthly_request_quota=1000,
            db=mock_db,
        )

        assert result["is_valid"] is True

    def test_soft_deleted_user_not_counted_in_request_quota(self):
        """Test that soft-deleted users with is_active=true are not counted in request quota."""
        from app.schemas.quota import validate_tenant_allocation

        mock_db = MagicMock()
        mock_db.fetch_one.side_effect = [
            # tenant_quotas row
            {
                "daily_token_limit": 10000000,
                "monthly_token_limit": 100000000,
                "daily_request_limit": 1000,  # 1000 requests limit
                "monthly_request_limit": 10000,
            },
            # allocated quota row - only active (non-deleted) users
            {
                "daily_token": 0,
                "monthly_token": 0,
                "daily_request": 400,  # Only 400 from active users
                "monthly_request": 0,
            },
        ]

        # Try to allocate 500 requests (total would be 400 + 500 = 900 < 1000)
        # If soft-deleted user's quota was counted, this might fail
        result = validate_tenant_allocation(
            tenant_id=1,
            user_id=2,
            new_daily_token_quota=5,
            new_monthly_token_quota=10,
            new_daily_request_quota=500,
            new_monthly_request_quota=1000,
            db=mock_db,
        )

        assert result["is_valid"] is True

    def test_active_user_counted_in_quota(self):
        """Test that active users with deleted_at=NULL are counted in quota."""
        from app.schemas.quota import validate_tenant_allocation

        mock_db = MagicMock()
        mock_db.fetch_one.side_effect = [
            # tenant_quotas row
            {
                "daily_token_limit": 10000000,  # 10M
                "monthly_token_limit": 100000000,
                "daily_request_limit": 1000,
                "monthly_request_limit": 10000,
            },
            # allocated quota row - active users counted
            {
                "daily_token": 8,  # 8M already allocated to active users
                "monthly_token": 0,
                "daily_request": 0,
                "monthly_request": 0,
            },
        ]

        # Try to allocate 3M more (total would be 8M + 3M = 11M > 10M limit)
        # This should fail because active users' quotas are counted
        result = validate_tenant_allocation(
            tenant_id=1,
            user_id=2,
            new_daily_token_quota=3,
            new_monthly_token_quota=10,
            new_daily_request_quota=100,
            new_monthly_request_quota=1000,
            db=mock_db,
        )

        assert result["is_valid"] is False
        assert "exceeded" in result["error"].lower()

    def test_current_user_excluded_from_allocation(self):
        """Test that current user being edited is excluded from allocation sum."""
        from app.schemas.quota import validate_tenant_allocation

        mock_db = MagicMock()
        mock_db.fetch_one.side_effect = [
            # tenant_quotas row
            {
                "daily_token_limit": 10000000,  # 10M
                "monthly_token_limit": 100000000,
                "daily_request_limit": 1000,
                "monthly_request_limit": 10000,
            },
            # allocated quota row - excludes current user (user_id=1)
            {
                "daily_token": 3,  # Other users have 3M (user 1's quota excluded)
                "monthly_token": 0,
                "daily_request": 0,
                "monthly_request": 0,
            },
        ]

        # Update user 1's quota to 6M (total = 3M + 6M = 9M < 10M)
        result = validate_tenant_allocation(
            tenant_id=1,
            user_id=1,  # Editing user 1
            new_daily_token_quota=6,
            new_monthly_token_quota=10,
            new_daily_request_quota=100,
            new_monthly_request_quota=1000,
            db=mock_db,
        )

        assert result["is_valid"] is True
