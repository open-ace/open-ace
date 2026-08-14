"""
Unit tests for Rule Permission Check Middleware.

Tests for permission checking functionality.
"""

from unittest.mock import Mock, patch

import pytest
from flask import Flask, g

from app.middleware.rule_permission_check import (
    RULE_ROLES,
    can_approve_rule,
    can_delete_rule,
    can_edit_rule,
    can_mark_test_rule,
    check_permission,
    check_self_approval,
    check_tenant_access,
    get_user_role,
    require_role,
    require_tenant_access,
)


class TestGetUserRole:
    """Tests for get_user_role function."""

    def test_returns_none_when_no_user(self):
        """Test that None is returned when no user in context."""
        app = Flask(__name__)

        with app.app_context():
            # No g.user set
            assert get_user_role() is None

    def test_returns_system_admin_for_platform_admin(self):
        """Test that platform admin returns system_admin role."""
        app = Flask(__name__)

        with app.app_context():
            g.user = {"id": 1, "is_platform_admin": True}

            role = get_user_role()
            assert role == RULE_ROLES["system_admin"]

    def test_returns_admin_for_rule_admin_role(self):
        """Test that rule_admin role returns admin."""
        app = Flask(__name__)

        with app.app_context():
            g.user = {"id": 1, "role": "rule_admin"}

            role = get_user_role()
            assert role == RULE_ROLES["admin"]

    def test_returns_admin_for_admin_role(self):
        """Test that admin role returns admin."""
        app = Flask(__name__)

        with app.app_context():
            g.user = {"id": 1, "role": "admin"}

            role = get_user_role()
            assert role == RULE_ROLES["admin"]

    def test_returns_approver_for_approver_role(self):
        """Test that rule_approver role returns approver."""
        app = Flask(__name__)

        with app.app_context():
            g.user = {"id": 1, "role": "rule_approver"}

            role = get_user_role()
            assert role == RULE_ROLES["approver"]

    def test_returns_creator_for_creator_role(self):
        """Test that rule_creator role returns creator."""
        app = Flask(__name__)

        with app.app_context():
            g.user = {"id": 1, "role": "rule_creator"}

            role = get_user_role()
            assert role == RULE_ROLES["creator"]

    def test_returns_none_for_unknown_role(self):
        """Test that unknown role returns None (no default permissions)."""
        app = Flask(__name__)

        with app.app_context():
            g.user = {"id": 1, "role": "unknown_role"}

            role = get_user_role()
            assert role is None

    def test_returns_none_for_empty_role(self):
        """Test that empty role returns None."""
        app = Flask(__name__)

        with app.app_context():
            g.user = {"id": 1, "role": ""}

            role = get_user_role()
            assert role is None

    def test_returns_none_for_no_role_field(self):
        """Test that missing role field returns None."""
        app = Flask(__name__)

        with app.app_context():
            g.user = {"id": 1}

            role = get_user_role()
            assert role is None


class TestCheckPermission:
    """Tests for check_permission function."""

    def test_system_admin_has_all_permissions(self):
        """Test that system admin can do anything."""
        app = Flask(__name__)

        with app.app_context():
            g.user = {"id": 1, "is_platform_admin": True}

            assert check_permission(RULE_ROLES["creator"])
            assert check_permission(RULE_ROLES["approver"])
            assert check_permission(RULE_ROLES["admin"])
            assert check_permission(RULE_ROLES["system_admin"])

    def test_admin_cannot_be_system_admin(self):
        """Test that admin cannot do system admin tasks."""
        app = Flask(__name__)

        with app.app_context():
            g.user = {"id": 1, "role": "admin"}

            assert check_permission(RULE_ROLES["creator"])
            assert check_permission(RULE_ROLES["approver"])
            assert check_permission(RULE_ROLES["admin"])
            assert not check_permission(RULE_ROLES["system_admin"])

    def test_approver_cannot_be_admin(self):
        """Test that approver cannot do admin tasks."""
        app = Flask(__name__)

        with app.app_context():
            g.user = {"id": 1, "role": "rule_approver"}

            assert check_permission(RULE_ROLES["creator"])
            assert check_permission(RULE_ROLES["approver"])
            assert not check_permission(RULE_ROLES["admin"])

    def test_creator_cannot_be_approver(self):
        """Test that creator cannot approve."""
        app = Flask(__name__)

        with app.app_context():
            g.user = {"id": 1, "role": "rule_creator"}

            assert check_permission(RULE_ROLES["creator"])
            assert not check_permission(RULE_ROLES["approver"])

    def test_unknown_role_has_no_permissions(self):
        """Test that unknown role has no permissions."""
        app = Flask(__name__)

        with app.app_context():
            g.user = {"id": 1, "role": "unknown"}

            assert not check_permission(RULE_ROLES["creator"])
            assert not check_permission(RULE_ROLES["approver"])


