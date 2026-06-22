"""
Unit tests for Mapping Rules API endpoints.

Tests cover:
- Authorization (admin_required decorator)
- CRUD operations for mapping rules
- Auto-mapping functionality
- Unmapped accounts management

Related Issue: https://github.com/open-ace/open-ace/pull/1170
"""

import json
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def app():
    """Create Flask app for testing."""
    from flask import Flask

    from app.routes.mapping_rules import mapping_rules_bp

    app = Flask(__name__)
    app.register_blueprint(mapping_rules_bp)
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

        def post(self, *args, **kwargs):
            with self._token_patch():
                with self._auth_patch():
                    return self._client.post(*args, **kwargs)

        def put(self, *args, **kwargs):
            with self._token_patch():
                with self._auth_patch():
                    return self._client.put(*args, **kwargs)

        def delete(self, *args, **kwargs):
            with self._token_patch():
                with self._auth_patch():
                    return self._client.delete(*args, **kwargs)

    return AuthenticatedClient(test_client)


@pytest.fixture
def unauthorized_client(app):
    """Create test client without authentication."""
    return app.test_client()


@pytest.fixture
def non_admin_client(app):
    """Create test client with non-admin user."""
    test_client = app.test_client()

    class NonAdminAuthenticatedClient:
        def __init__(self, client):
            self._client = client

        def _auth_patch(self):
            return patch(
                "app.auth.decorators._load_user_from_token",
                return_value={"id": 2, "role": "user", "username": "test_user"},
            )

        def _token_patch(self):
            return patch("app.auth.decorators._extract_token", return_value="test-token")

        def get(self, *args, **kwargs):
            with self._token_patch():
                with self._auth_patch():
                    return self._client.get(*args, **kwargs)

        def post(self, *args, **kwargs):
            with self._token_patch():
                with self._auth_patch():
                    return self._client.post(*args, **kwargs)

    return NonAdminAuthenticatedClient(test_client)


class TestAuthorization:
    """Test that all endpoints require admin authorization."""

    def test_get_all_rules_requires_auth(self, unauthorized_client):
        """GET /api/mapping-rules should return 401 without auth."""
        response = unauthorized_client.get("/api/mapping-rules")
        assert response.status_code == 401

    def test_get_all_rules_requires_admin(self, non_admin_client):
        """GET /api/mapping-rules should return 403 for non-admin."""
        response = non_admin_client.get("/api/mapping-rules")
        assert response.status_code == 403

    def test_create_rule_requires_auth(self, unauthorized_client):
        """POST /api/mapping-rules should return 401 without auth."""
        response = unauthorized_client.post(
            "/api/mapping-rules",
            data=json.dumps({"user_id": 1, "pattern": "test-*"}),
            content_type="application/json",
        )
        assert response.status_code == 401

    def test_delete_rule_requires_auth(self, unauthorized_client):
        """DELETE /api/mapping-rules/<id> should return 401 without auth."""
        response = unauthorized_client.delete("/api/mapping-rules/1")
        assert response.status_code == 401

    def test_auto_map_requires_auth(self, unauthorized_client):
        """POST /api/mapping-rules/auto-map should return 401 without auth."""
        response = unauthorized_client.post(
            "/api/mapping-rules/auto-map",
            data=json.dumps({"dry_run": True}),
            content_type="application/json",
        )
        assert response.status_code == 401


