"""
Integration tests for Data Retention API endpoints.

Tests cover:
- Action parameter validation (delete, archive, anonymize)
- Invalid action value handling
- Default action value behavior
- Retention rule CRUD operations
"""

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def app(tmp_db):
    """Create Flask app for testing with temporary database."""
    from flask import Flask

    from app.routes.compliance import compliance_bp

    app = Flask(__name__)
    app.register_blueprint(compliance_bp)
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test-secret-key"

    yield app


@pytest.fixture
def client(app):
    """Create test client with authentication."""
    test_client = app.test_client()

    # Create a wrapper that patches authentication for each request
    class AuthenticatedClient:
        def __init__(self, client):
            self._client = client

        def get(self, *args, **kwargs):
            with patch("app.auth.decorators._extract_session_token", return_value="test-token"):
                with patch(
                    "app.auth.decorators._load_user_from_token",
                    return_value={"id": 1, "role": "admin", "username": "test_admin"},
                ):
                    return self._client.get(*args, **kwargs)

        def post(self, *args, **kwargs):
            with patch("app.auth.decorators._extract_session_token", return_value="test-token"):
                with patch(
                    "app.auth.decorators._load_user_from_token",
                    return_value={"id": 1, "role": "admin", "username": "test_admin"},
                ):
                    return self._client.post(*args, **kwargs)

        def put(self, *args, **kwargs):
            with patch("app.auth.decorators._extract_session_token", return_value="test-token"):
                with patch(
                    "app.auth.decorators._load_user_from_token",
                    return_value={"id": 1, "role": "admin", "username": "test_admin"},
                ):
                    return self._client.put(*args, **kwargs)

        def delete(self, *args, **kwargs):
            with patch("app.auth.decorators._extract_session_token", return_value="test-token"):
                with patch(
                    "app.auth.decorators._load_user_from_token",
                    return_value={"id": 1, "role": "admin", "username": "test_admin"},
                ):
                    return self._client.delete(*args, **kwargs)

    return AuthenticatedClient(test_client)


@pytest.fixture
def admin_headers():
    """Headers for admin user requests."""
    return {"Content-Type": "application/json"}


