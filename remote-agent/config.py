"""
Open ACE Remote Agent - Configuration Management

Loads configuration from environment variables and/or a config file.
Config file location: ~/.open-ace-agent/config.json
Environment variables take precedence over the config file.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import uuid
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Default configuration values
DEFAULTS = {
    "server_url": "http://localhost:19888",
    "heartbeat_interval": 60,
    "reconnect_base_delay": 1,
    "reconnect_max_delay": 60,
    "output_buffer_size": 4096,
    "max_sessions": 5,
    "log_level": "INFO",
    "skip_ssl_verify": False,  # Security: Default to verifying TLS certificates
    "allow_insecure_tls": False,  # Admin policy: explicit insecure mode is disabled by default
    "ca_bundle_path": None,  # Optional: Custom CA bundle for private certificates
}

CONFIG_DIR = Path.home() / ".open-ace-agent"
CONFIG_FILE = CONFIG_DIR / "config.json"


class AgentConfig:
    """
    Configuration for the remote agent daemon.

    Resolution order (highest priority first):
    1. Environment variables
    2. Config file (~/.open-ace-agent/config.json)
    3. Built-in defaults
    """

    def __init__(self, config_path: str | None = None):
        self._config_path = Path(config_path) if config_path else CONFIG_FILE
        self._data: dict[str, Any] = {}
        self._load()

    def _load(self) -> None:
        """Load configuration from file, then apply environment variable overrides."""
        # Start with defaults
        self._data = dict(DEFAULTS)

        # Load config file if it exists
        if self._config_path.exists():
            try:
                with open(self._config_path) as f:
                    file_config = json.load(f)
                self._data.update(file_config)
                logger.info("Loaded config from %s", self._config_path)
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("Failed to load config file %s: %s", self._config_path, e)
        else:
            logger.info("Config file not found at %s, using defaults", self._config_path)

        # Apply environment variable overrides
        env_overrides = {
            "OPENACE_SERVER_URL": ("server_url", str),
            "OPENACE_AGENT_TOKEN": ("agent_token", str),
            "OPENACE_MACHINE_ID": ("machine_id", str),
            "OPENACE_HEARTBEAT_INTERVAL": ("heartbeat_interval", int),
            "OPENACE_RECONNECT_BASE_DELAY": ("reconnect_base_delay", float),
            "OPENACE_RECONNECT_MAX_DELAY": ("reconnect_max_delay", float),
            "OPENACE_MAX_SESSIONS": ("max_sessions", int),
            "OPENACE_LOG_LEVEL": ("log_level", str),
            "OPENACE_SKIP_SSL_VERIFY": (
                "skip_ssl_verify",
                lambda v: v.lower() in ("true", "1", "yes"),
            ),
            "OPENACE_ALLOW_INSECURE_TLS": (
                "allow_insecure_tls",
                lambda v: v.lower() in ("true", "1", "yes"),
            ),
            "OPENACE_CA_BUNDLE_PATH": ("ca_bundle_path", str),
        }

        for env_key, (config_key, type_fn) in env_overrides.items():
            value = os.environ.get(env_key)
            if value is not None:
                try:
                    self._data[config_key] = type_fn(value)
                    logger.debug("Override %s from env var %s", config_key, env_key)
                except (ValueError, TypeError) as e:
                    logger.warning("Invalid env var %s=%r: %s", env_key, value, e)

    # --- Required settings ---

    @property
    def server_url(self) -> str:
        """Open ACE server URL (e.g. https://ace.example.com)."""
        url = self._data.get("server_url", DEFAULTS["server_url"])
        return url.rstrip("/")

    @property
    def agent_token(self) -> str | None:
        """
        Bearer authentication token for agent-server communication.

        Issued by the server during registration (one-time registration_token
        exchange). Persisted in config.json after successful registration.
        """
        return self._data.get("agent_token")

    @property
    def machine_id(self) -> str:
        """
        Unique machine identifier.

        If not configured, reads from a local file (~/.open-ace-agent/machine_id).
        If that file does not exist either, generates a new UUID and saves it.
        """
        mid = self._data.get("machine_id")
        if mid:
            return mid

        # Try loading from local file
        mid_file = CONFIG_DIR / "machine_id"
        if mid_file.exists():
            try:
                mid = mid_file.read_text().strip()
                if mid:
                    self._data["machine_id"] = mid
                    return mid
            except OSError:
                pass

        # Generate and persist a new machine ID
        mid = str(uuid.uuid4())
        self._data["machine_id"] = mid
        self._save_machine_id(mid)
        return mid

    # --- Optional / tuning settings ---

    @property
    def heartbeat_interval(self) -> int:
        """Seconds between heartbeat messages."""
        return self._data.get("heartbeat_interval", DEFAULTS["heartbeat_interval"])

    @property
    def reconnect_base_delay(self) -> float:
        """Base delay in seconds for exponential backoff reconnection."""
        return self._data.get("reconnect_base_delay", DEFAULTS["reconnect_base_delay"])

    @property
    def reconnect_max_delay(self) -> float:
        """Maximum delay in seconds for reconnection backoff."""
        return self._data.get("reconnect_max_delay", DEFAULTS["reconnect_max_delay"])

    @property
    def output_buffer_size(self) -> int:
        """Read buffer size for subprocess stdout/stderr."""
        return self._data.get("output_buffer_size", DEFAULTS["output_buffer_size"])

    @property
    def skip_ssl_verify(self) -> bool:
        """Skip SSL certificate verification (for self-signed certs)."""
        return self._data.get("skip_ssl_verify", DEFAULTS["skip_ssl_verify"])

    @property
    def ca_bundle_path(self) -> str | None:
        """Custom CA bundle path for private certificates."""
        return self._data.get("ca_bundle_path", DEFAULTS["ca_bundle_path"])

    @property
    def allow_insecure_tls(self) -> bool:
        """Whether administrator policy permits explicitly insecure TLS."""
        return self._data.get("allow_insecure_tls", DEFAULTS["allow_insecure_tls"])

    @property
    def max_sessions(self) -> int:
        """Maximum concurrent sessions allowed on this agent."""
        return self._data.get("max_sessions", DEFAULTS["max_sessions"])

    @property
    def log_level(self) -> str:
        """Logging level (DEBUG, INFO, WARNING, ERROR)."""
        return self._data.get("log_level", DEFAULTS["log_level"])

    # --- Derived properties ---

    @property
    def ws_url(self) -> str:
        """Websocket URL derived from server_url."""
        http_url = self.server_url
        if http_url.startswith("https://"):
            return "wss://" + http_url[8:]
        elif http_url.startswith("http://"):
            return "ws://" + http_url[7:]
        return "ws://" + http_url

    @property
    def machine_name(self) -> str:
        """Human-readable machine name."""
        return self._data.get("machine_name", socket.gethostname())

    @property
    def hostname(self) -> str:
        """Machine hostname."""
        return self._data.get("hostname", socket.gethostname())

    # --- Persistence helpers ---

    def _save_machine_id(self, machine_id: str) -> None:
        """Persist the machine ID to disk."""
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            (CONFIG_DIR / "machine_id").write_text(machine_id)
            logger.info("Saved machine_id to %s", CONFIG_DIR / "machine_id")
        except OSError as e:
            logger.warning("Failed to save machine_id: %s", e)

    def save(self) -> None:
        """Save current configuration to the config file."""
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            with open(self._config_path, "w") as f:
                json.dump(self._data, f, indent=2)
            logger.info("Saved config to %s", self._config_path)
        except OSError as e:
            logger.warning("Failed to save config: %s", e)

    def save_agent_token(self, token: str) -> None:
        """Save agent_token to config file for persistence across restarts."""
        if os.environ.get("OPENACE_AGENT_TOKEN"):
            logger.warning(
                "OPENACE_AGENT_TOKEN env var is set; saved token will be"
                " overridden on next restart. Unset the env var to use"
                " the config file value."
            )
        self.update({"agent_token": token})
        self.save()
        logger.info("Agent token updated and persisted to config")

    def ensure_config_dir(self) -> None:
        """Create the configuration directory if it does not exist."""
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            logger.warning("Failed to create config directory: %s", e)

    def update(self, values: dict[str, Any]) -> None:
        """Update multiple config values at once."""
        self._data.update(values)

    def to_dict(self) -> dict[str, Any]:
        """Return a copy of the current configuration."""
        return dict(self._data)

    def __repr__(self) -> str:  # noqa: D105
        # Hide sensitive fields
        safe = {k: v for k, v in self._data.items() if k != "agent_token"}
        return f"AgentConfig({safe})"

    def get_tls_config(
        self,
        explicit_insecure: bool = False,
        ca_bundle_path: str | None = None,
    ) -> Any:
        """
        Create TLSConfig from this configuration.

        Args:
            explicit_insecure: Whether --insecure-skip-tls-verify CLI flag was used
            ca_bundle_path: Optional CLI override for the custom CA bundle

        Returns:
            TLSConfig instance
        """
        from tls_config import TLSConfig

        return TLSConfig.from_config(
            self,
            explicit_insecure=explicit_insecure,
            ca_bundle_path=ca_bundle_path,
        )
