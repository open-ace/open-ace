"""Thin REST client for the OpenSandbox Lifecycle + Execd APIs (#2023).

Two properties carry security weight and are pinned here:

* the **lifecycle** base URL comes from operator config and needs no SSRF guard,
  but the **execd** URL is a *server-supplied string* from
  ``GET /sandboxes/{id}/endpoints/{port}`` that we then POST the entire
  workspace snapshot to — so it is validated against a host allowlist, follows
  no redirects, and has its server-supplied headers filtered;
* ``GET /sandboxes`` is paginated (``pageSize`` defaults to 20 upstream), so a
  single-request sweep would silently miss every sandbox past the first page.
"""

from __future__ import annotations

import json

import pytest

from app.modules.workspace.autonomous.sandbox.opensandbox.client import (
    EXECD_TOKEN_HEADER,
    LIFECYCLE_API_KEY_HEADER,
    HttpOpenSandboxApi,
    OpenSandboxApiError,
    iter_sse_events,
)
from app.modules.workspace.autonomous.sandbox.opensandbox.config import Attestations, EndpointConfig

pytestmark = [pytest.mark.regression, pytest.mark.issue(2023)]

_DIGEST = "ghcr.io/open-ace/agent@sha256:" + "a" * 64


class _Response:
    def __init__(self, status_code=200, body=None, text="", content=b"", headers=None):
        self.status_code = status_code
        self._body = body
        self.text = text or (json.dumps(body) if body is not None else "")
        self.content = content
        self.headers = headers or {}

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body

    def iter_lines(self):
        yield from self.content.split(b"\n")


