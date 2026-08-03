"""
Open ACE - Usage Diagnostics Module

Structured logging and metrics for usage parsing issues.
Issue #2184: Multi-provider usage recording with diagnostics.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class UsageDiagnostics:
    """Container for usage parsing diagnostics."""

    session_id: str = ""
    provider: str = ""
    protocol: str = ""
    parse_status: str = ""
    error_type: str = ""
    error_message: str = ""
    chunks_seen: int = 0
    usage_events_seen: int = 0
    request_id: str | None = None
    model: str | None = None
    raw_usage_preview: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for logging."""
        return {
            "session_id": self.session_id,
            "provider": self.provider,
            "protocol": self.protocol,
            "parse_status": self.parse_status,
            "error_type": self.error_type,
            "error_message": self.error_message,
            "chunks_seen": self.chunks_seen,
            "usage_events_seen": self.usage_events_seen,
            "request_id": self.request_id,
            "model": self.model,
            "raw_usage_preview": self.raw_usage_preview,
        }


def log_usage_parse_failure(
    diagnostics: UsageDiagnostics,
) -> None:
    """Log a usage parse failure.

    Args:
        diagnostics: Diagnostic information.
    """
    logger.warning(
        "usage_parse_failure",
        extra={
            "session_id": diagnostics.session_id,
            "provider": diagnostics.provider,
            "protocol": diagnostics.protocol,
            "parse_status": diagnostics.parse_status,
            "error_type": diagnostics.error_type,
            "error_message": diagnostics.error_message,
            "request_id": diagnostics.request_id,
            "model": diagnostics.model,
        },
    )


def log_usage_missing(
    session_id: str,
    provider: str,
    protocol: str,
    request_id: str | None = None,
    chunks_seen: int = 0,
) -> None:
    """Log missing usage event.

    Args:
        session_id: Session identifier.
        provider: Provider identifier.
        protocol: Protocol identifier.
        request_id: Request identifier.
        chunks_seen: Number of chunks processed.
    """
    logger.info(
        "usage_missing",
        extra={
            "session_id": session_id,
            "provider": provider,
            "protocol": protocol,
            "request_id": request_id,
            "chunks_seen": chunks_seen,
        },
    )


def log_usage_malformed(
    session_id: str,
    provider: str,
    protocol: str,
    raw_usage: dict[str, Any] | None,
    error_message: str | None = None,
    request_id: str | None = None,
) -> None:
    """Log malformed usage event.

    Args:
        session_id: Session identifier.
        provider: Provider identifier.
        protocol: Protocol identifier.
        raw_usage: Raw usage dictionary (if available).
        error_message: Error message.
        request_id: Request identifier.
    """
    import json

    raw_usage_preview = ""
    if raw_usage:
        try:
            raw_usage_preview = json.dumps(raw_usage)[:200]
        except Exception:
            raw_usage_preview = str(raw_usage)[:200]

    logger.info(
        "usage_malformed",
        extra={
            "session_id": session_id,
            "provider": provider,
            "protocol": protocol,
            "request_id": request_id,
            "error_message": error_message,
            "raw_usage_preview": raw_usage_preview,
        },
    )


def log_usage_indeterminate(
    session_id: str,
    provider: str,
    protocol: str,
    reason: str,
    request_id: str | None = None,
    partial_usage: dict[str, Any] | None = None,
) -> None:
    """Log indeterminate usage (could not determine final usage).

    Args:
        session_id: Session identifier.
        provider: Provider identifier.
        protocol: Protocol identifier.
        reason: Reason for indeterminate status.
        request_id: Request identifier.
        partial_usage: Partial usage that was collected.
    """
    logger.info(
        "usage_indeterminate",
        extra={
            "session_id": session_id,
            "provider": provider,
            "protocol": protocol,
            "request_id": request_id,
            "reason": reason,
            "partial_usage": partial_usage,
        },
    )


def log_usage_duplicate_detected(
    session_id: str,
    provider: str,
    request_id: str | None = None,
    is_strict_match: bool = True,
) -> None:
    """Log duplicate usage detection.

    Args:
        session_id: Session identifier.
        provider: Provider identifier.
        request_id: Request identifier.
        is_strict_match: True if matched by request_id, False if by composite key.
    """
    logger.info(
        "usage_duplicate_detected",
        extra={
            "session_id": session_id,
            "provider": provider,
            "request_id": request_id,
            "match_type": "strict" if is_strict_match else "composite",
        },
    )


# Prometheus-style metrics (simple counters for now)
_METRICS: dict[str, dict[str, int]] = {
    "llm_proxy_usage_parse_errors_total": {},
    "llm_proxy_usage_events_total": {},
    "llm_proxy_usage_dedup_hits_total": {},
}


def increment_metric(metric_name: str, labels: dict[str, str]) -> None:
    """Increment a counter metric.

    Args:
        metric_name: Name of the metric.
        labels: Label key-value pairs.
    """
    import hashlib

    if metric_name not in _METRICS:
        return

    # Create a stable key from labels
    label_str = "|".join(f"{k}={v}" for k, v in sorted(labels.items()))
    label_key = hashlib.md5(label_str.encode()).hexdigest()

    if label_key not in _METRICS[metric_name]:
        _METRICS[metric_name][label_key] = 0

    _METRICS[metric_name][label_key] += 1


def get_metrics() -> dict[str, dict[str, int]]:
    """Get current metrics.

    Returns:
        Dictionary of metrics.
    """
    return dict(_METRICS)


def reset_metrics_for_tests() -> None:
    """Reset metrics for tests."""
    for metric in _METRICS.values():
        metric.clear()