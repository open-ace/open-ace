from __future__ import annotations

import base64
import re
import urllib.parse
import zlib
from datetime import datetime, timedelta, timezone

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from lxml import etree
from signxml import XMLSigner, methods

from app.modules.sso.provider import SSOProviderConfig
from app.modules.sso.saml import (
    SAML_ASSERTION_NS,
    SAML_POST_BINDING,
    SAML_PROTOCOL_NS,
    SAML_REDIRECT_BINDING,
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


def _signed_response(
    key_pem: bytes,
    cert_pem: bytes,
    *,
    name_id: str = "user-123",
    email: str | None = "alice@example.com",
    audience: str = SP_ENTITY_ID,
    recipient: str = ACS_URL,
    in_response_to: str = "_request-1",
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
        Destination=recipient,
        InResponseTo=in_response_to,
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
    etree.SubElement(
        confirmation,
        f"{{{SAML_ASSERTION_NS}}}SubjectConfirmationData",
        Recipient=recipient,
        InResponseTo=in_response_to,
        NotOnOrAfter=_saml_time(now + timedelta(minutes=5)),
    )
    conditions = etree.SubElement(
        assertion,
        f"{{{SAML_ASSERTION_NS}}}Conditions",
        NotBefore=_saml_time(now - timedelta(minutes=1)),
        NotOnOrAfter=_saml_time(now + timedelta(minutes=5)),
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


def test_saml_authn_request_and_metadata():
    _key, _cert, cert_body = _idp_key_and_cert()
    provider = _provider(cert_body)

    url = provider.get_authorization_url("relay-state-1", ACS_URL)

    parsed = urllib.parse.urlparse(url)
    query = urllib.parse.parse_qs(parsed.query)
    assert parsed.scheme == "https"
    assert query["RelayState"] == ["relay-state-1"]
    assert provider.last_request_id

    request_xml = zlib.decompress(base64.b64decode(query["SAMLRequest"][0]), -15)
    root = etree.fromstring(request_xml)
    assert root.tag.endswith("AuthnRequest")
    assert root.get("AssertionConsumerServiceURL") == ACS_URL
    assert root.get("ProtocolBinding") == SAML_POST_BINDING

    metadata = provider.get_service_provider_metadata()
    assert SP_ENTITY_ID in metadata
    assert SAML_POST_BINDING in metadata


def test_saml_response_successfully_authenticates_user():
    key_pem, cert_pem, cert_body = _idp_key_and_cert()
    provider = _provider(cert_body)
    saml_response = _signed_response(key_pem, cert_pem)

    result = provider.authenticate_saml_response(saml_response, "_request-1", ACS_URL)

    assert result.success is True
    assert result.user is not None
    assert result.user.provider_user_id == "user-123"
    assert result.user.email == "alice@example.com"
    assert result.user.name == "Alice Example"
    assert result.token is not None
    assert result.token.token_type == "SAML"


def test_saml_response_rejects_invalid_signature():
    key_pem, cert_pem, cert_body = _idp_key_and_cert()
    provider = _provider(cert_body)
    raw_response = base64.b64decode(_signed_response(key_pem, cert_pem))
    root = etree.fromstring(raw_response)
    root.find(".//{urn:oasis:names:tc:SAML:2.0:assertion}AttributeValue").text = (
        "tampered@example.com"
    )
    tampered_response = base64.b64encode(etree.tostring(root)).decode("ascii")

    result = provider.authenticate_saml_response(tampered_response, "_request-1", ACS_URL)

    assert result.success is False
    assert result.error == "invalid_signature"


def test_saml_response_rejects_wrong_audience():
    key_pem, cert_pem, cert_body = _idp_key_and_cert()
    provider = _provider(cert_body)
    saml_response = _signed_response(key_pem, cert_pem, audience="https://other.example.com/sp")

    result = provider.authenticate_saml_response(saml_response, "_request-1", ACS_URL)

    assert result.success is False
    assert result.error == "invalid_audience"


def test_saml_response_rejects_wrong_recipient():
    key_pem, cert_pem, cert_body = _idp_key_and_cert()
    provider = _provider(cert_body)
    saml_response = _signed_response(
        key_pem,
        cert_pem,
        recipient="https://openace.example.com/api/sso/acs/other",
    )

    result = provider.authenticate_saml_response(saml_response, "_request-1", ACS_URL)

    assert result.success is False
    assert result.error == "invalid_recipient"


def test_saml_response_rejects_missing_required_email_attribute():
    key_pem, cert_pem, cert_body = _idp_key_and_cert()
    provider = _provider(cert_body)
    saml_response = _signed_response(key_pem, cert_pem, name_id="opaque-user-id", email=None)

    result = provider.authenticate_saml_response(saml_response, "_request-1", ACS_URL)

    assert result.success is False
    assert result.error == "missing_attribute"
