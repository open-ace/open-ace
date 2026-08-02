"""
Open ACE - Usage Parser Unit Tests

Tests for provider-specific usage parsers.

Issue #2184: Validates parsing logic for OpenAI and Anthropic protocols.
"""

from __future__ import annotations

import json
import pytest

from app.modules.workspace.usage_accumulator import AccumulatorState, UsageAccumulator
from app.modules.workspace.usage_evidence import UsageEvidence
from app.modules.workspace.usage_parser import (
    AnthropicMessagesParser,
    OpenAIChatParser,
    OpenAIResponsesParser,
    UsageParserFactory,
)

# Import fixtures
from tests.fixtures.usage_fixtures import (
    ANTHROPIC_MESSAGES_REGULAR,
    ANTHROPIC_MESSAGES_REGULAR_NO_CACHE,
    ANTHROPIC_MESSAGES_STREAM_EVENTS,
    ANTHROPIC_MESSAGES_STREAM_NO_DELTA,
    ANTHROPIC_RESPONSE_HEADERS,
    GATEWAY_CONVERTED_RESPONSE,
    MALFORMED_USAGE_CACHE_NEGATIVE,
    MALFORMED_USAGE_EMPTY,
    MALFORMED_USAGE_MISSING_FIELD,
    MALFORMED_USAGE_NEGATIVE,
    OPENAI_CHAT_REGULAR,
    OPENAI_CHAT_REGULAR_WITH_CACHE,
    OPENAI_CHAT_STREAM_CHUNKS,
    OPENAI_CHAT_STREAM_MULTI_USAGE,
    OPENAI_CHAT_STREAM_WITH_CACHE,
    OPENAI_RESPONSES_API,
    OPENAI_RESPONSE_HEADERS,
    USAGE_EXCEEDS_LIMIT,
    USAGE_LARGE_TOKENS,
    USAGE_ZERO_TOKENS,
    make_anthropic_regular_content,
    make_anthropic_stream_content,
    make_openai_regular_content,
    make_openai_stream_content,
)


class TestUsageParserFactory:
    """Test parser factory and protocol detection."""

    def test_detect_openai_chat_regular(self):
        """Detect OpenAI Chat Completions regular response."""
        content = make_openai_regular_content(OPENAI_CHAT_REGULAR)
        protocol = UsageParserFactory.detect_protocol(content, "application/json", "openai")
        assert protocol == "chat_completions"

    def test_detect_openai_chat_stream_from_content_type(self):
        """Detect OpenAI streaming from Content-Type."""
        protocol = UsageParserFactory.detect_protocol(b"", "text/event-stream", "openai")
        assert protocol == "chat_completions_stream"

    def test_detect_openai_chat_stream_from_prefix(self):
        """Detect OpenAI streaming from content prefix."""
        content = b"data: {}\n\n"
        protocol = UsageParserFactory.detect_protocol(content, "application/json", "openai")
        assert protocol == "chat_completions_stream"

    def test_detect_anthropic_messages_regular(self):
        """Detect Anthropic Messages API regular response."""
        content = make_anthropic_regular_content(ANTHROPIC_MESSAGES_REGULAR)
        protocol = UsageParserFactory.detect_protocol(content, "application/json", "anthropic")
        assert protocol == "messages_api"

    def test_detect_anthropic_messages_stream(self):
        """Detect Anthropic streaming from Content-Type."""
        protocol = UsageParserFactory.detect_protocol(b"", "text/event-stream", "anthropic")
        assert protocol == "messages_api_stream"

    def test_detect_responses_api(self):
        """Detect OpenAI Responses API."""
        content = make_openai_regular_content(OPENAI_RESPONSES_API)
        protocol = UsageParserFactory.detect_protocol(content, "application/json", "openai")
        assert protocol == "responses_api"

    def test_get_parser_openai_chat(self):
        """Get OpenAI Chat parser."""
        parser = UsageParserFactory.get_parser("chat_completions", "openai")
        assert isinstance(parser, OpenAIChatParser)

    def test_get_parser_anthropic(self):
        """Get Anthropic parser."""
        parser = UsageParserFactory.get_parser("messages_api", "anthropic")
        assert isinstance(parser, AnthropicMessagesParser)

    def test_get_parser_responses(self):
        """Get Responses API parser."""
        parser = UsageParserFactory.get_parser("responses_api", "openai")
        assert isinstance(parser, OpenAIResponsesParser)


