"""
Configuration utility for reading values from ~/.open-ace/config.json.
"""

import json
import logging
import os

logger = logging.getLogger(__name__)


def get_config_value(section: str, key: str, default=None):
    """Read a value from ~/.open-ace/config.json.

    Returns the value at config[section][key], or `default` if the
    file, section, or key does not exist.

    Args:
        section: Top-level config section (e.g. "workspace", "autonomous").
        key: Key within the section (e.g. "enabled").
        default: Fallback value if the file, section, or key is missing.

    Returns:
        The config value, or `default`.
    """
    from app.repositories.database import CONFIG_DIR

    config_path = os.path.join(CONFIG_DIR, "config.json")
    if not os.path.exists(config_path):
        return default
    try:
        with open(config_path) as f:
            config = json.load(f)
        return config.get(section, {}).get(key, default)
    except Exception as e:
        logger.warning(f"Error reading config [{section}][{key}]: {e}")
        return default


def is_autonomous_enabled() -> bool:
    """Check whether the autonomous development feature is enabled."""
    return bool(get_config_value("autonomous", "enabled", False))
