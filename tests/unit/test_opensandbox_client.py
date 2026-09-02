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
    INGRESS_ROUTE_HEADER,
    LIFECYCLE_API_KEY_HEADER,
    SECURE_ACCESS_HEADER,
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
        "execd_token_env": "",
        "tier": "gvisor",
        "base_url": "http://osb.open-ace.svc.cluster.local:8080/v1",
        "api_key_env": "OSB_KEY",
        "runtime_class": "kata-qemu",
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


def test_execd_calls_actually_send_the_execd_token(monkeypatch):
    # Upstream defines AccessToken as apiKey in header X-EXECD-ACCESS-TOKEN, and
    # refusal 9 makes execd_token_required mandatory for a usable tier — so a
    # client that never attaches it 401s on every execd call.
    monkeypatch.setenv("OSB_EXECD_TOKEN", "execd-secret")
    session = _Session(
        [
            _Response(200, {"endpoint": "http://osb.open-ace.svc.cluster.local/p"}),
            _Response(200, {"running": False, "exit_code": 0}),
        ]
    )
    _api(session, _endpoint(execd_token_env="OSB_EXECD_TOKEN")).command_status("sb-1", "cmd-1")
    assert session.calls[-1]["headers"][EXECD_TOKEN_HEADER] == "execd-secret"


def test_server_supplied_per_sandbox_token_overrides_the_static_one(monkeypatch):
    monkeypatch.setenv("OSB_EXECD_TOKEN", "static")
    session = _Session(
        [
            _Response(
                200,
                {
                    "endpoint": "http://osb.open-ace.svc.cluster.local/p",
                    "headers": {EXECD_TOKEN_HEADER: "per-sandbox"},
                },
            ),
            _Response(200, {"running": False, "exit_code": 0}),
        ]
    )
    _api(session, _endpoint(execd_token_env="OSB_EXECD_TOKEN")).command_status("sb-1", "cmd-1")
    assert session.calls[-1]["headers"][EXECD_TOKEN_HEADER] == "per-sandbox"


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
    # The 404 was consumed by exactly one DELETE: no retry, no other traffic.
    assert [c["method"] for c in session.calls] == ["DELETE"]


def test_get_sandbox_returns_none_on_404():
    session = _Session([_Response(404, {"code": "NOT_FOUND", "message": "gone"})])
    assert _api(session).get_sandbox("sb-1") is None


# ── pagination ────────────────────────────────────────────────────────


def test_list_sandboxes_reads_the_upstream_items_key():
    # Upstream's ListSandboxesResponse is {items, pagination}, both required.
    body = {"items": [{"id": "sb-1"}], "pagination": {"hasNextPage": False}}
    session = _Session([_Response(200, body)])
    assert len(_api(session).list_sandboxes()) == 1


def test_list_sandboxes_stops_on_has_next_page_false():
    page1 = {"items": [{"id": f"sb-{i}"} for i in range(20)], "pagination": {"hasNextPage": True}}
    page2 = {"items": [{"id": "sb-20"}], "pagination": {"hasNextPage": False}}
    session = _Session([_Response(200, page1), _Response(200, page2)])
    api = HttpOpenSandboxApi(_endpoint(), session=session, page_size=20)
    assert len(api.list_sandboxes()) == 21
    assert session.calls[0]["params"]["page"] == 1
    assert session.calls[1]["params"]["page"] == 2


def test_list_sandboxes_keeps_paging_when_the_server_clamps_page_size():
    # A server that clamps pageSize to its own maximum returns a "short" page on
    # request 1. Trusting the short-page heuristic there would stop the orphan
    # sweep after one page — silently, in the situation reconciliation exists for.
    page1 = {"items": [{"id": f"sb-{i}"} for i in range(5)], "pagination": {"hasNextPage": True}}
    page2 = {"items": [{"id": "sb-5"}], "pagination": {"hasNextPage": False}}
    session = _Session([_Response(200, page1), _Response(200, page2)])
    api = HttpOpenSandboxApi(_endpoint(), session=session, page_size=20)
    assert len(api.list_sandboxes()) == 6


