"""Unit tests for Usage, DailyUsage, and UsageSummary models."""

from datetime import datetime

import pytest

from app.models.usage import DailyUsage, Usage, UsageSummary


class TestUsage:
    """Test Usage dataclass."""

    def test_create_with_required_fields(self):
        u = Usage(date="2025-01-01", tool_name="qwen")
        assert u.date == "2025-01-01"
        assert u.tool_name == "qwen"
        assert u.host_name == "localhost"
        assert u.tokens_used == 0
        assert u.input_tokens == 0
        assert u.output_tokens == 0
        assert u.cache_tokens == 0
        assert u.request_count == 0
        assert u.models_used is None
        assert u.id is None
        assert u.created_at is None

    def test_create_with_all_fields(self):
        now = datetime(2025, 6, 15, 10, 0, 0)
        u = Usage(
            date="2025-06-15",
            tool_name="claude",
            host_name="prod-server",
            tokens_used=5000,
            input_tokens=2000,
            output_tokens=3000,
            cache_tokens=500,
            request_count=100,
            models_used=["claude-sonnet-4-20250514", "claude-3-5-haiku-20241022"],
            id=42,
            created_at=now,
        )
        assert u.host_name == "prod-server"
        assert u.tokens_used == 5000
        assert u.input_tokens == 2000
        assert u.output_tokens == 3000
        assert u.cache_tokens == 500
        assert u.request_count == 100
        assert u.models_used == ["claude-sonnet-4-20250514", "claude-3-5-haiku-20241022"]
        assert u.id == 42
        assert u.created_at == now

    def test_to_dict(self):
        now = datetime(2025, 3, 10, 14, 30, 0)
        u = Usage(
            date="2025-03-10",
            tool_name="openclaw",
            tokens_used=1000,
            input_tokens=400,
            output_tokens=600,
            cache_tokens=100,
            request_count=25,
            models_used=["gpt-4"],
            id=7,
            created_at=now,
        )
        d = u.to_dict()
        assert d["id"] == 7
        assert d["date"] == "2025-03-10"
        assert d["tool_name"] == "openclaw"
        assert d["host_name"] == "localhost"
        assert d["tokens_used"] == 1000
        assert d["input_tokens"] == 400
        assert d["output_tokens"] == 600
        assert d["cache_tokens"] == 100
        assert d["request_count"] == 25
        assert d["models_used"] == ["gpt-4"]
        assert d["created_at"] == "2025-03-10T14:30:00"

    def test_to_dict_none_created_at(self):
        u = Usage(date="2025-01-01", tool_name="qwen")
        d = u.to_dict()
        assert d["created_at"] is None

    def test_to_dict_none_models_used(self):
        u = Usage(date="2025-01-01", tool_name="qwen")
        d = u.to_dict()
        assert d["models_used"] is None

    def test_from_dict(self):
        data = {
            "id": 15,
            "date": "2025-07-20",
            "tool_name": "claude",
            "host_name": "myhost",
            "tokens_used": 8000,
            "input_tokens": 3000,
            "output_tokens": 5000,
            "cache_tokens": 200,
            "request_count": 80,
            "models_used": ["claude-sonnet-4-20250514"],
            "created_at": "2025-07-20T09:00:00",
        }
        u = Usage.from_dict(data)
        assert u.id == 15
        assert u.date == "2025-07-20"
        assert u.tool_name == "claude"
        assert u.host_name == "myhost"
        assert u.tokens_used == 8000
        assert u.input_tokens == 3000
        assert u.output_tokens == 5000
        assert u.cache_tokens == 200
        assert u.request_count == 80
        assert u.models_used == ["claude-sonnet-4-20250514"]
        assert u.created_at == datetime(2025, 7, 20, 9, 0, 0)

    def test_from_dict_defaults(self):
        data = {}
        u = Usage.from_dict(data)
        assert u.date == ""
        assert u.tool_name == ""
        assert u.host_name == "localhost"
        assert u.tokens_used == 0
        assert u.input_tokens == 0
        assert u.output_tokens == 0
        assert u.cache_tokens == 0
        assert u.request_count == 0
        assert u.models_used is None
        assert u.id is None
        assert u.created_at is None

    def test_from_dict_none_created_at(self):
        data = {"date": "2025-01-01", "tool_name": "qwen", "created_at": None}
        u = Usage.from_dict(data)
        assert u.created_at is None

    def test_roundtrip_to_dict_from_dict(self):
        now = datetime(2025, 9, 1, 12, 0, 0)
        original = Usage(
            date="2025-09-01",
            tool_name="qwen",
            host_name="testhost",
            tokens_used=3000,
            input_tokens=1000,
            output_tokens=2000,
            cache_tokens=50,
            request_count=30,
            models_used=["qwen-max"],
            id=99,
            created_at=now,
        )
        d = original.to_dict()
        restored = Usage.from_dict(d)
        assert restored.date == original.date
        assert restored.tool_name == original.tool_name
        assert restored.host_name == original.host_name
        assert restored.tokens_used == original.tokens_used
        assert restored.input_tokens == original.input_tokens
        assert restored.output_tokens == original.output_tokens
        assert restored.cache_tokens == original.cache_tokens
        assert restored.request_count == original.request_count
        assert restored.models_used == original.models_used
        assert restored.id == original.id


