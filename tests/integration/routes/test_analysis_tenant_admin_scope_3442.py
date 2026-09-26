"""``tenant_admin`` must be tenant-scoped on /api/analysis/* (Issue #3442).

``analysis.py`` decided scope with ``User.is_admin_role``, whose
``ADMIN_ROLES`` includes ``tenant_admin``. A tenant admin was therefore handed
``tenant_id=None`` -- which the repository layer reads as "no tenant filter"
-- and saw analysis data aggregated across every tenant.

These tests drive every analysis endpoint through the real blueprint (auth +
tenant gate) and record the ``tenant_id`` each one hands to the service.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from flask import Flask

pytestmark = [pytest.mark.regression, pytest.mark.issue(3442), pytest.mark.security]

_Q = "?start=2026-09-01&end=2026-09-20"
ENDPOINTS = [
    f"/api/analysis/batch{_Q}",
    f"/api/analysis/key-metrics{_Q}",
    f"/api/analysis/hourly-usage{_Q}",
    f"/api/analysis/daily-hourly-usage{_Q}",
    f"/api/analysis/peak-usage{_Q}",
    f"/api/analysis/user-ranking{_Q}",
    f"/api/analysis/conversation-stats{_Q}",
    f"/api/analysis/user-segmentation{_Q}",
    f"/api/analysis/user-role-distribution{_Q}",
    f"/api/analysis/tool-comparison{_Q}",
    f"/api/analysis/anomaly-detection{_Q}",
    f"/api/analysis/anomaly-trend{_Q}",
    "/api/analysis/data-range",
    f"/api/analysis/recommendations{_Q}",
]

PLATFORM_ADMIN = {"id": 1, "username": "pa", "role": "platform_admin", "tenant_id": None}
TENANT_ADMIN = {"id": 20, "username": "ta", "role": "tenant_admin", "tenant_id": 2}
TENANT_USER = {"id": 10, "username": "u", "role": "user", "tenant_id": 1}
NO_TENANT_ADMIN = {"id": 21, "username": "ta0", "role": "tenant_admin", "tenant_id": None}


class _RecordingService:
    """Stands in for AnalysisService; records the tenant_id of every call."""

    def __init__(self):
        self.tenant_ids: list = []

    def __getattr__(self, name):
        def _call(*args, **kwargs):
            self.tenant_ids.append(kwargs.get("tenant_id", "<missing>"))
            return {}

        return _call


@pytest.fixture
def client_and_service():
    from app.routes.analysis import analysis_bp

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test-secret-key"
    app.register_blueprint(analysis_bp, url_prefix="/api")

    service = _RecordingService()
    with patch("app.routes.analysis.analysis_service", service):
        client = app.test_client()
        client.set_cookie("session_token", "test-token")
        yield client, service


def _get(client, path, user):
    with patch("app.auth.decorators._load_user_from_token", return_value=dict(user)):
        return client.get(path)


@pytest.mark.parametrize("path", ENDPOINTS)
@pytest.mark.parametrize(
    ("user", "expected_tenant_id"),
    [(TENANT_ADMIN, 2), (TENANT_USER, 1), (PLATFORM_ADMIN, None)],
    ids=["tenant_admin", "tenant_user", "platform_admin"],
)
def test_endpoint_passes_callers_scope(client_and_service, path, user, expected_tenant_id):
    client, service = client_and_service
    resp = _get(client, path, user)
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert service.tenant_ids, "endpoint did not reach the service"
    assert set(service.tenant_ids) == {expected_tenant_id}


@pytest.mark.parametrize("path", ENDPOINTS)
def test_tenant_admin_without_tenant_is_denied(client_and_service, path):
    client, service = client_and_service
    resp = _get(client, path, NO_TENANT_ADMIN)
    assert resp.status_code == 403
    assert service.tenant_ids == []
