"""Unit tests for Usage Parser Module (Issue #2184)."""

import pytest

from app.modules.workspace.usage_evidence import UsageEvidence
from app.modules.workspace.usage_parser import (
    AnthropicMessagesParser,
    GatewayParser,
    OpenAIChatParser,
    OpenAIResponsesParser,
    UsageParserFactory,
    parse_anthropic_sse_event,
    parse_sse_line,
)


class TestOpenAIChatParser:
    """Test OpenAI Chat Completions parser."""

    def test_parse_with_usage(self):
        """Test parsing response with usage."""
        parser = OpenAIChatParser(
            provider="openai",
            model="gpt-4",
            session_id="sess-123",
            user_id=10,
            tenant_id=5,
        )

        response = {
            "id": "chatcmpl-123",
            "model": "gpt-4",
            "usage": {"prompt_tokens": 500, "completion_tokens": 100},
        }

        evidence = parser.parse(response)

        assert evidence is not None
        assert evidence.input_tokens == 500
        assert evidence.output_tokens == 100
        assert evidence.provider == "openai"
        assert evidence.protocol == "openai_chat"

    def test_parse_without_usage(self):
        """Test parsing response without usage."""
        parser = OpenAIChatParser(provider="openai")

        response = {"id": "chatcmpl-123", "model": "gpt-4"}

        evidence = parser.parse(response)

        assert evidence is None

    def test_parse_sse_chunk(self):
        """Test parsing SSE chunk with usage."""
        parser = OpenAIChatParser(
            provider="openai",
            model="gpt-4",
            session_id="sess-123",
            user_id=10,
            tenant_id=5,
        )

        chunk = {
            "id": "chatcmpl-123",
            "model": "gpt-4",
            "usage": {"prompt_tokens": 500, "completion_tokens": 100},
        }

        evidence = parser.parse_sse_chunk(chunk)

        assert evidence is not None
        assert evidence.input_tokens == 500
        assert evidence.output_tokens == 100


class TestAnthropicMessagesParser:
    """Test Anthropic Messages API parser."""

    def test_parse_message_start(self):
        """Test parsing message_start event."""
        parser = AnthropicMessagesParser(
            model="claude-3-opus",
            session_id="sess-456",
            user_id=15,
            tenant_id=3,
            api_version="2023-06-01",
        )

        chunk = {
            "type": "message_start",
            "message": {
                "id": "msg-123",
                "model": "claude-3-opus",
                "usage": {
                    "input_tokens": 1000,
                    "output_tokens": 0,
                    "cache_read_input_tokens": 200,
                    "cache_creation_input_tokens": 100,
                },
            },
        }

        evidence = parser.parse_sse_chunk(chunk)

        assert evidence is not None
        assert evidence.input_tokens == 1000
        assert evidence.output_tokens == 0
        assert evidence.cache_read_tokens == 200
        assert evidence.cache_write_tokens == 100
        assert evidence.is_final is False  # Not final, more events coming
        assert evidence.parse_status == "partial"

    def test_parse_message_delta(self):
        """Test parsing message_delta event."""
        parser = AnthropicMessagesParser(
            model="claude-3-opus",
            session_id="sess-456",
            user_id=15,
            tenant_id=3,
        )

        chunk = {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn"},
            "usage": {"output_tokens": 500},
        }

        evidence = parser.parse_sse_chunk(chunk)

        assert evidence is not None
        assert evidence.input_tokens == 0  # input only in message_start
        assert evidence.output_tokens == 500
        assert evidence.is_final is False

    def test_parse_message_stop(self):
        """Test parsing message_stop event."""
        parser = AnthropicMessagesParser(
            model="claude-3-opus",
            session_id="sess-456",
            user_id=15,
            tenant_id=3,
        )

        chunk = {"type": "message_stop"}

        evidence = parser.parse_sse_chunk(chunk)

        assert evidence is not None
        assert evidence.is_final is True  # Signals finalization

    def test_parse_non_streaming_response(self):
        """Test parsing non-streaming Anthropic response."""
        parser = AnthropicMessagesParser(
            model="claude-3-opus",
            session_id="sess-456",
            user_id=15,
            tenant_id=3,
        )

        response = {
            "id": "msg-123",
            "model": "claude-3-opus",
            "usage": {
                "input_tokens": 1000,
                "output_tokens": 500,
                "cache_read_input_tokens": 200,
            },
        }

        evidence = parser.parse(response)

        assert evidence is not None
        assert evidence.input_tokens == 1000
        assert evidence.output_tokens == 500
        assert evidence.cache_read_tokens == 200
        assert evidence.is_final is True


