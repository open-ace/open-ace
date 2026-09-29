"""Backend-neutral half of the sandboxed interactive WebUI (#3378, #3431).

The ``sandboxed`` isolation level runs each user's qwen-code-webui outside the
control-plane host process. What does not depend on WHERE it runs lives here:

* the per-process generation + heartbeat files (T-D multi-replica mutex) and
  the timer greenlet that refreshes them;
* the per-instance v2 token mint and the proxy-token expiry decode;
* :class:`SandboxWebuiProxy`, the port-shaped local browser proxy (D1);
* the launch result / error types the manager consumes;
* the env-gated spawn of the backend's orphan reconcile.

Backend-specific lifecycles live in sibling modules — today
``webui_sandbox_opensandbox`` (OpenSandbox pods on Kubernetes). They import
this module AS A MODULE for anything tests replace (``write_webui_heartbeat``,
``fresh_peer_heartbeats``, ``current_process_generation``), so a
``monkeypatch.setattr(webui_sandbox, ...)`` reaches the binding that runs.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import secrets
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# The webui port inside the pod (D2; the local per-instance port proxy in D1
# forwards to this).
WEBUI_POD_PORT = 3100

# Independent snapshot root (D6: the reaper never scans it). Chosen INSIDE the
# config directory (alongside config.json), because that is the directory Docker
# deployments mount for persistence — a sibling of CONFIG_DIR itself would be
# container-local and lose every snapshot on container recreation.
STATE_ROOT_ENV = "OPENACE_WEBUI_STATE_ROOT"

# Per-process generation: reconcile destroys webui pods whose generation is not
# this value (D5). Module-level so every launcher in this process agrees with
# the reconciler.
_PROCESS_GENERATION = uuid.uuid4().hex

# ── T-D (review round 1): multi-replica reconcile mutex via heartbeats ──────
#
# The k8s reference manifest ships 3 replicas (HPA 3-10). Generation-based
# orphan discrimination alone let every newly started replica sweep every
# OTHER replica's live pods. Each web process keeps a heartbeat file in the
# shared state root; the reconcile destroys nothing while another FRESH
# heartbeat exists.
#
# Review round 2 (F-2) reworks the identity/cadence/cleanup contract:
#
# * Identity: the filename carries ``_PROCESS_GENERATION`` (a per-process
#   uuid4), NOT ``(boot_id, pid)`` — same-node containers share the host's
#   boot_id and frequently collide on pids, so single-node k3s/kind replicas
#   used to mistake each other (or themselves) and void the mutex.
# * Cadence: a dedicated timer greenlet (worker boot hook, same env gate as
#   the reconcile) refreshes the file every HEARTBEAT_REFRESH_SECONDS. This
#   is the ONLY refresh source: a replica without a manager instance used to
#   write once and never again, and the old manager-loop refresh could ride a
#   cleanup interval far beyond the freshness window.
# * Cleanup: the heartbeat file is removed at process shutdown (atexit +
#   gunicorn ``worker_exit``) so a restarted single-process deployment is not
#   blocked by its own dead predecessor for a whole freshness window.
# * Readability: an unreadable root/file is NOT "no peers" — readers treat it
#   as "peers may exist" (fail-closed) instead of swallowing the OSError.
HEARTBEAT_FILENAME_PREFIX = "webui-heartbeat-"
HEARTBEAT_REFRESH_SECONDS = 300.0
# Freshness window: two full refresh periods plus a scheduling grace, derived
# from the FIXED refresh cadence above (window = 2*300+60 = 660s).
HEARTBEAT_FRESH_WINDOW_SECONDS = 2 * HEARTBEAT_REFRESH_SECONDS + 60.0
# Ancient heartbeat files (dead processes from previous deploys) are pruned
# on every write so a long-lived mounted volume cannot accumulate them.
HEARTBEAT_MAX_AGE_SECONDS = 24 * 3600.0
# Explicit opt-out for the atexit heartbeat cleanup (F-2②). Tests that manage
# heartbeat files by hand can set it to "0".
HEARTBEAT_ATEXIT_ENV = "OPENACE_WEBUI_HEARTBEAT_ATEXIT"

# The exact heartbeat file THIS process last wrote (drives shutdown cleanup —
# only the file we ourselves created is ever unlinked by it).
_last_heartbeat_path: Path | None = None
_atexit_registered = False


def _boot_id() -> str:
    """The host's boot id (Linux), or "" where /proc is unavailable."""
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _heartbeat_path(base: Path) -> Path:
    """Return this process's heartbeat file: keyed by the per-process generation."""
    return base / f"{HEARTBEAT_FILENAME_PREFIX}{_PROCESS_GENERATION}.json"