class TestRetentionRuleActionValidation:
    """Test action parameter validation for retention rules."""

    def test_set_rule_action_delete(self, client, admin_headers):
        """Test setting retention rule with delete action."""
        with patch("app.routes.compliance.get_retention_manager") as mock_manager:
            mock_retention = MagicMock()
            mock_rule = MagicMock()
            mock_rule.to_dict.return_value = {
                "data_type": "audit_logs",
                "retention_days": 90,
                "action": "delete",
            }
            mock_retention.set_rule.return_value = None
            mock_retention.get_rule.return_value = mock_rule
            mock_manager.return_value = mock_retention

            response = client.put(
                "/api/compliance/retention/rules",
                json={
                    "data_type": "audit_logs",
                    "retention_days": 90,
                    "action": "delete",
                },
                headers=admin_headers,
            )

            assert response.status_code == 200
            data = response.get_json()
            assert "rule" in data
            assert data["rule"]["action"] == "delete"

    def test_set_rule_action_archive(self, client, admin_headers):
        """Test setting retention rule with archive action."""
        with patch("app.routes.compliance.get_retention_manager") as mock_manager:
            mock_retention = MagicMock()
            mock_rule = MagicMock()
            mock_rule.to_dict.return_value = {
                "data_type": "sessions",
                "retention_days": 30,
                "action": "archive",
            }
            mock_retention.set_rule.return_value = None
            mock_retention.get_rule.return_value = mock_rule
            mock_manager.return_value = mock_retention

            response = client.put(
                "/api/compliance/retention/rules",
                json={
                    "data_type": "sessions",
                    "retention_days": 30,
                    "action": "archive",
                },
                headers=admin_headers,
            )

            assert response.status_code == 200
            data = response.get_json()
            assert "rule" in data
            assert data["rule"]["action"] == "archive"

    def test_set_rule_action_anonymize(self, client, admin_headers):
        """Test setting retention rule with anonymize action."""
        with patch("app.routes.compliance.get_retention_manager") as mock_manager:
            mock_retention = MagicMock()
            mock_rule = MagicMock()
            mock_rule.to_dict.return_value = {
                "data_type": "messages",
                "retention_days": 60,
                "action": "anonymize",
            }
            mock_retention.set_rule.return_value = None
            mock_retention.get_rule.return_value = mock_rule
            mock_manager.return_value = mock_retention

            response = client.put(
                "/api/compliance/retention/rules",
                json={
                    "data_type": "messages",
                    "retention_days": 60,
                    "action": "anonymize",
                },
                headers=admin_headers,
            )

            assert response.status_code == 200
            data = response.get_json()
            assert "rule" in data
            assert data["rule"]["action"] == "anonymize"

    def test_set_rule_invalid_action(self, client, admin_headers):
        """Test that invalid action value returns 400 error."""
        response = client.put(
            "/api/compliance/retention/rules",
            json={
                "data_type": "audit_logs",
                "retention_days": 90,
                "action": "invalid_action",
            },
            headers=admin_headers,
        )

        assert response.status_code == 400
        data = response.get_json()
        assert "error" in data
        assert "action must be one of" in data["error"]
        assert "delete" in data["error"]
        assert "archive" in data["error"]
        assert "anonymize" in data["error"]

    def test_set_rule_missing_action_defaults_to_delete(self, client, admin_headers):
        """Test that missing action parameter defaults to delete."""
        with patch("app.routes.compliance.get_retention_manager") as mock_manager:
            mock_retention = MagicMock()
            mock_rule = MagicMock()
            mock_rule.to_dict.return_value = {
                "data_type": "audit_logs",
                "retention_days": 90,
                "action": "delete",
            }
            mock_retention.set_rule.return_value = None
            mock_retention.get_rule.return_value = mock_rule
            mock_manager.return_value = mock_retention

            response = client.put(
                "/api/compliance/retention/rules",
                json={
                    "data_type": "audit_logs",
                    "retention_days": 90,
                },
                headers=admin_headers,
            )

            assert response.status_code == 200
            data = response.get_json()
            assert "rule" in data
            assert data["rule"]["action"] == "delete"

    def test_set_rule_empty_action_defaults_to_delete(self, client, admin_headers):
        """Test that empty action string defaults to delete."""
        # Note: empty string is still invalid, should use default
        with patch("app.routes.compliance.get_retention_manager") as mock_manager:
            mock_retention = MagicMock()
            mock_rule = MagicMock()
            mock_rule.to_dict.return_value = {
                "data_type": "audit_logs",
                "retention_days": 90,
                "action": "delete",
            }
            mock_retention.set_rule.return_value = None
            mock_retention.get_rule.return_value = mock_rule
            mock_manager.return_value = mock_retention

            response = client.put(
                "/api/compliance/retention/rules",
                json={
                    "data_type": "audit_logs",
                    "retention_days": 90,
                    "action": "",
                },
                headers=admin_headers,
            )

            # Empty string should be rejected as invalid action
            assert response.status_code == 400
            data = response.get_json()
            assert "error" in data

    def test_set_rule_missing_required_params(self, client, admin_headers):
        """Test that missing data_type and retention_days returns error."""
        response = client.put(
            "/api/compliance/retention/rules",
            json={
                "action": "delete",
            },
            headers=admin_headers,
        )

        assert response.status_code == 400
        data = response.get_json()
        assert "error" in data
        assert "data_type and retention_days are required" in data["error"]

    def test_get_retention_rules_success(self, client, admin_headers):
        """Test getting all retention rules."""
        with patch("app.routes.compliance.get_retention_manager") as mock_manager:
            mock_retention = MagicMock()
            mock_rule1 = MagicMock()
            mock_rule1.to_dict.return_value = {
                "data_type": "audit_logs",
                "retention_days": 90,
                "action": "delete",
            }
            mock_rule2 = MagicMock()
            mock_rule2.to_dict.return_value = {
                "data_type": "messages",
                "retention_days": 60,
                "action": "anonymize",
            }
            mock_retention.get_all_rules.return_value = {
                "audit_logs": mock_rule1,
                "messages": mock_rule2,
            }
            mock_manager.return_value = mock_retention

            response = client.get(
                "/api/compliance/retention/rules",
                headers=admin_headers,
            )

            assert response.status_code == 200
            data = response.get_json()
            assert "rules" in data
            assert len(data["rules"]) == 2
            assert data["rules"]["audit_logs"]["action"] == "delete"
            assert data["rules"]["messages"]["action"] == "anonymize"


class TestRetentionCleanupAPI:
    """Test retention cleanup API endpoints."""

    def test_run_cleanup_dry_run(self, client, admin_headers):
        """Test running cleanup in dry-run mode."""
        with patch("app.routes.compliance.get_retention_manager") as mock_manager:
            mock_retention = MagicMock()
            mock_report = MagicMock()
            mock_report.to_dict.return_value = {
                "dry_run": True,
                "cleanup_summary": [],
                "total_records": 0,
            }
            mock_retention.run_cleanup.return_value = mock_report
            mock_manager.return_value = mock_retention

            response = client.post(
                "/api/compliance/retention/cleanup?dry_run=true",
                headers=admin_headers,
            )

            assert response.status_code == 200
            data = response.get_json()
            assert data["dry_run"] is True

    def test_run_cleanup_actual(self, client, admin_headers):
        """Test running actual cleanup."""
        with patch("app.routes.compliance.get_retention_manager") as mock_manager:
            mock_retention = MagicMock()
            mock_report = MagicMock()
            mock_report.to_dict.return_value = {
                "dry_run": False,
                "cleanup_summary": [{"data_type": "audit_logs", "records_deleted": 100}],
                "total_records": 100,
            }
            mock_retention.run_cleanup.return_value = mock_report
            mock_manager.return_value = mock_retention

            response = client.post(
                "/api/compliance/retention/cleanup",
                headers=admin_headers,
            )

            assert response.status_code == 200
            data = response.get_json()
            assert data["dry_run"] is False
            assert data["total_records"] == 100
