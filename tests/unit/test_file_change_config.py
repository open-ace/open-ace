"""Unit tests for file_change_config module.

Tests for configuration loading, validation, environment variable overrides,
and caching functionality.

Issue #2589: File change panel configuration for detecting file/folder operations.
"""

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from app.utils.file_change_config import (
    AlertRuleConfig,
    AlertRulesConfig,
    AsyncParsingConfig,
    CleanupConfig,
    ConsistencyCheckConfig,
    FileChangeParserConfig,
    ParserConfig,
    ParsersConfig,
    PathValidationConfig,
    RedisConfig,
    get_file_change_parser_config,
    invalidate_config_cache,
)


class TestAsyncParsingConfig:
    """Tests for AsyncParsingConfig validation."""

    def test_valid_defaults(self):
        """Test default values are valid."""
        config = AsyncParsingConfig()
        errors = config.validate()
        assert errors == []

    def test_invalid_thread_pool_size_low(self):
        """Test thread_pool_size below minimum."""
        config = AsyncParsingConfig(thread_pool_size=0)
        errors = config.validate()
        assert len(errors) == 1
        assert "thread_pool_size must be in [1, 10]" in errors[0]

    def test_invalid_thread_pool_size_high(self):
        """Test thread_pool_size above maximum."""
        config = AsyncParsingConfig(thread_pool_size=11)
        errors = config.validate()
        assert len(errors) == 1
        assert "thread_pool_size must be in [1, 10]" in errors[0]

    def test_invalid_timeout_seconds_low(self):
        """Test timeout_seconds below minimum."""
        config = AsyncParsingConfig(timeout_seconds=5)
        errors = config.validate()
        assert any("timeout_seconds must be in [10, 3600]" in e for e in errors)

    def test_invalid_pending_result_timeout(self):
        """Test pending_result_timeout_seconds must be less than timeout_seconds."""
        config = AsyncParsingConfig(
            timeout_seconds=30, pending_result_timeout_seconds=30
        )
        errors = config.validate()
        assert any("must be less than timeout_seconds" in e for e in errors)

    def test_multiple_validation_errors(self):
        """Test multiple validation errors are returned."""
        config = AsyncParsingConfig(
            thread_pool_size=0, timeout_seconds=5, max_retries=-1
        )
        errors = config.validate()
        assert len(errors) >= 3


class TestParserConfig:
    """Tests for ParserConfig validation."""

    def test_valid_defaults(self):
        """Test default values are valid."""
        config = ParserConfig()
        errors = config.validate()
        assert errors == []

    def test_invalid_timeout_ms_low(self):
        """Test timeout_ms below minimum."""
        config = ParserConfig(timeout_ms=5)
        errors = config.validate()
        assert len(errors) == 1
        assert "timeout_ms must be in [10, 1000]" in errors[0]

    def test_invalid_timeout_ms_high(self):
        """Test timeout_ms above maximum."""
        config = ParserConfig(timeout_ms=2000)
        errors = config.validate()
        assert len(errors) == 1
        assert "timeout_ms must be in [10, 1000]" in errors[0]


class TestParsersConfig:
    """Tests for ParsersConfig validation."""

    def test_valid_defaults(self):
        """Test default values are valid."""
        config = ParsersConfig()
        errors = config.validate()
        assert errors == []

    def test_invalid_parser_propagates_error(self):
        """Test invalid parser config propagates errors."""
        config = ParsersConfig(mkdir=ParserConfig(timeout_ms=5))
        errors = config.validate()
        assert len(errors) == 1
        assert "mkdir:" in errors[0]


class TestPathValidationConfig:
    """Tests for PathValidationConfig validation."""

    def test_valid_defaults(self):
        """Test default values are valid."""
        config = PathValidationConfig()
        errors = config.validate()
        assert errors == []

    def test_invalid_max_path_length_low(self):
        """Test max_path_length below minimum."""
        config = PathValidationConfig(max_path_length=100)
        errors = config.validate()
        assert len(errors) == 1
        assert "max_path_length must be in [256, 8192]" in errors[0]

    def test_invalid_symlink_cache_ttl(self):
        """Test symlink_cache_ttl_seconds out of range."""
        config = PathValidationConfig(symlink_cache_ttl_seconds=5)
        errors = config.validate()
        assert any("symlink_cache_ttl_seconds must be in [10, 300]" in e for e in errors)


