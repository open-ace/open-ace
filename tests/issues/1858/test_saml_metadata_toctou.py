"""Tests for SAML metadata URL SSRF / DNS-rebinding TOCTOU fix (Issue #1858).

These tests verify that the SAML metadata URL download path uses safe_request()
to pin the verified IP at connect time, closing the DNS rebinding TOCTOU window.
"""

import socket
from unittest.mock import Mock, patch, MagicMock

import pytest
import requests
from lxml import etree

from app.modules.sso.provider import SSOProviderConfig
from app.modules.sso.saml import SAMLProvider
from app.utils.outbound_url_guard import OutboundUrlBlockedError, _PinnedIPAdapter


# Test fixtures
SP_ENTITY_ID = "https://openace.example.com/saml/metadata"
ACS_URL = "https://openace.example.com/api/sso/acs/corp-saml"
IDP_ENTITY_ID = "https://idp.example.com/metadata"
IDP_SSO_URL = "https://example.com/sso"

VALID_METADATA_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<md:EntityDescriptor xmlns:md="urn:oasis:names:tc:SAML:2.0:metadata"
                     entityID="{IDP_ENTITY_ID}">
  <md:IDPSSODescriptor protocolSupportEnumeration="urn:oasis:names:tc:SAML:2.0:protocol">
    <md:SingleSignOnService Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect"
                            Location="{IDP_SSO_URL}"/>
  </md:IDPSSODescriptor>
</md:EntityDescriptor>
"""


class _RebindingResolver:
    """getaddrinfo that flips: first call returns a PUBLIC IP,
    later calls return metadata IP (169.254.169.254)."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, host, *args, **kwargs):
        self.calls += 1
        # First resolution (the guard): public IP -> passes _is_public_address.
        # Subsequent resolutions: metadata.
        ip = "93.184.216.34" if self.calls == 1 else "169.254.169.254"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443))]


def _create_provider_without_metadata_config(extra_params=None):
    """Helper to create a SAMLProvider instance that needs metadata from URL."""
    params = {}
    if extra_params:
        params.update(extra_params)
    # Don't set idp_entity_id or idp_sso_url, so they must come from metadata
    return SAMLProvider(
        SSOProviderConfig(
            name="test-saml",
            provider_type="saml",
            client_id=SP_ENTITY_ID,
            client_secret="",
            authorization_url="",  # Empty, must come from metadata
            token_url="",
            redirect_uri=ACS_URL,
            issuer_url="",
            extra_params=params,
        )
    )


def test_saml_metadata_url_pins_verified_ip(monkeypatch):
    """Verify that safe_request pins the verified IP and prevents DNS rebinding."""
    resolver = _RebindingResolver()
    captured = {}

    # Mock the pinned adapter's send to capture the request without network dial
    def fake_send(self, request, **kwargs):
        captured["url"] = request.url
        captured["headers"] = dict(request.headers)
        resp = requests.Response()
        resp.status_code = 200
        resp.url = request.url
        resp._content = VALID_METADATA_XML.encode("utf-8")
        return resp

    monkeypatch.setattr(_PinnedIPAdapter, "send", fake_send)

    # Create provider with metadata URL but without configured entity_id/sso_url
    provider = _create_provider_without_metadata_config(
        extra_params={"idp_metadata_url": "https://idp.example.com/metadata"}
    )

    # Inject resolver into safe_request call
    original_safe_request = __import__(
        "app.utils.outbound_url_guard", fromlist=["safe_request"]
    ).safe_request

    def mock_safe_request_with_resolver(method, url, **kwargs):
        # Inject our resolver
        return original_safe_request(method, url, resolver=resolver, **kwargs)

    monkeypatch.setattr("app.modules.sso.saml.safe_request", mock_safe_request_with_resolver)

    # Trigger metadata loading by accessing idp_entity_id
    entity_id = provider.idp_entity_id

    # Only ONE resolution happened — the rebinding second call was never made
    assert resolver.calls == 1, f"Expected 1 resolution, got {resolver.calls}"

    # The verified public IP is pinned into the URL
    outgoing = captured.get("url", "")
    assert "93.184.216.34" in outgoing, f"Public IP not pinned: {outgoing}"
    assert "169.254.169.254" not in outgoing, f"Metadata IP should not appear: {outgoing}"

    # The original hostname is preserved as Host header
    host_header = captured["headers"].get("Host")
    assert host_header == "idp.example.com", f"Host header not preserved: {host_header}"


def test_saml_metadata_url_rejects_metadata_ip(monkeypatch, caplog):
    """Verify that metadata IP addresses are rejected with graceful degradation."""
    from app.utils.outbound_url_guard import safe_request as real_safe_request

    def metadata_resolver(host, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 443))]

    # Mock safe_request to use our metadata resolver
    def mock_safe_request(method, url, **kwargs):
        return real_safe_request(method, url, resolver=metadata_resolver, **kwargs)

    monkeypatch.setattr("app.modules.sso.saml.safe_request", mock_safe_request)

    # Create provider with metadata URL but without configured values
    provider = _create_provider_without_metadata_config(
        extra_params={"idp_metadata_url": "https://idp.example.com/metadata"}
    )

    # Access property to trigger metadata loading
    entity_id = provider.idp_entity_id

    # Should gracefully degrade to empty string (since metadata loading failed)
    assert entity_id == "", f"Should be empty string: {entity_id}"

    # Check log contains warning
    assert any("Failed to load IdP metadata" in record.message for record in caplog.records)


