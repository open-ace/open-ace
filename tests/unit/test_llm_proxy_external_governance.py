"""External proxy-token governance: session lifecycle, filter scope, redact/model
policy, principal liveness, and the key-echo hold-back guard.

These pin the shared-path behaviors agreed in the #3415 token-exchange design
review: an ``external`` session type dies with its session row (2a), is never
content-filter-exempt (2b), honors ``redact_policy: "deny"`` (2c) and an
``allowed_models`` allow-list (2e), rechecks its mapped principal on every
request (2d), and its responses run through a key-echo guard that withholds a
key-sized window so an echoed key can never reach the caller (2g).
"""

import json
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from flask import Flask

from app.modules.workspace.api_key_proxy import APIKeyProxyService
from app.modules.workspace.llm_proxy_handler import (
    _finalize_upstream_response,
    _is_autonomous_request,
    _key_echo_guard,
)


@pytest.fixture
def flask_app():
    app = Flask(__name__)
    app.config["TESTING"] = True
    return app


_PROXY_PATH = "app.routes.remote.get_api_key_proxy_service"
_QUOTA_PATH = "app.modules.governance.quota_manager.QuotaManager"
_HTTP_PATH = "requests.request"
_FILTER_PATH = "app.modules.workspace.llm_proxy_handler._check_content_filter"

_EXTERNAL_TOKEN = {
    "user_id": 1,
    "tenant_id": 1,
    "provider": "openai",
    "session_id": "ext-sess-1",
    "session_type": "external",
    "scope": "remote",
    "redact_policy": "deny",
    "allowed_models": ["glm-5"],
}


def _conn_returning(rows):
    conn = MagicMock()
    cursor = MagicMock()
    cursor.fetchone.side_effect = list(rows)
    conn.cursor.return_value = cursor
    return conn


def _service():
    return object.__new__(APIKeyProxyService)


# ── 2a: external session lifecycle ────────────────────────────────────────


class TestExternalSessionLifecycle:
    def _allows(self, conn, session_type):
        now = datetime.now()
        return _service()._session_allows_proxy_token_with_conn(
            conn,
            session_id="ext-session",
            session_type=session_type,
            user_id=1,
            now=now,
            exp=now + timedelta(minutes=5),
        )

    def test_external_without_session_row_fails_closed(self):
        assert self._allows(_conn_returning([None]), "external") is False

    def test_external_with_active_session_allowed(self):
        assert self._allows(_conn_returning([("active",)]), "external") is True

    def test_external_with_stopped_session_rejected(self):
        assert self._allows(_conn_returning([("stopped",)]), "external") is False

    def test_unknown_session_type_without_row_still_allowed(self):
        # Existing behavior for session types without agent_sessions rows.
        assert self._allows(_conn_returning([None]), "other") is True

    def test_external_without_row_rejected_in_conn_free_variant(self):
        svc = _service()
        svc._get_connection = lambda: _conn_returning([None])
        now = datetime.now()
        assert (
            svc._session_allows_proxy_token(
                session_id="ext-session",
                session_type="external",
                user_id=1,
                now=now,
                exp=now + timedelta(minutes=5),
            )
            is False
        )


# ── 2b: external is never content-filter-exempt ──────────────────────────


class TestExternalNotFilterExempt:
    @pytest.mark.regression
    def test_external_is_not_autonomous(self):
        assert _is_autonomous_request({"session_type": "external"}) is False

    @pytest.mark.regression
    def test_only_agent_session_type_is_exempt(self):
        assert _is_autonomous_request({"session_type": "agent"}) is True
        for other in ("terminal", "workflow", "external", "webui"):
            assert _is_autonomous_request({"session_type": other}) is False


# ── 2d: external principal liveness ───────────────────────────────────────


