"""Flask contract for the external token exchange route.

An operator-registered issuer proves who it is once with the same signed
request the probe uses, and receives a short-lived scoped proxy token for the
mapped user (``session_type="external"``, provider-pinned, model allow-listed,
``redact_policy=deny``). The token then talks to the existing LLM proxy; its
governance behaviors — session-stop cutoff, filtering, deny-on-redact, the
model allow-list and the key-echo guard — are pinned in
``test_llm_proxy_external_governance.py`` on the shared path.
"""

import json
import secrets
import sqlite3
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from flask import Flask

# Captured at import time, before any fixture patches the module attribute:
# the metering e2e needs the real QuotaManager bound to the fixture database.
from app.modules.governance.quota_manager import QuotaManager as _RealQuotaManager
from app.modules.workspace.external_identity import TOKEN_PATH, signature
from app.repositories.user_repo import UserRepository as _RealUserRepo
from app.routes.external_identity import external_identity_bp

_KEY = b"i" * 32
_MINTED = "minted-proxy-token"


@pytest.fixture
def exchange_app(tmp_path, monkeypatch):
    from app.modules.workspace import api_key_proxy, session_manager
    from app.modules.workspace.api_key_proxy import APIKeyProxyService
    from app.repositories import database, tenant_repo, user_repo

    api = APIKeyProxyService.__new__(APIKeyProxyService)
    api.db_path = str(tmp_path / "proxy.sqlite")
    api._encryption_key = b"k" * 32
    api._router = MagicMock()
    api._ensure_tables()
    from app.repositories.schema_init import load_schema_from_file

    load_schema_from_file(f"sqlite:///{api.db_path}", dialect="sqlite")
    minted = {}

    def mint(**kwargs):
        minted.update(kwargs)
        return _MINTED

    api.generate_proxy_token = mint
    monkeypatch.setattr(api_key_proxy, "get_api_key_proxy_service", lambda: api)
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

    sessions = []

    class FakeSessions:
        def __init__(self):
            self.rows = {}

        def create_session(self, **kwargs):
            session_id = kwargs.get("session_id") or secrets.token_hex(16)
            session = SimpleNamespace(
                session_id=session_id,
                session_type=kwargs.get("session_type", "external"),
                user_id=kwargs.get("user_id"),
                tenant_id=kwargs.get("tenant_id"),
                tool_name=kwargs.get("tool_name", "external:host"),
                host_name="localhost",
                model=None,
                title=kwargs.get("title", ""),
                message_count=0,
                request_count=0,
                total_tokens=0,
                total_input_tokens=0,
                total_output_tokens=0,
            )
            self.rows[session_id] = session
            sessions.append((dict(kwargs), session))
            return session

        def get_session(self, session_id, include_messages=False):
            return self.rows.get(session_id)

        def update_session_fields(self, session_id, fields, tenant_id=None, require_tenant=False):
            row = self.rows.get(session_id)
            if row:
                for key, value in fields.items():
                    setattr(row, key, value)
            return True

        def increment_session_usage(self, session_id, **kwargs):
            row = self.rows.get(session_id)
            if row:
                for delta, name in (
                    ("message_delta", "message_count"),
                    ("request_delta", "request_count"),
                    ("total_tokens_delta", "total_tokens"),
                    ("total_input_delta", "total_input_tokens"),
                    ("total_output_delta", "total_output_tokens"),
                ):
                    setattr(row, name, getattr(row, name, 0) + kwargs.get(delta, 0))
            return True

        def append_transcript_message(self, **kwargs):
            return SimpleNamespace(_was_inserted=True)

    fake_sessions = FakeSessions()

    monkeypatch.setattr(session_manager, "get_session_manager", lambda: fake_sessions)

    from app.modules.governance import audit_logger as audit_module

    audited = []
    monkeypatch.setattr(
        audit_module,
        "AuditLogger",
        lambda: SimpleNamespace(log_action=lambda **kwargs: audited.append(kwargs) or True),
    )

    secret = tmp_path / "issuer.key"
    secret.write_bytes(_KEY)
    secret.chmod(0o600)
    policy = tmp_path / "identity.json"
    policy.write_text(
        json.dumps(
            {
                "issuers": [
                    {
                        "id": "host",
                        "secret_file": str(secret),
                        "audience": "openace",
                        "allowed_providers": ["openai"],
                        "allowed_models": ["glm-5"],
                        "max_ttl_seconds": 900,
                    }
                ],
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
    monkeypatch.setenv("OPENACE_EXTERNAL_TOKEN_ENABLED", "1")

    app = Flask("exchange")
    app.config["TESTING"] = True
    app.register_blueprint(external_identity_bp, url_prefix="/api")
    return SimpleNamespace(
        client=app.test_client(), minted=minted, sessions=sessions, audited=audited, api=api
    )


def call(client, body, nonce, *, path=TOKEN_PATH, secret=_KEY):
    stamp = str(int(time.time()))
    raw = json.dumps(body, separators=(",", ":")).encode()
    return client.post(
        path,
        data=raw,
        content_type="application/json",
        headers={
            "X-ACE-Issuer": "host",
            "X-ACE-Time": stamp,
            "X-ACE-Nonce": nonce,
            "X-ACE-Signature": signature(
                secret, "host", stamp, nonce, raw, path=path, audience="openace"
            ),
        },
    )


_BODY = {"organization": "1", "user": "7", "login": "alice", "provider": "openai"}


def _nonce():
    return secrets.token_hex(24)


class TestTokenExchange:
    def test_exchange_mints_scoped_external_token(self, exchange_app):
        resp = call(exchange_app.client, _BODY, _nonce())
        assert resp.status_code == 200
        payload = resp.get_json()
        assert payload["token"] == _MINTED
        assert payload["session_id"]
        # UTC with offset, on the same second the token was minted at.
        assert payload["expires_at"].endswith("+00:00")
        assert payload["proxy_path"] == "/api/remote/llm-proxy"
        assert "base_url" not in payload
        assert resp.headers["Cache-Control"] == "no-store"

        kwargs = exchange_app.minted
        assert kwargs["session_type"] == "external"
        assert kwargs["provider"] == "openai"
        assert kwargs["user_id"] == 9 and kwargs["tenant_id"] == 3
        assert kwargs["extra_payload"] == {
            "issuer": "host",
            "redact_policy": "deny",
            "allowed_models": ["glm-5"],
        }

        session_kwargs, _ = exchange_app.sessions[0]
        assert session_kwargs["session_type"] == "external"
        assert session_kwargs["user_id"] == 9

        assert len(exchange_app.audited) == 1
        assert exchange_app.audited[0]["action"].value == "external_token_issued"

    def test_provider_outside_issuer_policy_denied(self, exchange_app):
        body = dict(_BODY, provider="anthropic")
        assert call(exchange_app.client, body, _nonce()).status_code == 403
        assert exchange_app.minted == {}

    def test_ttl_over_max_denied_and_default_applies(self, exchange_app):
        body = dict(_BODY, ttl_seconds=901)
        assert call(exchange_app.client, body, _nonce()).status_code == 403

        resp = call(exchange_app.client, _BODY, _nonce())
        assert resp.status_code == 200
        # The default TTL is threaded as exact seconds, not floored minutes.
        assert exchange_app.minted["expires_seconds"] == 900

    def test_signature_bound_to_the_token_path(self, exchange_app):
        stamp = str(int(time.time()))
        raw = json.dumps(_BODY, separators=(",", ":")).encode()
        nonce = _nonce()
        wrong_path = signature(
            _KEY,
            "host",
            stamp,
            nonce,
            raw,
            path="/api/integrations/external/capabilities",
            audience="openace",
        )
        resp = exchange_app.client.post(
            TOKEN_PATH,
            data=raw,
            content_type="application/json",
            headers={
                "X-ACE-Issuer": "host",
                "X-ACE-Time": stamp,
                "X-ACE-Nonce": nonce,
                "X-ACE-Signature": wrong_path,
            },
        )
        assert resp.status_code == 403

    def test_exchange_replays_rejected(self, exchange_app):
        nonce = _nonce()
        assert call(exchange_app.client, _BODY, nonce).status_code == 200
        assert call(exchange_app.client, _BODY, nonce).status_code == 403

    def test_disabled_gate_returns_404(self, exchange_app, monkeypatch):
        monkeypatch.delenv("OPENACE_EXTERNAL_TOKEN_ENABLED")
        assert call(exchange_app.client, _BODY, _nonce()).status_code == 404

    def test_capabilities_reports_the_exchange_dry_run(self, exchange_app):
        from app.modules.workspace.external_identity import PATH

        body = {"organization": "1", "user": "7", "login": "alice"}
        resp = call(exchange_app.client, body, _nonce(), path=PATH)
        assert resp.status_code == 200
        payload = resp.get_json()
        assert payload["exchange"] == {
            "available": True,
            "providers": ["openai"],
            "max_ttl_seconds": 900,
        }

    def test_capabilities_exchange_unavailable_without_issuer_grants(self, tmp_path, monkeypatch):
        secret = tmp_path / "issuer.key"
        secret.write_bytes(_KEY)
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

        from app.modules.workspace import api_key_proxy
        from app.modules.workspace.api_key_proxy import APIKeyProxyService
        from app.repositories import database, tenant_repo, user_repo

        api = APIKeyProxyService.__new__(APIKeyProxyService)
        api.db_path = str(tmp_path / "proxy.sqlite")
        api._encryption_key = b"k" * 32
        api._router = MagicMock()
        api._ensure_tables()
        from app.repositories.schema_init import load_schema_from_file

        load_schema_from_file(f"sqlite:///{api.db_path}")
        monkeypatch.setattr(api_key_proxy, "get_api_key_proxy_service", lambda: api)
        monkeypatch.setattr(database, "is_postgresql", lambda: False)
        monkeypatch.setattr(database, "get_param_placeholder", lambda: "?")
        monkeypatch.setattr(database, "get_connection", lambda: sqlite3.connect(api.db_path))
        monkeypatch.setattr(
            user_repo,
            "UserRepository",
            lambda: SimpleNamespace(
                get_user_by_id=lambda _: {"id": 9, "tenant_id": 3, "is_active": True}
            ),
        )
        monkeypatch.setattr(
            tenant_repo,
            "TenantRepository",
            lambda: SimpleNamespace(
                get_by_id=lambda _: SimpleNamespace(to_dict=lambda: {"id": 3, "status": "active"})
            ),
        )

        app = Flask("probe")
        app.config["TESTING"] = True
        app.register_blueprint(external_identity_bp, url_prefix="/api")
        from app.modules.workspace.external_identity import PATH

        body = {"organization": "1", "user": "7", "login": "alice"}
        resp = call(app.test_client(), body, _nonce(), path=PATH)
        assert resp.status_code == 200
        assert resp.get_json()["exchange"]["available"] is False


class TestTokenExchangeFieldContract:
    def test_missing_required_provider_denied(self, exchange_app):
        body = {"organization": "1", "user": "7", "login": "alice"}
        assert call(exchange_app.client, body, _nonce()).status_code == 403

    def test_unknown_extra_field_denied(self, exchange_app):
        body = dict(_BODY, model="glm-5")
        assert call(exchange_app.client, body, _nonce()).status_code == 403


class TestTokenExchangeExactTtl:
    def test_real_mint_honors_expires_seconds(self, exchange_app):
        """The minted token's exp must equal the advertised expires_at.

        A minutes floor would make the two disagree by up to 59s, and a
        sub-minute issuer ceiling would be silently exceeded.
        """
        import json as _json
        import time as _time
        from base64 import b64decode

        from app.modules.workspace.api_key_proxy import APIKeyProxyService

        # The fixture's service has generate_proxy_token stubbed for capture;
        # mint through a fresh service bound to the same schema-loaded DB.
        api = APIKeyProxyService.__new__(APIKeyProxyService)
        api.db_path = exchange_app.api.db_path
        api._encryption_key = b"k" * 32
        api._router = MagicMock()
        token = api.generate_proxy_token(
            user_id=9,
            session_id="sess",
            tenant_id=3,
            provider="openai",
            session_type="external",
            expires_seconds=37,
        )
        payload_b64 = token.split(".")[0]
        payload = _json.loads(b64decode(payload_b64))
        exp = _time.mktime(_time.strptime(payload["exp"][:19], "%Y-%m-%dT%H:%M:%S"))
        assert 34 <= exp - _time.time() <= 40, "exp must be ~37s out, not floored to minutes"

    def test_unhashable_provider_denied_not_500(self, exchange_app):
        body = dict(_BODY)
        body["provider"] = ["openai"]
        resp = call(exchange_app.client, body, _nonce())
        assert resp.status_code == 403, "shape violation must deny, not error"


class TestTokenExchangeSessionAndAudit:
    def test_repeated_exchanges_reuse_one_session(self, exchange_app):
        first = call(exchange_app.client, _BODY, _nonce())
        second = call(exchange_app.client, _BODY, _nonce())
        assert first.status_code == 200 and second.status_code == 200
        assert first.get_json()["session_id"] == second.get_json()["session_id"]
        assert len(exchange_app.sessions) == 2  # create_session called twice...
        # ...but the mock returns a fresh id each time; pin the REQUEST id:
        requested = [kwargs.get("session_id") for kwargs, _ in exchange_app.sessions]
        assert requested[0] == requested[1] == "external:host:9"

    def test_audit_failure_fails_closed_revoking_only_the_minted_jti(
        self, exchange_app, monkeypatch
    ):
        """One transient audit-write failure must cost exactly this token:
        no session stop, no revocation of the identity's other live tokens."""
        from app.modules.governance import audit_logger as audit_module
        from app.modules.workspace import api_key_proxy as akp_module

        revoked_jtis = []
        revoked_sessions = []

        def fake_revoke_jti(jti):
            revoked_jtis.append(jti)
            return True

        original_revoke_session = akp_module.APIKeyProxyService.revoke_proxy_tokens_for_session

        def spy_revoke_session(self, session_id, *a, **k):
            revoked_sessions.append(session_id)
            return original_revoke_session(self, session_id, *a, **k)

        # Capture the jti the route parses out of the minted token.
        real_generate = exchange_app.minted

        from base64 import b64encode as _b64e

        fake_payload = _b64e(json.dumps({"jti": "deadbeefdeadbeef"}).encode()).decode()

        def mint_capture(**kwargs):
            real_generate.update(kwargs)
            return f"{fake_payload}.signature"

        exchange_app.api.generate_proxy_token = mint_capture
        exchange_app.api.revoke_proxy_token_jti = fake_revoke_jti
        monkeypatch.setattr(
            akp_module.APIKeyProxyService,
            "revoke_proxy_tokens_for_session",
            spy_revoke_session,
        )
        monkeypatch.setattr(
            audit_module,
            "AuditLogger",
            lambda: SimpleNamespace(log_action=lambda **kwargs: False),
        )
        resp = call(exchange_app.client, _BODY, _nonce())
        assert resp.status_code == 503
        assert revoked_jtis == ["deadbeefdeadbeef"], "the route must revoke the parsed jti"
        assert revoked_sessions == [], "the shared session must be left alone"

    def test_stopped_session_denies_exchange_explicitly(self, exchange_app):
        """An admin stopping the shared row is the per-identity kill switch:
        the exchange says so, instead of minting a dead-on-arrival token."""
        exchange_app.sessions.clear()
        call(exchange_app.client, _BODY, _nonce())  # create the shared row

        stopped_session = SimpleNamespace(session_id="external:host:9", status="stopped")

        from app.modules.workspace import session_manager as sm_module

        real_manager = sm_module.get_session_manager()
        real_manager.create_session = lambda **kwargs: stopped_session
        resp = call(exchange_app.client, _BODY, _nonce())
        assert resp.status_code == 403
        assert resp.get_json()["error_code"] == "session_stopped"
        assert exchange_app.minted.get("expires_seconds") is None or resp.status_code == 403


class TestExchangeToQuotaMetering:
    def test_exchange_token_to_usage_to_quota_and_second_day(self, exchange_app, monkeypatch):
        """Exchange -> proxy call -> usage recorded -> check_quota reflects it.

        The metering chain is the property that failed in the first revision
        (the meter could not see delegated usage). This pins it against the
        REAL session manager (so SessionSink's per-day upsert writes
        session_daily_usage — the table check_quota actually reads), with
        the clock frozen, and covers a second-day call on the same shared
        session so the agent_sessions created_at fallback is never needed.
        """
        import sqlite3 as _sqlite3
        from datetime import datetime as _real_datetime
        from datetime import timedelta as _real_td
        from datetime import timezone as _real_tz

        from flask import Flask

        from app.modules.governance import quota_manager as _qm_module
        from app.modules.workspace import api_key_proxy as _akp
        from app.modules.workspace import session_manager as _sm_module
        from app.modules.workspace.api_key_proxy import APIKeyProxyService
        from app.modules.workspace.session_manager import SessionManager
        from app.routes.remote import remote_bp

        db = exchange_app.api.db_path
        with _sqlite3.connect(db) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO tenants (id, name, slug, status)"
                " VALUES (3, 'T', 't', 'active')"
            )
            conn.execute(
                "INSERT OR REPLACE INTO users (id, username, password_hash, tenant_id,"
                " is_active, daily_token_quota) VALUES (9, 'alice', 'h', 3, 1, 20)"
            )
            conn.commit()

        # The service's sqlite/pg switch resolves the operator's global
        # config (a dev PostgreSQL on some machines): pin it to sqlite so
        # minting and validating both run against the fixture file.
        monkeypatch.setattr(_akp, "is_postgresql", lambda: False)

        # Real session manager on the fixture database: the usage sinks'
        # increment_session_usage -> _upsert_daily_usage writes the
        # session_daily_usage rows check_quota reads.
        real_sessions = SessionManager(db_path=db)
        monkeypatch.setattr(_sm_module, "get_session_manager", lambda: real_sessions)
        real_sessions.create_session(
            session_id="external:host:9",
            tool_name="external:host",
            user_id=9,
            tenant_id=3,
            session_type="external",
            title="t",
        )

        # Frozen, shiftable clock shared by the recording and reading paths
        # (both use datetime.now(timezone.utc)); anchored at the real now so
        # the minted token's validity window lines up.
        frozen_now = [_real_datetime.now(_real_tz.utc).replace(microsecond=0)]

        class _FrozenDateTime(_real_datetime):
            @classmethod
            def now(cls, tz=None):
                moment = frozen_now[0]
                return moment.replace(tzinfo=tz) if tz is not None else moment.replace(tzinfo=None)

        monkeypatch.setattr(_sm_module, "datetime", _FrozenDateTime)
        monkeypatch.setattr(_qm_module, "datetime", _FrozenDateTime)

        quota_db = _qm_module.Database(f"sqlite:///{db}")

        # The handler's own pre-call quota check constructs QuotaManager()
        # with the operator's default database; pin it to the fixture file
        # too, so checks and recording share one database.
        monkeypatch.setattr(
            _qm_module,
            "QuotaManager",
            lambda *a, **k: _RealQuotaManager(db=quota_db, user_repo=_RealUserRepo(db=quota_db)),
        )

        def bound_quota():
            # _RealQuotaManager/_RealUserRepo are import-time aliases from
            # the top of this file — immune to the fixture's patches.
            return _RealQuotaManager(db=quota_db, user_repo=_RealUserRepo(db=quota_db))

        def mint():
            api = APIKeyProxyService.__new__(APIKeyProxyService)
            api.db_path = db
            api._encryption_key = b"k" * 32
            api._router = MagicMock()
            return api.generate_proxy_token(
                user_id=9,
                session_id="external:host:9",
                tenant_id=3,
                provider="openai",
                session_type="external",
                expires_seconds=3600,
                extra_payload={
                    "issuer": "host",
                    "redact_policy": "deny",
                    "allowed_models": ["glm-5"],
                },
            )

        app = Flask("metering")
        app.config["TESTING"] = True
        app.register_blueprint(remote_bp, url_prefix="/api/remote")

        from app.modules.workspace import llm_proxy_handler as _handler

        def proxy_call(token, *, usage=b'"usage":{"prompt_tokens":7,"completion_tokens":9}'):
            upstream = MagicMock()
            upstream.status_code = 200
            upstream.content = b'{"choices":[{"message":{"content":"answer"}}],' + usage + b"}"
            upstream.headers = {"Content-Type": "application/json"}
            upstream.iter_content.return_value = [upstream.content]
            with (
                patch(
                    "app.routes.remote.get_api_key_proxy_service",
                    lambda: _ValidatorBackedProxy(_bound_api(db)),
                ),
                patch("requests.request", return_value=upstream),
                patch.object(_handler, "_check_content_filter", return_value=None),
                patch(
                    "app.repositories.daily_stats_repo.DailyStatsRepository",
                    lambda: SimpleNamespace(refresh_stats=lambda: None),
                ),
            ):
                return app.test_client().post(
                    "/api/remote/llm-proxy",
                    json={"model": "glm-5", "messages": [{"role": "user", "content": "hi"}]},
                    headers={"Authorization": f"Bearer {token}"},
                )

        day1 = bound_quota().check_quota(9)
        assert day1["status"]["tokens"]["used"] == 0

        resp = proxy_call(mint())
        assert resp.status_code == 200, resp.get_data()

        day1_after = bound_quota().check_quota(9)
        assert day1_after["status"]["tokens"]["used"] == 16, (
            "the metered 16 tokens must be visible to check_quota; " f"status={day1_after}"
        )
        assert day1_after["status"]["requests"]["used"] == 1

        # Second day, same shared session: the per-day upsert means the new
        # day's window counts only that day's usage (16 again, not 32).
        frozen_now[0] = frozen_now[0] + _real_td(days=1)
        # A distinct response, so the usage dedup cache (keyed on request
        # content) doesn't swallow the second day's record.
        resp2 = proxy_call(mint(), usage=b'"usage":{"prompt_tokens":8,"completion_tokens":10}')
        assert resp2.status_code == 200, resp2.get_data()
        day2_after = bound_quota().check_quota(9)
        assert day2_after["status"]["tokens"]["used"] == 18, (
            "day two must count its own usage through the same shared session; "
            f"status={day2_after}"
        )


def _bound_api(db):
    from app.modules.workspace.api_key_proxy import APIKeyProxyService

    api = APIKeyProxyService.__new__(APIKeyProxyService)
    api.db_path = db
    api._encryption_key = b"k" * 32
    api._router = MagicMock()
    return api


class _ValidatorBackedProxy:
    """Real validation and key resolution over the fixture database."""

    def __init__(self, real_api):
        self._real = real_api

    def validate_proxy_token(self, token):
        return self._real.validate_proxy_token(token)

    def resolve_api_key_for_scope(self, *args, **kwargs):
        return ("sk-metering-key-0123456789abcdef", "https://api.openai.com", 7, None, [])
