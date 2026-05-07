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
    "server_url": "http://localhost:5000",
    "heartbeat_interval": 60,
    "reconnect_base_delay": 1,
    "reconnect_max_delay": 60,
    "output_buffer_size": 4096,
    "max_sessions": 5,
    "log_level": "INFO",
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
        Authentication token for the agent.

        This is the registration token issued by the server admin during initial
        machine registration. After successful registration, the server returns a
        machine_id that is persisted locally.
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
