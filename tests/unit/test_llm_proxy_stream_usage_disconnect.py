"""Quota bypass fix: usage must be recorded when a streamed response is cut short.

A client that reads every content delta and then drops the connection just
before the final chunk (which carries `usage` and [DONE]) receives the full
generation while zero usage is recorded. These tests pin the fix: usage
recording runs in a `finally`, a stream without a usage block is charged a
byte-derived estimate capped by the requested max_tokens, and the upstream
response is closed so provider-side generation stops.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from flask import Flask


@pytest.fixture
def flask_app():
    """Minimal Flask app for request-context tests."""
    app = Flask(__name__)
    app.config["TESTING"] = True
    return app


_RECORD_PATH = "app.modules.workspace.llm_proxy_handler._record_llm_usage"

_SSE_NO_USAGE = b"".join(
    [
        b'data: {"choices":[{"delta":{"content":"The answer is 42."}}]}\n\n',
        b"data: [DONE]\n\n",
    ]
)


def _sse_response(chunks, headers=None):
    response = MagicMock()
    response.status_code = 200
    response.headers = headers or {"Content-Type": "text/event-stream"}
    response.iter_content.return_value = list(chunks)
    return response


class TestStreamUsageOnDisconnect:
    def test_disconnect_after_deltas_records_usage_and_closes(self, flask_app):
        """Read one delta, then drop the connection: usage still recorded."""
        from app.modules.workspace.llm_proxy_handler import _finalize_upstream_response

        upstream = _sse_response([_SSE_NO_USAGE[:60], _SSE_NO_USAGE[60:]])
        with patch(_RECORD_PATH) as record:
            with flask_app.test_request_context("/"):
                streamed = _finalize_upstream_response(
                    upstream,
                    b'{"model":"gpt-4.1","max_tokens":512,"stream":true}',
                    session_id="session-1",
                    user_id=1,
                    provider="openai",
                    content_type="text/event-stream",
                    tenant_id=1,
                )
                iterator = streamed.response
                next(iterator)
                # Client disconnect: Werkzeug closes the response iterator,
                # which raises GeneratorExit at the suspended yield.
                iterator.close()

        assert record.call_count == 1, "usage must be recorded on disconnect"
        fallback = record.call_args[1]["fallback_output_tokens"]
        assert fallback is not None and fallback >= 1
        assert fallback <= 512, "estimate must be capped by the requested max_tokens"
        assert upstream.close.called, "upstream response must be closed"

    def test_normal_drain_records_usage_and_closes(self, flask_app):
        """A fully drained stream keeps recording usage once, and closes."""
        from app.modules.workspace.llm_proxy_handler import _finalize_upstream_response

        upstream = _sse_response([_SSE_NO_USAGE])
        with patch(_RECORD_PATH) as record:
            with flask_app.test_request_context("/"):
                streamed = _finalize_upstream_response(
                    upstream,
                    b'{"model":"gpt-4.1","stream":true}',
                    session_id="session-1",
                    user_id=1,
                    provider="openai",
                    content_type="text/event-stream",
                    tenant_id=1,
                )
                assert b"".join(streamed.response) == _SSE_NO_USAGE

        assert record.call_count == 1
        assert record.call_args[1]["fallback_output_tokens"] is not None
        assert upstream.close.called

    def test_upstream_error_stream_still_closes(self, flask_app):
        """An exception from the upstream iterator must not leak the response."""
        from app.modules.workspace.llm_proxy_handler import _finalize_upstream_response

        def exploding():
            yield b"data: partial\n\n"
            raise RuntimeError("upstream reset")

        upstream = _sse_response([])
        upstream.iter_content.return_value = exploding()
        with patch(_RECORD_PATH) as record:
            with flask_app.test_request_context("/"):
                streamed = _finalize_upstream_response(
                    upstream,
                    b'{"model":"gpt-4.1","stream":true}',
                    session_id="session-1",
                    user_id=1,
                    provider="openai",
                    content_type="text/event-stream",
                    tenant_id=1,
                )
                with pytest.raises(RuntimeError):
                    b"".join(streamed.response)

        assert record.call_count == 1
        assert upstream.close.called


class TestStreamUsageFallbackEstimate:
    def test_counts_payload_bytes_at_four_per_token(self):
        from app.modules.workspace.llm_proxy_handler import _stream_usage_fallback

        # Only `data:` payload bytes count; SSE event/blank framing does not.
        stream = b"data: " + b"x" * 400 + b"\n\n" + b"event: ping\n\n"
        estimate = _stream_usage_fallback(stream, None, "text/event-stream")
        assert estimate == 100

    def test_capped_by_requested_max_tokens(self):
        from app.modules.workspace.llm_proxy_handler import _stream_usage_fallback

        stream = b"data: " + b"x" * 400 + b"\n\n"
        estimate = _stream_usage_fallback(stream, b'{"max_tokens":50}', "text/event-stream")
        assert estimate == 50

    def test_max_completion_tokens_wins_when_present(self):
        from app.modules.workspace.llm_proxy_handler import _stream_usage_fallback

        stream = b"data: " + b"x" * 400 + b"\n\n"
        estimate = _stream_usage_fallback(
            stream, b'{"max_tokens":50,"max_completion_tokens":25}', "text/event-stream"
        )
        assert estimate == 25

    def test_none_for_non_streaming_and_empty_streams(self):
        from app.modules.workspace.llm_proxy_handler import _stream_usage_fallback

        assert _stream_usage_fallback(b"{}", None, "application/json") is None
        assert _stream_usage_fallback(b"", b"{}", "text/event-stream") is None


class TestRecordLlmUsageEstimateCharge:
    def test_stream_without_usage_charges_the_estimate(self, monkeypatch):
        """No usage block in the stream: the fallback charge reaches the session."""
        from app.modules.workspace.llm_proxy_handler import _record_llm_usage

        class FakeSessionManager:
            def __init__(self):
                self.session = SimpleNamespace(
                    message_count=0,
                    request_count=0,
                    total_tokens=0,
                    total_input_tokens=0,
                    total_output_tokens=0,
                    tool_name="qwen-code",
                    host_name="localhost",
                    model=None,
                    tenant_id=1,
                )
                self.calls = []

            def get_session(self, session_id, include_messages=False):
                return self.session

            def update_session_fields(
                self, session_id, fields, tenant_id=None, require_tenant=False
            ):
                for key, value in fields.items():
                    setattr(self.session, key, value)
                return True

            _FIELDS = {
                "message_delta": "message_count",
                "request_delta": "request_count",
                "total_tokens_delta": "total_tokens",
                "total_input_delta": "total_input_tokens",
                "total_output_delta": "total_output_tokens",
            }

            def increment_session_usage(self, session_id, **kwargs):
                for delta, name in self._FIELDS.items():
                    setattr(
                        self.session,
                        name,
                        getattr(self.session, name, 0) + kwargs.get(delta, 0),
                    )
                return True

            def append_transcript_message(self, **kwargs):
                self.calls.append(kwargs)
                return SimpleNamespace(_was_inserted=True)

        fake_sm = FakeSessionManager()
        monkeypatch.setattr(
            "app.modules.workspace.session_manager.get_session_manager", lambda: fake_sm
        )
        monkeypatch.setattr(
            "app.modules.governance.quota_manager.QuotaManager",
            lambda: SimpleNamespace(record_usage=lambda **kwargs: None),
        )
        monkeypatch.setattr(
            "app.repositories.daily_stats_repo.DailyStatsRepository",
            lambda: SimpleNamespace(refresh_stats=lambda: None),
        )

        _record_llm_usage(
            content=_SSE_NO_USAGE,
            session_id="proxy-1",
            user_id=3,
            provider="openai",
            content_type="text/event-stream",
            request_body=b'{"model":"gpt-4.1","stream":true}',
            fallback_output_tokens=42,
        )

        assert fake_sm.session.request_count == 1
        assert fake_sm.session.total_output_tokens == 42
        assert fake_sm.session.total_input_tokens == 0

    def test_stream_without_usage_and_no_fallback_stays_free(self, monkeypatch):
        """Without a streaming fallback nothing reaches the session counters."""
        from app.modules.workspace import session_manager as sm_module
        from app.modules.workspace.llm_proxy_handler import _record_llm_usage

        increments = []

        class FakeSessionManager:
            def get_session(self, session_id, include_messages=False):
                return None

            def increment_session_usage(self, session_id, **kwargs):
                increments.append(kwargs)
                return True

        monkeypatch.setattr(sm_module, "get_session_manager", lambda: FakeSessionManager())

        _record_llm_usage(
            content=b"not-json",
            session_id="proxy-2",
            user_id=3,
            provider="openai",
            content_type="application/json",
            request_body=b"{}",
        )

        assert increments == [], "no evidence and no fallback must charge nothing"


class TestPartialStreamUsageEvidence:
    """A partial stream can carry evidence that is not a chargeable total.

    OpenAI `stream_options.include_usage` puts `"usage": null` on every
    non-final chunk; Anthropic's message_start reports input tokens with
    output ~ 0. Reading all deltas and disconnecting before the final usage
    event must NOT zero out the output charge.
    """

    def _record_with_fake_session(
        self, monkeypatch, content, fallback, request_body, **record_kwargs
    ):
        from app.modules.workspace.llm_proxy_handler import _record_llm_usage

        class FakeSessionManager:
            def __init__(self):
                self.session = SimpleNamespace(
                    message_count=0,
                    request_count=0,
                    total_tokens=0,
                    total_input_tokens=0,
                    total_output_tokens=0,
                    tool_name="qwen-code",
                    host_name="localhost",
                    model=None,
                    tenant_id=1,
                )

            def get_session(self, session_id, include_messages=False):
                return self.session

            def update_session_fields(
                self, session_id, fields, tenant_id=None, require_tenant=False
            ):
                for key, value in fields.items():
                    setattr(self.session, key, value)
                return True

            def increment_session_usage(self, session_id, **kwargs):
                for delta, name in (
                    ("message_delta", "message_count"),
                    ("request_delta", "request_count"),
                    ("total_tokens_delta", "total_tokens"),
                    ("total_input_delta", "total_input_tokens"),
                    ("total_output_delta", "total_output_tokens"),
                ):
                    setattr(
                        self.session,
                        name,
                        getattr(self.session, name, 0) + kwargs.get(delta, 0),
                    )
                return True

            def append_transcript_message(self, **kwargs):
                return SimpleNamespace(_was_inserted=True)

        fake = FakeSessionManager()
        monkeypatch.setattr(
            "app.modules.workspace.session_manager.get_session_manager", lambda: fake
        )
        monkeypatch.setattr(
            "app.modules.governance.quota_manager.QuotaManager",
            lambda: SimpleNamespace(record_usage=lambda **kwargs: None),
        )
        monkeypatch.setattr(
            "app.repositories.daily_stats_repo.DailyStatsRepository",
            lambda: SimpleNamespace(refresh_stats=lambda: None),
        )
        _record_llm_usage(
            content=content,
            session_id="proxy-partial",
            user_id=3,
            provider="openai",
            content_type="text/event-stream",
            request_body=request_body,
            fallback_output_tokens=fallback,
            **record_kwargs,
        )
        return fake.session

    def test_null_usage_chunks_still_charge_the_estimate(self, monkeypatch):
        sse = (
            b'data: {"choices":[{"delta":{"content":"The answer is 42."}}],"usage":null}\n\n'
            b"data: [DONE]\n\n"
        )
        session = self._record_with_fake_session(
            monkeypatch,
            sse,
            42,
            b'{"model":"gpt-4.1","stream":true}',
            stream_completed=False,
        )
        assert (
            session.total_output_tokens == 42
        ), "a usage:null partial stream must not zero out the output charge"

    def test_interim_usage_on_disconnect_uses_the_larger_charge(self, monkeypatch):
        # A stream cut before its end carries interim usage (input known,
        # output partial); the estimate merges with what was already seen.
        sse = (
            b'data: {"choices":[{"delta":{"content":"partial answer"}}],"usage":'
            b'{"prompt_tokens":100,"completion_tokens":2}}\n\n'
        )
        session = self._record_with_fake_session(
            monkeypatch,
            sse,
            55,
            b'{"model":"gpt-4.1","stream":true}',
            stream_completed=False,
        )
        assert session.total_input_tokens == 100
        assert session.total_output_tokens == max(2, 55)

    def test_interim_usage_on_completed_stream_is_trusted(self, monkeypatch):
        # Same interim-shaped event, but the stream ran to its end: the
        # parsed totals stand and the fallback must not inflate them.
        sse = (
            b'data: {"choices":[{"delta":{"content":"partial answer"}}],"usage":'
            b'{"prompt_tokens":100,"completion_tokens":2}}\n\ndata: [DONE]\n\n'
        )
        session = self._record_with_fake_session(
            monkeypatch,
            sse,
            55,
            b'{"model":"gpt-4.1","stream":true}',
            stream_completed=True,
        )
        assert session.total_input_tokens == 100
        assert session.total_output_tokens == 2

    def test_final_positive_usage_is_not_inflated(self, monkeypatch):
        sse = (
            b'data: {"choices":[{"delta":{}}],"usage":{"prompt_tokens":7,"completion_tokens":9},'
            b'"final":true}\n\n'
        )
        session = self._record_with_fake_session(
            monkeypatch, sse, 99, b'{"model":"gpt-4.1","stream":true}'
        )
        assert session.total_output_tokens == 9, "a final usage event charges itself"
        assert session.total_input_tokens == 7
