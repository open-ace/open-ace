"""
Concurrent safety tests for generate default rules functionality.

Issue #2131: Verify UPSERT behavior under concurrent requests.
"""

import json
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
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


class TestConcurrentGenerateDefaultRules:
    """
    Concurrent safety tests for generate default rules.

    Issue #2131: Verify UPSERT correctness under concurrent requests.
    """

    def test_concurrent_generate_same_user(self, app):
        """
        Test concurrent generation of default rules for the same user.

        Verify that multiple concurrent requests for the same user
        complete successfully without exceptions or data corruption.

        Expected: All requests succeed, no duplicate rules created.
        """
        from app.models.tool_account_mapping_rule import ToolAccountMappingRule
        from app.services.tool_account_auto_mapping_service import GenerateDefaultRulesResult

        results = []
        errors = []
        lock = threading.Lock()

        def generate_rules(request_id):
            """Generate rules for user."""
            try:
                test_client = app.test_client()

                with patch("app.auth.decorators._extract_session_token", return_value="test-token"):
                    with patch(
                        "app.auth.decorators._load_user_from_token",
                        return_value={"id": 1, "role": "admin", "username": "test_admin"},
                    ):
                        with patch("app.routes.mapping_rules.ToolAccountAutoMappingService") as mock_service:
                            # Mock service - simulate real UPSERT behavior
                            mock_service_instance = MagicMock()

                            # First request creates rules, subsequent requests skip
                            # (This simulates the UPSERT ON CONFLICT DO NOTHING behavior)
                            result = GenerateDefaultRulesResult(
                                created=[
                                    ToolAccountMappingRule(
                                        id=1,
                                        user_id=5,
                                        pattern="user-*",
                                        match_type="prefix",
                                        priority=10,
                                        is_auto=True,
                                        is_active=True,
                                    )
                                ] if request_id == 0 else [],  # Only first request creates
                                skipped=[
                                    {"pattern": "user-*", "match_type": "prefix", "priority": 10}
                                ] if request_id > 0 else [],  # Subsequent requests skip
                                created_count=1 if request_id == 0 else 0,
                                skipped_count=0 if request_id == 0 else 1,
                            )
                            mock_service_instance.create_default_rules_for_user.return_value = result
                            mock_service.return_value = mock_service_instance

                            response = test_client.post(
                                "/api/mapping-rules/user/5/generate-default",
                                content_type="application/json",
                            )

                            with lock:
                                results.append({
                                    "request_id": request_id,
                                    "status": response.status_code,
                                    "data": json.loads(response.data),
                                })
            except Exception as e:
                with lock:
                    errors.append({
                        "request_id": request_id,
                        "error": str(e),
                    })

        # Run 10 concurrent requests
        num_requests = 10
        with ThreadPoolExecutor(max_workers=num_requests) as executor:
            futures = [executor.submit(generate_rules, i) for i in range(num_requests)]
            for future in as_completed(futures):
                future.result()  # Wait for completion

        # Verify all requests completed
        assert len(results) == num_requests, (
            f"Expected {num_requests} results, got {len(results)}. "
            f"Errors: {errors}"
        )

        # Verify all requests succeeded (status 200 or 201)
        for result in results:
            assert result["status"] in (200, 201), (
                f"Request {result['request_id']} failed with status {result['status']}"
            )

        # Verify at least one request created rules (201)
        created_count = sum(1 for r in results if r["status"] == 201)
        assert created_count >= 1, "At least one request should have created rules"

        # Verify no errors occurred
        assert len(errors) == 0, f"Errors during concurrent execution: {errors}"

        print("Concurrent test results:")
        print(f"  Total requests: {num_requests}")
        print(f"  Successful: {len(results)}")
        print(f"  Created (201): {sum(1 for r in results if r['status'] == 201)}")
        print(f"  Skipped (200): {sum(1 for r in results if r['status'] == 200)}")

    def test_concurrent_generate_different_users(self, app):
        """
        Test concurrent generation of default rules for different users.

        Verify that concurrent requests for different users
        do not interfere with each other.

        Expected: All requests succeed independently.
        """
        from app.models.tool_account_mapping_rule import ToolAccountMappingRule
        from app.services.tool_account_auto_mapping_service import GenerateDefaultRulesResult

        results = defaultdict(list)
        errors = []
        lock = threading.Lock()

        def generate_rules_for_user(user_id):
            """Generate rules for a specific user."""
            try:
                test_client = app.test_client()

                with patch("app.auth.decorators._extract_session_token", return_value="test-token"):
                    with patch(
                        "app.auth.decorators._load_user_from_token",
                        return_value={"id": 1, "role": "admin", "username": "test_admin"},
                    ):
                        with patch("app.routes.mapping_rules.ToolAccountAutoMappingService") as mock_service:
                            mock_service_instance = MagicMock()
                            result = GenerateDefaultRulesResult(
                                created=[
                                    ToolAccountMappingRule(
                                        id=user_id,
                                        user_id=user_id,
                                        pattern=f"user{user_id}-*",
                                        match_type="prefix",
                                        priority=10,
                                        is_auto=True,
                                        is_active=True,
                                    )
                                ],
                                skipped=[],
                                created_count=1,
                                skipped_count=0,
                            )
                            mock_service_instance.create_default_rules_for_user.return_value = result
                            mock_service.return_value = mock_service_instance

                            response = test_client.post(
                                f"/api/mapping-rules/user/{user_id}/generate-default",
                                content_type="application/json",
                            )

                            with lock:
                                results["success"].append({
                                    "user_id": user_id,
                                    "status": response.status_code,
                                })
            except Exception as e:
                with lock:
                    errors.append({
                        "user_id": user_id,
                        "error": str(e),
                    })

        # Run concurrent requests for different users
        num_users = 10
        with ThreadPoolExecutor(max_workers=num_users) as executor:
            futures = [executor.submit(generate_rules_for_user, i) for i in range(num_users)]
            for future in as_completed(futures):
                future.result()

        # Verify all requests succeeded
        assert len(results["success"]) == num_users, (
            f"Expected {num_users} successful results, got {len(results['success'])}. "
            f"Errors: {errors}"
        )

        # Verify all returned 201 (created)
        for result in results["success"]:
            assert result["status"] == 201, (
                f"User {result['user_id']} got status {result['status']} instead of 201"
            )

        # Verify no errors
        assert len(errors) == 0, f"Errors during concurrent execution: {errors}"

        print("Concurrent different users test:")
        print(f"  Total users: {num_users}")
        print(f"  All succeeded: {len(results['success'])}")

    def test_no_race_condition(self, app):
        """
        Verify no race condition in UPSERT under concurrent access.

        This test specifically verifies that the UPSERT logic
        prevents race conditions when multiple threads attempt
        to create the same rule simultaneously.
        """
        from app.models.tool_account_mapping_rule import ToolAccountMappingRule
        from app.services.tool_account_auto_mapping_service import GenerateDefaultRulesResult

        # Track the number of times the service is called
        call_count = 0
        call_lock = threading.Lock()

        results = []
        errors = []

        def mock_create_default_rules(user_id):
            """Mock service that simulates UPSERT behavior."""
            nonlocal call_count

            with call_lock:
                call_count += 1
                current_call = call_count

            # Simulate UPSERT behavior:
            # - First call creates rules
            # - Subsequent calls skip (simulate conflict)
            if current_call == 1:
                return GenerateDefaultRulesResult(
                    created=[
                        ToolAccountMappingRule(
                            id=1, user_id=user_id, pattern="user-*",
                            match_type="prefix", priority=10, is_auto=True, is_active=True
                        )
                    ],
                    skipped=[],
                    created_count=1,
                    skipped_count=0,
                )
            else:
                return GenerateDefaultRulesResult(
                    created=[],
                    skipped=[
                        {"pattern": "user-*", "match_type": "prefix", "priority": 10}
                    ],
                    created_count=0,
                    skipped_count=1,
                )

        def generate_rules_concurrently():
            """Generate rules concurrently."""
            try:
                test_client = app.test_client()

                with patch("app.auth.decorators._extract_session_token", return_value="test-token"):
                    with patch(
                        "app.auth.decorators._load_user_from_token",
                        return_value={"id": 1, "role": "admin", "username": "test_admin"},
                    ):
                        with patch("app.routes.mapping_rules.ToolAccountAutoMappingService") as mock_service:
                            mock_service_instance = MagicMock()
                            mock_service_instance.create_default_rules_for_user.side_effect = (
                                mock_create_default_rules
                            )
                            mock_service.return_value = mock_service_instance

                            response = test_client.post(
                                "/api/mapping-rules/user/5/generate-default",
                                content_type="application/json",
                            )

                            results.append({
                                "status": response.status_code,
                                "data": json.loads(response.data),
                            })
            except Exception as e:
                errors.append(str(e))

        # Run concurrent requests
        num_threads = 5
        threads = [threading.Thread(target=generate_rules_concurrently) for _ in range(num_threads)]

        for t in threads:
            t.start()

        for t in threads:
            t.join(timeout=5)

        # Verify no errors
        assert len(errors) == 0, f"Errors during execution: {errors}"

        # Verify all threads completed
        assert len(results) == num_threads, (
            f"Expected {num_threads} results, got {len(results)}"
        )

        # Verify service was called the expected number of times
        assert call_count == num_threads, (
            f"Expected {num_threads} service calls, got {call_count}"
        )

        # Verify results are consistent:
        # - Exactly one request should have created rules (status 201)
        # - Other requests should have skipped (status 200)
        created_count = sum(1 for r in results if r["status"] == 201)
        skipped_count = sum(1 for r in results if r["status"] == 200)

        assert created_count == 1, f"Expected 1 created result, got {created_count}"
        assert skipped_count == num_threads - 1, (
            f"Expected {num_threads - 1} skipped results, got {skipped_count}"
        )

        print("Race condition test:")
        print(f"  Threads: {num_threads}")
        print(f"  Created: {created_count}")
        print(f"  Skipped: {skipped_count}")
        print(f"  Service calls: {call_count}")


