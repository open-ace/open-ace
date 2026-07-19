"""Custom gevent WSGI handler that processes remote WebSocket upgrades.

Handles WebSocket upgrades for both remote terminals and remote VSCode
(code-server) sessions. Bypasses geventwebsocket entirely for these
paths, handling the WebSocket handshake and framing directly on the raw
socket.

HA Support (Issue #1851):
- Cross-Pod relay state awareness via Redis
- Close frame redirect (code=3010) for browser WebSocket
- Graceful degradation when Redis is unavailable

For all other requests the handler delegates to the normal WSGIHandler
flow (including geventwebsocket if it is configured).
"""

from __future__ import annotations

import hmac
import logging
import os
import re
import threading
from http.cookies import SimpleCookie
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from gevent.pywsgi import WSGIHandler

import app.ws_frame as ws_frame
from app.modules.workspace.relay_distributed_store import (
    REDIRECT_CLOSE_CODE,
    RelayDistributedStore,
    RelayType,
    get_relay_distributed_store,
)

logger = logging.getLogger(__name__)


class _RawSocketRelayWrapper:
    """Wrapper for raw socket to provide WebSocket-like interface for relay.

    This wrapper provides send/receive methods compatible with the bridge functions,
    using ws_frame for actual I/O on the raw socket.
    """

    def __init__(self, socket, close_event=None):
        self._socket = socket
        self._closed = False
        self._close_event = close_event

    def send(self, data) -> None:
        """Send data through the relay socket."""
        if self._closed:
            raise ConnectionError("Socket is closed")
        try:
            ws_frame.send_message(self._socket, data)
        except Exception:
            self._closed = True
            if self._close_event:
                self._close_event.set()
            raise

    def recv(self):
        """Receive data from the relay socket."""
        if self._closed:
            raise ConnectionError("Socket is closed")
        try:
            result = ws_frame.recv_message(self._socket)
            if result is None:
                self._closed = True
                if self._close_event:
                    self._close_event.set()
            return result
        except Exception:
            self._closed = True
            if self._close_event:
                self._close_event.set()
            raise

    def close(self, code: int = 1000, reason: str = "") -> None:
        """Close the relay socket."""
        if not self._closed:
            self._closed = True
            if self._close_event:
                self._close_event.set()
            try:
                ws_frame.send_close(self._socket, code, reason)
            except Exception:
                pass

    @property
    def closed(self) -> bool:
        """Check if the socket is closed."""
        return self._closed


def _is_private_ip(ws_url: str) -> bool:
    """Check if a WebSocket URL points to a private IP address.

    Private IP ranges:
    - 10.0.0.0 - 10.255.255.255 (10.x.x.x)
    - 172.16.0.0 - 172.31.255.255 (172.16-31.x.x)
    - 192.168.0.0 - 192.168.255.255 (192.168.x.x)
    - 127.0.0.0 - 127.255.255.255 (loopback)
    """
    import re
    from urllib.parse import urlparse

    try:
        parsed = urlparse(ws_url)
        host = parsed.hostname or ""
        # Check for private IP patterns
        private_patterns = [
            r"^10\.",  # 10.x.x.x
            r"^172\.(1[6-9]|2[0-9]|3[01])\.",  # 172.16-31.x.x
            r"^192\.168\.",  # 192.168.x.x
            r"^127\.",  # loopback
            r"^169\.254\.",  # link-local
            r"^0\.0\.0\.0$",  # unspecified
        ]
        return any(re.match(pattern, host) for pattern in private_patterns)
    except Exception:
        return False


def _get_backend_private_ip_prefix() -> str | None:
    """Get the private IP network prefix of the backend server.

    Returns the first two octets of the backend's private IP (e.g., "192.168")
    if the backend is on a private network, otherwise None.
    """
    import socket

    try:
        # Get backend's primary IP by connecting to an external address
        # (doesn't actually send data, just determines local IP)
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.1)
        try:
            # Connect to a public IP (Google DNS) to determine local interface
            s.connect(("8.8.8.8", 80))
            local_ip = s.getsockname()[0]
        finally:
            s.close()

        # Check if local IP is private and return prefix
        parts = local_ip.split(".")
        if len(parts) >= 2:
            prefix = f"{parts[0]}.{parts[1]}"
            # Check if it's a private IP range
            if local_ip.startswith("10."):
                return "10."  # Class A private
            elif local_ip.startswith("192.168."):
                return "192.168"  # Class C private
            elif re.match(r"^172\.(1[6-9]|2[0-9]|3[01])\.", local_ip):
                return prefix  # Class B private (172.16-31)
        return None
    except Exception:
        return None


