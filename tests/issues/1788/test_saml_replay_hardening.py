"""TDD tests for SAML replay / lifetime / linking hardening (PR #1788 review findings).

Covers seven confirmed findings against app/modules/sso/saml.py and
app/routes/sso.py:

1. InResponseTo must be required when SP issued a request (high, replay).
2. Recipient / NotOnOrAfter on SubjectConfirmationData must be required (medium).
3. Conditions NotOnOrAfter must be required -> bounded assertion lifetime (medium).
4. Unauthenticated ACS must cap SAMLResponse size (medium, parse DoS).
5. email_verified must not be optimistic-True from a bare attribute (low).
6. Auto-provisioning must not link to an existing local account by email unless an
   admin opts in via allow_email_linking (medium, privilege escalation).
7. SAML login + logout must be audit-logged (medium, forensic gap).
"""

from __future__ import annotations

import base64
import re
from datetime import datetime, timedelta, timezone

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from lxml import etree
from signxml import XMLSigner, methods

from app.modules.sso.provider import SSOProviderConfig
from app.modules.sso.saml import (
    SAML_ASSERTION_NS,
    SAML_PROTOCOL_NS,
    SAML_SUCCESS_STATUS,
    SAMLProvider,
)

SP_ENTITY_ID = "https://openace.example.com/saml/metadata"
ACS_URL = "https://openace.example.com/api/sso/acs/corp-saml"
IDP_ENTITY_ID = "https://idp.example.com/metadata"
IDP_SSO_URL = "https://example.com/sso"


def _idp_key_and_cert():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name(
        [
            x509.NameAttribute(NameOID.COUNTRY_NAME, "US"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Example IdP"),
            x509.NameAttribute(NameOID.COMMON_NAME, "idp.example.com"),
        ]
    )
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(timezone.utc) - timedelta(days=1))
        .not_valid_after(datetime.now(timezone.utc) + timedelta(days=30))
        .sign(key, hashes.SHA256())
    )
    key_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    cert_pem = cert.public_bytes(serialization.Encoding.PEM)
    cert_body = re.sub(
        r"-----BEGIN CERTIFICATE-----|-----END CERTIFICATE-----|\s+",
        "",
        cert_pem.decode("utf-8"),
    )
    return key_pem, cert_pem, cert_body


def _provider(cert_body: str, extra_params: dict | None = None) -> SAMLProvider:
    params = {
        "idp_entity_id": IDP_ENTITY_ID,
        "idp_x509_cert": cert_body,
        "clock_skew_seconds": 0,
    }
    if extra_params:
        params.update(extra_params)
    return SAMLProvider(
        SSOProviderConfig(
            name="corp-saml",
            provider_type="saml",
            client_id=SP_ENTITY_ID,
            client_secret="",
            authorization_url=IDP_SSO_URL,
            token_url="",
            redirect_uri=ACS_URL,
            issuer_url=IDP_ENTITY_ID,
            extra_params=params,
        )
    )