class TestConsistencyCheckConfig:
    """Tests for ConsistencyCheckConfig validation."""

    def test_valid_defaults(self):
        """Test default values are valid."""
        config = ConsistencyCheckConfig()
        errors = config.validate()
        assert errors == []

    def test_invalid_interval_minutes_low(self):
        """Test interval_minutes below minimum."""
        config = ConsistencyCheckConfig(interval_minutes=5)
        errors = config.validate()
        assert any("interval_minutes must be in [10, 1440]" in e for e in errors)

    def test_invalid_retention_days(self):
        """Test parse_failure_retention_days out of range."""
        config = ConsistencyCheckConfig(parse_failure_retention_days=5)
        errors = config.validate()
        assert any("parse_failure_retention_days must be in [7, 365]" in e for e in errors)


class TestAlertRuleConfig:
    """Tests for AlertRuleConfig validation."""

    def test_valid_defaults(self):
        """Test default values are valid."""
        config = AlertRuleConfig()
        errors = config.validate()
        assert errors == []

    def test_invalid_severity(self):
        """Test invalid severity value."""
        config = AlertRuleConfig(severity="invalid")
        errors = config.validate()
        assert any("severity must be 'warning' or 'critical'" in e for e in errors)

    def test_invalid_notification_method(self):
        """Test invalid notification method."""
        config = AlertRuleConfig(notifications=["log", "invalid_method"])
        errors = config.validate()
        assert any("invalid notification method" in e for e in errors)


class TestFileChangeParserConfig:
    """Tests for complete FileChangeParserConfig."""

    def test_valid_defaults(self):
        """Test default configuration is valid."""
        config = FileChangeParserConfig()
        errors = config.validate()
        assert errors == []

    def test_from_dict_basic(self):
        """Test creating config from dictionary."""
        data = {
            "async_parsing": {
                "enabled": True,
                "thread_pool_size": 4,
                "timeout_seconds": 600,
            },
            "version": "2.0",
        }
        config = FileChangeParserConfig.from_dict(data)
        assert config.async_parsing.enabled is True
        assert config.async_parsing.thread_pool_size == 4
        assert config.async_parsing.timeout_seconds == 600
        assert config.version == "2.0"

    def test_from_dict_full_config(self):
        """Test creating config with all sections."""
        data = {
            "async_parsing": {
                "enabled": True,
                "thread_pool_size": 3,
                "timeout_seconds": 400,
                "max_retries": 2,
                "pending_result_timeout_seconds": 40,
                "shutdown_timeout_seconds": 50,
            },
            "parsers": {
                "mkdir": {"enabled": True, "timeout_ms": 100},
                "mv": {"enabled": False, "timeout_ms": 200},
            },
            "path_validation": {
                "allow_symlinks": True,
                "max_path_length": 8192,
                "symlink_cache_ttl_seconds": 120,
            },
            "consistency_check": {
                "enabled": True,
                "interval_minutes": 120,
                "parse_failure_retention_days": 60,
                "cleanup_interval_hours": 48,
            },
            "cleanup": {
                "distributed_lock_enabled": True,
                "redis": {
                    "host": "redis.example.com",
                    "port": 6380,
                    "db": 1,
                    "password": "secret",
                    "lock_timeout_seconds": 600,
                },
            },
            "alert_rules": {
                "parse_success_rate": {
                    "enabled": True,
                    "threshold_percent": 95.0,
                    "severity": "critical",
                    "notifications": ["log", "email", "sms"],
                }
            },
            "version": "1.5",
        }
        config = FileChangeParserConfig.from_dict(data)

        # Verify async_parsing
        assert config.async_parsing.thread_pool_size == 3
        assert config.async_parsing.timeout_seconds == 400

        # Verify parsers
        assert config.parsers.mkdir.enabled is True
        assert config.parsers.mkdir.timeout_ms == 100
        assert config.parsers.mv.enabled is False
        assert config.parsers.mv.timeout_ms == 200

        # Verify path_validation
        assert config.path_validation.allow_symlinks is True
        assert config.path_validation.max_path_length == 8192

        # Verify consistency_check
        assert config.consistency_check.interval_minutes == 120
        assert config.consistency_check.parse_failure_retention_days == 60

        # Verify cleanup
        assert config.cleanup.distributed_lock_enabled is True
        assert config.cleanup.redis.host == "redis.example.com"
        assert config.cleanup.redis.port == 6380

        # Verify alert_rules
        assert config.alert_rules.parse_success_rate.threshold_percent == 95.0

        # Verify version
        assert config.version == "1.5"

    def test_validate_aggregates_all_errors(self):
        """Test validation aggregates errors from all sections."""
        config = FileChangeParserConfig()
        config.async_parsing.thread_pool_size = 0  # Invalid
        config.parsers.mkdir.timeout_ms = 5  # Invalid
        config.path_validation.max_path_length = 100  # Invalid

        errors = config.validate()
        assert len(errors) >= 3
        assert any("async_parsing:" in e for e in errors)
        assert any("parsers:" in e for e in errors)
        assert any("path_validation:" in e for e in errors)


