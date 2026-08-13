"""
Unit tests for file change parser configuration.

Issue #2589: File change panel configuration for detecting file/folder operations.
"""

import os
import tempfile
import time
from pathlib import Path

import pytest
import yaml


class TestAsyncParsingConfig:
    """Tests for AsyncParsingConfig validation."""

    def test_default_values(self):
        """Test default configuration values."""
        from app.utils.file_change_config import AsyncParsingConfig

        config = AsyncParsingConfig()
        assert config.enabled is True
        assert config.thread_pool_size == 2
        assert config.timeout_seconds == 300
        assert config.max_retries == 3

    def test_valid_config(self):
        """Test valid configuration passes validation."""
        from app.utils.file_change_config import AsyncParsingConfig

        config = AsyncParsingConfig(
            enabled=True,
            thread_pool_size=5,
            timeout_seconds=100,
            max_retries=2,
        )
        errors = config.validate()
        assert errors == []

    def test_invalid_thread_pool_size_low(self):
        """Test thread_pool_size below minimum."""
        from app.utils.file_change_config import AsyncParsingConfig

        config = AsyncParsingConfig(thread_pool_size=0)
        errors = config.validate()
        assert len(errors) == 1
        assert "thread_pool_size" in errors[0]

    def test_invalid_thread_pool_size_high(self):
        """Test thread_pool_size above maximum."""
        from app.utils.file_change_config import AsyncParsingConfig

        config = AsyncParsingConfig(thread_pool_size=20)
        errors = config.validate()
        assert len(errors) == 1
        assert "thread_pool_size" in errors[0]

    def test_invalid_timeout_low(self):
        """Test timeout_seconds below minimum."""
        from app.utils.file_change_config import AsyncParsingConfig

        config = AsyncParsingConfig(timeout_seconds=5)
        errors = config.validate()
        assert any("timeout_seconds" in e for e in errors)

    def test_invalid_timeout_high(self):
        """Test timeout_seconds above maximum."""
        from app.utils.file_change_config import AsyncParsingConfig

        config = AsyncParsingConfig(timeout_seconds=5000)
        errors = config.validate()
        assert any("timeout_seconds" in e for e in errors)

    def test_pending_timeout_exceeds_timeout(self):
        """Test pending_result_timeout_seconds must be less than timeout_seconds."""
        from app.utils.file_change_config import AsyncParsingConfig

        config = AsyncParsingConfig(
            pending_result_timeout_seconds=100,
            timeout_seconds=50,
        )
        errors = config.validate()
        assert any("pending_result_timeout_seconds" in e for e in errors)

    def test_max_retries_range(self):
        """Test max_retries validation."""
        from app.utils.file_change_config import AsyncParsingConfig

        # Valid values
        for retries in [0, 5, 10]:
            config = AsyncParsingConfig(max_retries=retries)
            assert not any("max_retries" in e for e in config.validate())

        # Invalid values
        for retries in [-1, 11]:
            config = AsyncParsingConfig(max_retries=retries)
            assert any("max_retries" in e for e in config.validate())


class TestParserConfig:
    """Tests for ParserConfig validation."""

    def test_default_values(self):
        """Test default configuration values."""
        from app.utils.file_change_config import ParserConfig

        config = ParserConfig()
        assert config.enabled is True
        assert config.timeout_ms == 50

    def test_valid_timeout(self):
        """Test valid timeout_ms values."""
        from app.utils.file_change_config import ParserConfig

        for timeout in [10, 100, 500, 1000]:
            config = ParserConfig(timeout_ms=timeout)
            assert config.validate() == []

    def test_invalid_timeout_low(self):
        """Test timeout_ms below minimum."""
        from app.utils.file_change_config import ParserConfig

        config = ParserConfig(timeout_ms=5)
        errors = config.validate()
        assert len(errors) == 1
        assert "timeout_ms" in errors[0]

    def test_invalid_timeout_high(self):
        """Test timeout_ms above maximum."""
        from app.utils.file_change_config import ParserConfig

        config = ParserConfig(timeout_ms=2000)
        errors = config.validate()
        assert len(errors) == 1
        assert "timeout_ms" in errors[0]


