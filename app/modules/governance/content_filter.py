"""
Open ACE - Content Filter Module

Provides content security filtering for enterprise compliance.
Detects and filters sensitive information, PII, and prohibited content.
"""

import logging
import re
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, cast

from app.repositories.governance_repo import GovernanceRepository

logger = logging.getLogger(__name__)


class RiskLevel(Enum):
    """Risk level for filtered content."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ContentType(Enum):
    """Types of sensitive content."""

    PII_EMAIL = "pii_email"
    PII_PHONE = "pii_phone"
    PII_SSN = "pii_ssn"
    PII_CREDIT_CARD = "pii_credit_card"
    PII_ADDRESS = "pii_address"
    PII_PASSPORT = "pii_passport"
    PII_DRIVER_LICENSE = "pii_driver_license"
    SENSITIVE_KEYWORD = "sensitive_keyword"
    PROFANITY = "profanity"
    CUSTOM_PATTERN = "custom_pattern"


@dataclass
class FilterResult:
    """Result of content filtering."""

    passed: bool
    risk_level: str = "low"
    action: str = "none"  # none, warn, block, redact
    matched_rules: list[dict[str, Any]] = field(default_factory=list)
    redacted_content: str | None = None
    original_content: str | None = None  # For audit logging with redact
    message: str | None = None
    suggestion: str | None = None
    timestamp: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None)
    )

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "passed": self.passed,
            "risk_level": self.risk_level,
            "action": self.action,
            "matched_rules": self.matched_rules,
            "redacted_content": self.redacted_content,
            "message": self.message,
            "suggestion": self.suggestion,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
        }


# Predefined redaction templates for performance
REDACTION_TEMPLATES = {
    "pii_email": lambda m: (
        f"{m.group().split('@')[0][0]}***@{m.group().split('@')[1]}"
        if "@" in m.group()
        else "*" * len(m.group())
    ),
    "pii_phone_us": lambda m: m.group()[:3] + "-***-****",
    "pii_phone_intl": lambda m: m.group()[:3] + "-***-****",
    "pii_ssn": lambda m: "***-**-****",
    "pii_credit_card": lambda m: "****-****-****-****",
    "pii_credit_card_amex": lambda m: "****-****-****-****",
    "default": lambda m: "*" * len(m.group()),
}


class ContentFilter:
    """
    Content security filter for enterprise compliance.

    Features:
    - PII detection (email, phone, SSN, credit card, etc.)
    - Custom sensitive pattern matching
    - Content redaction
    - Configurable severity levels
    """

    # Priority mappings for risk levels and actions (used by static helper methods)
    _RISK_PRIORITY = {"critical": 4, "high": 3, "medium": 2, "low": 1}
    _ACTION_PRIORITY = {"block": 3, "warn": 2, "redact": 1, "none": 0}

    # Default PII patterns
    DEFAULT_PII_PATTERNS = {
        "pii_email": r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}",
        "pii_phone_us": r"\b(\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b",
        "pii_phone_intl": r"\b\+?[1-9]\d{1,3}[-.\s]?\d{1,14}\b",
        "pii_ssn": r"\b\d{3}-\d{2}-\d{4}\b",
        "pii_credit_card": r"\b(?:\d{4}[-\s]?){3}\d{4}\b",
        "pii_credit_card_amex": r"\b\d{4}[-\s]?\d{6}[-\s]?\d{5}\b",
        "pii_zip_us": r"\b\d{5}(-\d{4})?\b",
        "pii_ip_v4": r"\b(?:\d{1,3}\.){3}\d{1,3}\b",
        "pii_ip_v6": r"\b(?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}\b",
    }

    # Default sensitive keywords (can be customized)
    DEFAULT_SENSITIVE_KEYWORDS = [
        "password",
        "secret",
        "api_key",
        "apikey",
        "access_token",
        "auth_token",
        "private_key",
        "ssh_key",
        "credential",
    ]

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        custom_patterns: dict[str, str] | None = None,
        custom_keywords: list[str] | None = None,
        governance_repo: GovernanceRepository | None = None,
    ):
        """
        Initialize content filter.

        Args:
            config: Configuration dictionary with options:
                - enabled: Enable/disable filtering (default: True)
                - redact_pii: Redact PII in output (default: True)
                - block_high_risk: Block high risk content (default: True)
                - log_matches: Log all matches (default: True)
            custom_patterns: Additional regex patterns to match.
            custom_keywords: Additional keywords to detect.
            governance_repo: Optional GovernanceRepository for database rules.
        """
        self.config = config or {}
        self.enabled = self.config.get("enabled", True)
        self.redact_pii = self.config.get("redact_pii", True)
        self.block_high_risk = self.config.get("block_high_risk", True)
        self.log_matches = self.config.get("log_matches", True)

        # Database rules integration
        self.governance_repo = governance_repo
        self._rules_cache: list[dict[str, Any]] | None = None
        self._cache_valid: bool = False

        # Compiled regex cache for database rules (LRU cache)
        self._compiled_rules_cache: OrderedDict[tuple[str, int], re.Pattern] = OrderedDict()
        self._cache_lock = threading.Lock()
        self._cache_hits: int = 0
        self._cache_misses: int = 0
        self._max_compiled_cache_size: int = self.config.get("max_compiled_cache_size", 100)

        # Compile patterns
        self.patterns = dict(self.DEFAULT_PII_PATTERNS)
        if custom_patterns:
            self.patterns.update(custom_patterns)

        self.compiled_patterns = {
            name: re.compile(pattern, re.IGNORECASE) for name, pattern in self.patterns.items()
        }

        # Keywords
        self.keywords = set(self.DEFAULT_SENSITIVE_KEYWORDS)
        if custom_keywords:
            self.keywords.update(custom_keywords)

        # Risk level mapping
        self.risk_mapping = {
            "pii_ssn": "critical",
            "pii_credit_card": "critical",
            "pii_credit_card_amex": "critical",
            "pii_passport": "critical",
            "pii_driver_license": "high",
            "pii_email": "medium",
            "pii_phone_us": "medium",
            "pii_phone_intl": "medium",
            "sensitive_keyword": "medium",  # Downgraded: prone to false positives with substring match
            "custom_pattern": "medium",
        }

    def _load_rules_from_db(self) -> list[dict[str, Any]]:
        """
        Load filter rules from database.

        Returns:
            List of enabled filter rules.
        """
        if self.governance_repo is None:
            return []

        if self._cache_valid and self._rules_cache is not None:
            return self._rules_cache

        try:
            rules = self.governance_repo.get_filter_rules()
            # Filter only enabled rules
            enabled_rules = [r for r in rules if r.get("is_enabled", True)]
            self._rules_cache = enabled_rules
            self._cache_valid = True
            logger.debug(f"Loaded {len(enabled_rules)} filter rules from database")
            return enabled_rules
        except Exception as e:
            logger.error(f"Failed to load filter rules from database: {e}")
            return []

    def _get_compiled_pattern(
        self, pattern: str, flags: int = re.IGNORECASE, rule_id: Any = None
    ) -> re.Pattern | None:
        """
        Get compiled regex pattern from cache or compile and cache it.

        Uses LRU cache with configurable max size. Thread-safe with double-checked locking.

        Args:
            pattern: Regex pattern string to compile.
            flags: Regex flags (default: re.IGNORECASE).
            rule_id: Optional rule ID for error logging.

        Returns:
            Compiled regex pattern, or None if pattern is invalid.
        """
        if not pattern:
            return None

        cache_key = (pattern, flags)

        # First check without lock (fast path)
        if cache_key in self._compiled_rules_cache:
            # Move to end for LRU
            self._compiled_rules_cache.move_to_end(cache_key)
            self._cache_hits += 1
            return self._compiled_rules_cache[cache_key]

        # Need to compile - acquire lock
        with self._cache_lock:
            # Double-check after acquiring lock
            if cache_key in self._compiled_rules_cache:
                self._compiled_rules_cache.move_to_end(cache_key)
                self._cache_hits += 1
                return self._compiled_rules_cache[cache_key]

            # Track attempt before compiling (counts as miss regardless of validity)
            self._cache_misses += 1

            # Compile the pattern
            try:
                compiled = re.compile(pattern, flags)
            except re.error as e:
                logger.error(f"Invalid regex pattern in rule {rule_id}: {e}")
                return None

            # Cache the compiled pattern
            self._compiled_rules_cache[cache_key] = compiled

            # Check cache size and evict oldest if needed (LRU)
            while len(self._compiled_rules_cache) > self._max_compiled_cache_size:
                self._compiled_rules_cache.popitem(last=False)

            return compiled

    def invalidate_cache(self) -> None:
        """
        Invalidate the rules cache.
        Call this after CRUD operations on filter rules.
        """
        with self._cache_lock:
            self._cache_valid = False
            self._rules_cache = None
            self._compiled_rules_cache.clear()
            self._cache_hits = 0
            self._cache_misses = 0
        logger.debug("Content filter rules cache invalidated")

    def refresh_rules(self) -> None:
        """
        Refresh the rules cache by invalidating and forcing reload on next check.
        Alias for invalidate_cache() with clearer naming for API integration.
        """
        self.invalidate_cache()

    def check_content(self, content: str, context: dict[str, Any] | None = None) -> FilterResult:
        """
        Check content for sensitive information.

        Args:
            content: Text content to check.
            context: Optional context information (user, action, etc.).

        Returns:
            FilterResult: Result of the filtering check.
        """
        if not self.enabled:
            return FilterResult(passed=True, risk_level="low", action="none")

        if not content:
            return FilterResult(passed=True, risk_level="low", action="none")

        matched_rules = []
        overall_risk = "low"
        overall_action = "none"  # Track the most severe action
        redacted = content

        # First check database rules (user-configured rules)
        db_rules = self._load_rules_from_db()
        for rule in db_rules:
            rule_pattern = rule.get("pattern", "")
            rule_type = rule.get("type", "keyword")
            rule_action = rule.get("action", "warn")
            rule_severity = rule.get("severity", "medium")
            rule_id = rule.get("id")
            rule_description = rule.get("description", "")

            if not rule_pattern:
                continue

            compiled = None  # Cache compiled pattern for reuse
            try:
                if rule_type == "regex":
                    # Compile regex pattern with caching
                    compiled = self._get_compiled_pattern(rule_pattern, re.IGNORECASE, rule_id)
                    if compiled is None:
                        continue  # Invalid pattern, skip this rule
                    matches = compiled.findall(content)
                elif rule_type == "keyword":
                    # Keyword matching (case-insensitive substring)
                    matches = [rule_pattern] if rule_pattern.lower() in content.lower() else []
                elif rule_type == "pii":
                    # PII pattern matching with caching
                    compiled = self._get_compiled_pattern(rule_pattern, re.IGNORECASE, rule_id)
                    if compiled is None:
                        continue  # Invalid pattern, skip this rule
                    matches = compiled.findall(content)
                else:
                    continue

                if matches:
                    matched_rules.append(
                        {
                            "id": rule_id,
                            "type": rule_type,
                            "pattern": rule_pattern,
                            "action": rule_action,
                            "severity": rule_severity,
                            "count": len(matches),
                            "description": rule_description,
                            "source": "database",
                        }
                    )

                    # Update overall risk and action
                    overall_risk = self._update_risk_level(overall_risk, rule_severity)
                    overall_action = self._update_action(overall_action, rule_action)

                    # Apply redaction if action is redact
                    if rule_action == "redact":
                        if rule_type == "regex" or rule_type == "pii":
                            # Reuse compiled pattern (already cached)
                            if compiled is None:
                                compiled = self._get_compiled_pattern(
                                    rule_pattern, re.IGNORECASE, rule_id
                                )
                            if compiled is not None:
                                redacted = compiled.sub("***", redacted)
                        elif rule_type == "keyword":
                            # Redact keyword matches
                            compiled_keyword = self._get_compiled_pattern(
                                rule_pattern, re.IGNORECASE, rule_id
                            )
                            if compiled_keyword is not None:
                                redacted = compiled_keyword.sub("***", redacted)

            except re.error as e:
                logger.error(f"Invalid regex pattern in rule {rule_id}: {e}")
                continue

        # Then check built-in PII patterns
        for pattern_name, compiled_pattern in self.compiled_patterns.items():
            matches = compiled_pattern.findall(content)
            if matches:
                risk = self.risk_mapping.get(pattern_name, "medium")
                matched_rules.append(
                    {
                        "type": pattern_name,
                        "count": len(matches),
                        "risk": risk,
                        "sample": matches[0] if matches else None,
                        "source": "builtin",
                    }
                )

                # Update overall risk
                overall_risk = self._update_risk_level(overall_risk, risk)

                # For built-in PII, use block_high_risk config for action
                if self.block_high_risk and risk in ["high", "critical"]:
                    overall_action = self._update_action(overall_action, "block")
                elif self.redact_pii:
                    overall_action = self._update_action(overall_action, "redact")

                # Redact if enabled
                if self.redact_pii:
                    redacted = self._redact_matches(redacted, compiled_pattern, pattern_name)

        # Check built-in sensitive keywords
        keyword_matches = self._check_keywords(content)
        if keyword_matches:
            matched_rules.append(
                {
                    "type": "sensitive_keyword",
                    "count": len(keyword_matches),
                    "risk": "medium",  # Downgrade: sensitive_keyword sub-match is prone to false positives
                    "keywords": list(keyword_matches),
                    "source": "builtin",
                }
            )

            # Update overall_risk but do NOT force overall_action to block
            # sensitive_keyword should only generate audit logs, not block requests
            overall_risk = self._update_risk_level(overall_risk, "medium")

            # Set action to 'warn' for audit visibility, but do NOT block
            # This prevents blocking legitimate messages containing words like
            # "password" or "secret" in non-sensitive contexts
            overall_action = self._update_action(overall_action, "warn")

        # Determine passed based on action
        passed = overall_action != "block"

        # Log if enabled
        if self.log_matches and matched_rules:
            logger.warning(
                f"Content filter matched: {len(matched_rules)} rules, "
                f"risk={overall_risk}, action={overall_action}, passed={passed}"
            )

        # Generate message and suggestion
        message = None
        suggestion = None
        if matched_rules:
            if overall_action == "block":
                message = f"Content blocked: matched {len(matched_rules)} filter rule(s)"
            elif overall_action == "warn":
                message = f"Content warning: matched {len(matched_rules)} filter rule(s)"
            elif overall_action == "redact":
                message = f"Content redacted: matched {len(matched_rules)} filter rule(s)"
            suggestion = self._generate_suggestion(matched_rules)

        return FilterResult(
            passed=passed,
            risk_level=overall_risk,
            action=overall_action,
            matched_rules=matched_rules,
            redacted_content=redacted if overall_action == "redact" else None,
            original_content=content if overall_action == "redact" else None,
            message=message,
            suggestion=suggestion,
        )

    def _check_keywords(self, content: str) -> set[str]:
        """Check for sensitive keywords."""
        content_lower = content.lower()
        found = set()

        for keyword in self.keywords:
            if keyword.lower() in content_lower:
                found.add(keyword)

        return found

    @staticmethod
    def _update_risk_level(current: str, new_risk: str) -> str:
        """
        Return the higher of two risk levels.

        Invalid risk levels are treated as priority 0 (lowest) and will not
        override any valid level.

        Args:
            current: Current overall risk level.
            new_risk: Newly detected risk level.

        Returns:
            The higher severity level.
        """
        current_priority = ContentFilter._RISK_PRIORITY.get(current, 0)
        new_priority = ContentFilter._RISK_PRIORITY.get(new_risk, 0)
        return new_risk if new_priority > current_priority else current

    @staticmethod
    def _update_action(current: str, new_action: str) -> str:
        """
        Return the more severe of two actions.

        Invalid actions are treated as priority 0 (lowest) and will not
        override any valid action.

        Args:
            current: Current overall action.
            new_action: Newly detected action.

        Returns:
            The more severe action.
        """
        current_priority = ContentFilter._ACTION_PRIORITY.get(current, 0)
        new_priority = ContentFilter._ACTION_PRIORITY.get(new_action, 0)
        return new_action if new_priority > current_priority else current

    def _redact_matches(self, content: str, pattern: re.Pattern, pattern_name: str) -> str:
        """Redact matched patterns in content using predefined templates."""
        # Use predefined template for better performance
        redact_func = REDACTION_TEMPLATES.get(pattern_name, REDACTION_TEMPLATES["default"])
        return cast("str", pattern.sub(redact_func, content))

    def _generate_suggestion(self, matched_rules: list[dict]) -> str:
        """Generate suggestion for blocked content."""
        suggestions = []

        for rule in matched_rules:
            rule_type = rule.get("type", "")
            if "email" in rule_type:
                suggestions.append("Remove or mask email addresses")
            elif "phone" in rule_type:
                suggestions.append("Remove or mask phone numbers")
            elif "ssn" in rule_type:
                suggestions.append("Remove Social Security Numbers")
            elif "credit_card" in rule_type:
                suggestions.append("Remove credit card numbers")
            elif "keyword" in rule_type:
                suggestions.append("Remove sensitive keywords or credentials")

        if suggestions:
            return "Suggestions: " + "; ".join(set(suggestions))

        return "Please review and remove sensitive information before proceeding."

    def add_custom_pattern(self, name: str, pattern: str, risk: str = "medium") -> None:
        """
        Add a custom pattern to detect.

        Args:
            name: Pattern name.
            pattern: Regex pattern string.
            risk: Risk level for this pattern.
        """
        self.patterns[name] = pattern
        self.compiled_patterns[name] = re.compile(pattern, re.IGNORECASE)
        self.risk_mapping[name] = risk

    def add_custom_keyword(self, keyword: str) -> None:
        """
        Add a custom keyword to detect.

        Args:
            keyword: Keyword to add.
        """
        self.keywords.add(keyword.lower())

    def get_stats(self) -> dict[str, Any]:
        """Get filter statistics."""
        total_requests = self._cache_hits + self._cache_misses
        hit_rate = (self._cache_hits / total_requests * 100) if total_requests > 0 else 0.0
        return {
            "enabled": self.enabled,
            "redact_pii": self.redact_pii,
            "block_high_risk": self.block_high_risk,
            "pattern_count": len(self.patterns),
            "keyword_count": len(self.keywords),
            "patterns": list(self.patterns.keys()),
            "compiled_cache_size": len(self._compiled_rules_cache),
            "compiled_cache_hits": self._cache_hits,
            "compiled_cache_misses": self._cache_misses,
            "compiled_cache_hit_rate": round(hit_rate, 2),
            "compiled_cache_max_size": self._max_compiled_cache_size,
        }