class TestRequireRole:
    """Tests for require_role decorator."""

    def test_allows_authorized_user(self):
        """Test that authorized user can proceed."""
        app = Flask(__name__)

        @require_role(RULE_ROLES["creator"])
        def test_endpoint():
            return {"status": "ok"}

        with app.app_context():
            g.user = {"id": 1, "role": "rule_creator"}

            result = test_endpoint()
            assert result == {"status": "ok"}

    def test_blocks_unauthorized_user(self):
        """Test that unauthorized user is blocked."""
        app = Flask(__name__)

        @require_role(RULE_ROLES["admin"])
        def test_endpoint():
            return {"status": "ok"}

        with app.app_context():
            g.user = {"id": 1, "role": "rule_creator"}

            result = test_endpoint()
            assert result[1] == 403
            assert "Permission denied" in result[0]["error"]


class TestCheckSelfApproval:
    """Tests for check_self_approval function."""

    def test_system_admin_can_approve_own_rule(self):
        """Test that system admin can approve their own rule."""
        app = Flask(__name__)

        with app.app_context():
            g.user = {"id": 1, "is_platform_admin": True}

            result = check_self_approval(rule_creator_id=1)
            assert result is True

    def test_others_cannot_approve_own_rule(self):
        """Test that non-admins cannot approve their own rule."""
        app = Flask(__name__)

        with app.app_context():
            g.user = {"id": 1, "role": "rule_approver"}

            result = check_self_approval(rule_creator_id=1)
            assert result is False

    def test_can_approve_others_rule(self):
        """Test that approvers can approve others' rules."""
        app = Flask(__name__)

        with app.app_context():
            g.user = {"id": 1, "role": "rule_approver"}

            result = check_self_approval(rule_creator_id=999)
            assert result is True


class TestCheckTenantAccess:
    """Tests for check_tenant_access function."""

    def test_global_rule_accessible_to_all(self):
        """Test that global rules are accessible to all."""
        app = Flask(__name__)

        with app.app_context():
            g.user = {"id": 1, "tenant_id": 100}

            result = check_tenant_access(rule_tenant_id=None)
            assert result is True

    def test_tenant_rule_accessible_to_same_tenant(self):
        """Test that tenant rules are accessible to same tenant."""
        app = Flask(__name__)

        with app.app_context():
            g.user = {"id": 1, "tenant_id": 100}

            result = check_tenant_access(rule_tenant_id=100)
            assert result is True

    def test_tenant_rule_not_accessible_to_other_tenant(self):
        """Test that tenant rules are not accessible to other tenants."""
        app = Flask(__name__)

        with app.app_context():
            g.user = {"id": 1, "tenant_id": 100}

            result = check_tenant_access(rule_tenant_id=200)
            assert result is False

    def test_user_without_tenant_can_access_global(self):
        """Test that users without tenant can access global rules."""
        app = Flask(__name__)

        with app.app_context():
            g.user = {"id": 1, "tenant_id": None}

            result = check_tenant_access(rule_tenant_id=None)
            assert result is True

    def test_user_without_tenant_cannot_access_tenant_rule(self):
        """Test that users without tenant cannot access tenant rules."""
        app = Flask(__name__)

        with app.app_context():
            g.user = {"id": 1, "tenant_id": None}

            result = check_tenant_access(rule_tenant_id=100)
            assert result is False


