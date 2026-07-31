"""Tests for SAML ACS canonical URL hardening (Issue #1832 F7).

Covers the ``_build_acs_url`` helper used by the three SAML ACS call sites
(SP metadata, the SAML branch of ``start_login``, and the ``saml_acs``
callback). The ACS URL must be:

* identical across the three sites (an IdP/SP mismatch breaks SAML), and
* derivable from a configured ``sso.canonical_base_url`` instead of the
  request ``Host`` / ``X-Forwarded-*`` headers (Host-header hardening).

The OAuth callback (``sso.callback``) is intentionally NOT converged — its
redirect_uri stays request-derived — and these tests pin that boundary.
"""

import pytest
from flask import Flask

from app.routes.sso import _build_acs_url, sso_bp, start_login


def _make_app() -> Flask:
    app = Flask(__name__)
    app.register_blueprint(sso_bp)
    return app


class _FakeProvider:
    def __init__(self, provider_type: str) -> None:
        self.provider_type = provider_type


class _FakeManager:
    """Capture the callback_uri passed to start_authentication."""

    def __init__(self, provider_type: str, captured: dict) -> None:
        self._provider_type = provider_type
        self._captured = captured

    def get_provider(self, name: str) -> _FakeProvider:
        return _FakeProvider(self._provider_type)

    def start_authentication(self, name: str, callback_uri: str) -> dict:
        self._captured["callback_uri"] = callback_uri
        return {"authorization_url": "https://idp.example.com/authorize", "state": "st"}


def test_acs_url_falls_back_to_url_for_without_config(monkeypatch):
    """No canonical_base_url configured → behave exactly like the old url_for."""
    monkeypatch.setattr("app.routes.sso.get_config_value", lambda *a, **k: None)
    with _make_app().test_request_context(
        "/api/sso/acs/foo", base_url="https://public.example.com"
    ):
        url = _build_acs_url("foo")
    assert url == "https://public.example.com/api/sso/acs/foo"


def test_acs_url_uses_canonical_base_when_configured(monkeypatch):
    """Configured canonical base overrides a spoofed Host header."""
    monkeypatch.setattr(
        "app.routes.sso.get_config_value", lambda *a, **k: "https://canonical.example.com"
    )
    with _make_app().test_request_context("/api/sso/acs/foo", base_url="https://spoofed.evil"):
        url = _build_acs_url("foo")
    assert url == "https://canonical.example.com/api/sso/acs/foo"
    assert "spoofed" not in url


def test_acs_url_strips_trailing_slash_in_config(monkeypatch):
    monkeypatch.setattr(
        "app.routes.sso.get_config_value", lambda *a, **k: "https://canonical.example.com/"
    )
    with _make_app().test_request_context("/api/sso/acs/foo"):
        url = _build_acs_url("foo")
    assert url == "https://canonical.example.com/api/sso/acs/foo"


def test_acs_url_invalid_config_falls_back_and_logs(monkeypatch, caplog):
    """An invalid canonical_base_url must NOT break SSO — fall back + log error."""
    monkeypatch.setattr("app.routes.sso.get_config_value", lambda *a, **k: "not-a-url")
    with _make_app().test_request_context(
        "/api/sso/acs/foo", base_url="https://public.example.com"
    ):
        with caplog.at_level("ERROR"):
            url = _build_acs_url("foo")
    assert url == "https://public.example.com/api/sso/acs/foo"
    assert any("canonical_base_url" in rec.message for rec in caplog.records)


def test_acs_url_three_sites_are_identical(monkeypatch):
    """The metadata / start_login / saml_acs sites must share one ACS value."""
    monkeypatch.setattr(
        "app.routes.sso.get_config_value", lambda *a, **k: "https://canonical.example.com"
    )
    with _make_app().test_request_context("/api/sso/acs/foo"):
        urls = [_build_acs_url("foo") for _ in range(3)]
    assert len(set(urls)) == 1
    assert urls[0] == "https://canonical.example.com/api/sso/acs/foo"


def test_start_login_saml_branch_uses_canonical_acs(monkeypatch):
    """SAML providers route start_login's callback through the canonical helper."""
    monkeypatch.setattr(
        "app.routes.sso.get_config_value", lambda *a, **k: "https://canonical.example.com"
    )
    captured: dict = {}
    app = _make_app()
    with app.test_request_context(
        "/api/sso/login/foo?json=1", base_url="https://public.example.com"
    ):
        monkeypatch.setattr(
            "app.routes.sso.get_sso_manager", lambda: _FakeManager("saml", captured)
        )
        start_login("foo")
    assert captured["callback_uri"] == "https://canonical.example.com/api/sso/acs/foo"


def test_start_login_oauth_branch_is_not_converged(monkeypatch):
    """OAuth redirect_uri must stay Host-derived — the helper is SAML-only.

    This pins the code-level boundary (Issue #1832 F7): converging the OAuth
    branch would replace its redirect_uri with the SAML ACS URL and break
    OAuth login, since providers register redirect URIs out of band.
    """
    monkeypatch.setattr(
        "app.routes.sso.get_config_value", lambda *a, **k: "https://canonical.example.com"
    )
    captured: dict = {}
    app = _make_app()
    with app.test_request_context(
        "/api/sso/login/foo?json=1", base_url="https://public.example.com"
    ):
        monkeypatch.setattr(
            "app.routes.sso.get_sso_manager", lambda: _FakeManager("oauth", captured)
        )
        start_login("foo")
    assert captured["callback_uri"] == "https://public.example.com/api/sso/callback/foo"
    assert "canonical" not in captured["callback_uri"]
    assert "/acs/" not in captured["callback_uri"]


def test_oauth_callback_route_unaffected_by_canonical_config(monkeypatch):
    """The OAuth callback view (sso.callback) is not routed through the helper.

    Symmetric to start_login's OAuth branch: ``sso.callback`` calls
    ``url_for`` directly, so a configured canonical_base_url must not change
    the OAuth redirect_uri used to complete authentication.
    """
    monkeypatch.setattr(
        "app.routes.sso.get_config_value", lambda *a, **k: "https://canonical.example.com"
    )
    with _make_app().test_request_context(
        "/api/sso/callback/foo", base_url="https://public.example.com"
    ):
        from flask import url_for

        oauth_callback = url_for("sso.callback", provider_name="foo", _external=True)
    assert oauth_callback == "https://public.example.com/api/sso/callback/foo"
    assert "canonical" not in oauth_callback
