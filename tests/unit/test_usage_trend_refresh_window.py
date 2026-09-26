"""Issue #3424: the dashboard trend API must not rebuild all of daily_stats.

GET /api/trend runs on every dashboard load. When daily_stats looked stale it
used to call ``refresh_stats()`` with no bounds, re-aggregating every date of
daily_messages (73 s on a real install) before the dashboard could render.
"""

from unittest.mock import patch

import pytest
from flask import Flask

pytestmark = [pytest.mark.regression, pytest.mark.issue(3424)]

_ADMIN = {
    "id": 1,
    "role": "platform_admin",
    "tenant_id": 1,
    "username": "platform_admin",
    "email": "admin@example.com",
}


def _get_trend(needs_refresh: bool, start_date: str | None):
    from app.routes.usage import usage_bp

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test-secret-key"
    app.register_blueprint(usage_bp, url_prefix="/api")

    with (
        patch("app.auth.decorators._load_user_from_token", return_value=_ADMIN),
        patch("app.repositories.daily_stats_repo.DailyStatsRepository") as repo_cls,
        patch("app.routes.usage.usage_service") as usage_service,
    ):
        repo = repo_cls.return_value
        repo.needs_refresh.return_value = needs_refresh
        repo.get_refresh_start_date.return_value = start_date
        usage_service.get_trend_data.return_value = []

        response = app.test_client().get(
            "/api/trend?start=2026-08-27&end=2026-09-26",
            headers={"Authorization": "Bearer test-token"},
        )
    assert response.status_code == 200
    return repo


def test_stale_trend_refreshes_only_from_latest_aggregated_date():
    repo = _get_trend(needs_refresh=True, start_date="2026-09-22")
    repo.refresh_stats.assert_called_once_with(since="2026-09-22")


def test_empty_stats_fall_back_to_full_refresh():
    repo = _get_trend(needs_refresh=True, start_date=None)
    repo.refresh_stats.assert_called_once_with(since=None)


def test_fresh_trend_does_not_refresh():
    repo = _get_trend(needs_refresh=False, start_date="2026-09-22")
    repo.refresh_stats.assert_not_called()