class TestEnvironmentVariableOverrides:
    """Tests for environment variable overrides."""

    def setup_method(self):
        """Clear cache before each test."""
        invalidate_config_cache()

    def teardown_method(self):
        """Clean up environment variables."""
        env_vars = [
            "FILE_CHANGE_ASYNC_PARSING_ENABLED",
            "FILE_CHANGE_ASYNC_PARSING_THREAD_POOL_SIZE",
            "FILE_CHANGE_ASYNC_PARSING_TIMEOUT_SECONDS",
            "FILE_CHANGE_ASYNC_PARSING_MAX_RETRIES",
            "FILE_CHANGE_PATH_VALIDATION_ALLOW_SYMLINKS",
            "FILE_CHANGE_PATH_VALIDATION_MAX_PATH_LENGTH",
            "FILE_CHANGE_CONSISTENCY_CHECK_ENABLED",
        ]
        for var in env_vars:
            os.environ.pop(var, None)

    def test_async_parsing_enabled_override(self):
        """Test FILE_CHANGE_ASYNC_PARSING_ENABLED override."""
        os.environ["FILE_CHANGE_ASYNC_PARSING_ENABLED"] = "false"
        config = get_file_change_parser_config(use_cache=False)
        assert config.async_parsing.enabled is False

    def test_async_parsing_thread_pool_size_override(self):
        """Test FILE_CHANGE_ASYNC_PARSING_THREAD_POOL_SIZE override."""
        os.environ["FILE_CHANGE_ASYNC_PARSING_THREAD_POOL_SIZE"] = "5"
        config = get_file_change_parser_config(use_cache=False)
        assert config.async_parsing.thread_pool_size == 5

    def test_async_parsing_timeout_override(self):
        """Test FILE_CHANGE_ASYNC_PARSING_TIMEOUT_SECONDS override."""
        os.environ["FILE_CHANGE_ASYNC_PARSING_TIMEOUT_SECONDS"] = "600"
        config = get_file_change_parser_config(use_cache=False)
        assert config.async_parsing.timeout_seconds == 600

    def test_path_validation_allow_symlinks_override(self):
        """Test FILE_CHANGE_PATH_VALIDATION_ALLOW_SYMLINKS override."""
        os.environ["FILE_CHANGE_PATH_VALIDATION_ALLOW_SYMLINKS"] = "true"
        config = get_file_change_parser_config(use_cache=False)
        assert config.path_validation.allow_symlinks is True

    def test_path_validation_max_path_length_override(self):
        """Test FILE_CHANGE_PATH_VALIDATION_MAX_PATH_LENGTH override."""
        os.environ["FILE_CHANGE_PATH_VALIDATION_MAX_PATH_LENGTH"] = "8192"
        config = get_file_change_parser_config(use_cache=False)
        assert config.path_validation.max_path_length == 8192

    def test_consistency_check_enabled_override(self):
        """Test FILE_CHANGE_CONSISTENCY_CHECK_ENABLED override."""
        os.environ["FILE_CHANGE_CONSISTENCY_CHECK_ENABLED"] = "false"
        config = get_file_change_parser_config(use_cache=False)
        assert config.consistency_check.enabled is False

    def test_invalid_thread_pool_size_logged(self):
        """Test invalid THREAD_POOL_SIZE logs warning."""
        os.environ["FILE_CHANGE_ASYNC_PARSING_THREAD_POOL_SIZE"] = "invalid"
        with patch("app.utils.file_change_config.logger") as mock_logger:
            config = get_file_change_parser_config(use_cache=False)
            # Should use default value
            assert config.async_parsing.thread_pool_size == 2
            # Should log warning
            mock_logger.warning.assert_called()


