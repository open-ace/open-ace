"""
Open ACE - Feishu Config Service

Provides Feishu configuration management and connection testing.
Configuration is stored in ~/.open-ace/config.json under the "feishu" key.
"""

import json
import logging
import os
import re
import shutil
import threading
from typing import Any

import requests

from app.repositories.database import CONFIG_DIR

logger = logging.getLogger(__name__)

# File lock for concurrent config.json writes
_config_lock = threading.Lock()

# Feishu API endpoint for tenant_access_token
FEISHU_AUTH_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"

# Placeholder patterns to detect template values
PLACEHOLDER_PATTERNS = [
    r"<FEISHU_APP_ID>",
    r"<FEISHU_APP_SECRET>",
    r"<APP_ID>",
    r"<APP_SECRET>",
    r"your_app_id",
    r"your_app_secret",
    r"cli_xxxxxxxxxxxxxxxx",
]


def _is_placeholder(value: str) -> bool:
    """Check if a value is a placeholder/template value."""
    if not value:
        return False
    return any(re.search(pattern, value, re.IGNORECASE) for pattern in PLACEHOLDER_PATTERNS)


def _mask_app_secret(app_secret: str | None) -> str:
    """Mask app_secret for display (show first 4 and last 4 chars)."""
    if not app_secret:
        return ""
    if len(app_secret) <= 8:
        return "****"
    return f"{app_secret[:4]}...{app_secret[-4:]}"