class TestOpenAIChatParser:
    """Test OpenAI Chat Completions parser."""

    @pytest.fixture
    def parser(self):
        return OpenAIChatParser()

    def test_parse_regular_basic(self, parser):
        """Parse basic regular response."""
        content = make_openai_regular_content(OPENAI_CHAT_REGULAR)
        evidence = parser.parse_regular(content)

        assert evidence.provider == "openai"
        assert evidence.input_tokens == 100
        assert evidence.output_tokens == 50
        assert evidence.cache_read_tokens == 0
        assert evidence.is_final is True
        assert evidence.parse_status == "success"
        assert evidence.request_id == "chatcmpl-123"
        assert evidence.model == "gpt-4"

    def test_parse_regular_with_cache(self, parser):
        """Parse regular response with cache tokens."""
        content = make_openai_regular_content(OPENAI_CHAT_REGULAR_WITH_CACHE)
        evidence = parser.parse_regular(content)

        assert evidence.input_tokens == 100
        assert evidence.output_tokens == 50
        assert evidence.cache_read_tokens == 20
        assert evidence.has_cache_data is True

    def test_parse_regular_malformed_json(self, parser):
        """Parse malformed JSON."""
        evidence = parser.parse_regular(b"not json")

        assert evidence.is_indeterminate is True
        assert evidence.parse_status == "malformed"

    def test_parse_regular_missing_usage(self, parser):
        """Parse response without usage field."""
        content = make_openai_regular_content(MALFORMED_USAGE_MISSING_FIELD)
        evidence = parser.parse_regular(content)

        assert evidence.is_indeterminate is True
        assert evidence.parse_status == "missing"

    def test_parse_regular_empty_usage(self, parser):
        """Parse response with empty usage."""
        content = make_openai_regular_content(MALFORMED_USAGE_EMPTY)
        evidence = parser.parse_regular(content)

        assert evidence.is_indeterminate is True
        assert evidence.parse_status == "missing"

    def test_parse_regular_negative_tokens(self, parser):
        """Parse response with negative tokens (should clamp to 0)."""
        content = make_openai_regular_content(MALFORMED_USAGE_NEGATIVE)
        evidence = parser.parse_regular(content)

        # Should be clamped to 0
        assert evidence.input_tokens >= 0
        assert evidence.output_tokens >= 0
        assert len(evidence.parse_warnings) > 0

    def test_parse_regular_exceeds_limit(self, parser):
        """Parse response with tokens exceeding limit."""
        content = make_openai_regular_content(USAGE_EXCEEDS_LIMIT)
        evidence = parser.parse_regular(content)

        # Should be clamped to limits
        assert evidence.input_tokens <= 1_000_000_000
        assert len(evidence.parse_warnings) > 0

    def test_extract_request_id_openai(self, parser):
        """Extract request_id from OpenAI headers."""
        request_id = parser.extract_request_id(OPENAI_RESPONSE_HEADERS)
        assert request_id == "req-abc123"

    def test_stream_single_usage(self, parser):
        """Parse streaming response with single usage chunk."""
        accumulator = UsageAccumulator(session_id="test-session", provider="openai")
        content = make_openai_stream_content(OPENAI_CHAT_STREAM_CHUNKS)

        # Split by SSE boundaries (double newline)
        events = content.split(b"\n\n")
        for event in events:
            if event.strip():
                parser.parse_stream_chunk(event, accumulator)

        evidence = parser.finalize_stream(accumulator)

        assert evidence is not None
        assert evidence.input_tokens == 100
        assert evidence.output_tokens == 50

    def test_stream_multiple_usage_takes_final(self, parser):
        """Parse streaming with multiple usage chunks - should take final."""
        accumulator = UsageAccumulator(session_id="test-session", provider="openai")
        content = make_openai_stream_content(OPENAI_CHAT_STREAM_MULTI_USAGE)

        # Split by SSE boundaries
        events = content.split(b"\n\n")
        for event in events:
            if event.strip():
                parser.parse_stream_chunk(event, accumulator)

        evidence = parser.finalize_stream(accumulator)

        assert evidence is not None
        # Should take final values (100, 50)
        assert evidence.input_tokens == 100
        assert evidence.output_tokens == 50

    def test_stream_with_cache(self, parser):
        """Parse streaming response with cache tokens."""
        accumulator = UsageAccumulator(session_id="test-session", provider="openai")
        content = make_openai_stream_content(OPENAI_CHAT_STREAM_WITH_CACHE)

        # Split by SSE boundaries
        events = content.split(b"\n\n")
        for event in events:
            if event.strip():
                parser.parse_stream_chunk(event, accumulator)

        evidence = parser.finalize_stream(accumulator)

        assert evidence is not None
        assert evidence.cache_read_tokens == 20
        assert evidence.has_cache_data is True


