"""Feishu connection testing.

Configuration persistence moved to ``NotificationSettingsRepository`` in
Issue #2628. This module intentionally contains no config.json read/write path.
"""

import logging
from typing import Any

from app.utils.outbound_url_guard import OutboundUrlBlockedError, safe_request
from app.utils.placeholder import is_placeholder_value

logger = logging.getLogger(__name__)
FEISHU_AUTH_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"


class FeishuConfigService:
    """Test Feishu credentials without owning configuration persistence."""

    def test_connection(self, app_id: str | None, app_secret: str | None) -> dict[str, Any]:
        """Exchange credentials for a tenant token and return a sanitized result."""
        if not app_id or not app_secret:
            return {"success": False, "message": "App ID and App Secret are required"}
        if is_placeholder_value(app_id) or is_placeholder_value(app_secret):
            return {
                "success": False,
                "message": "App ID or App Secret appears to be a placeholder value",
            }
        try:
            # Issue #2237: Use safe_request to avoid gevent RecursionError and get SSRF protection
            response = safe_request(
                "POST",
                FEISHU_AUTH_URL,
                json={"app_id": app_id, "app_secret": app_secret},
                timeout=15,
            )
            response.raise_for_status()
            data = response.json()
            if data.get("code") != 0:
                return {
                    "success": False,
                    "message": f"Feishu API error (code={data.get('code')}): "
                    f"{data.get('msg', 'Unknown error')}",
                }
            if not data.get("tenant_access_token"):
                return {"success": False, "message": "Failed to get tenant_access_token"}
            return {
                "success": True,
                "message": "Feishu connection test successful",
                "token_expire_seconds": data.get("expire", 7200),
            }
        except OutboundUrlBlockedError as e:
            logger.error("Feishu connection test blocked by SSRF protection: %s", e)
            return {"success": False, "message": f"Request blocked by security policy: {e}"}
        except Exception:
            logger.warning("Feishu connection test failed", exc_info=True)
            return {"success": False, "message": "Feishu connection test failed"}


_feishu_config_service = FeishuConfigService()


def get_feishu_config_service() -> FeishuConfigService:
    """Return the process-wide Feishu connection tester."""
    return _feishu_config_service