def _saml_time(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _build_signed_response(
    key_pem: bytes,
    cert_pem: bytes,
    *,
    name_id: str = "user-123",
    email: str | None = "alice@example.com",
    audience: str = SP_ENTITY_ID,
    recipient: str | None = ACS_URL,
    response_in_response_to: str | None = "_request-1",
    confirmation_in_response_to: str | None = "_request-1",
    confirmation_not_on_or_after: datetime | None = "_default",
    conditions_not_before: datetime | None = "_default",
    conditions_not_on_or_after: datetime | None = "_default",
) -> str:
    """Build a signed SAMLResponse with fine-grained control over each finding lever.

    The ``"_default"`` sentinel produces a valid, current time window so callers can
    pass ``None`` to explicitly omit an attribute and exercise the optional-attribute
    bugs under review.
    """
    now = datetime.now(timezone.utc)
    if confirmation_not_on_or_after == "_default":
        confirmation_not_on_or_after = now + timedelta(minutes=5)
    if conditions_not_before == "_default":
        conditions_not_before = now - timedelta(minutes=1)
    if conditions_not_on_or_after == "_default":
        conditions_not_on_or_after = now + timedelta(minutes=5)
    response_id = "_response-1"
    assertion_id = "_assertion-1"
    response_attrs = {
        "ID": response_id,
        "Version": "2.0",
        "IssueInstant": _saml_time(now),
        "Destination": recipient or ACS_URL,
    }
    if response_in_response_to is not None:
        response_attrs["InResponseTo"] = response_in_response_to
    response = etree.Element(
        f"{{{SAML_PROTOCOL_NS}}}Response",
        nsmap={"samlp": SAML_PROTOCOL_NS, "saml": SAML_ASSERTION_NS},
        **response_attrs,
    )
    etree.SubElement(response, f"{{{SAML_ASSERTION_NS}}}Issuer").text = IDP_ENTITY_ID
    status = etree.SubElement(response, f"{{{SAML_PROTOCOL_NS}}}Status")
    etree.SubElement(status, f"{{{SAML_PROTOCOL_NS}}}StatusCode", Value=SAML_SUCCESS_STATUS)

    assertion = etree.SubElement(
        response,
        f"{{{SAML_ASSERTION_NS}}}Assertion",
        ID=assertion_id,
        Version="2.0",
        IssueInstant=_saml_time(now),
    )
    etree.SubElement(assertion, f"{{{SAML_ASSERTION_NS}}}Issuer").text = IDP_ENTITY_ID
    subject = etree.SubElement(assertion, f"{{{SAML_ASSERTION_NS}}}Subject")
    etree.SubElement(subject, f"{{{SAML_ASSERTION_NS}}}NameID").text = name_id
    confirmation = etree.SubElement(subject, f"{{{SAML_ASSERTION_NS}}}SubjectConfirmation")
    confirmation_data_attrs: dict[str, str] = {}
    if recipient is not None:
        confirmation_data_attrs["Recipient"] = recipient
    if confirmation_in_response_to is not None:
        confirmation_data_attrs["InResponseTo"] = confirmation_in_response_to
    if confirmation_not_on_or_after is not None:
        confirmation_data_attrs["NotOnOrAfter"] = _saml_time(confirmation_not_on_or_after)
    etree.SubElement(
        confirmation,
        f"{{{SAML_ASSERTION_NS}}}SubjectConfirmationData",
        **confirmation_data_attrs,
    )
    conditions_attrs: dict[str, str] = {}
    if conditions_not_before is not None:
        conditions_attrs["NotBefore"] = _saml_time(conditions_not_before)
    if conditions_not_on_or_after is not None:
        conditions_attrs["NotOnOrAfter"] = _saml_time(conditions_not_on_or_after)
    conditions = etree.SubElement(
        assertion,
        f"{{{SAML_ASSERTION_NS}}}Conditions",
        **conditions_attrs,
    )
    audience_restriction = etree.SubElement(
        conditions,
        f"{{{SAML_ASSERTION_NS}}}AudienceRestriction",
    )
    etree.SubElement(audience_restriction, f"{{{SAML_ASSERTION_NS}}}Audience").text = audience
    attr_statement = etree.SubElement(assertion, f"{{{SAML_ASSERTION_NS}}}AttributeStatement")
    if email is not None:
        email_attr = etree.SubElement(
            attr_statement, f"{{{SAML_ASSERTION_NS}}}Attribute", Name="email"
        )
        etree.SubElement(email_attr, f"{{{SAML_ASSERTION_NS}}}AttributeValue").text = email
    name_attr = etree.SubElement(
        attr_statement, f"{{{SAML_ASSERTION_NS}}}Attribute", Name="displayName"
    )
    etree.SubElement(name_attr, f"{{{SAML_ASSERTION_NS}}}AttributeValue").text = "Alice Example"

    signed_assertion = XMLSigner(
        method=methods.enveloped,
        signature_algorithm="rsa-sha256",
        digest_algorithm="sha256",
    ).sign(assertion, key=key_pem, cert=cert_pem, reference_uri=assertion_id)
    response.remove(assertion)
    response.append(signed_assertion)

    return base64.b64encode(etree.tostring(response, xml_declaration=False)).decode("ascii")


# ---------------------------------------------------------------------------
# Finding 1: InResponseTo required when SP issued a request (high replay).
# ---------------------------------------------------------------------------


def test_inresponseto_required_on_response_when_request_id_known():
    """A response omitting InResponseTo must be rejected when SP issued a request."""
    key_pem, _cert_pem, cert_body = _idp_key_and_cert()
    provider = _provider(cert_body)
    # SP issued a request -> request_id is known; attacker omits InResponseTo entirely.
    response = _build_signed_response(key_pem, _cert_pem, response_in_response_to=None)
    result = provider.authenticate_saml_response(response, request_id="_request-1", acs_url=ACS_URL)
    assert not result.success, "response with no InResponseTo must be rejected (replay)"
    assert result.error == "missing_in_response_to"


def test_inresponseto_required_on_confirmation_when_request_id_known():
    """A confirmation omitting InResponseTo must be rejected when SP issued a request."""
    key_pem, _cert_pem, cert_body = _idp_key_and_cert()
    provider = _provider(cert_body)
    response = _build_signed_response(
        key_pem,
        _cert_pem,
        response_in_response_to="_request-1",
        confirmation_in_response_to=None,
    )
    result = provider.authenticate_saml_response(response, request_id="_request-1", acs_url=ACS_URL)
    assert not result.success, "confirmation with no InResponseTo must be rejected (replay)"


# ---------------------------------------------------------------------------
# Finding 2: Recipient / NotOnOrAfter required on SubjectConfirmationData.
# ---------------------------------------------------------------------------


def test_recipient_required_on_subject_confirmation():
    """A SubjectConfirmationData missing Recipient must be rejected."""
    key_pem, _cert_pem, cert_body = _idp_key_and_cert()
    provider = _provider(cert_body)
    response = _build_signed_response(key_pem, _cert_pem, recipient=None)
    result = provider.authenticate_saml_response(response, request_id="_request-1", acs_url=ACS_URL)
    assert not result.success
    assert result.error == "invalid_recipient"


def test_not_on_or_after_required_on_subject_confirmation():
    """A SubjectConfirmationData missing NotOnOrAfter must be rejected (unbounded replay)."""
    key_pem, _cert_pem, cert_body = _idp_key_and_cert()
    provider = _provider(cert_body)
    response = _build_signed_response(
        key_pem,
        _cert_pem,
        confirmation_not_on_or_after=None,
    )
    result = provider.authenticate_saml_response(response, request_id="_request-1", acs_url=ACS_URL)
    assert not result.success
    assert result.error == "invalid_recipient"


# ---------------------------------------------------------------------------
# Finding 3: Conditions NotOnOrAfter required (bounded assertion lifetime).
# ---------------------------------------------------------------------------


def test_conditions_not_on_or_after_required():
    """An assertion omitting Conditions/NotOnOrAfter must be rejected (unbounded replay)."""
    key_pem, _cert_pem, cert_body = _idp_key_and_cert()
    provider = _provider(cert_body)
    response = _build_signed_response(
        key_pem,
        _cert_pem,
        conditions_not_on_or_after=None,
    )
    result = provider.authenticate_saml_response(response, request_id="_request-1", acs_url=ACS_URL)
    assert not result.success
    assert result.error == "missing_conditions_lifetime"


# ---------------------------------------------------------------------------
# Finding 5: email_verified semantics (low).
# ---------------------------------------------------------------------------


def test_email_verified_defaults_to_false_without_idp_attestation():
    """An IdP-asserted email without verification must not mark email_verified True."""
    key_pem, _cert_pem, cert_body = _idp_key_and_cert()
    provider = _provider(cert_body)
    response = _build_signed_response(key_pem, _cert_pem, email="alice@example.com")
    result = provider.authenticate_saml_response(response, request_id="_request-1", acs_url=ACS_URL)
    assert result.success
    assert result.user is not None
    assert result.user.email_verified is False


def test_email_verified_true_when_idp_asserts_verification():
    """An IdP that explicitly asserts email verification may set email_verified True."""
    key_pem, _cert_pem, cert_body = _idp_key_and_cert()
    provider = _provider(cert_body)
    response = _build_signed_response_with_verified_email(key_pem, _cert_pem, verified=True)
    result = provider.authenticate_saml_response(response, request_id="_request-1", acs_url=ACS_URL)
    assert result.success
    assert result.user is not None
    assert result.user.email_verified is True


def _build_signed_response_with_verified_email(
    key_pem: bytes, cert_pem: bytes, *, verified: bool
) -> str:
    now = datetime.now(timezone.utc)
    response_id = "_response-1"
    assertion_id = "_assertion-1"
    response = etree.Element(
        f"{{{SAML_PROTOCOL_NS}}}Response",
        nsmap={"samlp": SAML_PROTOCOL_NS, "saml": SAML_ASSERTION_NS},
        ID=response_id,
        Version="2.0",
        IssueInstant=_saml_time(now),
        Destination=ACS_URL,
        InResponseTo="_request-1",
    )
    etree.SubElement(response, f"{{{SAML_ASSERTION_NS}}}Issuer").text = IDP_ENTITY_ID
    status = etree.SubElement(response, f"{{{SAML_PROTOCOL_NS}}}Status")
    etree.SubElement(status, f"{{{SAML_PROTOCOL_NS}}}StatusCode", Value=SAML_SUCCESS_STATUS)
    assertion = etree.SubElement(
        response,
        f"{{{SAML_ASSERTION_NS}}}Assertion",
        ID=assertion_id,
        Version="2.0",
        IssueInstant=_saml_time(now),
    )
    etree.SubElement(assertion, f"{{{SAML_ASSERTION_NS}}}Issuer").text = IDP_ENTITY_ID
    subject = etree.SubElement(assertion, f"{{{SAML_ASSERTION_NS}}}Subject")
    etree.SubElement(subject, f"{{{SAML_ASSERTION_NS}}}NameID").text = "user-123"
    confirmation = etree.SubElement(subject, f"{{{SAML_ASSERTION_NS}}}SubjectConfirmation")
    etree.SubElement(
        confirmation,
        f"{{{SAML_ASSERTION_NS}}}SubjectConfirmationData",
        Recipient=ACS_URL,
        InResponseTo="_request-1",
        NotOnOrAfter=_saml_time(now + timedelta(minutes=5)),
    )
    conditions = etree.SubElement(
        assertion,
        f"{{{SAML_ASSERTION_NS}}}Conditions",
        NotBefore=_saml_time(now - timedelta(minutes=1)),
        NotOnOrAfter=_saml_time(now + timedelta(minutes=5)),
    )
    ar = etree.SubElement(conditions, f"{{{SAML_ASSERTION_NS}}}AudienceRestriction")
    etree.SubElement(ar, f"{{{SAML_ASSERTION_NS}}}Audience").text = SP_ENTITY_ID
    attr_statement = etree.SubElement(assertion, f"{{{SAML_ASSERTION_NS}}}AttributeStatement")
    email_attr = etree.SubElement(attr_statement, f"{{{SAML_ASSERTION_NS}}}Attribute", Name="email")
    etree.SubElement(email_attr, f"{{{SAML_ASSERTION_NS}}}AttributeValue").text = (
        "alice@example.com"
    )
    verified_attr = etree.SubElement(
        attr_statement, f"{{{SAML_ASSERTION_NS}}}Attribute", Name="email_verified"
    )
    etree.SubElement(verified_attr, f"{{{SAML_ASSERTION_NS}}}AttributeValue").text = (
        "true" if verified else "false"
    )
    signed_assertion = XMLSigner(
        method=methods.enveloped,
        signature_algorithm="rsa-sha256",
        digest_algorithm="sha256",
    ).sign(assertion, key=key_pem, cert=cert_pem, reference_uri=assertion_id)
    response.remove(assertion)
    response.append(signed_assertion)
    return base64.b64encode(etree.tostring(response, xml_declaration=False)).decode("ascii")


# ---------------------------------------------------------------------------
# Finding 4: ACS response-size cap (medium, parse DoS).
# ---------------------------------------------------------------------------


def test_app_has_max_content_length_configured():
    """The Flask app must set MAX_CONTENT_LENGTH to cap unauthenticated POST bodies."""
    from app import create_app

    app = create_app()
    assert app.config.get("MAX_CONTENT_LENGTH"), "MAX_CONTENT_LENGTH must be configured"
    # A SAMLResponse is well under 1MB; cap should be generous but bounded.
    assert app.config["MAX_CONTENT_LENGTH"] <= 2 * 1024 * 1024


def test_oversized_post_body_rejected_with_413():
    """Flask must reject a POST body exceeding MAX_CONTENT_LENGTH with 413 before
    the ACS handler parses it. This is the mechanism that protects /acs."""
    from flask import Flask, request

    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 256 * 1024

    @app.route("/acs", methods=["POST"])
    def acs():  # pragma: no cover - should never run; 413 fires first
        # Reading the form triggers Werkzeug's MAX_CONTENT_LENGTH enforcement,
        # exactly as the real ACS handler does via request.form.get("SAMLResponse").
        request.form.get("SAMLResponse")
        return "reachable"

    client = app.test_client()
    oversized = b"SAMLResponse=" + b"A" * (256 * 1024 + 1024)
    resp = client.post(
        "/acs",
        data=oversized,
        content_type="application/x-www-form-urlencoded",
    )
    assert resp.status_code == 413


# ---------------------------------------------------------------------------
# Findings 6 & 7: email-linking gate + audit logging (route-level).
# These are exercised against the SSO route helpers; see the route test module.
# ---------------------------------------------------------------------------
