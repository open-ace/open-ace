"""Tenant configuration cache for sensitive keyword settings.

This module provides a shared caching mechanism for tenant-specific
sensitive keyword filtering configuration, avoiding code duplication
across multiple modules.
"""

import logging
import threading
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# Tenant config cache for sensitive keyword settings
# Structure: {tenant_id: {"config": dict, "expiry": datetime}}
_tenant_config_cache: dict[int, dict[str, Any]] = {}
_tenant_config_cache_lock = threading.Lock()
_TENANT_CONFIG_CACHE_TTL = 300  # 5 minutes


def get_tenant_sensitive_keyword_config(tenant_id: int) -> dict[str, Any]:
    """
    Get tenant-specific sensitive keyword configuration with caching.

    Args:
        tenant_id: Tenant ID.

    Returns:
        Dictionary with 'block_sensitive_keyword' and 'sensitive_keyword_match_mode' keys.
    """
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    # Check cache first
    with _tenant_config_cache_lock:
        if tenant_id in _tenant_config_cache:
            cached = _tenant_config_cache[tenant_id]
            if cached["expiry"] > now:
                return dict(cached["config"])

    # Fetch from database
    try:
        from app.repositories.tenant_repo import TenantRepository

        tenant_repo = TenantRepository()
        tenant = tenant_repo.get_by_id(tenant_id)
        if tenant and tenant.settings:
            config = {
                "block_sensitive_keyword": tenant.settings.block_sensitive_keyword,
                "sensitive_keyword_match_mode": tenant.settings.sensitive_keyword_match_mode,
            }
        else:
            config = {
                "block_sensitive_keyword": False,
                "sensitive_keyword_match_mode": "word_boundary",
            }
    except Exception as e:
        logger.warning(f"Failed to fetch tenant config for tenant {tenant_id}: {e}")
        config = {
            "block_sensitive_keyword": False,
            "sensitive_keyword_match_mode": "word_boundary",
        }

    # Update cache
    with _tenant_config_cache_lock:
        _tenant_config_cache[tenant_id] = {
            "config": config,
            "expiry": now + _TENANT_CONFIG_CACHE_TTL,
        }

    return config


def invalidate_tenant_config_cache(tenant_id: int | None = None) -> None:
    """
    Invalidate tenant config cache.

    Args:
        tenant_id: Specific tenant ID to invalidate, or None to clear all.
    """
    with _tenant_config_cache_lock:
        if tenant_id is not None:
            _tenant_config_cache.pop(tenant_id, None)
        else:
            _tenant_config_cache.clear()