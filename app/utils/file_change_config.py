"""
File Change Parser Configuration.

Configuration loader and validator for the file change parsing system.
Supports YAML configuration file and environment variable overrides.

Issue #2589: File change panel configuration for detecting file/folder operations.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

# Default configuration file path
DEFAULT_CONFIG_PATH = Path(__file__).parent.parent.parent / "config" / "file_change_parser.yaml"

# Configuration cache with TTL
_config_lock = threading.Lock()
_config_cache: tuple[float, FileChangeParserConfig] | None = None
_config_ttl: float = 60.0  # seconds


@dataclass
class AsyncParsingConfig:
    """Asynchronous parsing configuration."""

    enabled: bool = True
    thread_pool_size: int = 2
    timeout_seconds: int = 300
    max_retries: int = 3
    pending_result_timeout_seconds: int = 30
    shutdown_timeout_seconds: int = 30

    def validate(self) -> list[str]:
        """Validate configuration values. Returns list of error messages."""
        errors = []

        if not 1 <= self.thread_pool_size <= 10:
            errors.append(f"thread_pool_size must be in [1, 10], got {self.thread_pool_size}")

        if not 10 <= self.timeout_seconds <= 3600:
            errors.append(f"timeout_seconds must be in [10, 3600], got {self.timeout_seconds}")

        if not 0 <= self.max_retries <= 10:
            errors.append(f"max_retries must be in [0, 10], got {self.max_retries}")

        if not 10 <= self.pending_result_timeout_seconds <= 60:
            errors.append(
                f"pending_result_timeout_seconds must be in [10, 60], "
                f"got {self.pending_result_timeout_seconds}"
            )

        if not 10 <= self.shutdown_timeout_seconds <= 60:
            errors.append(
                f"shutdown_timeout_seconds must be in [10, 60], "
                f"got {self.shutdown_timeout_seconds}"
            )

        # Validate dependency: pending_result_timeout < timeout
        if self.pending_result_timeout_seconds >= self.timeout_seconds:
            errors.append(
                f"pending_result_timeout_seconds ({self.pending_result_timeout_seconds}) "
                f"must be less than timeout_seconds ({self.timeout_seconds})"
            )

        return errors


@dataclass
class ParserConfig:
    """Individual parser configuration."""

    enabled: bool = True
    timeout_ms: int = 50

    def validate(self) -> list[str]:
        """Validate configuration values. Returns list of error messages."""
        errors = []

        if not 10 <= self.timeout_ms <= 1000:
            errors.append(f"timeout_ms must be in [10, 1000], got {self.timeout_ms}")

        return errors


@dataclass
class ParsersConfig:
    """All parsers configuration."""

    mkdir: ParserConfig = field(default_factory=ParserConfig)
    mv: ParserConfig = field(default_factory=ParserConfig)
    cp: ParserConfig = field(default_factory=ParserConfig)
    rm: ParserConfig = field(default_factory=ParserConfig)
    write_file: ParserConfig = field(default_factory=ParserConfig)
    edit: ParserConfig = field(default_factory=ParserConfig)

    def validate(self) -> list[str]:
        """Validate all parser configurations."""
        errors = []
        for name, parser in [
            ("mkdir", self.mkdir),
            ("mv", self.mv),
            ("cp", self.cp),
            ("rm", self.rm),
            ("write_file", self.write_file),
            ("edit", self.edit),
        ]:
            errors.extend([f"{name}: {e}" for e in parser.validate()])
        return errors


@dataclass
class PathValidationConfig:
    """Path security validation configuration."""

    allow_symlinks: bool = False
    max_path_length: int = 4096
    symlink_cache_ttl_seconds: int = 60

    def validate(self) -> list[str]:
        """Validate configuration values. Returns list of error messages."""
        errors = []

        if not 256 <= self.max_path_length <= 8192:
            errors.append(f"max_path_length must be in [256, 8192], got {self.max_path_length}")

        if not 10 <= self.symlink_cache_ttl_seconds <= 300:
            errors.append(
                f"symlink_cache_ttl_seconds must be in [10, 300], "
                f"got {self.symlink_cache_ttl_seconds}"
            )

        return errors


@dataclass
class ConsistencyCheckConfig:
    """Data consistency check configuration."""

    enabled: bool = True
    interval_minutes: int = 60
    parse_failure_retention_days: int = 30
    cleanup_interval_hours: int = 24

    def validate(self) -> list[str]:
        """Validate configuration values. Returns list of error messages."""
        errors = []

        if not 10 <= self.interval_minutes <= 1440:
            errors.append(f"interval_minutes must be in [10, 1440], got {self.interval_minutes}")

        if not 7 <= self.parse_failure_retention_days <= 365:
            errors.append(
                f"parse_failure_retention_days must be in [7, 365], "
                f"got {self.parse_failure_retention_days}"
            )

        if not 1 <= self.cleanup_interval_hours <= 168:
            errors.append(
                f"cleanup_interval_hours must be in [1, 168], " f"got {self.cleanup_interval_hours}"
            )

        return errors


@dataclass
class RedisConfig:
    """Redis configuration for distributed lock."""

    host: str = "localhost"
    port: int = 6379
    db: int = 0
    password: str | None = None
    lock_timeout_seconds: int = 300


@dataclass
class CleanupConfig:
    """Cleanup task configuration."""

    distributed_lock_enabled: bool = False
    redis: RedisConfig = field(default_factory=RedisConfig)


@dataclass
class AlertRuleConfig:
    """Single alert rule configuration."""

    enabled: bool = True
    threshold_percent: float | None = None
    threshold_ms: int | None = None
    threshold_size: int | None = None
    threshold_per_day: int | None = None
    severity: str = "warning"
    notifications: list[str] = field(default_factory=lambda: ["log"])

    def validate(self) -> list[str]:
        """Validate alert rule configuration."""
        errors = []

        if self.severity not in ("warning", "critical"):
            errors.append(f"severity must be 'warning' or 'critical', got {self.severity}")

        for method in self.notifications:
            if method not in ("log", "email", "sms"):
                errors.append(f"invalid notification method: {method}")

        return errors


@dataclass
class AlertRulesConfig:
    """All alert rules configuration."""

    parse_success_rate: AlertRuleConfig = field(
        default_factory=lambda: AlertRuleConfig(
            threshold_percent=99, severity="warning", notifications=["log", "email"]
        )
    )
    persistence_latency: AlertRuleConfig = field(
        default_factory=lambda: AlertRuleConfig(
            threshold_ms=20, severity="critical", notifications=["log", "email", "sms"]
        )
    )
    thread_pool_blocked: AlertRuleConfig = field(
        default_factory=lambda: AlertRuleConfig(
            severity="critical", notifications=["log", "email", "sms"]
        )
    )
    pending_queue_backlog: AlertRuleConfig = field(
        default_factory=lambda: AlertRuleConfig(
            threshold_size=100, severity="warning", notifications=["log", "email"]
        )
    )
    parse_failure_growth: AlertRuleConfig = field(
        default_factory=lambda: AlertRuleConfig(
            threshold_per_day=5, severity="warning", notifications=["log", "email"]
        )
    )

    def validate(self) -> list[str]:
        """Validate all alert rules."""
        errors = []
        for name, rule in [
            ("parse_success_rate", self.parse_success_rate),
            ("persistence_latency", self.persistence_latency),
            ("thread_pool_blocked", self.thread_pool_blocked),
            ("pending_queue_backlog", self.pending_queue_backlog),
            ("parse_failure_growth", self.parse_failure_growth),
        ]:
            errors.extend([f"{name}: {e}" for e in rule.validate()])
        return errors


@dataclass
class FileChangeParserConfig:
    """Complete file change parser configuration."""

    async_parsing: AsyncParsingConfig = field(default_factory=AsyncParsingConfig)
    parsers: ParsersConfig = field(default_factory=ParsersConfig)
    path_validation: PathValidationConfig = field(default_factory=PathValidationConfig)
    consistency_check: ConsistencyCheckConfig = field(default_factory=ConsistencyCheckConfig)
    cleanup: CleanupConfig = field(default_factory=CleanupConfig)
    alert_rules: AlertRulesConfig = field(default_factory=AlertRulesConfig)
    version: str = "1.0"

    def validate(self) -> list[str]:
        """Validate entire configuration. Returns list of error messages."""
        errors = []

        errors.extend([f"async_parsing: {e}" for e in self.async_parsing.validate()])
        errors.extend([f"parsers: {e}" for e in self.parsers.validate()])
        errors.extend([f"path_validation: {e}" for e in self.path_validation.validate()])
        errors.extend([f"consistency_check: {e}" for e in self.consistency_check.validate()])
        errors.extend([f"alert_rules: {e}" for e in self.alert_rules.validate()])

        return errors

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FileChangeParserConfig:
        """Create configuration from dictionary."""
        config = cls()

        # Load async_parsing
        if "async_parsing" in data:
            ap = data["async_parsing"]
            config.async_parsing = AsyncParsingConfig(
                enabled=ap.get("enabled", True),
                thread_pool_size=ap.get("thread_pool_size", 2),
                timeout_seconds=ap.get("timeout_seconds", 300),
                max_retries=ap.get("max_retries", 3),
                pending_result_timeout_seconds=ap.get("pending_result_timeout_seconds", 30),
                shutdown_timeout_seconds=ap.get("shutdown_timeout_seconds", 30),
            )

        # Load parsers
        if "parsers" in data:
            parsers_data = data["parsers"]
            config.parsers = ParsersConfig(
                mkdir=ParserConfig(**parsers_data.get("mkdir", {})),
                mv=ParserConfig(**parsers_data.get("mv", {})),
                cp=ParserConfig(**parsers_data.get("cp", {})),
                rm=ParserConfig(**parsers_data.get("rm", {})),
                write_file=ParserConfig(**parsers_data.get("write_file", {})),
                edit=ParserConfig(**parsers_data.get("edit", {})),
            )

        # Load path_validation
        if "path_validation" in data:
            pv = data["path_validation"]
            config.path_validation = PathValidationConfig(
                allow_symlinks=pv.get("allow_symlinks", False),
                max_path_length=pv.get("max_path_length", 4096),
                symlink_cache_ttl_seconds=pv.get("symlink_cache_ttl_seconds", 60),
            )

        # Load consistency_check
        if "consistency_check" in data:
            cc = data["consistency_check"]
            config.consistency_check = ConsistencyCheckConfig(
                enabled=cc.get("enabled", True),
                interval_minutes=cc.get("interval_minutes", 60),
                parse_failure_retention_days=cc.get("parse_failure_retention_days", 30),
                cleanup_interval_hours=cc.get("cleanup_interval_hours", 24),
            )

        # Load cleanup
        if "cleanup" in data:
            cleanup_data = data["cleanup"]
            redis_data = cleanup_data.get("redis", {})
            config.cleanup = CleanupConfig(
                distributed_lock_enabled=cleanup_data.get("distributed_lock_enabled", False),
                redis=RedisConfig(
                    host=redis_data.get("host", "localhost"),
                    port=redis_data.get("port", 6379),
                    db=redis_data.get("db", 0),
                    password=redis_data.get("password"),
                    lock_timeout_seconds=redis_data.get("lock_timeout_seconds", 300),
                ),
            )

        # Load alert_rules
        if "alert_rules" in data:
            rules_data = data["alert_rules"]
            config.alert_rules = AlertRulesConfig(
                parse_success_rate=AlertRuleConfig(
                    enabled=rules_data.get("parse_success_rate", {}).get("enabled", True),
                    threshold_percent=rules_data.get("parse_success_rate", {}).get(
                        "threshold_percent", 99
                    ),
                    severity=rules_data.get("parse_success_rate", {}).get("severity", "warning"),
                    notifications=rules_data.get("parse_success_rate", {}).get(
                        "notifications", ["log", "email"]
                    ),
                ),
                persistence_latency=AlertRuleConfig(
                    enabled=rules_data.get("persistence_latency", {}).get("enabled", True),
                    threshold_ms=rules_data.get("persistence_latency", {}).get("threshold_ms", 20),
                    severity=rules_data.get("persistence_latency", {}).get("severity", "critical"),
                    notifications=rules_data.get("persistence_latency", {}).get(
                        "notifications", ["log", "email", "sms"]
                    ),
                ),
                thread_pool_blocked=AlertRuleConfig(
                    enabled=rules_data.get("thread_pool_blocked", {}).get("enabled", True),
                    severity=rules_data.get("thread_pool_blocked", {}).get("severity", "critical"),
                    notifications=rules_data.get("thread_pool_blocked", {}).get(
                        "notifications", ["log", "email", "sms"]
                    ),
                ),
                pending_queue_backlog=AlertRuleConfig(
                    enabled=rules_data.get("pending_queue_backlog", {}).get("enabled", True),
                    threshold_size=rules_data.get("pending_queue_backlog", {}).get(
                        "threshold_size", 100
                    ),
                    severity=rules_data.get("pending_queue_backlog", {}).get("severity", "warning"),
                    notifications=rules_data.get("pending_queue_backlog", {}).get(
                        "notifications", ["log", "email"]
                    ),
                ),
                parse_failure_growth=AlertRuleConfig(
                    enabled=rules_data.get("parse_failure_growth", {}).get("enabled", True),
                    threshold_per_day=rules_data.get("parse_failure_growth", {}).get(
                        "threshold_per_day", 5
                    ),
                    severity=rules_data.get("parse_failure_growth", {}).get("severity", "warning"),
                    notifications=rules_data.get("parse_failure_growth", {}).get(
                        "notifications", ["log", "email"]
                    ),
                ),
            )

        config.version = data.get("version", "1.0")

        return config


def _apply_env_overrides(config: FileChangeParserConfig) -> None:
    """Apply environment variable overrides to configuration.

    Environment variables take precedence over config file values.
    Format: FILE_CHANGE_<SECTION>_<KEY> (e.g., FILE_CHANGE_ASYNC_PARSING_ENABLED=false)
    """
    env_prefix = "FILE_CHANGE_"

    # Async parsing overrides
    if enabled := os.environ.get(f"{env_prefix}ASYNC_PARSING_ENABLED"):
        config.async_parsing.enabled = enabled.lower() in ("true", "1", "yes")
    if pool_size := os.environ.get(f"{env_prefix}ASYNC_PARSING_THREAD_POOL_SIZE"):
        try:
            config.async_parsing.thread_pool_size = int(pool_size)
        except ValueError:
            logger.warning(f"Invalid FILE_CHANGE_ASYNC_PARSING_THREAD_POOL_SIZE: {pool_size}")
    if timeout := os.environ.get(f"{env_prefix}ASYNC_PARSING_TIMEOUT_SECONDS"):
        try:
            config.async_parsing.timeout_seconds = int(timeout)
        except ValueError:
            logger.warning(f"Invalid FILE_CHANGE_ASYNC_PARSING_TIMEOUT_SECONDS: {timeout}")
    if max_retries := os.environ.get(f"{env_prefix}ASYNC_PARSING_MAX_RETRIES"):
        try:
            config.async_parsing.max_retries = int(max_retries)
        except ValueError:
            logger.warning(f"Invalid FILE_CHANGE_ASYNC_PARSING_MAX_RETRIES: {max_retries}")

    # Path validation overrides
    if allow_symlinks := os.environ.get(f"{env_prefix}PATH_VALIDATION_ALLOW_SYMLINKS"):
        config.path_validation.allow_symlinks = allow_symlinks.lower() in ("true", "1", "yes")
    if max_path := os.environ.get(f"{env_prefix}PATH_VALIDATION_MAX_PATH_LENGTH"):
        try:
            config.path_validation.max_path_length = int(max_path)
        except ValueError:
            logger.warning(f"Invalid FILE_CHANGE_PATH_VALIDATION_MAX_PATH_LENGTH: {max_path}")

    # Consistency check overrides
    if enabled := os.environ.get(f"{env_prefix}CONSISTENCY_CHECK_ENABLED"):
        config.consistency_check.enabled = enabled.lower() in ("true", "1", "yes")


def _load_config_file(config_path: Path) -> dict[str, Any]:
    """Load configuration from YAML file."""
    if not config_path.exists():
        logger.debug(f"Config file not found: {config_path}, using defaults")
        return {}

    try:
        with open(config_path) as f:
            data = yaml.safe_load(f)
            return data if data else {}
    except yaml.YAMLError as e:
        logger.error(f"Error parsing config file {config_path}: {e}")
        return {}
    except Exception as e:
        logger.error(f"Error reading config file {config_path}: {e}")
        return {}


def get_file_change_parser_config(
    config_path: Path | None = None, use_cache: bool = True
) -> FileChangeParserConfig:
    """Get file change parser configuration.

    Configuration is loaded from:
    1. YAML config file (default: config/file_change_parser.yaml)
    2. Environment variable overrides (FILE_CHANGE_*)

    Results are cached for up to 60 seconds.

    Args:
        config_path: Optional path to config file. Uses default if not provided.
        use_cache: Whether to use cached configuration (default: True).

    Returns:
        FileChangeParserConfig instance.
    """
    global _config_cache

    if config_path is None:
        config_path = DEFAULT_CONFIG_PATH

    # Check cache
    if use_cache:
        now = time.time()
        with _config_lock:
            if _config_cache is not None:
                ts, cached_config = _config_cache
                if now - ts < _config_ttl:
                    return cached_config

    # Load from file
    data = _load_config_file(config_path)

    # Create config object
    config = FileChangeParserConfig.from_dict(data)

    # Apply environment variable overrides
    _apply_env_overrides(config)

    # Validate configuration
    errors = config.validate()
    if errors:
        logger.warning(f"Invalid file change parser configuration, using defaults: {errors}")
        # Return default config with overrides only for valid values
        default_config = FileChangeParserConfig()
        _apply_env_overrides(default_config)
        return default_config

    # Update cache
    if use_cache:
        now = time.time()
        with _config_lock:
            _config_cache = (now, config)

    return config


def invalidate_config_cache() -> None:
    """Force the configuration cache to refresh on next read."""
    global _config_cache
    with _config_lock:
        _config_cache = None