class FeishuConfigService:
    """Service for Feishu configuration management."""

    def __init__(self, config_dir: str | None = None):
        """
        Initialize service.

        Args:
            config_dir: Optional config directory override (for testing).
        """
        self.config_dir = config_dir or CONFIG_DIR
        self.config_path = os.path.join(self.config_dir, "config.json")
        self.backup_path = os.path.join(self.config_dir, "config.json.bak")

    def get_config(self) -> dict[str, Any] | None:
        """
        Get Feishu configuration from config.json.

        Returns:
            Feishu config dict with masked app_secret, or None if not configured.
        """
        # Read config directly from file (bypass cache to get fresh data)
        config = self._read_config_file()
        feishu_config = config.get("feishu")

        if not feishu_config:
            return None

        result = dict(feishu_config)

        # Mask app_secret for display
        if "app_secret" in result and result["app_secret"]:
            result["app_secret_masked"] = _mask_app_secret(result["app_secret"])
            result.pop("app_secret", None)
        else:
            result["app_secret_masked"] = ""

        return result

    def get_config_with_secret(self) -> dict[str, Any] | None:
        """
        Get Feishu configuration with unmasked app_secret (for API calls).

        Returns:
            Feishu config dict with unmasked app_secret, or None.
        """
        # Read config directly from file
        config = self._read_config_file()
        feishu_config = config.get("feishu")

        if not feishu_config:
            return None

        return dict(feishu_config)

    def save_config(
        self,
        app_id: str,
        app_secret: str,
        org_sync_enabled: bool = False,
        org_sync_tenant_id: int | None = None,
        org_sync_interval_minutes: int = 60,
        org_sync_max_runtime_seconds: int = 1800,
        org_sync_auto_recover: bool = False,
    ) -> dict[str, Any]:
        """
        Save Feishu configuration to config.json.

        Args:
            app_id: Feishu App ID.
            app_secret: Feishu App Secret.
            org_sync_enabled: Whether org sync is enabled.
            org_sync_tenant_id: Target tenant ID for org sync.
            org_sync_interval_minutes: Sync interval in minutes.
            org_sync_max_runtime_seconds: Max runtime for sync.
            org_sync_auto_recover: Whether to auto-recover from hung syncs.

        Returns:
            Saved config dict (with masked app_secret).

        Raises:
            ValueError: If validation fails.
            RuntimeError: If write fails.
        """
        # Validate inputs
        if not app_id or not app_id.strip():
            raise ValueError("app_id is required")
        if not app_secret or not app_secret.strip():
            raise ValueError("app_secret is required")

        app_id = app_id.strip()
        app_secret = app_secret.strip()

        # Check for placeholder values
        if _is_placeholder(app_id):
            raise ValueError("app_id appears to be a placeholder value, please use a real App ID")
        if _is_placeholder(app_secret):
            raise ValueError(
                "app_secret appears to be a placeholder value, please use a real App Secret"
            )

        with _config_lock:
            # Read existing config
            config = self._read_config_file()

            # Build feishu config
            feishu_config = {
                "app_id": app_id,
                "app_secret": app_secret,
                "org_sync_enabled": org_sync_enabled,
                "org_sync_interval_minutes": max(org_sync_interval_minutes, 5),
                "org_sync_max_runtime_seconds": max(org_sync_max_runtime_seconds, 300),
                "org_sync_auto_recover": org_sync_auto_recover,
            }

            if org_sync_tenant_id is not None:
                feishu_config["org_sync_tenant_id"] = int(org_sync_tenant_id)

            # Backup existing config
            self._backup_config()

            # Update config
            config["feishu"] = feishu_config

            # Write back
            self._write_config_file(config)

            # Invalidate cache
            self._invalidate_cache()

            logger.info("Feishu configuration saved")

            # Return config with masked secret
            result = dict(feishu_config)
            result["app_secret_masked"] = _mask_app_secret(app_secret)
            result.pop("app_secret", None)

            return result

    def test_connection(
        self,
        app_id: str | None = None,
        app_secret: str | None = None,
    ) -> dict[str, Any]:
        """
        Test Feishu connection by getting tenant_access_token.

        Args:
            app_id: Test App ID (uses saved config if not provided).
            app_secret: Test App Secret (uses saved config if not provided).

        Returns:
            Dict with success status and message.
        """
        # Use saved config if parameters not provided
        if app_id is None or app_secret is None:
            saved_config = self.get_config_with_secret()
            if not saved_config:
                return {
                    "success": False,
                    "message": "No Feishu configuration found",
                }
            app_id = app_id or saved_config.get("app_id")
            app_secret = app_secret or saved_config.get("app_secret")

        if not app_id or not app_secret:
            return {
                "success": False,
                "message": "App ID and App Secret are required",
            }

        # Check for placeholder values
        if _is_placeholder(app_id) or _is_placeholder(app_secret):
            return {
                "success": False,
                "message": "App ID or App Secret appears to be a placeholder value",
            }

        try:
            response = requests.post(
                FEISHU_AUTH_URL,
                json={"app_id": app_id, "app_secret": app_secret},
                timeout=15,
            )
            response.raise_for_status()
            data = response.json()

            if data.get("code") != 0:
                error_msg = data.get("msg", "Unknown error")
                return {
                    "success": False,
                    "message": f"Feishu API error (code={data.get('code')}): {error_msg}",
                }

            if not data.get("tenant_access_token"):
                return {
                    "success": False,
                    "message": "Failed to get tenant_access_token",
                }

            return {
                "success": True,
                "message": "Feishu connection test successful",
                "token_expire_seconds": data.get("expire", 7200),
            }

        except requests.exceptions.Timeout:
            return {
                "success": False,
                "message": "Connection timeout: unable to connect within 15 seconds",
            }

        except requests.exceptions.ConnectionError as e:
            return {
                "success": False,
                "message": f"Connection failed: {str(e)}",
            }

        except requests.exceptions.HTTPError as e:
            return {
                "success": False,
                "message": f"HTTP error: {str(e)}",
            }

        except Exception as e:
            logger.error(f"Feishu connection test failed: {e}")
            return {
                "success": False,
                "message": f"Connection test failed: {str(e)}",
            }

    def delete_config(self) -> bool:
        """
        Delete Feishu configuration from config.json.

        Returns:
            True if successful.
        """
        with _config_lock:
            config = self._read_config_file()

            if "feishu" not in config:
                return False

            # Backup before deleting
            self._backup_config()

            # Remove feishu config
            del config["feishu"]

            # Write back
            self._write_config_file(config)

            # Invalidate cache
            self._invalidate_cache()

            logger.info("Feishu configuration deleted")

            return True

    def _read_config_file(self) -> dict[str, Any]:
        """Read config.json file."""
        if not os.path.exists(self.config_path):
            return {}

        try:
            with open(self.config_path, encoding="utf-8") as f:
                return json.load(f)
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in config.json: {e}")
            raise RuntimeError(f"Invalid JSON in config.json: {e}")
        except Exception as e:
            logger.error(f"Failed to read config.json: {e}")
            raise RuntimeError(f"Failed to read config.json: {e}")

    def _write_config_file(self, config: dict[str, Any]) -> None:
        """Write config.json file with proper formatting."""
        try:
            # Ensure directory exists
            os.makedirs(self.config_dir, exist_ok=True)

            # Write to temporary file first
            temp_path = self.config_path + ".tmp"
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(config, f, indent=2, ensure_ascii=False)
                f.write("\n")  # Add trailing newline

            # Atomic rename
            shutil.move(temp_path, self.config_path)

        except Exception as e:
            logger.error(f"Failed to write config.json: {e}")
            raise RuntimeError(f"Failed to write config.json: {e}")

    def _backup_config(self) -> None:
        """Backup config.json to config.json.bak."""
        if os.path.exists(self.config_path):
            try:
                shutil.copy2(self.config_path, self.backup_path)
            except Exception as e:
                logger.warning(f"Failed to backup config.json: {e}")

    def _invalidate_cache(self) -> None:
        """Invalidate config cache."""
        # Import here to avoid circular dependency
        from app.utils.config import _cache, _cache_lock

        with _cache_lock:
            _cache.pop("_root", None)


# Global service instance
_feishu_config_service: FeishuConfigService | None = None


def get_feishu_config_service() -> FeishuConfigService:
    """Get the global Feishu config service instance."""
    global _feishu_config_service
    if _feishu_config_service is None:
        _feishu_config_service = FeishuConfigService()
    return _feishu_config_service
