"""Tests for frontend error reporting endpoint.

Issue #3489: Frontend error logging uses reserved fields causing 500.
"""

import pytest


@pytest.fixture
def app():
    """Create Flask app for testing."""
    import os
    import sys

    os.environ.setdefault("OPENACE_SECURITY_MODE", "development")
    os.environ.setdefault("OPENACE_ENCRYPTION_KEY", "test-encryption-key-for-unit-tests-32ch")
    os.environ.setdefault("SECRET_KEY", "test-secret-key-for-unit-tests-32-char")

    from app import create_app

    app = create_app()
    app.config["TESTING"] = True
    yield app


@pytest.fixture
def client(app):
    """Create test client."""
    return app.test_client()


class TestFrontendErrorReport:
    """Tests for /frontend-errors endpoint."""

    def test_frontend_errors_with_reserved_field_names_returns_200(self, client):
        """Test that frontend error report succeeds with name and message fields."""
        payload = {
            "category": "render",
            "errorId": "test-error-id",
            "name": "Error",  # 保留字段名称
            "message": "test message",  # 保留字段名称
            "buildVersion": "test",
            "pathname": "/workspace",
            "commitSha": "test",
            "userAgent": "test",
        }

        resp = client.post(
            "/api/frontend-errors",
            json=payload,
            headers={"Content-Type": "application/json"},
        )

        assert resp.status_code == 200
        assert resp.json["status"] == "ok"

    def test_frontend_errors_missing_required_field_returns_400(self, client):
        """Test that missing required field returns 400."""
        payload = {
            "category": "render",
            # 缺少 errorId
            "name": "Error",
            "message": "test message",
            "buildVersion": "test",
        }

        resp = client.post(
            "/api/frontend-errors",
            json=payload,
            headers={"Content-Type": "application/json"},
        )

        assert resp.status_code == 400
        assert "error" in resp.json

    def test_frontend_errors_rate_limit_returns_429(self, client):
        """Test that rate limiting works."""
        payload = {
            "category": "render",
            "errorId": "test-error-id",
            "name": "Error",
            "message": "test message",
            "buildVersion": "test",
        }

        # 使用固定 IP 地址测试限流功能
        test_ip = "10.0.0.100"

        # 发送超过限制的请求（RATE_LIMIT = 10）
        for i in range(11):
            resp = client.post(
                "/api/frontend-errors",
                json={**payload, "errorId": f"rate-limit-test-{i}"},
                headers={
                    "Content-Type": "application/json",
                    "X-Forwarded-For": test_ip,  # 使用固定 IP
                },
            )

            if i < 10:
                assert resp.status_code == 200, f"Request {i+1} should succeed"
            else:
                assert resp.status_code == 429, f"Request {i+1} should be rate limited"

        # 使用新 IP 发送请求应该成功
        resp = client.post(
            "/api/frontend-errors",
            json={**payload, "errorId": "new-ip-test"},
            headers={
                "Content-Type": "application/json",
                "X-Forwarded-For": "10.0.0.2",  # 新 IP
            },
        )
        assert resp.status_code == 200