class _Session:
    """Records every call so tests can assert on headers, proxies and URLs."""

    def __init__(self, responses=None):
        self.calls: list[dict] = []
        self._responses = list(responses or [])

    def request(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        if self._responses:
            return self._responses.pop(0)
        return _Response(200, {})


def _endpoint(**overrides) -> EndpointConfig:
    base = {
        "tier": "gvisor",
        "base_url": "http://osb.open-ace.svc.cluster.local:8080/v1",
        "api_key_env": "OSB_KEY",
        "runtime_class": "gvisor",
        "default_image": _DIGEST,
        "execd_endpoint_host_allowlist": ("osb.open-ace.svc.cluster.local",),
        "attestations": Attestations(egress_enforced=True, egress_mode_dns_nft=True),
    }
    base.update(overrides)
    return EndpointConfig(**base)


@pytest.fixture(autouse=True)
def _api_key(monkeypatch):
    monkeypatch.setenv("OSB_KEY", "secret-key")


def _api(session, endpoint=None) -> HttpOpenSandboxApi:
    return HttpOpenSandboxApi(endpoint or _endpoint(), session=session)


# ── auth, proxies, errors ─────────────────────────────────────────────


def test_every_lifecycle_call_sends_api_key_header_and_disables_proxies():
    session = _Session([_Response(202, {"id": "sb-1", "status": {"state": "Running"}})])
    _api(session).create_sandbox({"image": {"uri": _DIGEST}})
    call = session.calls[0]
    assert call["headers"][LIFECYCLE_API_KEY_HEADER] == "secret-key"
    # proxies=None avoids the gevent RecursionError documented in CLAUDE.md #2237.
    assert call["proxies"] == {"http": None, "https": None}


def test_execd_calls_use_the_apikey_header_not_bearer():
    # Upstream defines AccessToken as apiKey in header X-EXECD-ACCESS-TOKEN.
    # An Authorization: Bearer would 401 on every execd call.
    assert EXECD_TOKEN_HEADER == "X-EXECD-ACCESS-TOKEN"


def test_non_2xx_raises_with_status_and_upstream_code():
    session = _Session([_Response(400, {"code": "INVALID_REQUEST", "message": "bad image"})])
    with pytest.raises(OpenSandboxApiError) as exc:
        _api(session).create_sandbox({})
    assert exc.value.status_code == 400
    assert exc.value.code == "INVALID_REQUEST"
    assert "bad image" in str(exc.value)


def test_delete_sandbox_treats_404_as_success():
    # destroy() must be idempotent per the #2022 contract.
    session = _Session([_Response(404, {"code": "NOT_FOUND", "message": "gone"})])
    _api(session).delete_sandbox("sb-1")


def test_get_sandbox_returns_none_on_404():
    session = _Session([_Response(404, {"code": "NOT_FOUND", "message": "gone"})])
    assert _api(session).get_sandbox("sb-1") is None


# ── pagination ────────────────────────────────────────────────────────


def test_list_sandboxes_follows_pagination_until_a_short_page():
    page1 = {"sandboxes": [{"id": f"sb-{i}"} for i in range(20)]}
    page2 = {"sandboxes": [{"id": "sb-20"}]}
    session = _Session([_Response(200, page1), _Response(200, page2)])
    api = HttpOpenSandboxApi(_endpoint(), session=session, page_size=20)
    assert len(api.list_sandboxes()) == 21
    assert session.calls[0]["params"]["page"] == 1
    assert session.calls[1]["params"]["page"] == 2


def test_list_sandboxes_stops_at_the_max_page_guard():
    full = {"sandboxes": [{"id": f"sb-{i}"} for i in range(20)]}
    session = _Session([_Response(200, full) for _ in range(200)])
    api = HttpOpenSandboxApi(_endpoint(), session=session, page_size=20, max_list_pages=3)
    api.list_sandboxes()
    assert len(session.calls) == 3


# ── execd endpoint guard ──────────────────────────────────────────────


def test_execd_endpoint_off_allowlist_is_refused():
    # The endpoint URL is chosen by the SERVER. A compromised or misconfigured
    # server could redirect the whole workspace snapshot, plus the execd token,
    # off-cluster.
    session = _Session(
        [_Response(200, {"endpoint": "http://evil.example.com/sandboxes/sb-1/port/44772"})]
    )
    with pytest.raises(OpenSandboxApiError):
        _api(session).execd_base_url("sb-1")


def test_execd_endpoint_on_allowlist_is_accepted():
    session = _Session(
        [
            _Response(
                200,
                {"endpoint": "http://osb.open-ace.svc.cluster.local/sandboxes/sb-1/port/44772"},
            )
        ]
    )
    assert "osb.open-ace.svc.cluster.local" in _api(session).execd_base_url("sb-1")


def test_execd_endpoint_wildcard_allowlist_matches_subdomain():
    endpoint = _endpoint(execd_endpoint_host_allowlist=("*.open-ace.svc.cluster.local",))
    session = _Session(
        [_Response(200, {"endpoint": "http://sb-1.open-ace.svc.cluster.local/port/44772"})]
    )
    assert _api(session, endpoint).execd_base_url("sb-1")


def test_execd_requests_disable_redirects():
    session = _Session(
        [
            _Response(200, {"endpoint": "http://osb.open-ace.svc.cluster.local/p"}),
            _Response(200, {"running": False, "exit_code": 0}),
        ]
    )
    _api(session).command_status("sb-1", "cmd-1")
    assert session.calls[-1]["allow_redirects"] is False


def test_server_supplied_endpoint_headers_are_filtered_by_allowlist():
    session = _Session(
        [
            _Response(
                200,
                {
                    "endpoint": "http://osb.open-ace.svc.cluster.local/p",
                    "headers": {
                        "X-Sandbox-Access-Token": "tok",
                        "Authorization": "Bearer stolen",
                        "X-Evil": "1",
                    },
                },
            ),
            _Response(200, {"running": False, "exit_code": 0}),
        ]
    )
    _api(session).command_status("sb-1", "cmd-1")
    headers = session.calls[-1]["headers"]
    assert headers.get("X-Sandbox-Access-Token") == "tok"
    assert "X-Evil" not in headers
    assert headers.get("Authorization") != "Bearer stolen"


# ── SSE ───────────────────────────────────────────────────────────────


def test_sse_parser_yields_typed_events_and_skips_ping_and_comments():
    raw = (
        b": keepalive\n\n"
        b'data: {"type":"init"}\n\n'
        b'data: {"type":"ping"}\n\n'
        b'data: {"type":"stdout","text":"hello\\n"}\n\n'
        b'data: {"type":"execution_complete","execution_time":12}\n\n'
    )
    assert [e["type"] for e in iter_sse_events(_Response(content=raw))] == [
        "init",
        "stdout",
        "execution_complete",
    ]


def test_sse_parser_joins_multiline_data():
    raw = b'data: {"type":"stdout",\ndata: "text":"x"}\n\n'
    assert list(iter_sse_events(_Response(content=raw)))[0]["text"] == "x"


def test_sse_parser_drops_a_truncated_trailing_event_without_raising():
    raw = b'data: {"type":"stdout","text":"a"}\n\ndata: {"type":"std'
    assert [e["type"] for e in iter_sse_events(_Response(content=raw))] == ["stdout"]


# ── files ─────────────────────────────────────────────────────────────


def test_upload_file_sends_metadata_part_before_file_part():
    session = _Session(
        [
            _Response(200, {"endpoint": "http://osb.open-ace.svc.cluster.local/p"}),
            _Response(200, {}),
        ]
    )
    _api(session).upload_file("sb-1", "/workspace/a.py", b"x", mode=0o644)
    files = session.calls[-1]["files"]
    names = [name for name, _ in files]
    assert names == ["metadata", "file"]
    metadata = json.loads(files[0][1][1])
    assert metadata["path"] == "/workspace/a.py"
    assert metadata["mode"] == 0o644


def test_download_file_returns_raw_bytes():
    session = _Session(
        [
            _Response(200, {"endpoint": "http://osb.open-ace.svc.cluster.local/p"}),
            _Response(200, content=b"payload"),
        ]
    )
    assert _api(session).download_file("sb-1", "/workspace/a.py") == b"payload"


# ── PTY ───────────────────────────────────────────────────────────────


def test_create_pty_session_returns_the_session_id():
    session = _Session(
        [
            _Response(200, {"endpoint": "http://osb.open-ace.svc.cluster.local/p"}),
            _Response(200, {"session_id": "pty-1"}),
        ]
    )
    assert _api(session).create_pty_session("sb-1", cwd="/workspace") == "pty-1"


def test_pty_ws_url_is_pipe_mode_and_carries_the_since_offset():
    session = _Session([_Response(200, {"endpoint": "http://osb.open-ace.svc.cluster.local/p"})])
    url = _api(session).pty_ws_url("sb-1", "pty-1", since=42)
    assert url.startswith("ws://")
    assert "pty=0" in url  # pipe mode: stderr stays a separate stream
    assert "since=42" in url
