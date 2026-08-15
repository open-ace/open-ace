"""#2667: GET /api/autonomous/models must not mask infrastructure failures.

Regression for the route's exception handling: previously ANY failure inside
the try block — most notably ``APIKeyProxyService()`` construction raising
RuntimeError when OPENACE_ENCRYPTION_KEY is missing — was swallowed and
returned as ``{"success": True, "models": []}``. The frontend rendered the
misleading "no models configured, add an API key" hint for what was really a
server-side misconfiguration.

Contract locked here:
- a 200 response ALWAYS carries the ``empty_reason`` key (value may be None);
  a legitimate empty model list is distinguishable from a failure by that key;
- an exception returns 500 + ``success: false`` + a generic user-facing
  ``error`` string (no exception details leak to the client), matching the
  natural 500 that /api/api-keys produces for the same construction failure.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app import create_app

pytestmark = [pytest.mark.regression, pytest.mark.issue(2667)]


def _mock_auth(user_id=1, role="admin"):
    user = {
        "id": user_id,
        "username": "admin" if role == "admin" else "testuser",
        "email": f"{role}@test.com",
        "role": role,
        "tenant_id": None,
    }
    return patch("app.auth.decorators._load_user_from_token", return_value=user)


@pytest.fixture
def client():
    app = create_app({"TESTING": True})
    with app.app_context():
        c = app.test_client()
        c.set_cookie("session_token", "test-token")
        yield c


def _proxy_class_raising(exc: Exception):
    """Patch APIKeyProxyService so its CONSTRUCTOR raises (missing-key path)."""
    return patch(
        "app.modules.workspace.api_key_proxy.APIKeyProxyService",
        side_effect=exc,
    )


def _proxy_returning(pool: dict):
    """Patch APIKeyProxyService to a mock whose get_tool_models returns pool."""
    mock_proxy = MagicMock()
    mock_proxy.get_tool_models.return_value = pool
    return patch(
        "app.modules.workspace.api_key_proxy.APIKeyProxyService",
        return_value=mock_proxy,
    )


class TestFailurePaths:
    def test_constructor_runtime_error_returns_500_not_success(self, client):
        """Missing OPENACE_ENCRYPTION_KEY → constructor RuntimeError → 500.

        This is the exact #2667 repro: the service raises RuntimeError from
        get_encryption_key_material; the route must not disguise it as a
        successful empty model list.
        """
        with (
            _mock_auth(),
            _proxy_class_raising(
                RuntimeError(
                    "OPENACE_ENCRYPTION_KEY not set in development mode."
                )
            ),
        ):
            resp = client.get("/api/autonomous/models?tool=claude-code")
        assert resp.status_code == 500
        data = resp.get_json()
        assert data["success"] is False
        assert data["error"]
        # No masquerading empty list, no fabricated empty_reason.
        assert "models" not in data
        assert "empty_reason" not in data

    def test_query_runtime_error_returns_500(self, client):
        """A query-time failure (get_tool_models raising) is also a 500.

        Intentional widening vs the pre-#2667 "graceful degradation to an
        empty list": infrastructure errors are surfaced, consistent with
        /api/api-keys and the remote-lookup section of this same route.
        """
        mock_proxy = MagicMock()
        mock_proxy.get_tool_models.side_effect = RuntimeError("boom")
        with (
            _mock_auth(),
            patch(
                "app.modules.workspace.api_key_proxy.APIKeyProxyService",
                return_value=mock_proxy,
            ),
        ):
            resp = client.get("/api/autonomous/models?tool=claude-code")
        assert resp.status_code == 500
        data = resp.get_json()
        assert data["success"] is False
        assert "models" not in data

    def test_error_text_is_generic_no_exception_leak(self, client):
        """The user-facing error must not echo the raw exception message."""
        sensitive = "/home/user/secret-path/db.sqlite3"
        with (
            _mock_auth(),
            _proxy_class_raising(RuntimeError(f"cannot open {sensitive}")),
        ):
            resp = client.get("/api/autonomous/models?tool=claude-code")
        data = resp.get_json()
        assert resp.status_code == 500
        assert sensitive not in str(data.get("error"))
        assert "boom" not in str(data.get("error"))


class TestSuccessPaths:
    def test_no_keys_configured_returns_200_with_empty_reason(self, client):
        """Legitimate "no keys" empty result keeps 200 + empty_reason."""
        with (
            _mock_auth(),
            _proxy_returning(
                {
                    "models": [],
                    "empty_reason": (
                        "No active claude-code API keys configured "
                        "for scope 'local'"
                    ),
                }
            ),
        ):
            resp = client.get("/api/autonomous/models?tool=claude-code")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["models"] == []
        assert "No active" in data["empty_reason"]

    def test_models_present_returns_200_with_null_empty_reason(self, client):
        """Non-empty model list: 200, empty_reason key present (None value)."""
        with (
            _mock_auth(),
            _proxy_returning(
                {
                    "models": [
                        {"name": "claude-sonnet-4-6", "id": "claude-sonnet-4-6"}
                    ],
                    "empty_reason": None,
                }
            ),
        ):
            resp = client.get("/api/autonomous/models?tool=claude-code")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert [m["name"] for m in data["models"]] == ["claude-sonnet-4-6"]
        # Contract: the key exists even when there are models.
        assert "empty_reason" in data
        assert data["empty_reason"] is None

    def test_pool_missing_empty_reason_key_still_emits_key(self, client):
        """Defensive: even if get_tool_models omits empty_reason entirely,
        a 200 response must still carry the key (None) — the contract that
        distinguishes success from failure paths."""
        with (
            _mock_auth(),
            _proxy_returning({"models": [{"name": "m1", "id": "m1"}]}),
        ):
            resp = client.get("/api/autonomous/models?tool=claude-code")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert "empty_reason" in data
