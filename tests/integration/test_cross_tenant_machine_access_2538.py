"""
Integration tests for tenant isolation in machine access.

Issue #2538: Verifies that cross-tenant machine access returns 404, not 403.
"""

from unittest.mock import MagicMock, patch

import pytest
from flask import Flask, g


class TestCrossTenantMachineAccess:
    """Tests for cross-tenant machine access isolation.

    Issue #2538: Cross-tenant access should return 404, not 403.
    """

    def _invoke_check_machine_access(self, user, machine_id, machine_data, user_permission=None):
        """Invoke _check_machine_access under minimal app context."""
        from app.routes.remote import _check_machine_access

        app = Flask(__name__)
        app.config["TESTING"] = True
        with app.test_request_context("/test"):
            g.user = user
            with patch("app.routes.remote.get_remote_agent_manager") as mock_mgr:
                mock_mgr.return_value.get_machine.return_value = machine_data
                mock_mgr.return_value.get_user_permission.return_value = user_permission
                mock_mgr.return_value.check_user_access.return_value = True
                return _check_machine_access(machine_id)

    def _invoke_check_machine_tenant_access(self, user, machine_id, machine_data):
        """Invoke _check_machine_tenant_access under minimal app context."""
        from app.routes.remote import _check_machine_tenant_access

        app = Flask(__name__)
        app.config["TESTING"] = True
        with app.test_request_context("/test"):
            g.user = user
            with patch("app.routes.remote.get_remote_agent_manager") as mock_mgr:
                mock_mgr.return_value.get_machine.return_value = machine_data
                return _check_machine_tenant_access(machine_id)

    def test_cross_tenant_returns_404_not_403(self):
        """
        Cross-tenant machine access should return 404, not 403.

        Issue #2538: Prevent information leakage about machine existence.
        Issue #2540: Updated to use valid UUID format for machine_id.
        """
        # User in tenant 2 trying to access machine in tenant 1
        user = {
            "id": 2,
            "role": "user",
            "tenant_id": 2,
            "username": "user_b",
            "email": "user_b@example.com",
        }

        machine_data = {
            "machine_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
            "tenant_id": 1,
            "status": "online",
        }

        # Test _check_machine_access
        result = self._invoke_check_machine_access(
            user, "a1b2c3d4-e5f6-7890-abcd-ef1234567890", machine_data, user_permission=None
        )
        assert result is not None
        assert result[1] == 404
        assert "Machine not found" in result[0].get_json()["error"]

    def test_same_tenant_returns_none(self):
        """
        Same-tenant machine access should succeed.

        Issue #2538: Verify normal access still works.
        Issue #2540: Updated to use valid UUID format for machine_id.
        """
        # User in tenant 1 accessing machine in tenant 1
        user = {
            "id": 1,
            "role": "user",
            "tenant_id": 1,
            "username": "user_a",
            "email": "user_a@example.com",
        }

        machine_data = {
            "machine_id": "b1c2d3e4-f5a6-7890-bcde-f12345678901",
            "tenant_id": 1,
            "status": "online",
        }

        # Test _check_machine_access
        # User has permission on same-tenant machine
        result = self._invoke_check_machine_access(
            user, "b1c2d3e4-f5a6-7890-bcde-f12345678901", machine_data, user_permission="user"
        )
        assert result is None  # Success

    def test_no_tenant_machine_allows_access(self):
        """
        Machine without tenant_id should allow access (backward compatibility).

        Issue #2538: Maintain compatibility with legacy data.
        Issue #2540: Updated to use valid UUID format for machine_id.
        """
        user = {
            "id": 1,
            "role": "user",
            "tenant_id": 1,
            "username": "user_a",
            "email": "user_a@example.com",
        }

        machine_data = {
            "machine_id": "c1d2e3f4-a5b6-7890-cdef-123456789012",
            "tenant_id": None,  # No tenant
            "status": "online",
        }

        # Test _check_machine_access
        # User has permission on legacy (no-tenant) machine
        result = self._invoke_check_machine_access(
            user, "c1d2e3f4-a5b6-7890-cdef-123456789012", machine_data, user_permission="user"
        )
        assert result is None  # Success

    def test_user_without_tenant_cannot_access_tenant_machine(self):
        """
        User without tenant_id should not access tenant-scoped machine.

        Issue #2538: Security isolation for tenant-less users.
        Issue #2540: Updated to use valid UUID format for machine_id.
        """
        user = {
            "id": 1,
            "role": "user",
            "tenant_id": None,  # No tenant
            "username": "user_no_tenant",
            "email": "no_tenant@example.com",
        }

        machine_data = {
            "machine_id": "d1e2f3a4-b5c6-7890-defa-234567890123",
            "tenant_id": 1,
            "status": "online",
        }

        # Test _check_machine_access
        # User without tenant has no permission on tenant machine
        result = self._invoke_check_machine_access(
            user, "d1e2f3a4-b5c6-7890-defa-234567890123", machine_data, user_permission=None
        )
        assert result is not None
        assert result[1] == 404

    def test_platform_admin_bypasses_tenant_check(self):
        """
        Platform admin should bypass tenant isolation.

        Issue #2538: Verify admin access unchanged.
        Issue #2540: Updated to use valid UUID format for machine_id.
        """
        user = {
            "id": 1,
            "role": "platform_admin",
            "tenant_id": None,
            "username": "admin",
            "email": "admin@example.com",
        }

        machine_data = {
            "machine_id": "e1f2a3b4-c5d6-7890-efab-345678901234",
            "tenant_id": 1,
            "status": "online",
        }

        # Test _check_machine_access
        # Platform admin bypasses all checks
        result = self._invoke_check_machine_access(
            user, "e1f2a3b4-c5d6-7890-efab-345678901234", machine_data, user_permission=None
        )
        assert result is None  # Success

    def test_tenant_admin_cross_tenant_returns_404(self):
        """
        Tenant admin cross-tenant access should return 404.

        Issue #2538: Verify tenant_admin isolation (existing behavior).
        Issue #2540: Updated to use valid UUID format for machine_id.
        """
        user = {
            "id": 1,
            "role": "tenant_admin",
            "tenant_id": 2,
            "username": "tenant_admin_b",
            "email": "tenant_b@example.com",
        }

        machine_data = {
            "machine_id": "f1a2b3c4-d5e6-7890-fabc-456789012345",
            "tenant_id": 1,
            "status": "online",
        }

        # Test _check_machine_tenant_access
        machine, error = self._invoke_check_machine_tenant_access(
            user, "f1a2b3c4-d5e6-7890-fabc-456789012345", machine_data
        )
        assert machine is None
        assert error is not None
        assert error[1] == 404


