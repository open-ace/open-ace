"""Unit tests for Usage Accumulator Module (Issue #2184)."""

import pytest

from app.modules.workspace.usage_accumulator import AccumulatorState, UsageAccumulator
from app.modules.workspace.usage_evidence import UsageEvidence


class TestUsageAccumulator:
    """Test UsageAccumulator state machine."""

    def test_initial_state(self):
        """Test initial state is WAITING_START."""
        acc = UsageAccumulator(
            session_id="sess-123",
            provider="anthropic",
            protocol="anthropic_messages",
        )

        assert acc.state == AccumulatorState.WAITING_START
        assert not acc.has_usage
        assert not acc.is_finalized

    def test_collect_first_event(self):
        """Test collecting first usage event."""
        acc = UsageAccumulator(
            session_id="sess-123",
            provider="anthropic",
            protocol="anthropic_messages",
        )

        evidence = UsageEvidence(
            input_tokens=1000,
            output_tokens=0,
            provider="anthropic",
            session_id="sess-123",
            is_final=False,
        )

        should_continue = acc.collect(evidence)

        assert should_continue is True
        assert acc.state == AccumulatorState.COLLECTING
        assert acc.has_usage

    def test_collect_multiple_events(self):
        """Test collecting multiple usage events."""
        acc = UsageAccumulator(
            session_id="sess-123",
            provider="anthropic",
            protocol="anthropic_messages",
        )

        # First event: message_start
        ev1 = UsageEvidence(
            input_tokens=1000,
            output_tokens=0,
            cache_read_tokens=200,
            provider="anthropic",
            session_id="sess-123",
            is_final=False,
        )
        acc.collect(ev1)

        # Second event: message_delta
        ev2 = UsageEvidence(
            input_tokens=0,
            output_tokens=500,
            provider="anthropic",
            session_id="sess-123",
            is_final=False,
        )
        should_continue = acc.collect(ev2)

        assert should_continue is True
        assert acc.state == AccumulatorState.COLLECTING

    def test_finalize_on_is_final(self):
        """Test finalization when receiving is_final=True."""
        acc = UsageAccumulator(
            session_id="sess-123",
            provider="anthropic",
            protocol="anthropic_messages",
        )

        # Collect partial event
        ev1 = UsageEvidence(
            input_tokens=1000,
            output_tokens=500,
            provider="anthropic",
            session_id="sess-123",
            is_final=False,
        )
        acc.collect(ev1)

        # Collect final event (message_stop)
        ev2 = UsageEvidence(
            input_tokens=0,
            output_tokens=0,
            provider="anthropic",
            session_id="sess-123",
            is_final=True,
        )
        should_continue = acc.collect(ev2)

        assert should_continue is False
        assert acc.state == AccumulatorState.FINALIZED
        assert acc.is_finalized

    def test_finalize_explicit(self):
        """Test explicit finalization."""
        acc = UsageAccumulator(
            session_id="sess-123",
            provider="anthropic",
            protocol="anthropic_messages",
        )

        ev1 = UsageEvidence(
            input_tokens=1000,
            output_tokens=500,
            provider="anthropic",
            session_id="sess-123",
            is_final=False,
        )
        acc.collect(ev1)

        result = acc.finalize()

        assert result is not None
        assert result.input_tokens == 1000
        assert result.output_tokens == 500
        assert result.is_final is True
        assert acc.state == AccumulatorState.FINALIZED

    def test_finalize_without_usage(self):
        """Test finalizing without any usage."""
        acc = UsageAccumulator(
            session_id="sess-123",
            provider="anthropic",
            protocol="anthropic_messages",
        )

        result = acc.finalize()

        assert result is None

    def test_mark_error(self):
        """Test marking error state."""
        acc = UsageAccumulator(
            session_id="sess-123",
            provider="anthropic",
            protocol="anthropic_messages",
        )

        acc.mark_error("Connection reset")

        assert acc.state == AccumulatorState.ERROR

    def test_recover_partial(self):
        """Test recovering partial usage after error."""
        acc = UsageAccumulator(
            session_id="sess-123",
            provider="anthropic",
            protocol="anthropic_messages",
        )

        # Collect some usage
        ev1 = UsageEvidence(
            input_tokens=1000,
            output_tokens=200,
            provider="anthropic",
            session_id="sess-123",
            is_final=False,
        )
        acc.collect(ev1)

        # Recover partial
        result = acc.recover_partial()

        assert result is not None
        assert result.input_tokens == 1000
        assert result.output_tokens == 200
        assert result.is_final is False
        assert result.is_indeterminate is True
        assert result.parse_status == "partial"
        assert result.parse_diagnostics["state"] == "recovered"

    def test_recover_partial_no_usage(self):
        """Test recovering when no usage was collected."""
        acc = UsageAccumulator(
            session_id="sess-123",
            provider="anthropic",
            protocol="anthropic_messages",
        )

        result = acc.recover_partial()

        assert result is not None
        assert result.input_tokens == 0
        assert result.output_tokens == 0
        assert result.is_indeterminate is True
        assert result.parse_status == "partial"

    def test_merge_events(self):
        """Test merging multiple events."""
        acc = UsageAccumulator(
            session_id="sess-123",
            provider="anthropic",
            protocol="anthropic_messages",
        )

        # First event: message_start with cache
        ev1 = UsageEvidence(
            input_tokens=1000,
            output_tokens=0,
            cache_read_tokens=200,
            cache_write_tokens=100,
            provider="anthropic",
            session_id="sess-123",
            is_final=False,
        )
        acc.collect(ev1)

        # Second event: message_delta with output
        ev2 = UsageEvidence(
            input_tokens=0,
            output_tokens=500,
            cache_read_tokens=50,  # Additional cache read
            provider="anthropic",
            session_id="sess-123",
            is_final=False,
        )
        acc.collect(ev2)

        # Finalize
        result = acc.finalize()

        assert result is not None
        assert result.input_tokens == 1000  # Keep original input
        assert result.output_tokens == 500  # Accumulated output
        assert result.cache_read_tokens == 250  # Accumulated cache
        assert result.cache_write_tokens == 100
        assert result.is_merged is True

    def test_get_diagnostics(self):
        """Test getting diagnostic information."""
        acc = UsageAccumulator(
            session_id="sess-123",
            provider="anthropic",
            protocol="anthropic_messages",
            request_id="req-456",
            model="claude-3-opus",
        )

        ev1 = UsageEvidence(
            input_tokens=100,
            output_tokens=50,
            provider="anthropic",
            is_final=False,  # Not final to stay in COLLECTING state
        )
        acc.collect(ev1)

        diagnostics = acc.get_diagnostics()

        assert diagnostics["state"] == "collecting"
        assert diagnostics["session_id"] == "sess-123"
        assert diagnostics["provider"] == "anthropic"
        assert diagnostics["request_id"] == "req-456"
        assert diagnostics["model"] == "claude-3-opus"
        assert diagnostics["chunks_seen"] == 1
        assert diagnostics["usage_events_seen"] == 1
        assert diagnostics["has_accumulated"] is True

    def test_thread_safety(self):
        """Test thread-safe operations."""
        import threading

        acc = UsageAccumulator(
            session_id="sess-123",
            provider="anthropic",
            protocol="anthropic_messages",
        )

        errors = []

        def collect_usage():
            try:
                for i in range(10):
                    ev = UsageEvidence(
                        input_tokens=100,
                        output_tokens=50,
                        provider="anthropic",
                        session_id="sess-123",
                        is_final=False,
                    )
                    acc.collect(ev)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=collect_usage) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert acc.state in [AccumulatorState.COLLECTING, AccumulatorState.FINALIZED]