def write_webui_heartbeat(state_root_override: str | Path | None = None) -> Path | None:
    """Write/refresh THIS process's heartbeat file (T-D, entirely fail-soft).

    Called once per HEARTBEAT_REFRESH_SECONDS by the dedicated timer greenlet
    (and once at reconcile time as a belt-and-braces registration). Returns
    the path written, or None when the state root is unwritable — a missing
    heartbeat can only make a PEER's sweep more conservative toward skipping,
    never less.
    """
    global _last_heartbeat_path
    payload = {
        "pid": os.getpid(),
        "boot_id": _boot_id(),
        # F-2①: the uniqueness key. (boot_id, pid) collides between same-node
        # containers; a per-process uuid4 does not.
        "generation": _PROCESS_GENERATION,
        "ts": time.time(),
    }
    try:
        base = state_root(state_root_override)
        base.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = _heartbeat_path(base)
        temp = path.with_name(f"{path.name}.{uuid.uuid4().hex[:8]}.tmp")
        with open(temp, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
        os.replace(temp, path)
    except OSError as exc:
        logger.debug("webui heartbeat write failed (fail-soft): %s", exc)
        return None
    _last_heartbeat_path = path
    _register_heartbeat_atexit()
    _prune_stale_heartbeat_files(state_root_override)
    return path


def _remove_own_heartbeat_atexit() -> None:
    """Interpreter-exit hook: drop this process's heartbeat file (F-2②, fail-soft)."""
    remove_own_heartbeat()


def remove_own_heartbeat(state_root_override: str | Path | None = None) -> None:
    """Delete THIS process's heartbeat file (idempotent, fail-soft, F-2②).

    Registered via atexit on the first heartbeat write and called from the
    gunicorn ``worker_exit`` hook, so a normally-stopped web process stops
    looking like a live peer: without this, a restarted single-process
    deployment was refused the sandboxed level (and its reconcile was
    blocked) for up to a full freshness window by its own dead predecessor.
    Only the file this process actually wrote (or would write under an
    explicit override) is ever unlinked — nothing else in the state root.
    """
    global _last_heartbeat_path
    candidates: list[Path] = []
    if state_root_override is not None:
        try:
            candidates.append(_heartbeat_path(state_root(state_root_override)))
        except Exception:  # noqa: BLE001 - cleanup must never raise
            pass
    if _last_heartbeat_path is not None:
        candidates.append(_last_heartbeat_path)
    for path in candidates:
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            logger.debug("webui heartbeat removal failed (fail-soft): %s", exc)
    if state_root_override is None:
        # Interpreter-exit call: forget the remembered path so a later
        # explicit remove_own_heartbeat() is a clean no-op (idempotency).
        _last_heartbeat_path = None


def _register_heartbeat_atexit() -> None:
    """Register the shutdown cleanup once (honors HEARTBEAT_ATEXIT_ENV=0)."""
    global _atexit_registered
    if _atexit_registered:
        return
    opt_out = os.environ.get(HEARTBEAT_ATEXIT_ENV, "").strip().lower()
    if opt_out in {"0", "false", "no", "off"}:
        return
    import atexit

    atexit.register(_remove_own_heartbeat_atexit)
    _atexit_registered = True


def _iter_heartbeat_entries(
    state_root_override: str | Path | None = None,
):
    """Yield ``(path, entry)`` for every parseable heartbeat file.

    F-2④: "unreadable" is NOT "none". A heartbeat root that exists but cannot
    be listed, or a heartbeat file that exists but cannot be read, raises
    OSError to the caller — the contract's fail-closed "unreadable = a peer
    may be live" branch and the reconcile's skip-sweep branch key on it. A
    MISSING root (fresh deployment, nothing ever written) legitimately yields
    nothing; a file that vanished mid-scan is gone; a file whose JSON does
    not parse is not evidence of a live peer either. Legacy
    ``webui-heartbeat-<boot_id>-<pid>.json`` files from a pre-F-2 process are
    still yielded so a rolling upgrade keeps seeing the old replicas.
    """
    base = state_root(state_root_override)
    try:
        candidates = sorted(
            path
            for path in base.iterdir()
            if path.name.startswith(HEARTBEAT_FILENAME_PREFIX) and path.name.endswith(".json")
        )
    except FileNotFoundError:
        return  # no state root yet: nothing was ever written — absent, not unreadable
    for path in candidates:
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            continue  # raced with a peer's atomic replace/unlink: gone is gone
        try:
            data = json.loads(raw)
        except ValueError:
            continue  # corrupt payload is not a live peer
        if isinstance(data, dict):
            yield path, data


def _prune_stale_heartbeat_files(state_root_override: str | Path | None = None) -> None:
    """Unlink heartbeat files older than HEARTBEAT_MAX_AGE_SECONDS (fail-soft)."""
    cutoff = time.time() - HEARTBEAT_MAX_AGE_SECONDS
    try:
        entries = list(_iter_heartbeat_entries(state_root_override))
    except OSError:
        # Pruning is an optimisation riding the write path; an unreadable
        # root must not turn a successful heartbeat write into a failure.
        return
    for path, data in entries:
        try:
            if float(data.get("ts") or 0) < cutoff:
                path.unlink(missing_ok=True)
        except (OSError, TypeError, ValueError):
            continue


def fresh_peer_heartbeats(
    state_root_override: str | Path | None = None,
) -> list[dict]:
    """Heartbeat entries of OTHER live web processes (T-D).

    An entry counts when its ts is within HEARTBEAT_FRESH_WINDOW_SECONDS of
    now (a future ts — clock skew — reads as "just written" and counts as
    fresh; fail-safe in the skip direction). This process's own entry never
    counts: it is identified by the per-process generation (F-2①), both in
    the payload and in the filename — a same-node container sharing the
    host's boot_id and our pid can no longer be mistaken for us.

    Raises OSError when the heartbeat root or any heartbeat file cannot be
    read (F-2④): "unreadable" means peer liveness cannot be established, and
    callers must treat that as peers-present / skip, never as "we are alone".
    """
    own_generation = _PROCESS_GENERATION
    own_name = f"{HEARTBEAT_FILENAME_PREFIX}{own_generation}.json"
    now = time.time()
    peers: list[dict] = []
    for path, data in _iter_heartbeat_entries(state_root_override):
        if data.get("generation") == own_generation or path.name == own_name:
            continue
        try:
            if now - float(data.get("ts") or 0) <= HEARTBEAT_FRESH_WINDOW_SECONDS:
                peers.append(data)
        except (TypeError, ValueError):
            continue
    return peers


def current_process_generation() -> str:
    """Return this process's webui-pod generation tag (D5 reconcile key)."""
    return _PROCESS_GENERATION


class SandboxWebuiError(RuntimeError):
    """A fail-closed launcher refusal with a machine-readable reason code.

    Codes are the §5 vocabulary (``sandbox_create_failed``,
    ``sandbox_endpoint_unresolved``, ...) plus passthrough probe failures.
    """

    def __init__(self, message: str, *, reason_code: str = "") -> None:
        """Store the machine-readable reason code (§5 vocabulary)."""
        super().__init__(message)
        self.reason_code = reason_code


@dataclass(frozen=True)
class SandboxedWebuiLaunchResult:
    """Everything the manager needs after a successful sandboxed launch."""

    sandbox_id: str
    tier: str
    # Per-instance webui token secret (D2/B1): the pod's webui validates tokens
    # end-to-end against this; the manager stores it on the WebUIInstance.
    token_secret: str
    restore_confirmed: bool
    # Expiry of the proxy token baked into the pod env — renew clamps to this.
    proxy_token_expires_at: datetime
    proxy_token: str
    webui_port: int = WEBUI_POD_PORT


def state_root(explicit: str | Path | None = None) -> Path:
    """Resolve the independent snapshot root (see STATE_ROOT_ENV)."""
    if explicit:
        return Path(explicit)
    env_root = os.environ.get(STATE_ROOT_ENV, "").strip()
    if env_root:
        return Path(env_root)
    from app.repositories.database import CONFIG_DIR

    return Path(CONFIG_DIR) / "webui-agent-state"


def mint_instance_token(user_id: int, port: int, token_secret: str) -> str:
    """Mint a v2 webui token signed with a per-instance secret.

    Same wire format as ``WebUIManager.generate_token`` (qwen-code-webui PR
    #210) — ``v2:{user_id}:{port}:{timestamp}:{random}:{signature}`` — but
    signed with the pod's per-instance secret so instance A's tokens stop
    validating the moment instance A is destroyed (D2/B1, N8).
    """
    timestamp = int(time.time())
    random_part = secrets.token_hex(8)
    payload = f"v2:{user_id}:{port}:{timestamp}:{random_part}"
    signature = hashlib.sha256(f"{payload}:{token_secret}".encode()).hexdigest()[:16]
    return f"{payload}:{signature}"


def proxy_token_expiry(token: str) -> datetime:
    """Read the minted proxy token's expiry out of its signed payload.

    ``generate_proxy_token`` returns only the token string; the expiry lives
    in the base64 JSON payload under ``exp`` (ISO datetime). Renew clamps to
    this moment. Returns an aware UTC datetime (a naive payload — the mint
    writes local time — is normalized via astimezone).

    Review round 1 (T-F): decode failures and EMPTY tokens (the
    ``WebUIInstance.proxy_token`` default) fail CLOSED — 'now' (UTC) — so a
    renew clamps the pod to immediate expiry instead of re-arming a full
    fallback TTL every maintenance cycle (previously each 5-minute pass moved
    the pod's deadline 4 hours further out: an unbounded lifetime for a
    credential whose real expiry cannot be known). An unreadable token is a
    credential problem, never 'valid for another TTL'.
    """
    import base64
    import json as _json

    if token:
        try:
            payload_b64 = token.split(".", 1)[0]
            payload = _json.loads(base64.b64decode(payload_b64))
            parsed = datetime.fromisoformat(str(payload["exp"]))
            if parsed.tzinfo is None:
                # The mint wrote naive local time (datetime.now().isoformat()).
                parsed = parsed.astimezone()
            return parsed
        except Exception:  # noqa: BLE001 - fail closed below
            logger.warning(
                "webui proxy token expiry could not be decoded; treating the "
                "credential as expired (renew will clamp the pod to now)"
            )
    else:
        logger.warning(
            "webui proxy token is empty; treating the credential as expired "
            "(renew will clamp the pod to now)"
        )
    return datetime.now(timezone.utc)


# ── D1: the per-instance browser proxy ──────────────────────────────────────

# Client headers never forwarded upstream (beyond the credential prefixes):
# hop-by-hop headers per RFC 7230 §6.1 plus Expect (a 100-continue the proxy
# cannot answer would wedge the upstream).
_HOP_BY_HOP_REQUEST_HEADERS = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "expect",
    }
)