class TestSessionAccessReturnCode:
    """Tests for session_access return code consistency.

    Issue #2538: Cross-tenant session access should return 404, not 403.
    """

    def test_cross_tenant_session_returns_404(self):
        """
        Cross-tenant session access should return 404.

        Issue #2538: Prevent session existence leakage.
        Issue #2540: Updated to use valid UUID format for machine_id.
        """
        from app.modules.workspace.session_access import check_session_access

        app = Flask(__name__)
        app.config["TESTING"] = True

        with app.test_request_context("/test"):
            user = {
                "id": 2,
                "role": "user",
                "tenant_id": 2,
                "username": "user_b",
                "email": "user_b@example.com",
            }
            g.user = user

            # Mock session manager to return session in different tenant
            with patch(
                "app.modules.workspace.session_access.get_remote_session_manager"
            ) as mock_mgr:
                mock_status = {
                    "session_id": "session-1",
                    "tenant_id": 1,  # Different tenant
                    "user_id": 1,
                    "machine_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                }
                mock_mgr.return_value.get_session_status.return_value = mock_status

                # Mock session object
                mock_session = MagicMock()
                mock_session.tenant_id = 1
                mock_session.user_id = 1
                mock_mgr.return_value._session_manager.get_session.return_value = mock_session

                # Mock remote agent manager
                with patch(
                    "app.modules.workspace.session_access.get_remote_agent_manager"
                ) as mock_agent_mgr:
                    mock_agent_mgr.return_value.get_user_permission.return_value = None

                    session, error = check_session_access("session-1")

                    assert session is None
                    assert error is not None
                    assert error[1] == 404
                    assert "Session not found" in error[0].get_json()["error"]
