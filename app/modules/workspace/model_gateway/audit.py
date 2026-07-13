"""
Model Gateway Audit Logging.

Records all configuration changes, errors, and recovery operations
for the model gateway feature.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)


def log_config_change(
    user_id: int | None,
    action: str,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    result: str = "success",
    error_msg: str | None = None,
    version: int | None = None,
) -> None:
    """Log a model gateway configuration change.

    Args:
        user_id: ID of the user making the change.
        action: The action performed (enable, disable, update, delete).
        before: Configuration state before the change.
        after: Configuration state after the change.
        result: Result of the operation (success, failure).
        error_msg: Error message if the operation failed.
        version: Configuration version number.
    """
    audit_data: dict[str, str | int | None | dict[str, Any]] = {
        "event_type": "model_gateway_config_change",
        "user_id": user_id,
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "action": action,
        "result": result,
    }

    if before is not None:
        # Mask sensitive information in before state
        safe_before = dict(before)
        if "api_key" in safe_before:
            safe_before["api_key"] = "***MASKED***"
        audit_data["before"] = safe_before

    if after is not None:
        # Mask sensitive information in after state
        safe_after = dict(after)
        if "api_key" in safe_after:
            safe_after["api_key"] = "***MASKED***"
        audit_data["after"] = safe_after

    if error_msg:
        audit_data["error_msg"] = error_msg

    if version is not None:
        audit_data["version"] = version

    # Log to application log
    logger.info(
        "Model gateway config change: user=%s action=%s result=%s",
        user_id,
        action,
        result,
        extra={"audit_data": audit_data},
    )

    # Try to write to audit log database if available
    try:
        from app.repositories.audit_log_repo import AuditLogRepo

        repo = AuditLogRepo()
        repo.add_log(
            event_type="model_gateway_config_change",
            user_id=user_id,
            details=json.dumps(audit_data),
        )
    except Exception as e:
        # If audit log repo is not available, just log to application log
        logger.debug("Could not write to audit log database: %s", e)


def log_config_error(
    user_id: int | None,
    error_type: str,
    error_msg: str,
    details: dict[str, Any] | None = None,
) -> None:
    """Log a model gateway configuration error.

    Args:
        user_id: ID of the user who encountered the error.
        error_type: Type of error (validation, permission, file_error, etc.).
        error_msg: Detailed error message.
        details: Additional error details.
    """
    audit_data: dict[str, str | int | None | dict[str, Any]] = {
        "event_type": "model_gateway_config_error",
        "user_id": user_id,
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "error_type": error_type,
        "error_msg": error_msg,
    }

    if details:
        # Mask sensitive information in details
        safe_details = dict(details)
        if "api_key" in safe_details:
            safe_details["api_key"] = "***MASKED***"
        audit_data["details"] = safe_details

    logger.error(
        "Model gateway config error: user=%s type=%s msg=%s",
        user_id,
        error_type,
        error_msg,
        extra={"audit_data": audit_data},
    )

    # Try to write to audit log database
    try:
        from app.repositories.audit_log_repo import AuditLogRepo

        repo = AuditLogRepo()
        repo.add_log(
            event_type="model_gateway_config_error",
            user_id=user_id,
            details=json.dumps(audit_data),
        )
    except Exception as e:
        logger.debug("Could not write to audit log database: %s", e)


def log_config_recovery(
    backup_version: int,
    result: str = "success",
    error_msg: str | None = None,
) -> None:
    """Log a configuration file recovery operation.

    Args:
        backup_version: The backup version number that was restored.
        result: Result of the recovery (success, failure).
        error_msg: Error message if recovery failed.
    """
    audit_data = {
        "event_type": "model_gateway_config_recovery",
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "backup_version": backup_version,
        "result": result,
    }

    if error_msg:
        audit_data["error_msg"] = error_msg

    logger.warning(
        "Model gateway config recovery: backup_version=%s result=%s",
        backup_version,
        result,
        extra={"audit_data": audit_data},
    )

    # Try to write to audit log database
    try:
        from app.repositories.audit_log_repo import AuditLogRepo

        repo = AuditLogRepo()
        repo.add_log(
            event_type="model_gateway_config_recovery",
            user_id=None,  # System operation
            details=json.dumps(audit_data),
        )
    except Exception as e:
        logger.debug("Could not write to audit log database: %s", e)
