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

# Web server configuration
# Port for the web server (default: 5001, can be overridden via environment variable)
WEB_PORT = int(os.environ.get('AI_TOKEN_WEB_PORT', '5001'))
WEB_HOST = os.environ.get('AI_TOKEN_WEB_HOST', '0.0.0.0')


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
