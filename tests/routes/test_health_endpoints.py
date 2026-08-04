"""Tests for health check endpoints.

Issue #2186: Health check endpoint separation.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def app():
    """Create Flask app for testing."""
    import os
    import sys

    # Set required environment variables for testing
    os.environ.setdefault("OPENACE_SECURITY_MODE", "development")
    os.environ.setdefault("OPENACE_ENCRYPTION_KEY", "test-encryption-key-for-unit-tests-32ch")
    os.environ.setdefault("SECRET_KEY", "test-secret-key-for-unit-tests-32-char")

    # Add scripts/shared to path
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    shared_path = os.path.join(project_root, "scripts", "shared")
    if shared_path not in sys.path:
        sys.path.insert(0, shared_path)

    from app import create_app

    app = create_app()
    app.config["TESTING"] = True
    yield app


@pytest.fixture
def client(app):
    """Create test client."""
    return app.test_client()


class TestLivezEndpoint:
    """Tests for /livez endpoint."""

    def test_livez_returns_200_alive_status(self, client):
        """Test that /livez returns 200 with alive status."""
        resp = client.get("/livez")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["status"] == "alive"
        assert "timestamp" in data

    def test_livez_response_time_fast(self, client):
        """Test that /livez responds quickly (< 100ms)."""
        import time

        start = time.time()
        resp = client.get("/livez")
        elapsed = time.time() - start

        assert resp.status_code == 200
        assert elapsed < 0.1  # 100ms

    def test_livez_does_not_check_database(self, client):
        """Test that /livez doesn't depend on database."""
        with patch("app.utils.health_checks.check_database_connection") as mock:
            resp = client.get("/livez")
            # The mock should not be called
            mock.assert_not_called()
            assert resp.status_code == 200


class TestReadyzEndpoint:
    """Tests for /readyz endpoint."""

    def test_readyz_returns_200_when_all_checks_pass(self, client, app):
        """Test that /readyz returns 200 when all checks pass."""
        with app.app_context():
            # Mock database check to pass
            with patch(
                "app.utils.health_checks.check_database_connection",
                return_value={"status": "ok"},
            ):
                with patch(
                    "app.utils.health_checks.check_config_directory",
                    return_value={"status": "ok"},
                ):
                    with patch(
                        "app.utils.health_checks.check_workspace_directory",
                        return_value={"status": "ok"},
                    ):
                        with patch(
                            "app.utils.health_checks.check_encryption_registry",
                            return_value={"status": "ok"},
                        ):
                            with patch(
                                "app.utils.health_checks.check_initialization_status",
                                return_value={"status": "ok"},
                            ):
                                resp = client.get("/readyz")
                                assert resp.status_code == 200
                                data = json.loads(resp.data)
                                assert data["status"] == "ready"

    def test_readyz_returns_503_when_database_unavailable(self, client, app):
        """Test that /readyz returns 503 when database is unavailable."""
        with app.app_context():
            with patch(
                "app.utils.health_checks.check_database_connection",
                return_value={"status": "error", "error": "connection_failed"},
            ):
                resp = client.get("/readyz")
                assert resp.status_code == 503
                data = json.loads(resp.data)
                assert data["status"] == "not_ready"
                assert data["checks"]["database"]["status"] == "error"

    def test_readyz_returns_503_when_config_dir_not_writable(self, client, app):
        """Test that /readyz returns 503 when config dir is not writable."""
        with app.app_context():
            with patch(
                "app.utils.health_checks.check_database_connection",
                return_value={"status": "ok"},
            ):
                with patch(
                    "app.utils.health_checks.check_config_directory",
                    return_value={"status": "error", "error": "not_writable"},
                ):
                    with patch(
                        "app.utils.health_checks.check_workspace_directory",
                        return_value={"status": "ok"},
                    ):
                        with patch(
                            "app.utils.health_checks.check_encryption_registry",
                            return_value={"status": "ok"},
                        ):
                            with patch(
                                "app.utils.health_checks.check_initialization_status",
                                return_value={"status": "ok"},
                            ):
                                resp = client.get("/readyz")
                                assert resp.status_code == 503

    def test_readyz_does_not_leak_secrets_in_error_response(self, client, app):
        """Test that /readyz doesn't leak sensitive information."""
        with app.app_context():
            # The check_database_connection function already sanitizes errors
            # We test that the sanitized error message is returned
            with patch(
                "app.utils.health_checks.check_database_connection",
                return_value={"status": "error", "error": "connection_failed"},
            ):
                resp = client.get("/readyz")
                data = json.loads(resp.data)

                # The error should be sanitized (no sensitive info)
                error_msg = data["checks"]["database"].get("error", "")
                assert "p@ssw0rd" not in error_msg
                assert "password" not in error_msg.lower()
                assert error_msg == "connection_failed"