def test_list_sandboxes_falls_back_to_short_page_when_pagination_is_absent():
    page1 = {"items": [{"id": f"sb-{i}"} for i in range(20)]}
    page2 = {"items": [{"id": "sb-20"}]}
    session = _Session([_Response(200, page1), _Response(200, page2)])
    api = HttpOpenSandboxApi(_endpoint(), session=session, page_size=20)
    assert len(api.list_sandboxes()) == 21


def test_list_sandboxes_sends_the_metadata_filter():
    # Without it the orphan sweep lists every sandbox on a shared server and
    # would destroy other teams' and other products' workloads.
    session = _Session([_Response(200, {"items": [], "pagination": {"hasNextPage": False}})])
    _api(session).list_sandboxes({"openace.provider": "opensandbox"})
    assert "openace.provider" in session.calls[0]["params"]["metadata"]


def test_list_sandboxes_stops_at_the_max_page_guard():
    full = {"items": [{"id": f"sb-{i}"} for i in range(20)], "pagination": {"hasNextPage": True}}
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
                        # Upstream's real per-sandbox credential header.
                        SECURE_ACCESS_HEADER: "tok",
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
    assert headers.get(SECURE_ACCESS_HEADER) == "tok"
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
    assert metadata["mode"] == 644  # octal digits on the wire; see _wire_mode


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


# ── egress sidecar is a separate service (B5) ─────────────────────────


def test_egress_policy_targets_the_sidecar_port_not_execd():
    # The sidecar is its own service on its own port; GET /policy against
    # execd's 44772 is a 404, and the §5.3 probe that upgrades
    # NETWORK_EGRESS_POLICY from an operator's word to a verified fact would
    # never run.
    session = _Session(
        [
            _Response(200, {"endpoint": "http://osb.open-ace.svc.cluster.local/egress"}),
            _Response(200, {"defaultAction": "deny", "enforcementMode": "dns+nft"}),
        ]
    )
    policy = _api(session).egress_policy("sb-1")
    assert "/endpoints/18080" in session.calls[0]["url"]
    assert policy["enforcementMode"] == "dns+nft"


def test_egress_auth_header_survives_the_endpoint_header_filter():
    session = _Session(
        [
            _Response(
                200,
                {
                    "endpoint": "http://osb.open-ace.svc.cluster.local/egress",
                    "headers": {"OPENSANDBOX-EGRESS-AUTH": "egress-tok", "X-Evil": "1"},
                },
            ),
            _Response(200, {"defaultAction": "deny", "enforcementMode": "dns+nft"}),
        ]
    )
    _api(session).egress_policy("sb-1")
    assert session.calls[-1]["headers"]["OPENSANDBOX-EGRESS-AUTH"] == "egress-tok"
    assert "X-Evil" not in session.calls[-1]["headers"]


# ── upload ownership (B15) ────────────────────────────────────────────


def test_upload_file_sets_owner_group_and_mode():
    # execd may run as root; a root-owned 0644 file is unwritable by the
    # non-root agent that refusal 9 mandates, so the agent could not edit its
    # own workspace.
    session = _Session(
        [
            _Response(200, {"endpoint": "http://osb.open-ace.svc.cluster.local/p"}),
            _Response(200, {}),
        ]
    )
    _api(session).upload_file("sb-1", "/workspace/a.py", b"x", mode=0o644)
    metadata = json.loads(session.calls[-1]["files"][0][1][1])
    # The OCTAL DIGITS as an integer, not Python's 0o644 (= 420). execd chmods
    # via strconv.ParseUint(fmt.Sprint(mode), 8, ...), so 420 reads back as
    # 0o420 -> `-r---w----`: the upload "succeeds" and the agent cannot edit its
    # own workspace. This assertion previously pinned 0o644 and so encoded the
    # very bug it was meant to guard. Verified against a real execd.
    assert metadata["mode"] == 644
    assert metadata["owner"] == "openace"
    assert metadata["group"] == "openace"


# ── streamed response lifetime (B20) ──────────────────────────────────


