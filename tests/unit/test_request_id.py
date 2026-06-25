"""Tests for the X-Request-ID correlation middleware.

These exercise the before/after-request hooks added in register_error_handlers
without bootstrapping the full application factory, so they stay fast and
isolated from the database/background-service startup.
"""

from __future__ import annotations

import pytest
from flask import Flask

from app import register_error_handlers


@pytest.fixture
def app():
    """A minimal Flask app wired only with the error/middleware handlers."""
    flask_app = Flask(__name__)
    register_error_handlers(flask_app)
    return flask_app


def test_request_id_generated_when_absent(app):
    """No incoming X-Request-ID -> server generates one and echoes it."""
    client = app.test_client()
    resp = client.get("/does-not-exist")
    assert "X-Request-ID" in resp.headers
    assert resp.headers["X-Request-ID"] != ""


def test_request_id_echoed_when_provided(app):
    """An incoming X-Request-ID is preserved on the response verbatim."""
    client = app.test_client()
    resp = client.get("/does-not-exist", headers={"X-Request-ID": "abc-123"})
    assert resp.headers["X-Request-ID"] == "abc-123"


def test_request_id_unique_per_request(app):
    """Each request without an inbound id gets a distinct generated id."""
    client = app.test_client()
    first = client.get("/").headers["X-Request-ID"]
    second = client.get("/").headers["X-Request-ID"]
    assert first != second


def test_request_id_control_chars_stripped(app):
    """Non-newline control chars (NUL/US/DEL/tab) are stripped before echo.

    Werkzeug itself rejects CR/LF in header values at the WSGI boundary, so we
    can't push a true CRLF through the test client; the remaining C0/DEL set
    reaches our handler and must be stripped to keep the id safe to log.
    """
    from app import _sanitize_request_id

    client = app.test_client()
    poisoned = "abc\x00de\x1f\x7f\tfg"
    resp = client.get("/does-not-exist", headers={"X-Request-ID": poisoned})
    assert resp.headers["X-Request-ID"] == "abcdefg"
    # Defense-in-depth: the helper itself also neutralizes CR/LF even though the
    # framework rejects them upstream, so a raw-socket smuggle can't inject.
    assert _sanitize_request_id("evil\r\nX-Forged: header") == "evilX-Forged: header"


def test_sanitize_request_id_unit():
    """Authoritative coverage of the pure sanitizer (incl. CRLF the client rejects)."""
    from app import REQUEST_ID_MAX_LENGTH, _sanitize_request_id

    assert _sanitize_request_id(None) == ""
    assert _sanitize_request_id("") == ""
    assert _sanitize_request_id("abc-123") == "abc-123"
    # All C0 control chars + DEL removed.
    assert _sanitize_request_id("a\x00b\x01c\x1fd\x7fe") == "abcde"
    # CR/LF smuggle defeated.
    assert "\r" not in _sanitize_request_id("x\r\ny") and "\n" not in _sanitize_request_id("x\r\ny")
    # Whitespace trimmed, length capped.
    assert _sanitize_request_id("  ab  ") == "ab"
    assert len(_sanitize_request_id("z" * (REQUEST_ID_MAX_LENGTH + 50))) == REQUEST_ID_MAX_LENGTH


def test_request_id_truncated_to_max_length(app):
    """An over-long inbound id is capped to REQUEST_ID_MAX_LENGTH."""
    from app import REQUEST_ID_MAX_LENGTH

    client = app.test_client()
    too_long = "a" * (REQUEST_ID_MAX_LENGTH + 500)
    resp = client.get("/does-not-exist", headers={"X-Request-ID": too_long})
    echoed = resp.headers["X-Request-ID"]
    assert len(echoed) == REQUEST_ID_MAX_LENGTH
    assert echoed == "a" * REQUEST_ID_MAX_LENGTH


def test_request_id_falls_back_when_only_control_chars(app):
    """An inbound id that sanitizes to empty yields a fresh generated id."""
    client = app.test_client()
    resp = client.get("/does-not-exist", headers={"X-Request-ID": "\x00\x1f\x7f"})
    echoed = resp.headers["X-Request-ID"]
    assert echoed != ""
    assert all(c in "0123456789abcdef" for c in echoed)
    assert len(echoed) == 32  # generated uuid4 hex