class TestPathValidationConfig:
    """Tests for PathValidationConfig validation."""

    def test_default_values(self):
        """Test default configuration values."""
        from app.utils.file_change_config import PathValidationConfig

        config = PathValidationConfig()
        assert config.allow_symlinks is False
        assert config.max_path_length == 4096
        assert config.symlink_cache_ttl_seconds == 60

    def test_valid_config(self):
        """Test valid configuration."""
        from app.utils.file_change_config import PathValidationConfig

        config = PathValidationConfig(
            allow_symlinks=True,
            max_path_length=1024,
            symlink_cache_ttl_seconds=120,
        )
        assert config.validate() == []

    def test_invalid_max_path_length(self):
        """Test max_path_length validation."""
        from app.utils.file_change_config import PathValidationConfig

        # Too low
        config = PathValidationConfig(max_path_length=100)
        assert any("max_path_length" in e for e in config.validate())

        # Too high
        config = PathValidationConfig(max_path_length=10000)
        assert any("max_path_length" in e for e in config.validate())

    def test_invalid_symlink_cache_ttl(self):
        """Test symlink_cache_ttl_seconds validation."""
        from app.utils.file_change_config import PathValidationConfig

        # Too low
        config = PathValidationConfig(symlink_cache_ttl_seconds=5)
        assert any("symlink_cache_ttl_seconds" in e for e in config.validate())

        # Too high
        config = PathValidationConfig(symlink_cache_ttl_seconds=500)
        assert any("symlink_cache_ttl_seconds" in e for e in config.validate())


class TestConsistencyCheckConfig:
    """Tests for ConsistencyCheckConfig validation."""

    def test_default_values(self):
        """Test default configuration values."""
        from app.utils.file_change_config import ConsistencyCheckConfig

        config = ConsistencyCheckConfig()
        assert config.enabled is True
        assert config.interval_minutes == 60
        assert config.parse_failure_retention_days == 30

    def test_valid_config(self):
        """Test valid configuration."""
        from app.utils.file_change_config import ConsistencyCheckConfig

        config = ConsistencyCheckConfig(
            enabled=True,
            interval_minutes=120,
            parse_failure_retention_days=60,
            cleanup_interval_hours=48,
        )
        assert config.validate() == []

    def test_invalid_interval_minutes(self):
        """Test interval_minutes validation."""
        from app.utils.file_change_config import ConsistencyCheckConfig

        config = ConsistencyCheckConfig(interval_minutes=5)
        assert any("interval_minutes" in e for e in config.validate())

        config = ConsistencyCheckConfig(interval_minutes=2000)
        assert any("interval_minutes" in e for e in config.validate())

    def test_invalid_retention_days(self):
        """Test parse_failure_retention_days validation."""
        from app.utils.file_change_config import ConsistencyCheckConfig

        config = ConsistencyCheckConfig(parse_failure_retention_days=1)
        assert any("parse_failure_retention_days" in e for e in config.validate())

        config = ConsistencyCheckConfig(parse_failure_retention_days=500)
        assert any("parse_failure_retention_days" in e for e in config.validate())


class TestAlertRuleConfig:
    """Tests for AlertRuleConfig validation."""

    def test_default_values(self):
        """Test default configuration values."""
        from app.utils.file_change_config import AlertRuleConfig

        config = AlertRuleConfig()
        assert config.enabled is True
        assert config.severity == "warning"
        assert config.notifications == ["log"]

    def test_valid_severity(self):
        """Test valid severity values."""
        from app.utils.file_change_config import AlertRuleConfig

        for severity in ["warning", "critical"]:
            config = AlertRuleConfig(severity=severity)
            assert not any("severity" in e for e in config.validate())

    def test_invalid_severity(self):
        """Test invalid severity values."""
        from app.utils.file_change_config import AlertRuleConfig

        config = AlertRuleConfig(severity="error")
        errors = config.validate()
        assert any("severity" in e for e in errors)

    def test_valid_notifications(self):
        """Test valid notification methods."""
        from app.utils.file_change_config import AlertRuleConfig

        config = AlertRuleConfig(notifications=["log", "email", "sms"])
        assert not any("notification" in e for e in config.validate())

    def test_invalid_notification(self):
        """Test invalid notification methods."""
        from app.utils.file_change_config import AlertRuleConfig

        config = AlertRuleConfig(notifications=["log", "invalid"])
        errors = config.validate()
        assert any("notification" in e for e in errors)


