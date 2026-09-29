"""Thin REST client for the OpenSandbox Lifecycle and Execd APIs (Issue #2023).

Two surfaces, two auth schemes:

* **Lifecycle** — the server, base path ``/v1``, header
  ``OPEN-SANDBOX-API-KEY``. Creates and tears down sandboxes.
* **Execd** — inside each sandbox on port 44772, header
  ``X-EXECD-ACCESS-TOKEN`` (an apiKey header, *not* ``Authorization: Bearer``).
  Runs commands, moves files, and hosts the PTY WebSocket.

Everything is behind :class:`OpenSandboxApi` so the provider and its tests never
touch HTTP directly.

Why not ``safe_request``
------------------------
``app.utils.outbound_url_guard.safe_request`` rejects private and link-local
destinations by design (``_is_public_address``), and an OpenSandbox server is an
in-cluster address. This module therefore calls ``requests`` directly, which
``CLAUDE.md`` 出站 HTTP 请求规范 rule 2 permits for internal service calls with a
stated reason. Every call passes ``proxies={"http": None, "https": None}``.

The SSRF reasoning differs between the two surfaces, and conflating them would
be a real hole. The lifecycle ``base_url`` comes from operator configuration and
never from user input, so there is nothing to guard. The **execd** URL does not
have that property: it is returned by
``GET /sandboxes/{id}/endpoints/{port}`` — a server-chosen string — and we then
POST the entire workspace snapshot to it with the execd token attached. A
misconfigured or compromised server could point that anywhere. So execd URLs are
validated against a host allowlist, redirects are refused, and the
server-supplied ``headers`` map is filtered through a key allowlist before any
of it is forwarded.
"""

from __future__ import annotations

import contextlib
import json
import logging
from collections.abc import Iterator, Mapping
from typing import TYPE_CHECKING, Any, Protocol, cast
from urllib.parse import quote, urlencode, urlparse, urlunparse

import requests

from app.modules.workspace.autonomous.sandbox.opensandbox import config as config_mod
from app.modules.workspace.autonomous.sandbox.provider import SandboxError

if TYPE_CHECKING:  # pragma: no cover - annotations only
    from app.modules.workspace.autonomous.sandbox.opensandbox.config import EndpointConfig

logger = logging.getLogger(__name__)

LIFECYCLE_API_KEY_HEADER = "OPEN-SANDBOX-API-KEY"
EXECD_TOKEN_HEADER = "X-EXECD-ACCESS-TOKEN"
EGRESS_AUTH_HEADER = "OPENSANDBOX-EGRESS-AUTH"
# The per-sandbox credential gateway mode mints. Upstream name, verbatim from
# services/constants.py OPEN_SANDBOX_SECURE_ACCESS_HEADER — this is the header
# the whole peer-isolation property rests on, so it must match exactly.
SECURE_ACCESS_HEADER = "OpenSandbox-Secure-Access"
# ROUTING, not auth. Under `[ingress.gateway] route.mode = "header"` — what the
# shipped ConfigMaps use — upstream returns the bare gateway host as the endpoint
# and puts the sandbox identity ONLY here, as "<sandbox_id>-<port>"
# (services/helpers.py::format_ingress_endpoint). Drop it and every execd request
# reaches the gateway with nothing to route on: it falls back to the Host header,
# fails to parse a port out of the gateway's own name, and answers 400.
INGRESS_ROUTE_HEADER = "OpenSandbox-Ingress-To"

# Upstream ``GET /sandboxes`` defaults to ``pageSize`` 20. A single-request
# sweep would silently miss everything past the first page — precisely when the
# orphan reconciler matters most.
# Ordinary calls. Deliberately short: a hung execd must not stall a run.
_DEFAULT_TIMEOUT_SECONDS = 30.0

# `POST /sandboxes` is SYNCHRONOUS — upstream waits for the pod to be Running
# with an IP, bounded by `kubernetes.sandbox_create_timeout_seconds` (default
# 60). A 30s client timeout aborts WHILE the server is still working, and the
# sandbox then comes up with nobody holding its id: observed repeatedly on a
# real cluster, the client raising `Read timed out (read timeout=30.0)` and the
# server logging `state: Running` moments later. Cold starts (image pull plus
# sidecar boot) routinely exceed 30s. Sized above upstream's own bound so the
# SERVER's timeout fires first and answers with a structured error, rather than
# leaving us with a dangling connection and an unattributed sandbox.
_CREATE_TIMEOUT_SECONDS = 90.0

