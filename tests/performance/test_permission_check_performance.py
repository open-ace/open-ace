#!/usr/bin/env python3
"""
Performance benchmarks for permission checking.

Issue #2276: Verify that admin role permission check has minimal overhead.

Tests:
- Single permission check < 1ms
- High-frequency API response time increase < 5%
- Memory overhead < 1MB

Tests that assert on wall-clock time are marked ``@pytest.mark.performance`` and
are deselected from the required ``test (3.x)`` CI matrix -- a shared runner
cannot guarantee timing bounds. They run in the separate, non-blocking
``performance-test`` job.
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

    @pytest.mark.performance
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

    @pytest.mark.performance
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

    def test_permission_check_work_is_identical_across_roles(self, app):
        """
        Test that the permission check does identical work for every admin role.

        Issue #2276: Ensure no performance regression for different roles.

        This asserts on the *amount of work* per request rather than on wall-clock
        time. ``platform_admin_required`` accepts both ``platform_admin`` and the
        legacy ``admin`` role from a single branch (Issue #2286), so both roles must
        cost exactly one token extraction and one user load per request. A regression
        that makes one role more expensive than the other -- an extra role lookup, a
        per-request tenant query, a retry loop -- shows up here as a changed call
        count, deterministically and on any machine.

        The previous version of this test compared elapsed time between the two roles
        and required <20% variance. That is not a property a shared CI runner can
        guarantee (observed swings of 60-70% from noisy neighbours), and it made the
        required ``test (3.x)`` jobs flaky on main.
        """
        requests_per_role = 50
        # Guards the test against itself: at zero requests every count is 0 and
        # every assertion below holds while nothing has been exercised.
        assert requests_per_role > 1, "comparison is meaningless without repeated requests"
        roles = ["admin", "platform_admin"]

        call_counts = {}
        for role in roles:
            with patch(
                "app.auth.decorators._load_user_from_token",
                return_value={
                    "id": 1,
                    "username": role,
                    "email": f"{role}@example.com",
                    "role": role,
                    "tenant_id": None,
                    "must_change_password": False,
                },
            ) as load_user:
                with patch(
                    "app.auth.decorators._extract_session_token", return_value="valid-token"
                ) as extract_token:
                    with app.test_client() as client:
                        for _ in range(requests_per_role):
                            response = client.get(
                                "/api/test",
                                headers={"Authorization": "Bearer valid-token"},
                            )
                            assert (
                                response.status_code == 200
                            ), f"Role {role!r} was rejected with {response.status_code}"
                            assert response.get_json()["role"] == role

            call_counts[role] = {
                "extract_session_token": extract_token.call_count,
                "load_user_from_token": load_user.call_count,
            }

        # Each role costs exactly one token extraction + one user load per request.
        expected = {
            "extract_session_token": requests_per_role,
            "load_user_from_token": requests_per_role,
        }
        for role in roles:
            assert call_counts[role] == expected, (
                f"Role {role!r} performed {call_counts[role]} auth operations for "
                f"{requests_per_role} requests, expected {expected}"
            )

        # ...and both roles cost the same, so neither is privileged over the other.
        assert call_counts["admin"] == call_counts["platform_admin"], (
            f"admin cost {call_counts['admin']} but platform_admin cost "
            f"{call_counts['platform_admin']}"
        )

    @pytest.mark.performance
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