class TestExternalPrincipalLiveness:
    def _alive(self, rows, user_id=1, tenant_id=1, conn=None):
        return _service()._external_principal_alive_with_conn(
            conn or _conn_returning(rows), user_id, tenant_id
        )

    def test_active_user_active_tenant(self):
        assert self._alive([(True, None, "active", None)]) is True

    def test_deactivated_user_rejected(self):
        assert self._alive([(False, None, "active", None)]) is False

    def test_deleted_user_rejected(self):
        assert self._alive([(True, "2026-01-01 00:00:00", "active", None)]) is False

    def test_inactive_tenant_rejected(self):
        assert self._alive([(True, None, "suspended", None)]) is False

    def test_soft_deleted_tenant_rejected(self):
        # Tenant soft-deletion sets only deleted_at; status stays "active".
        assert self._alive([(True, None, "active", "2026-01-01 00:00:00")]) is False

    def test_trial_tenant_is_alive(self):
        # "trial" is a live platform state, not a deactivation.
        assert self._alive([(True, None, "trial", None)]) is True

    def test_missing_row_rejected(self):
        assert self._alive([None]) is False

    def test_missing_identifiers_rejected(self):
        assert self._alive([(True, None, "active", None)], user_id=None) is False
        assert self._alive([(True, None, "active", None)], tenant_id=None) is False

    def test_db_error_fails_closed(self):
        conn = MagicMock()
        conn.cursor.side_effect = RuntimeError("db down")
        assert self._alive([], conn=conn) is False


# ── 2c + 2e: redact-policy denial and the model allow-list ───────────────


def _remote_app():
    from app.routes.remote import remote_bp

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(remote_bp, url_prefix="/api/remote")
    return app


def _proxy_mock(token):
    proxy = MagicMock()
    proxy.validate_proxy_token.return_value = token
    proxy.resolve_api_key_for_scope.return_value = (
        "sk-pool-key-abcdef",
        "https://api.openai.com",
        7,
        None,
        [],
    )
    return proxy


def _upstream(content=b'{"ok":true}', content_type="application/json", chunks=None):
    resp = MagicMock()
    resp.status_code = 200
    resp.content = content
    resp.headers = {"Content-Type": content_type}
    resp.iter_content.return_value = chunks if chunks is not None else [content]
    return resp


_SSRF_PATH = "app.utils.llm_proxy_url_validator.validate_llm_proxy_url"


def _hermetic_200_patches():
    """Patch DNS-dependent URL validation so 200-path tests are hermetic.

    validate_llm_proxy_url resolves the target host; in sandboxes without
    external DNS that resolution fails and the SSRF guard 403-blocks a URL
    the test means to allow.
    """
    from unittest.mock import MagicMock

    ok = MagicMock()
    ok.allowed = True
    ok.transient = False
    return patch(_SSRF_PATH, lambda *a, **k: ok)