_LIST_PAGE_SIZE = 20
_DEFAULT_MAX_LIST_PAGES = 100

# The only server-supplied endpoint headers we will forward. Upstream returns an
# opaque ``{string: string}`` map alongside the endpoint URL; forwarding it
# verbatim would be header injection into every subsequent request.
_ALLOWED_ENDPOINT_HEADER_KEYS = frozenset(
    {
        SECURE_ACCESS_HEADER.lower(),
        INGRESS_ROUTE_HEADER.lower(),
        EXECD_TOKEN_HEADER.lower(),
        EGRESS_AUTH_HEADER.lower(),
    }
)
# "x-sandbox-access-token" / "x-openace-access-token" used to sit here. Neither
# is a name upstream ever sends — they were invented, and the fake server sent
# the invented one, so every test agreed with itself while the REAL header
# (OpenSandbox-Secure-Access) was silently stripped by the filter below. Under
# gateway mode that drops the per-sandbox credential from every execd call: the
# gateway 401s, no run completes, and the peer-isolation guarantee this backend
# advertises has nothing behind it.
#
# This list is the COMPLETE set of names upstream ever puts in Endpoint.headers:
# services/helpers.py:248 (ingress routing), services/endpoint_auth.py:39
# (egress) and :44 (secure access) — matching the three endpoint header
# constants in services/constants.py:34,35,37. api/lifecycle.py:597 also touches
# this map but only ever REMOVES the ingress header (under use_server_proxy the
# server proxies directly, so gateway routing is unwanted); it is not a source of
# names, and is cited here so the next audit does not have to rediscover that.
# Adding a name requires finding it in upstream's constants; removing one
# silently breaks a whole routing mode, which is how the ingress header went
# missing when route.mode changed to "header".

# Proxy lookup is disabled on every call: under gevent it can recurse (#2237).
# requests treats a None value as "no proxy for this scheme", which is exactly
# what disables the environment lookup; its type stub only admits str values.
_NO_PROXIES: Any = {"http": None, "https": None}


class OpenSandboxApiError(SandboxError):
    """An OpenSandbox API call failed.

    Carries the HTTP status and upstream ``code`` so callers can distinguish a
    fail-closed policy rejection from an infrastructure fault.
    """

    def __init__(self, message: str, *, status_code: int | None = None, code: str = "") -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code


class OpenSandboxApi(Protocol):
    """The API surface the provider needs, so tests can substitute a fake."""

    # Lifecycle
    def create_sandbox(self, body: dict) -> dict: ...
    def get_sandbox(self, sandbox_id: str) -> dict | None: ...
    def list_sandboxes(self, metadata: Mapping[str, str] | None = None) -> list[dict]: ...
    def delete_sandbox(self, sandbox_id: str) -> None: ...
    def pause_sandbox(self, sandbox_id: str) -> None: ...
    def resume_sandbox(self, sandbox_id: str) -> None: ...
    def renew_expiration(self, sandbox_id: str, expires_at: str) -> None: ...
    def execd_base_url(self, sandbox_id: str) -> str: ...

    # Execd
    def upload_file(self, sandbox_id: str, path: str, data: bytes, mode: int) -> None: ...
    def download_file(self, sandbox_id: str, path: str, *, max_bytes: int = 0) -> bytes: ...
    def run_command(self, sandbox_id: str, body: dict) -> Iterator[dict]: ...
    def command_status(self, sandbox_id: str, command_id: str) -> dict | None: ...
    def interrupt_command(self, sandbox_id: str, command_id: str) -> None: ...
    def egress_policy(self, sandbox_id: str) -> dict: ...

    # PTY (registered in execd's router but absent from its OpenAPI file)
    def create_pty_session(self, sandbox_id: str, *, cwd: str = "", command: str = "") -> str: ...
    def pty_status(self, sandbox_id: str, pty_session_id: str) -> dict: ...
    def delete_pty_session(self, sandbox_id: str, pty_session_id: str) -> None: ...
    def pty_ws_url(self, sandbox_id: str, pty_session_id: str, *, since: int = 0) -> str: ...