class TestFileChangeParserConfig:
    """Tests for FileChangeParserConfig."""

    def test_default_values(self):
        """Test default configuration values."""
        from app.utils.file_change_config import FileChangeParserConfig

        config = FileChangeParserConfig()
        assert config.version == "1.0"
        assert config.async_parsing.enabled is True
        assert config.parsers.mkdir.enabled is True

    def test_from_dict_empty(self):
        """Test creating config from empty dict."""
        from app.utils.file_change_config import FileChangeParserConfig

        config = FileChangeParserConfig.from_dict({})
        assert config.version == "1.0"
        assert config.async_parsing.enabled is True

    def test_from_dict_full(self):
        """Test creating config from full dict."""
        from app.utils.file_change_config import FileChangeParserConfig

        data = {
            "async_parsing": {
                "enabled": False,
                "thread_pool_size": 4,
            },
            "path_validation": {
                "allow_symlinks": True,
            },
            "version": "2.0",
        }
        config = FileChangeParserConfig.from_dict(data)
        assert config.async_parsing.enabled is False
        assert config.async_parsing.thread_pool_size == 4
        assert config.path_validation.allow_symlinks is True
        assert config.version == "2.0"

    def test_validate_all(self):
        """Test full configuration validation."""
        from app.utils.file_change_config import FileChangeParserConfig

        config = FileChangeParserConfig()
        errors = config.validate()
        assert errors == []

    def test_validate_propagates_errors(self):
        """Test that validation propagates nested errors."""
        from app.utils.file_change_config import AsyncParsingConfig, FileChangeParserConfig

        config = FileChangeParserConfig()
        config.async_parsing = AsyncParsingConfig(thread_pool_size=100)
        errors = config.validate()
        assert any("async_parsing" in e for e in errors)


class TestEnvOverrides:
    """Tests for environment variable overrides."""

    def test_async_parsing_enabled_override(self, monkeypatch):
        """Test FILE_CHANGE_ASYNC_PARSING_ENABLED override."""
        from app.utils.file_change_config import (
            get_file_change_parser_config,
            invalidate_config_cache,
        )

        invalidate_config_cache()
        monkeypatch.setenv("FILE_CHANGE_ASYNC_PARSING_ENABLED", "false")

        config = get_file_change_parser_config(use_cache=False)
        assert config.async_parsing.enabled is False

    def test_thread_pool_size_override(self, monkeypatch):
        """Test FILE_CHANGE_ASYNC_PARSING_THREAD_POOL_SIZE override."""
        from app.utils.file_change_config import (
            get_file_change_parser_config,
            invalidate_config_cache,
        )

        invalidate_config_cache()
        monkeypatch.setenv("FILE_CHANGE_ASYNC_PARSING_THREAD_POOL_SIZE", "8")

        config = get_file_change_parser_config(use_cache=False)
        assert config.async_parsing.thread_pool_size == 8

    def test_invalid_thread_pool_size_handled(self, monkeypatch):
        """Test invalid thread pool size is handled gracefully."""
        from app.utils.file_change_config import (
            get_file_change_parser_config,
            invalidate_config_cache,
        )

        invalidate_config_cache()
        monkeypatch.setenv("FILE_CHANGE_ASYNC_PARSING_THREAD_POOL_SIZE", "invalid")

        # Should not raise, just log warning and use default
        config = get_file_change_parser_config(use_cache=False)
        assert config.async_parsing.thread_pool_size == 2  # default

    def test_path_validation_allow_symlinks_override(self, monkeypatch):
        """Test FILE_CHANGE_PATH_VALIDATION_ALLOW_SYMLINKS override."""
        from app.utils.file_change_config import (
            get_file_change_parser_config,
            invalidate_config_cache,
        )

        invalidate_config_cache()
        monkeypatch.setenv("FILE_CHANGE_PATH_VALIDATION_ALLOW_SYMLINKS", "true")

        config = get_file_change_parser_config(use_cache=False)
        assert config.path_validation.allow_symlinks is True

    def test_consistency_check_enabled_override(self, monkeypatch):
        """Test FILE_CHANGE_CONSISTENCY_CHECK_ENABLED override."""
        from app.utils.file_change_config import (
            get_file_change_parser_config,
            invalidate_config_cache,
        )

        invalidate_config_cache()
        monkeypatch.setenv("FILE_CHANGE_CONSISTENCY_CHECK_ENABLED", "false")

        config = get_file_change_parser_config(use_cache=False)
        assert config.consistency_check.enabled is False