class TestAnthropicMessagesParser:
    """Test Anthropic Messages API parser."""

    @pytest.fixture
    def parser(self):
        return AnthropicMessagesParser()

    def test_parse_regular_basic(self, parser):
        """Parse basic regular response."""
        content = make_anthropic_regular_content(ANTHROPIC_MESSAGES_REGULAR)
        evidence = parser.parse_regular(content)

        assert evidence.provider == "anthropic"
        assert evidence.input_tokens == 100
        assert evidence.output_tokens == 50
        assert evidence.cache_read_tokens == 20
        assert evidence.cache_write_tokens == 10
        assert evidence.has_cache_data is True
        assert evidence.is_final is True
        assert evidence.parse_status == "success"

    def test_parse_regular_no_cache(self, parser):
        """Parse regular response without cache."""
        content = make_anthropic_regular_content(ANTHROPIC_MESSAGES_REGULAR_NO_CACHE)
        evidence = parser.parse_regular(content)

        assert evidence.input_tokens == 80
        assert evidence.output_tokens == 40
        assert evidence.cache_read_tokens == 0
        assert evidence.cache_write_tokens == 0
        assert evidence.has_cache_data is False

    def test_extract_request_id_anthropic(self, parser):
        """Extract request_id from Anthropic headers."""
        request_id = parser.extract_request_id(ANTHROPIC_RESPONSE_HEADERS)
        assert request_id == "req-def456"

    def test_stream_complete_sequence(self, parser):
        """Parse complete streaming sequence with message_start and message_delta."""
        accumulator = UsageAccumulator(session_id="test-session", provider="anthropic")
        content = make_anthropic_stream_content(ANTHROPIC_MESSAGES_STREAM_EVENTS)

        # Split by event boundaries (double newline)
        events = content.split(b"\n\n")
        for event in events:
            if event.strip():
                parser.parse_stream_chunk(event, accumulator)

        evidence = parser.finalize_stream(accumulator)

        assert evidence is not None
        # input_tokens from message_start
        assert evidence.input_tokens == 100
        # output_tokens accumulated from deltas (30 + 20 = 50)
        assert evidence.output_tokens == 50
        # cache from message_start
        assert evidence.cache_read_tokens == 20
        assert evidence.cache_write_tokens == 10

    def test_stream_only_message_start(self, parser):
        """Parse streaming with only message_start (no message_delta)."""
        accumulator = UsageAccumulator(session_id="test-session", provider="anthropic")
        content = make_anthropic_stream_content(ANTHROPIC_MESSAGES_STREAM_NO_DELTA)

        # Split by event boundaries
        events = content.split(b"\n\n")
        for event in events:
            if event.strip():
                parser.parse_stream_chunk(event, accumulator)

        evidence = parser.finalize_stream(accumulator)

        assert evidence is not None
        assert evidence.input_tokens == 100
        # output_tokens should be 0 (no deltas)
        assert evidence.output_tokens == 0


class TestOpenAIResponsesParser:
    """Test OpenAI Responses API parser."""

    @pytest.fixture
    def parser(self):
        return OpenAIResponsesParser()

    def test_parse_regular(self, parser):
        """Parse Responses API response."""
        content = make_openai_regular_content(OPENAI_RESPONSES_API)
        evidence = parser.parse_regular(content)

        assert evidence.provider == "openai"
        assert evidence.protocol_type == "responses_api"
        assert evidence.input_tokens == 100
        assert evidence.output_tokens == 50
        assert evidence.cache_read_tokens == 0  # Responses API has no cache
        assert evidence.has_cache_data is False


class TestGatewayConvertedResponse:
    """Test gateway-converted responses (Anthropic to OpenAI format)."""

    @pytest.fixture
    def parser(self):
        return OpenAIChatParser()

    def test_gateway_converted(self, parser):
        """Parse gateway-converted response."""
        content = make_openai_regular_content(GATEWAY_CONVERTED_RESPONSE)
        evidence = parser.parse_regular(content)

        assert evidence.provider == "openai"
        assert evidence.input_tokens == 100
        assert evidence.output_tokens == 50
        # Cache should be preserved in prompt_tokens_details
        assert evidence.cache_read_tokens == 20
        assert evidence.has_cache_data is True