def iter_sse_events(response: Any) -> Iterator[dict]:
    """Yield decoded events from a ``text/event-stream`` response.

    Accumulates ``data:`` lines until a blank line, skips ``:`` comments and
    ``ping`` keepalives, and **drops a truncated trailing event** rather than
    raising — a stream cut mid-event is a normal way for a connection to end and
    must not turn into an exception on an otherwise good run.
    """
    buffer: list[str] = []
    # The most recent bare-JSON line, held until we know whether it is last.
    pending: str | None = None
    for raw_line in response.iter_lines():
        line = (
            raw_line.decode("utf-8", errors="replace") if isinstance(raw_line, bytes) else raw_line
        )
        if line.startswith(":"):
            continue
        # Real execd streams BARE JSON objects, one per line, under
        # `content-type: text/event-stream` and with no `data:` prefix — verified
        # against opensandbox/execd v1.1.0. Accumulating only `data:` lines
        # yielded NOTHING for every command: _run_foreground then saw no error
        # event and reported the repo synthesis as successful whether or not it
        # ran, and the /command branch of stream() produced an empty run.
        # The `data:` form is still handled below because upstream's OpenAPI
        # documents SSE and other endpoints may use it.
        if line.startswith("{"):
            if buffer:
                event = _decode_sse_buffer(buffer, tolerate_undecodable=False)
                buffer = []
                if event is not None:
                    yield event
            # HELD, not decoded yet. Only the FINAL event of a stream may be
            # truncated, and we cannot know a line is final until something
            # follows it — so the pending line is decoded strictly once more
            # content arrives, and tolerantly at end-of-stream. Decoding
            # eagerly turned a mid-ping truncation after a SUCCESSFUL command
            # into a synthetic error event, which _run_foreground raises on:
            # it failed runs that had worked, and contradicted this function's
            # own promise to drop a truncated tail.
            if pending is not None:
                event = _decode_sse_buffer([pending], tolerate_undecodable=False)
                if event is not None:
                    yield event
            pending = line
            continue
        if pending is not None:
            # Any other content line also proves the held one was not last.
            event = _decode_sse_buffer([pending], tolerate_undecodable=False)
            pending = None
            if event is not None:
                yield event
        if line == "":
            event = _decode_sse_buffer(buffer, tolerate_undecodable=False)
            buffer = []
            if event is not None:
                yield event
            continue
        if line.startswith("data:"):
            buffer.append(line[5:].lstrip())
    # Only the FINAL event may be undecodable — a truncated tail is a normal
    # way for a stream to end. A malformed event mid-stream is a real signal (a
    # lost stdout chunk, a lost error) and must not vanish silently. This holds
    # for BOTH wire shapes: the bare-JSON one real execd sends, and the `data:`
    # one upstream's OpenAPI documents.
    if pending is not None:
        event = _decode_sse_buffer([pending], tolerate_undecodable=True)
        if event is not None:
            yield event
    event = _decode_sse_buffer(buffer, tolerate_undecodable=True)
    if event is not None:
        yield event


def _closing_events(response: Any) -> Iterator[dict]:
    """Yield SSE events and always release the connection.

    ``stream()`` abandons this iterator on timeout or stop, so without the
    ``finally`` the pooled connection is never returned and a long-lived
    scheduler process exhausts the session pool.
    """
    try:
        yield from iter_sse_events(response)
    finally:
        close = getattr(response, "close", None)
        if callable(close):
            close()


def _encode_metadata_filter(metadata: Mapping[str, str]) -> str:
    """Encode a metadata filter in upstream's documented form.

    ``k=v`` pairs joined by an encoded ``&``, with the value itself encoded —
    e.g. ``project%3DApollo%26note%3DDemo``.
    """
    return "&".join(
        f"{quote(str(k), safe='')}={quote(str(v), safe='')}" for k, v in metadata.items()
    )


def _decode_sse_buffer(buffer: list[str], *, tolerate_undecodable: bool) -> dict | None:
    if not buffer:
        return None
    try:
        event = json.loads("".join(buffer))
    except ValueError:
        if tolerate_undecodable:
            return None
        logger.warning("undecodable SSE event mid-stream; surfacing as an error event")
        return {"type": "error", "error": {"ename": "SSEDecodeError", "evalue": "<undecodable>"}}
    if not isinstance(event, dict) or event.get("type") == "ping":
        return None
    return event


