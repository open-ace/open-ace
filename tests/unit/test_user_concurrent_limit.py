"""Unit tests for Issue #1329: max_sessions_per_user concurrent limit check.

Covers ``app.routes.autonomous._check_user_concurrent_limit`` — the admission
gate that rejects (429) workflow creation when a user already holds
``max_sessions_per_user`` active workflows.

Migrated from tests/issues/1329/test_concurrent_limit.py
(TestCheckUserConcurrentLimit). Repair vs. the legacy harness: the old
``flask_app`` fixture used ``create_app`` (whose ``ensure_all_tables``
bootstrap dialed the ambient ``~/.open-ace`` Postgres URL and ERRORED on
machines without a live server — batch-13 R4). Every collaborator of the
function under test is mocked anyway (``_get_repo``, ``user_repo``,
``TenantRepository``), so a bare ``Flask`` app providing ``app_context`` for
``jsonify`` is sufficient; the 429-at-limit assertions are unchanged.
"""

from unittest.mock import MagicMock, patch

import pytest
from flask import Flask

pytestmark = [pytest.mark.regression, pytest.mark.issue(1329)]


@pytest.fixture
def flask_app():
    """Bare Flask app: only an app_context for jsonify is needed (R4 repair).

    create_app is deliberately NOT used — its ensure_all_tables bootstrap
    reads the ambient DATABASE_URL/config.json, which red-on-main'd the
    legacy fixture when it pointed at an unreachable Postgres.
    """
    app = Flask(__name__)
    app.config["TESTING"] = True
    return app


class TestCheckUserConcurrentLimit:
    def test_returns_none_when_under_limit(self, flask_app):
        """Under the limit: no response object, the request proceeds."""
        from app.routes.autonomous import _check_user_concurrent_limit

        with flask_app.app_context():
            with patch("app.routes.autonomous._get_repo") as mock_get_repo:
                mock_repo = MagicMock()
                mock_repo.count_active_workflows_by_user.return_value = 1
                mock_get_repo.return_value = mock_repo
                with patch("app.routes.autonomous.user_repo") as mock_user_repo:
                    mock_user_repo.get_user_by_id.return_value = {"id": 1, "tenant_id": 1}
                    with patch(
                        "app.repositories.tenant_repo.TenantRepository"
                    ) as mock_tenant_repo_class:
                        mock_tenant_repo = MagicMock()
                        mock_tenant = MagicMock()
                        mock_tenant.quota.max_sessions_per_user = 3
                        mock_tenant_repo.get_by_id.return_value = mock_tenant
                        mock_tenant_repo_class.return_value = mock_tenant_repo
                        result = _check_user_concurrent_limit(user_id=1)
        assert result is None

    def test_returns_429_when_at_limit(self, flask_app):
        """Should return 429 Response when user has reached the limit."""
        from app.routes.autonomous import _check_user_concurrent_limit

        with flask_app.app_context():
            with patch("app.routes.autonomous._get_repo") as mock_get_repo:
                mock_repo = MagicMock()
                mock_repo.count_active_workflows_by_user.return_value = 3
                mock_get_repo.return_value = mock_repo
                with patch("app.routes.autonomous.user_repo") as mock_user_repo:
                    mock_user_repo.get_user_by_id.return_value = {"id": 1, "tenant_id": 1}
                    with patch(
                        "app.repositories.tenant_repo.TenantRepository"
                    ) as mock_tenant_repo_class:
                        mock_tenant_repo = MagicMock()
                        mock_tenant = MagicMock()
                        mock_tenant.quota.max_sessions_per_user = 3
                        mock_tenant_repo.get_by_id.return_value = mock_tenant
                        mock_tenant_repo_class.return_value = mock_tenant_repo
                        result = _check_user_concurrent_limit(user_id=1)
        assert result is not None
        assert result[1] == 429

    def test_uses_default_when_no_tenant(self, flask_app):
        """User without tenant: falls back to the default limit (no 429)."""
        from app.routes.autonomous import _check_user_concurrent_limit

        with flask_app.app_context():
            with patch("app.routes.autonomous._get_repo") as mock_get_repo:
                mock_repo = MagicMock()
                mock_repo.count_active_workflows_by_user.return_value = 3
                mock_get_repo.return_value = mock_repo
                with patch("app.routes.autonomous.user_repo") as mock_user_repo:
                    mock_user_repo.get_user_by_id.return_value = {"id": 1, "tenant_id": None}
                    result = _check_user_concurrent_limit(user_id=1)
        assert result is None

    def test_fail_open_on_exception(self, flask_app):
        """Repo failure must not lock users out: the check fails open (None)."""
        from app.routes.autonomous import _check_user_concurrent_limit

        with flask_app.app_context():
            with patch("app.routes.autonomous._get_repo") as mock_get_repo:
                mock_repo = MagicMock()
                mock_repo.count_active_workflows_by_user.side_effect = Exception("DB error")
                mock_get_repo.return_value = mock_repo
                with patch("app.routes.autonomous.user_repo") as mock_user_repo:
                    mock_user_repo.get_user_by_id.return_value = {"id": 1, "tenant_id": 1}
                    result = _check_user_concurrent_limit(user_id=1)
        assert result is None
