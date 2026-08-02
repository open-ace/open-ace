"""
Open ACE - Usage Diagnostics Module

Diagnostic recording for usage parsing errors.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# Prometheus metrics (will be initialized if prometheus_client is available)
_prometheus_available = False
_usage_parse_errors_counter = None

try:
    from prometheus_client import Counter

    _prometheus_available = True
    _usage_parse_errors_counter = Counter(
        'llm_proxy_usage_parse_errors_total',
        'Total number of usage parsing errors',
        ['provider', 'parse_status']
    )
except ImportError:
    pass


@dataclass
class UsageParseDiagnostic:
    """Usage parsing diagnostic information.

    Captures detailed information about usage parsing failures
    for debugging and auditing purposes.
    """

    provider: str
    protocol_type: str
    parse_status: str  # "success" | "missing" | "malformed" | "unsupported" | "partial"
    error_message: str | None = None
    raw_usage_snippet: str | None = None  # First 200 chars for debugging
    request_id: str | None = None
    session_id: str | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_log_dict(self) -> dict[str, Any]:
        """Convert to dictionary for logging."""
        return {
            "provider": self.provider,
            "protocol_type": self.protocol_type,
            "parse_status": self.parse_status,
            "error_message": self.error_message,
            "raw_usage_snippet": self.raw_usage_snippet,
            "request_id": self.request_id,
            "session_id": self.session_id,
            "timestamp": self.timestamp.isoformat(),
        }


def record_parse_diagnostic(diagnostic: UsageParseDiagnostic) -> None:
    """Record a usage parsing diagnostic.

    Logs the diagnostic and updates Prometheus metrics.

    Args:
        diagnostic: Diagnostic information to record.
    """
    # Log the diagnostic
    log_level = logging.WARNING if diagnostic.parse_status in ("missing", "malformed") else logging.INFO

    logger.log(
        log_level,
        f"Usage parse {diagnostic.parse_status}",
        extra=diagnostic.to_log_dict()
    )

    # Update Prometheus metrics
    if _prometheus_available and _usage_parse_errors_counter is not None:
        try:
            _usage_parse_errors_counter.labels(
                provider=diagnostic.provider,
                parse_status=diagnostic.parse_status
            ).inc()
        except Exception as e:
            logger.debug(f"Failed to update Prometheus counter: {e}")


def create_missing_usage_diagnostic(
    provider: str,
    protocol_type: str,
    session_id: str | None = None,
    request_id: str | None = None,
    raw_content: bytes | None = None
) -> UsageParseDiagnostic:
    """Create diagnostic for missing usage.

    Args:
        provider: Provider identifier.
        protocol_type: Protocol type.
        session_id: Optional session identifier.
        request_id: Optional request identifier.
        raw_content: Optional raw response content for snippet.

    Returns:
        UsageParseDiagnostic instance.
    """
    raw_snippet = None
    if raw_content:
        try:
            raw_str = raw_content.decode('utf-8', errors='replace')
            raw_snippet = raw_str[:200]
        except Exception:
            pass

    return UsageParseDiagnostic(
        provider=provider,
        protocol_type=protocol_type,
        parse_status="missing",
        error_message="No usage field found in response",
        raw_usage_snippet=raw_snippet,
        request_id=request_id,
        session_id=session_id,
    )


def create_malformed_usage_diagnostic(
    provider: str,
    protocol_type: str,
    error_message: str,
    session_id: str | None = None,
    request_id: str | None = None,
    raw_content: bytes | None = None
) -> UsageParseDiagnostic:
    """Create diagnostic for malformed usage.

    Args:
        provider: Provider identifier.
        protocol_type: Protocol type.
        error_message: Error description.
        session_id: Optional session identifier.
        request_id: Optional request identifier.
        raw_content: Optional raw response content for snippet.

    Returns:
        UsageParseDiagnostic instance.
    """
    raw_snippet = None
    if raw_content:
        try:
            raw_str = raw_content.decode('utf-8', errors='replace')
            raw_snippet = raw_str[:200]
        except Exception:
            pass

    return UsageParseDiagnostic(
        provider=provider,
        protocol_type=protocol_type,
        parse_status="malformed",
        error_message=error_message,
        raw_usage_snippet=raw_snippet,
        request_id=request_id,
        session_id=session_id,
    )


def get_prometheus_counter():
    """Get Prometheus counter for usage parse errors.

    Returns:
        Counter instance or None if Prometheus not available.
    """
    return _usage_parse_errors_counter if _prometheus_available else None