class TestRedactPolicyAndModelAllowList:
    @patch(_HTTP_PATH)
    @patch(_QUOTA_PATH)
    @patch(_PROXY_PATH)
    def test_external_redact_verdict_denies(self, mock_get_proxy, mock_quota_cls, mock_http):
        mock_proxy = _proxy_mock(dict(_EXTERNAL_TOKEN))
        mock_get_proxy.return_value = mock_proxy
        mock_quota_cls.return_value = SimpleNamespace(check_quota=lambda *a, **k: {"allowed": True})
        with patch(_FILTER_PATH, return_value="REDACTED-CONTENT"):
            client = _remote_app().test_client()
            resp = client.post(
                "/api/remote/llm-proxy",
                json={"model": "glm-5", "messages": []},
                headers={"Authorization": "Bearer tok"},
            )
        assert resp.status_code == 403
        assert resp.get_json()["error"]["type"] == "content_redaction_denied"

    @patch(_HTTP_PATH)
    @patch(_QUOTA_PATH)
    @patch(_PROXY_PATH)
    def test_non_external_redact_verdict_still_continues(
        self, mock_get_proxy, mock_quota_cls, mock_http
    ):
        token = dict(_EXTERNAL_TOKEN)
        token.pop("session_type")
        token.pop("redact_policy")
        token.pop("allowed_models")
        mock_get_proxy.return_value = _proxy_mock(token)
        mock_quota_cls.return_value = SimpleNamespace(check_quota=lambda *a, **k: {"allowed": True})
        mock_http.return_value = _upstream()
        with patch(_FILTER_PATH, return_value="REDACTED-CONTENT"), _hermetic_200_patches():
            client = _remote_app().test_client()
            resp = client.post(
                "/api/remote/llm-proxy",
                json={"model": "gpt-4", "messages": []},
                headers={"Authorization": "Bearer tok"},
            )
        assert resp.status_code == 200

    @patch(_HTTP_PATH)
    @patch(_QUOTA_PATH)
    @patch(_PROXY_PATH)
    def test_external_model_outside_allow_list_denied(
        self, mock_get_proxy, mock_quota_cls, mock_http
    ):
        mock_get_proxy.return_value = _proxy_mock(dict(_EXTERNAL_TOKEN))
        mock_quota_cls.return_value = SimpleNamespace(check_quota=lambda *a, **k: {"allowed": True})
        client = _remote_app().test_client()
        resp = client.post(
            "/api/remote/llm-proxy",
            json={"model": "gpt-4", "messages": []},
            headers={"Authorization": "Bearer tok"},
        )
        assert resp.status_code == 403
        assert resp.get_json()["error"]["type"] == "model_not_allowed"

    @patch(_HTTP_PATH)
    @patch(_QUOTA_PATH)
    @patch(_PROXY_PATH)
    def test_external_request_without_model_denied(self, mock_get_proxy, mock_quota_cls, mock_http):
        mock_get_proxy.return_value = _proxy_mock(dict(_EXTERNAL_TOKEN))
        mock_quota_cls.return_value = SimpleNamespace(check_quota=lambda *a, **k: {"allowed": True})
        client = _remote_app().test_client()
        resp = client.post(
            "/api/remote/llm-proxy",
            json={"messages": []},
            headers={"Authorization": "Bearer tok"},
        )
        assert resp.status_code == 403
        assert resp.get_json()["error"]["type"] == "model_not_allowed"

    @patch(_HTTP_PATH)
    @patch(_QUOTA_PATH)
    @patch(_PROXY_PATH)
    def test_external_model_inside_allow_list_proceeds(
        self, mock_get_proxy, mock_quota_cls, mock_http
    ):
        mock_get_proxy.return_value = _proxy_mock(dict(_EXTERNAL_TOKEN))
        mock_quota_cls.return_value = SimpleNamespace(check_quota=lambda *a, **k: {"allowed": True})
        mock_http.return_value = _upstream()
        with _hermetic_200_patches():
            client = _remote_app().test_client()
            resp = client.post(
                "/api/remote/llm-proxy",
                json={"model": "glm-5", "messages": []},
                headers={"Authorization": "Bearer tok"},
            )
        assert resp.status_code == 200

    @patch(_HTTP_PATH)
    @patch(_QUOTA_PATH)
    @patch(_PROXY_PATH)
    def test_external_token_without_allow_list_claim_fails_closed(
        self, mock_get_proxy, mock_quota_cls, mock_http
    ):
        token = dict(_EXTERNAL_TOKEN)
        token.pop("allowed_models")
        mock_get_proxy.return_value = _proxy_mock(token)
        mock_quota_cls.return_value = SimpleNamespace(check_quota=lambda *a, **k: {"allowed": True})
        client = _remote_app().test_client()
        resp = client.post(
            "/api/remote/llm-proxy",
            json={"model": "glm-5", "messages": []},
            headers={"Authorization": "Bearer tok"},
        )
        assert resp.status_code == 403
        assert resp.get_json()["error"]["type"] == "model_not_allowed"


# ── 2g: key-echo guard with hold-back ─────────────────────────────────────

_KEY = b"sk-pool-key-abcdef"


