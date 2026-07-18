#!/usr/bin/env python3
"""
Regression tests for PR #1808 round-2 review.

Severe finding #1: the global ``MAX_CONTENT_LENGTH = 256KB`` set in
``app/__init__.py`` was applied Flask-app-wide, which means Werkzeug rejects
*any* authenticated upload request larger than 256KB with 413 *before* the
view runs. That is a functional regression for existing endpoints
(``/api/upload/messages``, ``/api/upload/batch``, avatar upload, remote
proxy bodies).

The fix narrows the cap to the unauthenticated SAML ``/acs`` endpoint only
and removes the global limit. These tests pin both sides:

* a reasonable batch upload (> 256KB) must NOT be 413'd;
* the unauthenticated ``/acs`` endpoint must still reject oversized SAML
  payloads with 413.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

project_root = str(Path(__file__).resolve().parent.parent.parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)


@pytest.fixture
def app(monkeypatch):
    # A strong, non-placeholder key so require_upload_auth lets the request in.
    monkeypatch.setenv("UPLOAD_AUTH_KEY", "upload-auth-key-123")
    from app import create_app

    app = create_app()
    app.config["TESTING"] = True
    return app


@pytest.fixture
def client(app):
    return app.test_client()


def test_no_global_max_content_length_regression():
    """The real app must NOT impose an app-wide MAX_CONTENT_LENGTH.

    A global cap would 413 existing authenticated upload endpoints (avatar,
    /api/upload/messages, /api/upload/batch, remote proxy bodies) that
    legitimately carry >256KB payloads. The SAML /acs DoS cap must be scoped
    to that one route instead.
    """
    from app import create_app

    app = create_app()
    assert not app.config.get("MAX_CONTENT_LENGTH"), (
        "MAX_CONTENT_LENGTH must not be set app-wide; it regresses authenticated "
        "upload endpoints. Scope the size cap to the /acs route only."
    )


def test_acs_still_rejects_oversized_saml_response(client):
    """The SAML /acs endpoint must still reject oversized SAMLResponse bodies.

    With the global cap removed, /acs now enforces its own per-route size
    check and returns 413 for a payload above the SAML ceiling.
    """
    oversized = b"SAMLResponse=" + b"A" * (256 * 1024 + 1024)
    resp = client.post(
        "/api/sso/acs/corp-saml",
        data=oversized,
        content_type="application/x-www-form-urlencoded",
    )
    assert resp.status_code == 413


def test_acs_allows_normal_sized_saml_response(client):
    """A normal-sized SAMLResponse must reach the handler (not be 413'd).

    A tiny payload should pass the per-route size check. The handler then
    rejects it for missing/invalid SAML content (400/redirect), proving the
    size cap itself does not false-positive on legitimate traffic.
    """
    # A small base64-ish SAMLResponse-shaped payload well under the 256KB cap.
    tiny = b"SAMLResponse=PHNhbWxwOkRlc3RpbmF0aW9uUmVzcG9uc2UgeG1sbnM9InNhbWxwIg=="
    resp = client.post(
        "/api/sso/acs/corp-saml",
        data=tiny,
        content_type="application/x-www-form-urlencoded",
    )
    # Must NOT be 413 (size gate passed). Anything else (400/302) means the
    # handler ran and rejected on content grounds -- exactly what we want.
    assert resp.status_code != 413


def test_upload_batch_above_256kb_not_blocked(client):
    """A reasonable /api/upload/batch payload > 256KB must succeed (regression).

    Before the fix the global 256KB cap 413'd this. After the fix the per-route
    /acs cap does not touch /api/upload/batch, so an authenticated batch upload
    of several hundred KB goes through.
    """
    # Build a batch payload comfortably above 256KB.
    big_blob = "x" * (300 * 1024)
    payload = {
        "usage": [
            {
                "date": "2026-07-17",
                "tool_name": "codex",
                "tokens_used": 1,
                "notes": big_blob,
            }
        ],
        "messages": [],
    }

    with patch("app.routes.upload.usage_service.save_usage", return_value=True):
        resp = client.post(
            "/api/upload/batch",
            headers={"X-Upload-Auth": "upload-auth-key-123"},
            json=payload,
        )

    assert (
        resp.status_code != 413
    ), "/api/upload/batch must not be subject to the SAML /acs size cap"
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["success"] is True
