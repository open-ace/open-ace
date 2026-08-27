#!/usr/bin/env python3
"""Issue #1829 — F3 (route layer): source-driven tool_type classification.

The unmapped-accounts route classifies ``tool_type`` from the structured
``message_source`` first, with the Feishu ``ou_`` prefix and the openclaw-family
tool-name tokens kept only as fallbacks — replacing the old brittle
sender_name substring heuristic (``-dingtalk`` etc.).

These tests mock the repository and drive the Flask test client. The real-SQLite
repo half of F3 (correlated-subquery ``message_source`` resolution) lives in
tests/integration/test_tool_account_source_repo.py.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

pytestmark = [pytest.mark.regression, pytest.mark.issue(1829)]


@pytest.fixture
def app():
    from flask import Flask

    from app.routes.tool_accounts import tool_accounts_bp

    app = Flask(__name__)
    app.register_blueprint(tool_accounts_bp)
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test-secret-key"
    yield app


def _authed_get(client, path):
    with patch("app.auth.decorators._extract_session_token", return_value="t"):
        with patch(
            "app.auth.decorators._load_user_from_token",
            return_value={"id": 1, "role": "admin", "username": "admin"},
        ):
            return client.get(path)


class TestF3RouteSourceDrivenClassification:
    def test_dingtalk_via_message_source(self, app):
        from app.routes import tool_accounts as ta

        with patch.object(
            ta.tool_account_repo,
            "get_unmapped_tool_accounts",
            return_value=[
                {"sender_name": "manager123", "message_source": "dingtalk", "message_count": 5}
            ],
        ):
            resp = _authed_get(app.test_client(), "/tool-accounts/unmapped")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body[0]["tool_type"] == "dingtalk"

    def test_feishu_via_message_source(self, app):
        from app.routes import tool_accounts as ta

        with patch.object(
            ta.tool_account_repo,
            "get_unmapped_tool_accounts",
            return_value=[
                {"sender_name": "anything", "message_source": "feishu", "message_count": 1}
            ],
        ):
            resp = _authed_get(app.test_client(), "/tool-accounts/unmapped")
        assert resp.get_json()[0]["tool_type"] == "feishu"

    def test_feishu_ou_prefix_fallback_when_source_missing(self, app):
        """Rows whose message_source wasn't resolved still classify via the
        stable Feishu OpenAPI ou_ prefix."""
        from app.routes import tool_accounts as ta

        with patch.object(
            ta.tool_account_repo,
            "get_unmapped_tool_accounts",
            return_value=[{"sender_name": "ou_abc123", "message_source": None, "message_count": 1}],
        ):
            resp = _authed_get(app.test_client(), "/tool-accounts/unmapped")
        assert resp.get_json()[0]["tool_type"] == "feishu"

    def test_openclaw_family_token_fallback(self, app):
        """openclaw-family sub-tools share message_source='openclaw' and carry
        the sub-tool name in sender_name; the token is the only discriminator."""
        from app.routes import tool_accounts as ta

        with patch.object(
            ta.tool_account_repo,
            "get_unmapped_tool_accounts",
            return_value=[
                {"sender_name": "host-qwen", "message_source": "openclaw", "message_count": 1},
                {"sender_name": "host-claude", "message_source": "openclaw", "message_count": 1},
            ],
        ):
            resp = _authed_get(app.test_client(), "/tool-accounts/unmapped")
        types = {row["sender_name"]: row["tool_type"] for row in resp.get_json()}
        assert types["host-qwen"] == "qwen"
        assert types["host-claude"] == "claude"

    def test_no_dingtalk_substring_matching(self, app):
        """A dingtag sender_name WITHOUT a -dingtalk token must still classify
        as dingtalk via message_source — confirming the old substring heuristic
        is gone, not just augmented."""
        from app.routes import tool_accounts as ta

        with patch.object(
            ta.tool_account_repo,
            "get_unmapped_tool_accounts",
            return_value=[
                {
                    "sender_name": "plainuser-no-token-here",
                    "message_source": "dingtalk",
                    "message_count": 1,
                },
            ],
        ):
            resp = _authed_get(app.test_client(), "/tool-accounts/unmapped")
        assert resp.get_json()[0]["tool_type"] == "dingtalk"
