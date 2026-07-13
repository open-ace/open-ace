#!/usr/bin/env python3
"""
API test for audit-actions endpoint (Issue #1439).

Tests:
- GET /api/audit-actions returns correct structure
- Response contains 31 actions and 8 categories
- Each action has required fields
"""

import os
import sys

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BASE_URL = os.environ.get("BASE_URL", "http://localhost:19888")

issues = []


def add_issue(endpoint, description, severity="medium"):
    issues.append({"endpoint": endpoint, "description": description, "severity": severity})
    print(f"  [ISSUE-{severity.upper()}] {endpoint}: {description}")


def test_audit_actions_api():
    """Test /api/audit-actions endpoint."""
    s = requests.Session()

    # Login
    print("\n[1] Login")
    r = s.post(
        f"{BASE_URL}/api/auth/login",
        json={"username": "admin", "password": "admin123"},
        timeout=10,
    )
    if r.status_code != 200 or not r.json().get("success"):
        print("  FAIL - Login failed")
        return False
    print("  OK - Login successful")

    # Test audit-actions API
    print("\n[2] GET /api/audit-actions")
    r = s.get(f"{BASE_URL}/api/audit-actions", timeout=10)

    if r.status_code != 200:
        add_issue("/api/audit-actions", f"HTTP {r.status_code}", "high")
        return False

    data = r.json()

    # Validate response structure
    if "actions" not in data:
        add_issue("/api/audit-actions", "Missing 'actions' in response", "high")
        return False

    if "categories" not in data:
        add_issue("/api/audit-actions", "Missing 'categories' in response", "high")
        return False

    actions = data["actions"]
    categories = data["categories"]

    # Validate actions count
    if len(actions) != 31:
        add_issue("/api/audit-actions", f"Expected 31 actions, got {len(actions)}", "high")
        return False

    print(f"  OK - Actions count: {len(actions)}")

    # Validate categories count
    if len(categories) != 8:
        add_issue(
            "/api/audit-actions",
            f"Expected 8 categories, got {len(categories)}",
            "high",
        )
        return False

    print(f"  OK - Categories count: {len(categories)}")

    # Validate each action has required fields
    required_action_fields = ["value", "label", "category", "i18n_key"]
    for i, action in enumerate(actions):
        for field in required_action_fields:
            if field not in action:
                add_issue(
                    "/api/audit-actions",
                    f"Action {i} missing field '{field}'",
                    "medium",
                )
                return False

    print("  OK - All actions have required fields")

    # Validate each category has required fields
    required_category_fields = ["key", "label", "i18n_key"]
    for i, category in enumerate(categories):
        for field in required_category_fields:
            if field not in category:
                add_issue(
                    "/api/audit-actions",
                    f"Category {i} missing field '{field}'",
                    "medium",
                )
                return False

    print("  OK - All categories have required fields")

    # Validate all categories are present
    expected_categories = [
        "auth",
        "user_management",
        "permission",
        "quota",
        "data",
        "system",
        "content",
        "agent",
    ]
    category_keys = [c["key"] for c in categories]
    for expected in expected_categories:
        if expected not in category_keys:
            add_issue("/api/audit-actions", f"Missing category: {expected}", "medium")
            return False

    print("  OK - All expected categories present")

    return True


if __name__ == "__main__":
    print("=" * 60)
    print("API Test: /api/audit-actions (Issue #1439)")
    print("=" * 60)

    success = test_audit_actions_api()

    print("\n" + "=" * 60)
    if success and not issues:
        print("All tests passed!")
    else:
        print(f"Found {len(issues)} issues:")
        for issue in issues:
            print(f"  - [{issue['severity'].upper()}] {issue['description']}")
    print("=" * 60)

    sys.exit(0 if success and not issues else 1)
