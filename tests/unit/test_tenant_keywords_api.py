"""Unit tests for Tenant Sensitive Keywords API (Issue #2789)."""

import json
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _isolated_app_db(monkeypatch, tmp_path):
    """Point this file's tests at a throwaway per-test database.

    In the full-suite run the workspace-level app.db is shared across every
    pytest session; leftover table shapes written by unrelated tests can make
    create_app's ensure_all_tables() snapshot replay fail (CREATE INDEX on a
    table whose columns don't match). A fresh DATABASE_URL per test sidesteps
    that shared state entirely.
    """
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/tenant-keywords.db")


class TestTenantKeywordsApiAuth:
    """Test authentication and authorization for tenant keywords API."""

    def test_get_keywords_requires_auth(self, client):
        """Should return 401 without authentication."""
        response = client.get("/api/tenants/1/sensitive-keywords")
        assert response.status_code == 401

    def test_post_keyword_requires_auth(self, client):
        """Should return 401 without authentication."""
        response = client.post(
            "/api/tenants/1/sensitive-keywords",
            json={"keyword": "secret"},
            content_type="application/json",
        )
        assert response.status_code == 401

    def test_put_keyword_requires_auth(self, client):
        """Should return 401 without authentication."""
        response = client.put(
            "/api/tenants/1/sensitive-keywords/1",
            json={"is_enabled": False},
            content_type="application/json",
        )
        assert response.status_code == 401

    def test_delete_keyword_requires_auth(self, client):
        """Should return 401 without authentication."""
        response = client.delete("/api/tenants/1/sensitive-keywords/1")
        assert response.status_code == 401


class TestTenantKeywordsApiCRUD:
    """Test CRUD operations for tenant keywords API."""

    def test_get_keywords_success(self, admin_client, mock_governance_repo):
        """Should return list of keywords for tenant."""
        mock_governance_repo.get_tenant_keywords.return_value = [
            {
                "id": 1,
                "tenant_id": 1,
                "keyword": "secret",
                "normalized_keyword": "secret",
                "is_enabled": True,
                "created_by": 1,
            }
        ]
        mock_governance_repo.get_tenant_keywords_count.return_value = 1

        response = admin_client.get("/api/tenants/1/sensitive-keywords")

        assert response.status_code == 200
        data = response.get_json()
        assert data["total"] == 1
        assert len(data["keywords"]) == 1
        assert data["keywords"][0]["keyword"] == "secret"

    def test_get_keywords_empty(self, admin_client, mock_governance_repo):
        """Should return empty list when no keywords."""
        mock_governance_repo.get_tenant_keywords.return_value = []
        mock_governance_repo.get_tenant_keywords_count.return_value = 0

        response = admin_client.get("/api/tenants/1/sensitive-keywords")

        assert response.status_code == 200
        data = response.get_json()
        assert data["total"] == 0
        assert data["keywords"] == []

    def test_post_keyword_creates_new(self, admin_client, mock_governance_repo):
        """Should create new keyword and return 201."""
        mock_governance_repo.create_tenant_keyword.return_value = (
            {
                "id": 1,
                "tenant_id": 1,
                "keyword": "NewSecret",
                "normalized_keyword": "newsecret",
                "is_enabled": True,
                "created_by": 1,
            },
            True,  # is_new
        )
        mock_governance_repo.increment_tenant_keywords_version.return_value = True

        response = admin_client.post(
            "/api/tenants/1/sensitive-keywords",
            json={"keyword": "NewSecret"},
            content_type="application/json",
        )

        assert response.status_code == 201
        data = response.get_json()
        assert data["keyword"] == "NewSecret"
        assert data["is_new"] is True

    def test_post_keyword_existing_returns_200(self, admin_client, mock_governance_repo):
        """Should return existing keyword with 200 when duplicate."""
        mock_governance_repo.create_tenant_keyword.return_value = (
            {
                "id": 1,
                "tenant_id": 1,
                "keyword": "ExistingSecret",
                "normalized_keyword": "existingsecret",
                "is_enabled": True,
                "created_by": 1,
            },
            False,  # is_new
        )

        response = admin_client.post(
            "/api/tenants/1/sensitive-keywords",
            json={"keyword": "ExistingSecret"},
            content_type="application/json",
        )

        assert response.status_code == 200
        data = response.get_json()
        assert data["is_new"] is False

    def test_post_keyword_missing_returns_400(self, admin_client, mock_governance_repo):
        """Should return 400 when keyword is missing."""
        response = admin_client.post(
            "/api/tenants/1/sensitive-keywords",
            json={},
            content_type="application/json",
        )

        assert response.status_code == 400
        data = response.get_json()
        assert "error" in data

    def test_post_keyword_empty_returns_400(self, admin_client, mock_governance_repo):
        """Should return 400 when keyword is empty."""
        response = admin_client.post(
            "/api/tenants/1/sensitive-keywords",
            json={"keyword": ""},
            content_type="application/json",
        )

        assert response.status_code == 400

    def test_put_keyword_success(self, admin_client, mock_governance_repo):
        """Should update keyword is_enabled status."""
        mock_governance_repo.get_tenant_keyword.return_value = {
            "id": 1,
            "tenant_id": 1,
            "keyword": "secret",
            "is_enabled": True,
        }
        mock_governance_repo.update_tenant_keyword.return_value = True
        mock_governance_repo.increment_tenant_keywords_version.return_value = True

        response = admin_client.put(
            "/api/tenants/1/sensitive-keywords/1",
            json={"is_enabled": False},
            content_type="application/json",
        )

        assert response.status_code == 200
        data = response.get_json()
        assert data["success"] is True

    def test_put_keyword_not_found_returns_404(self, admin_client, mock_governance_repo):
        """Should return 404 when keyword not found."""
        mock_governance_repo.get_tenant_keyword.return_value = None

        response = admin_client.put(
            "/api/tenants/1/sensitive-keywords/999",
            json={"is_enabled": False},
            content_type="application/json",
        )

        assert response.status_code == 404

    def test_put_keyword_missing_is_enabled_returns_400(self, admin_client, mock_governance_repo):
        """Should return 400 when is_enabled not provided."""
        response = admin_client.put(
            "/api/tenants/1/sensitive-keywords/1",
            json={},
            content_type="application/json",
        )

        assert response.status_code == 400

    def test_delete_keyword_success(self, admin_client, mock_governance_repo):
        """Should delete keyword successfully."""
        mock_governance_repo.get_tenant_keyword.return_value = {
            "id": 1,
            "tenant_id": 1,
            "keyword": "secret",
        }
        mock_governance_repo.delete_tenant_keyword.return_value = True
        mock_governance_repo.increment_tenant_keywords_version.return_value = True

        response = admin_client.delete("/api/tenants/1/sensitive-keywords/1")

        assert response.status_code == 200
        data = response.get_json()
        assert data["success"] is True

    def test_delete_keyword_not_found_returns_404(self, admin_client, mock_governance_repo):
        """Should return 404 when keyword not found."""
        mock_governance_repo.get_tenant_keyword.return_value = None

        response = admin_client.delete("/api/tenants/1/sensitive-keywords/999")

        assert response.status_code == 404


