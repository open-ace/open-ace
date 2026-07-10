"""
Open ACE - Model Gateway Configuration API Routes

REST API endpoints for the optional LiteLLM-compatible model gateway:
- Get / save / delete gateway configuration
- Test gateway connection
- Enable / disable gateway routing

Admin-only access. This file is the admin route registration for the removable
model_gateway module; deleting it (plus unregistering the blueprint) is part of
the feature's removal checklist.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from typing import Any

import fcntl
from flask import Blueprint, g, jsonify, request

from app.auth.decorators import admin_required
from app.modules.workspace.model_gateway import config as gateway_config
from app.modules.workspace.model_gateway.audit import (
    log_config_change,
    log_config_error,
    log_config_recovery,
)
from app.modules.workspace.model_gateway.service import get_gateway_service
from app.utils.config_backup import (
    backup_config,
    check_and_recover_corrupted_config,
    cleanup_old_temp_files,
    get_backup_versions,
    restore_config,
)
from app.utils.gateway_validation import (
    ValidationError,
    mask_api_key,
    validate_api_key,
    validate_base_url,
    validate_enabled_value,
    validate_model_prefix,
)
from app.utils.config import invalidate_config_cache, is_model_gateway_enabled

logger = logging.getLogger(__name__)

model_gateway_bp = Blueprint("model_gateway", __name__)

# File lock timeout in seconds
FILE_LOCK_TIMEOUT = 5.0


@model_gateway_bp.before_request
@admin_required
def check_admin():
    """Ensure user is admin before each request."""
    pass


# ==================== Helper Functions ====================


def _get_config_path() -> str:
    """Get the path to config.json."""
    from app.repositories.database import CONFIG_DIR

    return os.path.join(CONFIG_DIR, "config.json")


def _detect_env_override() -> tuple[bool, bool, str]:
    """Detect if environment variables override config.

    Returns:
        Tuple of (env_override, env_config_complete, config_source).
    """
    env_mode = os.environ.get("OPENACE_MODEL_GATEWAY_MODE", "").strip().lower()
    env_base_url = os.environ.get("OPENACE_MODEL_GATEWAY_BASE_URL", "").strip()
    env_api_key = os.environ.get("OPENACE_MODEL_GATEWAY_API_KEY", "").strip()

    # Check if MODE env var forces gateway mode
    env_override = env_mode == "gateway"

    # Check if env provides complete config
    env_config_complete = bool(env_base_url and env_api_key)

    # Determine config source
    if env_override:
        config_source = "env"
    elif env_config_complete:
        config_source = "env"
    else:
        config_source = "database"

    return env_override, env_config_complete, config_source


def _check_config_complete() -> tuple[bool, list[str]]:
    """Check if gateway configuration is complete.

    Returns:
        Tuple of (is_complete, missing_fields).
    """
    missing_fields = []

    try:
        # Check environment variables first
        env_base_url = os.environ.get("OPENACE_MODEL_GATEWAY_BASE_URL", "").strip()
        env_api_key = os.environ.get("OPENACE_MODEL_GATEWAY_API_KEY", "").strip()

        if env_base_url and env_api_key:
            # Validate env config
            try:
                validate_base_url(env_base_url)
                validate_api_key(env_api_key)
                return True, []
            except ValidationError:
                pass

        # Check database config
        config = get_gateway_service().get_config_with_key()
        if config is None:
            missing_fields.extend(["base_url", "api_key"])
        else:
            if not config.base_url:
                missing_fields.append("base_url")
            if not config.api_key:
                missing_fields.append("api_key")

        return len(missing_fields) == 0, missing_fields

    except Exception as e:
        logger.error("Error checking config completeness: %s", e)
        return False, ["base_url", "api_key"]


def _read_config_with_version() -> tuple[dict[str, Any], int]:
    """Read config.json and return with version number.

    Returns:
        Tuple of (config_dict, version_number).
    """
    config_path = _get_config_path()

    # Check for corruption and recover if needed
    config = check_and_recover_corrupted_config(config_path)
    if config is None:
        config = {}

    # Get or create version
    version = config.get("version", 0)

    return config, version


def _write_config_with_lock(
    config: dict[str, Any],
    user_id: int | None,
    expected_version: int | None = None,
) -> tuple[bool, int, str]:
    """Write config.json with file locking and atomic write.

    Args:
        config: The configuration dict to write.
        user_id: ID of the user making the change.
        expected_version: Expected current version for optimistic locking.

    Returns:
        Tuple of (success, new_version, error_message).
    """
    config_path = _get_config_path()
    config_dir = os.path.dirname(config_path) or "."

    # Clean up old temp files
    cleanup_old_temp_files(config_path)

    # Create config directory if it doesn't exist
    if config_dir and not os.path.exists(config_dir):
        try:
            os.makedirs(config_dir, exist_ok=True)
        except OSError as e:
            return False, 0, f"Failed to create config directory: {e}"

    lock_file = None
    try:
        # Open/create the config file
        if os.path.exists(config_path):
            lock_file = open(config_path, "r+")
        else:
            lock_file = open(config_path, "w+")

        # Acquire exclusive lock with timeout
        start_time = time.time()
        while True:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except (IOError, OSError):
                if time.time() - start_time >= FILE_LOCK_TIMEOUT:
                    return False, 0, "Lock acquisition timeout"
                time.sleep(0.1)

        # Read current config if file exists
        if os.path.exists(config_path) and os.path.getsize(config_path) > 0:
            lock_file.seek(0)
            try:
                current_config = json.load(lock_file)
            except json.JSONDecodeError:
                current_config = {}
        else:
            current_config = {}

        # Check version for optimistic locking
        current_version = current_config.get("version", 0)
        if expected_version is not None and current_version != expected_version:
            return False, 0, f"Version conflict: expected {expected_version}, got {current_version}"

        # Backup current config
        if current_config:
            backup_config(config_path)

        # Merge configs and increment version
        new_config = dict(current_config)
        new_config.update(config)
        new_config["version"] = current_version + 1
        new_version = new_config["version"]

        # Write to temporary file
        temp_fd, temp_path = tempfile.mkstemp(dir=config_dir, suffix=".tmp")
        try:
            with os.fdopen(temp_fd, "w") as temp_file:
                json.dump(new_config, temp_file, indent=2)

            # Verify the temp file is valid JSON
            with open(temp_path) as f:
                json.load(f)

            # Atomic rename
            os.rename(temp_path, config_path)

            # Invalidate cache
            invalidate_config_cache()

            return True, new_version, ""

        except Exception as e:
            # Clean up temp file on error
            if os.path.exists(temp_path):
                os.unlink(temp_path)
            return False, 0, f"Write failed: {e}"

    except PermissionError as e:
        return False, 0, f"Permission denied: {e}"
    except Exception as e:
        return False, 0, f"Unexpected error: {e}"
    finally:
        if lock_file:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
                lock_file.close()
            except Exception:
                pass


# ==================== Model Gateway Configuration API ====================


@model_gateway_bp.route("/management/model-gateway-config", methods=["GET"])
def get_model_gateway_config():
    """Get the model gateway configuration with enhanced status information.

    Returns complete configuration including:
    - enabled: Current enabled state
    - env_override: Whether environment variables override
    - config_complete: Whether configuration is complete
    - missing_fields: List of missing required fields
    - config_source: Where config comes from (env/database)
    - version: Configuration version number
    """
    try:
        user_id = g.user.get("id") if hasattr(g, "user") and g.user else None

        # Get enabled state
        enabled = is_model_gateway_enabled()

        # Detect environment variable override
        env_override, env_config_complete, config_source = _detect_env_override()

        # Check configuration completeness
        config_complete, missing_fields = _check_config_complete()

        # Get database configuration
        db_config = get_gateway_service().get_config()

        # Read config version
        config_dict, version = _read_config_with_version()

        # Build response
        response_data = {
            "enabled": enabled,
            "env_override": env_override,
            "env_config_complete": env_config_complete,
            "db_config_complete": db_config is not None,
            "config_complete": config_complete,
            "config_source": config_source,
            "missing_fields": missing_fields,
            "version": version,
        }

        # Add database config if available
        if db_config:
            response_data.update(
                {
                    "mode": db_config.get("mode", "direct"),
                    "base_url": db_config.get("base_url"),
                    "api_key_masked": mask_api_key(
                        db_config.get("api_key_masked", "")
                    ),
                    "model_prefix_mode": db_config.get("model_prefix_mode", False),
                    "model_prefix": db_config.get("model_prefix"),
                }
            )
        else:
            # Check environment variables for base_url
            env_base_url = os.environ.get("OPENACE_MODEL_GATEWAY_BASE_URL", "").strip()
            if env_base_url:
                response_data["base_url"] = env_base_url
                response_data["api_key_masked"] = "***"
                response_data["mode"] = "gateway" if env_override else "direct"
            else:
                response_data["mode"] = "direct"
                response_data["base_url"] = None
                response_data["api_key_masked"] = None
                response_data["model_prefix_mode"] = False
                response_data["model_prefix"] = None

        return jsonify({"success": True, "data": response_data})

    except Exception as e:
        logger.error("Error getting model gateway config: %s", e)
        return jsonify({"success": False, "error": "Internal server error"}), 500


@model_gateway_bp.route("/management/model-gateway-config", methods=["PUT"])
def update_model_gateway_config():
    """Save (replace) the model gateway configuration."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "No data provided"}), 400

        user_id = g.user.get("id") if hasattr(g, "user") and g.user else None

        # Validate inputs
        base_url = data.get("base_url")
        api_key = data.get("api_key")

        try:
            if base_url:
                base_url = validate_base_url(base_url)
            if api_key:
                api_key = validate_api_key(api_key)
        except ValidationError as e:
            log_config_error(user_id, "validation", str(e))
            return jsonify({"success": False, "error": str(e)}), 400

        if not base_url:
            return jsonify({"success": False, "error": "Missing required field: base_url"}), 400

        model_prefix_mode = bool(data.get("model_prefix_mode", False))
        model_prefix = data.get("model_prefix") or None

        # Validate model_prefix if provided
        if model_prefix:
            try:
                model_prefix = validate_model_prefix(model_prefix)
            except ValidationError as e:
                log_config_error(user_id, "validation", str(e))
                return jsonify({"success": False, "error": str(e)}), 400

        config = get_gateway_service().save_config(
            base_url=base_url,
            api_key=api_key,
            model_prefix_mode=model_prefix_mode,
            model_prefix=model_prefix,
            created_by=user_id,
        )

        # Log the change
        log_config_change(
            user_id=user_id,
            action="update",
            before=None,
            after={"base_url": base_url, "model_prefix_mode": model_prefix_mode},
            result="success",
        )

        logger.info("Model gateway configuration updated by user %s", user_id)
        return jsonify(
            {
                "success": True,
                "data": config,
                "message": "Model gateway configuration saved.",
            }
        )

    except ValueError as e:
        user_id = g.user.get("id") if hasattr(g, "user") and g.user else None
        log_config_error(user_id, "validation", str(e))
        return jsonify({"success": False, "error": str(e)}), 400
    except Exception as e:
        user_id = g.user.get("id") if hasattr(g, "user") and g.user else None
        log_config_error(user_id, "internal", str(e))
        logger.error("Error updating model gateway config: %s", e)
        return jsonify({"success": False, "error": "Internal server error"}), 500