# The credential/routing header namespace. Everything the client sends under
# these prefixes is stripped — the browser is the untrusted party here, and
# the ONLY legitimate source of these headers is the server-resolved endpoint
# map injected below.
_INJECTED_HEADER_PREFIXES = ("opensandbox-", "x-execd-")

# The four names the endpoint resolution may legitimately inject (mirrors
# client._ALLOWED_ENDPOINT_HEADER_KEYS — the complete set upstream ever sends).
_INJECTABLE_HEADER_NAMES = frozenset(
    {
        "opensandbox-secure-access",
        "opensandbox-ingress-to",
        "x-execd-access-token",
        "opensandbox-egress-auth",
    }
)

# Bounded buffering: a runaway head or chunked body fails loudly instead of
# being absorbed into control-plane memory.
_MAX_HEAD_BYTES = 64 * 1024
_MAX_DECHUNK_BYTES = 16 * 1024 * 1024

# RFC 7230 §3.2.6 token (header field-name): the only legal shape for a
# header NAME crossing the proxy. Anything else (a smuggled space, a colon,
# a bare CR/LF) would re-serialize into a different header set than the one
# parsed — the request/response-splitting vector T-A closes.
_HEADER_NAME_RE = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")


def _parse_head(head: bytes) -> tuple[str, list[tuple[str, str]]]:
    r"""Split a raw request/response head into its start line + header pairs.

    Raises :class:`_MalformedHead` (not a bare ValueError) so the handler can
    classify: a malformed CLIENT head is a 400, a malformed UPSTREAM head a
    502 (m2).

    Whole-head control-character check (T-A): splitting on ``\\r\\n`` removes
    every legitimate CRLF, so any CR/LF/NUL left anywhere — the start line, a
    header name, or a header value — is a bare one smuggled inside a field.
    Re-serializing such a head lets a lenient upstream read injected headers
    (``GET /x\\nOpenSandbox-Secure-Access: ...`` parses as one request line
    here but as two lines for a parser that accepts bare LF), so the entire
    head is refused, not repaired.
    """
    text = head.decode("iso-8859-1")
    lines = text.split("\r\n")
    for line in lines:
        if "\r" in line or "\n" in line or "\0" in line:
            raise _MalformedHead(f"bare CR/LF or NUL inside head line {line!r}")
    start_line = lines[0]
    headers: list[tuple[str, str]] = []
    for line in lines[1:]:
        if not line:
            continue
        if ":" not in line:
            raise _MalformedHead(f"malformed header line {line!r}")
        name, _, value = line.partition(":")
        name = name.strip()
        if not _HEADER_NAME_RE.fullmatch(name):
            raise _MalformedHead(f"header name {name!r} is not an RFC 7230 token")
        headers.append((name, value.strip()))
    return start_line, headers


