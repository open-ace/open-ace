"""Metadata shared by the migrated browser regression suite."""

import logging
import uuid

import pytest
import requests

from tests.e2e.browser.test_helpers import BASE_URL

logger = logging.getLogger(__name__)


def pytest_collection_modifyitems(items):
    """Preserve regression provenance after removing the purpose-based directory."""
    for item in items:
        item.add_marker(pytest.mark.regression)


# Issue #2114: SSO test data fixture for E2E browser tests
@pytest.fixture
def sso_test_data(request, tmp_path):  # noqa: ARG001
    """
    Create isolated SSO test data for E2E browser tests.

    This fixture creates a test tenant and GitHub provider via API calls,
    ensuring test isolation for SSO Settings page validation.

    Yields:
        dict: Test data containing tenant_id, provider_name, expected_state

    Note:
        Cleanup is performed via API calls after test completion.
        Uses unique UUID-suffixed tenant names to avoid conflicts.
    """
    # Generate unique tenant name
    test_uuid = uuid.uuid4().hex[:8]
    tenant_name = f"sso-test-{test_uuid}"

    # Admin credentials from test_helpers
    admin_username = "admin"
    admin_password = "admin123"  # Default test password  # noqa: S105

    # Create admin session
    session = requests.Session()
    login_resp = session.post(
        f"{BASE_URL}/api/auth/login",
        json={"username": admin_username, "password": admin_password},
        timeout=30,
    )

    if not login_resp.ok:
        # Skip test if admin login fails (test environment not ready)
        pytest.skip(f"Admin login failed: {login_resp.status_code}")

    tenant_id = None
    provider_registered = False

    try:
        # Create test tenant
        tenant_resp = session.post(
            f"{BASE_URL}/api/tenants",
            json={
                "name": tenant_name,
                "slug": tenant_name,
                "status": "active",
            },
            timeout=10,
        )

        if tenant_resp.ok:
            tenant_data = tenant_resp.json()
            tenant_id = tenant_data.get("id")

        # Register GitHub provider for the tenant (if tenant created)
        if tenant_id:
            provider_resp = session.post(
                f"{BASE_URL}/api/sso/providers",
                json={
                    "name": "github",
                    "provider_type": "oauth2",
                    "client_id": f"test-client-{test_uuid}",
                    "client_secret": f"test-secret-{test_uuid}",  # noqa: S106
                    "predefined": True,
                    "tenant_id": tenant_id,
                },
                timeout=10,
            )
            provider_registered = provider_resp.ok

        # Get system-level SSO setting
        settings_resp = session.get(f"{BASE_URL}/api/system/settings", timeout=10)
        sso_enabled = False
        if settings_resp.ok:
            sso_enabled = settings_resp.json().get("sso_enabled", False)

        yield {
            "tenant_id": tenant_id,
            "provider_name": "github",
            "expected_state": {
                "sso_enabled": sso_enabled,
                "is_enabled": True,
            },
            "session": session,
        }

    finally:
        # Cleanup: Disable and delete provider, delete tenant
        if tenant_id:
            try:
                if provider_registered:
                    # Disable provider first
                    session.patch(
                        f"{BASE_URL}/api/sso/providers/github/disable",
                        timeout=10,
                    )

                # Delete tenant
                session.delete(
                    f"{BASE_URL}/api/tenants/{tenant_id}",
                    timeout=10,
                )
            except Exception as e:  # noqa: BLE001
                # Log cleanup errors but don't fail the test
                logger.warning(f"Cleanup failed for tenant {tenant_id}: {e}")


@pytest.fixture
def sso_test_admin_page(page, sso_test_data):
    """
    Create a logged-in admin page with SSO test data.

    This fixture combines the sso_test_data fixture with a Playwright page
    that is already logged in as admin and navigated to SSO Settings.

    Args:
        page: Playwright page fixture
        sso_test_data: SSO test data fixture

    Yields:
        tuple: (page, test_data) where test_data contains tenant and provider info
    """
    from tests.e2e.browser.test_helpers import login, navigate_to

    # Login as admin
    login(page)

    # Navigate to SSO settings
    navigate_to(page, "/manage/settings/sso")

    # Wait for page to load
    page.wait_for_timeout(2000)

    yield page, sso_test_data