@model_gateway_bp.route("/management/model-gateway-config/enabled", methods=["PUT"])
def set_model_gateway_enabled():
    """Enable or disable model gateway routing.

    Requires:
    - enabled: boolean value
    - version: current config version (for optimistic locking)

    Validates:
    - Environment variable override detection
    - Configuration completeness before enabling

    Returns:
    - success: boolean
    - enabled: new enabled state
    - version: new config version
    - message: result message
    - effective_time: when changes take effect (up to 10 seconds)
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "No data provided"}), 400

        user_id = g.user.get("id") if hasattr(g, "user") and g.user else None

        # Validate enabled value
        enabled = data.get("enabled")
        try:
            enabled = validate_enabled_value(enabled)
        except ValidationError as e:
            log_config_error(user_id, "validation", str(e))
            return jsonify({"success": False, "error": str(e)}), 400

        # Get expected version for optimistic locking
        expected_version = data.get("version")
        if expected_version is not None and not isinstance(expected_version, int):
            return jsonify({"success": False, "error": "version must be an integer"}), 400

        # Check for environment variable override
        env_override, _, _ = _detect_env_override()
        if env_override:
            log_config_error(
                user_id,
                "env_override",
                "Cannot modify enabled state when environment variable is set",
            )
            return jsonify(
                {
                    "success": False,
                    "error": "Environment variable OPENACE_MODEL_GATEWAY_MODE is set, cannot modify through UI",
                    "error_code": "env_override",
                }
            ), 400

        # If enabling, check configuration completeness
        if enabled:
            config_complete, missing_fields = _check_config_complete()
            if not config_complete:
                log_config_error(
                    user_id,
                    "config_incomplete",
                    f"Missing required fields: {missing_fields}",
                )
                return jsonify(
                    {
                        "success": False,
                        "error": f"Configuration incomplete. Missing fields: {', '.join(missing_fields)}",
                        "error_code": "config_incomplete",
                        "missing_fields": missing_fields,
                    }
                ), 400

        # Read current config
        current_config, current_version = _read_config_with_version()

        # Check version conflict
        if expected_version is not None and current_version != expected_version:
            log_config_error(
                user_id,
                "version_conflict",
                f"Expected version {expected_version}, got {current_version}",
            )
            return jsonify(
                {
                    "success": False,
                    "error": "Configuration has been modified by another user. Please refresh and try again.",
                    "error_code": "version_conflict",
                    "current_version": current_version,
                }
            ), 409

        # Update the enabled field
        model_gateway_config = current_config.get("model_gateway", {})
        model_gateway_config["enabled"] = enabled
        update_config = {"model_gateway": model_gateway_config}

        # Write with lock
        success, new_version, error_msg = _write_config_with_lock(
            update_config, user_id, expected_version=current_version
        )

        if not success:
            log_config_error(user_id, "write_failed", error_msg)
            return jsonify({"success": False, "error": error_msg}), 500

        # Log the change
        log_config_change(
            user_id=user_id,
            action="enable" if enabled else "disable",
            before={"enabled": current_config.get("model_gateway", {}).get("enabled", False)},
            after={"enabled": enabled},
            result="success",
            version=new_version,
        )

        logger.info(
            "Model gateway %s by user %s (version %d)",
            "enabled" if enabled else "disabled",
            user_id,
            new_version,
        )

        return jsonify(
            {
                "success": True,
                "enabled": enabled,
                "version": new_version,
                "message": f"Model gateway routing {'enabled' if enabled else 'disabled'} successfully.",
                "effective_time": "Changes will take effect within 10 seconds.",
            }
        )

    except Exception as e:
        user_id = g.user.get("id") if hasattr(g, "user") and g.user else None
        log_config_error(user_id, "internal", str(e))
        logger.error("Error setting model gateway enabled state: %s", e)
        return jsonify({"success": False, "error": "Internal server error"}), 500


@model_gateway_bp.route("/management/model-gateway-config/rollback", methods=["POST"])
def rollback_model_gateway_config():
    """Rollback configuration to a previous backup version.

    Args:
        backup_index: Which backup to restore (1 = most recent, default=1)

    Returns:
        The restored configuration.
    """
    try:
        data = request.get_json() or {}
        backup_index = data.get("backup_index", 1)

        user_id = g.user.get("id") if hasattr(g, "user") and g.user else None

        config_path = _get_config_path()

        # Restore from backup
        restored_config = restore_config(config_path, backup_index)

        if restored_config is None:
            return jsonify(
                {
                    "success": False,
                    "error": f"Failed to restore from backup {backup_index}",
                }
            ), 500

        # Invalidate cache
        invalidate_config_cache()

        # Log the recovery
        log_config_recovery(backup_version=backup_index, result="success")

        logger.info(
            "Model gateway config rolled back to backup %d by user %s",
            backup_index,
            user_id,
        )

        return jsonify(
            {
                "success": True,
                "data": restored_config,
                "message": f"Configuration restored from backup {backup_index}",
            }
        )

    except Exception as e:
        user_id = g.user.get("id") if hasattr(g, "user") and g.user else None
        log_config_error(user_id, "rollback_failed", str(e))
        logger.error("Error rolling back model gateway config: %s", e)
        return jsonify({"success": False, "error": "Internal server error"}), 500


@model_gateway_bp.route("/management/model-gateway-config/backups", methods=["GET"])
def get_model_gateway_config_backups():
    """Get list of available backup versions."""
    try:
        config_path = _get_config_path()
        backups = get_backup_versions(config_path)

        return jsonify(
            {
                "success": True,
                "data": backups,
            }
        )

    except Exception as e:
        logger.error("Error getting backup versions: %s", e)
        return jsonify({"success": False, "error": "Internal server error"}), 500


@model_gateway_bp.route("/management/model-gateway-config", methods=["DELETE"])
def delete_model_gateway_config():
    """Delete the model gateway configuration."""
    try:
        user_id = g.user.get("id") if hasattr(g, "user") and g.user else None

        deleted = get_gateway_service().delete_config()

        # Log the deletion
        log_config_change(
            user_id=user_id,
            action="delete",
            result="success" if deleted else "no_change",
        )

        return jsonify(
            {
                "success": True,
                "deleted": deleted,
                "message": (
                    "Model gateway configuration deleted." if deleted else "No config to delete."
                ),
            }
        )
    except Exception as e:
        user_id = g.user.get("id") if hasattr(g, "user") and g.user else None
        log_config_error(user_id, "delete_failed", str(e))
        logger.error("Error deleting model gateway config: %s", e)
        return jsonify({"success": False, "error": "Internal server error"}), 500


@model_gateway_bp.route("/management/model-gateway-config/test", methods=["POST"])
def test_model_gateway_connection():
    """Test the gateway connection with supplied (or stored) credentials."""
    try:
        data = request.get_json() or {}
        base_url = data.get("base_url")
        api_key = data.get("api_key")

        # Validate inputs if provided
        if base_url:
            try:
                base_url = validate_base_url(base_url)
            except ValidationError as e:
                return jsonify({"success": False, "error": str(e)}), 400

        # Fall back to stored config when the caller omits credentials.
        if not base_url or not api_key:
            stored = get_gateway_service().get_config_with_key()
            if stored is not None:
                base_url = base_url or stored.base_url
                api_key = api_key or stored.api_key

        result = get_gateway_service().test_connection(
            base_url=base_url or "", api_key=api_key or ""
        )
        return jsonify({"success": True, "data": result})
    except Exception as e:
        logger.error("Error testing model gateway connection: %s", e)
        return jsonify({"success": False, "error": "Internal server error"}), 500