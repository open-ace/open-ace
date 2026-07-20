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
    return bool(get_config_value("autonomous", "enabled", True))


def is_run_timeline_enabled() -> bool:
    """Check whether the persisted remote-session run timeline feature is enabled.

    Mirrors ``is_autonomous_enabled``: reads ``run_timeline.enabled`` from
    config.json (60 s TTL cache). When disabled the recorder is a no-op and
    the timeline API returns ``{disabled: true}``. Strictly mirrors autonomous
    (no env bypass) so the whole feature is easy to remove later.
    """
    return bool(get_config_value("run_timeline", "enabled", False))


def is_model_gateway_enabled() -> bool:
    """Check whether the optional LiteLLM-compatible model gateway is enabled.

    Mirrors ``is_run_timeline_enabled``: reads ``model_gateway.enabled`` from
    config.json (60 s TTL cache). When disabled (the default) the LLM proxy uses
    direct provider mode unchanged and the gateway module is inert. The whole
    feature is self-contained and easy to remove later; the env-override layer
    (``OPENACE_MODEL_GATEWAY_MODE``) lives in the gateway package itself.
    """
    return bool(get_config_value("model_gateway", "enabled", False))


def is_policy_enabled() -> bool:
    """Check whether the central policy & approval feature is enabled.

    Reads ``policy.enabled`` from config.json (60 s TTL cache), mirroring
    ``is_run_timeline_enabled``. When disabled, ``get_evaluator`` returns a
    ``NullPolicyEvaluator`` (model → allow, tool → require_human) so the system
    behaves exactly as before — real-time manual approval, no auto allow/deny.
    Strictly mirrors the other toggles (no env bypass) so the feature is easy
    to remove later.
    """
    return bool(get_config_value("policy", "enabled", False))


def get_policy_approval_ttl_seconds() -> int:
    """Default approval lifetime (seconds) for a policy decision.

    Read from ``policy.approval_ttl_seconds`` (default 3600 = 1 hour). A rule
    may override this per-rule via ``policy_rules.approval_ttl_seconds``.
    Approvals never live indefinitely (review M3): an expired decision cannot
    be consumed and a fresh request is triggered.
    """
    return int(get_config_value("policy", "approval_ttl_seconds", 3600) or 3600)


# ── AI GitHub Account env cache ───────────────────────────────────
# Avoids a DB query on every subprocess.run() inside GitHubOps.
# Simple two-variable cache: data + timestamp, guarded by _cache_lock.

_ai_github_env_data: dict[str, str] | None = None
_ai_github_env_ts: float = 0.0
_ai_github_env_ttl: float = 60.0  # seconds


def get_ai_github_env() -> dict[str, str] | None:
    """Return env overrides for the AI GitHub account, or None if not configured.

    Results are cached for up to 60 seconds so that repeated GitHubOps
    subprocess calls do not hit the database each time.

    Returns:
        Dict with GH_TOKEN, GIT_AUTHOR_NAME/EMAIL, GIT_COMMITTER_NAME/EMAIL,
        or None if no AI GitHub token is configured.
    """
    global _ai_github_env_data, _ai_github_env_ts

    now = time.time()
    with _cache_lock:
        if now - _ai_github_env_ts < _ai_github_env_ttl:
            return _ai_github_env_data

    # Cache miss — read from DB
    try:
        from app.repositories.ai_agent_settings_repo import AiAgentSettingsRepo

        result = AiAgentSettingsRepo().get_ai_github_env()
    except Exception as e:
        logger.debug("Failed to read AI GitHub env: %s", e)
        result = None

    with _cache_lock:
        _ai_github_env_data = result
        _ai_github_env_ts = now
    return result


def invalidate_ai_github_env_cache():
    """Force the AI GitHub env cache to refresh on next read.

    Call this after updating AI agent settings via the admin API
    so that new token values propagate immediately instead of
    waiting for the 60-second TTL to expire.
    """
    global _ai_github_env_data, _ai_github_env_ts
    with _cache_lock:
        _ai_github_env_ts = 0.0