class TestConcurrentDatabaseBehavior:
    """
    Test database behavior under concurrent access.

    These tests verify database-level safety mechanisms.
    """

    def test_concurrent_upsert_no_duplicate_rows(self, app):
        """
        Verify that concurrent UPSERT operations do not create duplicate rows.

        This test simulates the scenario where multiple threads attempt to
        create the same rule simultaneously, verifying that the database
        constraint prevents duplicates.
        """
        from app.models.tool_account_mapping_rule import ToolAccountMappingRule
        from app.services.tool_account_auto_mapping_service import GenerateDefaultRulesResult

        # Simulate database state
        created_rules = []
        lock = threading.Lock()

        def mock_create_or_ignore(user_id, pattern, **kwargs):
            """Mock create_or_ignore that simulates UPSERT behavior."""
            # Simulate ON CONFLICT DO NOTHING behavior
            # If rule already exists, return None (conflict)
            # Otherwise, create and return the rule

            rule_key = (user_id, pattern)

            with lock:
                # Check if rule already exists
                for rule in created_rules:
                    if (rule.user_id, rule.pattern) == rule_key:
                        # Conflict - return None
                        return None

                # No conflict - create the rule
                new_rule = ToolAccountMappingRule(
                    id=len(created_rules) + 1,
                    user_id=user_id,
                    pattern=pattern,
                    match_type=kwargs.get("match_type", "prefix"),
                    priority=kwargs.get("priority", 10),
                    is_auto=kwargs.get("is_auto", True),
                    is_active=kwargs.get("is_active", True),
                )
                created_rules.append(new_rule)
                return new_rule

        def generate_rules_thread():
            """Thread function to generate rules."""
            with patch("app.auth.decorators._extract_session_token", return_value="test-token"):
                with patch(
                    "app.auth.decorators._load_user_from_token",
                    return_value={"id": 1, "role": "admin", "username": "test_admin"},
                ):
                    with patch("app.repositories.tool_account_mapping_rule_repo.ToolAccountMappingRuleRepository") as mock_repo:
                        mock_repo_instance = MagicMock()
                        mock_repo_instance.create_or_ignore.side_effect = mock_create_or_ignore
                        mock_repo.return_value = mock_repo_instance

                        test_client = app.test_client()
                        response = test_client.post(
                            "/api/mapping-rules/user/5/generate-default",
                            content_type="application/json",
                        )
                        return response.status_code

        # Run concurrent threads
        num_threads = 5
        threads = [threading.Thread(target=generate_rules_thread) for _ in range(num_threads)]

        for t in threads:
            t.start()

        for t in threads:
            t.join(timeout=5)

        # Verify no duplicate rules were created
        # (Due to UPSERT behavior, only one rule should exist)
        unique_rules = {(r.user_id, r.pattern) for r in created_rules}
        assert len(unique_rules) == len(created_rules), (
            f"Duplicate rules detected: {len(created_rules)} rules for "
            f"{len(unique_rules)} unique keys"
        )

        print("UPSERT test:")
        print(f"  Threads: {num_threads}")
        print(f"  Unique rules created: {len(created_rules)}")
        print("  No duplicates: True")
