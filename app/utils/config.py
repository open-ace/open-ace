"""
Configuration utility for reading values from ~/.open-ace/config.json.

Configuration changes require a server restart to take full effect:
the autonomous scheduler is started once at boot and cannot be toggled
at runtime. The API guard reads the cached value (refreshed every 60 s)
to allow near-immediate enforcement of a disable toggle without restart.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)

# ── Simple TTL cache ──────────────────────────────────────────────────
# Avoids reading config.json from disk on every HTTP request while still
# allowing runtime config changes to propagate within ~60 seconds.

_cache_lock = threading.Lock()
_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_cache_ttl: float = 60.0  # seconds


def _read_config() -> dict[str, Any]:
    """Return the parsed config.json, with a 60-second TTL in-memory cache."""
    now = time.time()
    with _cache_lock:
        entry = _cache.get("_root")
        if entry is not None:
            ts, data = entry
            if now - ts < _cache_ttl:
                return data
    # Cache miss — read from disk
    from app.repositories.database import CONFIG_DIR

    config_path = os.path.join(CONFIG_DIR, "config.json")
    result: dict[str, Any] = {}
    if os.path.exists(config_path):
        try:
            with open(config_path) as f:
                result = json.load(f)
        except Exception as e:
            logger.warning("Error reading config.json: %s", e)
    with _cache_lock:
        _cache["_root"] = (now, result)
    return result


def get_config_value(section: str, key: str, default=None):
    """Read a value from ~/.open-ace/config.json.

    Returns the value at config[section][key], or ``default`` if the
    file, section, or key does not exist.  Results are cached for up
    to 60 seconds.

    Args:
        section: Top-level config section (e.g. "workspace", "autonomous").
        key: Key within the section (e.g. "enabled").
        default: Fallback value if the file, section, or key is missing.

    Returns:
        The config value, or ``default``.
    """
    config = _read_config()
    return config.get(section, {}).get(key, default)


def is_autonomous_enabled() -> bool:
    """Check whether the autonomous development feature is enabled."""
    return bool(get_config_value("autonomous", "enabled", False))