class TestCRUDOperations:
    """Test CRUD operations for mapping rules."""

    @patch("app.routes.mapping_rules.ToolAccountMappingRuleRepository")
    def test_get_all_rules_success(self, mock_repo_class, admin_client):
        """GET /api/mapping-rules should return list of rules."""
        mock_repo = MagicMock()
        mock_rule = MagicMock()
        mock_rule.to_dict.return_value = {
            "id": 1,
            "user_id": 1,
            "pattern": "test-*",
            "match_type": "prefix",
        }
        mock_repo.get_all.return_value = [mock_rule]
        mock_repo_class.return_value = mock_repo

        response = admin_client.get("/api/mapping-rules")
        assert response.status_code == 200
        data = json.loads(response.data)
        assert len(data) == 1
        assert data[0]["pattern"] == "test-*"

    @patch("app.routes.mapping_rules.ToolAccountMappingRuleRepository")
    def test_create_rule_success(self, mock_repo_class, admin_client):
        """POST /api/mapping-rules should create a rule."""
        mock_repo = MagicMock()
        mock_rule = MagicMock()
        mock_rule.to_dict.return_value = {
            "id": 1,
            "user_id": 1,
            "pattern": "test-*",
            "match_type": "prefix",
        }
        mock_repo.create.return_value = mock_rule
        mock_repo_class.return_value = mock_repo

        response = admin_client.post(
            "/api/mapping-rules",
            data=json.dumps({"user_id": 1, "pattern": "test-*", "match_type": "prefix"}),
            content_type="application/json",
        )
        assert response.status_code == 201
        data = json.loads(response.data)
        assert data["pattern"] == "test-*"

    def test_create_rule_missing_fields(self, admin_client):
        """POST /api/mapping-rules should return 400 if fields missing."""
        response = admin_client.post(
            "/api/mapping-rules",
            data=json.dumps({"user_id": 1}),
            content_type="application/json",
        )
        assert response.status_code == 400
        data = json.loads(response.data)
        assert "error" in data

    def test_create_rule_no_data(self, admin_client):
        """POST /api/mapping-rules should return 400 if no data."""
        response = admin_client.post(
            "/api/mapping-rules",
            content_type="application/json",
        )
        assert response.status_code == 400

    @patch("app.routes.mapping_rules.ToolAccountMappingRuleRepository")
    def test_update_rule_success(self, mock_repo_class, admin_client):
        """PUT /api/mapping-rules/<id> should update a rule."""
        mock_repo = MagicMock()
        mock_rule = MagicMock()
        mock_rule.to_dict.return_value = {
            "id": 1,
            "user_id": 1,
            "pattern": "updated-*",
            "match_type": "prefix",
        }
        mock_repo.update.return_value = mock_rule
        mock_repo_class.return_value = mock_repo

        response = admin_client.put(
            "/api/mapping-rules/1",
            data=json.dumps({"pattern": "updated-*"}),
            content_type="application/json",
        )
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["pattern"] == "updated-*"

    @patch("app.routes.mapping_rules.ToolAccountMappingRuleRepository")
    def test_update_rule_not_found(self, mock_repo_class, admin_client):
        """PUT /api/mapping-rules/<id> should return 404 if not found."""
        mock_repo = MagicMock()
        mock_repo.update.return_value = None
        mock_repo_class.return_value = mock_repo

        response = admin_client.put(
            "/api/mapping-rules/999",
            data=json.dumps({"pattern": "updated-*"}),
            content_type="application/json",
        )
        assert response.status_code == 404

    @patch("app.routes.mapping_rules.ToolAccountMappingRuleRepository")
    def test_delete_rule_success(self, mock_repo_class, admin_client):
        """DELETE /api/mapping-rules/<id> should delete a rule."""
        mock_repo = MagicMock()
        mock_repo.delete.return_value = True
        mock_repo_class.return_value = mock_repo

        response = admin_client.delete("/api/mapping-rules/1")
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["success"] is True

    @patch("app.routes.mapping_rules.ToolAccountMappingRuleRepository")
    def test_delete_rule_failure(self, mock_repo_class, admin_client):
        """DELETE /api/mapping-rules/<id> should return 500 on failure."""
        mock_repo = MagicMock()
        mock_repo.delete.return_value = False
        mock_repo_class.return_value = mock_repo

        response = admin_client.delete("/api/mapping-rules/1")
        assert response.status_code == 500


class TestAutoMapping:
    """Test auto-mapping functionality."""

    @patch("app.routes.mapping_rules.ToolAccountAutoMappingService")
    def test_run_auto_mapping_success(self, mock_service_class, admin_client):
        """POST /api/mapping-rules/auto-map should run auto-mapping."""
        mock_service = MagicMock()
        mock_result = MagicMock()
        mock_result.__dict__ = {
            "tool_account": "test-account",
            "user_id": 1,
            "username": "test_user",
            "matched_by": "username",
        }
        mock_service.run_auto_mapping.return_value = ([mock_result], [])
        mock_service_class.return_value = mock_service

        response = admin_client.post(
            "/api/mapping-rules/auto-map",
            data=json.dumps({"dry_run": False}),
            content_type="application/json",
        )
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["mapped_count"] == 1
        assert data["unmapped_count"] == 0

    @patch("app.routes.mapping_rules.ToolAccountAutoMappingService")
    def test_run_auto_mapping_dry_run(self, mock_service_class, admin_client):
        """POST /api/mapping-rules/auto-map should support dry_run mode."""
        mock_service = MagicMock()
        mock_service.run_auto_mapping.return_value = ([], [])
        mock_service_class.return_value = mock_service

        response = admin_client.post(
            "/api/mapping-rules/auto-map",
            data=json.dumps({"dry_run": True}),
            content_type="application/json",
        )
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["dry_run"] is True

    @patch("app.routes.mapping_rules.ToolAccountAutoMappingService")
    def test_test_match_success(self, mock_service_class, admin_client):
        """POST /api/mapping-rules/test-match should test matching."""
        mock_service = MagicMock()
        mock_result = MagicMock()
        mock_result.user_id = 1
        mock_result.username = "test_user"
        mock_result.matched_by = "username"
        mock_result.rule_id = None
        mock_service.auto_map_account.return_value = mock_result
        mock_service_class.return_value = mock_service

        response = admin_client.post(
            "/api/mapping-rules/test-match",
            data=json.dumps({"tool_account": "test-account"}),
            content_type="application/json",
        )
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["matched"] is True
        assert data["username"] == "test_user"

    @patch("app.routes.mapping_rules.ToolAccountAutoMappingService")
    def test_test_match_no_match(self, mock_service_class, admin_client):
        """POST /api/mapping-rules/test-match should handle no match."""
        mock_service = MagicMock()
        mock_service.auto_map_account.return_value = None
        mock_service_class.return_value = mock_service

        response = admin_client.post(
            "/api/mapping-rules/test-match",
            data=json.dumps({"tool_account": "unknown-account"}),
            content_type="application/json",
        )
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["matched"] is False

    def test_test_match_missing_tool_account(self, admin_client):
        """POST /api/mapping-rules/test-match should return 400 if missing tool_account."""
        response = admin_client.post(
            "/api/mapping-rules/test-match",
            data=json.dumps({}),
            content_type="application/json",
        )
        assert response.status_code == 400


