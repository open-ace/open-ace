#!/usr/bin/env python3
"""API-level test for audit log filter consistency."""

import sys

import requests

BASE_URL = "http://localhost:19888"


def main():
    print("Testing API...")
    session = requests.Session()

    # Test login
    r = session.post(
        f"{BASE_URL}/api/auth/login", json={"username": "admin", "password": "admin123"}, timeout=10
    )
    print(f"Login: {r.status_code}")

    if r.status_code != 200:
        print("Server not available, skipping API tests")
        return

    # Get audit logs without filter
    r = session.get(f"{BASE_URL}/api/governance/audit-logs", params={"limit": 100})
    data = r.json()
    baseline_total = data.get("total", 0)
    baseline_logs = data.get("logs", [])
    print(f"Baseline: total={baseline_total}, logs={len(baseline_logs)}")

    if not baseline_logs:
        print("No audit logs found, tests skipped")
        return

    # Get unique values
    actions = {log.get("action") for log in baseline_logs if log.get("action")}
    resource_types = {log.get("resource_type") for log in baseline_logs if log.get("resource_type")}

    print(f"Actions found: {list(actions)[:5]}")
    print(f"Resource types found: {list(resource_types)[:5]}")

    passed = 0
    failed = 0

    # Test action filter
    if actions:
        test_action = list(actions)[0]
        r = session.get(
            f"{BASE_URL}/api/governance/audit-logs", params={"action": test_action, "limit": 100}
        )
        data = r.json()
        filtered_logs = data.get("logs", [])
        filtered_total = data.get("total", 0)

        all_match = all(log.get("action") == test_action for log in filtered_logs)
        if all_match:
            passed += 1
            print(
                f"PASS: Action filter ({test_action}): total={filtered_total}, logs={len(filtered_logs)}"
            )
        else:
            failed += 1
            print("FAIL: Action filter returned logs with different actions!")
            for log in filtered_logs[:3]:
                print(f'  - log action: {log.get("action")} (expected: {test_action})')

    # Test resource_type filter
    if resource_types:
        test_rt = list(resource_types)[0]
        r = session.get(
            f"{BASE_URL}/api/governance/audit-logs", params={"resource_type": test_rt, "limit": 100}
        )
        data = r.json()
        filtered_logs = data.get("logs", [])
        filtered_total = data.get("total", 0)

        all_match = all(log.get("resource_type") == test_rt for log in filtered_logs)
        if all_match:
            passed += 1
            print(
                f"PASS: Resource_type filter ({test_rt}): total={filtered_total}, logs={len(filtered_logs)}"
            )
        else:
            failed += 1
            print("FAIL: Resource_type filter returned logs with different resource_types!")
            for log in filtered_logs[:3]:
                print(f'  - log resource_type: {log.get("resource_type")} (expected: {test_rt})')

    # Test combined filter
    if actions and resource_types:
        test_action = list(actions)[0]
        test_rt = list(resource_types)[0]
        r = session.get(
            f"{BASE_URL}/api/governance/audit-logs",
            params={"action": test_action, "resource_type": test_rt, "limit": 100},
        )
        data = r.json()
        filtered_logs = data.get("logs", [])
        filtered_total = data.get("total", 0)

        all_match = all(
            log.get("action") == test_action and log.get("resource_type") == test_rt
            for log in filtered_logs
        )
        if all_match:
            passed += 1
            print(f"PASS: Combined filter: total={filtered_total}, logs={len(filtered_logs)}")
        else:
            failed += 1
            print("FAIL: Combined filter returned inconsistent results!")

    print(f"\nResults: {passed} passed, {failed} failed")


if __name__ == "__main__":
    main()
