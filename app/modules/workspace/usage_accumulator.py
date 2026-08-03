"""
Open ACE - Usage Accumulator Module

State machine for accumulating usage from streaming responses.
Issue #2184: Multi-provider usage recording with cache token support.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from app.modules.workspace.usage_evidence import UsageEvidence

logger = logging.getLogger(__name__)


class AccumulatorState(Enum):
    """State machine states for usage accumulation."""

    WAITING_START = "waiting_start"  # Waiting for first usage event
    COLLECTING = "collecting"  # Collecting usage events
    FINALIZED = "finalized"  # Received final usage
    ERROR = "error"  # Error occurred, use partial usage


@dataclass
class UsageAccumulator:
    """Accumulator for collecting usage from streaming responses.

    Each streaming request should create its own instance to avoid
    cross-request pollution.

    State machine:
        WAITING_START → COLLECTING (on first usage event)
        COLLECTING → FINALIZED (on is_final=True or explicit finalize)
        Any state → ERROR (on exception)

    Thread-safe: uses threading.Lock for state transitions.
    """

    session_id: str
    provider: str
    protocol: str
    request_id: str | None = None
    model: str | None = None
    user_id: int = 0
    tenant_id: int = 0

    # Internal state
    _state: AccumulatorState = field(default=AccumulatorState.WAITING_START, repr=False)
    _accumulated: UsageEvidence | None = field(default=None, repr=False)
    _chunks_seen: int = field(default=0, repr=False)
    _usage_events_seen: int = field(default=0, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _error_message: str | None = field(default=None, repr=False)

    def collect(self, evidence: UsageEvidence) -> bool:
        """Collect a usage event.

        Args:
            evidence: Usage evidence from parser.

        Returns:
            True if collection should continue, False if finalized.
        """
        with self._lock:
            self._chunks_seen += 1

            if evidence is None:
                return self._state not in (AccumulatorState.FINALIZED, AccumulatorState.ERROR)

            self._usage_events_seen += 1

            # Update model if provided
            if evidence.model:
                self.model = evidence.model

            # State transitions
            if self._state == AccumulatorState.WAITING_START:
                self._state = AccumulatorState.COLLECTING
                self._accumulated = evidence

            elif self._state == AccumulatorState.COLLECTING:
                # Merge with accumulated
                if self._accumulated:
                    self._accumulated = self._accumulated.merge_with(evidence)
                else:
                    self._accumulated = evidence

            # Check for finalization
            if evidence.is_final:
                self._state = AccumulatorState.FINALIZED
                return False

            return True

    def mark_error(self, error_message: str | None = None) -> None:
        """Mark the accumulator as errored.

        Args:
            error_message: Optional error message for diagnostics.
        """
        with self._lock:
            self._state = AccumulatorState.ERROR
            self._error_message = error_message

    def finalize(self) -> UsageEvidence | None:
        """Finalize and return the accumulated usage.

        Returns:
            Final UsageEvidence or None if no usage collected.
        """
        with self._lock:
            if self._state == AccumulatorState.WAITING_START:
                # No usage events received
                return None

            if self._accumulated is None:
                return None

            # Mark as final if not already
            self._state = AccumulatorState.FINALIZED

            # Ensure is_final is True
            evidence = self._accumulated
            if not evidence.is_final:
                evidence = UsageEvidence(
                    input_tokens=evidence.input_tokens,
                    output_tokens=evidence.output_tokens,
                    cache_read_tokens=evidence.cache_read_tokens,
                    cache_write_tokens=evidence.cache_write_tokens,
                    reasoning_tokens=evidence.reasoning_tokens,
                    provider=evidence.provider,
                    model=evidence.model,
                    protocol=evidence.protocol,
                    api_version=evidence.api_version,
                    is_final=True,
                    is_indeterminate=evidence.is_indeterminate,
                    is_merged=evidence.is_merged,
                    raw_usage=evidence.raw_usage,
                    parse_status=evidence.parse_status,
                    parse_diagnostics=evidence.parse_diagnostics,
                    request_id=evidence.request_id,
                    session_id=evidence.session_id,
                    user_id=evidence.user_id,
                    tenant_id=evidence.tenant_id,
                )

            return evidence

    def recover_partial(self) -> UsageEvidence | None:
        """Recover partial usage after error.

        Called when streaming is interrupted. Returns whatever was collected
        so far, marked as indeterminate.

        Returns:
            Partial UsageEvidence or None if no usage collected.
        """
        with self._lock:
            if self._accumulated is None:
                return UsageEvidence.create_empty(
                    provider=self.provider,
                    protocol=self.protocol,
                    session_id=self.session_id,
                    user_id=self.user_id,
                    tenant_id=self.tenant_id,
                    request_id=self.request_id,
                    parse_status="partial",
                    parse_diagnostics={
                        "state": self._state.value,
                        "chunks_seen": self._chunks_seen,
                        "usage_events_seen": self._usage_events_seen,
                        "error_message": self._error_message,
                    },
                )

            # Mark as indeterminate
            self._state = AccumulatorState.ERROR

            evidence = self._accumulated
            return UsageEvidence(
                input_tokens=evidence.input_tokens,
                output_tokens=evidence.output_tokens,
                cache_read_tokens=evidence.cache_read_tokens,
                cache_write_tokens=evidence.cache_write_tokens,
                reasoning_tokens=evidence.reasoning_tokens,
                provider=evidence.provider,
                model=evidence.model,
                protocol=evidence.protocol,
                api_version=evidence.api_version,
                is_final=False,  # Not a complete usage
                is_indeterminate=True,  # Mark as uncertain
                is_merged=evidence.is_merged,
                raw_usage=evidence.raw_usage,
                parse_status="partial",
                parse_diagnostics={
                    "state": "recovered",
                    "chunks_seen": self._chunks_seen,
                    "usage_events_seen": self._usage_events_seen,
                    "error_message": self._error_message,
                },
                request_id=evidence.request_id,
                session_id=evidence.session_id,
                user_id=evidence.user_id,
                tenant_id=evidence.tenant_id,
            )

    @property
    def state(self) -> AccumulatorState:
        """Get current state."""
        with self._lock:
            return self._state

    @property
    def is_finalized(self) -> bool:
        """Check if accumulator is finalized."""
        with self._lock:
            return self._state == AccumulatorState.FINALIZED

    @property
    def has_usage(self) -> bool:
        """Check if any usage was collected."""
        with self._lock:
            return self._accumulated is not None

    def get_diagnostics(self) -> dict[str, Any]:
        """Get diagnostic information.

        Returns:
            Dictionary with diagnostic data.
        """
        with self._lock:
            return {
                "state": self._state.value,
                "session_id": self.session_id,
                "provider": self.provider,
                "protocol": self.protocol,
                "request_id": self.request_id,
                "model": self.model,
                "chunks_seen": self._chunks_seen,
                "usage_events_seen": self._usage_events_seen,
                "has_accumulated": self._accumulated is not None,
                "error_message": self._error_message,
            }