def test_run_command_closes_the_response_when_the_iterator_is_abandoned():
    # stream() abandons this iterator on timeout or stop; without the finally
    # the pooled connection is never released and a long-lived scheduler
    # process exhausts the pool.
    closed: list[bool] = []

    class _Streamed(_Response):
        def close(self):
            closed.append(True)

    streamed = _Streamed(200, content=b'data: {"type":"stdout","text":"a"}\n\n')
    session = _Session(
        [_Response(200, {"endpoint": "http://osb.open-ace.svc.cluster.local/p"}), streamed]
    )
    events = _api(session).run_command("sb-1", {"command": "ls"})
    next(events)
    events.close()
    assert closed == [True]


def test_malformed_midstream_sse_event_surfaces_as_an_error_not_silence():
    raw = b'data: {"type":"stdout","text":"a"}\n\ndata: {broken\n\ndata: {"type":"execution_complete"}\n\n'
    kinds = [e["type"] for e in iter_sse_events(_Response(content=raw))]
    assert kinds == ["stdout", "error", "execution_complete"]


def test_lifecycle_requests_do_not_follow_redirects():
    """Requests strips `Authorization` across hosts — not arbitrary headers.

    OPEN-SANDBOX-API-KEY is a custom header, so a 30x from the lifecycle host
    would carry the API key to wherever it pointed. The base_url is
    operator-configured and already correct; there is no redirect worth
    following.
    """
    session = _Session([_Response(200, {"id": "sb-1", "status": {"state": "Running"}})])
    _api(session).get_sandbox("sb-1")
    assert session.calls[-1]["allow_redirects"] is False
    assert session.calls[-1]["headers"][LIFECYCLE_API_KEY_HEADER] == "secret-key"


def test_the_pty_websocket_gets_the_same_execd_credentials_as_http(monkeypatch):
    """execd_headers() must not be a weaker credential set than _execd_request.

    The HTTP calls added the configured static token; execd_headers returned
    only the server-resolved headers. On a deployment authenticating execd with
    EXECD_ACCESS_TOKEN that made every PTY upgrade unauthenticated — HTTP fine,
    WebSocket rejected, which reads as a transport bug rather than a missing
    credential.
    """
    monkeypatch.setenv("OSB_EXECD_TOKEN", "execd-secret")
    session = _Session([_Response(200, {"endpoint": "http://osb.open-ace.svc.cluster.local/p"})])
    api = _api(session, _endpoint(execd_token_env="OSB_EXECD_TOKEN"))
    assert api.execd_headers("sb-1")[EXECD_TOKEN_HEADER] == "execd-secret"


def test_a_per_sandbox_token_still_wins_for_the_websocket(monkeypatch):
    monkeypatch.setenv("OSB_EXECD_TOKEN", "static")
    session = _Session(
        [
            _Response(
                200,
                {
                    "endpoint": "http://osb.open-ace.svc.cluster.local/p",
                    "headers": {EXECD_TOKEN_HEADER: "per-sandbox"},
                },
            )
        ]
    )
    api = _api(session, _endpoint(execd_token_env="OSB_EXECD_TOKEN"))
    assert api.execd_headers("sb-1")[EXECD_TOKEN_HEADER] == "per-sandbox"


def test_the_real_secure_access_header_survives_the_endpoint_filter(monkeypatch):
    """The per-sandbox credential must reach execd, under UPSTREAM's name.

    The allowlist previously carried two invented names and not
    `OpenSandbox-Secure-Access`, which is what upstream actually sends
    (services/constants.py OPEN_SANDBOX_SECURE_ACCESS_HEADER). The filter
    stripped the real header, so under gateway mode every execd call would
    arrive uncredentialed and the gateway would reject it — while the fake
    server sent one of the invented names, so every test agreed with itself.
    """
    monkeypatch.setenv("OSB_EXECD_TOKEN", "static")
    session = _Session(
        [
            _Response(
                200,
                {
                    "endpoint": "http://osb.open-ace.svc.cluster.local/p",
                    "headers": {SECURE_ACCESS_HEADER: "per-sandbox-token"},
                },
            )
        ]
    )
    api = _api(session, _endpoint(execd_token_env="OSB_EXECD_TOKEN"))
    assert api.execd_headers("sb-1")[SECURE_ACCESS_HEADER] == "per-sandbox-token"


