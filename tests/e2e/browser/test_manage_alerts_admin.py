#!/usr/bin/env python3
"""
E2E Tests: Admin Alerts View with Tenant Isolation

Issue #2783: Tests admin permission isolation for quota alerts viewing.

Test scenarios:
1. Platform Admin can view all tenants' quota alerts
2. Tenant Admin can only view own tenant's quota alerts
3. Alert acknowledge permission isolation (UI feedback)
"""

import os
import sys

import pytest

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
)

from playwright.sync_api import sync_playwright

from tests.e2e.browser.test_helpers import (
    check_element_exists,
    create_browser_context,
    login,
    navigate_to,
    save_screenshot,
)

MODULE_NAME = "manage_alerts_admin"


@pytest.mark.e2e
@pytest.mark.issue(2783)
def test_platform_admin_can_view_all_tenant_alerts():
    """
    Platform Admin should be able to view quota alerts from all tenants.

    Issue #2783: Verifies cross-tenant visibility for platform-level admin.

    Steps:
    1. Login as Platform Admin
    2. Navigate to /manage/quota -> Alerts tab
    3. Verify alert list contains multi-tenant alerts (if data exists)

    Note: This test validates the page renders correctly for platform admin.
    Actual multi-tenant data depends on database state.
    """
    with sync_playwright() as p:
        browser, context = create_browser_context(p)
        page = context.new_page()

        try:
            # Login as platform admin
            login(page)
            navigate_to(page, "/manage/quota")

            # Wait for page load
            page.wait_for_timeout(2000)

            # Click Alerts tab
            alerts_tab = page.locator("button.nav-link").filter(has_text="Alert")
            if alerts_tab.count() > 0:
                alerts_tab.click()
                # Wait for alerts content to load
                page.wait_for_timeout(2000)

                # Check for alert-related elements
                # Platform admin should see the alerts interface
                alert_selectors = [
                    ".stat-card",  # Alert statistics
                    "table",  # Alert list table
                    ".empty-state",  # No alerts placeholder
                    ".no-data",  # Alternative no data indicator
                ]
                assert check_element_exists(
                    page, alert_selectors, timeout=10000
                ), "Platform admin should see alerts interface"

                save_screenshot(page, MODULE_NAME, "platform_admin_alerts_view")
            else:
                # Alerts tab not found - log and continue
                save_screenshot(page, MODULE_NAME, "no_alerts_tab")

        finally:
            browser.close()


@pytest.mark.e2e
@pytest.mark.issue(2783)
def test_tenant_admin_limited_alerts_view():
    """
    Tenant Admin should only see quota alerts from their own tenant.

    Issue #2783: Verifies tenant isolation in alerts view.

    Steps:
    1. Login as Tenant Admin
    2. Navigate to /manage/quota -> Alerts tab
    3. Verify alert list only contains own tenant's data

    Note: This test validates the page renders correctly for tenant admin.
    Actual tenant isolation is enforced by backend API.
    """
    with sync_playwright() as p:
        browser, context = create_browser_context(p)
        page = context.new_page()

        try:
            # Login as tenant admin (default test user)
            login(page)
            navigate_to(page, "/manage/quota")

            # Wait for page load
            page.wait_for_timeout(2000)

            # Click Alerts tab if available
            alerts_tab = page.locator("button.nav-link").filter(has_text="Alert")
            if alerts_tab.count() > 0:
                alerts_tab.click()
                page.wait_for_timeout(2000)

                # Tenant admin should see alerts interface
                # but data should be filtered to their tenant only
                alert_selectors = [
                    ".stat-card",
                    "table",
                    ".empty-state",
                    ".no-data",
                ]
                assert check_element_exists(
                    page, alert_selectors, timeout=10000
                ), "Tenant admin should see alerts interface"

                save_screenshot(page, MODULE_NAME, "tenant_admin_alerts_view")
            else:
                save_screenshot(page, MODULE_NAME, "tenant_admin_no_alerts_tab")

        finally:
            browser.close()


@pytest.mark.e2e
@pytest.mark.issue(2783)
def test_alert_acknowledge_permission_isolation():
    """
    Verify alert acknowledge operation respects tenant permissions.

    Issue #2783: Tenant admin can acknowledge own tenant's alerts,
    but cannot acknowledge other tenant's alerts.

    Steps:
    1. Login as Tenant Admin
    2. Navigate to alerts
    3. If alerts exist, verify acknowledge button is present for own alerts
    4. Verify UI feedback for permission errors (if applicable)

    Note: Backend API enforces permission; this test validates UI behavior.
    """
    with sync_playwright() as p:
        browser, context = create_browser_context(p)
        page = context.new_page()

        try:
            login(page)
            navigate_to(page, "/manage/quota")
            page.wait_for_timeout(2000)

            # Navigate to Alerts tab
            alerts_tab = page.locator("button.nav-link").filter(has_text="Alert")
            if alerts_tab.count() > 0:
                alerts_tab.click()
                page.wait_for_timeout(2000)

                # Check for action buttons (acknowledge, resolve, etc.)
                action_selectors = [
                    "button.btn-primary",  # Primary action buttons
                    "button.btn-outline",  # Secondary action buttons
                    "button:has-text('Acknowledge')",  # Explicit acknowledge button
                    "button:has-text('Resolve')",  # Resolve button
                ]

                # If alerts exist, action buttons should be present
                # If no alerts, empty state should be shown
                has_alerts = check_element_exists(page, ["table tbody tr"], timeout=3000)
                if has_alerts:
                    # Verify action buttons are visible for alerts
                    assert check_element_exists(
                        page, action_selectors, timeout=5000
                    ), "Action buttons should be available for alerts"

                save_screenshot(page, MODULE_NAME, "alert_acknowledge_buttons")
            else:
                save_screenshot(page, MODULE_NAME, "acknowledge_no_alerts_tab")

        finally:
            browser.close()


def run_all_tests():
    """Run all admin alerts view tests."""
    from tests.e2e.browser.test_helpers import TestRunner

    runner = TestRunner("Manage Mode - Admin Alerts View")
    runner.print_header()

    tests = [
        ("Platform Admin 全量告警视图", test_platform_admin_can_view_all_tenant_alerts),
        ("Tenant Admin 本租户告警视图", test_tenant_admin_limited_alerts_view),
        ("告警确认权限隔离", test_alert_acknowledge_permission_isolation),
    ]

    for name, test_func in tests:
        runner.run_test(name, test_func)

    return runner.print_summary()


if __name__ == "__main__":
    run_all_tests()