class TestUnmappedAccounts:
    """Test unmapped accounts endpoints."""

    @patch("app.routes.mapping_rules.UserToolAccountRepository")
    @patch("app.routes.mapping_rules.ToolAccountAutoMappingService")
    def test_get_unmapped_accounts_success(
        self, mock_service_class, mock_repo_class, admin_client
    ):
        """GET /api/unmapped-accounts should return list."""
        mock_repo = MagicMock()
        mock_repo.get_unmapped_tool_accounts.return_value = [
            {"sender_name": "unmapped-account", "message_count": 10}
        ]
        mock_repo_class.return_value = mock_repo

        mock_service = MagicMock()
        mock_service._infer_tool_type.return_value = "qwen"
        mock_service_class.return_value = mock_service

        response = admin_client.get("/api/unmapped-accounts")
        assert response.status_code == 200
        data = json.loads(response.data)
        assert len(data) == 1
        assert data[0]["sender_name"] == "unmapped-account"
        assert data[0]["inferred_tool_type"] == "qwen"

    @patch("app.routes.mapping_rules.ToolAccountAutoMappingService")
    def test_suggest_mapping_success(self, mock_service_class, admin_client):
        """GET /api/unmapped-accounts/<name>/suggest-mapping should return suggestion."""
        mock_service = MagicMock()
        mock_result = MagicMock()
        mock_result.user_id = 1
        mock_result.username = "test_user"
        mock_result.matched_by = "username"
        mock_result.rule_id = None
        mock_service.auto_map_account.return_value = mock_result
        mock_service_class.return_value = mock_service

        response = admin_client.get("/api/unmapped-accounts/test-account/suggest-mapping")
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["suggested_user_id"] == 1

    @patch("app.routes.mapping_rules.ToolAccountAutoMappingService")
    def test_suggest_mapping_no_match(self, mock_service_class, admin_client):
        """GET /api/unmapped-accounts/<name>/suggest-mapping should handle no match."""
        mock_service = MagicMock()
        mock_service.auto_map_account.return_value = None
        mock_service_class.return_value = mock_service

        response = admin_client.get("/api/unmapped-accounts/unknown/suggest-mapping")
        assert response.status_code == 200
        data = json.loads(response.data)
        assert data["suggestion"] is None

    @patch("app.routes.mapping_rules.UserToolAccountRepository")
    def test_manual_map_account_success(self, mock_repo_class, admin_client):
        """POST /api/unmapped-accounts/<name>/map should create mapping."""
        mock_repo = MagicMock()
        mock_mapping = MagicMock()
        mock_mapping.to_dict.return_value = {
            "id": 1,
            "user_id": 1,
            "tool_account": "test-account",
        }
        mock_repo.create.return_value = mock_mapping
        mock_repo.update_daily_messages_user_id.return_value = 5
        mock_repo_class.return_value = mock_repo

        response = admin_client.post(
            "/api/unmapped-accounts/test-account/map",
            data=json.dumps({"user_id": 1}),
            content_type="application/json",
        )
        assert response.status_code == 201
        data = json.loads(response.data)
        assert data["user_id"] == 1

    def test_manual_map_account_missing_user_id(self, admin_client):
        """POST /api/unmapped-accounts/<name>/map should return 400 if missing user_id."""
        response = admin_client.post(
            "/api/unmapped-accounts/test-account/map",
            data=json.dumps({}),
            content_type="application/json",
        )
        assert response.status_code == 400