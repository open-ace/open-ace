"""SAML 2.0 Service Provider support for Open ACE SSO."""

from __future__ import annotations

import base64
import hashlib
import logging
import secrets
import urllib.parse
import zlib
from datetime import datetime, timedelta, timezone
from typing import Any, cast

import requests
from lxml import etree
from signxml import SignatureConfiguration, XMLVerifier

from app.modules.sso.provider import (
    SSOAuthResult,
    SSOProvider,
    SSOProviderConfig,
    SSOToken,
    SSOUser,
)
from app.utils.outbound_url_guard import assert_public_http_url

logger = logging.getLogger(__name__)

SAML_PROTOCOL_NS = "urn:oasis:names:tc:SAML:2.0:protocol"
SAML_ASSERTION_NS = "urn:oasis:names:tc:SAML:2.0:assertion"
XMLDSIG_NS = "http://www.w3.org/2000/09/xmldsig#"
SAML_METADATA_NS = "urn:oasis:names:tc:SAML:2.0:metadata"
SAML_REDIRECT_BINDING = "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect"
SAML_POST_BINDING = "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST"
SAML_PASSWORD_CONTEXT = "urn:oasis:names:tc:SAML:2.0:ac:classes:PasswordProtectedTransport"
SAML_SUCCESS_STATUS = "urn:oasis:names:tc:SAML:2.0:status:Success"
DEFAULT_NAMEID_FORMAT = "urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress"
DEFAULT_CLOCK_SKEW_SECONDS = 180
DEFAULT_SESSION_SECONDS = 3600
SAML_PLACEHOLDER_TOKEN_PREFIX = "saml:"

NSMAP = {
    "samlp": SAML_PROTOCOL_NS,
    "saml": SAML_ASSERTION_NS,
    "ds": XMLDSIG_NS,
    "md": SAML_METADATA_NS,
}

DEFAULT_ATTRIBUTE_MAPPING = {
    "email": [
        "email",
        "mail",
        "Email",
        "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress",
    ],
    "username": [
        "username",
        "preferred_username",
        "uid",
        "UserName",
        "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/name",
    ],
    "name": ["name", "displayName", "cn"],
    "first_name": [
        "givenName",
        "first_name",
        "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/givenname",
    ],
    "last_name": [
        "sn",
        "surname",
        "last_name",
        "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/surname",
    ],
}