def _serialize_head(start_line: str, headers: list[tuple[str, str]]) -> bytes:
    """Assemble a start line + headers back into wire bytes."""
    lines = [start_line] + [f"{name}: {value}" for name, value in headers]
    return ("\r\n".join(lines) + "\r\n\r\n").encode("iso-8859-1")


class _BufferedSock:
    """A gevent socket plus the bytes overread past the last head/line read.

    ``recv`` semantics differ from a raw socket on purpose: ``read_head`` /
    ``read_line`` buffer until their terminator, and everything they read past
    it stays buffered so the body reader never blocks on bytes it already has.
    """

    def __init__(self, sock: Any) -> None:
        """Wrap *sock*; all I/O cooperates because *sock* is a gevent socket."""
        self._sock = sock
        self._pending = bytearray()

    def read_head(self, max_bytes: int = _MAX_HEAD_BYTES) -> bytes:
        """Read through the blank line; return the head WITHOUT its terminator."""
        while b"\r\n\r\n" not in self._pending:
            if len(self._pending) > max_bytes:
                raise _BadRequest("head over the proxy size limit")
            chunk = self._sock.recv(65536)
            if not chunk:
                raise OSError("connection closed before the head completed")
            self._pending.extend(chunk)
        head, _, rest = bytes(self._pending).partition(b"\r\n\r\n")
        self._pending = bytearray(rest)
        return head

    def read_line(self, max_bytes: int = 8192) -> bytes:
        r"""Read one ``\n``-terminated line, returning it WITH the newline."""
        while b"\n" not in self._pending:
            if len(self._pending) > max_bytes:
                raise _BadRequest("overlong framing line")
            chunk = self._sock.recv(4096)
            if not chunk:
                raise OSError("connection closed mid-line")
            self._pending.extend(chunk)
        line, _, rest = bytes(self._pending).partition(b"\n")
        self._pending = bytearray(rest)
        return line + b"\n"

    def read_exact(self, count: int) -> bytes:
        """Read exactly *count* bytes (short only on peer close).

        A non-positive *count* returns ``b""`` immediately — depth-in-depth
        against a caller that computes a negative length (T-A): the recv loop
        below would otherwise block forever waiting for bytes nobody will
        send.
        """
        if count <= 0:
            return b""
        while len(self._pending) < count:
            chunk = self._sock.recv(min(65536, count - len(self._pending)))
            if not chunk:
                break
            self._pending.extend(chunk)
        out = bytes(self._pending[:count])
        del self._pending[:count]
        return out

    def pending_bytes(self) -> int:
        """How many buffered bytes are already in hand."""
        return len(self._pending)

    def drain_pending(self) -> bytes:
        """Return and forget the buffered overread."""
        out = bytes(self._pending)
        self._pending = bytearray()
        return out

    def sendall(self, data: bytes) -> None:
        """Write bytes to the peer."""
        self._sock.sendall(data)

    def pump_from(self) -> bytes:
        """One receive step: buffered overread first, then a fresh recv."""
        if self._pending:
            return self.drain_pending()
        try:
            chunk: bytes = self._sock.recv(65536)
            return chunk
        except OSError:
            return b""

    def close(self) -> None:
        """Close the underlying socket (best effort)."""
        try:
            self._sock.close()
        except Exception:  # noqa: BLE001 - teardown is best effort
            pass


