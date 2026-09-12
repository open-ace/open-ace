"""
Open ACE - AI Computing Explorer - WebUI Manager Service

Manages per-user qwen-code-webui processes in multi-user mode.
Each user gets an independent webui process running under their system_account.
"""

import hashlib
import hmac
import json
import logging
import os
import platform
import pwd
import secrets
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, cast

import gevent
from gevent import lock as gevent_lock

from app.utils.workspace import ensure_system_user as _ensure_user_shared
from app.utils.workspace import run_as_root_if_needed

logger = logging.getLogger(__name__)

# Environment variable keys that are handled by sudoers env_keep
# (preserved automatically) and should NOT be inlined via
# openace-webui-launch. Everything else (dynamic envKeys from model
# pools, etc.) gets inlined automatically.
_WEBUI_ENV_SUDO_KNOWN_KEYS = frozenset(
    {
        "PATH",
        "OPENACE_PROXY_TOKEN",
        "OPENACE_PROXY_URL",
        "OPENACE_MODEL",
        "OPENACE_LOG_DIR",
        "SESSION_TIMEOUT_MS",
        "KEEPALIVE_INTERVAL_MS",
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "LANG",
        "LC_ALL",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
    }
)

# Path to the secure launch wrapper that enables inline env-var
# passthrough via /usr/bin/env without granting unrestricted env access
# in sudoers (Issue #2305 review).
_WEBUI_LAUNCH_WRAPPER = "/usr/local/bin/openace-webui-launch"

# Readiness-probe memoization window (both directions) so degraded hosts
# cannot be loop-polled into repeated probe work (Issue #3374 review #9).
_PROBE_MEMO_TTL_SECONDS = 30.0

# Issue #3378 (D5): how often the cleanup loop runs sandbox maintenance
# (snapshot export + renew clamp) for live sandboxed instances. 5 minutes is
# the documented crash-loss window ("crash 丢 ≤5min 增量").
SANDBOX_MAINTENANCE_INTERVAL_SECONDS = 300.0

WEBUI_FORM_LOCAL = "local"
WEBUI_FORM_SANDBOXED = "sandboxed"


@dataclass
class WebUIInstance:
    """Represents a running webui instance for a user."""

    user_id: int
    system_account: str
    port: int
    pid: int | None = None
    token: str = ""
    allocated_at: datetime = field(default_factory=datetime.now)
    last_activity: datetime = field(default_factory=datetime.now)
    process: subprocess.Popen | None = None
    url: str = ""
    session_model_pool: dict[str, Any] = field(default_factory=dict)

    # ── Issue #3378: the sandboxed form ───────────────────────────────
    # "local" (child process under an OS account) or "sandboxed" (webui pod
    # behind a per-instance local port proxy). Everything below is only
    # meaningful for the sandboxed form.
    form: str = "local"
    sandbox_id: str = ""
    sandbox_tier: str = ""
    # D6 export guard ground truth: set only when the launcher confirmed the
    # restore-done touch; a degraded instance never exports its empty tree.
    restore_confirmed: bool = False
    # Per-instance webui token secret (D2/B1): the pod's webui validates v2
    # tokens against THIS secret, not the manager's global one.
    token_secret: str = ""
    # The LLM proxy token baked into the pod env (renew clamps to its expiry).
    proxy_token: str = ""
    launcher: Any = None
    proxy: Any = None

    _last_health_check: float = 0.0
    _health_check_ttl: float = 30.0  # Cache health check result for 30s
    _consecutive_health_failures: int = 0
    _max_consecutive_failures: int = 10  # 10 × 30s = ~5min before declaring dead

    def is_alive(self) -> bool:
        """Check if the process is still running and responsive."""
        if self.form == "sandboxed":
            return self._is_sandbox_alive()
        if self.pid is None:
            return False
        try:
            os.kill(self.pid, 0)
        except (OSError, ProcessLookupError):
            return False

        # Cached: don't re-check within TTL window
        now = time.time()
        if now - self._last_health_check < self._health_check_ttl:
            return True

        healthy = self._check_http_health()
        if healthy:
            self._consecutive_health_failures = 0
            self._last_health_check = now
            return True

        # HTTP failed — count consecutive failures
        self._consecutive_health_failures += 1
        self._last_health_check = now
        if self._consecutive_health_failures >= self._max_consecutive_failures:
            logger.warning(
                f"WebUI instance (pid={self.pid}, port={self.port}) "
                f"unresponsive for {self._consecutive_health_failures} consecutive checks "
                f"(~{self._consecutive_health_failures * 30}s), declaring dead"
            )
            return False

        logger.info(
            f"WebUI health check failed ({self._consecutive_health_failures}/"
            f"{self._max_consecutive_failures}) for pid={self.pid}, port={self.port}"
            f" — still alive, will retry"
        )
        return True

    def _is_sandbox_alive(self) -> bool:
        """Sandboxed liveness: the pod has no local pid — probe the webui.

        The probe goes through the local D1 proxy with a freshly minted
        instance-signed token, so a pass proves the whole chain (proxy →
        gateway → pod → webui → token validation) end to end. Caching mirrors
        the local form: one probe per TTL window, dead only after consecutive
        failures.
        """
        if self.launcher is None:
            return False
        now = time.time()
        if (
            now - self._last_health_check < self._health_check_ttl
            and self._consecutive_health_failures == 0
        ):
            return True
        from app.services.webui_sandbox import mint_instance_token

        token = mint_instance_token(self.user_id, self.port, self.token_secret)
        healthy = bool(self.launcher.health_check(proxy_port=self.port, token=token))
        if healthy:
            self._consecutive_health_failures = 0
            self._last_health_check = now
            return True
        self._consecutive_health_failures += 1
        self._last_health_check = now
        if self._consecutive_health_failures >= self._max_consecutive_failures:
            logger.warning(
                f"Sandboxed WebUI instance (sandbox={self.sandbox_id}, port={self.port}) "
                f"unresponsive for {self._consecutive_health_failures} consecutive checks, "
                "declaring dead"
            )
            return False
        return True

    def _check_http_health(self) -> bool:
        """Make an HTTP request to verify the webui is actually responding."""
        try:
            health_url = f"http://localhost:{self.port}/api/version"
            if self.token:
                health_url += f"?token={self.token}"
            # Bypass system proxy for localhost — use direct socket connection
            # to avoid proxy returning 502 on health checks
            import socket as _socket

            parsed = urllib.parse.urlparse(health_url)
            host = parsed.hostname or "localhost"
            port = parsed.port or 80
            sock = _socket.create_connection((host, port), timeout=5)
            try:
                path = parsed.path
                if parsed.query:
                    path += f"?{parsed.query}"
                request_line = f"GET {path} HTTP/1.0\r\nHost: {host}\r\nConnection: close\r\n\r\n"
                sock.sendall(request_line.encode())
                response = b""
                while True:
                    chunk = sock.recv(4096)
                    if not chunk:
                        break
                    response += chunk
                status_line = response.split(b"\r\n")[0].decode()
                status_code = int(status_line.split(" ")[1])
                return status_code == 200
            finally:
                sock.close()
        except Exception:
            return False

    def update_activity(self):
        """Update last activity timestamp."""
        self.last_activity = datetime.now()


@dataclass
class WorkspaceConfig:
    """Workspace configuration from config.json."""

    enabled: bool = False
    url: str = "http://localhost"
    multi_user_mode: bool = False
    port_range_start: int = 3100
    port_range_end: int = 3200
    max_instances: int = 30
    idle_timeout_minutes: int = 30
    cleanup_interval_minutes: int = 5
    token_secret: str = ""
    webui_path: str = (
        ""  # Path to qwen-code-webui executable or project directory (leave empty for auto-detect)
    )
    # Optional explicit URL for the webui to reach the LLM proxy (e.g. behind an
    # HTTPS reverse proxy). When set, :web_port is NOT appended. See issue #1730.
    webui_callback_url: str = ""
    # Issue #3374 review #12: server-side minimum isolation requirement for
    # user-url launches. Empty string derives the default ("os_user" when
    # multi_user_mode is on, else "none"); the request parameter can only
    # raise, never lower, this floor.
    required_isolation_level: str = ""
    # Issue #3378: which sandbox-backends.json endpoint tier interactive
    # sandboxed WebUIs launch on. Empty string falls back to the backend
    # config's default_tier. Read by the isolation capability probe only.
    sandbox_tier: str = ""


def read_workspace_config() -> WorkspaceConfig:
    """Read WorkspaceConfig from disk WITHOUT constructing a manager.

    Issue #3374 review #13: capability reads must not mint token secrets or
    spawn cleanup greenlets; the snapshot path uses this when no manager
    singleton exists yet (launch-path readiness is only probed when a
    manager is available — see the capability docs).
    """
    from app.repositories.database import CONFIG_DIR

    config_path = os.path.join(CONFIG_DIR, "config.json")
    if not os.path.exists(config_path):
        logger.warning(f"Config file not found: {config_path}")
        return WorkspaceConfig()
    try:
        with open(config_path) as f:
            config = json.load(f)

        workspace = config.get("workspace", {})
        return WorkspaceConfig(
            enabled=workspace.get("enabled", False),
            url=workspace.get("url", "http://localhost"),
            multi_user_mode=workspace.get("multi_user_mode", False),
            port_range_start=workspace.get("port_range_start", 3100),
            port_range_end=workspace.get("port_range_end", 3200),
            max_instances=workspace.get("max_instances", 30),
            idle_timeout_minutes=workspace.get("idle_timeout_minutes", 30),
            cleanup_interval_minutes=workspace.get("cleanup_interval_minutes", 5),
            token_secret=workspace.get("token_secret", ""),
            webui_path=workspace.get("webui_path", ""),
            webui_callback_url=(workspace.get("webui_callback_url", "") or "").strip(),
            required_isolation_level=(workspace.get("required_isolation_level", "") or "").strip(),
            sandbox_tier=(workspace.get("sandbox_tier", "") or "").strip(),
        )
    except Exception as e:
        logger.error(f"Error loading config: {e}")
        return WorkspaceConfig()