class TestKeyEchoGuard:
    def test_split_key_never_reaches_the_client(self):
        stream = [b'data: {"content":"prefix sk-pool-', b'key-abcdef tail"}\n\ndata: [DONE]\n\n']
        yielded = b""
        with pytest.raises(Exception):
            for chunk in _key_echo_guard(iter(stream), _KEY):
                yielded += chunk
        # Nothing of the occurrence — nor any key prefix — was released, and
        # what was released is a strict prefix of the pre-occurrence text.
        assert _KEY not in yielded
        prefix = b'data: {"content":"prefix '
        assert prefix.startswith(yielded)
        assert len(yielded) <= len(prefix)

    def test_benign_stream_passes_through_byte_exact(self):
        stream = [b"data: one\n\n", b"data: two\n\n", b"data: [DONE]\n\n"]
        out = b"".join(_key_echo_guard(iter(stream), _KEY))
        assert out == b"".join(stream)

    def test_key_in_first_chunk_detected_immediately(self):
        stream = [b"sk-pool-key-abcdef leaked"]
        with pytest.raises(Exception):
            list(_key_echo_guard(iter(stream), _KEY))

    def test_empty_secret_disables_the_guard(self):
        stream = [b"anything at all"]
        assert list(_key_echo_guard(iter(stream), b"")) == stream

    def test_streaming_response_truncates_without_done_and_audits(self, flask_app):
        sse = [
            b'data: {"choices":[{"delta":{"content":"safe start "}}]}\n\n',
            b'data: {"choices":[{"delta":{"content":"the key is sk-pool-',
            b'key-abcdef and now done"}}]}\n\n',
            b"data: [DONE]\n\n",
        ]
        upstream = _upstream(content_type="text/event-stream", chunks=sse)
        with (
            patch("app.modules.workspace.llm_proxy_handler._record_llm_usage") as record,
            patch("app.modules.workspace.llm_proxy_handler._audit_key_echo_block") as audit,
        ):
            with flask_app.test_request_context("/"):
                streamed = _finalize_upstream_response(
                    upstream,
                    b'{"model":"glm-5","stream":true}',
                    session_id="ext-1",
                    user_id=1,
                    provider="openai",
                    content_type="text/event-stream",
                    tenant_id=1,
                    echo_guard_secret=_KEY,
                )
                body = b"".join(streamed.response)

        assert _KEY not in body
        assert b"[DONE]" not in body, "client must see the stream end without [DONE]"
        assert audit.call_count == 1 and audit.call_args[1]["streaming"] is True
        assert record.call_count == 1, "delivered prefix is still charged"

    def test_non_streaming_key_echo_returns_sanitized_502(self, flask_app):
        upstream = _upstream(content=b'{"choices":[{"message":{"content":"sk-pool-key-abcdef"}}]}')
        with patch("app.modules.workspace.llm_proxy_handler._audit_key_echo_block") as audit:
            with flask_app.test_request_context("/"):
                result, status = _finalize_upstream_response(
                    upstream,
                    b'{"model":"glm-5"}',
                    session_id="ext-1",
                    user_id=1,
                    provider="openai",
                    content_type="application/json",
                    tenant_id=1,
                    echo_guard_secret=_KEY,
                )
        assert status == 502
        assert b"sk-pool" not in result.get_data()
        assert audit.call_count == 1 and audit.call_args[1]["streaming"] is False
        assert upstream.close.called

    def test_non_streaming_benign_body_unaffected(self, flask_app):
        body = b'{"ok":true}'
        upstream = _upstream(content=body)
        with flask_app.test_request_context("/"):
            result = _finalize_upstream_response(
                upstream,
                b'{"model":"glm-5"}',
                session_id="ext-1",
                user_id=1,
                provider="openai",
                content_type="application/json",
                tenant_id=1,
                echo_guard_secret=_KEY,
            )
        if isinstance(result, tuple):
            result, status = result
        else:
            status = result.status_code
        assert status == 200
        assert b"ok" in result.get_data()