class TestConfigurationCaching:
    """Tests for configuration caching."""

    def setup_method(self):
        """Clear cache before each test."""
        invalidate_config_cache()

    def test_cache_returns_same_instance(self):
        """Test caching returns same config instance."""
        config1 = get_file_change_parser_config(use_cache=True)
        config2 = get_file_change_parser_config(use_cache=True)
        assert config1 is config2

    def test_no_cache_returns_different_instances(self):
        """Test use_cache=False returns different instances."""
        config1 = get_file_change_parser_config(use_cache=False)
        config2 = get_file_change_parser_config(use_cache=False)
        assert config1 is not config2

    def test_invalidate_cache_refreshes_config(self):
        """Test invalidating cache refreshes configuration."""
        config1 = get_file_change_parser_config(use_cache=True)

        # Modify environment
        os.environ["FILE_CHANGE_ASYNC_PARSING_THREAD_POOL_SIZE"] = "7"

        # Without invalidation, should return cached instance
        config2 = get_file_change_parser_config(use_cache=True)
        assert config1 is config2

        # Invalidate cache
        invalidate_config_cache()

        # Should load new config
        config3 = get_file_change_parser_config(use_cache=True)
        assert config1 is not config3
        assert config3.async_parsing.thread_pool_size == 7

        # Clean up
        os.environ.pop("FILE_CHANGE_ASYNC_PARSING_THREAD_POOL_SIZE", None)
        invalidate_config_cache()

    def test_cache_expiry(self):
        """Test cache expires after TTL."""
        config1 = get_file_change_parser_config(use_cache=True)

        # Invalidate cache to simulate expiry
        invalidate_config_cache()

        # Get new config - should be different instance
        config2 = get_file_change_parser_config(use_cache=True)
        # Should be different instance due to cache invalidation
        assert config1 is not config2


class TestLoadConfigFile:
    """Tests for configuration file loading."""

    def setup_method(self):
        """Clear cache before each test."""
        invalidate_config_cache()

    def test_missing_config_file_uses_defaults(self):
        """Test missing config file uses defaults."""
        config = get_file_change_parser_config(
            config_path=Path("/nonexistent/config.yaml"), use_cache=False
        )
        # Should have default values
        assert config.async_parsing.enabled is True
        assert config.async_parsing.thread_pool_size == 2
        assert config.version == "1.0"

    def test_invalid_yaml_uses_defaults(self):
        """Test invalid YAML file uses defaults."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write("invalid: yaml: content: [[[[")
            f.flush()

            config = get_file_change_parser_config(
                config_path=Path(f.name), use_cache=False
            )
            # Should have default values
            assert config.async_parsing.enabled is True

            os.unlink(f.name)

    def test_valid_yaml_file(self):
        """Test loading from valid YAML file."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write("""
async_parsing:
  enabled: false
  thread_pool_size: 4
version: "2.0"
""")
            f.flush()

            config = get_file_change_parser_config(
                config_path=Path(f.name), use_cache=False
            )
            assert config.async_parsing.enabled is False
            assert config.async_parsing.thread_pool_size == 4
            assert config.version == "2.0"

            os.unlink(f.name)