class WebUIManager:
    """
    Manages per-user qwen-code-webui processes.

    In multi-user mode, each user gets:
    - A dedicated port from the port pool
    - A webui process running under their system_account
    - A unique token for authentication

    Features:
    - Dynamic port allocation
    - Process lifecycle management
    - Idle instance cleanup
    - Cross-platform support (Linux, macOS, Windows)
    """

    def __init__(self, config: WorkspaceConfig | None = None):
        """
        Initialize the WebUI manager.

        Args:
            config: Workspace configuration. If None, loads from config.json.
        """
        self.config = config or self._load_config()
        self._instances: dict[int, WebUIInstance] = {}  # user_id -> instance
        # port -> (user_id, form). Issue #3378 review (m4): the value carries
        # the form so every holder check can key on (user_id, form) — a
        # cross-form allocate must never return the other form's port.
        self._port_allocations: dict[int, tuple[int, str]] = {}
        self._lock = gevent_lock.RLock()  # gevent-safe reentrant lock
        self._cleanup_greenlet: gevent.Greenlet | None = None
        self._running = False

        # Single-user mode instance (Issue #3129)
        # In single-user mode (Docker compose), this tracks the shared WebUI
        # instance listening on port 3100.
        self._single_user_instance: WebUIInstance | None = None
        self._single_user_lock = gevent_lock.RLock()  # Lock for single-user instance startup

        # Generate token secret if not configured
        if not self.config.token_secret:
            self.config.token_secret = secrets.token_hex(32)

        # Platform detection
        self._platform = platform.system().lower()

        # Issue #3374: memoized successful WebUI executable resolution for the
        # per-user launch probe (supports_per_user_launch), plus a short-TTL
        # readiness memo that also caches degraded states.
        self._resolved_webui: tuple[str, str | None] | None = None
        self._readiness_memo: tuple[float, str | None] | None = None

        # Issue #3378: last port used per (user_id, form) so a same-form
        # restart prefers its original port (SEC-Q4 — a user's bookmarked
        # origin keeps working across idle recycles), plus the sandbox
        # maintenance clock for the cleanup loop.
        self._form_last_ports: dict[tuple[int, str], int] = {}
        self._last_sandbox_maintenance = 0.0
        # Launcher injection point for tests; None builds the real one lazily.
        self._sandbox_launcher: Any = None

        # Windows doesn't support multi-user mode
        if self._platform == "windows" and self.config.multi_user_mode:
            logger.warning(
                "Windows does not support multi-user mode for webui. "
                "Falling back to single-instance mode."
            )
            self.config.multi_user_mode = False

        logger.info(
            f"WebUIManager initialized: multi_user_mode={self.config.multi_user_mode}, "
            f"port_range={self.config.port_range_start}-{self.config.port_range_end}"
        )

    def _load_config(self) -> WorkspaceConfig:
        """Load workspace configuration from config.json."""
        return read_workspace_config()

    def _remove_port_from_url(self, url: str) -> str:
        """Remove any existing port from URL, keeping only scheme and host.

        This ensures consistent URL handling when workspace.url is configured
        with a port (e.g., user mistakenly includes webui port).

        Examples:
            http://localhost:3100 -> http://localhost
            http://192.168.1.169:3100 -> http://192.168.1.169
            http://[::1]:19888 -> http://[::1] (IPv6)
            http://localhost -> http://localhost (no change)

        Args:
            url: URL string that may contain a port.

        Returns:
            URL string without port, preserving scheme and host.
        """
        from urllib.parse import urlparse

        parsed = urlparse(url)
        # Return scheme://hostname (no port)
        if parsed.scheme and parsed.hostname:
            return f"{parsed.scheme}://{parsed.hostname}"
        # Fallback: if URL lacks scheme, return original
        return url

    def _replace_host_from_request(self, config_url: str, request_host_url: str) -> str:
        """Replace hostname in config_url with hostname from request.

        This ensures iframe URL uses the actual IP/hostname that user accessed,
        not the container-detected IP which may be inaccessible from external browsers.

        Used in Docker deployments where container cannot detect host's real IP.

        **Design Principle (Issue #1357):**
        In docker compose deployment, WebUI and open-ace are on the same machine.
        URL should come from request.host_url (user's actual access IP),
        NOT from config.json (which may have wrong IPv6 or container-detected IP).

        Port is handled separately:
        - Single-user mode: fixed port 3100 (WebUI port)
        - Multi-user mode: dynamic port from instance.port

        Examples:
            config_url="http://172.17.0.1:3100", request_host_url="http://192.168.1.169:19888"
            -> "http://192.168.1.169" (hostname replaced, no port)

            config_url="http://host.docker.internal:3100", request_host_url="http://example.com"
            -> "http://example.com"

            config_url="http://[::1]:3100", request_host_url="http://[2001:db8::1]:19888"
            -> "http://[2001:db8::1]" (IPv6 preserved with brackets)

        Args:
            config_url: URL from config.json (may have wrong IP in Docker).
            request_host_url: Host URL from Flask request (user's actual access URL).

        Returns:
            URL with hostname replaced from request, without port.
        """
        from urllib.parse import urlparse

        request_parsed = urlparse(request_host_url)

        # Use request's scheme and hostname
        scheme = request_parsed.scheme or "http"
        hostname = request_parsed.hostname

        if hostname:
            # Check if hostname is IPv6 (contains colons and no dots)
            # IPv6 addresses need to be wrapped in brackets in URLs
            if ":" in hostname and "." not in hostname:
                return f"{scheme}://[{hostname}]"
            return f"{scheme}://{hostname}"
        # Fallback: parse config_url if request_host_url parsing fails
        config_parsed = urlparse(config_url)
        hostname = config_parsed.hostname
        scheme = config_parsed.scheme or "http"
        if hostname:
            if ":" in hostname and "." not in hostname:
                return f"{scheme}://[{hostname}]"
            return f"{scheme}://{hostname}"
        return config_url

    def start_cleanup_thread(self):
        """Start the background cleanup greenlet."""
        if self._cleanup_greenlet is not None:
            return

        self._running = True
        self._cleanup_greenlet = gevent.spawn(self._cleanup_loop)
        logger.info("Cleanup greenlet started")

    def stop_cleanup_thread(self):
        """Stop the background cleanup greenlet."""
        self._running = False
        if self._cleanup_greenlet is not None:
            self._cleanup_greenlet.kill()
            self._cleanup_greenlet = None
        logger.info("Cleanup greenlet stopped")

    def _cleanup_loop(self):
        """Periodically clean up idle instances and maintain sandboxed ones."""
        while self._running:
            try:
                self.cleanup_idle_instances()
            except Exception as e:
                logger.error(f"Error in cleanup loop: {e}")

            # Issue #3378 (D5): every SANDBOX_MAINTENANCE_INTERVAL_SECONDS the
            # live sandboxed instances get a snapshot export (bounds the crash
            # loss window at ~5 minutes) and a renew clamped to their baked-in
            # proxy-token expiry. Best-effort per instance: one failing
            # sandbox must not starve the others. The same tick refreshes the
            # process heartbeat (T-D).
            try:
                self._sandbox_maintenance_tick()
            except Exception as e:
                logger.error(f"Error in sandbox maintenance: {e}")

            # Sleep for cleanup interval
            time.sleep(self.config.cleanup_interval_minutes * 60)

    def _sandbox_maintenance_tick(self) -> None:
        """One maintenance gate pass: heartbeat refresh + instance upkeep.

        T-D: the heartbeat refresh rides the same cadence as the snapshot
        export/renew so a peer reconcile can always see a live web process
        within HEARTBEAT_FRESH_WINDOW_SECONDS. Fail-soft by design: an
        unwritable state root skips it (peers then treat us as stale, the
        conservative direction for THEIR sweep).
        """
        now = time.monotonic()
        if now - self._last_sandbox_maintenance < SANDBOX_MAINTENANCE_INTERVAL_SECONDS:
            return
        self._last_sandbox_maintenance = now
        from app.services.webui_sandbox import write_webui_heartbeat

        write_webui_heartbeat()
        self._maintain_sandboxed_instances()

    def _live_sandboxed_instances(self) -> list[WebUIInstance]:
        """Every live sandboxed instance, multi-user and single-user alike."""
        instances: list[WebUIInstance] = []
        single = self._single_user_instance
        if (
            single is not None
            and getattr(single, "form", "") == WEBUI_FORM_SANDBOXED
            and single.is_alive()
        ):
            instances.append(single)
        with self._lock:
            for instance in self._instances.values():
                if getattr(instance, "form", "") == WEBUI_FORM_SANDBOXED and instance.is_alive():
                    instances.append(instance)
        return instances

    def _maintain_sandboxed_instances(self) -> None:
        """Snapshot-export + renew every live sandboxed instance (fail-soft)."""
        for instance in self._live_sandboxed_instances():
            try:
                blob = instance.launcher.export_snapshot(
                    instance.sandbox_id, restore_confirmed=instance.restore_confirmed
                )
                if blob is not None:
                    instance.launcher.persist_snapshot(instance.user_id, blob)
                instance.launcher.renew_expiration(
                    instance.sandbox_id, proxy_token=instance.proxy_token
                )
            except Exception as e:  # noqa: BLE001 - maintenance is per instance
                logger.warning(
                    "Sandbox maintenance failed for user %s (sandbox %s): %s",
                    instance.user_id,
                    instance.sandbox_id,
                    e,
                )

    def cleanup_idle_instances(self):
        """Clean up instances that have been idle for too long."""
        now = datetime.now()
        timeout = timedelta(minutes=self.config.idle_timeout_minutes)

        with self._lock:
            to_cleanup = []
            for user_id, instance in self._instances.items():
                idle_time = now - instance.last_activity
                if idle_time > timeout:
                    to_cleanup.append(user_id)

            for user_id in to_cleanup:
                logger.info(f"Cleaning up idle instance for user {user_id}")
                self._stop_instance_internal(user_id)

        # Issue #3378 review (m1): the single-user SANDBOXED instance holds a
        # remote pod plus a proxy port, so it must not be exempt from idle
        # reaping — its last_activity is fed by the proxy's on_activity
        # heartbeat and by /user-url hits. The LOCAL single-user instance
        # keeps its historic never-reaped semantics (shared, fixed port 3100,
        # no remote resource; restart-on-dead already covers it). Taken AFTER
        # the _lock block: teardown reaches release_port (which takes _lock),
        # and the codebase's established order is _single_user_lock → _lock.
        with self._single_user_lock:
            single = self._single_user_instance
            if (
                single is not None
                and getattr(single, "form", "") == WEBUI_FORM_SANDBOXED
                and now - single.last_activity > timeout
            ):
                logger.info(
                    "Cleaning up idle single-user sandboxed webui instance (sandbox=%s, port=%s)",
                    single.sandbox_id,
                    single.port,
                )
                self._stop_single_user_instance_internal()

    def allocate_port(self, user_id: int, form: str = WEBUI_FORM_LOCAL) -> int:
        """
        Allocate a port for a user.

        Args:
            user_id: User ID to allocate port for.
            form: "local" or "sandboxed" (Issue #3378). The allocation key is
                (user_id, form) so a cross-form switch cannot silently steal
                the other form's port, and a same-form restart PREFERS its
                previous port (SEC-Q4: bookmarked origins survive recycles).

        Returns:
            Allocated port number.

        Raises:
            ValueError: If no ports are available.
        """
        with self._lock:
            # Still holding a port for this (user_id, form)? Return it — a
            # repeated allocate must never hand out a second port, and a
            # CROSS-form allocate must never steal the other form's port
            # (Issue #3378 review, m4: the key includes the form).
            for port, (uid, held_form) in self._port_allocations.items():
                if uid == user_id and held_form == form:
                    return port

            # Same-form restart: prefer the port this (user, form) last held
            # (SEC-Q4 — bookmarked origins survive idle recycles), if still
            # free.
            previous = self._form_last_ports.get((user_id, form))
            if (
                previous is not None
                and previous not in self._port_allocations
                and self._is_port_available(previous)
            ):
                self._port_allocations[previous] = (user_id, form)
                logger.info(f"Re-allocated port {previous} for user {user_id} ({form} form)")
                return previous

            # Find an available port
            for port in range(self.config.port_range_start, self.config.port_range_end + 1):
                if port not in self._port_allocations:
                    # Verify port is actually available
                    if self._is_port_available(port):
                        self._port_allocations[port] = (user_id, form)
                        logger.info(f"Allocated port {port} for user {user_id} ({form} form)")
                        return port

            raise ValueError(
                f"No available ports in range {self.config.port_range_start}-"
                f"{self.config.port_range_end}"
            )

    def _is_port_available(self, port: int) -> bool:
        """Check if a port is available for binding."""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("localhost", port))
                return True
        except OSError:
            return False

    def _wait_for_service_ready(self, port: int, timeout: float = 10.0) -> bool:
        """
        Wait for the webui service to be ready on the given port.

        Args:
            port: Port to check.
            timeout: Maximum time to wait in seconds.

        Returns:
            True if service is ready, False if timeout reached.
        """
        start_time = time.time()
        check_interval = 0.5  # Check every 500ms

        while time.time() - start_time < timeout:
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.settimeout(1.0)
                    # Try to connect to the port
                    s.connect(("localhost", port))
                    # Service is ready
                    logger.info(
                        f"WebUI service on port {port} is ready after {time.time() - start_time:.1f}s"
                    )
                    return True
            except OSError:
                # Service not ready yet, wait and retry
                time.sleep(check_interval)

        logger.warning(f"WebUI service on port {port} not ready after {timeout}s timeout")
        return False

    def release_port(self, port: int, form: str | None = None):
        """Release a port back to the pool.

        With *form*, the (user, form) → port memo is refreshed so the next
        same-form start prefers this port (SEC-Q4).
        """
        with self._lock:
            if port in self._port_allocations:
                user_id, held_form = self._port_allocations.pop(port)
                if form is not None:
                    self._form_last_ports[(user_id, form)] = port
                logger.info(f"Released port {port} from user {user_id} ({held_form} form)")

    def generate_token(self, user_id: int, port: int) -> str:
        """
        Generate an authentication token for a user.

        Uses v2 format with timestamp for TTL support:
        v2:{user_id}:{port}:{timestamp}:{random}:{signature}

        The v2 format is supported by qwen-code-webui PR #210.
        Token TTL is configurable via OPENACE_WEBUI_TOKEN_TTL_SECONDS (default 24 hours).

        Args:
            user_id: User ID.
            port: Allocated port.

        Returns:
            Generated token string.
        """
        import time

        timestamp = int(time.time())
        random_part = secrets.token_hex(8)
        payload = f"v2:{user_id}:{port}:{timestamp}:{random_part}"
        signature = hashlib.sha256(f"{payload}:{self.config.token_secret}".encode()).hexdigest()[
            :16
        ]
        return f"{payload}:{signature}"

    def refresh_token(self, old_token: str) -> tuple[bool, str | None, str | None]:
        """
        Refresh an expired or expiring token with a new one.

        This method allows refreshing a token even if it has expired,
        as long as the signature is valid. This enables seamless token
        renewal without requiring user re-authentication.

        Supports both v2 format (with TTL) and v1 format (legacy).

        Args:
            old_token: The token to refresh (can be expired).

        Returns:
            Tuple of (success, new_token, error_message).
            On success: (True, new_token, None)
            On failure: (False, None, error_message)
        """
        if not old_token:
            return False, None, "Empty token"

        # v2 format
        if old_token.startswith("v2:"):
            return self._refresh_token_v2(old_token)

        # v1 format (legacy) - refresh to v2
        return self._refresh_token_v1(old_token)

    def _refresh_token_v2(self, old_token: str) -> tuple[bool, str | None, str | None]:
        """Refresh a v2 format token.

        Validates signature (ignoring TTL), then generates a fresh token.
        Issue #3378: a token that verifies against a sandboxed instance's
        per-instance secret is re-minted with that same secret — refreshing
        must not silently downgrade a sandboxed token onto the global one.

        v2 format: v2:{user_id}:{port}:{timestamp}:{random}:{signature}
        """
        valid, _user_id, _port = self._verify_v2_signature(old_token, self.config.token_secret)
        if valid:
            new_token = self.generate_token(_user_id, _port)
            logger.info(f"Refreshed token for user {_user_id}, port {_port}")
            return True, new_token, None
        instance = self._find_sandboxed_instance_by_token(old_token)
        if instance is not None:
            valid, _user_id, _port = self._verify_v2_signature(old_token, instance.token_secret)
            if valid:
                new_token = self._mint_sandboxed_token(instance)
                logger.info("Refreshed sandboxed token for user %s, port %s", _user_id, _port)
                return True, new_token, None
        return False, None, "Invalid signature"

    def _refresh_token_v1(self, old_token: str) -> tuple[bool, str | None, str | None]:
        """Refresh a v1 format token to v2 format.

        v1 format: {user_id}:{port}:{random}:{signature}
        """
        try:
            parts = old_token.split(":")
            if len(parts) != 4:
                return False, None, "Invalid v1 token format"

            user_id_str, port_str, random_part, signature = parts
            user_id: int = int(user_id_str)
            port: int = int(port_str)

            # Verify signature
            expected_signature = hashlib.sha256(
                f"{user_id}:{port}:{random_part}:{self.config.token_secret}".encode()
            ).hexdigest()[:16]

            if not hmac.compare_digest(signature, expected_signature):
                return False, None, "Invalid signature"

            # Generate new v2 token
            new_token = self.generate_token(user_id, port)
            logger.info(f"Refreshed v1 token to v2 for user {user_id}, port {port}")
            return True, new_token, None

        except (ValueError, TypeError) as e:
            return False, None, f"Token parse error: {e}"

    def validate_token(self, token: str) -> tuple[bool, int | None, str | None]:
        """
        Validate an authentication token.

        Supports both v2 format (with TTL) and v1 format (legacy, no TTL).
        Issue #1896: v2 tokens have configurable TTL (default 24 hours).

        v2 format: v2:{user_id}:{port}:{timestamp}:{random}:{signature}
        v1 format: {user_id}:{port}:{random}:{signature}

        Args:
            token: Token string to validate.

        Returns:
            Tuple of (is_valid, user_id, error_message).
        """
        if not token:
            return False, None, "Empty token"

        # v2 format with TTL
        if token.startswith("v2:"):
            return self._validate_token_v2(token)

        # v1 format (legacy, no TTL)
        return self._validate_token_v1(token)

    def _find_sandboxed_instance(self, user_id: int, port: int) -> WebUIInstance | None:
        """Locate the live sandboxed instance a v2 token's (user, port) names.

        Issue #3378 (D2/B1): sandboxed tokens are signed with the instance's
        per-instance secret, so validation needs the instance. Searched
        lock-free on purpose: the registries hold dataclass instances and the
        lookup is advisory (a concurrent stop just makes the answer None).

        Review round 1 (T-C): the SHARED single-user sandboxed instance
        carries tokens minted for every requester, so it matches on the port
        alone — the token's user_id is proven by the per-instance-secret
        signature the caller verifies next, never by this lookup.
        """
        single = self._single_user_instance
        if (
            single is not None
            and getattr(single, "form", "") == WEBUI_FORM_SANDBOXED
            and single.port == port
        ):
            return single
        with self._lock:
            instance = self._instances.get(user_id)
        if (
            instance is not None
            and getattr(instance, "form", "") == WEBUI_FORM_SANDBOXED
            and instance.port == port
        ):
            return instance
        return None

    @staticmethod
    def _verify_v2_signature(token: str, token_secret: str) -> tuple[bool, int, int]:
        """Verify a v2 token's signature against *token_secret*.

        Returns ``(valid, user_id, port)``; TTL is NOT checked here so both
        validate (TTL enforced) and refresh (TTL ignored) can reuse it.
        """
        try:
            parts = token.split(":")
            if len(parts) != 6:
                return False, 0, 0
            _, user_id_str, port_str, timestamp_str, random_part, signature = parts
            user_id = int(user_id_str)
            port = int(port_str)
            payload = f"v2:{user_id}:{port}:{timestamp_str}:{random_part}"
            expected = hashlib.sha256(f"{payload}:{token_secret}".encode()).hexdigest()[:16]
            if not hmac.compare_digest(signature, expected):
                return False, 0, 0
            return True, user_id, port
        except (ValueError, TypeError):
            return False, 0, 0

    def _validate_token_v2(self, token: str) -> tuple[bool, int | None, str | None]:
        """Validate v2 format token with TTL.

        v2 format: v2:{user_id}:{port}:{timestamp}:{random}:{signature}

        Issue #3378: the signature is first checked against the manager's
        global secret (local instances, unchanged); on mismatch the token is
        re-verified against the named sandboxed instance's per-instance secret
        — a stopped sandbox leaves no matching instance, so its tokens fail
        (N8: 401 semantics after teardown).
        """
        from app.auth.decorators import WEBUI_TOKEN_TTL_SECONDS

        valid, user_id, port = self._verify_v2_signature(token, self.config.token_secret)
        if not valid:
            instance = self._find_sandboxed_instance_by_token(token)
            if instance is None:
                return False, None, "Invalid signature"
            valid, user_id, port = self._verify_v2_signature(token, instance.token_secret)
            if not valid:
                return False, None, "Invalid signature"
        try:
            parts = token.split(":")
            timestamp = int(parts[3])
        except (IndexError, ValueError) as e:
            return False, None, f"Token parse error: {e}"

        # Check TTL
        current_time = int(time.time())
        age_seconds = current_time - timestamp

        if age_seconds > WEBUI_TOKEN_TTL_SECONDS:
            return (
                False,
                None,
                f"Token expired (age: {age_seconds}s, TTL: {WEBUI_TOKEN_TTL_SECONDS}s)",
            )

        if age_seconds < 0:
            return False, None, "Token timestamp is in the future"

        return True, user_id, None

    def _find_sandboxed_instance_by_token(self, token: str) -> WebUIInstance | None:
        """Locate the sandboxed instance whose (user, port) a token carries."""
        try:
            parts = token.split(":")
            if len(parts) != 6:
                return None
            return self._find_sandboxed_instance(int(parts[1]), int(parts[2]))
        except (ValueError, TypeError):
            return None

    def _validate_token_v1(self, token: str) -> tuple[bool, int | None, str | None]:
        """Validate v1 format token (legacy, no TTL).

        v1 format: {user_id}:{port}:{random}:{signature}
        """
        try:
            parts = token.split(":")
            if len(parts) != 4:
                return False, None, "Invalid token format"

            user_id_str, port_str, random_part, signature = parts
            user_id: int = int(user_id_str)
            port: int = int(port_str)

            expected_signature = hashlib.sha256(
                f"{user_id}:{port}:{random_part}:{self.config.token_secret}".encode()
            ).hexdigest()[:16]

            if not hmac.compare_digest(signature, expected_signature):
                return False, None, "Invalid signature"

            # Note: We no longer check _port_allocations because each request
            # creates a new WebUIManager instance with empty allocations.
            # Signature validation is sufficient for security.

            return True, user_id, None

        except (ValueError, TypeError) as e:
            return False, None, f"Token parse error: {e}"

    def get_user_webui_url(
        self,
        user_id: int,
        system_account: str,
        host_url: str | None = None,
        required_isolation: str = "",
    ) -> tuple[str, str]:
        """
        Get or create the webui URL for a user.

        In multi-user mode, starts a new instance if needed.
        In single-user mode, returns the configured URL.

        Args:
            user_id: User ID.
            system_account: User's system account name.
            host_url: Optional host URL from Flask request (e.g., "http://192.168.1.169:19888").
                      Used to replace container-detected IP with user's actual access IP.
                      Required for Docker deployments where container cannot detect host's real IP.
            required_isolation: The effective isolation level the /user-url gate
                      resolved (Issue #3378). "sandboxed" launches the sandboxed
                      form; empty derives it from the capability snapshot's
                      floor (a passing sandbox probe flips the default).

        Returns:
            Tuple of (url, token).
        """
        # Determine base URL: use request host if provided, otherwise use config
        # Design Principle (Issue #1357):
        # - Single-user mode (docker compose): WebUI and open-ace on same machine
        #   URL from request.host_url with fixed port 3100, NOT from config.json
        # - Multi-user mode (install.sh): WebUI and open-ace may be on different machines
        #   URL from config.json (user-configured) or request.host_url with instance.port
        if host_url:
            base_url = self._replace_host_from_request(self.config.url, host_url)
        else:
            base_url = self.config.url

        form = self._resolve_form(required_isolation)

        if not self.config.multi_user_mode:
            if form == WEBUI_FORM_SANDBOXED:
                # Issue #3378 (D1): single-user + sandboxed swaps the hardcoded
                # 3100 for a proxy-port allocation — the pod's webui is remote,
                # the browser reaches it through the local D1 proxy.
                return self._get_sandboxed_url_single_user(user_id, base_url)
            # Single-user mode (docker compose): use fixed WebUI port 3100
            # Issue #3129: Start the single-user WebUI instance if not running
            with self._single_user_lock:
                # Check if instance exists and is alive
                if self._single_user_instance is not None and self._single_user_instance.is_alive():
                    if (
                        getattr(self._single_user_instance, "form", WEBUI_FORM_LOCAL)
                        != WEBUI_FORM_LOCAL
                    ):
                        # Issue #3378 review (MINOR-2): a live SANDBOXED instance
                        # must not keep serving a LOCAL-form request — the old
                        # path returned the hardcoded 3100 plus a global-secret
                        # token the remote pod cannot validate. Mirror the
                        # multi-user form-mismatch stop-and-restart (and the
                        # sandboxed branch's cross-form handling above): stop
                        # the old form, then start under the requested one.
                        logger.warning(
                            "Restarting single-user webui: running form "
                            f"'{self._single_user_instance.form}' != requested 'local'"
                        )
                        self._stop_single_user_instance_internal()
                        self._start_single_user_instance(user_id, system_account, base_url)
                    else:
                        self._single_user_instance.update_activity()
                        logger.debug(
                            f"Single-user WebUI instance already running: "
                            f"pid={self._single_user_instance.pid}, port={self._single_user_instance.port}"
                        )
                else:
                    # Instance not running or dead, start a new one
                    if self._single_user_instance is not None:
                        logger.warning(
                            "Single-user WebUI instance was dead, restarting: "
                            f"pid={self._single_user_instance.pid}, "
                            f"port={self._single_user_instance.port}"
                        )
                        self._stop_single_user_instance_internal()
                    self._start_single_user_instance(user_id, system_account, base_url)

            # Generate token for the request
            token = self.generate_token(user_id, 3100)
            # Always add port 3100 in single-user mode
            # Remove any existing port from base_url first, then add 3100
            base_url_no_port = self._remove_port_from_url(base_url)
            url = f"{base_url_no_port}:3100"
            return url, token

        # KNOWN LIMITATION (Issue #3378 review, m6 — documented, deliberately
        # not restructured): the instance-START path below (subprocess launch
        # with up to ~15s readiness wait, or a sandboxed pod create + snapshot
        # restore that can take tens of seconds) runs while this manager lock
        # is held, so concurrent FIRST hits by DIFFERENT users serialize
        # behind the first start. This is the pre-existing pattern (#3129/#3374
        # era) and deliberately kept: handing starts off to per-user locks
        # would add a concurrency regression surface (port allocation, form
        # switching, and the instance cap all rely on this mutual exclusion).
        # The hit path (instance exists and is alive) never blocks on a start.
        with self._lock:
            # Check if user already has an instance
            if user_id in self._instances:
                instance = self._instances[user_id]
                if getattr(instance, "form", WEBUI_FORM_LOCAL) != form:
                    # Issue #3378: extend the stale-account stop-and-restart
                    # precedent (Issue #3374 review #2) to a form mismatch — a
                    # local instance must not keep serving a sandboxed request.
                    logger.warning(
                        f"Restarting webui for user {user_id}: instance form "
                        f"'{instance.form}' != requested '{form}'"
                    )
                    self._stop_instance_internal(user_id)
                elif instance.is_alive():
                    if instance.system_account != system_account:
                        # Issue #3374 review #2: the cached instance was started
                        # under a different mapping (e.g. the admin changed
                        # system_account since); restart under the current
                        # mapping instead of silently serving the stale identity.
                        logger.warning(
                            f"Restarting webui for user {user_id}: instance account "
                            f"'{instance.system_account}' != requested '{system_account}'"
                        )
                        self._stop_instance_internal(user_id)
                    else:
                        instance.update_activity()
                        if form == WEBUI_FORM_SANDBOXED:
                            # D5: token minted per access with the instance
                            # secret (aligned with the single-user behavior of
                            # minting a fresh token on every request).
                            token = self._mint_sandboxed_token(instance)
                            url = f"{base_url}:{instance.port}" if host_url else instance.url
                            return url, token
                        # Use dynamic base_url if provided, otherwise use stored instance.url
                        if host_url:
                            url = f"{base_url}:{instance.port}"
                            return url, instance.token
                        return instance.url, instance.token
                else:
                    # Process declared dead after consecutive health check failures
                    logger.warning(
                        f"Restarting webui for user {user_id}: "
                        f"pid={instance.pid}, port={instance.port}, "
                        f"consecutive_failures={instance._consecutive_health_failures}"
                    )
                    self._stop_instance_internal(user_id)

            # Check instance limit
            active_count = sum(1 for i in self._instances.values() if i.is_alive())
            if active_count >= self.config.max_instances:
                raise ValueError(f"Maximum instances ({self.config.max_instances}) reached")

            # Start new instance
            if form == WEBUI_FORM_SANDBOXED:
                instance = self._start_sandboxed_instance(user_id, system_account, base_url)
            else:
                instance = self._start_instance_internal(user_id, system_account, base_url)
            return instance.url, instance.token

    def _resolve_form(self, required_isolation: str) -> str:
        """Pick the launch form for this request (Issue #3378, review round 1).

        The caller passes the EFFECTIVE requirement — max(server pin floor,
        request parameter), resolved by the /user-url gate. The pin is a
        FLOOR, not a target (#3375 semantics): the launch form is the
        strongest VERIFIED form that satisfies the requirement, derived from
        the same capability snapshot the contract reports (one source of
        truth — the previous code mapped every non-sandboxed explicit value
        to the local form, so a deployment pinned `os_user` by the entrypoint
        but configured with a webui_image advertised `sandboxed` in its
        contract while launching local OS processes).

        Consequences: with no sandboxed request parameter, a sandboxed-
        capable deployment always launches the pod form (even when the pin
        is merely `os_user`); a deployment without webui_image keeps the
        local form for every os_user-level requirement; an explicit
        `sandboxed` requirement keeps its fail-closed shape (the gate
        already refused unmet requests, and the launcher re-checks the
        backend so a bypassed gate cannot silently downgrade to local).
        """
        explicit = (required_isolation or "").strip()
        if explicit == WEBUI_FORM_SANDBOXED:
            return WEBUI_FORM_SANDBOXED
        from app.services.workspace_isolation_contract import (
            ISOLATION_LEVEL_SANDBOXED,
            build_workspace_isolation_snapshot,
        )

        snapshot = build_workspace_isolation_snapshot(self)
        return (
            WEBUI_FORM_SANDBOXED
            if snapshot.isolation_level == ISOLATION_LEVEL_SANDBOXED
            else WEBUI_FORM_LOCAL
        )

    def _start_single_user_instance(self, user_id: int, system_account: str, base_url: str) -> None:
        """
        Start the single-user WebUI instance on port 3100.

        This method is called when the first user requests the WebUI URL
        in single-user mode (Docker compose). It starts a single shared
        instance that all users connect to.

        Must be called with self._single_user_lock held.

        Args:
            user_id: User ID for logging and token generation.
            system_account: System account name (current user in single-user mode).
            base_url: Base URL from request (e.g., http://192.168.1.87).

        Raises:
            ValueError: If the WebUI process fails to start.
        """
        port = 3100  # Fixed port for single-user mode
        logger.info(f"Starting single-user WebUI instance on port {port}")

        # Generate token
        token = self.generate_token(user_id, port)

        # Build URL
        url = f"{base_url}:{port}"

        # Start process
        pid = None
        process = None

        try:
            process, model_pool = self._launch_webui_process(
                user_id, system_account, port, base_url
            )
            pid = process.pid if process else None

            if process is None:
                raise ValueError("Failed to launch single-user WebUI process")

            # Wait for service to be ready
            if not self._wait_for_service_ready(port, timeout=15.0):
                # Service not ready, clean up
                try:
                    process.terminate()
                    process.wait(timeout=2)
                except Exception:
                    pass
                raise ValueError(
                    "Single-user WebUI service failed to start within timeout. "
                    "Check if qwen-code-webui is installed correctly."
                )

        except Exception as e:
            logger.error(f"Failed to start single-user WebUI process: {e}")
            raise

        instance = WebUIInstance(
            user_id=user_id,
            system_account=system_account,
            port=port,
            pid=pid,
            token=token,
            process=process,
            url=url,
            session_model_pool=model_pool,
        )

        self._single_user_instance = instance
        logger.info(
            f"Started single-user WebUI instance: "
            f"port={port}, pid={pid}, system_account={system_account}"
        )

    def _stop_single_user_instance_internal(self) -> None:
        """
        Stop the single-user WebUI instance (internal, must be called with lock).

        This method is called when the instance is dead and needs to be restarted,
        or during shutdown.
        """
        if self._single_user_instance is None:
            return

        instance = self._single_user_instance
        # Issue #3378 review (Q1): revoke the instance's proxy tokens before
        # teardown — same precedent as _stop_instance_internal for the
        # multi-user form. Without this, a stopped instance leaves a
        # still-validatable webui:<user> session behind.
        try:
            from app.modules.workspace.api_key_proxy import get_api_key_proxy_service

            get_api_key_proxy_service().revoke_proxy_tokens_for_session(
                f"webui:{instance.user_id}",
                reason="webui_stopped",
            )
        except Exception as e:
            logger.warning(
                "Failed to revoke WebUI proxy tokens for user %s: %s", instance.user_id, e
            )
        if getattr(instance, "form", WEBUI_FORM_LOCAL) == WEBUI_FORM_SANDBOXED:
            logger.info(
                "Stopping single-user sandboxed WebUI instance: sandbox=%s, port=%s",
                instance.sandbox_id,
                instance.port,
            )
            self._teardown_sandboxed_instance(instance)
            self._single_user_instance = None
            return

        logger.info(
            f"Stopping single-user WebUI instance: pid={instance.pid}, port={instance.port}"
        )

        if instance.process is not None:
            try:
                # Terminate the process
                instance.process.terminate()
                # Wait for process to exit
                try:
                    instance.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    # Force kill if it doesn't exit gracefully
                    instance.process.kill()
                    instance.process.wait(timeout=2)
            except Exception as e:
                logger.warning(f"Error stopping single-user WebUI process: {e}")

        self._single_user_instance = None
        logger.info("Single-user WebUI instance stopped")

    def _start_instance_internal(
        self, user_id: int, system_account: str, base_url: str | None = None
    ) -> WebUIInstance:
        """
        Start a webui instance for a user (internal, must be called with lock).

        Args:
            user_id: User ID.
            system_account: User's system account name.
            base_url: Optional base URL (already processed with host_url if provided).
                      If None, uses config.url without port.

        Returns:
            WebUIInstance object.

        Raises:
            ValueError: If service fails to start within timeout.
        """
        # Allocate port
        port = self.allocate_port(user_id, WEBUI_FORM_LOCAL)

        # Generate token
        token = self.generate_token(user_id, port)

        # Build URL using provided base_url or fallback to config.url
        if base_url is None:
            base_url = self._remove_port_from_url(self.config.url)
        url = f"{base_url}:{port}"

        # Start process
        pid = None
        process = None

        try:
            process, model_pool = self._launch_webui_process(
                user_id, system_account, port, base_url
            )
            pid = process.pid if process else None

            if process is None:
                # Failed to launch process
                self.release_port(port, WEBUI_FORM_LOCAL)
                raise ValueError("Failed to launch webui process")

            # Wait for service to be ready
            if not self._wait_for_service_ready(port, timeout=10.0):
                # Service not ready, clean up
                try:
                    process.terminate()
                    process.wait(timeout=2)
                except Exception:
                    pass
                self.release_port(port, WEBUI_FORM_LOCAL)
                raise ValueError("WebUI service failed to start within timeout")

        except Exception as e:
            logger.error(f"Failed to start webui process: {e}")
            self.release_port(port, WEBUI_FORM_LOCAL)
            raise

        instance = WebUIInstance(
            user_id=user_id,
            system_account=system_account,
            port=port,
            pid=pid,
            token=token,
            process=process,
            url=url,
            session_model_pool=model_pool,
        )

        self._instances[user_id] = instance
        logger.info(
            f"Started webui instance for user {user_id}: "
            f"port={port}, pid={pid}, system_account={system_account}"
        )

        return instance

    # ── Issue #3378: the sandboxed form ───────────────────────────────

    def _get_sandbox_launcher(self) -> Any:
        """Return the sandboxed WebUI launcher (injectable for tests)."""
        if self._sandbox_launcher is None:
            from app.services.webui_sandbox import SandboxedWebuiLauncher

            self._sandbox_launcher = SandboxedWebuiLauncher(
                tier=(getattr(self.config, "sandbox_tier", "") or "").strip()
            )
        return self._sandbox_launcher

    def _mint_sandboxed_token(
        self, instance: WebUIInstance, *, requester_id: int | None = None
    ) -> str:
        """Mint a v2 token signed with the instance's per-instance secret.

        ``requester_id`` (review round 1, T-C): on the shared single-user
        sandboxed instance the token must be minted for the REQUESTING user.
        The reuse branch used the pod creator's user_id, so every subsequent
        user's token validated as the creator — including against the admin
        paths URL_TOKEN_ALLOWED_PATHS admits — a privilege escalation.
        Default (None) keeps the multi-user behavior of minting for the
        instance owner, aligned with the local branch's
        generate_token(user_id, ...) precedent.
        """
        from app.services.webui_sandbox import mint_instance_token

        subject = instance.user_id if requester_id is None else int(requester_id)
        token = mint_instance_token(subject, instance.port, instance.token_secret)
        instance.token = token
        return token

    def _launch_sandboxed(self, user_id: int, system_account: str, base_url: str) -> WebUIInstance:
        """Create the sandboxed instance (pod + restore + local port proxy).

        Shared by the multi-user branch and the single-user sandboxed branch:
        both end with a :class:`WebUIInstance` whose ``port`` is the LOCAL
        proxy port (the browser's origin), never the in-pod 3100.
        """
        from app.services.webui_sandbox import SandboxWebuiProxy

        launcher = self._get_sandbox_launcher()
        callback_url = (getattr(self.config, "webui_callback_url", "") or "").strip()
        if not callback_url:
            # The /user-url gate's sandbox probe already refuses this; the
            # re-check keeps a config race from creating an unreachable pod.
            raise ValueError(
                "workspace.webui_callback_url is not set; sandboxed webui "
                "pods cannot reach the control-plane LLM proxy"
            )

        snapshot = launcher.load_snapshot(user_id)
        result = launcher.launch(user_id=user_id, callback_url=callback_url, snapshot=snapshot)

        port = self.allocate_port(user_id, WEBUI_FORM_SANDBOXED)
        # The instance object does not exist until after the proxy starts, so
        # the activity callback closes over a holder cell filled below. Every
        # successful proxy forward then refreshes instance.last_activity —
        # the only heartbeat the single-user sandboxed form gets (m1), and the
        # signal the idle reaper below keys on.
        instance_holder: list[WebUIInstance] = []
        try:
            proxy = SandboxWebuiProxy(
                sandbox_id=result.sandbox_id,
                upstream_resolver=lambda: launcher.resolve_webui_endpoint(result.sandbox_id),
                on_activity=lambda: (
                    instance_holder[0].update_activity() if instance_holder else None
                ),
            )
            proxy.start(port)
        except Exception:
            logger.exception(
                "sandboxed webui proxy failed to start for user %s; destroying pod",
                user_id,
            )
            launcher.destroy(
                result.sandbox_id,
                user_id,
                restore_confirmed=result.restore_confirmed,
                final_export=True,
            )
            self.release_port(port, WEBUI_FORM_SANDBOXED)
            raise

        instance = WebUIInstance(
            user_id=user_id,
            system_account=system_account,
            port=port,
            form=WEBUI_FORM_SANDBOXED,
            sandbox_id=result.sandbox_id,
            sandbox_tier=result.tier,
            restore_confirmed=result.restore_confirmed,
            token_secret=result.token_secret,
            proxy_token=result.proxy_token,
            launcher=launcher,
            proxy=proxy,
            url=f"{self._remove_port_from_url(base_url)}:{port}",
        )
        instance_holder.append(instance)
        self._mint_sandboxed_token(instance)
        return instance

    def _start_sandboxed_instance(
        self, user_id: int, system_account: str, base_url: str
    ) -> WebUIInstance:
        """Multi-user sandboxed start (must be called with self._lock held).

        No OS account is created and no sudo wrapper is used (D4): identity
        for a sandboxed webui is the per-instance token, not a host uid.
        ``_wait_for_service_ready`` is deliberately NOT used — the webui boots
        behind a remote restore gate the proxy's health path already probes.
        """
        instance = self._launch_sandboxed(user_id, system_account, base_url)
        self._instances[user_id] = instance
        logger.info(
            "Started sandboxed webui instance for user %s: sandbox=%s tier=%s port=%s "
            "(restore_confirmed=%s)",
            user_id,
            instance.sandbox_id,
            instance.sandbox_tier,
            instance.port,
            instance.restore_confirmed,
        )
        return instance

    def _get_sandboxed_url_single_user(self, user_id: int, base_url: str) -> tuple[str, str]:
        """Single-user + sandboxed: proxy-port URL instead of hardcoded 3100.

        Mirrors the local single-user branch's lifecycle (shared instance,
        restart-on-dead) but every user token is minted with the instance's
        per-instance secret.
        """
        with self._single_user_lock:
            instance = self._single_user_instance
            if instance is not None and getattr(instance, "form", "") == WEBUI_FORM_SANDBOXED:
                if instance.is_alive():
                    instance.update_activity()
                    # T-C: mint for the REQUESTER, never the pod creator —
                    # the shared instance serves every user, and each token
                    # must validate as the user who asked for it.
                    token = self._mint_sandboxed_token(instance, requester_id=user_id)
                    return f"{self._remove_port_from_url(base_url)}:{instance.port}", token
                logger.warning(
                    "Single-user sandboxed webui (sandbox=%s) is dead; restarting",
                    instance.sandbox_id,
                )
                self._stop_single_user_instance_internal()
            elif instance is not None:
                # Cross-form: the local single-user instance gives way.
                self._stop_single_user_instance_internal()
            instance = self._launch_sandboxed(
                user_id, f"single-user-{user_id}", self._remove_port_from_url(base_url)
            )
            self._single_user_instance = instance
        return instance.url, instance.token

    def _load_server_config(self) -> dict:
        """Load server configuration from config.json."""
        from app.repositories.database import CONFIG_DIR

        config_path = os.path.join(CONFIG_DIR, "config.json")
        try:
            with open(config_path) as f:
                config = json.load(f)
            return cast("dict", config.get("server", {}))
        except Exception:
            return {}

    def _build_local_session_model_pool(self, user_id: int) -> dict[str, Any]:
        """Build the local qwen-code HA model pool snapshot for a webui instance."""
        from app.modules.workspace.api_key_proxy import get_api_key_proxy_service

        api_proxy = get_api_key_proxy_service()
        tenant_id = 1
        pool = api_proxy.get_tool_model_pool(
            tenant_id=tenant_id,
            tool_name="qwen-code",
            scope="local",
            provider="openai",
        )
        proxy_token = api_proxy.generate_proxy_token(
            user_id=user_id,
            session_id=f"webui:{user_id}",
            tenant_id=tenant_id,
            provider="openai",
            session_type="webui",
            extra_payload={
                "scope": "local",
                "tool_name": "qwen-code",
            },
        )
        return {
            **pool,
            "proxy_token": proxy_token,
        }

    def _configure_local_openai_proxy(
        self,
        user_id: int,
        env: dict[str, str],
        openace_api_url: str,
    ) -> dict[str, Any]:
        """
        Route local multi-user qwen-code-webui traffic through the Open ACE proxy.

        Returns the HA model pool snapshot that backs both request-time failover
        and the integrated-model list shown in the iframe.
        """
        try:
            pool = self._build_local_session_model_pool(user_id)
            proxy_token = str(pool.get("proxy_token", ""))
            env["OPENAI_API_KEY"] = proxy_token
            env["OPENAI_BASE_URL"] = f"{openace_api_url.rstrip('/')}/api/workspace/llm-proxy/v1"
            env["OPENACE_PROXY_TOKEN"] = proxy_token
            env["OPENACE_PROXY_URL"] = f"{openace_api_url.rstrip('/')}/api/workspace/llm-proxy"
            # qwen-code-webui reads the envKey from integrated model config;
            # set all declared envKeys to the proxy token so the webui can find them
            for model in pool.get("models", []):
                env_key = model.get("envKey")
                if env_key and env_key not in env:
                    env[env_key] = proxy_token
            return pool
        except Exception as e:
            logger.warning("Failed to configure local OpenAI proxy from database: %s", e)
            return {
                "provider": "openai",
                "tool_name": "qwen-code",
                "scope": "local",
                "models": [],
                "candidate_keys": [],
                "model_key_ids": {},
                "settings": {},
                "empty_reason": "Failed to resolve local API key pool",
                "proxy_token": "",
            }

    def _build_webui_env(
        self,
        user_id: int,
        system_account: str,
        openace_api_url: str,
    ) -> tuple[dict[str, str], dict[str, Any]]:
        """
        Build minimal environment for WebUI process.

        Issue #2298: Use explicit environment instead of os.environ.copy()
        to prevent leaking sensitive variables (DATABASE_URL, TOKEN_SECRET,
        GH_TOKEN, ANTHROPIC_API_KEY, etc.) to WebUI child process.

        Returns:
            Tuple of (environment dict, model pool dict).
        """
        # Start with minimal base environment
        # Issue #1141: Prepend system dirs, but preserve inherited PATH for macOS etc.
        # macOS Apple Silicon has node in /opt/homebrew/bin, which is not in the hardcoded PATH.
        # Preserving inherited PATH ensures custom node installations are found.
        _system_dirs = "/usr/local/bin:/usr/bin:/bin"
        _inherited_path = os.environ.get("PATH", "")
        child_env = {
            "PATH": _system_dirs + (":" + _inherited_path if _inherited_path else ""),
            # Language/encoding
            "LANG": os.environ.get("LANG", "C.UTF-8"),
            "LC_ALL": os.environ.get("LC_ALL", ""),
        }

        # Configure LLM proxy (includes dynamic envKey collection)
        model_pool = self._configure_local_openai_proxy(user_id, child_env, openace_api_url)

        # Optional: HTTP proxy settings (if configured in service environment)
        for proxy_var in ["HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY"]:
            if proxy_var in os.environ:
                child_env[proxy_var] = os.environ[proxy_var]

        return child_env, model_pool

    def _launch_webui_process(
        self, user_id: int, system_account: str, port: int, base_url: str
    ) -> tuple[subprocess.Popen | None, dict[str, Any]]:
        """
        Launch a webui process as the specified user.

        Args:
            user_id: User ID for log directory naming.
            system_account: System account to run the process as.
            port: Port for the webui to listen on.
            base_url: Base URL from request (e.g., http://192.168.1.87), used for
                      WebUI process to connect to main service's LLM proxy API.

        Returns:
            subprocess.Popen object or None if launch failed.
        """
        # Ensure system user exists (for Docker multi-user mode)
        if self._platform in ("linux", "darwin"):
            try:
                pwd.getpwnam(system_account)
            except KeyError:
                logger.info(f"User '{system_account}' not found, creating...")
                if not self._ensure_system_user(system_account):
                    raise ValueError(f"Failed to create system user: {system_account}")

        # Find webui executable or project path. Shares the successful-
        # resolution memo with the readiness probe (Issue #3374 review #9
        # round 2): both call sites see one resolution instead of diverging.
        resolved = getattr(self, "_resolved_webui", None)
        if resolved:
            webui_cmd, webui_dir = resolved
        else:
            webui_cmd, webui_dir = self._find_webui_executable()
            if webui_cmd:
                self._resolved_webui = (webui_cmd, webui_dir)

        if not webui_cmd:
            logger.error("qwen-code-webui executable not found")
            return None, {}

        # Build openace_api_url from base_url (from request host_url)
        # WebUI process needs to connect to main service's LLM proxy API
        # Use base_url (user's actual access IP) instead of config.url (container-detected IP)
        #
        # Issue #1730: When open-ace sits behind an HTTPS reverse proxy, the backend
        # listens on plain HTTP (web_port) while users access it via https://<domain>.
        # The default logic below would inject https://<domain>:<web_port> into the
        # webui, but <web_port> is not TLS-terminated (TLS ends at the proxy on 443),
        # causing Node fetch to fail with "fetch failed". When ``webui_callback_url``
        # is configured, use it verbatim (already includes the correct scheme and,
        # if needed, the proxy port) and do NOT append :web_port.
        webui_callback_url = (getattr(self.config, "webui_callback_url", "") or "").strip()
        if webui_callback_url:
            openace_api_url = webui_callback_url.rstrip("/")
        else:
            openace_api_url = self._remove_port_from_url(base_url)
            server_config = self._load_server_config()
            server_port = server_config.get("web_port", 19888)
            openace_api_url = f"{openace_api_url}:{server_port}"

        # Issue #2298: Build minimal environment for WebUI process
        # Do NOT use os.environ.copy() to avoid leaking sensitive variables
        # (DATABASE_URL, TOKEN_SECRET, GH_TOKEN, etc.)
        child_env, model_pool = self._build_webui_env(user_id, system_account, openace_api_url)

        # Set OPENACE_LOG_DIR to /tmp to avoid HOME permission issues
        webui_log_dir = f"/tmp/qwen-code-webui-{user_id}"
        os.makedirs(webui_log_dir, mode=0o755, exist_ok=True)
        child_env["OPENACE_LOG_DIR"] = webui_log_dir

        # Set timeout-related environment variables for webui (Issue #351)
        # SESSION_TIMEOUT_MS: timeout for permission prompts (default: 24 hours)
        # KEEPALIVE_INTERVAL_MS: heartbeat interval (default: 15 seconds)
        # These prevent premature session termination during long-running tasks.
        child_env["SESSION_TIMEOUT_MS"] = "86400000"  # 24 hours
        child_env["KEEPALIVE_INTERVAL_MS"] = "10000"  # 10 seconds (more frequent)

        # Change log directory ownership to system_account (Linux/macOS only)
        # This allows webui to create additional log files if needed
        # Issue #1262: Use sudo when running as non-root user
        if self._platform in ("linux", "darwin"):
            if os.geteuid() != 0:
                # Non-root user: use sudo chown
                result = run_as_root_if_needed(
                    ["chown", f"{system_account}:{system_account}", webui_log_dir]
                )
                if result.returncode != 0:
                    logger.warning(f"Failed to chown log dir: {result.stderr}")
            else:
                # Root user: direct chown
                try:
                    pw_info = pwd.getpwnam(system_account)
                    os.chown(webui_log_dir, pw_info.pw_uid, pw_info.pw_gid)
                except KeyError:
                    logger.warning(f"User '{system_account}' not found, skipping chown")
                except OSError as e:
                    logger.warning(f"Failed to chown log dir: {e}")

        # Build command based on platform.
        # popen_env tracks whether to pass child_env to Popen; for the sudo
        # inline path env vars are already in the command, so skip it.
        popen_env: dict[str, str] | None = child_env
        if webui_dir:
            # Running from project directory using node
            cmd = [
                "node",
                webui_cmd,
                "--port",
                str(port),
                "--host",
                "0.0.0.0",
                "--token-secret",
                self.config.token_secret,
                "--quota-check-enabled",
                "--openace-api-url",
                openace_api_url,
            ]
            cwd = webui_dir
        elif self._platform in ("linux", "darwin"):
            # Linux/macOS: Check if current user is already the target user
            # If so, skip sudo to avoid NoNewPrivileges restriction in systemd
            current_user = pwd.getpwuid(os.getuid()).pw_name
            if current_user == system_account:
                # Already running as target user, execute directly
                cmd = [
                    webui_cmd,
                    "--port",
                    str(port),
                    "--host",
                    "0.0.0.0",
                    "--token-secret",
                    self.config.token_secret,
                    "--quota-check-enabled",
                    "--openace-api-url",
                    openace_api_url,
                ]
                cwd = None
            else:
                # Different user: use sudo -u with openace-webui-launch wrapper
                # to pass environment variables inline.
                # Issue #2298: Popen(env=child_env) is filtered by sudo env_keep,
                # so we inline env vars as arguments to the launch wrapper.
                # The wrapper execs /usr/bin/env with all supplied args; sudoers
                # restricts it to only be called with the webui path as first
                # non-env argument (no arbitrary command execution).
                #
                # SECURITY NOTE: inline KEY=VALUE args are visible in /proc/<pid>/cmdline
                # to other processes on the same host. The values here are JWT proxy tokens
                # (not real API keys), so the risk is acceptable. For real API keys, consider
                # passing them via a file or other IPC mechanism.
                env_args = []
                # Standard keys: LLM config + locale + proxy
                for key in [
                    "OPENAI_API_KEY",
                    "OPENAI_BASE_URL",
                    "LANG",
                    "LC_ALL",
                    "HTTP_PROXY",
                    "HTTPS_PROXY",
                    "NO_PROXY",
                ]:
                    if child_env.get(key):
                        env_args.append(f"{key}={child_env[key]}")

                # Set HOME to the target user's home directory.
                # sudo's always_set_home should handle this, but it doesn't
                # work reliably in all sudo/PAM configurations (the webui
                # process inherits the service user's HOME instead). Without
                # the correct HOME, the webui fails to create its log
                # directory ($HOME/logs) with EACCES, causing startup failure.
                try:
                    target_home = pwd.getpwnam(system_account).pw_dir
                    env_args.append(f"HOME={target_home}")
                except KeyError:
                    logger.warning(
                        f"Could not resolve home dir for '{system_account}', "
                        "HOME will not be set explicitly"
                    )

                # Explicitly pass OPENACE_LOG_DIR. It's in
                # _WEBUI_ENV_SUDO_KNOWN_KEYS (excluded from the dynamic loop
                # below) because it was expected to be preserved by sudoers
                # env_keep. However, with popen_env=None the parent process
                # environment doesn't contain OPENACE_LOG_DIR (it's only in
                # child_env), so env_keep has nothing to preserve. Inline it
                # explicitly to ensure the webui logs to the correct directory.
                if child_env.get("OPENACE_LOG_DIR"):
                    env_args.append(f"OPENACE_LOG_DIR={child_env['OPENACE_LOG_DIR']}")

                # Dynamic envKeys from model pool (e.g., BAILIAN_CODING_PLAN_API_KEY)
                for key, value in child_env.items():
                    if key not in _WEBUI_ENV_SUDO_KNOWN_KEYS and value:
                        env_args.append(f"{key}={value}")

                cmd = (
                    [
                        "sudo",
                        "-u",
                        system_account,
                        _WEBUI_LAUNCH_WRAPPER,
                    ]
                    + env_args
                    + [
                        webui_cmd,
                        "--port",
                        str(port),
                        "--host",
                        "0.0.0.0",
                        "--token-secret",
                        self.config.token_secret,
                        "--quota-check-enabled",
                        "--openace-api-url",
                        openace_api_url,
                    ]
                )
                cwd = None
                # Env vars already inlined via launch wrapper; skip Popen env param.
                popen_env = None
        else:
            # Other platforms: direct execution (no user switching)
            cmd = [
                webui_cmd,
                "--port",
                str(port),
                "--host",
                "0.0.0.0",
                "--token-secret",
                self.config.token_secret,
                "--quota-check-enabled",
                "--openace-api-url",
                openace_api_url,
            ]
            cwd = None

        # All platforms: when proxy is configured, qwen-code CLI needs --auth-type openai
        if child_env.get("OPENAI_API_KEY"):
            cmd.extend(["--auth-type", "openai"])

        logger.debug(f"Launching webui: {cmd}, cwd: {cwd}")

        try:
            # Don't pre-create log file - let WebUI process handle its own logging
            # WebUI has OPENACE_LOG_DIR environment variable set and will create logs itself
            # This avoids permission issues with pre-created files owned by wrong user

            process = subprocess.Popen(
                cmd,
                start_new_session=True,  # Detach from parent process group
                cwd=cwd,
                env=popen_env,  # None for sudo-inline path (vars already in cmd)
                stdout=subprocess.DEVNULL,  # WebUI handles its own logging via OPENACE_LOG_DIR
                stderr=subprocess.DEVNULL,
            )
            return process, model_pool
        except Exception as e:
            logger.error(f"Failed to launch webui process: {e}")
            return None, model_pool

    def _find_webui_executable(self, probe_only: bool = False) -> tuple[str | None, str | None]:
        """
        Locate the qwen-code-webui executable or project entry.

        Args:
            probe_only: Skip side effects (npm build) — used by capability
                probes that must never produce build artifacts or block a
                worker for the 60s build timeout (Issue #3374 review #9).

        Returns:
            Tuple of (webui_cmd, webui_dir). If running from a project
            directory, webui_dir is the backend directory and webui_cmd is
            the node entry; working_directory is None for global installs.
            If running global executable, working_directory is None.
        """
        # Check webui_path from config
        if self.config.webui_path:
            # First, check if webui_path is an executable file (global install mode)
            # This supports users who configured webui_path as the executable path
            # (e.g., /usr/bin/qwen-code-webui) instead of project directory.
            # See Issue #2151 for context.
            if os.path.isfile(self.config.webui_path) and os.access(
                self.config.webui_path, os.X_OK
            ):
                logger.info(f"Using webui executable from config: {self.config.webui_path}")
                return self.config.webui_path, None

            # Then, check if webui_path is a project directory (development mode)
            webui_backend = os.path.join(self.config.webui_path, "backend")
            node_entry = os.path.join(webui_backend, "dist", "cli", "node.js")

            if os.path.isfile(node_entry):
                logger.info(f"Using webui from project directory: {self.config.webui_path}")
                return node_entry, webui_backend

            # Check if project needs to be built
            if os.path.isdir(webui_backend):
                if probe_only:
                    # Capability probes must not build (60s subprocess). Keep
                    # the directory so readiness classifies this checkout as
                    # dev-directory (shared-account) mode rather than
                    # "executable missing".
                    logger.info("WebUI project not built; probe-only resolution skips build")
                    return None, webui_backend
                logger.warning(f"WebUI project found but not built: {node_entry} not found")
                # Try to build it
                try:
                    subprocess.run(
                        ["npm", "run", "build"],
                        cwd=webui_backend,
                        capture_output=True,
                        timeout=60,
                    )
                    if os.path.isfile(node_entry):
                        logger.info("WebUI project built successfully")
                        return node_entry, webui_backend
                except Exception as e:
                    logger.error(f"Failed to build webui project: {e}")

        # Check common locations for global executable
        candidates = [
            "qwen-code-webui",
            "/usr/bin/qwen-code-webui",  # Most common location for npm global installs
            "/usr/local/bin/qwen-code-webui",
            "/opt/qwen-code-webui/bin/qwen-code-webui",
        ]

        # Also check if webui is bundled with open-ace
        from app.repositories.database import CONFIG_DIR

        bundled_path = os.path.join(os.path.dirname(CONFIG_DIR), "webui", "bin", "qwen-code-webui")
        candidates.append(bundled_path)

        for candidate in candidates:
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                return candidate, None

        # Try to find in PATH
        try:
            result = subprocess.run(
                ["which", "qwen-code-webui"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                return result.stdout.strip(), None
        except Exception:
            pass

        return None, None

    def _ensure_system_user(self, system_account: str) -> bool:
        """
        Ensure a system user exists for workspace operations.
        Creates the OS user if it doesn't exist.

        Reuses the ensure_system_user function from app/utils/workspace.py
        to avoid code duplication and ensure consistent behavior.

        Args:
            system_account: Username for the system account.

        Returns:
            True if user exists or was created successfully.
        """
        return _ensure_user_shared(system_account)

    def _stop_instance_internal(self, user_id: int):
        """
        Stop a webui instance (internal, must be called with lock).

        Args:
            user_id: User ID to stop instance for.
        """
        if user_id not in self._instances:
            return

        instance = self._instances.pop(user_id)

        try:
            from app.modules.workspace.api_key_proxy import get_api_key_proxy_service

            get_api_key_proxy_service().revoke_proxy_tokens_for_session(
                f"webui:{user_id}",
                reason="webui_stopped",
            )
        except Exception as e:
            logger.warning("Failed to revoke WebUI proxy tokens for user %s: %s", user_id, e)

        form = getattr(instance, "form", WEBUI_FORM_LOCAL)
        if form == WEBUI_FORM_SANDBOXED:
            self._teardown_sandboxed_instance(instance)
            return

        # Stop the process
        if instance.process is not None:
            try:
                # Try graceful termination first
                instance.process.terminate()

                # Wait a bit, then force kill if needed
                try:
                    instance.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    instance.process.kill()
                    instance.process.wait(timeout=2)

                logger.info(f"Stopped webui process for user {user_id}")
            except Exception as e:
                logger.error(f"Error stopping process: {e}")

        # Release port
        self.release_port(instance.port, WEBUI_FORM_LOCAL)

    def _teardown_sandboxed_instance(self, instance: WebUIInstance) -> None:
        """Tear down a sandboxed instance: final export, destroy, un-proxy.

        The export runs under the D6 guard (launcher-side, keyed on the
        instance's restore-confirmed state) and is best-effort — destroy is
        idempotent (404 = success) and never blocked by an export failure.
        """
        user_id = instance.user_id
        if instance.proxy is not None:
            try:
                instance.proxy.stop()
            except Exception as e:  # noqa: BLE001 - teardown must continue
                logger.warning("Failed to stop sandboxed webui proxy: %s", e)
        if instance.launcher is not None and instance.sandbox_id:
            try:
                instance.launcher.destroy(
                    instance.sandbox_id,
                    user_id,
                    restore_confirmed=instance.restore_confirmed,
                    final_export=True,
                )
            except Exception as e:  # noqa: BLE001 - destroy stays idempotent
                logger.warning(
                    "Failed to destroy sandboxed webui %s for user %s: %s",
                    instance.sandbox_id,
                    user_id,
                    e,
                )
        self.release_port(instance.port, WEBUI_FORM_SANDBOXED)

    def stop_user_webui(self, user_id: int):
        """
        Stop the webui instance for a user.

        Args:
            user_id: User ID to stop instance for.
        """
        with self._lock:
            self._stop_instance_internal(user_id)

    def per_user_launch_readiness(self) -> str | None:
        """Account-independent per-user launch readiness (Issue #3374 reviews).

        Returns a degradation reason code, or None when the launch PATH can
        host per-user WebUIs: platform, WebUI resolution (probe-only: never
        builds), dev-directory mode, the audited launch wrapper, and the
        sudo binary. The result is memoized for _PROBE_MEMO_TTL_SECONDS in
        BOTH directions so repeated GETs cannot loop a probe on a degraded
        host; successful resolution is additionally cached permanently for
        the real launch path.
        """
        now = time.monotonic()
        cached = self._readiness_memo
        if cached is not None and now - cached[0] < _PROBE_MEMO_TTL_SECONDS:
            return cached[1]
        reason = self._compute_launch_readiness()
        self._readiness_memo = (now, reason)
        return reason

    def _compute_launch_readiness(self) -> str | None:
        if self._platform not in ("linux", "darwin"):
            return "platform_unsupported"
        resolved = getattr(self, "_resolved_webui", None)
        if resolved:
            webui_cmd, webui_dir = resolved
        else:
            webui_cmd, webui_dir = self._find_webui_executable(probe_only=True)
            if webui_cmd:
                self._resolved_webui = (webui_cmd, webui_dir)
        if webui_dir:
            # Dev-directory mode runs `node` as the service user with no UID
            # switch — this holds for built and unbuilt checkouts alike.
            return "dev_directory_mode_shared_account"
        if not webui_cmd:
            return "webui_executable_missing"
        # The sudo path execs the audited launch wrapper; the wrapper being
        # installed and executable is the real precondition (the sudoers
        # rule itself cannot be verified cheaply here).
        from app.utils.workspace import _is_wrapper_available

        if not _is_wrapper_available(_WEBUI_LAUNCH_WRAPPER):
            return "launch_wrapper_missing"
        if shutil.which("sudo") is None:
            return "sudo_unavailable"
        return None

    def supports_per_user_launch(self, system_account: str) -> tuple[bool, str | None]:
        """Report whether a WebUI for ``system_account`` would run as that OS user.

        Issue #3374 isolation gate: multi-user mode must not silently run user
        WebUIs under the shared service account (or a privileged/reserved
        account that merely shares the name). Returns ``(True, None)`` when a
        per-user launch is possible, else ``(False, reason_code)``.
        """
        readiness = self.per_user_launch_readiness()
        if readiness:
            return False, readiness
        try:
            target_pw = pwd.getpwnam(system_account)
        except KeyError:
            # OS account absent. Provisioning only happens in the Docker
            # multi-user form (ensure_system_user skips creation elsewhere,
            # #3130) — outside that form an absent account can never be
            # created, so sudo -u would fail at launch: a probe failure,
            # not a tolerated pending state (PR review round 3).
            from app.utils.workspace import _is_docker_multi_user_mode

            if not _is_docker_multi_user_mode():
                return False, "identity_account_missing"
            target_pw = None
        if target_pw is not None:
            if target_pw.pw_uid == 0:
                return False, "privileged_system_account"
            if target_pw.pw_uid < 1000:
                return False, "reserved_system_account"
        try:
            pwd.getpwuid(os.getuid())
        except (KeyError, OSError):
            return False, "current_user_unresolved"
        # current_user == system_account is fine here: the only supported
        # multi-user form runs the service as root, and root is refused above;
        # single-user mode does not consult this probe.
        return True, None

    def stop_all_instances(self):
        """Stop all running webui instances."""
        # Stop single-user instance first (Issue #3129)
        with self._single_user_lock:
            self._stop_single_user_instance_internal()

        # Stop multi-user instances
        with self._lock:
            for user_id in list(self._instances.keys()):
                self._stop_instance_internal(user_id)

        logger.info("All webui instances stopped")

    def get_instance_count(self) -> int:
        """Get the number of active instances."""
        count = 0
        # Count single-user instance (Issue #3129)
        if self._single_user_instance is not None and self._single_user_instance.is_alive():
            count += 1
        # Count multi-user instances
        with self._lock:
            count += sum(1 for i in self._instances.values() if i.is_alive())
        return count

    def get_user_instance(self, user_id: int) -> WebUIInstance | None:
        """Get the instance for a specific user.

        Issue #3378 review (M1): a single-user SANDBOXED instance lives in
        ``_single_user_instance``, not ``_instances`` — the LLM-proxy token
        lifecycle check (``api_key_proxy._webui_instance_alive``) resolves the
        pod's baked-in token through this method, so without the fallback every
        proxy-token validation of the single-user sandboxed form would 401.
        Multi-user semantics are unchanged (``_instances`` stays keyed by
        user_id); the shared instance only answers for the user whose pod
        token it carries. Read lock-free like the other advisory single-user
        reads (a concurrent stop just makes the answer None).
        """
        with self._lock:
            instance = self._instances.get(user_id)
        if instance is not None:
            return instance
        single = self._single_user_instance
        if (
            single is not None
            and single.user_id == user_id
            and getattr(single, "form", "") == WEBUI_FORM_SANDBOXED
        ):
            return single
        return None

    def get_all_instances(self) -> list[dict[str, Any]]:
        """Get information about all instances."""
        instances = []

        # Include single-user instance (Issue #3129)
        if self._single_user_instance is not None:
            i = self._single_user_instance
            instances.append(
                {
                    "user_id": i.user_id,
                    "system_account": i.system_account,
                    "port": i.port,
                    "pid": i.pid,
                    "url": i.url,
                    "allocated_at": i.allocated_at.isoformat(),
                    "last_activity": i.last_activity.isoformat(),
                    "is_alive": i.is_alive(),
                    "mode": "single-user",
                }
            )

        # Include multi-user instances
        with self._lock:
            for i in self._instances.values():
                instances.append(
                    {
                        "user_id": i.user_id,
                        "system_account": i.system_account,
                        "port": i.port,
                        "pid": i.pid,
                        "url": i.url,
                        "allocated_at": i.allocated_at.isoformat(),
                        "last_activity": i.last_activity.isoformat(),
                        "is_alive": i.is_alive(),
                        "mode": "multi-user",
                    }
                )

        return instances

    def update_user_activity(self, user_id: int):
        """Update the activity timestamp for a user's instance."""
        # Update single-user instance activity (Issue #3129)
        if self._single_user_instance is not None:
            self._single_user_instance.update_activity()

        # Update multi-user instance activity
        with self._lock:
            if user_id in self._instances:
                self._instances[user_id].update_activity()

    def prestart_user_instance_async(
        self, user_id: int, system_account: str, host_url: str | None = None
    ):
        """
        Pre-start a webui instance for a user in background thread.

        This is called during login to start the instance early,
        so it's ready when the user navigates to /work.

        Args:
            user_id: User ID.
            system_account: User's system account name. Callers must pass the
                EXPLICIT DB mapping — prestart never falls back to username
                (Issue #3374 review #6: it goes through the same isolation
                gate as /user-url and must not launch what the gate rejects).
            host_url: Optional host URL from Flask request (e.g., "http://192.168.1.87:19888").
                      Used to replace container-detected IP with user's actual access IP.
                      Required for Docker deployments where container cannot detect host's real IP.
        """
        if not self.config.multi_user_mode:
            return  # No pre-start needed in single-user mode

        # Issue #3374 review #6 (+ round-2 normalization): evaluate the
        # server-side isolation floor (same contract as /user-url) before
        # spawning anything. Floor resolution is shared with the route via
        # resolve_required_floor — an invalid hand-edited config value must
        # fall back fail-closed here too, not skip the gate entirely.
        from app.services.workspace_isolation_contract import (
            ISOLATION_LEVEL_SANDBOXED,
            build_workspace_isolation_snapshot,
            evaluate_isolation_requirement,
            resolve_required_floor,
        )

        snapshot = build_workspace_isolation_snapshot(self)
        required = resolve_required_floor(self.config, snapshot)
        if required != "none":
            rejection = evaluate_isolation_requirement(
                required,
                snapshot=snapshot,
                system_account=system_account,
                manager=self,
            )
            if rejection is not None:
                logger.info("Skipping webui prestart for user %s: %s", user_id, rejection.code)
                return

        # Review round 1 (T-B): the explicit-mapping requirement belongs to
        # the os_user chain — key the early exit on the launch FORM the
        # default request would take (the strongest verified form satisfying
        # the floor), not on the floor itself. A pinned `os_user` floor on a
        # sandboxed-capable deployment launches pods, which have no OS
        # account by design; skipping those prestarts would silently diverge
        # from what /user-url actually launches.
        launch_form_is_sandboxed = snapshot.isolation_level == ISOLATION_LEVEL_SANDBOXED
        if not system_account and not launch_form_is_sandboxed:
            logger.info("Skipping webui prestart for user %s: no explicit mapping", user_id)
            return

        # Check if already has an instance
        with self._lock:
            if user_id in self._instances:
                instance = self._instances[user_id]
                if instance.is_alive():
                    logger.info(f"User {user_id} already has active instance, skipping pre-start")
                    return

        # Start in background thread
        def start_in_background():
            try:
                logger.info(f"Pre-starting webui instance for user {user_id} ({system_account})")
                url, token = self.get_user_webui_url(user_id, system_account, host_url)
                logger.info(f"Pre-started webui for user {user_id}: {url}")
            except Exception as e:
                logger.error(f"Failed to pre-start webui for user {user_id}: {e}")

        gevent.spawn(start_in_background)
        logger.info(f"Spawned greenlet to pre-start webui for user {user_id}")


# Global manager instance
_manager: WebUIManager | None = None


def get_webui_manager() -> WebUIManager:
    """Get the global WebUI manager instance, creating it if needed."""
    global _manager
    if _manager is None:
        _manager = WebUIManager()
        # Start cleanup thread when manager is created
        _manager.start_cleanup_thread()
    return _manager


def peek_webui_manager() -> WebUIManager | None:
    """Return the existing manager singleton WITHOUT creating one.

    Issue #3374 review #13: read-only capability paths use this —
    constructing a manager mints a token secret and spawns a resident
    cleanup greenlet, which a pure capability GET must not do.
    """
    return _manager


def shutdown_webui_manager():
    """Shutdown the global WebUI manager."""
    global _manager
    if _manager is not None:
        _manager.stop_cleanup_thread()
        _manager.stop_all_instances()
        _manager = None
