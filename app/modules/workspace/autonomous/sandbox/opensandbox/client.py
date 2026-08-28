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
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any, Protocol
from urllib.parse import urlencode, urlparse, urlunparse

import requests

from app.modules.workspace.autonomous.sandbox.provider import SandboxError

if TYPE_CHECKING:  # pragma: no cover - annotations only
    from app.modules.workspace.autonomous.sandbox.opensandbox.config import EndpointConfig

LIFECYCLE_API_KEY_HEADER = "OPEN-SANDBOX-API-KEY"
EXECD_TOKEN_HEADER = "X-EXECD-ACCESS-TOKEN"

# Upstream ``GET /sandboxes`` defaults to ``pageSize`` 20. A single-request
# sweep would silently miss everything past the first page — precisely when the
# orphan reconciler matters most.
_LIST_PAGE_SIZE = 100
_DEFAULT_MAX_LIST_PAGES = 100

# The only server-supplied endpoint headers we will forward. Upstream returns an
# opaque ``{string: string}`` map alongside the endpoint URL; forwarding it
# verbatim would be header injection into every subsequent request.
_ALLOWED_ENDPOINT_HEADER_KEYS = frozenset(
    {"x-sandbox-access-token", "x-openace-access-token", EXECD_TOKEN_HEADER.lower()}
)

# Proxy lookup is disabled on every call: under gevent it can recurse (#2237).
_NO_PROXIES = {"http": None, "https": None}


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
    def list_sandboxes(self) -> list[dict]: ...
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
            event = _decode_sse_buffer(buffer)
            buffer = []
            if event is not None:
                yield event
            continue
        if line.startswith("data:"):
            buffer.append(line[5:].lstrip())
    # A complete final event with no trailing blank line is still valid.
    event = _decode_sse_buffer(buffer)
    if event is not None:
        yield event


def _decode_sse_buffer(buffer: list[str]) -> dict | None:
    if not buffer:
        return None
    try:
        event = json.loads("".join(buffer))
    except ValueError:
        return None  # truncated tail
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
        # sandbox_id -> (base_url, extra_headers) resolved from endpoints/{port}
        self._execd: dict[str, tuple[str, dict[str, str]]] = {}

    # ── Lifecycle ─────────────────────────────────────────────────────

    def create_sandbox(self, body: dict) -> dict:
        return self._json(self._lifecycle("POST", "/sandboxes", json=body))

    def get_sandbox(self, sandbox_id: str) -> dict | None:
        response = self._lifecycle("GET", f"/sandboxes/{sandbox_id}", allow_404=True)
        return None if response.status_code == 404 else self._json(response)

    def list_sandboxes(self) -> list[dict]:
        """Return every sandbox, following pagination.

        Stops on a short page, or at ``max_list_pages`` — the guard exists so a
        server that keeps returning full pages cannot spin the reconciler
        forever; tripping it is logged by the caller as an incomplete sweep.
        """
        collected: list[dict] = []
        for page in range(1, self._max_list_pages + 1):
            body = self._json(
                self._lifecycle(
                    "GET", "/sandboxes", params={"page": page, "pageSize": self._page_size}
                )
            )
            batch = body.get("sandboxes") or body.get("items") or []
            collected.extend(batch)
            if len(batch) < self._page_size:
                break
        return collected

    def delete_sandbox(self, sandbox_id: str) -> None:
        # 404 means the sandbox is already gone, which is the desired end state.
        # destroy() is required to be idempotent by the #2022 contract.
        self._lifecycle("DELETE", f"/sandboxes/{sandbox_id}", allow_404=True)
        self._execd.pop(sandbox_id, None)

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
        metadata = json.dumps({"path": path, "mode": mode})
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
        return self._execd_request(
            sandbox_id, "GET", "/files/download", params={"path": path}
        ).content

    def run_command(self, sandbox_id: str, body: dict) -> Iterator[dict]:
        response = self._execd_request(sandbox_id, "POST", "/command", json=body, stream=True)
        return iter_sse_events(response)

    def command_status(self, sandbox_id: str, command_id: str) -> dict | None:
        response = self._execd_request(
            sandbox_id, "GET", f"/command/status/{command_id}", allow_404=True
        )
        return None if response.status_code == 404 else self._json(response)

    def interrupt_command(self, sandbox_id: str, command_id: str) -> None:
        self._execd_request(sandbox_id, "DELETE", "/command", params={"id": command_id})

    def egress_policy(self, sandbox_id: str) -> dict:
        """Read the egress sidecar's live policy, for the §5.3 verification probe."""
        return self._json(self._execd_request(sandbox_id, "GET", "/policy"))

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
        """Headers a caller outside this class (the PTY transport) must send."""
        return dict(self._resolve_execd(sandbox_id)[1])

    # ── plumbing ──────────────────────────────────────────────────────

    def _lifecycle(self, method: str, path: str, *, allow_404: bool = False, **kwargs) -> Any:
        headers = {LIFECYCLE_API_KEY_HEADER: self._endpoint.api_key()}
        url = f"{self._endpoint.base_url.rstrip('/')}{path}"
        return self._request(method, url, headers=headers, allow_404=allow_404, **kwargs)

    def _execd_request(
        self, sandbox_id: str, method: str, path: str, *, allow_404: bool = False, **kwargs
    ) -> Any:
        base, headers = self._resolve_execd(sandbox_id)
        url = f"{base.rstrip('/')}{path}"
        # allow_redirects=False: the execd host was server-supplied and already
        # allowlisted; a redirect would move the request off that host after the
        # check, taking the token and the payload with it.
        return self._request(
            method, url, headers=dict(headers), allow_404=allow_404, allow_redirects=False, **kwargs
        )

    def _resolve_execd(self, sandbox_id: str) -> tuple[str, dict[str, str]]:
        cached = self._execd.get(sandbox_id)
        if cached is not None:
            return cached
        body = self._json(
            self._lifecycle("GET", f"/sandboxes/{sandbox_id}/endpoints/{self._endpoint.execd_port}")
        )
        url = str(body.get("endpoint") or "")
        if not url:
            raise OpenSandboxApiError(f"sandbox {sandbox_id}: no execd endpoint returned")
        if "://" not in url:
            url = f"http://{url}"
        self._assert_execd_host_allowed(url)
        headers = _filter_endpoint_headers(body.get("headers") or {})
        resolved = (url, headers)
        self._execd[sandbox_id] = resolved
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
    pattern = pattern.lower().strip()
    if pattern.startswith("*."):
        suffix = pattern[1:]  # ".open-ace.svc.cluster.local"
        return host.endswith(suffix) or host == pattern[2:]
    return host == pattern
