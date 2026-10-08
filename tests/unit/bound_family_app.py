"""Shared Flask/application fixture for the external delegation contract tests."""

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from flask import Flask

from app.modules.workspace.api_key_proxy import APIKeyProxyService
from app.modules.workspace.llm_proxy_handler import handle_llm_proxy_request


@pytest.fixture
def bound_app(tmp_path, monkeypatch):
    from app.auth import decorators
    from app.modules.governance import audit_logger, content_filter_singleton, quota_manager
    from app.modules.workspace import llm_proxy_handler, session_manager
    from app.repositories import tenant_repo, user_repo

    profile = {
        "id": "local",
        "tenant_id": 3,
        "user_ids": [9, 10],
        "provider": "openai",
        "protocol": "openai_chat",
        "base_url": "https://models.example/v1",
        "model": "selected-model",
        "location": "cloud",
        "managed_keys": True,
    }
    api = APIKeyProxyService.__new__(APIKeyProxyService)
    api.db_path = str(tmp_path / "proxy.sqlite")
    api._encryption_key = b"k" * 32
    api._router = Mock()
    api._ensure_tables()
    # The delegation tables come from the authoritative schema (migrations in
    # production); load them into this fixture database the same way.
    from app.repositories.schema_init import load_schema_from_file

    load_schema_from_file(f"sqlite:///{api.db_path}")
    with sqlite3.connect(api.db_path) as db:
        # The authoritative agent_sessions schema has NOT NULL columns beyond
        # (session_id, status); seed the one row the tests read.
        db.execute(
            "INSERT INTO agent_sessions (session_id, session_type, tool_name, user_id,"
            " tenant_id, status) VALUES ('owned-session', 'chat', 'qwen', 9, 3, 'active')"
        )
    state = {
        "user": {"id": 9, "tenant_id": 3, "is_active": True, "role": "user", "username": "owner"},
        "filter": "none",
        "quota": True,
        "provider_calls": 0,
    }
    monkeypatch.setattr(
        decorators,
        "_load_user_from_token",
        lambda token: state["user"] if token == "session-test-token" else None,
    )
    monkeypatch.setattr(
        user_repo, "UserRepository", lambda: SimpleNamespace(get_user_by_id=lambda _: state["user"])
    )
    tenant = SimpleNamespace(
        status="active",
        settings=SimpleNamespace(
            block_sensitive_keyword=False, sensitive_keyword_match_mode="word_boundary"
        ),
    )
    monkeypatch.setattr(
        tenant_repo, "TenantRepository", lambda: SimpleNamespace(get_by_id=lambda _: tenant)
    )
    session = SimpleNamespace(
        session_id="owned-session",
        user_id=9,
        tenant_id=3,
        status="active",
        session_type="chat",
        workspace_type="remote",
        expires_at=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1),
    )
    monkeypatch.setattr(
        session_manager,
        "get_session_manager",
        lambda: SimpleNamespace(get_session=lambda *args, **kwargs: session),
    )
    monkeypatch.setattr(
        quota_manager,
        "QuotaManager",
        lambda: SimpleNamespace(check_quota=lambda _: {"allowed": state["quota"]}),
    )
    monkeypatch.setattr(
        content_filter_singleton,
        "get_content_filter",
        lambda: SimpleNamespace(
            check_content=lambda *args, **kwargs: SimpleNamespace(
                passed=True, action=state["filter"]
            )
        ),
    )
    monkeypatch.setattr(
        audit_logger,
        "AuditLogger",
        lambda: SimpleNamespace(log_action=lambda *args, **kwargs: True),
    )
    monkeypatch.setattr(
        llm_proxy_handler,
        "get_gateway_planner",
        Mock(side_effect=AssertionError("bound request reached shared gateway")),
    )
    monkeypatch.setattr(llm_proxy_handler, "_record_llm_usage", Mock())

    app = Flask(__name__)
    app.config["TESTING"] = True

    @app.post("/api/remote/llm-proxy/<path:path>")
    def model(path):
        return handle_llm_proxy_request(scope="remote", api_proxy=api, path=path)

    return app.test_client(), api, state, profile


def seed_pool_key(
    api,
    base_url="https://models.example/v1",
    key="synthetic-pool-key",
    provider="openai",
    active=True,
):
    """Insert one ACE-managed pool row; managed resolution reads the table directly."""
    encrypted = api._encrypt_key(key)
    with sqlite3.connect(api.db_path) as db:
        db.execute(
            "INSERT INTO api_key_store (tenant_id, provider, key_name, encrypted_key, key_hash,"
            " base_url, is_active, scope) VALUES (3, ?, 'pool-key', ?, ?, ?, ?, 'shared')",
            (provider, encrypted, "hash", base_url, 1 if active else 0),
        )
        db.commit()


@pytest.fixture
def external(bound_app, tmp_path, monkeypatch):
    """Agent-route scaffold: app + blueprint, identity policy, quota stubs."""
    client, api, state, _ = bound_app
    from types import SimpleNamespace

    from app.modules.governance import quota_manager
    from app.modules.workspace import api_key_proxy
    from app.repositories import tenant_repo
    from app.routes.external_identity import external_identity_bp

    client.application.register_blueprint(external_identity_bp, url_prefix="/api")
    monkeypatch.setattr(api_key_proxy, "get_api_key_proxy_service", lambda: api)
    tenant = SimpleNamespace(
        status="active",
        settings=SimpleNamespace(
            block_sensitive_keyword=False, sensitive_keyword_match_mode="word_boundary"
        ),
        to_dict=lambda: {"id": 3, "status": "active"},
    )
    monkeypatch.setattr(
        tenant_repo, "TenantRepository", lambda: SimpleNamespace(get_by_id=lambda _: tenant)
    )
    state["reserved"] = []
    monkeypatch.setattr(
        quota_manager,
        "QuotaManager",
        lambda: SimpleNamespace(
            check_quota=lambda *args, **kwargs: {"allowed": state["quota"]},
            record_usage=lambda *args, **kwargs: state["reserved"].append(kwargs) or True,
        ),
    )
    secret = tmp_path / "signing.key"
    secret.write_text("k" * 32)
    secret.chmod(0o600)
    policy = tmp_path / "identity.json"
    policy.write_text(
        json.dumps(
            {
                "issuers": [{"id": "client", "secret_file": str(secret), "audience": "openace"}],
                "mappings": [
                    {
                        "issuer": "client",
                        "external_org": "1",
                        "external_user": str(user),
                        "external_login": login,
                        "tenant_id": 3,
                        "user_id": local,
                    }
                    for user, login, local in [(7, "alice", 9), (8, "bob", 10)]
                ],
            }
        )
    )
    policy.chmod(0o600)
    monkeypatch.setenv("OPENACE_EXTERNAL_IDENTITY_POLICY_FILE", str(policy))
    return client, api, state, {}