def _can_reach_directly(ws_url: str) -> bool:
    """Check if backend can directly reach the remote WebSocket server.

    Returns True if:
    - The URL is a public IP (always reachable)
    - The URL is a private IP in the same network segment as backend

    Returns False if:
    - The URL is a private IP in a different network segment
    - Quick TCP probe fails (optional check)
    """
    from urllib.parse import urlparse

    try:
        parsed = urlparse(ws_url)
        host = parsed.hostname or ""
        port = parsed.port or 80

        # Public IP - assume reachable
        if not _is_private_ip(ws_url):
            return True

        # Private IP - check if in same network segment as backend
        backend_prefix = _get_backend_private_ip_prefix()
        if backend_prefix:
            # Extract target IP prefix
            parts = host.split(".")
            if len(parts) >= 2:
                target_prefix = f"{parts[0]}.{parts[1]}"

                # Same private network segment - likely reachable
                if host.startswith("10.") and backend_prefix == "10.":
                    return True  # Both in 10.x.x.x
                if host.startswith("192.168.") and backend_prefix == "192.168":
                    return True  # Both in 192.168.x.x
                if re.match(r"^172\.(1[6-9]|2[0-9]|3[01])\.", host):
                    # Class B - check if in same 172.16-31 range
                    if re.match(r"^172\.(1[6-9]|2[0-9]|3[01])\.", backend_prefix or ""):
                        # Both in 172.16-31 range, check exact subnet
                        backend_parts = backend_prefix.split(".")
                        if len(backend_parts) >= 2:
                            # Same Class B subnet (first two octets match)
                            if target_prefix == backend_prefix:
                                return True

        # Different private network - try quick TCP probe
        import socket

        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(0.5)  # Quick timeout
            s.connect((host, port))
            s.close()
            return True  # Connection succeeded
        except OSError:
            return False  # Cannot reach
    except Exception:
        return False


def _needs_relay(ws_url: str) -> bool:
    """Determine if relay is needed for connecting to remote terminal.

    Relay is needed when backend cannot directly reach the remote WebSocket URL.
    Results are cached per ws_url to avoid repeated TCP probes (SSRF mitigation).
    """
    return not _can_reach_directly(ws_url)


# Cache reachability results to avoid repeated TCP probes per ws_url.
# This mitigates SSRF via repeated probe requests.
_reachability_cache: dict[str, bool] = {}
_reachability_cache_lock = threading.Lock()


def _needs_relay_cached(ws_url: str) -> bool:
    """Cached version of _needs_relay to avoid repeated TCP probes."""
    with _reachability_cache_lock:
        if ws_url in _reachability_cache:
            return _reachability_cache[ws_url]
    result = _needs_relay(ws_url)
    with _reachability_cache_lock:
        _reachability_cache[ws_url] = result
    return result


_WS_PATH_RE = re.compile(
    r"^/api/remote/terminal/"
    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
    r"/ws$",
    re.IGNORECASE,
)

# Agent relay WebSocket path - agent connects here for terminal relay
_AGENT_RELAY_WS_PATH_RE = re.compile(
    r"^/api/remote/agent/terminal-relay/"
    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
    r"$",
    re.IGNORECASE,
)

_VSCODE_LEGACY_WS_PATH_RE = re.compile(
    r"^/api/remote/vscode/"
    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
    r"/ws$",
    re.IGNORECASE,
)

_VSCODE_PROXY_WS_PATH_RE = re.compile(
    r"^/api/remote/vscode/"
    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
    r"/proxy(?P<path>/.*)?$",
    re.IGNORECASE,
)


