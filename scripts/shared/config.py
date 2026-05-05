#!/usr/bin/env python3
"""
Open ACE - AI Computing Explorer - Configuration Module

Provides centralized configuration for the open-ace project.

This module should be the single source of truth for all path configurations.
For remote machine configurations, edit the config.json file or use the
environment variables to override defaults.
"""

import json
import os
from typing import cast

# Configuration directory path
# This is the main configuration that should be set during installation
CONFIG_DIR = os.path.expanduser("~/.open-ace")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")
DB_DIR = CONFIG_DIR  # Database is stored in the same directory
DB_PATH = os.path.join(DB_DIR, "ace.db")

# Remote user name - default is 'openclaw' but can be overridden
# This is used for remote deployment and fetching data from remote machines
REMOTE_USER = os.environ.get("AI_TOKEN_REMOTE_USER", "openclaw")

# Remote configuration directory on remote machines
# This is used when deploying to or fetching data from remote machines
REMOTE_CONFIG_DIR = f"/home/{REMOTE_USER}/.open-ace"
REMOTE_DB_PATH = f"{REMOTE_CONFIG_DIR}/ace.db"


def _load_user_config() -> dict:
    """Load user configuration from config.json if it exists."""
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH) as f:
                return cast(dict, json.load(f))
        except (OSError, json.JSONDecodeError):
            pass
    return {}


def _get_web_port() -> int:
    """Get web server port with priority: config file > default."""
    # Priority 1: Config file
    user_config = _load_user_config()
    server_config = user_config.get("server", {})
    config_port = server_config.get("web_port")
    if config_port:
        try:
            return int(config_port)
        except (ValueError, TypeError):
            pass

    # Priority 2: Default
    return 5000


def _get_web_host() -> str:
    """Get web server host with priority: config file > default."""
    # Priority 1: Config file
    user_config = _load_user_config()
    server_config = user_config.get("server", {})
    config_host = server_config.get("web_host")
    if config_host:
        return cast(str, config_host)

    # Priority 2: Default
    return "0.0.0.0"


# Web server configuration
# Port and host are loaded with priority: config file > environment variable > default
WEB_PORT = _get_web_port()
WEB_HOST = _get_web_host()


def ensure_config_dir():
    """Ensure the configuration directory exists."""
    os.makedirs(CONFIG_DIR, exist_ok=True)


def ensure_db_dir():
    """Ensure the database directory exists."""
    os.makedirs(DB_DIR, exist_ok=True)


def load_remote_config() -> dict:
    """Load remote configuration from config.json if it exists."""
    remote_config_path = os.path.join(CONFIG_DIR, "remote_config.json")
    if os.path.exists(remote_config_path):
        try:
            with open(remote_config_path) as f:
                return cast(dict, json.load(f))
        except (OSError, json.JSONDecodeError):
            pass
    return {}


def get_remote_users() -> list:
    """Get list of configured remote users."""
    config = load_remote_config()
    if "remote_users" in config:
        return cast(list, config["remote_users"])
    return [REMOTE_USER]


def get_database_config() -> dict:
    """
    Get database configuration from config.json.

    Returns:
        dict: Database configuration with keys:
            - type: 'sqlite' or 'postgresql'
            - url: Database URL (for PostgreSQL)
            - path: Database path (for SQLite, optional)
    """
    user_config = _load_user_config()
    db_config = user_config.get("database", {})

    # Default configuration: PostgreSQL is the default
    default_config = {"type": "postgresql", "path": DB_PATH, "url": None}

    # Merge with user config
    default_config.update(db_config)
    return cast(dict, default_config)


def get_database_url() -> str:
    """
    Get database URL with priority: environment variable > config file > default PostgreSQL > fallback SQLite.

    Returns:
        str: Database URL.
    """
    # Priority 1: Environment variable DATABASE_URL
    if os.environ.get("DATABASE_URL"):
        return os.environ["DATABASE_URL"]

    # Priority 2: Config file
    db_config = get_database_config()
    db_type = db_config.get("type", "postgresql").lower()

    if db_type == "postgresql":
        url = db_config.get("url")
        if url:
            return url
        # If type is postgresql but no url, fall back to SQLite
        print("Warning: database type is postgresql but no url configured, using SQLite")
        db_path = db_config.get("path", DB_PATH)
        return f"sqlite:///{db_path}"

    # Priority 3: SQLite (explicitly configured)
    db_path = db_config.get("path", DB_PATH)
    return f"sqlite:///{db_path}"


def get_data_fetch_config() -> dict:
    """
    Get data fetch configuration from config.json.

    Returns:
        dict: Data fetch configuration with keys:
            - interval: Fetch interval in seconds (default: 300 = 5 minutes)
            - enabled: Whether auto fetch is enabled (default: True)
    """
    user_config = _load_user_config()
    fetch_config = user_config.get("data_fetch", {})

    # Default configuration
    default_config = {"interval": 300, "enabled": True}  # 5 minutes

    # Merge with user config
    default_config.update(fetch_config)
    return cast(dict, default_config)


def get_data_fetch_interval() -> int:
    """
    Get data fetch interval with priority: environment variable > config file > default.

    Returns:
        int: Fetch interval in seconds.
    """
    # Priority 1: Environment variable
    env_interval = os.environ.get("DATA_FETCH_INTERVAL")
    if env_interval:
        try:
            return int(env_interval)
        except ValueError:
            pass

    # Priority 2: Config file
    fetch_config = get_data_fetch_config()
    return cast(int, fetch_config.get("interval", 300))


def is_data_fetch_enabled() -> bool:
    """
    Check if data fetch is enabled.

    Returns:
        bool: True if data fetch is enabled.
    """
    # Priority 1: Environment variable
    env_enabled = os.environ.get("DATA_FETCH_ENABLED")
    if env_enabled:
        return env_enabled.lower() in ("true", "1", "yes")

    # Priority 2: Config file
    fetch_config = get_data_fetch_config()
    return cast(bool, fetch_config.get("enabled", True))


def get_quota_enforcement_config() -> dict:
    """Get quota enforcement configuration from config.json."""
    user_config = _load_user_config()
    enforcement_config = user_config.get("quota_enforcement", {})
    default_config = {"interval": 60, "enabled": True}
    default_config.update(enforcement_config)
    return default_config
