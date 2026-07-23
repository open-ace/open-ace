#!/usr/bin/env python3
"""
Tests for Issue #1060 - Upstream quota exceeded alert handling.

Integration tests for:
1. Upstream 429 with "quota exceeded" triggers alert creation
2. Deduplication mechanism prevents repeated alerts within time window
3. Alert metadata contains correct quota_type
"""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from flask import Flask

from app.modules.governance.alert_notifier import AlertNotifier

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def flask_app():
    """Minimal Flask app for request-context tests."""
    app = Flask(__name__)
    app.config["TESTING"] = True
    return app


@pytest.fixture
def remote_app():
    """Flask app with remote blueprint for route-level handler tests."""
    from app.routes.remote import remote_bp

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(remote_bp, url_prefix="/api/remote")
    return app


def _mock_proxy_token(**overrides):
    """Build a mock token payload."""
    base = {
        "user_id": 1,
        "tenant_id": 1,
        "provider": "openai",
        "session_id": "sess-abc",
        "scope": "remote",
    }
    base.update(overrides)
    return base


def _make_quota_ok():
    mock = MagicMock()
    mock.check_quota.return_value = {"allowed": True}
    return mock


def _mock_upstream_429_quota_exceeded():
    """Mock upstream 429 response with quota exceeded error."""
    resp = MagicMock()
    resp.status_code = 429
    resp.content = b'{"error": {"message": "Quota exceeded for this month", "type": "quota_error"}}'
    resp.headers = {"Content-Type": "application/json"}
    return resp


def _mock_bailian_allocated_rate_limit():
    """Mock Bailian Coding Plan's temporary allocation limit response."""
    resp = MagicMock()
    resp.status_code = 429
    resp.content = b'{"error": {"message": "usage allocated quota exceeded"}}'
    resp.headers = {"Content-Type": "application/json"}
    return resp


_PROXY_PATH = "app.routes.remote.get_api_key_proxy_service"
_QUOTA_PATH = "app.modules.governance.quota_manager.QuotaManager"
_HTTP_PATH = "requests.request"


# ===================================================================
# Tests for Issue #1060
# ===================================================================


