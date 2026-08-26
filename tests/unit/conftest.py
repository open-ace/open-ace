"""
Pytest configuration for tests/unit.

Add shared fixtures and hooks here.
"""

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _isolated_unit_db(request, tmp_path, monkeypatch):
    """Give every unit test its own throwaway SQLite database (#2869).

    The full-suite CI lane runs many test files in one pytest process that all
    share a workspace-level ``app.db``. Any test that goes through
    ``create_app`` triggers ``ensure_all_tables`` -> ``load_schema_from_file``,
    which replays the authoritative schema onto that shared DB. ``CREATE TABLE
    IF NOT EXISTS`` does NOT reconcile an already-existing table's columns, so
    if an earlier test left a table whose shape doesn't match the snapshot, a
    later ``CREATE INDEX`` referencing the missing column raises
    ``sqlite3.OperationalError: no such column`` — and an *unrelated* test's
    setup crashes at random (``test_tenant_keywords_api`` was the first
    casualty; the next could be any ``create_app``-using file).

    Pointing ``DATABASE_URL`` at a fresh per-test path removes the shared state
    entirely. It also makes the test DB choice EXPLICIT instead of silently
    inheriting the developer's ambient ``DATABASE_URL`` (which, when it points
    at Postgres, makes the same repositories fail with PG syntax errors — a
    different false signal). ``@pytest.mark.postgres`` tests are exempt: they
    intentionally use the ambient ``DATABASE_URL`` (the CI Postgres service).
    """
    if request.node.get_closest_marker("postgres"):
        return
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/unit-test.db")


@pytest.fixture
def _enable_acceptance_verification():
    """Run acceptance-verifier tests with the feature flag forced on.

    The flag defaults to on (``app/utils/config.py``), but an ambient config
    (e.g. a developer's ~/.open-ace/config.json) can disable it; the #2335
    regression files opt in via ``pytest.mark.usefixtures`` so the tests stay
    independent of the host configuration. Migrated from the retired
    ``tests/issues/2335/conftest.py`` autouse fixture (#2429 batch 2).
    """
    with patch(
        "app.modules.workspace.autonomous.phases.acceptance_verification."
        "is_acceptance_verification_enabled",
        return_value=True,
    ):
        yield


@pytest.fixture
def app(_isolated_unit_db):
    """Create Flask app for testing.

    Depends on ``_isolated_unit_db`` (also autouse) so the isolated
    ``DATABASE_URL`` is in place before ``create_app`` bootstraps the schema.
    """
    from app import create_app

    app = create_app({"TESTING": True, "SECRET_KEY": "test-secret-key"})
    return app


@pytest.fixture
def client(app):
    """Create test client without authentication."""
    with app.app_context():
        yield app.test_client()


@pytest.fixture
def admin_client(app):
    """Create authenticated admin test client."""
    # Mock authenticated admin user
    admin_user = {
        "id": 1,
        "username": "admin",
        "email": "admin@test.com",
        "role": "admin",
        "tenant_id": None,  # Platform admin
    }

    with patch("app.auth.decorators._load_user_from_token", return_value=admin_user):
        with app.app_context():
            client = app.test_client()
            client.set_cookie("session_token", "test-admin-token")
            yield client


@pytest.fixture
def mock_governance_repo():
    """Mock governance repository for testing.

    This fixture patches the governance_repo instance used by routes.
    """
    mock_repo = MagicMock()

    # Patch the module-level instance in routes.governance
    with patch("app.routes.governance.governance_repo", mock_repo):
        yield mock_repo