class TestTenantKeywordsApiPagination:
    """Test pagination for tenant keywords API."""

    def test_pagination_default_limit(self, admin_client, mock_governance_repo):
        """Should use default limit of 100."""
        mock_governance_repo.get_tenant_keywords.return_value = []
        mock_governance_repo.get_tenant_keywords_count.return_value = 0

        response = admin_client.get("/api/tenants/1/sensitive-keywords")

        assert response.status_code == 200
        mock_governance_repo.get_tenant_keywords.assert_called_once()
        call_args = mock_governance_repo.get_tenant_keywords.call_args
        assert call_args.kwargs.get("limit") == 100

    def test_pagination_custom_limit(self, admin_client, mock_governance_repo):
        """Should accept custom limit parameter."""
        mock_governance_repo.get_tenant_keywords.return_value = []
        mock_governance_repo.get_tenant_keywords_count.return_value = 0

        response = admin_client.get("/api/tenants/1/sensitive-keywords?limit=50")

        assert response.status_code == 200
        call_args = mock_governance_repo.get_tenant_keywords.call_args
        assert call_args.kwargs.get("limit") == 50

    def test_pagination_max_limit(self, admin_client, mock_governance_repo):
        """Should cap limit at 1000."""
        mock_governance_repo.get_tenant_keywords.return_value = []
        mock_governance_repo.get_tenant_keywords_count.return_value = 0

        response = admin_client.get("/api/tenants/1/sensitive-keywords?limit=5000")

        assert response.status_code == 200
        call_args = mock_governance_repo.get_tenant_keywords.call_args
        assert call_args.kwargs.get("limit") == 1000


class TestDeprecatedKeywordEndpoint:
    """Test deprecated /content/filter/keywords endpoint."""

    def test_add_keyword_returns_deprecation_warning(self, admin_client):
        """Should return deprecation warning instead of creating keyword."""
        response = admin_client.post(
            "/api/content/filter/keywords",
            json={"keyword": "test"},
            content_type="application/json",
        )

        assert response.status_code == 200
        data = response.get_json()
        assert data["success"] is False
        assert data["error"] == "Deprecated"
        assert "migration_guide" in data

        # Check deprecation headers
        assert "Deprecation" in response.headers
        assert response.headers["Deprecation"] == "true"