class TestKeyEchoGuardCrossStreamReconstruction:
    """Two colluding requests must not each release half the key.

    A relay can end one stream with key[:-1] and serve another consisting of
    key[1:]; before the flush fix, the end-of-stream release handed the
    caller n-1 key bytes per stream.
    """

    def test_key_prefix_tail_aborts(self):
        stream = [_KEY[:-1]]
        with pytest.raises(Exception):
            assert list(_key_echo_guard(iter(stream), _KEY))

    def test_key_suffix_stream_aborts(self):
        stream = [_KEY[1:]]
        with pytest.raises(Exception):
            assert list(_key_echo_guard(iter(stream), _KEY))

    def test_half_key_interior_fragment_aborts(self):
        half = _KEY[: len(_KEY) // 2 + 1]
        stream = [b"junk " + half + b" tail"]
        with pytest.raises(Exception):
            assert list(_key_echo_guard(iter(stream), _KEY))

    def test_short_benign_overlap_is_not_key_material(self):
        # A short run (here 5 bytes, under the half-key threshold) that
        # happens to sit inside the key stays out of contract and passes.
        stream = [b"answer uses key-a wording"]
        out = b"".join(_key_echo_guard(iter(stream), _KEY))
        assert b"key-a" in out

    def test_benign_tail_still_passes_through(self):
        # Newlines and framing are not key material: byte-exact passthrough.
        stream = [b"response text", b"data: [DONE]\n\n"]
        out = b"".join(_key_echo_guard(iter(stream), _KEY))
        assert out == b"response textdata: [DONE]\n\n"


class TestKeyEchoGuardHeaderVector:
    def test_key_echo_in_forwarded_header_is_dropped(self, flask_app):
        from app.modules.workspace.llm_proxy_handler import _finalize_upstream_response

        upstream = MagicMock()
        upstream.status_code = 200
        upstream.content = b'{"ok":true}'
        upstream.headers = {
            "Content-Type": "application/json",
            "x-request-id": _KEY.decode(),
        }
        upstream.iter_content.return_value = [b'{"ok":true}']
        with patch("app.modules.workspace.llm_proxy_handler._audit_key_echo_block") as audit:
            with flask_app.test_request_context("/"):
                outcome = _finalize_upstream_response(
                    upstream,
                    b'{"model":"glm-5"}',
                    session_id="ext-1",
                    user_id=1,
                    provider="openai",
                    content_type="application/json",
                    tenant_id=1,
                    echo_guard_secret=_KEY,
                )
        result, status = outcome if isinstance(outcome, tuple) else (outcome, outcome.status_code)
        assert status == 200
        assert "x-request-id" not in result.headers
        assert audit.call_count == 1


class TestKeyEchoGuardBufferedPath:
    def test_buffered_body_containing_key_blocks(self):
        from app.modules.workspace.llm_proxy_handler import _echo_guard_blocks

        with patch("app.modules.workspace.llm_proxy_handler._audit_key_echo_block") as audit:
            assert _echo_guard_blocks(b'{"content":"' + _KEY + b'"}', _KEY, "s", 1, 1) is True
            assert audit.call_count == 1
            assert _echo_guard_blocks(b"clean body", _KEY, "s", 1, 1) is False
            assert _echo_guard_blocks(b"anything", None, "s", 1, 1) is False


class TestBufferedPathFragmentVector:
    def test_buffered_half_key_fragment_blocks(self):
        """Two buffered responses carrying key[:9] / key[9:] must not pass.

        The buffered checks originally tested only the full contiguous key,
        leaving buffered paths (non-streaming and /responses conversion)
        with no fragment threshold at all.
        """
        from app.modules.workspace.llm_proxy_handler import _echo_guard_blocks

        half = _KEY[: len(_KEY) // 2]
        with patch("app.modules.workspace.llm_proxy_handler._audit_key_echo_block") as audit:
            assert _echo_guard_blocks(half, _KEY, "s", 1, 1) is True
            assert audit.call_count == 1

    def test_non_streaming_half_key_fragment_returns_sanitized_502(self, flask_app):
        from app.modules.workspace.llm_proxy_handler import _finalize_upstream_response

        half = _KEY[: len(_KEY) // 2]
        upstream = MagicMock()
        upstream.status_code = 200
        upstream.content = b'{"content":"' + half + b'"}'
        upstream.headers = {"Content-Type": "application/json"}
        upstream.iter_content.return_value = [upstream.content]
        with patch("app.modules.workspace.llm_proxy_handler._audit_key_echo_block") as audit:
            with flask_app.test_request_context("/"):
                outcome = _finalize_upstream_response(
                    upstream,
                    b'{"model":"glm-5"}',
                    session_id="ext-1",
                    user_id=1,
                    provider="openai",
                    content_type="application/json",
                    tenant_id=1,
                    echo_guard_secret=_KEY,
                )
        _, status = outcome if isinstance(outcome, tuple) else (outcome, outcome.status_code)
        assert status == 502
        assert audit.call_count == 1
        assert upstream.close.called

    def test_gateway_external_stream_echo_aborts_and_audits(self, flask_app):
        """Wiring: an external token's gateway-streamed echo aborts + audits."""
        from app.modules.workspace.llm_proxy_handler import _forward_via_gateway

        class Plan:
            target_url = "https://gw.example.com/v1/chat/completions"
            path = "v1/chat/completions"
            gateway_key = _KEY.decode()
            headers = {}
            ssrf_blocked = False
            is_responses = False

            @staticmethod
            def body_transformer(data):
                return data

        upstream = MagicMock()
        upstream.status_code = 200
        upstream.headers = {"Content-Type": "text/event-stream"}
        upstream.iter_content.return_value = [b"data: " + _KEY + b"\n\n"]
        with (
            patch("requests.request", return_value=upstream),
            patch("app.modules.workspace.llm_proxy_handler._audit_key_echo_block") as audit,
        ):
            with flask_app.test_request_context(
                "/", method="POST", data=b"{}", content_type="application/json"
            ):
                response = _forward_via_gateway(
                    Plan(),
                    session_id="ext-1",
                    user_id=1,
                    provider="openai",
                    tenant_id=1,
                    requested_model="glm-5",
                    token_payload={"session_type": "external"},
                )
                body = b"".join(response.response)
        assert _KEY not in body
        assert b"[DONE]" not in body
        assert audit.call_count == 1
