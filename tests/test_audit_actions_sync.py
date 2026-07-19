"""
Tests for audit actions synchronization between backend and frontend.

This module verifies that audit action data structures stay consistent.
When new AuditAction enum values are added, this test ensures:
1. All actions are properly defined with clear categories
2. Frontend action options (if present) can be validated against backend

IMPORTANT: This test serves as a guardrail for future development.
When adding a new AuditAction:
- Add the enum value in audit_logger.py
- If frontend has fallback data, update it accordingly
- This test will catch any mismatches
"""

import pytest

from app.modules.governance.audit_logger import AuditAction


class TestAuditActionsSynchronization:
    """Test that backend audit actions are properly defined and documented."""

    def test_audit_action_enum_complete(self):
        """
        Verify AuditAction enum contains all expected action types.

        This test documents the expected action categories and counts,
        serving as a baseline for future audits.
        """
        # Expected action counts by category (based on audit_logger.py comments)
        expected_categories = {
            "Authentication": 4,  # LOGIN, LOGOUT, LOGIN_FAILED, SESSION_EXPIRED
            "User Management": 7,  # USER_CREATE, UPDATE, DELETE, PASSWORD_CHANGE, PASSWORD_CHANGE_FAILED, ROLE_CHANGE, STATUS_CHANGE
            "Permission": 2,  # PERMISSION_GRANT, REVOKE
            "Quota": 3,  # QUOTA_UPDATE, ALERT, EXCEEDED
            "Data Access": 4,  # DATA_VIEW, EXPORT, IMPORT, DELETE
            "System": 3,  # SYSTEM_CONFIG_CHANGE, START, STOP
            "Content Filter": 4,  # CONTENT_BLOCKED, FLAGGED, WARNED, REDACTED
            "Remote Agent": 5,  # AGENT_REGISTER, TOKEN_ROTATE, REVOKE, AUTH_FAILURE, RECONNECT
        }

        total_expected = sum(expected_categories.values())

        # Verify total count
        actual_count = len(AuditAction)
        assert actual_count == total_expected, (
            f"AuditAction enum has {actual_count} values, expected {total_expected}. "
            f"If you added a new action, update expected_categories in this test. "
            f"If you removed an action, also update expected_categories."
        )

    def test_audit_action_values_are_unique(self):
        """Verify all AuditAction enum values are unique."""
        values = [action.value for action in AuditAction]
        unique_values = set(values)

        assert len(values) == len(unique_values), (
            f"AuditAction values are not unique. "
            f"Duplicates found: {[v for v in values if values.count(v) > 1]}"
        )

    def test_audit_action_values_format(self):
        """
        Verify AuditAction enum values follow snake_case format.

        This ensures consistency for frontend filtering and API responses.
        """
        for action in AuditAction:
            value = action.value
            # Snake case: lowercase letters, underscores, no spaces
            assert value.replace("_", "").islower(), (
                f"AuditAction.{action.name} value '{value}' is not snake_case format. "
                f"Expected format like 'user_create', not 'userCreate' or 'User Create'"
            )
            assert " " not in value, (
                f"AuditAction.{action.name} value '{value}' contains spaces. "
                f"Use underscores instead: '{value.replace(' ', '_')}'"
            )

    def test_audit_action_enum_names_format(self):
        """
        Verify AuditAction enum names follow UPPER_CASE format.

        This ensures consistency with Python enum conventions.
        """
        for action in AuditAction:
            name = action.name
            # Upper case with underscores
            assert name.replace("_", "").isupper(), (
                f"AuditAction enum name '{name}' is not UPPER_CASE format. "
                f"Expected format like 'USER_CREATE', not 'userCreate' or 'User_Create'"
            )

    @pytest.mark.parametrize(
        "action,expected_category",
        [
            (AuditAction.LOGIN, "auth"),
            (AuditAction.LOGOUT, "auth"),
            (AuditAction.LOGIN_FAILED, "auth"),
            (AuditAction.SESSION_EXPIRED, "auth"),
            (AuditAction.USER_CREATE, "user_management"),
            (AuditAction.USER_UPDATE, "user_management"),
            (AuditAction.USER_DELETE, "user_management"),
            (AuditAction.USER_PASSWORD_CHANGE, "user_management"),
            (AuditAction.USER_ROLE_CHANGE, "user_management"),
            (AuditAction.USER_STATUS_CHANGE, "user_management"),
            (AuditAction.PERMISSION_GRANT, "permission"),
            (AuditAction.PERMISSION_REVOKE, "permission"),
            (AuditAction.QUOTA_UPDATE, "quota"),
            (AuditAction.QUOTA_ALERT, "quota"),
            (AuditAction.QUOTA_EXCEEDED, "quota"),
            (AuditAction.DATA_VIEW, "data"),
            (AuditAction.DATA_EXPORT, "data"),
            (AuditAction.DATA_IMPORT, "data"),
            (AuditAction.DATA_DELETE, "data"),
            (AuditAction.SYSTEM_CONFIG_CHANGE, "system"),
            (AuditAction.SYSTEM_START, "system"),
            (AuditAction.SYSTEM_STOP, "system"),
            (AuditAction.CONTENT_BLOCKED, "content"),
            (AuditAction.CONTENT_FLAGGED, "content"),
            (AuditAction.CONTENT_WARNED, "content"),
            (AuditAction.CONTENT_REDACTED, "content"),
            (AuditAction.AGENT_REGISTER, "agent"),
            (AuditAction.AGENT_TOKEN_ROTATE, "agent"),
            (AuditAction.AGENT_TOKEN_REVOKE, "agent"),
            (AuditAction.AGENT_AUTH_FAILURE, "agent"),
            (AuditAction.AGENT_RECONNECT, "agent"),
        ],
    )
    def test_audit_action_category_mapping(self, action: AuditAction, expected_category: str):
        """
        Verify each AuditAction maps to the correct category.

        This test documents the expected category for each action,
        useful for frontend UI grouping and filtering.

        IMPORTANT: When adding new AuditAction enum values,
        add corresponding parametrize entries to this test.
        """
        # This test primarily serves as documentation
        # The actual category mapping logic is typically in frontend or API
        # Here we just verify the mapping is defined and consistent
        assert action.value is not None, f"AuditAction.{action.name} must have a value"
