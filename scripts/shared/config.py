#!/usr/bin/env python3
"""
AI Token Analyzer - Configuration Module

Provides centralized configuration for the ai-token-analyzer project.

This module should be the single source of truth for all path configurations.
For remote machine configurations, edit the config.json file or use the
environment variables to override defaults.
"""

import os
import json

# Configuration directory path
# This is the main configuration that should be set during installation
CONFIG_DIR = os.path.expanduser("~/.ai-token-analyzer")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")
DB_DIR = CONFIG_DIR  # Database is stored in the same directory
DB_PATH = os.path.join(DB_DIR, "usage.db")

# Remote user name - default is 'openclaw' but can be overridden
# This is used for remote deployment and fetching data from remote machines
REMOTE_USER = os.environ.get('AI_TOKEN_REMOTE_USER', 'openclaw')

# Remote configuration directory on remote machines
# This is used when deploying to or fetching data from remote machines
REMOTE_CONFIG_DIR = f"/home/{REMOTE_USER}/.ai-token-analyzer"
REMOTE_DB_PATH = f"{REMOTE_CONFIG_DIR}/usage.db"

def _load_user_config() -> dict:
    """Load user configuration from config.json if it exists."""
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {}


def _get_web_port() -> int:
    """Get web server port with priority: config file > environment variable > default."""
    # Priority 1: Environment variable
    env_port = os.environ.get('AI_TOKEN_WEB_PORT')
    if env_port:
        try:
            return int(env_port)
        except ValueError:
            pass

    # Priority 2: Config file
    user_config = _load_user_config()
    server_config = user_config.get('server', {})
    config_port = server_config.get('web_port')
    if config_port:
        try:
            return int(config_port)
        except (ValueError, TypeError):
            pass

    # Priority 3: Default
    return 5001


def _get_web_host() -> str:
    """Get web server host with priority: config file > environment variable > default."""
    # Priority 1: Environment variable
    env_host = os.environ.get('AI_TOKEN_WEB_HOST')
    if env_host:
        return env_host

    # Priority 2: Config file
    user_config = _load_user_config()
    server_config = user_config.get('server', {})
    config_host = server_config.get('web_host')
    if config_host:
        return config_host

    # Priority 3: Default
    return '0.0.0.0'


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
            with open(remote_config_path, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {}


def get_remote_users() -> list:
    """Get list of configured remote users."""
    config = load_remote_config()
    if 'remote_users' in config:
        return config['remote_users']
    return [REMOTE_USER]
