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

# Upstream ``GET /sandboxes`` defaults to ``pageSize`` 20. A single-request
# sweep would silently miss everything past the first page — precisely when the
# orphan reconciler matters most.
_LIST_PAGE_SIZE = 20
_DEFAULT_MAX_LIST_PAGES = 100

# The only server-supplied endpoint headers we will forward. Upstream returns an
# opaque ``{string: string}`` map alongside the endpoint URL; forwarding it
# verbatim would be header injection into every subsequent request.
_ALLOWED_ENDPOINT_HEADER_KEYS = frozenset(
    {
        "x-sandbox-access-token",
        "x-openace-access-token",
        EXECD_TOKEN_HEADER.lower(),
        EGRESS_AUTH_HEADER.lower(),
    }
)

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
    def download_file(self, sandbox_id: str, path: str) -> bytes: ...
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
    for raw_line in response.iter_lines():
        line = (
            raw_line.decode("utf-8", errors="replace") if isinstance(raw_line, bytes) else raw_line
        )
        if line.startswith(":"):
            continue
        if line == "":
            event = _decode_sse_buffer(buffer, tolerate_undecodable=False)
            buffer = []
            if event is not None:
                yield event
            continue
        if line.startswith("data:"):
            buffer.append(line[5:].lstrip())
    # Only the FINAL buffer may be undecodable — a truncated tail is a normal
    # way for a stream to end. A malformed event mid-stream is a real signal (a
    # lost stdout chunk, a lost error) and must not vanish silently.
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
        timeout: float = 30.0,
        page_size: int = _LIST_PAGE_SIZE,
        max_list_pages: int = _DEFAULT_MAX_LIST_PAGES,
    ) -> None:
        self._endpoint = endpoint
        self._session = session or requests.Session()
        self._timeout = timeout
        self._page_size = page_size
        self._max_list_pages = max_list_pages
        # sandbox_id -> (base_url, extra_headers) resolved from endpoints/{port},
        # cached per service because each is a separate port.
        self._execd: dict[str, tuple[str, dict[str, str]]] = {}
        self._egress: dict[str, tuple[str, dict[str, str]]] = {}

    # ── Lifecycle ─────────────────────────────────────────────────────

    def create_sandbox(self, body: dict) -> dict:
        return self._json(self._lifecycle("POST", "/sandboxes", json=body))

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
                "mode": mode,
                "owner": self._endpoint.runtime_user,
                "group": self._endpoint.runtime_group,
            }
        )
        self._execd_request(
            sandbox_id,
            "POST",
            "/files/upload",
            files=[
                ("metadata", (None, metadata, "application/json")),
                ("file", (path.rsplit("/", 1)[-1], data, "application/octet-stream")),
            ],
        )

    def download_file(self, sandbox_id: str, path: str) -> bytes:
        return cast(
            "bytes",
            self._execd_request(
                sandbox_id, "GET", "/files/download", params={"path": path}
            ).content,
        )

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
