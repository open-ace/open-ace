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