class SandboxWebuiProxy:
    """Dumb byte pipe from one local port to one pod's webui endpoint (D1).

    Port-shaped on purpose: a path-shaped proxy would depend on the webui
    frontend using relative paths everywhere — an external application whose
    behavior we cannot verify. Port-shaped, the origin is the root path and no
    rewriting assumption exists.

    HTTP mode is one request per connection (``Connection: close`` on both
    legs; SSE is unaffected because the response body streams until the
    upstream closes). WS mode forwards the client's Upgrade with the injected
    headers and, on 101, splices the two sockets in both directions.

    Authentication is NOT the proxy's business (D1: a dumb pipe): the pod's
    webui validates tokens end-to-end against the per-instance secret, so a
    local open port is equivalent to today's local webui port. Every
    successful forward reports through ``on_activity`` — this is the only
    heartbeat the single-user form has.
    """

    def __init__(
        self,
        *,
        sandbox_id: str,
        upstream_resolver: Callable[[], tuple[str, dict[str, str]]],
        on_activity: Callable[[], None] | None = None,
        bind_host: str = "0.0.0.0",  # noqa: S104 - same exposure as the local webui port
        upstream_connect_timeout_seconds: float = 15.0,
        upstream_head_timeout_seconds: float = 15.0,
    ) -> None:
        """Store wiring; nothing binds until :meth:`start`.

        The two upstream timeouts are phase-scoped on purpose (M2): the
        connect budget applies only while dialing, the head budget only while
        waiting for the upstream's answer. Neither may leak into the body
        pump — a streaming response (SSE/WS) may legitimately idle between
        bytes far longer than either budget.
        """
        self.sandbox_id = sandbox_id
        self._upstream_resolver = upstream_resolver
        self._on_activity = on_activity
        self._bind_host = bind_host
        self._upstream_connect_timeout_seconds = upstream_connect_timeout_seconds
        self._upstream_head_timeout_seconds = upstream_head_timeout_seconds
        self._server: Any = None
        self.port: int | None = None
        self._greenlets: set[Any] = set()

    # ── lifecycle ────────────────────────────────────────────────────

    def start(self, port: int = 0) -> int:
        """Bind and start serving; returns the bound port."""
        import gevent.server

        if self._server is not None:
            return int(self.port or 0)
        self._server = gevent.server.StreamServer((self._bind_host, port), self._handle)
        self._server.start()  # cooperative: serves on this thread's hub
        self.port = int(self._server.address[1])
        logger.info(
            "webui proxy for sandbox %s listening on %s:%s",
            self.sandbox_id,
            self._bind_host,
            self.port,
        )
        return self.port

    def stop(self) -> None:
        """Close the listening socket and kill every in-flight greenlet."""
        import gevent

        greenlets = list(self._greenlets)
        self._greenlets.clear()
        for greenlet in greenlets:
            try:
                greenlet.kill()
            except Exception:  # noqa: BLE001 - stop must always succeed
                pass
        if self._server is not None:
            try:
                self._server.stop()
            except Exception:  # noqa: BLE001 - stop must always succeed
                pass
            self._server = None
        gevent.sleep(0)
        self.port = None

    # ── connection handling ──────────────────────────────────────────

    def _handle(self, client_sock: Any, _address: Any) -> None:
        """Serve exactly one client connection (HTTP or WS upgrade)."""
        import gevent

        greenlet = gevent.getcurrent()
        self._greenlets.add(greenlet)
        client = _BufferedSock(client_sock)
        upstream: _BufferedSock | None = None
        try:
            head = client.read_head()
            try:
                start_line, headers = _parse_head(head)
            except _MalformedHead as exc:
                # Client garbage (m2): 400, not 502 — the upstream was never
                # contacted.
                raise _BadRequest(f"malformed request head: {exc}") from exc
            self._validate_request(headers)
            method, path, _version = self._split_request_line(start_line)

            url, inject_headers = self._upstream_resolver()
            outbound = self._build_outbound_headers(headers, inject_headers)

            # A chunked request body is de-chunked BEFORE the head is sent:
            # its TE header is stripped (hop-by-hop), so the upstream needs a
            # real Content-Length instead of framing nothing.
            request_body: bytes | None = None
            lowered = {name.lower(): value for name, value in headers}
            if "transfer-encoding" in lowered:
                request_body = self._dechunk(client)
                outbound.append(("Content-Length", str(len(request_body))))
            elif "content-length" in lowered:
                # T-A: the value must be a non-empty run of ASCII digits.
                # int() alone would accept "-1", "5_0" (→50!), "+5" and
                # " 5" — every one of those frames a different body than
                # the upstream will read after re-serialization. isascii()
                # keeps Unicode digit-lookalikes (e.g. ² after iso-8859-1
                # decoding) from passing isdigit() and then blowing up
                # int() with an uncaught ValueError (review follow-up).
                raw_length = lowered["content-length"]
                if not raw_length or not raw_length.isascii() or not raw_length.isdigit():
                    raise _BadRequest("invalid Content-Length")
                length = int(raw_length)
                if length < 0:  # unreachable post-isdigit; depth in depth
                    raise _BadRequest("invalid Content-Length")
                request_body = client.read_exact(length) if length else b""

            if self._is_websocket_upgrade(headers):
                outbound = [
                    (name, value) for name, value in outbound if name.lower() != "connection"
                ] + [("Connection", "Upgrade")]

            sock, base_path = self._connect_upstream(url)
            upstream = _BufferedSock(sock)
            target = f"{base_path.rstrip('/')}{path}" if base_path.rstrip("/") else path
            upstream.sendall(_serialize_head(f"{method} {target} HTTP/1.1", outbound))
            if request_body:
                upstream.sendall(request_body)

            # M2: a bounded window for the upstream's ANSWER only. A gateway
            # that accepted the connection but never responds must not pin a
            # greenlet forever; once the head is in, the budget is cleared so
            # the body pump (SSE included) can idle between bytes unboundedly.
            sock.settimeout(self._upstream_head_timeout_seconds)
            try:
                response_head = upstream.read_head()
            except OSError as exc:
                raise _UpstreamError(f"upstream did not answer in time: {exc}") from exc
            finally:
                sock.settimeout(None)
            try:
                status_line, response_headers = _parse_head(response_head)
            except _MalformedHead as exc:
                # Upstream garbage (m2): 502 — the client's request was fine.
                raise _UpstreamError(f"malformed upstream response head: {exc}") from exc
            is_101 = status_line.split(" ", 2)[1:2] == ["101"]
            if self._is_websocket_upgrade(headers) and is_101:
                client.sendall(
                    _serialize_head(status_line, self._clean_response_headers(response_headers))
                )
                self._notify_activity()
                self._splice(client, upstream)
                upstream = None  # splice owns both sockets now
                return
            cleaned = [
                (name, value)
                for name, value in self._clean_response_headers(response_headers)
                if name.lower() != "connection"
            ] + [("Connection", "close")]
            client.sendall(_serialize_head(status_line, cleaned))
            self._notify_activity()
            # Single request per connection: relay the body raw (SSE included)
            # until the upstream closes, then the finally block closes client.
            self._pump(upstream, client)
        except _BadRequest as exc:
            self._send_simple(client, 400, "Bad Request", str(exc))
        except (_UpstreamError, OSError):
            self._send_simple(client, 502, "Bad Gateway", "upstream unavailable")
        except Exception:  # noqa: BLE001 - a dead connection must not kill the hub
            logger.exception("webui proxy error for sandbox %s", self.sandbox_id)
            self._send_simple(client, 502, "Bad Gateway", "proxy error")
        finally:
            self._greenlets.discard(greenlet)
            client.close()
            if upstream is not None:
                upstream.close()

    def _validate_request(self, headers: list[tuple[str, str]]) -> None:
        """Reject the malformed shapes D1 names: CL+TE, duplicate CL/injects.

        Duplicate Content-Length is a MUST-reject (RFC 7230 §3.3.2): a
        request smuggling vector, refused before the upstream is contacted —
        same 400 treatment as duplicated injection names (m2).
        """
        names = [name.lower() for name, _ in headers]
        if names.count("content-length") > 1:
            raise _BadRequest("duplicate Content-Length header")
        if "content-length" in names and "transfer-encoding" in names:
            raise _BadRequest("Content-Length and Transfer-Encoding are mutually exclusive")
        duplicated = sorted(name for name in _INJECTABLE_HEADER_NAMES if names.count(name) > 1)
        if duplicated:
            raise _BadRequest(f"duplicated injection headers: {duplicated}")

    def _build_outbound_headers(
        self, headers: list[tuple[str, str]], inject_headers: dict[str, str]
    ) -> list[tuple[str, str]]:
        """Strip client prefixes/hop-by-hop, then force-append the inject set.

        The inject map is appended AFTER the strip, so an injected name always
        overwrites whatever the client tried to send under the same name — and
        every client header in the credential prefixes is dropped regardless.
        """
        outbound: list[tuple[str, str]] = []
        for name, value in headers:
            lowered = name.lower()
            if lowered in _HOP_BY_HOP_REQUEST_HEADERS:
                continue
            if lowered.startswith(_INJECTED_HEADER_PREFIXES):
                continue
            outbound.append((name, value))
        outbound.append(("Connection", "close"))
        outbound.extend((str(key), str(value)) for key, value in inject_headers.items())
        return outbound

    def _clean_response_headers(self, headers: list[tuple[str, str]]) -> list[tuple[str, str]]:
        """Strip credential-namespace headers the browser never needs.

        The four injectable names are allowlisted through (the gateway may
        legitimately set them); anything else under the OpenSandbox-/
        X-EXECD-/OPENSANDBOX- prefixes is dropped before the bytes reach the
        browser. Framing headers (Content-Length / Transfer-Encoding) are kept
        verbatim because the body is relayed raw behind them.
        """
        cleaned: list[tuple[str, str]] = []
        for name, value in headers:
            lowered = name.lower()
            if lowered.startswith(_INJECTED_HEADER_PREFIXES) and (
                lowered not in _INJECTABLE_HEADER_NAMES
            ):
                continue
            cleaned.append((name, value))
        return cleaned

    def _dechunk(self, client: _BufferedSock) -> bytes:
        """Read one chunked body to its terminator, bounded."""
        body = bytearray()
        while True:
            size_line = client.read_line()
            try:
                size = int(size_line.split(b";", 1)[0].strip() or b"0", 16)
            except ValueError as exc:
                raise _BadRequest("malformed chunk size") from exc
            if size < 0:
                # int(hex) happily parses "-5"; a negative size is framing
                # garbage and must be refused, not "read" (m3).
                raise _BadRequest("negative chunk size")
            if size == 0:
                while True:  # trailers up to the blank line
                    line = client.read_line()
                    if line.strip() in (b"", b"\r\n"):
                        break
                break
            if len(body) + size > _MAX_DECHUNK_BYTES:
                raise _BadRequest("chunked request body over the proxy limit")
            body.extend(client.read_exact(size))
            client.read_line()  # the CRLF after each chunk
        return bytes(body)

    def _splice(self, client: _BufferedSock, upstream: _BufferedSock) -> None:
        """Two-direction raw pump for an established 101 connection."""
        import gevent

        def pump(source: _BufferedSock, destination: _BufferedSock) -> None:
            try:
                self._pump(source, destination)
            finally:
                source.close()
                destination.close()

        up_greenlet = gevent.spawn(pump, client, upstream)
        down_greenlet = gevent.spawn(pump, upstream, client)
        self._greenlets.add(up_greenlet)
        self._greenlets.add(down_greenlet)
        try:
            gevent.joinall([up_greenlet, down_greenlet], raise_error=True)
        finally:
            self._greenlets.discard(up_greenlet)
            self._greenlets.discard(down_greenlet)

    def _pump(self, source: _BufferedSock, destination: _BufferedSock) -> None:
        """Relay raw bytes until the source closes or a write fails."""
        while True:
            chunk = source.pump_from()
            if not chunk:
                return
            destination.sendall(chunk)

    @staticmethod
    def _split_request_line(start_line: str) -> tuple[str, str, str]:
        """Split ``METHOD PATH HTTP/x``; refuse anything else."""
        parts = start_line.split(" ", 2)
        if len(parts) != 3:
            raise _BadRequest(f"malformed request line {start_line!r}")
        return parts[0], parts[1], parts[2]

    @staticmethod
    def _is_websocket_upgrade(headers: list[tuple[str, str]]) -> bool:
        """Return True when the request asks for a websocket Upgrade.

        Connection tokens are comma-separated with OPTIONAL whitespace (RFC
        7230 §6.1 list syntax), so ``Upgrade,keep-alive`` (no space) must
        still register (m7).
        """
        lowered = {name.lower(): value.lower() for name, value in headers}
        connection_tokens = [token.strip() for token in lowered.get("connection", "").split(",")]
        return lowered.get("upgrade", "") == "websocket" and "upgrade" in connection_tokens

    def _connect_upstream(self, url: str) -> tuple[Any, str]:
        """Open the upstream connection (url from the endpoint resolver)."""
        from urllib.parse import urlparse

        from gevent import socket as gevent_socket

        parsed = urlparse(url)
        host = parsed.hostname or ""
        if not host:
            raise _UpstreamError(f"upstream url {url!r} has no host")
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        try:
            sock = gevent_socket.create_connection(
                (host, port), timeout=self._upstream_connect_timeout_seconds
            )
        except OSError as exc:
            raise _UpstreamError(f"upstream {host}:{port} unreachable: {exc}") from exc
        if parsed.scheme == "https":
            from gevent import ssl as gevent_ssl

            sock = gevent_ssl.wrap_socket(sock, server_hostname=host)
        # Issue #3378 review (M2): create_connection leaves its connect
        # timeout installed on the socket; it must be cleared or it becomes
        # a whole-session read timeout, and pump_from treats a read timeout
        # as peer-close — truncating any stream that idles longer than the
        # connect budget. The response head keeps its own bounded window in
        # _handle; the body pump is deliberately unbounded.
        sock.settimeout(None)
        return sock, parsed.path or ""

    def _notify_activity(self) -> None:
        """Report one successful forward to the activity callback."""
        if self._on_activity is None:
            return
        try:
            self._on_activity()
        except Exception:  # noqa: BLE001 - bookkeeping must never break the pipe
            logger.exception("webui proxy activity callback failed")

    def _send_simple(self, sock: _BufferedSock, status: int, phrase: str, reason: str) -> None:
        """Write a minimal error response (best effort)."""
        body = reason.encode()
        head = (
            f"HTTP/1.1 {status} {phrase}\r\n"
            "Content-Type: text/plain\r\n"
            f"Content-Length: {len(body)}\r\n"
            "Connection: close\r\n\r\n"
        ).encode()
        try:
            sock.sendall(head + body)
        except Exception:  # noqa: BLE001 - the client may already be gone
            pass


