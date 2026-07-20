#!/usr/bin/env python3
"""
Open ACE - AI Computing Explorer - WebUI Manager Service
Manages per-user qwen-code-webui processes in multi-user mode.
Each user gets an independent webui process running under their system_account.
"""

from __future__ import annotations


import hashlib
import json
import logging
import os
import platform
import pwd
import secrets
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

    _last_health_check: float = 0.0
    _health_check_ttl: float = 30.0  # Cache health check result for 30s
    _consecutive_health_failures: int = 0
    _max_consecutive_failures: int = 10  # 10 × 30s = ~5min before declaring dead

    def is_alive(self) -> bool:
        """Check if the process is still running and responsive."""
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
    webui_path: str = ""  # Path to qwen-code-webui project directory
    # Optional explicit URL for the webui to reach the LLM proxy (e.g. behind an
    # HTTPS reverse proxy). When set, :web_port is NOT appended. See issue #1730.
    webui_callback_url: str = ""


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
        self._port_allocations: dict[int, int] = {}  # port -> user_id
        self._lock = gevent_lock.RLock()  # gevent-safe reentrant lock
        self._cleanup_greenlet: gevent.Greenlet | None = None
        self._running = False

        # Generate token secret if not configured
        if not self.config.token_secret:
            self.config.token_secret = secrets.token_hex(32)

        # Platform detection
        self._platform = platform.system().lower()

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
            )
        except Exception as e:
            logger.error(f"Error loading config: {e}")
            return WorkspaceConfig()

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
        """Periodically clean up idle instances."""
        while self._running:
            try:
                self.cleanup_idle_instances()
            except Exception as e:
                logger.error(f"Error in cleanup loop: {e}")

            # Sleep for cleanup interval
            time.sleep(self.config.cleanup_interval_minutes * 60)

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

    def allocate_port(self, user_id: int) -> int:
        """
        Allocate a port for a user.

        Args:
            user_id: User ID to allocate port for.

        Returns:
            Allocated port number.

        Raises:
            ValueError: If no ports are available.
        """
        with self._lock:
            # Check if user already has an allocated port
            for port, uid in self._port_allocations.items():
                if uid == user_id:
                    return port

            # Find an available port
            for port in range(self.config.port_range_start, self.config.port_range_end + 1):
                if port not in self._port_allocations:
                    # Verify port is actually available
                    if self._is_port_available(port):
                        self._port_allocations[port] = user_id
                        logger.info(f"Allocated port {port} for user {user_id}")
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

    def release_port(self, port: int):
        """Release a port back to the pool."""
        with self._lock:
            if port in self._port_allocations:
                user_id = self._port_allocations.pop(port)
                logger.info(f"Released port {port} from user {user_id}")

    def generate_token(self, user_id: int, port: int) -> str:
        """
        Generate an authentication token for a user.

        Token format: {user_id}:{port}:{random}:{signature}

        Args:
            user_id: User ID.
            port: Allocated port.

        Returns:
            Generated token string.
        """
        random_part = secrets.token_hex(16)
        signature = hashlib.sha256(
            f"{user_id}:{port}:{random_part}:{self.config.token_secret}".encode()
        ).hexdigest()[:16]
        return f"{user_id}:{port}:{random_part}:{signature}"

    def validate_token(self, token: str) -> tuple[bool, int | None, str | None]:
        """
        Validate an authentication token.

        Args:
            token: Token string to validate.

        Returns:
            Tuple of (is_valid, user_id, error_message).
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

            if signature != expected_signature:
                return False, None, "Invalid signature"

            # Note: We no longer check _port_allocations because each request
            # creates a new WebUIManager instance with empty allocations.
            # Signature validation is sufficient for security.

            return True, user_id, None

        except (ValueError, TypeError) as e:
            return False, None, f"Token parse error: {e}"

    def get_user_webui_url(
        self, user_id: int, system_account: str, host_url: str | None = None
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

        if not self.config.multi_user_mode:
            # Single-user mode (docker compose): use fixed WebUI port 3100
            # base_url from _replace_host_from_request has no port
            token = self.generate_token(user_id, 0)
            if host_url:
                # Docker compose: URL from request.host_url with fixed port 3100
                url = f"{base_url}:3100"
            else:
                # Fallback: use config.url (for edge cases where host_url not provided)
                url = base_url
            return url, token

        with self._lock:
            # Check if user already has an instance
            if user_id in self._instances:
                instance = self._instances[user_id]
                if instance.is_alive():
                    instance.update_activity()
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
            instance = self._start_instance_internal(user_id, system_account, base_url)
            return instance.url, instance.token

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
        port = self.allocate_port(user_id)

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
                self.release_port(port)
                raise ValueError("Failed to launch webui process")

            # Wait for service to be ready
            if not self._wait_for_service_ready(port, timeout=10.0):
                # Service not ready, clean up
                try:
                    process.terminate()
                    process.wait(timeout=2)
                except Exception:
                    pass
                self.release_port(port)
                raise ValueError("WebUI service failed to start within timeout")

        except Exception as e:
            logger.error(f"Failed to start webui process: {e}")
            self.release_port(port)
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

        # Find webui executable or project path
        webui_cmd, webui_dir = self._find_webui_executable()

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

        # Build child environment first (needed for sudo env passing)
        child_env = os.environ.copy()
        model_pool = self._configure_local_openai_proxy(user_id, child_env, openace_api_url)

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

        # Ensure PATH can resolve the `node` binary while preserving the host's
        # inherited PATH. Prepend the standard system directories so Docker
        # (node in /usr/bin) keeps resolving (Issue #1083), then append the
        # inherited PATH so host-installed binaries are still found — e.g.
        # /opt/homebrew/bin on Apple Silicon macOS, where Homebrew installs
        # node. NODE_PATH is intentionally NOT set: it controls Node *module*
        # resolution (a list of directories, not a binary path), so pointing it
        # at an executable was semantically wrong and could only interfere with
        # module lookup.
        _system_dirs = "/usr/local/bin:/usr/bin:/bin"
        _inherited_path = child_env.get("PATH", "")
        child_env["PATH"] = (
            _system_dirs + ":" + _inherited_path if _inherited_path else _system_dirs
        )

        # Build command based on platform
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
                # Different user: use sudo -u for global executable
                # Environment variables are passed via sudoers env_keep configuration
                cmd = [
                    "sudo",
                    "-u",
                    system_account,
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
                env=child_env,  # Passed to sudo; preserved via sudoers env_keep
                stdout=subprocess.DEVNULL,  # WebUI handles its own logging via OPENACE_LOG_DIR
                stderr=subprocess.DEVNULL,
            )
            return process, model_pool
        except Exception as e:
            logger.error(f"Failed to launch webui process: {e}")
            return None, model_pool

    def _find_webui_executable(self) -> tuple[str | None, str | None]:
        """
        Find the qwen-code-webui executable.

        Returns:
            Tuple of (executable_path, working_directory).
            If running from project directory, executable_path is the node.js entry
            and working_directory is the backend directory.
            If running global executable, working_directory is None.
        """
        # Check webui_path from config (project directory mode)
        if self.config.webui_path:
            webui_backend = os.path.join(self.config.webui_path, "backend")
            node_entry = os.path.join(webui_backend, "dist", "cli", "node.js")

            if os.path.isfile(node_entry):
                logger.info(f"Using webui from project directory: {self.config.webui_path}")
                return node_entry, webui_backend

            # Check if project needs to be built
            if os.path.isdir(webui_backend):
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
        self.release_port(instance.port)

    def stop_user_webui(self, user_id: int):
        """
        Stop the webui instance for a user.

        Args:
            user_id: User ID to stop instance for.
        """
        with self._lock:
            self._stop_instance_internal(user_id)

    def stop_all_instances(self):
        """Stop all running webui instances."""
        with self._lock:
            for user_id in list(self._instances.keys()):
                self._stop_instance_internal(user_id)

        logger.info("All webui instances stopped")

    def get_instance_count(self) -> int:
        """Get the number of active instances."""
        with self._lock:
            return sum(1 for i in self._instances.values() if i.is_alive())

    def get_user_instance(self, user_id: int) -> WebUIInstance | None:
        """Get the instance for a specific user."""
        with self._lock:
            return self._instances.get(user_id)

    def get_all_instances(self) -> list[dict[str, Any]]:
        """Get information about all instances."""
        with self._lock:
            return [
                {
                    "user_id": i.user_id,
                    "system_account": i.system_account,
                    "port": i.port,
                    "pid": i.pid,
                    "url": i.url,
                    "allocated_at": i.allocated_at.isoformat(),
                    "last_activity": i.last_activity.isoformat(),
                    "is_alive": i.is_alive(),
                }
                for i in self._instances.values()
            ]

    def update_user_activity(self, user_id: int):
        """Update the activity timestamp for a user's instance."""
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
            system_account: User's system account name.
            host_url: Optional host URL from Flask request (e.g., "http://192.168.1.87:19888").
                      Used to replace container-detected IP with user's actual access IP.
                      Required for Docker deployments where container cannot detect host's real IP.
        """
        if not self.config.multi_user_mode:
            return  # No pre-start needed in single-user mode

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
    """Get the global WebUI manager instance."""
    global _manager
    if _manager is None:
        _manager = WebUIManager()
        # Start cleanup thread when manager is created
        _manager.start_cleanup_thread()
    return _manager


def shutdown_webui_manager():
    """Shutdown the global WebUI manager."""
    global _manager
    if _manager is not None:
        _manager.stop_cleanup_thread()
        _manager.stop_all_instances()
        _manager = None
