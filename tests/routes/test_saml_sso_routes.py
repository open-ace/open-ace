from __future__ import annotations

import json
import re
import urllib.parse
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

import app.utils.smtp_crypto as smtp_crypto
from app.modules.sso.manager import SSOManager
from app.repositories.database import Database
from app.repositories.schema_init import load_schema_from_file
from app.routes.sso import sso_bp

ADMIN_SESSION = {
    "user_id": 1,
    "username": "admin",
    "email": "admin@example.com",
    "role": "admin",
}


@pytest.fixture
def sso_manager(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENACE_ENCRYPTION_KEY", "test-saml-route-encryption-key")
    smtp_crypto._password_manager_instance = None

    db = Database(db_url=f"sqlite:///{tmp_path / 'route-saml.db'}")
    manager = SSOManager(db=db)
    load_schema_from_file(db_url=manager.db.db_url, dialect="sqlite")

    try:
        yield manager
    finally:
        smtp_crypto._password_manager_instance = None


@pytest.fixture
def client(sso_manager):
    from flask import Flask

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(sso_bp)

    audit_logger = MagicMock()
    admin_user = {"id": 1, "username": "admin", "role": "admin", "tenant_id": None}

    with (
        patch("app.routes.sso.get_sso_manager", return_value=sso_manager),
        patch("app.routes.sso.get_audit_logger", return_value=audit_logger),
        patch("app.routes.sso.user_repo.get_user_by_id", return_value=admin_user),
        patch("app.auth.decorators._authenticate", return_value=(True, ADMIN_SESSION)),
    ):
        yield app.test_client()


def _cert_body() -> str:
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
    cert_pem = cert.public_bytes(serialization.Encoding.PEM).decode("utf-8")
    return re.sub(r"-----BEGIN CERTIFICATE-----|-----END CERTIFICATE-----|\s+", "", cert_pem)


def _register_saml_provider(client, cert_body: str):
    return client.post(
        "/api/sso/providers",
        headers={"Authorization": "Bearer t"},
        json={
            "name": "corp-saml",
            "provider_type": "saml",
            "client_id": "https://openace.example.com/saml/metadata",
            "authorization_url": "https://example.com/sso",
            "redirect_uri": "https://openace.example.com/api/sso/acs/corp-saml",
            "issuer_url": "https://idp.example.com/metadata",
            "extra_params": {
                "idp_x509_cert": cert_body,
                "idp_entity_id": "https://idp.example.com/metadata",
            },
        },
    )


def test_register_saml_provider_does_not_require_client_secret(client, sso_manager):
    cert_body = _cert_body()

    response = _register_saml_provider(client, cert_body)

    assert response.status_code == 201
    row = sso_manager.db.fetch_one(
        "SELECT config FROM sso_providers WHERE name = ?", ("corp-saml",)
    )
    stored = json.loads(row["config"])
    restored = sso_manager.deserialize_provider_config(row["config"])
    assert stored["provider_type"] == "saml"
    assert stored["client_secret_encrypted"] == ""
    assert restored["client_secret"] == ""


def test_saml_login_uses_relay_state_and_metadata_endpoint(client):
    cert_body = _cert_body()
    assert _register_saml_provider(client, cert_body).status_code == 201

    login = client.get(
        "/api/sso/login/corp-saml",
        query_string={"json": "1", "redirect_uri": "http://localhost/sso-done"},
    )
    assert login.status_code == 200
    authorization_url = login.get_json()["authorization_url"]
    query = urllib.parse.parse_qs(urllib.parse.urlparse(authorization_url).query)
    assert "SAMLRequest" in query
    assert "RelayState" in query
    assert "state" not in query

    metadata = client.get("/api/sso/providers/corp-saml/metadata")
    assert metadata.status_code == 200
    assert b"EntityDescriptor" in metadata.data
    assert b"AssertionConsumerService" in metadata.data