class HttpOpenSandboxApi:
    """:class:`OpenSandboxApi` over HTTP."""

    def __init__(
        self,
        endpoint: EndpointConfig,
        *,
        session: Any | None = None,
        timeout: float = _DEFAULT_TIMEOUT_SECONDS,
        create_timeout: float = _CREATE_TIMEOUT_SECONDS,
        page_size: int = _LIST_PAGE_SIZE,
        max_list_pages: int = _DEFAULT_MAX_LIST_PAGES,
    ) -> None:
        self._endpoint = endpoint
        self._session = session or requests.Session()
        self._timeout = timeout
        self._create_timeout = create_timeout
        self._page_size = page_size
        self._max_list_pages = max_list_pages
        # sandbox_id -> (base_url, extra_headers) resolved from endpoints/{port},
        # cached per service because each is a separate port.
        self._execd: dict[str, tuple[str, dict[str, str]]] = {}
        self._egress: dict[str, tuple[str, dict[str, str]]] = {}

    # ── Lifecycle ─────────────────────────────────────────────────────

    def create_sandbox(self, body: dict) -> dict:
        # Synchronous upstream; see _CREATE_TIMEOUT_SECONDS.
        return self._json(
            self._lifecycle("POST", "/sandboxes", json=body, timeout=self._create_timeout)
        )

    def get_sandbox(self, sandbox_id: str) -> dict | None:
        response = self._lifecycle("GET", f"/sandboxes/{sandbox_id}", allow_404=True)
        return None if response.status_code == 404 else self._json(response)

    def list_sandboxes(self, metadata: Mapping[str, str] | None = None) -> list[dict]:
        """Return every sandbox matching *metadata*, following pagination.

        ``metadata`` is not optional in practice: the orphan sweep destroys
        every sandbox the control plane does not claim, and on a shared
        OpenSandbox server an unfiltered list would include other teams' and
        other products' sandboxes.

        Stops on a short page, or at ``max_list_pages`` — the guard exists so a
        server that keeps returning full pages cannot spin the reconciler
        forever; tripping it is logged by the caller as an incomplete sweep.
        """
        collected: list[dict] = []
        for page in range(1, self._max_list_pages + 1):
            params: dict[str, Any] = {"page": page, "pageSize": self._page_size}
            if metadata:
                params["metadata"] = _encode_metadata_filter(metadata)
            body = self._json(self._lifecycle("GET", "/sandboxes", params=params))
            batch = body.get("items") or []
            collected.extend(batch)
            pagination = body.get("pagination")
            if isinstance(pagination, dict) and "hasNextPage" in pagination:
                # Authoritative signal. Preferred over the short-page heuristic
                # because a server that clamps pageSize to its own maximum
                # returns a "short" page on request 1 — which would silently
                # stop the orphan sweep after one page, in exactly the situation
                # reconciliation exists for.
                if not pagination["hasNextPage"]:
                    break
                continue
            if len(batch) < self._page_size:
                break
        return collected

    def delete_sandbox(self, sandbox_id: str) -> None:
        # 404 means the sandbox is already gone, which is the desired end state.
        # destroy() is required to be idempotent by the #2022 contract.
        self._lifecycle("DELETE", f"/sandboxes/{sandbox_id}", allow_404=True)
        self._execd.pop(sandbox_id, None)
        self._egress.pop(sandbox_id, None)

    def pause_sandbox(self, sandbox_id: str) -> None:
        self._lifecycle("POST", f"/sandboxes/{sandbox_id}/pause")

    def resume_sandbox(self, sandbox_id: str) -> None:
        self._lifecycle("POST", f"/sandboxes/{sandbox_id}/resume")

    def renew_expiration(self, sandbox_id: str, expires_at: str) -> None:
        self._lifecycle(
            "POST", f"/sandboxes/{sandbox_id}/renew-expiration", json={"expiresAt": expires_at}
        )

    def execd_base_url(self, sandbox_id: str) -> str:
        return self._resolve_execd(sandbox_id)[0]

    # ── Execd ─────────────────────────────────────────────────────────

    def upload_file(self, sandbox_id: str, path: str, data: bytes, mode: int) -> None:
        # Upstream reads a JSON metadata part followed by the file part, in that
        # order, so the ordering here is load-bearing rather than cosmetic.
        # owner/group matter as much as mode: execd may run as root, and a
        # root-owned 0644 file is unwritable by the non-root agent.
        metadata = json.dumps(
            {
                "path": path,
                "mode": _wire_mode(mode),
                "owner": self._endpoint.runtime_user,
                "group": self._endpoint.runtime_group,
            }
        )
        self._execd_request(
            sandbox_id,
            "POST",
            "/files/upload",
            files=[
                # The metadata part MUST carry a filename. execd reads it from
                # `form.File["metadata"]` (components/execd/.../filesystem_upload.go),
                # and a part with filename=None is emitted by requests as a plain
                # form field, landing in form.Value instead — execd then counts
                # zero metadata parts and answers `400 metadata file is missing`.
                # Verified against a real execd; every upload failed before this,
                # which is the first call any run makes.
                ("metadata", ("metadata.json", metadata, "application/json")),
                ("file", (path.rsplit("/", 1)[-1], data, "application/octet-stream")),
            ],
        )

    def download_file(self, sandbox_id: str, path: str, *, max_bytes: int = 0) -> bytes:
        """Fetch a file. With ``max_bytes``, refuse an oversized one UNREAD.

        Without the bound this reads ``.content``, which materialises the whole
        body before anything can measure it. That is fine for the ChangeSet
        path, whose sizes are pre-declared in a manifest — but the agent-state
        transcript (#3237) is written by the agent into a 1Gi ``emptyDir``,
        while the scheduler pod runs with a 512Mi limit. One runaway transcript
        would OOM-kill the control plane.

        Bounded mode streams: it checks ``Content-Length`` first, then still
        counts delivered bytes, because a server may send no length header or a
        wrong one and the header is not the guarantee — the counter is.
        """
        if max_bytes <= 0:
            return cast(
                "bytes",
                self._execd_request(
                    sandbox_id, "GET", "/files/download", params={"path": path}
                ).content,
            )
        response = self._execd_request(
            sandbox_id, "GET", "/files/download", params={"path": path}, stream=True
        )
        with contextlib.closing(response):
            declared = response.headers.get("Content-Length")
            if declared is not None:
                with contextlib.suppress(ValueError):
                    if int(declared) > max_bytes:
                        raise OpenSandboxApiError(
                            f"{path} is {declared} bytes, over the {max_bytes} limit",
                            status_code=0,
                            code="FILE_TOO_LARGE",
                        )
            chunks: list[bytes] = []
            total = 0
            for chunk in response.iter_content(chunk_size=64 * 1024):
                total += len(chunk)
                if total > max_bytes:
                    raise OpenSandboxApiError(
                        f"{path} exceeded the {max_bytes} byte limit mid-transfer",
                        status_code=0,
                        code="FILE_TOO_LARGE",
                    )
                chunks.append(chunk)
            return b"".join(chunks)

    def run_command(self, sandbox_id: str, body: dict) -> Iterator[dict]:
        response = self._execd_request(sandbox_id, "POST", "/command", json=body, stream=True)
        return _closing_events(response)

    def command_status(self, sandbox_id: str, command_id: str) -> dict | None:
        response = self._execd_request(
            sandbox_id, "GET", f"/command/status/{command_id}", allow_404=True
        )
        return None if response.status_code == 404 else self._json(response)

    def interrupt_command(self, sandbox_id: str, command_id: str) -> None:
        self._execd_request(sandbox_id, "DELETE", "/command", params={"id": command_id})

    def egress_policy(self, sandbox_id: str) -> dict:
        """Read the egress sidecar's live policy, for the §5.3 verification probe.

        The sidecar is a separate service on ``egress_port`` with its own
        ``OPENSANDBOX-EGRESS-AUTH`` header — issuing this against execd's port
        would simply 404, and the probe that upgrades NETWORK_EGRESS_POLICY from
        an operator's word to a verified fact would never run.
        """
        base, headers = self._resolve_port(sandbox_id, self._endpoint.egress_port, self._egress)
        return self._json(
            self._request(
                "GET",
                f"{base.rstrip('/')}/policy",
                headers=dict(headers),
                allow_redirects=False,
            )
        )

    # ── PTY ───────────────────────────────────────────────────────────

    def create_pty_session(self, sandbox_id: str, *, cwd: str = "", command: str = "") -> str:
        body: dict[str, str] = {}
        if cwd:
            body["cwd"] = cwd
        if command:
            body["command"] = command
        return str(
            self._json(self._execd_request(sandbox_id, "POST", "/pty", json=body))["session_id"]
        )

    def pty_status(self, sandbox_id: str, pty_session_id: str) -> dict:
        return self._json(self._execd_request(sandbox_id, "GET", f"/pty/{pty_session_id}"))

    def delete_pty_session(self, sandbox_id: str, pty_session_id: str) -> None:
        self._execd_request(sandbox_id, "DELETE", f"/pty/{pty_session_id}", allow_404=True)

    def pty_ws_url(self, sandbox_id: str, pty_session_id: str, *, since: int = 0) -> str:
        """Build the pipe-mode WebSocket URL for a PTY session.

        ``pty=0`` selects pipe mode, which is what keeps stderr a separate frame
        stream (``0x02``) instead of merging it into the terminal output — the
        agent's stderr has to stay distinguishable for evidence collection.
        """
        base, _ = self._resolve_execd(sandbox_id)
        parsed = urlparse(base)
        scheme = "wss" if parsed.scheme == "https" else "ws"
        query = {"pty": "0"}
        if since:
            query["since"] = str(since)
        path = f"{parsed.path.rstrip('/')}/pty/{pty_session_id}/ws"
        return urlunparse((scheme, parsed.netloc, path, "", urlencode(query), ""))

    def execd_headers(self, sandbox_id: str) -> dict[str, str]:
        """Headers a caller outside this class (the PTY transport) must send.

        Must produce the same credential set as :meth:`_execd_request`: the
        static configured token first, then the server-resolved per-sandbox
        headers on top. Returning only the resolved headers made every PTY
        upgrade unauthenticated on a deployment that authenticates execd with
        the configured ``EXECD_ACCESS_TOKEN`` — the HTTP calls would succeed
        and the WebSocket would be rejected, which reads as a transport bug
        rather than a missing credential.
        """
        return self._execd_auth_headers(sandbox_id)

    # ── plumbing ──────────────────────────────────────────────────────

    def _lifecycle(self, method: str, path: str, *, allow_404: bool = False, **kwargs) -> Any:
        headers = {LIFECYCLE_API_KEY_HEADER: self._endpoint.api_key()}
        url = f"{self._endpoint.base_url.rstrip('/')}{path}"
        # allow_redirects=False, matching _execd_request. Requests strips
        # `Authorization` on a cross-host redirect but has no such rule for
        # arbitrary headers, so a 30x from a compromised or misconfigured
        # lifecycle host would forward OPEN-SANDBOX-API-KEY to wherever it
        # pointed. The base_url is operator-configured and already correct;
        # there is no redirect we would want to follow.
        return self._request(
            method, url, headers=headers, allow_404=allow_404, allow_redirects=False, **kwargs
        )

    def _execd_auth_headers(self, sandbox_id: str) -> dict[str, str]:
        """The execd credential set — the one definition, HTTP and WebSocket."""
        headers: dict[str, str] = {}
        token = self._endpoint.execd_token()
        if token:
            headers[EXECD_TOKEN_HEADER] = token
        # A server-supplied per-sandbox token takes precedence over the static
        # one: upstream's secureAccess mints a credential per sandbox.
        headers.update(self._resolve_execd(sandbox_id)[1])
        return headers

    def _execd_request(
        self, sandbox_id: str, method: str, path: str, *, allow_404: bool = False, **kwargs
    ) -> Any:
        base, _ = self._resolve_execd(sandbox_id)
        headers = self._execd_auth_headers(sandbox_id)
        url = f"{base.rstrip('/')}{path}"
        # allow_redirects=False: the execd host was server-supplied and already
        # allowlisted; a redirect would move the request off that host after the
        # check, taking the token and the payload with it.
        return self._request(
            method, url, headers=headers, allow_404=allow_404, allow_redirects=False, **kwargs
        )

    def _resolve_execd(self, sandbox_id: str) -> tuple[str, dict[str, str]]:
        return self._resolve_port(sandbox_id, self._endpoint.execd_port, self._execd)

    def _resolve_port(
        self, sandbox_id: str, port: int, cache: dict[str, tuple[str, dict[str, str]]]
    ) -> tuple[str, dict[str, str]]:
        cached = cache.get(sandbox_id)
        if cached is not None:
            return cached
        body = self._json(self._lifecycle("GET", f"/sandboxes/{sandbox_id}/endpoints/{port}"))
        url = str(body.get("endpoint") or "")
        if not url:
            raise OpenSandboxApiError(f"sandbox {sandbox_id}: no execd endpoint returned")
        if "://" not in url:
            url = f"http://{url}"
        self._assert_execd_host_allowed(url)
        headers = _filter_endpoint_headers(body.get("headers") or {})
        resolved = (url, headers)
        cache[sandbox_id] = resolved
        return resolved

    def _assert_execd_host_allowed(self, url: str) -> None:
        host = (urlparse(url).hostname or "").lower()
        allowlist = self._endpoint.execd_endpoint_host_allowlist
        if not host or not any(_host_matches(host, pattern) for pattern in allowlist):
            raise OpenSandboxApiError(
                f"execd endpoint host {host!r} is not in the configured allowlist "
                f"{list(allowlist)}; refusing to send workspace data to it"
            )

    def _request(self, method: str, url: str, *, allow_404: bool = False, **kwargs) -> Any:
        # 直接调用原因：OpenSandbox 服务端是集群内地址（*.svc.cluster.local / 私网 IP），
        # app.utils.outbound_url_guard.safe_request 按设计拒绝私网目标，无法用于本调用。
        # lifecycle base_url 仅来自运维配置，永不来自用户输入；execd URL 来自服务端响应，
        # 已单独经 _assert_execd_host_allowed 白名单校验 + allow_redirects=False。
        # proxies=None 关闭代理查找，避免 gevent 环境下的 RecursionError（CLAUDE.md #2237）。
        kwargs.setdefault("timeout", self._timeout)
        try:
            response = self._session.request(method, url, proxies=_NO_PROXIES, **kwargs)
        except requests.RequestException as exc:
            raise OpenSandboxApiError(f"{method} {url} failed: {exc}") from exc
        status = response.status_code
        if status == 404 and allow_404:
            return response
        if status >= 400:
            code, message = _decode_error(response)
            raise OpenSandboxApiError(
                f"{method} {url} -> {status}: {message}", status_code=status, code=code
            )
        return response

    @staticmethod
    def _json(response: Any) -> dict:
        try:
            body = response.json()
        except ValueError as exc:
            raise OpenSandboxApiError(f"expected a JSON body, got {response.text[:200]!r}") from exc
        return body if isinstance(body, dict) else {"items": body}