class SAMLProvider(SSOProvider):
    """SAML 2.0 Service Provider implementation."""

    def __init__(self, config: SSOProviderConfig):
        super().__init__(config)
        self.last_request_id: str | None = None
        self._metadata_root: etree._Element | None = None

    @property
    def sp_entity_id(self) -> str:
        """Return the configured SP entity ID."""
        return str(
            self.config.extra_params.get("sp_entity_id")
            or self.config.client_id
            or self.config.name
        )

    @property
    def acs_url(self) -> str:
        """Return the Assertion Consumer Service URL."""
        return str(self.config.extra_params.get("acs_url") or self.config.redirect_uri or "")

    @property
    def idp_entity_id(self) -> str:
        """Return the expected IdP entity ID."""
        configured = self.config.extra_params.get("idp_entity_id") or self.config.issuer_url
        if configured:
            return str(configured)
        metadata = self._load_idp_metadata()
        return str(metadata.get("entityID") or "") if metadata is not None else ""

    @property
    def idp_sso_url(self) -> str:
        """Return the IdP SSO redirect URL."""
        configured = self.config.extra_params.get("idp_sso_url") or self.config.authorization_url
        if configured:
            return str(configured)
        metadata = self._load_idp_metadata()
        if metadata is None:
            return ""
        services = metadata.findall(
            ".//md:IDPSSODescriptor/md:SingleSignOnService",
            namespaces=NSMAP,
        )
        redirect_service = next(
            (service for service in services if service.get("Binding") == SAML_REDIRECT_BINDING),
            None,
        )
        service = (
            redirect_service
            if redirect_service is not None
            else (services[0] if services else None)
        )
        return str(service.get("Location") or "") if service is not None else ""

    @property
    def nameid_format(self) -> str:
        """Return the requested NameID format."""
        return str(self.config.extra_params.get("nameid_format") or DEFAULT_NAMEID_FORMAT)

    def get_authorization_url(
        self,
        state: str,
        redirect_uri: str | None = None,
        code_challenge: str | None = None,
        nonce: str | None = None,
    ) -> str:
        """Build an HTTP-Redirect binding AuthnRequest URL."""
        del code_challenge, nonce

        sso_url = self.idp_sso_url
        acs_url = redirect_uri or self.acs_url
        if not sso_url:
            raise ValueError("SAML IdP SSO URL is required")
        if not acs_url:
            raise ValueError("SAML ACS URL is required")

        assert_public_http_url(sso_url)
        request_id = self.generate_request_id()
        self.last_request_id = request_id
        request_xml = self._build_authn_request(request_id=request_id, acs_url=acs_url)
        encoded_request = self._deflate_and_base64(request_xml)

        params = {
            "SAMLRequest": encoded_request,
            "RelayState": state,
        }
        separator = "&" if urllib.parse.urlparse(sso_url).query else "?"
        return f"{sso_url}{separator}{urllib.parse.urlencode(params)}"

    def exchange_code(
        self, code: str, redirect_uri: str | None = None, code_verifier: str | None = None
    ) -> SSOAuthResult:
        """SAML does not use OAuth authorization codes."""
        return SSOAuthResult(success=False, error="unsupported_flow")

    def get_user_info(self, access_token: str) -> SSOUser | None:
        """SAML user information is read from the assertion during ACS processing."""
        return None

    def refresh_token(self, refresh_token: str) -> SSOToken | None:
        """SAML sessions do not expose refresh tokens."""
        return None

    def authenticate_saml_response(
        self,
        saml_response: str,
        request_id: str | None,
        acs_url: str | None = None,
    ) -> SSOAuthResult:
        """Validate a Base64-encoded SAMLResponse and return the asserted user."""
        try:
            response_xml = base64.b64decode(saml_response, validate=True)
        except Exception as e:
            return SSOAuthResult(
                success=False,
                error="invalid_saml_response",
                error_description=f"SAMLResponse is not valid Base64: {e}",
            )

        try:
            root = self._parse_xml(response_xml)
            self._verify_signature(root)
            assertion = self._select_assertion(root)
            effective_acs_url = acs_url or self.acs_url
            self._validate_response(root, assertion, request_id, effective_acs_url)
            user, expires_in = self._build_user(assertion, response_xml)
        except ValueError as e:
            return SSOAuthResult(success=False, error=str(e))
        except Exception as e:
            logger.exception("Unexpected SAML validation error")
            return SSOAuthResult(
                success=False,
                error="saml_validation_error",
                error_description=str(e),
            )

        return SSOAuthResult(
            success=True,
            user=user,
            token=SSOToken(
                access_token=self._build_session_token(response_xml),
                token_type="SAML",
                expires_in=expires_in,
            ),
        )

    def get_service_provider_metadata(self, acs_url: str | None = None) -> str:
        """Return SP metadata XML for configuring an enterprise IdP."""
        location = acs_url or self.acs_url
        entity_descriptor = etree.Element(
            "{urn:oasis:names:tc:SAML:2.0:metadata}EntityDescriptor",
            nsmap={
                "md": "urn:oasis:names:tc:SAML:2.0:metadata",
                "saml": SAML_ASSERTION_NS,
            },
            entityID=self.sp_entity_id,
        )
        sp_sso = etree.SubElement(
            entity_descriptor,
            "{urn:oasis:names:tc:SAML:2.0:metadata}SPSSODescriptor",
            AuthnRequestsSigned="false",
            WantAssertionsSigned="true",
            protocolSupportEnumeration=SAML_PROTOCOL_NS,
        )
        etree.SubElement(
            sp_sso,
            "{urn:oasis:names:tc:SAML:2.0:metadata}NameIDFormat",
        ).text = self.nameid_format
        etree.SubElement(
            sp_sso,
            "{urn:oasis:names:tc:SAML:2.0:metadata}AssertionConsumerService",
            Binding=SAML_POST_BINDING,
            Location=location,
            index="0",
            isDefault="true",
        )
        return cast(
            "str",
            etree.tostring(
                entity_descriptor,
                xml_declaration=True,
                encoding="UTF-8",
                pretty_print=True,
            ).decode("utf-8"),
        )

    def _build_authn_request(self, request_id: str, acs_url: str) -> bytes:
        now = self._now().isoformat(timespec="seconds").replace("+00:00", "Z")
        root = etree.Element(
            f"{{{SAML_PROTOCOL_NS}}}AuthnRequest",
            nsmap=NSMAP,
            ID=request_id,
            Version="2.0",
            IssueInstant=now,
            Destination=self.idp_sso_url,
            ProtocolBinding=SAML_POST_BINDING,
            AssertionConsumerServiceURL=acs_url,
        )
        etree.SubElement(root, f"{{{SAML_ASSERTION_NS}}}Issuer").text = self.sp_entity_id
        etree.SubElement(
            root,
            f"{{{SAML_PROTOCOL_NS}}}NameIDPolicy",
            Format=self.nameid_format,
            AllowCreate="true",
        )
        requested_context = etree.SubElement(
            root,
            f"{{{SAML_PROTOCOL_NS}}}RequestedAuthnContext",
            Comparison="minimum",
        )
        etree.SubElement(
            requested_context,
            f"{{{SAML_ASSERTION_NS}}}AuthnContextClassRef",
        ).text = SAML_PASSWORD_CONTEXT
        return cast("bytes", etree.tostring(root, xml_declaration=False, encoding="UTF-8"))

    def _validate_response(
        self,
        root: etree._Element,
        assertion: etree._Element,
        request_id: str | None,
        acs_url: str,
    ) -> None:
        status = root.find(".//samlp:StatusCode", namespaces=NSMAP)
        if status is None or status.get("Value") != SAML_SUCCESS_STATUS:
            raise ValueError("saml_status_not_success")

        destination = root.get("Destination")
        if destination and acs_url and destination != acs_url:
            raise ValueError("invalid_recipient")

        if request_id:
            response_in_response_to = root.get("InResponseTo")
            if response_in_response_to and response_in_response_to != request_id:
                raise ValueError("invalid_in_response_to")

        self._validate_issuer(root, assertion)
        self._validate_conditions(assertion, acs_url)
        self._validate_subject_confirmation(assertion, request_id, acs_url)

    def _verify_signature(self, root: etree._Element) -> None:
        certs = self._configured_certificates()
        if not certs:
            raise ValueError("missing_idp_certificate")

        candidates = [root] + root.findall(".//saml:Assertion", namespaces=NSMAP)
        last_error: Exception | None = None
        expect_config = SignatureConfiguration(require_x509=False, expect_references=1)
        for candidate in candidates:
            if candidate.find(".//ds:Signature", namespaces=NSMAP) is None:
                continue
            for cert in certs:
                try:
                    XMLVerifier().verify(
                        candidate,
                        x509_cert=cert,
                        id_attribute="ID",
                        validate_schema=False,
                        expect_config=expect_config,
                    )
                    return
                except Exception as e:
                    last_error = e

        logger.warning("SAML signature validation failed: %s", last_error)
        raise ValueError("invalid_signature")

    def _select_assertion(self, root: etree._Element) -> etree._Element:
        assertion = root.find(".//saml:Assertion", namespaces=NSMAP)
        if assertion is None:
            raise ValueError("missing_assertion")
        return assertion

    def _validate_issuer(self, root: etree._Element, assertion: etree._Element) -> None:
        expected_issuer = self.idp_entity_id
        if not expected_issuer:
            return
        issuers = [
            issuer.text.strip()
            for issuer in [
                root.find("./saml:Issuer", namespaces=NSMAP),
                assertion.find("./saml:Issuer", namespaces=NSMAP),
            ]
            if issuer is not None and issuer.text
        ]
        if expected_issuer not in issuers:
            raise ValueError("invalid_issuer")

    def _validate_conditions(self, assertion: etree._Element, acs_url: str) -> None:
        conditions = assertion.find("./saml:Conditions", namespaces=NSMAP)
        if conditions is None:
            raise ValueError("missing_conditions")

        self._validate_time_window(conditions)

        audiences = [
            node.text.strip()
            for node in conditions.findall(
                ".//saml:AudienceRestriction/saml:Audience",
                namespaces=NSMAP,
            )
            if node.text
        ]
        if self.sp_entity_id not in audiences:
            raise ValueError("invalid_audience")

    def _validate_subject_confirmation(
        self,
        assertion: etree._Element,
        request_id: str | None,
        acs_url: str,
    ) -> None:
        confirmations = assertion.findall(
            "./saml:Subject/saml:SubjectConfirmation/saml:SubjectConfirmationData",
            namespaces=NSMAP,
        )
        if not confirmations:
            raise ValueError("missing_subject_confirmation")

        for confirmation in confirmations:
            recipient = confirmation.get("Recipient")
            in_response_to = confirmation.get("InResponseTo")
            if acs_url and recipient and recipient != acs_url:
                continue
            if request_id and in_response_to and in_response_to != request_id:
                continue
            not_on_or_after = confirmation.get("NotOnOrAfter")
            if not_on_or_after and self._parse_saml_datetime(not_on_or_after) <= self._skewed_now(
                past=True
            ):
                continue
            return

        raise ValueError("invalid_recipient")

    def _validate_time_window(self, conditions: etree._Element) -> None:
        now = self._now()
        not_before = conditions.get("NotBefore")
        not_on_or_after = conditions.get("NotOnOrAfter")
        if not_before and now + self._clock_skew() < self._parse_saml_datetime(not_before):
            raise ValueError("assertion_not_yet_valid")
        if not_on_or_after and now - self._clock_skew() >= self._parse_saml_datetime(
            not_on_or_after
        ):
            raise ValueError("assertion_expired")

    def _build_user(self, assertion: etree._Element, raw_xml: bytes) -> tuple[SSOUser, int]:
        name_id = assertion.find("./saml:Subject/saml:NameID", namespaces=NSMAP)
        provider_user_id = name_id.text.strip() if name_id is not None and name_id.text else ""
        if not provider_user_id:
            raise ValueError("missing_nameid")

        attributes = self._extract_attributes(assertion)
        email = self._mapped_attribute(attributes, "email")
        if not email and "@" in provider_user_id:
            email = provider_user_id

        required_attributes = self.config.extra_params.get("required_attributes", ["email"])
        if not isinstance(required_attributes, list):
            required_attributes = ["email"]
        for required in required_attributes:
            if required == "email" and not email:
                raise ValueError("missing_attribute")

        username = self._mapped_attribute(attributes, "username") or email or provider_user_id
        user = SSOUser(
            provider=self.name,
            provider_user_id=provider_user_id,
            email=email,
            username=username,
            name=self._mapped_attribute(attributes, "name"),
            first_name=self._mapped_attribute(attributes, "first_name"),
            last_name=self._mapped_attribute(attributes, "last_name"),
            email_verified=bool(email),
            raw_data={
                "attributes": attributes,
                "name_id": provider_user_id,
                "assertion_id": assertion.get("ID"),
                "response_sha256": hashlib.sha256(raw_xml).hexdigest(),
            },
        )

        return user, self._expires_in(assertion)

    def _extract_attributes(self, assertion: etree._Element) -> dict[str, list[str]]:
        attributes: dict[str, list[str]] = {}
        for attr in assertion.findall(
            ".//saml:AttributeStatement/saml:Attribute", namespaces=NSMAP
        ):
            name = attr.get("Name") or attr.get("FriendlyName")
            if not name:
                continue
            values = [
                value.text.strip()
                for value in attr.findall("./saml:AttributeValue", namespaces=NSMAP)
                if value.text
            ]
            attributes[name] = values
            friendly_name = attr.get("FriendlyName")
            if friendly_name and friendly_name != name:
                attributes[friendly_name] = values
        return attributes

    def _mapped_attribute(self, attributes: dict[str, list[str]], field: str) -> str | None:
        configured = self.config.extra_params.get("attribute_mapping", {})
        names = configured.get(field) if isinstance(configured, dict) else None
        if isinstance(names, str):
            lookup_names = [names]
        elif isinstance(names, list):
            lookup_names = [str(name) for name in names]
        else:
            lookup_names = DEFAULT_ATTRIBUTE_MAPPING.get(field, [])

        for name in lookup_names:
            values = attributes.get(name)
            if values:
                return values[0]
        return None

    def _configured_certificates(self) -> list[str]:
        values = self.config.extra_params.get("idp_x509_cert") or self.config.extra_params.get(
            "x509cert"
        )
        if not values:
            metadata = self._load_idp_metadata()
            values = self._extract_certs_from_metadata_root(metadata)
        if isinstance(values, str):
            return [self._normalize_certificate(values)]
        if isinstance(values, list):
            return [self._normalize_certificate(str(value)) for value in values if value]
        return []

    def _extract_certs_from_metadata(self, metadata_xml: Any) -> list[str]:
        if not isinstance(metadata_xml, str) or not metadata_xml.strip():
            return []
        root = self._parse_xml(metadata_xml.encode("utf-8"))
        return self._extract_certs_from_metadata_root(root)

    def _extract_certs_from_metadata_root(self, root: etree._Element | None) -> list[str]:
        if root is None:
            return []
        return [
            node.text.strip()
            for node in root.findall(".//ds:X509Certificate", namespaces=NSMAP)
            if node.text
        ]

    def _load_idp_metadata(self) -> etree._Element | None:
        if self._metadata_root is not None:
            return self._metadata_root

        metadata_xml = self.config.extra_params.get("idp_metadata_xml")
        if isinstance(metadata_xml, str) and metadata_xml.strip():
            self._metadata_root = self._parse_xml(metadata_xml.encode("utf-8"))
            return self._metadata_root

        metadata_url = self.config.extra_params.get("idp_metadata_url")
        if not isinstance(metadata_url, str) or not metadata_url.strip():
            return None

        assert_public_http_url(metadata_url)
        response = requests.get(metadata_url, timeout=10, allow_redirects=False)
        if 300 <= response.status_code < 400:
            raise ValueError("metadata_redirect_blocked")
        response.raise_for_status()
        self._metadata_root = self._parse_xml(response.content)
        return self._metadata_root

    def _expires_in(self, assertion: etree._Element) -> int:
        conditions = assertion.find("./saml:Conditions", namespaces=NSMAP)
        if conditions is not None and conditions.get("NotOnOrAfter"):
            expires_at = self._parse_saml_datetime(str(conditions.get("NotOnOrAfter")))
            return max(1, int((expires_at - self._now()).total_seconds()))
        return DEFAULT_SESSION_SECONDS

    def _clock_skew(self) -> timedelta:
        seconds = self.config.extra_params.get("clock_skew_seconds", DEFAULT_CLOCK_SKEW_SECONDS)
        try:
            return timedelta(seconds=max(int(seconds), 0))
        except (TypeError, ValueError):
            return timedelta(seconds=DEFAULT_CLOCK_SKEW_SECONDS)

    def _skewed_now(self, past: bool = False) -> datetime:
        return self._now() - self._clock_skew() if past else self._now() + self._clock_skew()

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _parse_xml(raw_xml: bytes) -> etree._Element:
        parser = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False)
        return cast("etree._Element", etree.fromstring(raw_xml, parser=parser))

    @staticmethod
    def _parse_saml_datetime(value: str) -> datetime:
        normalized = value.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    @staticmethod
    def _normalize_certificate(value: str) -> str:
        stripped = value.strip()
        if "BEGIN CERTIFICATE" in stripped:
            return stripped
        compact = "".join(stripped.split())
        lines = [compact[i : i + 64] for i in range(0, len(compact), 64)]
        return "-----BEGIN CERTIFICATE-----\n" + "\n".join(lines) + "\n-----END CERTIFICATE-----"

    @staticmethod
    def _deflate_and_base64(raw_xml: bytes) -> str:
        compressor = zlib.compressobj(wbits=-15)
        compressed = compressor.compress(raw_xml) + compressor.flush()
        return base64.b64encode(compressed).decode("ascii")

    @staticmethod
    def _build_session_token(raw_xml: bytes) -> str:
        return (
            SAML_PLACEHOLDER_TOKEN_PREFIX
            + hashlib.sha256(raw_xml + secrets.token_bytes(16)).hexdigest()
        )

    @staticmethod
    def generate_request_id() -> str:
        return "_" + secrets.token_urlsafe(32)