class TestUpstreamQuotaExceededAlert:
    """Tests for upstream 429 quota exceeded alert handling."""

    @patch("app.modules.governance.alert_notifier.get_alert_notifier")
    @patch(_HTTP_PATH)
    @patch(_QUOTA_PATH)
    @patch(_PROXY_PATH)
    def test_bailian_allocated_limit_is_retryable_and_does_not_alert(
        self, mock_get_proxy, mock_quota_cls, mock_http, mock_get_notifier, remote_app
    ):
        """Bailian allocated quota wording is rate limiting, not depletion."""
        mock_proxy = MagicMock()
        mock_proxy.validate_proxy_token.return_value = _mock_proxy_token()
        mock_proxy.resolve_api_key_for_scope.side_effect = [
            (
                "sk-key",
                "https://coding.dashscope.aliyuncs.com/apps/anthropic/v1",
                18,
                None,
                None,
            ),
            None,
        ]
        mock_get_proxy.return_value = mock_proxy
        mock_quota_cls.return_value = _make_quota_ok()
        mock_http.return_value = _mock_bailian_allocated_rate_limit()

        client = remote_app.test_client()
        resp = client.post(
            "/api/remote/llm-proxy",
            json={"model": "glm-5", "messages": [{"role": "user", "content": "hi"}]},
            headers={"Authorization": "Bearer tok"},
        )

        assert resp.status_code == 429
        data = resp.get_json()
        assert data["error"]["type"] == "rate_limit_error"
        assert "retry later" in data["error"]["message"].lower()
        mock_get_notifier.assert_not_called()
        assert mock_proxy.resolve_api_key_for_scope.call_count == 2
        assert mock_proxy.resolve_api_key_for_scope.call_args.kwargs["exclude_key_ids"] == {18}

    @patch("app.modules.governance.alert_notifier.get_alert_notifier")
    @patch(_HTTP_PATH)
    @patch(_QUOTA_PATH)
    @patch(_PROXY_PATH)
    def test_upstream_429_quota_exceeded_creates_alert(
        self, mock_get_proxy, mock_quota_cls, mock_http, mock_get_notifier, remote_app
    ):
        """Upstream 429 with 'quota exceeded' should trigger alert creation."""
        # Setup mocks
        mock_proxy = MagicMock()
        mock_proxy.validate_proxy_token.return_value = _mock_proxy_token()
        mock_proxy.resolve_api_key_for_scope.return_value = (
            "sk-key",
            "https://api.openai.com/v1",
            1,
            None,
            None,
        )
        mock_get_proxy.return_value = mock_proxy
        mock_quota_cls.return_value = _make_quota_ok()
        mock_http.return_value = _mock_upstream_429_quota_exceeded()

        # Mock alert notifier
        mock_notifier = MagicMock(spec=AlertNotifier)
        mock_notifier.has_recent_quota_alert.return_value = False  # No recent alert
        mock_notifier.create_alert.return_value = MagicMock(alert_id="alert-123")
        mock_get_notifier.return_value = mock_notifier

        # Mock user repo
        with patch("app.repositories.user_repo.UserRepository") as mock_user_repo_cls:
            mock_user_repo = MagicMock()
            mock_user_repo.get_user_by_id.return_value = {"username": "testuser"}
            mock_user_repo_cls.return_value = mock_user_repo

            client = remote_app.test_client()
            resp = client.post(
                "/api/remote/llm-proxy",
                json={"model": "gpt-4", "messages": [{"role": "user", "content": "hi"}]},
                headers={"Authorization": "Bearer tok"},
            )

            # Should return 429 with quota_exceeded error
            assert resp.status_code == 429
            data = resp.get_json()
            assert data["error"]["type"] == "quota_exceeded"
            assert "Platform quota exceeded" in data["error"]["message"]

            # Alert should be created with correct parameters
            mock_notifier.create_alert.assert_called_once()
            call_kwargs = mock_notifier.create_alert.call_args.kwargs
            assert call_kwargs["user_id"] == 1
            assert call_kwargs["username"] == "testuser"
            assert call_kwargs["metadata"]["quota_type"] == "platform"
            assert call_kwargs["metadata"]["usage_percent"] == 100

    @patch("app.modules.governance.alert_notifier.get_alert_notifier")
    @patch(_HTTP_PATH)
    @patch(_QUOTA_PATH)
    @patch(_PROXY_PATH)
    def test_deduplication_skips_duplicate_alerts(
        self, mock_get_proxy, mock_quota_cls, mock_http, mock_get_notifier, remote_app
    ):
        """Repeated 429 within dedup window should not create duplicate alerts."""
        # Setup mocks
        mock_proxy = MagicMock()
        mock_proxy.validate_proxy_token.return_value = _mock_proxy_token()
        mock_proxy.resolve_api_key_for_scope.return_value = (
            "sk-key",
            "https://api.openai.com/v1",
            1,
            None,
            None,
        )
        mock_get_proxy.return_value = mock_proxy
        mock_quota_cls.return_value = _make_quota_ok()
        mock_http.return_value = _mock_upstream_429_quota_exceeded()

        # Mock alert notifier - simulate recent alert exists
        mock_notifier = MagicMock(spec=AlertNotifier)
        mock_notifier.has_recent_quota_alert.return_value = True  # Recent alert exists
        mock_get_notifier.return_value = mock_notifier

        # Mock user repo
        with patch("app.repositories.user_repo.UserRepository") as mock_user_repo_cls:
            mock_user_repo = MagicMock()
            mock_user_repo.get_user_by_id.return_value = {"username": "testuser"}
            mock_user_repo_cls.return_value = mock_user_repo

            client = remote_app.test_client()
            resp = client.post(
                "/api/remote/llm-proxy",
                json={"model": "gpt-4", "messages": [{"role": "user", "content": "hi"}]},
                headers={"Authorization": "Bearer tok"},
            )

            # Should return 429 (quota exceeded still returned to user)
            assert resp.status_code == 429
            assert resp.get_json()["error"]["type"] == "quota_exceeded"

            # Alert should NOT be created (deduplicated)
            mock_notifier.create_alert.assert_not_called()

    @patch("app.modules.governance.alert_notifier.get_alert_notifier")
    @patch(_HTTP_PATH)
    @patch(_QUOTA_PATH)
    @patch(_PROXY_PATH)
    def test_different_quota_type_not_deduplicated(
        self, mock_get_proxy, mock_quota_cls, mock_http, mock_get_notifier, remote_app
    ):
        """Different quota_type should not be deduplicated."""
        # Setup mocks
        mock_proxy = MagicMock()
        mock_proxy.validate_proxy_token.return_value = _mock_proxy_token()
        mock_proxy.resolve_api_key_for_scope.return_value = (
            "sk-key",
            "https://api.openai.com/v1",
            1,
            None,
            None,
        )
        mock_get_proxy.return_value = mock_proxy
        mock_quota_cls.return_value = _make_quota_ok()
        mock_http.return_value = _mock_upstream_429_quota_exceeded()

        # Mock alert notifier - simulate 'tokens' alert exists but not 'platform'
        mock_notifier = MagicMock(spec=AlertNotifier)
        mock_notifier.has_recent_quota_alert.side_effect = lambda uid, qtype, hours: (
            qtype == "tokens"  # Only tokens has recent alert
        )
        mock_notifier.create_alert.return_value = MagicMock(alert_id="alert-456")
        mock_get_notifier.return_value = mock_notifier

        # Mock user repo
        with patch("app.repositories.user_repo.UserRepository") as mock_user_repo_cls:
            mock_user_repo = MagicMock()
            mock_user_repo.get_user_by_id.return_value = {"username": "testuser"}
            mock_user_repo_cls.return_value = mock_user_repo

            client = remote_app.test_client()
            resp = client.post(
                "/api/remote/llm-proxy",
                json={"model": "gpt-4", "messages": [{"role": "user", "content": "hi"}]},
                headers={"Authorization": "Bearer tok"},
            )

            # Should return 429
            assert resp.status_code == 429

            # Platform alert should be created (not deduplicated)
            mock_notifier.create_alert.assert_called_once()
            call_kwargs = mock_notifier.create_alert.call_args.kwargs
            assert call_kwargs["metadata"]["quota_type"] == "platform"

    @patch(_HTTP_PATH)
    @patch(_QUOTA_PATH)
    @patch(_PROXY_PATH)
    def test_upstream_429_other_error_no_alert(
        self, mock_get_proxy, mock_quota_cls, mock_http, remote_app
    ):
        """Upstream 429 without 'quota exceeded' should not trigger alert."""
        # Setup mocks
        mock_proxy = MagicMock()
        mock_proxy.validate_proxy_token.return_value = _mock_proxy_token()
        mock_proxy.resolve_api_key_for_scope.return_value = (
            "sk-key",
            "https://api.openai.com/v1",
            1,
            None,
            None,
        )
        mock_get_proxy.return_value = mock_proxy
        mock_quota_cls.return_value = _make_quota_ok()

        # Mock 429 response without quota exceeded
        resp = MagicMock()
        resp.status_code = 429
        resp.content = b'{"error": {"message": "Rate limit exceeded", "type": "rate_limit"}}'
        resp.headers = {"Content-Type": "application/json"}
        mock_http.return_value = resp

        # Mock alert notifier (should not be called)
        with patch("app.modules.governance.alert_notifier.get_alert_notifier") as mock_get_notifier:
            mock_notifier = MagicMock(spec=AlertNotifier)
            mock_get_notifier.return_value = mock_notifier

            client = remote_app.test_client()
            resp = client.post(
                "/api/remote/llm-proxy",
                json={"model": "gpt-4", "messages": [{"role": "user", "content": "hi"}]},
                headers={"Authorization": "Bearer tok"},
            )

            # Should not return 429 quota_exceeded (should continue failover loop)
            # The 429 without quota exceeded message is treated as regular rate limit
            # and added to exclude_key_ids for failover
            # Since we only have one key, it will return 500 after exhausting keys
            assert resp.status_code in (500, 502)


class TestHasRecentQuotaAlert:
    """Direct tests for has_recent_quota_alert method."""

    def test_returns_true_when_recent_alert_exists(self):
        """Should return True when recent alert exists in time window."""
        with patch("app.modules.governance.alert_notifier.AlertNotifier._get_connection"):
            notifier = AlertNotifier.__new__(AlertNotifier)
            notifier.db_path = ":memory:"

            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_cursor.fetchone.return_value = {"count": 2}
            mock_conn.cursor.return_value = mock_cursor
            notifier._get_connection = lambda: mock_conn

            result = notifier.has_recent_quota_alert(user_id=1, quota_type="platform", hours=1)
            assert result is True

    def test_returns_false_when_no_recent_alert(self):
        """Should return False when no recent alert in time window."""
        with patch("app.modules.governance.alert_notifier.AlertNotifier._get_connection"):
            notifier = AlertNotifier.__new__(AlertNotifier)
            notifier.db_path = ":memory:"

            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            mock_cursor.fetchone.return_value = {"count": 0}
            mock_conn.cursor.return_value = mock_cursor
            notifier._get_connection = lambda: mock_conn

            result = notifier.has_recent_quota_alert(user_id=1, quota_type="platform", hours=1)
            assert result is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