class _BadRequest(Exception):
    """A client request the D1 rules refuse with 400."""


class _MalformedHead(Exception):
    """A head that does not parse as start-line + colon-separated headers."""


class _UpstreamError(Exception):
    """The upstream endpoint could not be reached (502)."""


# ── D5: orphan reconcile (positive-trigger, web-process-hosted) ──────────────

# The ONLY production host of the reconcile is the gunicorn worker startup
# hook (app/gunicorn_worker.py), env-gated below; server.py's dev __main__
# path spawns the same helper on its own greenlet. Management scripts import
# create_app without ever setting this env, so they can never reconcile.
RECONCILE_ENV = "OPENACE_WEBUI_ORPHAN_RECONCILE"


def maybe_spawn_webui_orphan_reconcile() -> bool:
    """Env-gated, TESTING-guarded, fail-soft reconcile spawn (D5/FEAS-R4-3).

    Returns True when a reconcile greenlet was spawned. The reconcile work
    (list + per-orphan export + destroy + polling) NEVER runs inline in the
    caller's greenlet — the worker boot hook and server.py's dev path both
    spawn it separately, so a slow sweep cannot block request serving.
    """
    if os.environ.get(RECONCILE_ENV) != "1":
        return False
    # TESTING guard (FEAS-M3), matching the app/__init__.py precedent: a test
    # process must never sweep real sandbox backends.
    if os.environ.get("PYTEST_VERSION") or os.environ.get("TESTING"):
        logger.info("webui orphan reconcile skipped (test process)")
        return False

    def _run() -> None:
        try:
            # Lazy module import: the backend module imports this one.
            from app.services import webui_sandbox_opensandbox

            webui_sandbox_opensandbox.reconcile_webui_orphans()
        except Exception:  # noqa: BLE001 - fail-soft: web boot must not fail
            logger.exception("webui orphan reconcile failed (fail-soft)")

    import gevent

    gevent.spawn(_run)
    logger.info("webui orphan reconcile spawned (worker startup)")
    return True