class TestConfigLoading:
    """Tests for configuration file loading."""

    def test_load_missing_file(self):
        """Test loading missing config file returns defaults."""
        from app.utils.file_change_config import (
            get_file_change_parser_config,
            invalidate_config_cache,
        )

        invalidate_config_cache()
        config = get_file_change_parser_config(
            config_path=Path("/nonexistent/config.yaml"),
            use_cache=False,
        )
        assert config.async_parsing.enabled is True  # default

    def test_load_valid_yaml(self, tmp_path):
        """Test loading valid YAML config file."""
        from app.utils.file_change_config import (
            get_file_change_parser_config,
            invalidate_config_cache,
        )

        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
async_parsing:
  enabled: false
  thread_pool_size: 4
path_validation:
  allow_symlinks: true
""")

        invalidate_config_cache()
        config = get_file_change_parser_config(
            config_path=config_file,
            use_cache=False,
        )
        assert config.async_parsing.enabled is False
        assert config.async_parsing.thread_pool_size == 4
        assert config.path_validation.allow_symlinks is True

    def test_load_invalid_yaml(self, tmp_path):
        """Test loading invalid YAML returns defaults."""
        from app.utils.file_change_config import (
            get_file_change_parser_config,
            invalidate_config_cache,
        )

        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
invalid yaml content: [
  unclosed bracket
""")

        invalidate_config_cache()
        config = get_file_change_parser_config(
            config_path=config_file,
            use_cache=False,
        )
        # Should return defaults on parse error
        assert config.async_parsing.enabled is True


class TestConfigCache:
    """Tests for configuration caching."""

    def test_cache_works(self):
        """Test that cache returns same instance within TTL."""
        from app.utils.file_change_config import (
            get_file_change_parser_config,
            invalidate_config_cache,
        )

        invalidate_config_cache()
        config1 = get_file_change_parser_config(use_cache=True)
        config2 = get_file_change_parser_config(use_cache=True)

        # Should be same instance from cache
        assert config1 is config2

    def test_cache_bypass(self):
        """Test that use_cache=False bypasses cache."""
        from app.utils.file_change_config import (
            get_file_change_parser_config,
            invalidate_config_cache,
        )

        invalidate_config_cache()
        config1 = get_file_change_parser_config(use_cache=False)
        config2 = get_file_change_parser_config(use_cache=False)

        # Should be different instances
        assert config1 is not config2

    def test_invalidate_cache(self):
        """Test that invalidate_config_cache clears cache."""
        from app.utils.file_change_config import (
            get_file_change_parser_config,
            invalidate_config_cache,
        )

        invalidate_config_cache()
        config1 = get_file_change_parser_config(use_cache=True)
        invalidate_config_cache()
        config2 = get_file_change_parser_config(use_cache=True)

        # Should be different instances after invalidation
        assert config1 is not config2
