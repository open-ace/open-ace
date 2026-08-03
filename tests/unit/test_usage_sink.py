"""Integration tests for Usage Sink Module (Issue #2184)."""

import pytest

from app.modules.workspace.usage_dedup import reset_dedup_cache_for_tests
from app.modules.workspace.usage_evidence import UsageEvidence
from app.modules.workspace.usage_sink import (
    CompositeSink,
    DiagnosticsSink,
    StatsSink,
)


class TestDiagnosticsSink:
    """Test DiagnosticsSink (always succeeds)."""

    def test_consume_success_status(self):
        """Test consuming success status."""
        sink = DiagnosticsSink()

        evidence = UsageEvidence(
            input_tokens=100,
            output_tokens=50,
            provider="openai",
            parse_status="success",
            session_id="sess-123",
        )

        result = sink.consume(evidence)

        assert result is True

    def test_consume_malformed_status(self):
        """Test consuming malformed status."""
        sink = DiagnosticsSink()

        evidence = UsageEvidence(
            input_tokens=0,
            output_tokens=0,
            provider="unknown",
            parse_status="malformed",
            parse_diagnostics={"error": "missing_usage"},
            session_id="sess-123",
        )

        result = sink.consume(evidence)

        assert result is True  # Always succeeds

    def test_consume_indeterminate(self):
        """Test consuming indeterminate evidence."""
        sink = DiagnosticsSink()

        evidence = UsageEvidence(
            input_tokens=100,
            output_tokens=50,
            provider="openai",
            is_indeterminate=True,
            parse_status="partial",
            session_id="sess-123",
        )

        result = sink.consume(evidence)

        assert result is True


class TestStatsSink:
    """Test StatsSink (non-critical, always succeeds)."""

    def test_consume_always_succeeds(self):
        """Test that StatsSink always succeeds."""
        sink = StatsSink()

        evidence = UsageEvidence(
            input_tokens=100,
            output_tokens=50,
            provider="openai",
            session_id="sess-123",
        )

        result = sink.consume(evidence)

        assert result is True  # Non-critical, always succeeds


class TestCompositeSink:
    """Test CompositeSink."""

    def setup_method(self):
        """Reset dedup cache before each test."""
        reset_dedup_cache_for_tests()

    def test_all_sinks_succeed(self):
        """Test when all sinks succeed."""
        # Create sink with just diagnostics (no DB dependencies)
        sink = CompositeSink([DiagnosticsSink()])

        evidence = UsageEvidence(
            input_tokens=100,
            output_tokens=50,
            provider="openai",
            session_id="sess-123",
        )

        result = sink.consume(evidence)

        assert result is True

    def test_partial_failure(self):
        """Test handling partial failures."""
        # Create a failing sink
        class FailingSink:
            def consume(self, evidence):
                return False  # Always fails

        sink = CompositeSink([DiagnosticsSink(), FailingSink()])

        evidence = UsageEvidence(
            input_tokens=100,
            output_tokens=50,
            provider="openai",
            session_id="sess-123",
        )

        result = sink.consume(evidence)

        assert result is False  # At least one failed

    def test_exception_in_sink(self):
        """Test handling exceptions in sinks."""
        # Create a sink that throws
        class ThrowingSink:
            def consume(self, evidence):
                raise RuntimeError("Test error")

        sink = CompositeSink([DiagnosticsSink(), ThrowingSink()])

        evidence = UsageEvidence(
            input_tokens=100,
            output_tokens=50,
            provider="openai",
            session_id="sess-123",
        )

        result = sink.consume(evidence)

        assert result is False  # Exception caught and logged


class TestUsageEvidenceFlow:
    """Test end-to-end flow from evidence creation to sink consumption."""

    def setup_method(self):
        """Reset dedup cache before each test."""
        reset_dedup_cache_for_tests()

    def test_openai_chat_flow(self):
        """Test OpenAI Chat response flow."""
        # Create evidence from OpenAI Chat response
        response_data = {
            "id": "chatcmpl-123",
            "model": "gpt-4",
            "usage": {"prompt_tokens": 500, "completion_tokens": 100},
        }

        evidence = UsageEvidence.from_openai_chat_response(
            response_data,
            provider="openai",
            session_id="sess-456",
            user_id=10,
            tenant_id=5,
        )

        # Verify evidence
        assert evidence.input_tokens == 500
        assert evidence.output_tokens == 100
        assert evidence.provider == "openai"
        assert evidence.protocol == "openai_chat"
        assert evidence.is_final is True

        # Consume through diagnostics sink
        sink = DiagnosticsSink()
        result = sink.consume(evidence)
        assert result is True

    def test_anthropic_response_flow(self):
        """Test Anthropic Messages response flow."""
        # Create evidence from Anthropic response
        response_data = {
            "id": "msg-123",
            "model": "claude-3-opus",
            "usage": {
                "input_tokens": 1000,
                "output_tokens": 500,
                "cache_read_input_tokens": 200,
                "cache_creation_input_tokens": 100,
            },
        }

        evidence = UsageEvidence.from_anthropic_response(
            response_data,
            session_id="sess-789",
            user_id=15,
            tenant_id=3,
            api_version="2023-06-01",
        )

        # Verify evidence
        assert evidence.input_tokens == 1000
        assert evidence.output_tokens == 500
        assert evidence.cache_read_tokens == 200
        assert evidence.cache_write_tokens == 100
        assert evidence.provider == "anthropic"
        assert evidence.protocol == "anthropic_messages"

        # Verify token calculations
        assert evidence.effective_quota_tokens() == 1000 + 500 - 200
        assert evidence.effective_cost_tokens() == 1000 + 500 + 100
        assert evidence.total_session_tokens() == 1500

        # Consume through diagnostics sink
        sink = DiagnosticsSink()
        result = sink.consume(evidence)
        assert result is True

    def test_gateway_response_flow(self):
        """Test Gateway response flow."""
        # Create evidence from Gateway response
        response_data = {
            "id": "gateway-123",
            "model": "custom-model",
            "usage": {"prompt_tokens": 300, "completion_tokens": 150},
        }

        evidence = UsageEvidence.from_gateway_response(
            response_data,
            provider="custom-gateway",
            session_id="sess-abc",
            user_id=20,
            tenant_id=10,
        )

        # Verify evidence
        assert evidence.input_tokens == 300
        assert evidence.output_tokens == 150
        assert evidence.provider == "custom-gateway"
        assert evidence.protocol == "gateway_openai"

        # Consume through composite sink
        sink = CompositeSink([DiagnosticsSink(), StatsSink()])
        result = sink.consume(evidence)
        assert result is True