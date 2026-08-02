"""
Open ACE - LLM Proxy Usage Parsing Integration Tests

Issue #2184: End-to-end validation of usage parsing across multiple providers.
"""

from __future__ import annotations

import json
import pytest

from app.modules.workspace.usage_accumulator import UsageAccumulator
from app.modules.workspace.usage_evidence import UsageEvidence
from app.modules.workspace.usage_parser import (
    AnthropicMessagesParser,
    OpenAIChatParser,
    UsageParserFactory,
)

from tests.fixtures.usage_fixtures import (
    ANTHROPIC_MESSAGES_REGULAR,
    ANTHROPIC_MESSAGES_STREAM_EVENTS,
    make_anthropic_regular_content,
    make_anthropic_stream_content,
    make_openai_regular_content,
    OPENAI_CHAT_REGULAR,
    OPENAI_CHAT_REGULAR_WITH_CACHE,
    OPENAI_CHAT_STREAM_CHUNKS,
    OPENAI_CHAT_STREAM_MULTI_USAGE,
    make_openai_stream_content,
)


class TestEndToEndParsing:
    """End-to-end usage parsing tests."""

    def test_openai_regular_complete_flow(self):
        """Test complete flow for OpenAI regular response."""
        # Parse
        parser = OpenAIChatParser()
        content = make_openai_regular_content(OPENAI_CHAT_REGULAR)
        evidence = parser.parse_regular(content)

        # Verify
        assert evidence.parse_status == "success"
        assert evidence.input_tokens == 100
        assert evidence.output_tokens == 50
        assert evidence.total_tokens == 150
        assert evidence.provider == "openai"

    def test_openai_regular_with_cache(self):
        """Test OpenAI regular response with cache tokens."""
        parser = OpenAIChatParser()
        content = make_openai_regular_content(OPENAI_CHAT_REGULAR_WITH_CACHE)
        evidence = parser.parse_regular(content)

        # Verify cache tokens
        assert evidence.cache_read_tokens == 20
        assert evidence.has_cache_data is True
        assert evidence.total_tokens == 170  # 100 + 50 + 20

    def test_openai_streaming_complete_flow(self):
        """Test complete flow for OpenAI streaming response."""
        parser = OpenAIChatParser()
        accumulator = UsageAccumulator(session_id="test-session", provider="openai")
        content = make_openai_stream_content(OPENAI_CHAT_STREAM_CHUNKS)

        # Process all chunks
        events = content.split(b"\n\n")
        for event in events:
            if event.strip():
                parser.parse_stream_chunk(event, accumulator)

        evidence = parser.finalize_stream(accumulator)

        # Verify
        assert evidence is not None
        assert evidence.parse_status == "success"
        assert evidence.input_tokens == 100
        assert evidence.output_tokens == 50

    def test_openai_streaming_multiple_usage_takes_final(self):
        """Test OpenAI streaming with multiple usage chunks takes final."""
        parser = OpenAIChatParser()
        accumulator = UsageAccumulator(session_id="test-session", provider="openai")
        content = make_openai_stream_content(OPENAI_CHAT_STREAM_MULTI_USAGE)

        events = content.split(b"\n\n")
        for event in events:
            if event.strip():
                parser.parse_stream_chunk(event, accumulator)

        evidence = parser.finalize_stream(accumulator)

        # Should take final values
        assert evidence.input_tokens == 100
        assert evidence.output_tokens == 50

    def test_anthropic_regular_complete_flow(self):
        """Test complete flow for Anthropic regular response."""
        parser = AnthropicMessagesParser()
        content = make_anthropic_regular_content(ANTHROPIC_MESSAGES_REGULAR)
        evidence = parser.parse_regular(content)

        # Verify
        assert evidence.parse_status == "success"
        assert evidence.input_tokens == 100
        assert evidence.output_tokens == 50
        assert evidence.cache_read_tokens == 20
        assert evidence.cache_write_tokens == 10
        assert evidence.has_cache_data is True
        assert evidence.provider == "anthropic"

    def test_anthropic_streaming_complete_flow(self):
        """Test complete flow for Anthropic streaming response with accumulation."""
        parser = AnthropicMessagesParser()
        accumulator = UsageAccumulator(session_id="test-session", provider="anthropic")
        content = make_anthropic_stream_content(ANTHROPIC_MESSAGES_STREAM_EVENTS)

        # Process all events
        events = content.split(b"\n\n")
        for event in events:
            if event.strip():
                parser.parse_stream_chunk(event, accumulator)

        evidence = parser.finalize_stream(accumulator)

        # Verify accumulation
        assert evidence is not None
        assert evidence.input_tokens == 100  # From message_start
        assert evidence.output_tokens == 50  # 30 + 20 from deltas
        assert evidence.cache_read_tokens == 20  # From message_start
        assert evidence.cache_write_tokens == 10  # From message_start

    def test_token_counting_consistency(self):
        """Test token counting is consistent across providers."""
        # OpenAI
        openai_evidence = UsageEvidence(
            input_tokens=100,
            output_tokens=50,
            cache_read_tokens=20,
            provider="openai"
        )

        # Anthropic
        anthropic_evidence = UsageEvidence(
            input_tokens=100,
            output_tokens=50,
            cache_read_tokens=20,
            provider="anthropic"
        )

        # Both should have same total
        assert openai_evidence.total_tokens == anthropic_evidence.total_tokens
        assert openai_evidence.total_tokens == 170  # 100 + 50 + 20

    def test_protocol_detection(self):
        """Test protocol detection for various response types."""
        # OpenAI regular
        openai_content = make_openai_regular_content(OPENAI_CHAT_REGULAR)
        protocol = UsageParserFactory.detect_protocol(openai_content, "application/json", "openai")
        assert protocol == "chat_completions"

        # Anthropic regular
        anthropic_content = make_anthropic_regular_content(ANTHROPIC_MESSAGES_REGULAR)
        protocol = UsageParserFactory.detect_protocol(anthropic_content, "application/json", "anthropic")
        assert protocol == "messages_api"

        # Streaming from content-type
        protocol = UsageParserFactory.detect_protocol(b"", "text/event-stream", "openai")
        assert protocol == "chat_completions_stream"

    def test_usage_evidence_validation(self):
        """Test UsageEvidence validation and clamping."""
        # Negative tokens
        evidence = UsageEvidence(input_tokens=-10, output_tokens=50, provider="test")
        warnings = evidence.clamp_tokens()

        assert evidence.input_tokens == 0
        assert len(warnings) > 0

        # Exceeds limit
        evidence = UsageEvidence(input_tokens=2_000_000_000, output_tokens=50, provider="test")
        warnings = evidence.clamp_tokens()

        assert evidence.input_tokens == 1_000_000_000
        assert len(warnings) > 0

    def test_accumulator_state_transitions(self):
        """Test UsageAccumulator state machine transitions."""
        accumulator = UsageAccumulator(session_id="test", provider="openai")

        # Initial state
        assert accumulator.is_empty()

        # Add chunk
        evidence = UsageEvidence(input_tokens=100, output_tokens=50, provider="openai")
        accumulator.add_chunk(evidence)
        assert not accumulator.is_empty()

        # Finalize
        result = accumulator.finalize()
        assert result is not None

    def test_accumulator_dedup_key(self):
        """Test accumulator dedup key generation."""
        from datetime import datetime, timezone

        start_time = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        accumulator = UsageAccumulator(
            session_id="test-session",
            provider="openai",
            request_start_time=start_time
        )

        key = accumulator.dedup_key
        assert key.startswith("test-session:")
        # Should contain timestamp in milliseconds
        assert ":" in key


