#!/usr/bin/env python3
"""
Open ACE - AI Computing Explorer - WebUI Manager Service

Manages per-user qwen-code-webui processes in multi-user mode.
Each user gets an independent webui process running under their system_account.
"""

import hashlib
import json
import logging
import os
import platform
import secrets
import signal
import socket
import subprocess
import threading
from threading import RLock
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class WebUIInstance:
    """Represents a running webui instance for a user."""

    user_id: int
    system_account: str
    port: int
    pid: Optional[int] = None
    token: str = ""
    allocated_at: datetime = field(default_factory=datetime.now)
    last_activity: datetime = field(default_factory=datetime.now)
    process: Optional[subprocess.Popen] = None
    url: str = ""

    def is_alive(self) -> bool:
        """Check if the process is still running."""
        if self.pid is None:
            return False
        try:
            os.kill(self.pid, 0)
            return True
        except (OSError, ProcessLookupError):
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
    port_range_start: int = 9000
    port_range_end: int = 9999
    max_instances: int = 30
    idle_timeout_minutes: int = 30
    cleanup_interval_minutes: int = 5
    token_secret: str = ""
    webui_path: str = ""  # Path to qwen-code-webui project directory


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

    def __init__(self, config: Optional[WorkspaceConfig] = None):
        """
        Initialize the WebUI manager.

        Args:
            config: Workspace configuration. If None, loads from config.json.
        """
        self.config = config or self._load_config()
        self._instances: Dict[int, WebUIInstance] = {}  # user_id -> instance
        self._port_allocations: Dict[int, int] = {}  # port -> user_id
        self._lock = RLock()  # Use reentrant lock to avoid deadlock
        self._cleanup_thread: Optional[threading.Thread] = None
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
            with open(config_path, "r") as f:
                config = json.load(f)

            workspace = config.get("workspace", {})
            return WorkspaceConfig(
                enabled=workspace.get("enabled", False),
                url=workspace.get("url", "http://localhost"),
                multi_user_mode=workspace.get("multi_user_mode", False),
                port_range_start=workspace.get("port_range_start", 9000),
                port_range_end=workspace.get("port_range_end", 9999),
                max_instances=workspace.get("max_instances", 30),
                idle_timeout_minutes=workspace.get("idle_timeout_minutes", 30),
                cleanup_interval_minutes=workspace.get("cleanup_interval_minutes", 5),
                token_secret=workspace.get("token_secret", ""),
                webui_path=workspace.get("webui_path", ""),
            )
        except Exception as e:
            logger.error(f"Error loading config: {e}")
            return WorkspaceConfig()

    def start_cleanup_thread(self):
        """Start the background cleanup thread."""
        if self._cleanup_thread is not None:
            return

        self._running = True
        self._cleanup_thread = threading.Thread(
            target=self._cleanup_loop, daemon=True
        )
        self._cleanup_thread.start()
        logger.info("Cleanup thread started")

    def stop_cleanup_thread(self):
        """Stop the background cleanup thread."""
        self._running = False
        if self._cleanup_thread is not None:
            self._cleanup_thread.join(timeout=5)
            self._cleanup_thread = None
        logger.info("Cleanup thread stopped")

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
            for port in range(
                self.config.port_range_start, self.config.port_range_end + 1
            ):
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

    def validate_token(self, token: str) -> Tuple[bool, Optional[int], Optional[str]]:
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

            user_id, port, random_part, signature = parts
            user_id = int(user_id)
            port = int(port)

            expected_signature = hashlib.sha256(
                f"{user_id}:{port}:{random_part}:{self.config.token_secret}".encode()
            ).hexdigest()[:16]

            if signature != expected_signature:
                return False, None, "Invalid signature"

            # Verify port is allocated to this user
            if self._port_allocations.get(port) != user_id:
                return False, None, "Port not allocated to user"

            return True, user_id, None

        except (ValueError, TypeError) as e:
            return False, None, f"Token parse error: {e}"

    def get_user_webui_url(
        self, user_id: int, system_account: str
    ) -> Tuple[str, str]:
        """
        Get or create the webui URL for a user.

        In multi-user mode, starts a new instance if needed.
        In single-user mode, returns the configured URL.

        Args:
            user_id: User ID.
            system_account: User's system account name.

        Returns:
            Tuple of (url, token).
        """
        if not self.config.multi_user_mode:
            # Single-user mode: return configured URL with empty token
            return self.config.url, ""

        with self._lock:
            # Check if user already has an instance
            if user_id in self._instances:
                instance = self._instances[user_id]
                if instance.is_alive():
                    instance.update_activity()
                    return instance.url, instance.token

            # Check instance limit
            active_count = sum(1 for i in self._instances.values() if i.is_alive())
            if active_count >= self.config.max_instances:
                raise ValueError(
                    f"Maximum instances ({self.config.max_instances}) reached"
                )

            # Start new instance
            instance = self._start_instance_internal(user_id, system_account)
            return instance.url, instance.token

    def _start_instance_internal(
        self, user_id: int, system_account: str
    ) -> WebUIInstance:
        """
        Start a webui instance for a user (internal, must be called with lock).

        Args:
            user_id: User ID.
            system_account: User's system account name.

        Returns:
            WebUIInstance object.
        """
        # Allocate port
        port = self.allocate_port(user_id)

        # Generate token
        token = self.generate_token(user_id, port)

        # Build URL
        base_url = self.config.url
        # Remove any existing port from base_url
        if ":" in base_url.split("//")[-1]:
            base_url = base_url.split(":")[0] + ":" + base_url.split(":")[1]
        url = f"{base_url}:{port}"

        # Start process
        pid = None
        process = None

        try:
            process = self._launch_webui_process(system_account, port)
            pid = process.pid if process else None
        except Exception as e:
            logger.error(f"Failed to start webui process: {e}")
            self.release_port(port)

        instance = WebUIInstance(
            user_id=user_id,
            system_account=system_account,
            port=port,
            pid=pid,
            token=token,
            process=process,
            url=url,
        )

        self._instances[user_id] = instance
        logger.info(
            f"Started webui instance for user {user_id}: "
            f"port={port}, pid={pid}, system_account={system_account}"
        )

        return instance

    def _launch_webui_process(
        self, system_account: str, port: int
    ) -> Optional[subprocess.Popen]:
        """
        Launch a webui process as the specified user.

        Args:
            system_account: System account to run the process as.
            port: Port for the webui to listen on.

        Returns:
            subprocess.Popen object or None if launch failed.
        """
        # Find webui executable or project path
        webui_cmd, webui_dir = self._find_webui_executable()

        if not webui_cmd:
            logger.error("qwen-code-webui executable not found")
            return None

        # Build command based on platform
        if webui_dir:
            # Running from project directory using node
            cmd = ["node", webui_cmd, "--port", str(port)]
            cwd = webui_dir
        elif self._platform in ("linux", "darwin"):
            # Linux/macOS: use sudo -u for global executable
            cmd = ["sudo", "-u", system_account, webui_cmd, "--port", str(port)]
            cwd = None
        else:
            # Other platforms: direct execution (no user switching)
            cmd = [webui_cmd, "--port", str(port)]
            cwd = None

        logger.debug(f"Launching webui: {cmd}, cwd: {cwd}")

        try:
            # Don't capture stdout/stderr to avoid blocking on pipe buffer
            # In production, consider redirecting to log files
            process = subprocess.Popen(
                cmd,
                start_new_session=True,  # Detach from parent process group
                cwd=cwd,
            )
            return process
        except Exception as e:
            logger.error(f"Failed to launch webui process: {e}")
            return None

    def _find_webui_executable(self) -> Tuple[Optional[str], Optional[str]]:
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
                        logger.info(f"WebUI project built successfully")
                        return node_entry, webui_backend
                except Exception as e:
                    logger.error(f"Failed to build webui project: {e}")

        # Check common locations for global executable
        candidates = [
            "qwen-code-webui",
            "/usr/local/bin/qwen-code-webui",
            "/opt/qwen-code-webui/bin/qwen-code-webui",
        ]

        # Also check if webui is bundled with open-ace
        from app.repositories.database import CONFIG_DIR

        bundled_path = os.path.join(
            os.path.dirname(CONFIG_DIR), "webui", "bin", "qwen-code-webui"
        )
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

    def _stop_instance_internal(self, user_id: int):
        """
        Stop a webui instance (internal, must be called with lock).

        Args:
            user_id: User ID to stop instance for.
        """
        if user_id not in self._instances:
            return

        instance = self._instances.pop(user_id)

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

    def get_user_instance(self, user_id: int) -> Optional[WebUIInstance]:
        """Get the instance for a specific user."""
        with self._lock:
            return self._instances.get(user_id)

    def get_all_instances(self) -> List[Dict[str, Any]]:
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


# Global manager instance
_manager: Optional[WebUIManager] = None


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