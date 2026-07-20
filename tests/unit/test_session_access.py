"""Tests for the shared remote-user loading helpers in session_access.

Locks in the auth path the remote and run-timeline blueprints now share:
session-token success, the WebUI-URL-token fallback, the OPTIONS passthrough,
and the 401 when no credential is present. These guard against the two
blueprints' auth silently diverging again.
"""

from unittest.mock import patch

import pytest
from flask import Flask, g

from app.modules.workspace.session_access import _set_user_from_token, load_remote_user


@pytest.fixture
def app():
    return Flask(__name__)


class TestSetUserFromToken:
    def test_sets_g_when_token_valid(self, app):
        user = {"id": 5, "role": "user", "username": "bob"}
        with app.test_request_context("/api/remote/x"):
            with (
                patch(
                    "app.modules.workspace.session_access._extract_session_token",
                    return_value="tok",
                ),
                patch(
                    "app.modules.workspace.session_access._load_user_from_token",
                    return_value=user,
                ),
            ):
                assert _set_user_from_token() is True
            assert g.user["id"] == 5
            assert g.user_role == "user"

    def test_returns_false_when_no_token(self, app):
        with app.test_request_context("/api/remote/x"):
            with patch("app.modules.workspace.session_access._extract_session_token", return_value=""):
                assert _set_user_from_token() is False


class TestLoadRemoteUser:
    def test_token_success_returns_none(self, app):
        with app.test_request_context("/api/remote/x"):
            with patch(
                "app.modules.workspace.session_access._set_user_from_token",
                return_value=True,
            ):
                assert load_remote_user() is None

    def test_webui_fallback_then_401(self, app):
        with app.test_request_context("/api/remote/x"):
            with (
                patch(
                    "app.modules.workspace.session_access._set_user_from_token",
                    return_value=False,
                ),
                patch(
                    "app.modules.workspace.session_access._set_user_from_webui_token",
                    return_value=False,
                ),
            ):
                result = load_remote_user()
            assert result is not None
            assert result[1] == 401  # (response, status_code)

    def test_webui_token_authenticates(self, app):
        with app.test_request_context("/api/remote/x?token=abc"):
            with (
                patch(
                    "app.modules.workspace.session_access._set_user_from_token",
                    return_value=False,
                ),
                patch(
                    "app.modules.workspace.session_access._set_user_from_webui_token",
                    return_value=True,
                ),
            ):
                assert load_remote_user() is None

    def test_options_request_is_unauthenticated_passthrough(self, app):
        with app.test_request_context("/api/remote/x", method="OPTIONS"):
            assert load_remote_user() is None