class TestDailyUsage:
    """Test DailyUsage dataclass."""

    def test_create_with_required_fields(self):
        du = DailyUsage(date="2025-01-01")
        assert du.date == "2025-01-01"
        assert du.total_tokens == 0
        assert du.total_input_tokens == 0
        assert du.total_output_tokens == 0
        assert du.total_cache_tokens == 0
        assert du.total_requests == 0
        assert du.tools == []

    def test_create_with_values(self):
        tool = Usage(date="2025-01-01", tool_name="qwen", tokens_used=500)
        du = DailyUsage(
            date="2025-06-15",
            total_tokens=10000,
            total_input_tokens=4000,
            total_output_tokens=6000,
            total_cache_tokens=500,
            total_requests=200,
            tools=[tool],
        )
        assert du.total_tokens == 10000
        assert du.total_input_tokens == 4000
        assert du.total_output_tokens == 6000
        assert du.total_cache_tokens == 500
        assert du.total_requests == 200
        assert len(du.tools) == 1

    def test_to_dict(self):
        tool = Usage(date="2025-01-01", tool_name="claude", tokens_used=1000)
        du = DailyUsage(
            date="2025-08-10",
            total_tokens=1000,
            total_input_tokens=400,
            total_output_tokens=600,
            total_cache_tokens=50,
            total_requests=20,
            tools=[tool],
        )
        d = du.to_dict()
        assert d["date"] == "2025-08-10"
        assert d["total_tokens"] == 1000
        assert d["total_input_tokens"] == 400
        assert d["total_output_tokens"] == 600
        assert d["total_cache_tokens"] == 50
        assert d["total_requests"] == 20
        assert len(d["tools"]) == 1
        assert d["tools"][0]["tool_name"] == "claude"

    def test_to_dict_empty_tools(self):
        du = DailyUsage(date="2025-01-01")
        d = du.to_dict()
        assert d["tools"] == []


class TestUsageSummary:
    """Test UsageSummary dataclass."""

    def test_create_with_required_fields(self):
        us = UsageSummary(tool_name="qwen")
        assert us.tool_name == "qwen"
        assert us.total_tokens == 0
        assert us.total_input_tokens == 0
        assert us.total_output_tokens == 0
        assert us.total_cache_tokens == 0
        assert us.total_requests == 0
        assert us.days_active == 0
        assert us.hosts == []
        assert us.models_used == []

    def test_create_with_all_fields(self):
        us = UsageSummary(
            tool_name="claude",
            total_tokens=50000,
            total_input_tokens=20000,
            total_output_tokens=30000,
            total_cache_tokens=5000,
            total_requests=1000,
            days_active=30,
            hosts=["host1", "host2"],
            models_used=["claude-sonnet-4-20250514", "claude-3-5-haiku-20241022"],
        )
        assert us.tool_name == "claude"
        assert us.total_tokens == 50000
        assert us.total_input_tokens == 20000
        assert us.total_output_tokens == 30000
        assert us.total_cache_tokens == 5000
        assert us.total_requests == 1000
        assert us.days_active == 30
        assert us.hosts == ["host1", "host2"]
        assert us.models_used == ["claude-sonnet-4-20250514", "claude-3-5-haiku-20241022"]

    def test_to_dict(self):
        us = UsageSummary(
            tool_name="openclaw",
            total_tokens=20000,
            total_requests=500,
            days_active=15,
            hosts=["server1"],
            models_used=["gpt-4"],
        )
        d = us.to_dict()
        assert d["tool_name"] == "openclaw"
        assert d["total_tokens"] == 20000
        assert d["total_input_tokens"] == 0
        assert d["total_output_tokens"] == 0
        assert d["total_cache_tokens"] == 0
        assert d["total_requests"] == 500
        assert d["days_active"] == 15
        assert d["hosts"] == ["server1"]
        assert d["models_used"] == ["gpt-4"]
