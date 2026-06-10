"""
Shared fixtures for tests/ui/ Playwright tests.

Provides:
- ui_screenshot_dir: tmp_path-based screenshot directory (avoids hardcoded paths and module-level side effects)
- Shared configuration constants accessible via fixture
- Playwright availability guard via pytest.importorskip
"""

import os

import pytest

# If playwright is not installed, skip collection of all UI tests in this
# directory.  This prevents ImportError / sys.exit(1) crashes at collection
# time and makes ``pytest tests/ui/`` safe to run on any environment.
pytest.importorskip("playwright", reason="playwright not installed, skipping UI tests")


@pytest.fixture(scope="function")
def ui_screenshot_dir(tmp_path):
    """
    Provide a temporary directory for saving screenshots.

    Uses pytest's built-in tmp_path fixture so each test run gets a clean
    directory under the system temp folder, avoiding:
    - Hardcoded paths like ``screenshots/issues/71/``
    - Module-level ``os.makedirs()`` side effects that can cause PermissionError
    """
    d = tmp_path / "screenshots"
    d.mkdir()
    return str(d)


@pytest.fixture(scope="session")
def ui_base_url():
    """Base URL for the application under test."""
    return os.environ.get("BASE_URL", "http://localhost:5001")


@pytest.fixture(scope="session")
def ui_credentials():
    """Default test credentials as a (username, password) tuple."""
    return (
        os.environ.get("TEST_USERNAME", "admin"),
        os.environ.get("TEST_PASSWORD", "admin123"),
    )


@pytest.fixture(scope="session")
def ui_headless():
    """Whether to run the browser in headless mode."""
    return os.environ.get("HEADLESS", "true").lower() == "true"
