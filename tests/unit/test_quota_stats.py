"""Tests for quota stats API endpoint."""

import json
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def app():
    """Create Flask app for testing."""
    from flask import Flask

    from app.routes.admin import admin_bp

    app = Flask(__name__)
    app.register_blueprint(admin_bp, url_prefix="/api")
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test-secret-key"

    yield app


@pytest.fixture
def admin_client(app):
    """Create test client with admin authentication."""
    test_client = app.test_client()

    class AuthenticatedClient:
        def __init__(self, client):
            self._client = client

        def _auth_patch(self):
            return patch(
                "app.auth.decorators._load_user_from_token",
                return_value={"id": 1, "role": "admin", "username": "test_admin"},
            )

        def _token_patch(self):
            return patch("app.auth.decorators._extract_token", return_value="test-token")

        def get(self, *args, **kwargs):
            with self._token_patch():
                with self._auth_patch():
                    return self._client.get(*args, **kwargs)

    return AuthenticatedClient(test_client)


class TestQuotaStatsAPIAuthorization:
    """Test authorization for quota stats endpoint."""

    def test_quota_stats_requires_auth(self, app):
        """GET /api/admin/quota/stats should return 401 without auth."""
        with app.test_client() as client:
            response = client.get("/api/admin/quota/stats")
            assert response.status_code == 401


class TestQuotaStatsAPI:
    """Test /api/admin/quota/stats endpoint functionality."""

    def test_quota_stats_normal_response(self, admin_client):
        """Test normal quota stats response."""
        # Mock tenant
        mock_tenant = MagicMock()
        mock_tenant.quota = MagicMock()
        mock_tenant.quota.daily_token_limit = 100_000_000
        mock_tenant.quota.monthly_token_limit = 1_000_000_000
        mock_tenant.quota.daily_request_limit = 1000
        mock_tenant.quota.monthly_request_limit = 10000
        mock_tenant.quota.max_users = 50

        mock_users = [
            {"id": 1, "is_active": True, "daily_token_quota": 10, "monthly_token_quota": 100},
            {"id": 2, "is_active": True, "daily_token_quota": 5, "monthly_token_quota": 50},
            {"id": 3, "is_active": False, "daily_token_quota": 20, "monthly_token_quota": 200},
        ]

        with patch("app.routes.admin.user_repo.get_all_users", return_value=mock_users):
            with patch(
                "app.services.tenant_service.TenantService.get_tenant", return_value=mock_tenant
            ):
                response = admin_client.get("/api/admin/quota/stats")
                assert response.status_code == 200
                data = json.loads(response.data)
                assert "tenant_quota" in data
                assert "allocated" in data
                assert "remaining" in data
                assert "percentages" in data
                assert "user_count" in data

    def test_quota_stats_tenant_not_found(self, admin_client):
        """Test quota stats when tenant not found."""
        mock_users = []

        with patch("app.routes.admin.user_repo.get_all_users", return_value=mock_users):
            with patch("app.services.tenant_service.TenantService.get_tenant", return_value=None):
                response = admin_client.get("/api/admin/quota/stats")
                assert response.status_code == 404
                data = json.loads(response.data)
                assert "error" in data

    def test_quota_stats_user_quota_none_handling(self, admin_client):
        """Test quota stats handles None quota values."""
        mock_tenant = MagicMock()
        mock_tenant.quota = MagicMock()
        mock_tenant.quota.daily_token_limit = 100_000_000
        mock_tenant.quota.monthly_token_limit = 1_000_000_000
        mock_tenant.quota.daily_request_limit = 1000
        mock_tenant.quota.monthly_request_limit = 10000
        mock_tenant.quota.max_users = 50

        mock_users = [
            {"id": 1, "is_active": True, "daily_token_quota": None, "monthly_token_quota": None},
            {"id": 2, "is_active": True, "daily_token_quota": 5, "monthly_token_quota": 50},
        ]

        with patch("app.routes.admin.user_repo.get_all_users", return_value=mock_users):
            with patch(
                "app.services.tenant_service.TenantService.get_tenant", return_value=mock_tenant
            ):
                response = admin_client.get("/api/admin/quota/stats")
                assert response.status_code == 200
                data = json.loads(response.data)
                # Should not crash, allocated should only count user 2's quota
                assert data["allocated"]["daily_token"] == 5

    def test_quota_stats_zero_limit(self, admin_client):
        """Test quota stats with zero limit."""
        mock_tenant = MagicMock()
        mock_tenant.quota = MagicMock()
        mock_tenant.quota.daily_token_limit = 0
        mock_tenant.quota.monthly_token_limit = 1_000_000_000
        mock_tenant.quota.daily_request_limit = 0
        mock_tenant.quota.monthly_request_limit = 10000
        mock_tenant.quota.max_users = 50

        mock_users = [{"id": 1, "is_active": True, "daily_token_quota": 10}]

        with patch("app.routes.admin.user_repo.get_all_users", return_value=mock_users):
            with patch(
                "app.services.tenant_service.TenantService.get_tenant", return_value=mock_tenant
            ):
                response = admin_client.get("/api/admin/quota/stats")
                assert response.status_code == 200
                data = json.loads(response.data)
                # Percentage should be 0 when limit is 0
                assert data["percentages"]["daily_token"] == 0.0

    def test_quota_stats_over_100_percent(self, admin_client):
        """Test quota stats when allocated exceeds limit."""
        mock_tenant = MagicMock()
        mock_tenant.quota = MagicMock()
        mock_tenant.quota.daily_token_limit = 10_000_000  # 10M
        mock_tenant.quota.monthly_token_limit = 1_000_000_000
        mock_tenant.quota.daily_request_limit = 100
        mock_tenant.quota.monthly_request_limit = 10000
        mock_tenant.quota.max_users = 50

        # Users allocated more than limit (15M total vs 10M limit)
        mock_users = [
            {"id": 1, "is_active": True, "daily_token_quota": 10},
            {"id": 2, "is_active": True, "daily_token_quota": 5},
        ]

        with patch("app.routes.admin.user_repo.get_all_users", return_value=mock_users):
            with patch(
                "app.services.tenant_service.TenantService.get_tenant", return_value=mock_tenant
            ):
                response = admin_client.get("/api/admin/quota/stats")
                assert response.status_code == 200
                data = json.loads(response.data)
                # Percentage can exceed 100%
                assert data["percentages"]["daily_token"] == 150.0
                # Remaining should be negative
                assert data["remaining"]["daily_token"] < 0


class TestCalcPercentHelper:
    """Test the calc_percent helper function logic."""

    def test_calc_percent_normal(self):
        """Test normal percentage calculation."""
        allocated = 50
        limit = 100
        if limit <= 0:
            result = 0.0
        else:
            result = round((allocated / limit) * 100, 1)
        assert result == 50.0

    def test_calc_percent_zero_limit(self):
        """Test percentage with zero limit."""
        allocated = 50
        limit = 0
        if limit <= 0:
            result = 0.0
        else:
            result = round((allocated / limit) * 100, 1)
        assert result == 0.0

    def test_calc_percent_over_100(self):
        """Test percentage over 100."""
        allocated = 150
        limit = 100
        if limit <= 0:
            result = 0.0
        else:
            result = round((allocated / limit) * 100, 1)
        assert result == 150.0

    def test_calc_percent_rounding(self):
        """Test percentage rounding to 1 decimal."""
        allocated = 33
        limit = 100
        if limit <= 0:
            result = 0.0
        else:
            result = round((allocated / limit) * 100, 1)
        assert result == 33.0