class TestCanApproveRule:
    """Tests for can_approve_rule function."""

    def test_approver_can_approve_others_rule(self):
        """Test that approver can approve others' rule."""
        app = Flask(__name__)

        with app.app_context():
            g.user = {"id": 1, "role": "rule_approver", "tenant_id": 100}

            rule = {"created_by": 999, "tenant_id": 100}

            can_approve, error = can_approve_rule(rule)
            assert can_approve is True
            assert error is None

    def test_cannot_approve_own_rule(self):
        """Test that approver cannot approve their own rule."""
        app = Flask(__name__)

        with app.app_context():
            g.user = {"id": 1, "role": "rule_approver", "tenant_id": 100}

            rule = {"created_by": 1, "tenant_id": 100}

            can_approve, error = can_approve_rule(rule)
            assert can_approve is False
            assert "own rule" in error.lower()

    def test_creator_cannot_approve(self):
        """Test that creator cannot approve any rule."""
        app = Flask(__name__)

        with app.app_context():
            g.user = {"id": 1, "role": "rule_creator", "tenant_id": 100}

            rule = {"created_by": 999, "tenant_id": 100}

            can_approve, error = can_approve_rule(rule)
            assert can_approve is False


class TestCanEditRule:
    """Tests for can_edit_rule function."""

    def test_admin_can_edit_any_rule(self):
        """Test that admin can edit any rule."""
        app = Flask(__name__)

        with app.app_context():
            g.user = {"id": 1, "role": "admin", "tenant_id": 100}

            rule = {"created_by": 999, "tenant_id": 100, "approval_status": "approved"}

            can_edit, error = can_edit_rule(rule)
            assert can_edit is True

    def test_creator_can_edit_own_draft(self):
        """Test that creator can edit their own draft."""
        app = Flask(__name__)

        with app.app_context():
            g.user = {"id": 1, "role": "rule_creator", "tenant_id": 100}

            rule = {"created_by": 1, "tenant_id": 100, "approval_status": "draft"}

            can_edit, error = can_edit_rule(rule)
            assert can_edit is True

    def test_creator_cannot_edit_approved_rule(self):
        """Test that creator cannot edit approved rule."""
        app = Flask(__name__)

        with app.app_context():
            g.user = {"id": 1, "role": "rule_creator", "tenant_id": 100}

            rule = {"created_by": 1, "tenant_id": 100, "approval_status": "approved"}

            can_edit, error = can_edit_rule(rule)
            assert can_edit is False


class TestCanDeleteRule:
    """Tests for can_delete_rule function."""

    def test_admin_can_delete(self):
        """Test that admin can delete."""
        app = Flask(__name__)

        with app.app_context():
            g.user = {"id": 1, "role": "admin", "tenant_id": 100}

            rule = {"tenant_id": 100}

            can_delete, error = can_delete_rule(rule)
            assert can_delete is True

    def test_creator_cannot_delete(self):
        """Test that creator cannot delete."""
        app = Flask(__name__)

        with app.app_context():
            g.user = {"id": 1, "role": "rule_creator", "tenant_id": 100}

            rule = {"tenant_id": 100}

            can_delete, error = can_delete_rule(rule)
            assert can_delete is False


class TestCanMarkTestRule:
    """Tests for can_mark_test_rule function."""

    def test_admin_can_mark_test(self):
        """Test that admin can mark test rule."""
        app = Flask(__name__)

        with app.app_context():
            g.user = {"id": 1, "role": "admin", "tenant_id": 100}

            rule = {"tenant_id": 100}

            can_mark, error = can_mark_test_rule(rule)
            assert can_mark is True

    def test_creator_cannot_mark_test(self):
        """Test that creator cannot mark test rule."""
        app = Flask(__name__)

        with app.app_context():
            g.user = {"id": 1, "role": "rule_creator", "tenant_id": 100}

            rule = {"tenant_id": 100}

            can_mark, error = can_mark_test_rule(rule)
            assert can_mark is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])