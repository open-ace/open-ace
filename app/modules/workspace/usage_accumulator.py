"""
Open ACE - Usage Accumulator Module

Streaming usage accumulator with deduplication and merging logic.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

from .usage_evidence import UsageEvidence

logger = logging.getLogger(__name__)


class AccumulatorState(Enum):
    """Accumulator state machine states."""
    EMPTY = "empty"                 # Initial state, no usage data
    ACCUMULATING = "accumulating"   # Accumulating streaming chunks
    FINALIZED = "finalized"         # Completed, has final usage
    FAILED = "failed"               # Parsing failed, no valid usage


@dataclass
class AccumulatorConfig:
    """Configuration for usage accumulator."""
    # Dedup window in seconds
    dedup_window_seconds: int = 300  # 5 minutes


@dataclass
class UsageAccumulator:
    """Streaming usage accumulator with deduplication and merging.

    Handles multi-chunk merging and deduplication for streaming responses.
    Each HTTP request should create a new instance.

    Dedup strategy:
    - Granularity: Single LLM call (one HTTP request-response pair)
    - Dedup key: session_id + request_start_timestamp (milliseconds, UTC)
    - Dedup window: 5 minutes (covers streaming response duration)

    Provider-specific merge strategies:
    - OpenAI Chat: Subsequent chunks override previous (take final usage)
    - Anthropic Messages:
        - input_tokens: Take value from message_start (absolute)
        - output_tokens: Sum all message_delta.usage.output_tokens (incremental)
        - cache_tokens: Take value from message_start (absolute)

    Concurrency safety:
    - Each HTTP request creates an independent instance
    - Instance-level state, no shared data
    - No thread locks needed
    """

    session_id: str
    provider: str = "unknown"
    request_start_time: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    config: AccumulatorConfig = field(default_factory=AccumulatorConfig)

    # Internal state
    _state: AccumulatorState = field(default=AccumulatorState.EMPTY, repr=False)
    _chunks: list[UsageEvidence] = field(default_factory=list, repr=False)
    _final: UsageEvidence | None = field(default=None, repr=False)

    # Anthropic-specific accumulation state
    _anthropic_input: int = field(default=0, repr=False)
    _anthropic_output: int = field(default=0, repr=False)
    _anthropic_cache_read: int = field(default=0, repr=False)
    _anthropic_cache_write: int = field(default=0, repr=False)
    _anthropic_model: str | None = field(default=None, repr=False)
    _anthropic_request_id: str | None = field(default=None, repr=False)
    _anthropic_has_message_start: bool = field(default=False, repr=False)

    def __post_init__(self):
        """Initialize after dataclass construction."""
        # Ensure timezone-aware datetime
        if self.request_start_time.tzinfo is None:
            self.request_start_time = self.request_start_time.replace(tzinfo=timezone.utc)

    @property
    def dedup_key(self) -> str:
        """Generate dedup key: session_id + request_start_timestamp (UTC, milliseconds)."""
        ts_ms = int(self.request_start_time.timestamp() * 1000)
        return f"{self.session_id}:{ts_ms}"

    @property
    def state(self) -> AccumulatorState:
        """Get current accumulator state."""
        return self._state

    def is_empty(self) -> bool:
        """Check if accumulator has no usage data."""
        return self._state == AccumulatorState.EMPTY

    def add_chunk(self, evidence: UsageEvidence) -> None:
        """Add a usage chunk, automatically handling dedup and merge.

        Different provider merge strategies:
        - OpenAI Chat: Subsequent chunk overrides previous (take final usage)
        - Anthropic Messages: output_tokens incremental accumulation
        - OpenAI Responses: Single final value
        """
        if self._state == AccumulatorState.FINALIZED:
            logger.warning(
                "Attempted to add chunk to finalized accumulator",
                extra={"dedup_key": self.dedup_key}
            )
            return

        if evidence.is_indeterminate:
            # Don't accumulate indeterminate chunks
            return

        # Update state
        if self._state == AccumulatorState.EMPTY:
            self._state = AccumulatorState.ACCUMULATING

        # Provider-specific accumulation logic
        if self.provider == "anthropic":
            self._accumulate_anthropic(evidence)
        else:
            # OpenAI-style: override with latest usage
            self._accumulate_override(evidence)

    def _accumulate_override(self, evidence: UsageEvidence) -> None:
        """Accumulate using override strategy (OpenAI-style).

        Each new usage chunk replaces the previous one.
        """
        self._chunks.append(evidence)
        self._final = evidence

    def _accumulate_anthropic(self, evidence: UsageEvidence) -> None:
        """Accumulate using Anthropic incremental strategy.

        - message_start: Provides input_tokens, cache_tokens (absolute values)
        - message_delta: Provides output_tokens increment

        Args:
            evidence: Usage evidence from a single chunk.
        """
        # Extract event type from protocol_type or infer from evidence
        # For Anthropic streaming, we need to handle message_start and message_delta differently

        # Check if this is from message_start (has input_tokens and maybe cache)
        # or message_delta (only has output_tokens increment)
        is_message_start = (
            evidence.input_tokens > 0 and
            not self._anthropic_has_message_start
        )

        if is_message_start:
            # message_start event: capture input and cache tokens (absolute values)
            self._anthropic_input = evidence.input_tokens
            self._anthropic_cache_read = evidence.cache_read_tokens
            self._anthropic_cache_write = evidence.cache_write_tokens
            self._anthropic_model = evidence.model
            self._anthropic_request_id = evidence.request_id
            self._anthropic_has_message_start = True
        else:
            # message_delta event: accumulate output_tokens (incremental)
            self._anthropic_output += evidence.output_tokens

        self._chunks.append(evidence)

    def finalize(self) -> UsageEvidence | None:
        """Return final merged result.

        Returns:
            UsageEvidence if valid usage found, None otherwise.
        """
        if self._state == AccumulatorState.EMPTY:
            return None

        if self._state == AccumulatorState.FINALIZED:
            return self._final

        if self._state == AccumulatorState.FAILED:
            return None

        # Build final evidence based on provider
        if self.provider == "anthropic":
            self._final = self._build_anthropic_final()
        else:
            # Override strategy: _final already set in add_chunk
            pass

        self._state = AccumulatorState.FINALIZED
        return self._final

    def _build_anthropic_final(self) -> UsageEvidence:
        """Build final UsageEvidence for Anthropic streaming.

        Combines input_tokens from message_start and accumulated output_tokens.
        """
        # Ensure non-negative values
        input_tokens = max(0, self._anthropic_input)
        output_tokens = max(0, self._anthropic_output)
        cache_read = max(0, self._anthropic_cache_read)
        cache_write = max(0, self._anthropic_cache_write)

        return UsageEvidence(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read,
            cache_write_tokens=cache_write,
            provider=self.provider,
            model=self._anthropic_model,
            is_final=True,
            has_cache_data=(cache_read > 0 or cache_write > 0),
            protocol_type="messages_api",
            parse_status="success",
            request_id=self._anthropic_request_id,
            session_id=self.session_id,
        )

    def mark_interrupted(self) -> UsageEvidence | None:
        """Mark streaming response interrupted, return partial result.

        Use this when a streaming response is interrupted or times out.

        Returns:
            Partial UsageEvidence with is_indeterminate=True, or None if empty.
        """
        if self._state == AccumulatorState.EMPTY:
            return None

        if self._state == AccumulatorState.FINALIZED:
            return self._final

        # Create partial evidence
        if self.provider == "anthropic" and self._anthropic_has_message_start:
            # Have partial Anthropic data
            partial = self._build_anthropic_final()
            partial.is_indeterminate = True
            partial.parse_status = "partial"
            partial.parse_warnings.append("Stream interrupted before final usage")
        elif self._final is not None:
            # Have partial OpenAI data
            partial = UsageEvidence(
                input_tokens=self._final.input_tokens,
                output_tokens=self._final.output_tokens,
                cache_read_tokens=self._final.cache_read_tokens,
                cache_write_tokens=self._final.cache_write_tokens,
                provider=self.provider,
                model=self._final.model,
                is_final=True,
                is_indeterminate=True,
                has_cache_data=self._final.has_cache_data,
                protocol_type=self._final.protocol_type,
                parse_status="partial",
                parse_warnings=["Stream interrupted before final usage"],
                request_id=self._final.request_id,
                session_id=self.session_id,
            )
        else:
            # No usable data
            partial = UsageEvidence.create_empty(
                provider=self.provider,
                parse_status="partial",
                parse_warnings=["Stream interrupted with no usage data"],
                session_id=self.session_id,
            )

        self._final = partial
        self._state = AccumulatorState.FAILED
        return partial

    def get_chunk_count(self) -> int:
        """Get number of usage chunks accumulated."""
        return len(self._chunks)