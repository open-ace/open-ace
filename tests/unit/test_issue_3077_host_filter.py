"""
Test for Issue #3077: Host filter should include registered remote machines.

Tests that the /api/hosts endpoint merges hosts from:
1. usage_summary table (hosts with usage data)
2. remote_machines table (registered machines)
"""

from unittest.mock import MagicMock, patch

import pytest
from flask import Flask, g


class TestHostFilterIncludesRemoteMachines:
    """Tests for GET /api/hosts merging hosts from usage_summary and remote_machines.

    Issue #3077: 管理员注册了远程机器后，应能在分析页面的主机筛选器中看到这些机器。
    """

    def _make_app(self):
        """Create a minimal Flask app with usage blueprint."""
        from app.routes.usage import usage_bp

        app = Flask(__name__)
        app.config["TESTING"] = True
        app.config["SECRET_KEY"] = "test-secret-key"
        app.register_blueprint(usage_bp, url_prefix="/api")
        return app

    def _mock_auth(self, user):
        """Return context manager that mocks authentication."""
        return patch(
            "app.auth.decorators._load_user_from_token",
            return_value=user,
        )

    def test_hosts_merges_usage_summary_and_remote_machines(self):
        """
        /api/hosts should merge hosts from usage_summary and remote_machines.

        Issue #3077: 已注册的远程机器应出现在主机列表中，即使它们还没有使用数据。
        """
        app = self._make_app()
        user = {
            "id": 1,
            "role": "platform_admin",
            "tenant_id": 1,
            "username": "platform_admin",
            "email": "admin@example.com",
        }

        with self._mock_auth(user):
            with patch("app.routes.usage.summary_service") as mock_summary:
                mock_summary.needs_refresh.return_value = False
                mock_summary.get_all_hosts.return_value = ["host-with-usage-1", "host-with-usage-2"]

                # Mock remote_agent_manager to return registered machines
                mock_agent_mgr = MagicMock()
                mock_agent_mgr.list_machines.return_value = [
                    {"machine_name": "host-with-usage-1"},  # Already in usage_summary
                    {"machine_name": "new-remote-machine-1"},  # Not in usage_summary
                    {"machine_name": "new-remote-machine-2"},  # Not in usage_summary
                ]

                with patch(
                    "app.modules.workspace.remote_agent_manager.get_remote_agent_manager",
                    return_value=mock_agent_mgr,
                ):
                    client = app.test_client()
                    response = client.get(
                        "/api/hosts",
                        headers={"Authorization": "Bearer test-token"},
                    )

        assert response.status_code == 200
        data = response.get_json()
        # Should include both usage hosts and remote machines, deduplicated
        assert "host-with-usage-1" in data
        assert "host-with-usage-2" in data
        assert "new-remote-machine-1" in data
        assert "new-remote-machine-2" in data
        # Verify deduplication: host-with-usage-1 appears only once
        assert data.count("host-with-usage-1") == 1

    def test_tenant_admin_only_sees_tenant_machines(self):
        """
        Tenant admin should only see machines from their tenant.

        Issue #3077: 租户管理员只能看到自己租户的远程机器。
        """
        app = self._make_app()
        user = {
            "id": 2,
            "role": "tenant_admin",
            "tenant_id": 1,
            "username": "tenant_admin",
            "email": "tenant@example.com",
        }

        with self._mock_auth(user):
            with patch("app.routes.usage.summary_service") as mock_summary:
                mock_summary.needs_refresh.return_value = False

                with patch("app.routes.usage.usage_service") as mock_usage:
                    mock_usage.get_all_hosts.return_value = ["tenant-host-1"]

                    mock_agent_mgr = MagicMock()
                    mock_agent_mgr.list_machines.return_value = [
                        {"machine_name": "tenant-machine-1"},
                    ]

                    with patch(
                        "app.modules.workspace.remote_agent_manager.get_remote_agent_manager",
                        return_value=mock_agent_mgr,
                    ):
                        client = app.test_client()
                        response = client.get(
                            "/api/hosts",
                            headers={"Authorization": "Bearer test-token"},
                        )

        assert response.status_code == 200
        data = response.get_json()
        assert "tenant-host-1" in data
        assert "tenant-machine-1" in data

    def test_regular_user_sees_assigned_machines(self):
        """
        Regular user should only see machines assigned to them.

        Issue #3077: 普通用户只能看到分配给自己的远程机器。
        Note: require_tenant_scope() requires non-admins to have a tenant_id,
        so we give the user a tenant_id.
        """
        app = self._make_app()
        user = {
            "id": 3,
            "role": "user",
            "tenant_id": 1,  # Must have tenant_id for require_tenant_scope
            "username": "regular_user",
            "email": "user@example.com",
        }

        with self._mock_auth(user):
            with patch("app.routes.usage.summary_service") as mock_summary:
                mock_summary.needs_refresh.return_value = False

                with patch("app.routes.usage.usage_service") as mock_usage:
                    mock_usage.get_all_hosts.return_value = ["user-host-1"]

                    mock_agent_mgr = MagicMock()
                    mock_agent_mgr.list_machines.return_value = [
                        {"machine_name": "assigned-machine-1"},
                    ]

                    with patch(
                        "app.modules.workspace.remote_agent_manager.get_remote_agent_manager",
                        return_value=mock_agent_mgr,
                    ):
                        client = app.test_client()
                        response = client.get(
                            "/api/hosts",
                            headers={"Authorization": "Bearer test-token"},
                        )

        assert response.status_code == 200
        data = response.get_json()
        assert "user-host-1" in data
        assert "assigned-machine-1" in data

    def test_hosts_sorted_alphabetically(self):
        """
        Hosts should be sorted alphabetically.

        Issue #3077: 主机列表应按字母顺序排序。
        """
        app = self._make_app()
        user = {
            "id": 1,
            "role": "platform_admin",
            "tenant_id": 1,
            "username": "platform_admin",
            "email": "admin@example.com",
        }

        with self._mock_auth(user):
            with patch("app.routes.usage.summary_service") as mock_summary:
                mock_summary.needs_refresh.return_value = False
                mock_summary.get_all_hosts.return_value = ["zebra-host", "alpha-host"]

                mock_agent_mgr = MagicMock()
                mock_agent_mgr.list_machines.return_value = [
                    {"machine_name": "beta-machine"},
                    {"machine_name": "gamma-machine"},
                ]

                with patch(
                    "app.modules.workspace.remote_agent_manager.get_remote_agent_manager",
                    return_value=mock_agent_mgr,
                ):
                    client = app.test_client()
                    response = client.get(
                        "/api/hosts",
                        headers={"Authorization": "Bearer test-token"},
                    )

        assert response.status_code == 200
        data = response.get_json()
        # Should be sorted alphabetically
        assert data == ["alpha-host", "beta-machine", "gamma-machine", "zebra-host"]

    def test_empty_machine_name_is_ignored(self):
        """
        Remote machines with empty machine_name should be ignored.

        Issue #3077: machine_name 为空的远程机器应被忽略。
        """
        app = self._make_app()
        user = {
            "id": 1,
            "role": "platform_admin",
            "tenant_id": 1,
            "username": "platform_admin",
            "email": "admin@example.com",
        }

        with self._mock_auth(user):
            with patch("app.routes.usage.summary_service") as mock_summary:
                mock_summary.needs_refresh.return_value = False
                mock_summary.get_all_hosts.return_value = ["host-1"]

                mock_agent_mgr = MagicMock()
                mock_agent_mgr.list_machines.return_value = [
                    {"machine_name": "valid-machine"},
                    {"machine_name": ""},  # Empty machine_name
                    {},  # No machine_name field
                ]

                with patch(
                    "app.modules.workspace.remote_agent_manager.get_remote_agent_manager",
                    return_value=mock_agent_mgr,
                ):
                    client = app.test_client()
                    response = client.get(
                        "/api/hosts",
                        headers={"Authorization": "Bearer test-token"},
                    )

        assert response.status_code == 200
        data = response.get_json()
        assert "host-1" in data
        assert "valid-machine" in data
        # Empty machine_name should not be in the list
        assert "" not in data