def maybe_spawn_webui_heartbeat_timer() -> bool:
    """Env-gated, TESTING-guarded heartbeat refresh greenlet (F-2③).

    Returns True when a timer greenlet was spawned. The heartbeat refresh is
    a FIXED 300s cadence driven by this timer — the single refresh source
    (the manager's maintenance loop no longer refreshes it, and a replica
    without a manager instance must still heartbeat). The first write happens
    immediately at spawn so presence is registered from worker boot; every
    write is fail-soft. HEARTBEAT_FRESH_WINDOW_SECONDS is derived from this
    constant cadence (2*300 + 60), so the window derivation stays sound.
    """
    if os.environ.get(RECONCILE_ENV) != "1":
        return False
    # TESTING guard, same as the reconcile: a test process must never touch
    # the real heartbeat state root.
    if os.environ.get("PYTEST_VERSION") or os.environ.get("TESTING"):
        logger.info("webui heartbeat timer skipped (test process)")
        return False

    import gevent

    def _loop() -> None:
        while True:
            try:
                write_webui_heartbeat()
            except Exception:  # noqa: BLE001 - fail-soft: the web boot must not fail
                logger.exception("webui heartbeat refresh failed (fail-soft)")
            gevent.sleep(HEARTBEAT_REFRESH_SECONDS)

    gevent.spawn(_loop)
    logger.info("webui heartbeat timer spawned (%ss cadence)", int(HEARTBEAT_REFRESH_SECONDS))
    return True