def test_saml_metadata_xml_inline_skips_network_request(monkeypatch):
    """Verify that inline XML configuration does not trigger network requests."""
    # Mock safe_request to ensure it's never called
    mock_safe_request = Mock()
    monkeypatch.setattr("app.modules.sso.saml.safe_request", mock_safe_request)

    # Mock requests.get to ensure it's never called
    mock_requests_get = Mock()
    monkeypatch.setattr("requests.get", mock_requests_get)

    # Create provider with inline XML
    provider = _create_provider_without_metadata_config(
        extra_params={"idp_metadata_xml": VALID_METADATA_XML}
    )

    # Access property to trigger metadata loading
    entity_id = provider.idp_entity_id
    sso_url = provider.idp_sso_url

    # Verify metadata was loaded from inline XML
    assert entity_id == IDP_ENTITY_ID
    assert sso_url == IDP_SSO_URL

    # Verify no network requests were made
    mock_safe_request.assert_not_called()
    mock_requests_get.assert_not_called()


def test_saml_metadata_url_timeout_preserved(monkeypatch):
    """Verify that timeout and allow_redirects parameters are preserved."""
    captured = {}

    def fake_send(self, request, **kwargs):
        captured["send_called"] = True
        resp = requests.Response()
        resp.status_code = 200
        resp._content = VALID_METADATA_XML.encode("utf-8")
        return resp

    monkeypatch.setattr(_PinnedIPAdapter, "send", fake_send)

    # Mock safe_request to capture kwargs
    def mock_safe_request(method, url, **kwargs):
        captured["timeout"] = kwargs.get("timeout")
        captured["allow_redirects"] = kwargs.get("allow_redirects")
        # Return a minimal response
        resp = requests.Response()
        resp.status_code = 200
        resp._content = VALID_METADATA_XML.encode("utf-8")
        resp.url = url
        return resp

    monkeypatch.setattr("app.modules.sso.saml.safe_request", mock_safe_request)

    provider = _create_provider_without_metadata_config(
        extra_params={"idp_metadata_url": "https://idp.example.com/metadata"}
    )

    # Trigger metadata loading
    _ = provider.idp_entity_id

    # Verify parameters
    assert captured.get("timeout") == 10
    assert captured.get("allow_redirects") is False


def test_saml_metadata_load_failure_graceful_degradation(monkeypatch, caplog):
    """Verify graceful degradation when metadata URL fails to load."""
    from app.utils.outbound_url_guard import OutboundUrlBlockedError

    def mock_safe_request(method, url, **kwargs):
        # Simulate SSRF protection rejection
        raise OutboundUrlBlockedError("Blocked non-public address: 192.168.1.1")

    monkeypatch.setattr("app.modules.sso.saml.safe_request", mock_safe_request)

    provider = _create_provider_without_metadata_config(
        extra_params={"idp_metadata_url": "https://idp.example.com/metadata"}
    )

    # Access properties - should not raise exception
    entity_id = provider.idp_entity_id
    sso_url = provider.idp_sso_url

    # Should gracefully degrade to empty strings (since metadata loading failed)
    assert entity_id == ""
    assert sso_url == ""

    # Check log contains warning
    assert any("Failed to load IdP metadata" in record.message for record in caplog.records)


def test_saml_auth_flow_with_metadata_load_failure(monkeypatch):
    """Verify authentication flow fails clearly when metadata loading fails."""
    from app.utils.outbound_url_guard import OutboundUrlBlockedError

    def mock_safe_request(method, url, **kwargs):
        raise OutboundUrlBlockedError("Blocked non-public address: 169.254.169.254")

    monkeypatch.setattr("app.modules.sso.saml.safe_request", mock_safe_request)

    # Create provider WITHOUT configured idp_sso_url (must come from metadata)
    provider = _create_provider_without_metadata_config(
        extra_params={
            "idp_metadata_url": "https://idp.example.com/metadata",
        }
    )

    # Attempt to get authorization URL should raise clear error
    with pytest.raises(ValueError, match="SAML IdP SSO URL is required"):
        provider.get_authorization_url(state="test-state")


def test_saml_metadata_xml_parse_error_propagates(monkeypatch):
    """Verify XML parsing errors are not caught and continue to propagate."""
    invalid_xml = b"Not valid XML <broken>"

    # Mock safe_request to return invalid XML
    def mock_safe_request(method, url, **kwargs):
        resp = requests.Response()
        resp.status_code = 200
        resp._content = invalid_xml
        resp.url = url
        return resp

    monkeypatch.setattr("app.modules.sso.saml.safe_request", mock_safe_request)

    provider = _create_provider_without_metadata_config(
        extra_params={"idp_metadata_url": "https://idp.example.com/metadata"}
    )

    # XML parsing error should propagate
    with pytest.raises(etree.XMLSyntaxError):
        _ = provider.idp_entity_id


def test_saml_metadata_redirect_blocked_propagates(monkeypatch):
    """Verify redirect blocking errors are not caught and continue to propagate."""
    # Mock safe_request to return redirect response
    def mock_safe_request(method, url, **kwargs):
        resp = requests.Response()
        resp.status_code = 302
        resp.headers["Location"] = "https://evil.example.com/phishing"
        resp._content = b""
        resp.url = url
        return resp

    monkeypatch.setattr("app.modules.sso.saml.safe_request", mock_safe_request)

    provider = _create_provider_without_metadata_config(
        extra_params={"idp_metadata_url": "https://idp.example.com/metadata"}
    )

    # Redirect blocked error should propagate
    with pytest.raises(ValueError, match="metadata_redirect_blocked"):
        _ = provider.idp_entity_id