class TestHealthEndpoint:
    """Tests for /health endpoint (deprecated)."""

    def test_health_returns_deprecated_flag(self, client, app):
        """Test that /health returns deprecated flag."""
        with app.app_context():
            with patch(
                "app.utils.health_checks.check_database_connection",
                return_value={"status": "ok"},
            ):
                with patch(
                    "app.utils.health_checks.check_config_directory",
                    return_value={"status": "ok"},
                ):
                    with patch(
                        "app.utils.health_checks.check_workspace_directory",
                        return_value={"status": "ok"},
                    ):
                        with patch(
                            "app.utils.health_checks.check_encryption_registry",
                            return_value={"status": "ok"},
                        ):
                            with patch(
                                "app.utils.health_checks.check_initialization_status",
                                return_value={"status": "ok"},
                            ):
                                resp = client.get("/health")
                                assert resp.status_code == 200
                                data = json.loads(resp.data)
                                assert data.get("deprecated") is True

    def test_health_delegates_to_readyz(self, client, app):
        """Test that /health delegates to /readyz logic."""
        with app.app_context():
            with patch(
                "app.utils.health_checks.check_database_connection",
                return_value={"status": "error", "error": "connection_failed"},
            ):
                resp = client.get("/health")
                # Should return same status as /readyz
                assert resp.status_code == 503
                data = json.loads(resp.data)
                assert data["status"] == "not_ready"


class TestMetricsEndpoint:
    """Tests for /metrics endpoint."""

    def test_metrics_endpoint_exists(self, client):
        """Test that /metrics endpoint exists."""
        resp = client.get("/metrics")
        # Endpoint exists (may return 503 if prometheus not configured)
        assert resp.status_code in (200, 503)


class TestHealthChecksUtility:
    """Tests for health check utility functions."""

    def test_sanitize_error_message_hides_password(self):
        """Test that sensitive info is hidden from errors."""
        from app.utils.health_checks import _sanitize_error_message

        # Password in connection string
        error = Exception("connection to postgresql://user:secret123@host failed")
        sanitized = _sanitize_error_message(error)
        assert "secret" not in sanitized
        assert sanitized == "authentication_failed"

    def test_sanitize_error_message_hides_api_key(self):
        """Test that API keys are hidden from errors."""
        from app.utils.health_checks import _sanitize_error_message

        error = Exception("API key sk-abc123xyz not found")
        sanitized = _sanitize_error_message(error)
        assert "abc123" not in sanitized
        assert "sk-" not in sanitized

    def test_sanitize_error_message_connection_error(self):
        """Test connection error sanitization."""
        from app.utils.health_checks import _sanitize_error_message

        error = Exception("Connection refused to database")
        sanitized = _sanitize_error_message(error)
        assert sanitized == "connection_failed"

    def test_check_initialization_status_ok(self):
        """Test initialization status check when OK."""
        from app.utils.health_checks import check_initialization_status, mark_init_completed

        # Reset and mark completed
        mark_init_completed()
        result = check_initialization_status()
        assert result["status"] == "ok"

    def test_check_initialization_status_error(self):
        """Test initialization status check when failed."""
        from app.utils.health_checks import check_initialization_status, set_init_error

        set_init_error("Test error", "test")
        result = check_initialization_status()
        assert result["status"] == "error"
        assert "Test error" in result["error"]

    def test_init_error_propagates_to_readyz(self, client, app):
        """Test that initialization errors are reflected in /readyz."""
        from app.utils.health_checks import set_init_error

        with app.app_context():
            # Set an initialization error
            set_init_error("Encryption initialization failed", "encryption")

            resp = client.get("/readyz")
            assert resp.status_code == 503
            data = json.loads(resp.data)
            assert data["checks"]["init_status"]["status"] == "error"

    def test_readyz_timeout_on_slow_operation(self, client, app):
        """Test that /readyz handles timeout on slow operations."""
        import time

        def slow_check():
            time.sleep(5)  # Simulate slow filesystem
            return {"status": "ok"}

        with app.app_context():
            with patch("app.utils.health_checks.check_config_directory", side_effect=slow_check):
                # Should not timeout because run_check_with_timeout has 1s timeout
                resp = client.get("/readyz")
                # Should return 503 due to timeout
                data = json.loads(resp.data)
                assert data["checks"]["config_dir"]["status"] == "error"
                assert data["checks"]["config_dir"]["error"] == "timeout"

    def test_metrics_returns_prometheus_format(self, client, app):
        """Test that /metrics returns Prometheus format when configured."""
        with app.app_context():
            resp = client.get("/metrics")
            # If prometheus_flask_exporter is initialized, it returns 200
            # with Prometheus format.
            # If not installed, fallback route returns 503.
            assert resp.status_code in (200, 503)

            if resp.status_code == 200:
                # Should be Prometheus format (text/plain)
                content_type = resp.content_type
                assert "text/plain" in content_type or "text/html" in content_type
