"""
Open ACE - Run Timeline audit bridge.

Maps important run events to generic ``audit_logs`` entries for compliance
export. Kept inside this self-contained module so the bridge never pollutes
existing code; ``DbRunRecorder`` calls into it when recording a mapped event.

Only lifecycle/security-relevant events are bridged (permissions, lifecycle
transitions, errors). High-volume events (usage, assistant output) are not
mirrored to audit_logs to avoid write amplification — they remain queryable
through the dedicated timeline API.
"""

from __future__ import annotations


import logging
from typing import Any

logger = logging.getLogger(__name__)

# event_type -> (audit action, severity)
EVENT_AUDIT_MAP: dict[str, tuple[str, str]] = {
    "session_created": ("agent_session_create", "info"),
    "permission_requested": ("agent_permission_request", "info"),
    "permission_answered": ("agent_permission_response", "info"),
    "policy_decision": ("agent_policy_decision", "info"),
    "stop": ("agent_session_stop", "info"),
    "error": ("agent_session_error", "error"),
    "request_aborted": ("agent_request_abort", "warning"),
}


def maybe_log_audit(
    event_type: str,
    run_id: str,
    user_id: int | None,
    username: str | None = None,
    session_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    """Forward a mapped event to audit_logs. Best-effort, never raises."""
    mapping = EVENT_AUDIT_MAP.get(event_type)
    if mapping is None:
        return
    action, severity = mapping
    try:
        from app.modules.governance.audit_logger import AuditLogger

        AuditLogger().log(
            action=action,
            user_id=user_id,
            username=username,
            severity=severity,
            resource_type="agent_run",
            resource_id=run_id,
            session_id=session_id,
            details=details,
        )
    except Exception as e:  # pragma: no cover - defensive, audit must never break flow
        logger.debug("audit bridge skipped %s: %s", event_type, e)


__all__ = ["maybe_log_audit", "EVENT_AUDIT_MAP"]