def _match_vscode_ws_path(path: str) -> tuple[str, str] | None:
    """Return (vscode_id, upstream_path) for VSCode websocket proxy paths."""
    legacy_match = _VSCODE_LEGACY_WS_PATH_RE.match(path)
    if legacy_match:
        return legacy_match.group(1), "/"

    proxy_match = _VSCODE_PROXY_WS_PATH_RE.match(path)
    if proxy_match:
        return proxy_match.group(1), proxy_match.group("path") or "/"

    return None


def _query_token_and_upstream_query(query: str) -> tuple[str, str]:
    """Extract Open ACE token and remove it from the upstream query string."""
    token = ""
    upstream_pairs = []
    for key, value in parse_qsl(query, keep_blank_values=True):
        if key == "token":
            if not token:
                token = value
            continue
        upstream_pairs.append((key, value))
    return token, urlencode(upstream_pairs)


def _parse_token_from_query(query_string: str) -> str:
    """Extract the token parameter from a raw query string."""
    for part in query_string.split("&"):
        if part.startswith("token="):
            return part[6:]
    return ""


def _cookie_value(cookie_header: str, name: str) -> str:
    if not cookie_header:
        return ""
    try:
        cookies = SimpleCookie()
        cookies.load(cookie_header)
        morsel = cookies.get(name)
        return morsel.value if morsel else ""
    except Exception:
        return ""


def _build_vscode_remote_ws_url(original_http_url: str, upstream_path: str, query: str) -> str:
    """Build the remote code-server websocket URL for a proxied browser path."""
    parsed = urlparse(original_http_url)
    ws_scheme = "wss" if parsed.scheme == "https" else "ws"
    return urlunparse(
        parsed._replace(
            scheme=ws_scheme,
            path=upstream_path or "/",
            params="",
            query=query,
            fragment="",
        )
    )