def _wire_mode(mode: int) -> int:
    """Convert a Python file mode to the integer execd expects on the wire.

    execd chmods with ``strconv.ParseUint(fmt.Sprint(mode), 8, ...)``, so the
    value it wants is the OCTAL DIGITS read as a decimal integer — ``0o644``
    must travel as ``644``, and upstream's API examples say ``mode: 755``.

    Sending Python's own integer was silently destructive and loudly broken by
    turns, both verified against a real execd:

    * ``0o644`` is 420, which ParseUint reads as ``0o420`` → ``-r---w----``.
      The upload "succeeds" and the agent cannot edit its own workspace.
    * ``0o755`` is 493, which has no octal reading at all → HTTP 500
      ``strconv.ParseUint: parsing "493": invalid syntax``. That is the mode the
      ChangeSet manifest producer is uploaded with, so ``collect_changes`` could
      never have run.

    Converting here, at the single wire boundary, keeps every caller in ordinary
    Python modes — ``apply_changeset`` chmods the trusted worktree with the same
    values and must keep receiving real ones.
    """
    return int(f"{mode:o}")


def _decode_error(response: Any) -> tuple[str, str]:
    try:
        body = response.json()
    except ValueError:
        return "", (response.text or "")[:200]
    if not isinstance(body, dict):
        return "", str(body)[:200]
    return str(body.get("code") or ""), str(body.get("message") or "")


def _filter_endpoint_headers(headers: Any) -> dict[str, str]:
    if not isinstance(headers, dict):
        return {}
    return {
        str(key): str(value)
        for key, value in headers.items()
        if str(key).lower() in _ALLOWED_ENDPOINT_HEADER_KEYS
    }


def _host_matches(host: str, pattern: str) -> bool:
    # Delegates to the single shared definition — see config.host_matches.
    return config_mod.host_matches(host, pattern)