class TestUsageEvidenceValidation:
    """Test UsageEvidence validation and clamping."""

    def test_validate_negative_tokens(self):
        """Validate detects negative tokens."""
        evidence = UsageEvidence(
            input_tokens=-10,
            output_tokens=50,
            provider="test"
        )
        warnings = evidence.validate()
        assert len(warnings) > 0
        assert "negative" in warnings[0]

    def test_validate_exceeds_limit(self):
        """Validate detects tokens exceeding limit."""
        evidence = UsageEvidence(
            input_tokens=2_000_000_000,  # Over limit
            output_tokens=50,
            provider="test"
        )
        warnings = evidence.validate()
        assert len(warnings) > 0
        assert "exceeds limit" in warnings[0]

    def test_clamp_tokens_negative(self):
        """Clamp negative tokens to 0."""
        evidence = UsageEvidence(
            input_tokens=-10,
            output_tokens=-5,
            provider="test"
        )
        warnings = evidence.clamp_tokens()

        assert evidence.input_tokens == 0
        assert evidence.output_tokens == 0
        assert len(warnings) >= 2

    def test_clamp_tokens_exceeds_limit(self):
        """Clamp tokens exceeding limit."""
        evidence = UsageEvidence(
            input_tokens=2_000_000_000,
            output_tokens=1_500_000_000,
            provider="test"
        )
        warnings = evidence.clamp_tokens()

        assert evidence.input_tokens == 1_000_000_000
        assert evidence.output_tokens == 1_000_000_000


class TestUsageAccumulatorState:
    """Test UsageAccumulator state machine."""

    def test_initial_state(self):
        """Test initial state is EMPTY."""
        acc = UsageAccumulator(session_id="test", provider="openai")
        assert acc.state == AccumulatorState.EMPTY
        assert acc.is_empty() is True

    def test_state_transitions_on_add(self):
        """Test state transitions to ACCUMULATING on first chunk."""
        acc = UsageAccumulator(session_id="test", provider="openai")
        evidence = UsageEvidence(input_tokens=100, output_tokens=50, provider="openai")

        acc.add_chunk(evidence)
        assert acc.state == AccumulatorState.ACCUMULATING
        assert acc.is_empty() is False

    def test_state_transitions_on_finalize(self):
        """Test state transitions to FINALIZED on finalize."""
        acc = UsageAccumulator(session_id="test", provider="openai")
        evidence = UsageEvidence(input_tokens=100, output_tokens=50, provider="openai")

        acc.add_chunk(evidence)
        result = acc.finalize()

        assert acc.state == AccumulatorState.FINALIZED
        assert result is not None

    def test_dedup_key_format(self):
        """Test dedup key format."""
        from datetime import datetime, timezone

        start_time = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        acc = UsageAccumulator(session_id="test-session", provider="openai", request_start_time=start_time)

        key = acc.dedup_key
        assert key.startswith("test-session:")
        assert ":" in key

    def test_anthropic_accumulation_strategy(self):
        """Test Anthropic accumulation strategy."""
        acc = UsageAccumulator(session_id="test", provider="anthropic")

        # Add message_start (absolute input)
        start_evidence = UsageEvidence(
            input_tokens=100,
            output_tokens=0,
            cache_read_tokens=20,
            provider="anthropic"
        )
        acc.add_chunk(start_evidence)

        # Add message_delta (incremental output)
        delta1_evidence = UsageEvidence(
            input_tokens=0,
            output_tokens=30,  # Increment
            provider="anthropic"
        )
        acc.add_chunk(delta1_evidence)

        # Add another message_delta
        delta2_evidence = UsageEvidence(
            input_tokens=0,
            output_tokens=20,  # Increment
            provider="anthropic"
        )
        acc.add_chunk(delta2_evidence)

        result = acc.finalize()

        assert result is not None
        assert result.input_tokens == 100  # From start
        assert result.output_tokens == 50  # 30 + 20 accumulated
        assert result.cache_read_tokens == 20  # From start

    def test_mark_interrupted(self):
        """Test marking accumulator as interrupted."""
        acc = UsageAccumulator(session_id="test", provider="openai")
        evidence = UsageEvidence(input_tokens=100, output_tokens=30, provider="openai")

        acc.add_chunk(evidence)
        result = acc.mark_interrupted()

        assert result is not None
        assert result.is_indeterminate is True
        assert result.parse_status == "partial"
        assert acc.state == AccumulatorState.FAILED