def test_an_invented_access_token_header_is_still_stripped():
    """The allowlist stays an allowlist — only names upstream really sends."""
    session = _Session(
        [
            _Response(
                200,
                {
                    "endpoint": "http://osb.open-ace.svc.cluster.local/p",
                    "headers": {"X-Sandbox-Access-Token": "made-up", "X-Evil": "no"},
                },
            )
        ]
    )
    headers = _api(session).execd_headers("sb-1")
    assert "X-Sandbox-Access-Token" not in headers
    assert "X-Evil" not in headers


def _header_mode_endpoint_body(sandbox_id: str, port: int = 44772) -> dict:
    """Exactly what upstream returns under `route.mode = "header"`.

    services/helpers.py::format_ingress_endpoint — the endpoint is the BARE
    gateway host and the sandbox identity travels only in the routing header.
    Tests that supply only the auth header model a response the shipped
    ConfigMaps never produce, which is how the routing header went missing.
    """
    return {
        "endpoint": "http://opensandbox-gateway.open-ace.example",
        "headers": {
            INGRESS_ROUTE_HEADER: f"{sandbox_id}-{port}",
            SECURE_ACCESS_HEADER: "per-sandbox-token",
        },
    }


def test_gateway_header_routing_survives_the_endpoint_filter(monkeypatch):
    """Without the routing header the gateway cannot tell which sandbox we mean.

    It falls back to the Host header, tries to parse `<id>-<port>` out of the
    gateway's own name, and returns 400 — so every upload/exec/PTY call fails
    and no run completes.
    """
    monkeypatch.setenv("OSB_EXECD_TOKEN", "static")
    session = _Session(
        [
            _Response(200, _header_mode_endpoint_body("sb-1")),
            _Response(200, {"running": False, "exit_code": 0}),
        ]
    )
    api = _api(
        session,
        _endpoint(
            execd_token_env="OSB_EXECD_TOKEN",
            execd_endpoint_host_allowlist=("opensandbox-gateway.open-ace.example",),
        ),
    )
    api.command_status("sb-1", "cmd-1")
    sent = session.calls[-1]["headers"]
    assert sent[INGRESS_ROUTE_HEADER] == "sb-1-44772"
    assert sent[SECURE_ACCESS_HEADER] == "per-sandbox-token"


def test_the_pty_socket_also_carries_the_routing_header(monkeypatch):
    """The WebSocket upgrade goes through the gateway too."""
    monkeypatch.setenv("OSB_EXECD_TOKEN", "static")
    session = _Session([_Response(200, _header_mode_endpoint_body("sb-9"))])
    api = _api(
        session,
        _endpoint(
            execd_token_env="OSB_EXECD_TOKEN",
            execd_endpoint_host_allowlist=("opensandbox-gateway.open-ace.example",),
        ),
    )
    headers = api.execd_headers("sb-9")
    assert headers[INGRESS_ROUTE_HEADER] == "sb-9-44772"


def test_bare_json_lines_are_parsed_as_events():
    """Real execd streams bare JSON objects, not `data:`-prefixed SSE.

    Captured verbatim from opensandbox/execd v1.1.0 over
    `content-type: text/event-stream`. Accumulating only `data:` lines yielded
    NOTHING for every command — _run_foreground saw no error event and reported
    the repo synthesis as successful whether or not it ran.
    """
    body = (
        b'{"type":"init","text":"cmd-abc","timestamp":1}\n\n'
        b'{"type":"ping","text":"pong","timestamp":2}\n\n'
        b'{"type":"stdout","text":"hello-real","timestamp":3}\n\n'
        b'{"type":"error","timestamp":4,'
        b'"error":{"ename":"CommandExecError","evalue":"3","traceback":["exit status 3"]}}\n\n'
    )
    events = list(iter_sse_events(_Response(200, content=body)))
    assert [e["type"] for e in events] == ["init", "stdout", "error"], events
    assert events[-1]["error"]["evalue"] == "3"