class TestUsageParserFactory:
    """Test parser factory."""

    def test_determine_protocol_openai_chat(self):
        """Test protocol detection for OpenAI Chat."""
        protocol = UsageParserFactory.determine_protocol(
            request_path="/v1/chat/completions",
            provider="openai",
            content_type="application/json",
        )
        assert protocol == "openai_chat"

    def test_determine_protocol_openai_responses(self):
        """Test protocol detection for OpenAI Responses API."""
        protocol = UsageParserFactory.determine_protocol(
            request_path="/v1/responses",
            provider="openai",
            content_type="application/json",
        )
        assert protocol == "openai_responses"

    def test_determine_protocol_anthropic(self):
        """Test protocol detection for Anthropic."""
        protocol = UsageParserFactory.determine_protocol(
            request_path="/v1/messages",
            provider="anthropic",
            content_type="application/json",
        )
        assert protocol == "anthropic_messages"

    def test_determine_protocol_anthropic_by_provider(self):
        """Test protocol detection by provider alone."""
        protocol = UsageParserFactory.determine_protocol(
            request_path="",
            provider="anthropic",
            content_type="text/event-stream",
        )
        assert protocol == "anthropic_messages"

    def test_create_parser_openai_chat(self):
        """Test creating OpenAI Chat parser."""
        parser = UsageParserFactory.create_parser(
            protocol="openai_chat",
            provider="openai",
            model="gpt-4",
            session_id="sess-123",
            user_id=10,
            tenant_id=5,
        )

        assert isinstance(parser, OpenAIChatParser)
        assert parser.provider == "openai"
        assert parser.model == "gpt-4"

    def test_create_parser_anthropic(self):
        """Test creating Anthropic parser."""
        parser = UsageParserFactory.create_parser(
            protocol="anthropic_messages",
            provider="anthropic",
            model="claude-3-opus",
            session_id="sess-456",
            user_id=15,
            tenant_id=3,
            api_version="2023-06-01",
        )

        assert isinstance(parser, AnthropicMessagesParser)
        assert parser.api_version == "2023-06-01"

    def test_create_parser_gateway(self):
        """Test creating Gateway parser."""
        parser = UsageParserFactory.create_parser(
            protocol="gateway_openai",
            provider="custom-gateway",
            model="custom-model",
            session_id="sess-789",
            user_id=20,
            tenant_id=10,
        )

        assert isinstance(parser, GatewayParser)
        assert parser.provider == "custom-gateway"


class TestParseSSELine:
    """Test SSE line parsing."""

    def test_parse_data_line(self):
        """Test parsing data line."""
        line = b'data: {"type": "response", "usage": {"input_tokens": 100}}'
        result = parse_sse_line(line)

        assert result is not None
        assert result["type"] == "response"
        assert result["usage"]["input_tokens"] == 100

    def test_parse_event_line(self):
        """Test parsing event line."""
        line = b"event: message_start"
        result = parse_sse_line(line)

        assert result is not None
        assert result["_event_type"] == "message_start"

    def test_parse_done_marker(self):
        """Test parsing [DONE] marker."""
        line = b"data: [DONE]"
        result = parse_sse_line(line)

        assert result is not None
        assert result.get("_done") is True

    def test_parse_empty_line(self):
        """Test parsing empty line."""
        result = parse_sse_line(b"")
        assert result is None

    def test_parse_invalid_json(self):
        """Test parsing invalid JSON."""
        line = b"data: {invalid json}"
        result = parse_sse_line(line)

        assert result is None


class TestParseAnthropicSSEEvent:
    """Test Anthropic SSE event parsing."""

    def test_parse_data_line(self):
        """Test parsing Anthropic SSE data line."""
        line = b'data: {"type": "message_start", "message": {"usage": {"input_tokens": 1000}}}'
        result = parse_anthropic_sse_event(line)

        assert result is not None
        assert result["type"] == "message_start"
        assert result["message"]["usage"]["input_tokens"] == 1000