class RemoteWSHandler(WSGIHandler):
    """WSGI handler that intercepts remote terminal and VSCode WebSocket upgrades."""

    def run_application(self) -> None:
        if self._is_agent_relay_ws_request():
            self._handle_agent_relay_ws()
            return
        if self._is_terminal_ws_request():
            self._handle_terminal_ws()
            return
        if self._is_vscode_ws_request():
            self._handle_vscode_ws()
            return
        # Non-terminal: fall through to normal WSGI handling.
        super().run_application()

    # ------------------------------------------------------------------
    # Agent relay WebSocket handling
    # ------------------------------------------------------------------

    def _is_agent_relay_ws_request(self) -> bool:
        if self.command != "GET":
            return False
        upgrade = self.environ.get("HTTP_UPGRADE", "").lower()
        if upgrade != "websocket":
            return False
        path = self.environ.get("PATH_INFO", "")
        return _AGENT_RELAY_WS_PATH_RE.match(path) is not None

    def _handle_agent_relay_ws(self) -> None:
        """Handle agent WebSocket connection for terminal relay.

        When remote machines are on private networks, the agent connects
        to this endpoint to establish a relay WebSocket. The backend then
        bridges browser connections through this relay to the remote terminal.
        """
        path = self.environ.get("PATH_INFO", "")
        m = _AGENT_RELAY_WS_PATH_RE.match(path)
        assert m is not None
        terminal_id = m.group(1)

        # WebSocket handshake directly on the raw socket.
        try:
            ws_frame.perform_handshake(self.environ, self.socket)
        except Exception:
            logger.exception("Agent relay WS handshake failed for %s", terminal_id[:8])
            self.close_connection = True
            return

        # Parse token from query string.
        token = _parse_token_from_query(self.environ.get("QUERY_STRING", ""))

        # Validate token against terminal info store.
        from app.modules.workspace.terminal_relay_store import terminal_relay_store
        from app.modules.workspace.terminal_store import terminal_info_store

        found = terminal_info_store.find_by_terminal_id(terminal_id)
        if not found:
            logger.warning("Agent relay WS: unknown terminal %s", terminal_id[:8])
            ws_frame.send_close(self.socket, 1011)
            self.close_connection = True
            return

        machine_id, info = found

        # For agent relay, we use the original_token (the terminal server's token)
        # Agent must present this token to authenticate
        original_token = info.get("original_token", "")
        if not token or not original_token or not hmac.compare_digest(token, original_token):
            logger.warning("Agent relay WS: invalid token for terminal %s", terminal_id[:8])
            ws_frame.send_close(self.socket, 4001)
            self.close_connection = True
            return

        logger.info(
            "Agent relay WS: registered relay for terminal %s from machine %s",
            terminal_id[:8],
            machine_id[:8],
        )

        # Create relay wrapper and close event for lifecycle management
        from gevent.event import Event

        close_event = Event()

        relay_ws = _RawSocketRelayWrapper(self.socket, close_event)
        terminal_relay_store.register_relay(terminal_id, relay_ws, token)

        # Keep relay alive until close_event is set (by bridge or agent disconnection)
        try:
            close_event.wait()
            logger.info("Agent relay WS: close_event signaled for terminal %s", terminal_id[:8])
        except Exception as e:
            logger.info("Agent relay WS: connection ended for terminal %s: %s", terminal_id[:8], e)
        finally:
            terminal_relay_store.unregister_relay(terminal_id)

        self.close_connection = True

    # ------------------------------------------------------------------
    # Terminal WebSocket handling (browser connections)
    # ------------------------------------------------------------------

    def _is_terminal_ws_request(self) -> bool:
        if self.command != "GET":
            return False
        upgrade = self.environ.get("HTTP_UPGRADE", "").lower()
        if upgrade != "websocket":
            return False
        path = self.environ.get("PATH_INFO", "")
        return _WS_PATH_RE.match(path) is not None

    def _handle_terminal_ws(self) -> None:
        path = self.environ.get("PATH_INFO", "")
        m = _WS_PATH_RE.match(path)
        assert m is not None
        terminal_id = m.group(1)

        # WebSocket handshake directly on the raw socket.
        try:
            ws_frame.perform_handshake(self.environ, self.socket)
        except Exception:
            logger.exception("Terminal WS handshake failed for %s", terminal_id[:8])
            self.close_connection = True
            return

        # Parse token from query string.
        token = _parse_token_from_query(self.environ.get("QUERY_STRING", ""))

        # Look up terminal info.
        from app.modules.workspace.terminal_store import terminal_info_store

        found = terminal_info_store.find_by_terminal_id(terminal_id)
        if not found:
            logger.warning("Terminal WS handler: unknown terminal %s", terminal_id[:8])
            ws_frame.send_close(self.socket, 1011)
            self.close_connection = True
            return

        machine_id, info = found

        # Validate token.
        stored_token = info.get("token", "")
        if not token or not stored_token or not hmac.compare_digest(token, stored_token):
            logger.warning("Terminal WS handler: invalid token for terminal %s", terminal_id[:8])
            ws_frame.send_close(self.socket, 4001)
            self.close_connection = True
            return

        # HA: Check if relay is owned by another Pod (Issue #1851)
        redirect_url = self._check_relay_redirect(terminal_id, token)
        if redirect_url:
            # Relay is owned by another Pod - send redirect close frame
            logger.info(
                "Terminal WS handler: redirecting terminal %s to owner pod",
                terminal_id[:8],
            )
            ws_frame.send_close(self.socket, REDIRECT_CLOSE_CODE, redirect_url)
            self.close_connection = True
            return

        # Check if relay connection exists (for private network machines).
        from app.modules.workspace.terminal_relay_store import terminal_relay_store

        # Use add_pending_browser for ALL relay paths to ensure _active_bridges
        # tracking is consistent and concurrent access is prevented.
        if terminal_relay_store.has_relay(terminal_id) or _needs_relay_cached(
            info.get("original_ws_url") or info.get("ws_url", "")
        ):
            from gevent.event import Event

            bridge_done_event = Event()
            added = terminal_relay_store.add_pending_browser(
                terminal_id, self.socket, bridge_done_event
            )
            if added:
                # No relay yet - wait for relay to connect and bridge
                try:
                    bridge_done_event.wait(timeout=30.0)
                    if not bridge_done_event.is_set():
                        logger.warning(
                            "Terminal WS handler: timeout waiting for relay for terminal %s",
                            terminal_id[:8],
                        )
                        terminal_relay_store.remove_pending_browser(terminal_id, self.socket)
                        ws_frame.send_close(self.socket, 1001, "Relay timeout")
                        self.close_connection = True
                    else:
                        self.close_connection = False
                except Exception as e:
                    logger.info(
                        "Terminal WS handler: browser wait ended for terminal %s: %s",
                        terminal_id[:8],
                        e,
                    )
                    self.close_connection = True
            else:
                # Bridge was started by the store (or browser was rejected).
                # Wait for bridge completion — no timeout, terminal sessions
                # are long-lived and should only end when user disconnects.
                try:
                    bridge_done_event.wait()
                except Exception:
                    pass
                self.close_connection = True
            return

        # No relay needed - check if we should try direct connection.
        remote_ws_url = info.get("original_ws_url") or info.get("ws_url", "")
        remote_token = info.get("original_token", "")
        if not remote_ws_url or remote_ws_url.startswith("/"):
            logger.error("Terminal WS handler: missing remote URL for terminal %s", terminal_id[:8])
            ws_frame.send_close(self.socket, 1011)
            self.close_connection = True
            return

        # Direct connection to remote terminal (public IP, no relay needed).
        try:
            from app.modules.workspace.terminal_ws_bridge import bridge_terminal_websocket_raw

            logger.info(
                "Terminal WS handler: bridging %s for machine %s",
                terminal_id[:8],
                machine_id[:8],
            )
            bridge_terminal_websocket_raw(terminal_id, self.socket, remote_ws_url, remote_token)
        except Exception:
            logger.exception("Terminal WS handler: bridge failed for terminal %s", terminal_id[:8])
            try:
                ws_frame.send_close(self.socket, 1011)
            except Exception:
                pass

        self.close_connection = True

    # ------------------------------------------------------------------
    # VSCode (code-server) WebSocket handling
    # ------------------------------------------------------------------

    def _is_vscode_ws_request(self) -> bool:
        if self.command != "GET":
            return False
        upgrade = self.environ.get("HTTP_UPGRADE", "").lower()
        if upgrade != "websocket":
            return False
        path = self.environ.get("PATH_INFO", "")
        return _match_vscode_ws_path(path) is not None

    def _handle_vscode_ws(self) -> None:
        path = self.environ.get("PATH_INFO", "")
        matched = _match_vscode_ws_path(path)
        assert matched is not None
        vscode_id, upstream_path = matched

        # WebSocket handshake directly on the raw socket.
        try:
            ws_frame.perform_handshake(self.environ, self.socket)
        except Exception:
            logger.exception("VSCode WS handshake failed for %s", vscode_id[:8])
            self.close_connection = True
            return

        # Parse token from query string.
        query = self.environ.get("QUERY_STRING", "")
        token, upstream_query = _query_token_and_upstream_query(query)

        # Look up VSCode info.
        from app.modules.workspace.vscode_store import vscode_info_store

        found = vscode_info_store.find_by_vscode_id(vscode_id)
        if not found:
            logger.warning("VSCode WS handler: unknown vscode %s", vscode_id[:8])
            ws_frame.send_close(self.socket, 1011)
            self.close_connection = True
            return

        machine_id, info = found

        # Validate token.
        stored_token = info.get("token", "")
        if not token:
            token = _cookie_value(
                self.environ.get("HTTP_COOKIE", ""),
                f"vscode_token_{vscode_id}",
            )
        if not token and info.get("status") == "running":
            token = stored_token
        if not token or not stored_token or not hmac.compare_digest(token, stored_token):
            logger.warning("VSCode WS handler: invalid token for vscode %s", vscode_id[:8])
            ws_frame.send_close(self.socket, 4001)
            self.close_connection = True
            return

        # HA: Check if relay is owned by another Pod (Issue #1851)
        redirect_url = self._check_relay_redirect(vscode_id, token, RelayType.VSCODE)
        if redirect_url:
            # Relay is owned by another Pod - send redirect close frame
            logger.info(
                "VSCode WS handler: redirecting vscode %s to owner pod",
                vscode_id[:8],
            )
            ws_frame.send_close(self.socket, REDIRECT_CLOSE_CODE, redirect_url)
            self.close_connection = True
            return

        original_http_url = info.get("original_http_url", "")
        if not original_http_url:
            logger.error("VSCode WS handler: missing remote URL for vscode %s", vscode_id[:8])
            ws_frame.send_close(self.socket, 1011)
            self.close_connection = True
            return

        remote_ws_url = _build_vscode_remote_ws_url(
            original_http_url,
            upstream_path,
            upstream_query,
        )

        # Bridge browser socket to remote code-server.
        try:
            from app.modules.workspace.vscode_ws_bridge import bridge_vscode_ws_raw

            logger.info(
                "VSCode WS handler: bridging %s for machine %s",
                vscode_id[:8],
                machine_id[:8],
            )
            bridge_vscode_ws_raw(vscode_id, self.socket, remote_ws_url)
        except Exception:
            logger.exception("VSCode WS handler: bridge failed for vscode %s", vscode_id[:8])
            try:
                ws_frame.send_close(self.socket, 1011)
            except Exception:
                pass

        self.close_connection = True

    # ------------------------------------------------------------------
    # HA Helper Methods (Issue #1851)
    # ------------------------------------------------------------------

    def _check_relay_redirect(self, relay_id: str, token: str, relay_type: RelayType = RelayType.TERMINAL) -> str | None:
        """Check if relay should be redirected to another Pod.

        Queries Redis to check if the relay is owned by another Pod.
        If so, returns the redirect URL. If this Pod is the owner or
        Redis is unavailable, returns None.

        Args:
            relay_id: The terminal or vscode ID
            token: Authentication token for the relay
            relay_type: Type of relay (terminal or vscode)

        Returns:
            Redirect URL if should redirect, None otherwise
        """
        try:
            distributed_store = get_relay_distributed_store()
            if not distributed_store.is_redis_available():
                # Redis unavailable - fallback to local mode
                logger.debug(
                    "Redis unavailable, falling back to local relay mode for %s",
                    relay_id[:8],
                )
                return None

            relay_state = distributed_store.get_relay_owner(relay_type, relay_id)

            if relay_state is None:
                # No owner registered - this Pod will handle it
                return None

            pod_name = os.environ.get("POD_NAME", "unknown")
            if relay_state.owner_pod == pod_name:
                # This Pod is the owner - handle locally
                return None

            # Another Pod owns the relay - build redirect URL
            redirect_url = self._build_redirect_url(relay_state.owner_pod, relay_id, token)
            logger.info(
                "Relay %s owned by pod %s, redirecting to %s",
                relay_id[:8],
                relay_state.owner_pod,
                redirect_url[:50] + "...",
            )
            return redirect_url

        except Exception as e:
            logger.warning(
                "Failed to check relay redirect for %s: %s, falling back to local mode",
                relay_id[:8],
                e,
            )
            return None

    def _build_redirect_url(self, owner_pod: str, relay_id: str, token: str) -> str:
        """Build redirect URL for a relay owned by another Pod.

        Uses the headless Service to connect directly to the owner Pod.

        Args:
            owner_pod: Name of the Pod that owns the relay
            relay_id: The terminal or vscode ID
            token: Authentication token

        Returns:
            WebSocket URL for the owner Pod
        """
        # Use headless Service for direct Pod-to-Pod communication
        # Format: ws://{pod_name}.{headless_service}.{namespace}.svc.cluster.local:{port}/...
        namespace = os.environ.get("POD_NAMESPACE", "open-ace")
        port = 19888

        # Build WebSocket URL
        # Use the same path but with the owner Pod's address
        path = self.environ.get("PATH_INFO", "")
        query = self.environ.get("QUERY_STRING", "")

        # Build redirect URL
        if query:
            redirect_url = f"ws://{owner_pod}.open-ace-headless.{namespace}.svc.cluster.local:{port}{path}?{query}"
        else:
            redirect_url = f"ws://{owner_pod}.open-ace-headless.{namespace}.svc.cluster.local:{port}{path}"

        # Ensure token is included
        if "token=" not in redirect_url and token:
            separator = "&" if "?" in redirect_url else "?"
            redirect_url = f"{redirect_url}{separator}token={token}"

        return redirect_url
