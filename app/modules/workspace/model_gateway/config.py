"""Gateway configuration access: toggle + encrypted credentials (env or DB).

Resolution order: environment overrides first (enables DB-free unit tests and
headless/CI setups), then the single admin DB row (Phase B). Returns None when no
usable base_url + api_key are configured, regardless of the toggle — the planner
treats None as "not configured" and the handler surfaces a 503 (no silent
fallback to direct mode).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from app.utils.config import is_model_gateway_enabled

logger = logging.getLogger(__name__)


@dataclass
class GatewayConfig:
    """Resolved gateway credentials + model-prefix options."""

    base_url: str
    api_key: str
    model_prefix_mode: bool = False
    model_prefix: str | None = None


def is_enabled() -> bool:
    """True when the gateway is toggled on via config.json flag OR env override."""
    if is_model_gateway_enabled():
        return True
    return os.environ.get("OPENACE_MODEL_GATEWAY_MODE", "").strip().lower() == "gateway"


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in ("1", "true", "yes", "on")


def _config_from_env() -> GatewayConfig | None:
    """Read gateway credentials from env (overrides DB). None if incomplete."""
    base_url = os.environ.get("OPENACE_MODEL_GATEWAY_BASE_URL", "").strip()
    api_key = os.environ.get("OPENACE_MODEL_GATEWAY_API_KEY", "").strip()
    if not base_url or not api_key:
        return None
    return GatewayConfig(
        base_url=base_url,
        api_key=api_key,
        model_prefix_mode=_truthy(os.environ.get("OPENACE_MODEL_GATEWAY_MODEL_PREFIX_MODE")),
        model_prefix=(os.environ.get("OPENACE_MODEL_GATEWAY_MODEL_PREFIX", "").strip() or None),
    )


def _config_from_db() -> GatewayConfig | None:
    """Read gateway credentials from the admin DB row. None if absent/unavailable.

    Imported lazily so Phase A (no DB table yet) and tests that mock the planner
    never require the repository to exist.
    """
    try:
        from app.modules.workspace.model_gateway.repository import get_gateway_repository

        return get_gateway_repository().get_config_with_key()
    except Exception as exc:  # noqa: BLE001 — DB optional in POC phase / tests
        logger.debug("model_gateway: DB config unavailable: %s", exc)
        return None


def get_gateway_config() -> GatewayConfig | None:
    """Resolve gateway credentials. Env overrides take precedence; else DB row."""
    env_cfg = _config_from_env()
    if env_cfg is not None:
        return env_cfg
    return _config_from_db()
