"""Unit tests for Usage Evidence Module (Issue #2184)."""

import pytest

from app.modules.workspace.usage_evidence import UsageEvidence


class TestUsageEvidence:
    """Test UsageEvidence dataclass."""

    def test_create_with_defaults(self):
        """Test creating UsageEvidence with default values."""
        evidence = UsageEvidence()
        assert evidence.input_tokens == 0
        assert evidence.output_tokens == 0
        assert evidence.cache_read_tokens is None
        assert evidence.cache_write_tokens is None
        assert evidence.reasoning_tokens is None
        assert evidence.provider == ""
        assert evidence.model is None
        assert evidence.protocol == ""
        assert evidence.is_final is True
        assert evidence.is_indeterminate is False
        assert evidence.is_merged is False
        assert evidence.parse_status == "success"
        assert evidence.session_id == ""
        assert evidence.user_id == 0
        assert evidence.tenant_id == 0

    def test_create_with_all_fields(self):
        """Test creating UsageEvidence with all fields."""
        evidence = UsageEvidence(
            input_tokens=1000,
            output_tokens=500,
            cache_read_tokens=200,
            cache_write_tokens=100,
            reasoning_tokens=50,
            provider="anthropic",
            model="claude-3-opus",
            protocol="anthropic_messages",
            api_version="2023-06-01",
            is_final=True,
            raw_usage={"input_tokens": 1000, "output_tokens": 500},
            parse_status="success",
            request_id="req-123",
            session_id="sess-456",
            user_id=10,
            tenant_id=5,
        )
        assert evidence.input_tokens == 1000
        assert evidence.output_tokens == 500
        assert evidence.cache_read_tokens == 200
        assert evidence.cache_write_tokens == 100
        assert evidence.reasoning_tokens == 50
        assert evidence.provider == "anthropic"
        assert evidence.model == "claude-3-opus"
        assert evidence.protocol == "anthropic_messages"
        assert evidence.api_version == "2023-06-01"
        assert evidence.is_final is True
        assert evidence.parse_status == "success"
        assert evidence.request_id == "req-123"
        assert evidence.session_id == "sess-456"
        assert evidence.user_id == 10
        assert evidence.tenant_id == 5

    def test_effective_quota_tokens(self):
        """Test quota token calculation (no double-charge for cache reads)."""
        evidence = UsageEvidence(
            input_tokens=1000,
            output_tokens=500,
            cache_read_tokens=200,
        )
        # quota = input + output - cache_read
        assert evidence.effective_quota_tokens() == 1000 + 500 - 200

    def test_effective_quota_tokens_no_cache(self):
        """Test quota calculation without cache tokens."""
        evidence = UsageEvidence(input_tokens=1000, output_tokens=500)
        assert evidence.effective_quota_tokens() == 1500

    def test_effective_cost_tokens(self):
        """Test cost token calculation (include cache write cost)."""
        evidence = UsageEvidence(
            input_tokens=1000,
            output_tokens=500,
            cache_write_tokens=100,
        )
        # cost = input + output + cache_write
        assert evidence.effective_cost_tokens() == 1000 + 500 + 100

    def test_total_session_tokens(self):
        """Test session token calculation (record all tokens)."""
        evidence = UsageEvidence(
            input_tokens=1000,
            output_tokens=500,
            cache_read_tokens=200,
            cache_write_tokens=100,
        )
        # session = input + output (no cache adjustments)
        assert evidence.total_session_tokens() == 1500

    def test_merge_with_basic(self):
        """Test merging two UsageEvidence instances."""
        ev1 = UsageEvidence(
            input_tokens=1000,
            output_tokens=100,
            cache_read_tokens=200,
            provider="anthropic",
            session_id="sess-123",
            user_id=10,
            tenant_id=5,
        )
        ev2 = UsageEvidence(
            input_tokens=0,  # Don't accumulate input
            output_tokens=50,
            cache_read_tokens=100,
            provider="anthropic",
            session_id="sess-123",
            user_id=10,
            tenant_id=5,
            is_final=True,
        )

        merged = ev1.merge_with(ev2)

        assert merged.input_tokens == 1000  # Keep original input
        assert merged.output_tokens == 150  # Accumulate output
        assert merged.cache_read_tokens == 300  # Accumulate cache
        assert merged.is_merged is True
        assert merged.is_final is True

    def test_from_openai_chat_response(self):
        """Test creating UsageEvidence from OpenAI Chat response."""
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

        assert evidence.input_tokens == 500
        assert evidence.output_tokens == 100
        assert evidence.provider == "openai"
        assert evidence.model == "gpt-4"
        assert evidence.protocol == "openai_chat"
        assert evidence.is_final is True
        assert evidence.request_id == "chatcmpl-123"

    def test_from_anthropic_response(self):
        """Test creating UsageEvidence from Anthropic Messages response."""
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

        assert evidence.input_tokens == 1000
        assert evidence.output_tokens == 500
        assert evidence.cache_read_tokens == 200
        assert evidence.cache_write_tokens == 100
        assert evidence.provider == "anthropic"
        assert evidence.model == "claude-3-opus"
        assert evidence.protocol == "anthropic_messages"
        assert evidence.api_version == "2023-06-01"
        assert evidence.is_final is True

    def test_create_empty(self):
        """Test creating empty/malformed UsageEvidence."""
        evidence = UsageEvidence.create_empty(
            provider="unknown",
            protocol="unknown",
            session_id="sess-xyz",
            user_id=1,
            tenant_id=1,
            parse_status="malformed",
            parse_diagnostics={"error": "missing_usage"},
        )

        assert evidence.input_tokens == 0
        assert evidence.output_tokens == 0
        assert evidence.provider == "unknown"
        assert evidence.protocol == "unknown"
        assert evidence.is_final is False
        assert evidence.is_indeterminate is True
        assert evidence.parse_status == "malformed"
        assert evidence.parse_diagnostics == {"error": "missing_usage"}

    def test_to_dict(self):
        """Test serializing to dictionary."""
        evidence = UsageEvidence(
            input_tokens=100,
            output_tokens=50,
            provider="openai",
            model="gpt-4",
            protocol="openai_chat",
            session_id="sess-123",
            user_id=10,
            tenant_id=5,
        )

        d = evidence.to_dict()

        assert d["input_tokens"] == 100
        assert d["output_tokens"] == 50
        assert d["provider"] == "openai"
        assert d["model"] == "gpt-4"
        assert d["protocol"] == "openai_chat"
        assert d["session_id"] == "sess-123"
        assert d["effective_quota_tokens"] == 150
        assert d["effective_cost_tokens"] == 150
        assert d["total_session_tokens"] == 150
