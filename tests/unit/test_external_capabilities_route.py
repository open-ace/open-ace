"""Flask contract for the default-off external capabilities probe route."""

import json
import secrets
import sqlite3
import time
from types import SimpleNamespace

import pytest
from flask import Flask

from app.modules.workspace.external_identity import PATH, signature
from app.routes.external_identity import external_identity_bp


@pytest.fixture
def probe_app(tmp_path, monkeypatch):
    from unittest.mock import Mock

    from app.modules.workspace import api_key_proxy
    from app.modules.workspace.api_key_proxy import APIKeyProxyService
    from app.repositories import tenant_repo, user_repo

    api = APIKeyProxyService.__new__(APIKeyProxyService)
    api.db_path = str(tmp_path / "proxy.sqlite")
    api._encryption_key = b"k" * 32
    api._router = Mock()
    api._ensure_tables()
    from app.repositories import database
    from app.repositories.schema_init import load_schema_from_file

    load_schema_from_file(f"sqlite:///{api.db_path}")
    monkeypatch.setattr(api_key_proxy, "get_api_key_proxy_service", lambda: api)
    # The route obtains its replay connection through the shared database
    # module; point that at the fixture database instead of any real server.
    monkeypatch.setattr(database, "is_postgresql", lambda: False)
    monkeypatch.setattr(database, "get_param_placeholder", lambda: "?")
    monkeypatch.setattr(database, "get_connection", lambda: sqlite3.connect(api.db_path))
    account = {"id": 9, "tenant_id": 3, "is_active": True}
    monkeypatch.setattr(
        user_repo, "UserRepository", lambda: SimpleNamespace(get_user_by_id=lambda _: account)
    )
    tenant = {"id": 3, "status": "active"}
    monkeypatch.setattr(
        tenant_repo,
        "TenantRepository",
        lambda: SimpleNamespace(get_by_id=lambda _: SimpleNamespace(to_dict=lambda: tenant)),
    )
    secret = tmp_path / "issuer.key"
    secret.write_bytes(b"i" * 32)
    secret.chmod(0o600)
    policy = tmp_path / "identity.json"
    policy.write_text(
        json.dumps(
            {
                "issuers": [{"id": "host", "secret_file": str(secret), "audience": "openace"}],
                "mappings": [
                    {
                        "issuer": "host",
                        "external_org": "1",
                        "external_user": "7",
                        "external_login": "alice",
                        "tenant_id": 3,
                        "user_id": 9,
                    }
                ],
            }
        )
    )
    policy.chmod(0o600)
    monkeypatch.setenv("OPENACE_EXTERNAL_IDENTITY_POLICY_FILE", str(policy))

    app = Flask("probe")
    app.config["TESTING"] = True
    app.register_blueprint(external_identity_bp, url_prefix="/api")
    return app.test_client()


def call(client, body, nonce, *, issuer="host", secret=b"i" * 32, path=PATH):
    stamp = str(int(time.time()))
    return client.post(
        path,
        data=body,
        content_type="application/json",
        headers={
            "X-ACE-Issuer": issuer,
            "X-ACE-Time": stamp,
            "X-ACE-Nonce": nonce,
            "X-ACE-Signature": signature(
                secret, issuer, stamp, nonce, body, path=path, audience="openace"
            ),
        },
    )


def test_probe_route_verifies_mapping_and_caps(probe_app):
    body = b'{"organization":"1","user":"7","login":"alice"}'
    response = call(probe_app, body, secrets.token_hex(24))
    assert response.status_code == 200, response.get_json()
    assert response.get_json()["identity"] == {"tenant_id": 3, "user_id": 9}
    assert response.get_json()["capabilities"] == {"diagnosis_llm": False, "agent_workspace": False}
    assert response.get_json()["reason"] == "governance_adapter_not_implemented"


def test_probe_route_rejects_replay(probe_app):
    body = b'{"organization":"1","user":"7","login":"alice"}'
    nonce = secrets.token_hex(24)
    assert call(probe_app, body, nonce).status_code == 200
    assert call(probe_app, body, nonce).status_code == 403


def test_probe_route_unknown_issuer_denied_without_enumeration(probe_app):
    body = b'{"organization":"1","user":"7","login":"alice"}'
    response = call(probe_app, body, secrets.token_hex(24), issuer="ghost")
    assert response.status_code == 403
    assert response.get_json()["error_code"] == "not_accessible"


def test_probe_route_disabled_by_default(monkeypatch):
    app = Flask("probe")
    app.config["TESTING"] = True
    app.register_blueprint(external_identity_bp, url_prefix="/api")
    monkeypatch.delenv("OPENACE_EXTERNAL_IDENTITY_POLICY_FILE", raising=False)
    response = app.test_client().post(PATH, data=b"{}", content_type="application/json")
    assert response.status_code == 404