class TestDownstreamIntegration:
    """Test integration with downstream consumers."""

    def test_quota_manager_interface(self):
        """Test QuotaManager.record_usage_from_evidence interface."""
        from app.modules.governance.quota_manager import QuotaManager
        from unittest.mock import MagicMock

        # Create mock to avoid actual database write
        quota_mgr = QuotaManager()
        quota_mgr.record_usage = MagicMock(return_value=True)

        evidence = UsageEvidence(
            input_tokens=100,
            output_tokens=50,
            cache_read_tokens=20,
            provider="openai",
            parse_status="success"
        )

        # Should use total_tokens (170)
        result = quota_mgr.record_usage_from_evidence(user_id=1, evidence=evidence)

        # Verify record_usage was called with total_tokens
        quota_mgr.record_usage.assert_called_once()
        call_args = quota_mgr.record_usage.call_args
        assert call_args[1]["tokens"] == 170

    def test_session_manager_interface(self):
        """Test SessionManager.increment_from_evidence interface."""
        from app.modules.workspace.session_manager import SessionManager
        from unittest.mock import MagicMock

        # Create mock to avoid actual database write
        session_mgr = SessionManager()
        session_mgr.increment_session_usage = MagicMock(return_value=True)

        evidence = UsageEvidence(
            input_tokens=100,
            output_tokens=50,
            cache_read_tokens=20,
            provider="openai",
            parse_status="success"
        )

        result = session_mgr.increment_from_evidence(
            session_id="test-session",
            evidence=evidence
        )

        # Verify increment_session_usage was called
        session_mgr.increment_session_usage.assert_called_once()
        call_args = session_mgr.increment_session_usage.call_args
        assert call_args[1]["total_tokens_delta"] == 170  # total tokens
        assert call_args[1]["total_input_delta"] == 100
        assert call_args[1]["total_output_delta"] == 50


if __name__ == "__main__":
    pytest.main([__file__, "-v"])