def test_the_data_prefixed_form_still_parses():
    """Upstream's OpenAPI documents SSE; both shapes must work."""
    body = b'data: {"type":"stdout","text":"x"}\n\ndata: {"type":"execution_complete"}\n\n'
    assert [e["type"] for e in iter_sse_events(_Response(200, content=body))] == [
        "stdout",
        "execution_complete",
    ]


def test_the_upload_metadata_part_carries_a_filename():
    """execd reads metadata from form.File, so the part needs a filename.

    With filename=None requests emits it as a plain form field, it lands in
    form.Value, execd counts zero metadata parts and answers
    `400 metadata file is missing` — verified against a real execd. This is the
    first call any run makes, so every run failed at upload.
    """
    session = _Session(
        [
            _Response(200, {"endpoint": "http://osb.open-ace.svc.cluster.local/p"}),
            _Response(200, {}),
        ]
    )
    _api(session).upload_file("sb-1", "/workspace/a.py", b"x", 0o644)
    files = session.calls[-1]["files"]
    parts = dict(files)
    assert parts["metadata"][0], "metadata part has no filename; execd will not see it"


def test_a_truncated_tail_is_dropped_in_the_BARE_shape_too():
    """The tolerance must cover the shape real execd actually sends.

    The pre-existing truncation guard used the `data:` form, which execd never
    emits — so when the bare-JSON branch was added it decoded eagerly and turned
    a cut mid-event into a synthetic error event. `_run_foreground` raises on any
    error event, so a command that had ALREADY succeeded failed. Pings fire every
    3s and this backend now routes execd through a gateway hop, so a truncated
    final chunk is reachable, not theoretical.
    """
    body = (
        b'{"type":"stdout","text":"done"}\n\n'
        b'{"type":"execution_complete"}\n\n'
        b'{"type":"pi'  # cut mid-ping
    )
    types = [e["type"] for e in iter_sse_events(_Response(200, content=body))]
    assert types == ["stdout", "execution_complete"], types


def test_corruption_mid_stream_is_still_surfaced_in_the_bare_shape():
    """Only the FINAL event may be dropped — a lost middle event is a real signal."""
    body = b'{"type":"stdout","text":"a"}\n\n{TRUNCATED-MIDDLE\n\n{"type":"execution_complete"}\n\n'
    types = [e["type"] for e in iter_sse_events(_Response(200, content=body))]
    assert types == ["stdout", "error", "execution_complete"], types


@pytest.mark.parametrize(
    ("python_mode", "wire"),
    [(0o644, 644), (0o755, 755), (0o600, 600), (0o777, 777)],
)
def test_file_modes_travel_as_octal_digits(python_mode, wire):
    """execd parses the mode with base 8, so the wire value is the octal digits.

    Sending Python's integer was destructive in one direction and fatal in the
    other, both verified against a real execd: 0o644 (420) chmods to 0o420
    (`-r---w----`, agent cannot edit its own tree) while 0o755 (493) has no
    octal reading and 500s — and 0o755 is the mode the ChangeSet manifest
    producer is uploaded with, so collect_changes could never have run.
    """
    session = _Session(
        [
            _Response(200, {"endpoint": "http://osb.open-ace.svc.cluster.local/p"}),
            _Response(200, {}),
        ]
    )
    _api(session).upload_file("sb-1", "/workspace/a", b"x", mode=python_mode)
    assert json.loads(session.calls[-1]["files"][0][1][1])["mode"] == wire


def test_create_waits_longer_than_ordinary_calls():
    """POST /sandboxes is synchronous; upstream waits up to 60s for a pod.

    A 30s client timeout aborted while the server was still working, leaving a
    sandbox running with nobody holding its id.
    """
    session = _Session([_Response(200, {"id": "sb-1", "status": {"state": "Running"}})])
    _api(session).create_sandbox({"image": {"uri": _DIGEST}})
    assert session.calls[-1]["timeout"] > 60
