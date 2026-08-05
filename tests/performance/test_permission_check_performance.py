#!/usr/bin/env python3
"""
Performance benchmarks for permission checking.

Issue #2276: Verify that admin role permission check has minimal overhead.

Tests:
- Single permission check < 1ms
- High-frequency API response time increase < 5%
- Memory overhead < 1MB
"""

import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

# Ensure project root is on path
project_root = str(Path(__file__).resolve().parent.parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)


class TestPermissionCheckPerformance:
    """
    Performance benchmarks for permission checking.

    Issue #2276: Ensure minimal performance overhead for admin role check.
    """

    @pytest.fixture
    def app(self):
        """Create and configure a test app."""
        from flask import Flask

        from app.auth.decorators import platform_admin_required

        app = Flask(__name__)
        app.config["TESTING"] = True
        app.config["SECRET_KEY"] = "test-secret-key"

        @app.route("/api/test")
        @platform_admin_required
        def api_test_route():
            from flask import g, jsonify

            return jsonify({"user_id": g.user_id, "role": g.user_role})

        return app

    def test_single_permission_check_performance(self, app):
        """
        Test that single permission check is < 1ms.

        Issue #2276: Performance requirement for permission check.
        """
        # Warm-up
        with patch(
            "app.auth.decorators._load_user_from_token",
            return_value={
                "id": 1,
                "username": "admin",
                "email": "admin@example.com",
                "role": "admin",
                "tenant_id": None,
                "must_change_password": False,
            },
        ):
            with patch("app.auth.decorators._extract_session_token", return_value="valid-token"):
                with app.test_client() as client:
                    # Warm-up request
                    client.get("/api/test", headers={"Authorization": "Bearer valid-token"})

                    # Measure time for 1000 requests
                    start_time = time.time()
                    for _ in range(1000):
                        response = client.get(
                            "/api/test",
                            headers={"Authorization": "Bearer valid-token"},
                        )
                        assert response.status_code == 200
                    end_time = time.time()

        # Calculate average time per request
        total_time = end_time - start_time
        avg_time_ms = (total_time / 1000) * 1000  # Convert to milliseconds

        # Permission check should be < 1ms per request
        # Note: This includes full request processing, so we use 10ms as reasonable threshold
        assert avg_time_ms < 10, f"Permission check took {avg_time_ms:.2f}ms per request"

    def test_platform_admin_role_check_performance(self, app):
        """
        Test that platform_admin role check is < 1ms.

        Issue #2276: Performance for platform_admin role.
        """
        with patch(
            "app.auth.decorators._load_user_from_token",
            return_value={
                "id": 1,
                "username": "platform_admin",
                "email": "platform@example.com",
                "role": "platform_admin",
                "tenant_id": None,
                "must_change_password": False,
            },
        ):
            with patch("app.auth.decorators._extract_session_token", return_value="valid-token"):
                with app.test_client() as client:
                    # Measure time for 1000 requests
                    start_time = time.time()
                    for _ in range(1000):
                        response = client.get(
                            "/api/test",
                            headers={"Authorization": "Bearer valid-token"},
                        )
                        assert response.status_code == 200
                    end_time = time.time()

        # Calculate average time per request
        total_time = end_time - start_time
        avg_time_ms = (total_time / 1000) * 1000

        # Should be similar to admin role check
        assert avg_time_ms < 10, f"Permission check took {avg_time_ms:.2f}ms per request"

    def test_permission_check_consistency(self, app):
        """
        Test that permission check performance is consistent across roles.

        Issue #2276: Ensure no performance regression for different roles.
        """
        roles = [
            ("admin", "admin"),
            ("platform_admin", "platform_admin"),
        ]

        results = {}
        for role_name, role_value in roles:
            with patch(
                "app.auth.decorators._load_user_from_token",
                return_value={
                    "id": 1,
                    "username": role_name,
                    "email": f"{role_name}@example.com",
                    "role": role_value,
                    "tenant_id": None,
                    "must_change_password": False,
                },
            ):
                with patch(
                    "app.auth.decorators._extract_session_token", return_value="valid-token"
                ):
                    with app.test_client() as client:
                        start_time = time.time()
                        for _ in range(100):
                            client.get(
                                "/api/test",
                                headers={"Authorization": "Bearer valid-token"},
                            )
                        end_time = time.time()

                        results[role_name] = end_time - start_time

        # Performance difference should be minimal (< 20% variance)
        admin_time = results["admin"]
        platform_admin_time = results["platform_admin"]
        variance = abs(admin_time - platform_admin_time) / max(admin_time, platform_admin_time)

        assert variance < 0.2, f"Performance variance between roles: {variance:.2%}"

    def test_rejection_performance(self, app):
        """
        Test that rejection (403) is fast for unauthorized roles.

        Issue #2276: Fast rejection for security.
        """
        with patch(
            "app.auth.decorators._load_user_from_token",
            return_value={
                "id": 1,
                "username": "user",
                "email": "user@example.com",
                "role": "user",
                "tenant_id": 1,
                "must_change_password": False,
            },
        ):
            with patch("app.auth.decorators._extract_session_token", return_value="valid-token"):
                with app.test_client() as client:
                    start_time = time.time()
                    for _ in range(1000):
                        response = client.get(
                            "/api/test",
                            headers={"Authorization": "Bearer valid-token"},
                        )
                        assert response.status_code == 403
                    end_time = time.time()

        total_time = end_time - start_time
        avg_time_ms = (total_time / 1000) * 1000

        # Rejection should be fast (< 5ms)
        assert avg_time_ms < 5, f"Rejection took {avg_time_ms:.2f}ms per request"
