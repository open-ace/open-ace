"""
Open ACE - Content Filter Module

Provides content security filtering for enterprise compliance.
Detects and filters sensitive information, PII, and prohibited content.
"""

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional

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
    matched_rules: list[dict[str, Any]] = field(default_factory=list)
    redacted_content: Optional[str] = None
    suggestion: Optional[str] = None
    timestamp: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "passed": self.passed,
            "risk_level": self.risk_level,
            "matched_rules": self.matched_rules,
            "redacted_content": self.redacted_content,
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
        config: Optional[dict[str, Any]] = None,
        custom_patterns: Optional[dict[str, str]] = None,
        custom_keywords: Optional[list[str]] = None,
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
        """
        self.config = config or {}
        self.enabled = self.config.get("enabled", True)
        self.redact_pii = self.config.get("redact_pii", True)
        self.block_high_risk = self.config.get("block_high_risk", True)
        self.log_matches = self.config.get("log_matches", True)

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
            "sensitive_keyword": "high",
            "custom_pattern": "medium",
        }

    def check_content(self, content: str, context: Optional[dict[str, Any]] = None) -> FilterResult:
        """
        Check content for sensitive information.

        Args:
            content: Text content to check.
            context: Optional context information (user, action, etc.).

        Returns:
            FilterResult: Result of the filtering check.
        """
        if not self.enabled:
            return FilterResult(passed=True, risk_level="low")

        if not content:
            return FilterResult(passed=True, risk_level="low")

        matched_rules = []
        overall_risk = "low"
        redacted = content

        # Check PII patterns
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
                    }
                )

                # Update overall risk
                if risk == "critical":
                    overall_risk = "critical"
                elif risk == "high" and overall_risk not in ["critical"]:
                    overall_risk = "high"
                elif risk == "medium" and overall_risk not in ["critical", "high"]:
                    overall_risk = "medium"

                # Redact if enabled
                if self.redact_pii:
                    redacted = self._redact_matches(redacted, compiled_pattern, pattern_name)

        # Check sensitive keywords
        keyword_matches = self._check_keywords(content)
        if keyword_matches:
            matched_rules.append(
                {
                    "type": "sensitive_keyword",
                    "count": len(keyword_matches),
                    "risk": "high",
                    "keywords": list(keyword_matches),
                }
            )

            if overall_risk not in ["critical"]:
                overall_risk = "high"

        # Determine if passed
        passed = True
        if self.block_high_risk and overall_risk in ["high", "critical"]:
            passed = False

        # Log if enabled
        if self.log_matches and matched_rules:
            logger.warning(
                f"Content filter matched: {len(matched_rules)} rules, "
                f"risk={overall_risk}, passed={passed}"
            )

        # Generate suggestion
        suggestion = None
        if not passed:
            suggestion = self._generate_suggestion(matched_rules)

        return FilterResult(
            passed=passed,
            risk_level=overall_risk,
            matched_rules=matched_rules,
            redacted_content=redacted if self.redact_pii else None,
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

    def _redact_matches(self, content: str, pattern: re.Pattern, pattern_name: str) -> str:
        """Redact matched patterns in content using predefined templates."""
        # Use predefined template for better performance
        redact_func = REDACTION_TEMPLATES.get(pattern_name, REDACTION_TEMPLATES["default"])
        return pattern.sub(redact_func, content)

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
        return {
            "enabled": self.enabled,
            "redact_pii": self.redact_pii,
            "block_high_risk": self.block_high_risk,
            "pattern_count": len(self.patterns),
            "keyword_count": len(self.keywords),
            "patterns": list(self.patterns.keys()),
        }
