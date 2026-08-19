"""Tests for Issue #2747 — content_redacted audit details must not leak sensitive content.

Verifies that:
- ContentFilter no longer stores ``sample`` in matched_rules.
- ContentFilter no longer stores ``original_content`` in FilterResult.
- AuditLogger strips sensitive keys from ``details`` on write.
- AuditLog.to_dict() strips sensitive keys on read (historical data safety).
- The ``_sanitize_details`` helper handles nested dicts/lists.
- The llm_proxy_handler helpers build safe audit details.
"""

import hashlib
import json
from unittest.mock import MagicMock

import pytest

from app.modules.governance.audit_logger import (
    _AUDIT_DETAILS_DENYLIST,
    AuditAction,
    AuditLog,
    AuditLogger,
    _sanitize_details,
)
from app.modules.governance.content_filter import ContentFilter, FilterResult

# ── ContentFilter tests ──────────────────────────────────────────────────────


class TestContentFilterNoSampleLeak:
    """Issue #2747: matched_rules must not contain ``sample``."""

    def test_email_rule_has_no_sample(self):
        cf = ContentFilter()
        result = cf.check_content("Email me at alice@example.com please")
        email_rules = [r for r in result.matched_rules if r.get("type") == "pii_email"]
        assert len(email_rules) >= 1
        for rule in email_rules:
            assert "sample" not in rule, "matched_rules must not contain 'sample'"

    def test_phone_rule_has_no_sample(self):
        cf = ContentFilter()
        result = cf.check_content("Call me at +1-555-123-4567")
        phone_rules = [r for r in result.matched_rules if "phone" in r.get("type", "")]
        assert len(phone_rules) >= 1
        for rule in phone_rules:
            assert "sample" not in rule

    def test_ssn_rule_has_no_sample(self):
        cf = ContentFilter()
        result = cf.check_content("My SSN is 123-45-6789")
        ssn_rules = [r for r in result.matched_rules if r.get("type") == "pii_ssn"]
        assert len(ssn_rules) >= 1
        for rule in ssn_rules:
            assert "sample" not in rule

    def test_credit_card_rule_has_no_sample(self):
        cf = ContentFilter()
        # Use a Luhn-valid number so it passes the false-positive filter
        result = cf.check_content("Card: 4111-1111-1111-1111")
        cc_rules = [r for r in result.matched_rules if "credit_card" in r.get("type", "")]
        assert len(cc_rules) >= 1
        for rule in cc_rules:
            assert "sample" not in rule

    def test_matched_rules_keep_type_and_count(self):
        """Even without sample, rules must retain audit-useful fields."""
        cf = ContentFilter()
        result = cf.check_content("Email: bob@test.org, phone: +1-212-555-0100")
        for rule in result.matched_rules:
            assert "type" in rule
            assert "count" in rule
            assert rule["count"] >= 1


class TestContentFilterNoOriginalContent:
    """Issue #2747: FilterResult.original_content must always be None."""

    def test_redact_action_no_original_content(self):
        cf = ContentFilter(config={"redact_pii": True})
        result = cf.check_content("My email is test@example.com")
        # redact_pii=True + medium-risk PII → action should be "redact"
        if result.action == "redact":
            assert result.original_content is None

    def test_filter_result_original_content_always_none(self):
        """Even for high-risk content that triggers redact, no original stored."""
        cf = ContentFilter(config={"redact_pii": True, "block_high_risk": False})
        result = cf.check_content("SSN: 123-45-6789, email: a@b.com")
        assert result.original_content is None


# ── AuditLogger sanitize tests ───────────────────────────────────────────────


class TestSanitizeDetails:
    """Issue #2747: _sanitize_details recursively strips denylisted keys."""

    def test_strips_original_content(self):
        details = {"original_content": "secret text", "risk_level": "medium"}
        safe = _sanitize_details(details)
        assert "original_content" not in safe
        assert safe["risk_level"] == "medium"

    def test_strips_redacted_content(self):
        details = {"redacted_content": "***", "content_length": 42}
        safe = _sanitize_details(details)
        assert "redacted_content" not in safe
        assert safe["content_length"] == 42

    def test_strips_sample_from_nested_list(self):
        details = {
            "matched_rules": [
                {"type": "pii_email", "count": 1, "sample": "alice@example.com"},
                {"type": "pii_phone", "count": 2, "sample": "+1-555-0000"},
            ]
        }
        safe = _sanitize_details(details)
        for rule in safe["matched_rules"]:
            assert "sample" not in rule
            assert "type" in rule
            assert "count" in rule

    def test_strips_password_and_token(self):
        details = {"password": "hunter2", "token": "abc123", "action": "login"}
        safe = _sanitize_details(details)
        assert "password" not in safe
        assert "token" not in safe
        assert safe["action"] == "login"

    def test_strips_nested_dict(self):
        details = {
            "outer": {
                "inner": {"secret": "value", "safe_key": "ok"},
            }
        }
        safe = _sanitize_details(details)
        assert "secret" not in safe["outer"]["inner"]
        assert safe["outer"]["inner"]["safe_key"] == "ok"

    def test_preserves_non_sensitive_data(self):
        details = {
            "risk_level": "high",
            "matched_rules": [{"type": "pii_ssn", "count": 1, "risk": "critical"}],
            "content_length": 100,
            "content_hash": "abcdef0123456789",
        }
        safe = _sanitize_details(details)
        assert safe == details  # Nothing should be stripped

    def test_handles_empty_input(self):
        assert _sanitize_details({}) == {}
        assert _sanitize_details([]) == []
        assert _sanitize_details("string") == "string"
        assert _sanitize_details(42) == 42
        assert _sanitize_details(None) is None

    def test_all_denylist_keys_covered(self):
        """Verify the denylist includes the expected keys."""
        expected = {
            "original_content",
            "redacted_content",
            "password",
            "secret",
            "api_key",
            "apikey",
            "access_token",
            "auth_token",
            "private_key",
            "ssh_key",
            "credential",
            "token",
            "sample",
            "keywords",
        }
        assert frozenset(expected) == _AUDIT_DETAILS_DENYLIST


class TestAuditLoggerWriteSanitize:
    """Issue #2747: AuditLogger.log() sanitizes details before persisting."""

    def _make_logger(self):
        mock_db = MagicMock()
        mock_cursor = MagicMock()
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_db.connection.return_value = mock_conn
        logger = AuditLogger(db=mock_db)
        return logger, mock_cursor

    def test_log_strips_original_content(self):
        logger, cursor = self._make_logger()
        logger.log(
            action="content_redacted",
            user_id=1,
            details={
                "original_content": "my secret email a@b.com",
                "redacted_content": "my secret email ***",
                "risk_level": "medium",
            },
        )
        # Inspect the JSON that was persisted
        call_args = cursor.execute.call_args
        params = call_args[0][1]  # second positional arg is the params tuple
        details_json = params[7]  # details is the 8th parameter (index 7)
        details = json.loads(details_json)
        assert "original_content" not in details
        assert "redacted_content" not in details
        assert details["risk_level"] == "medium"

    def test_log_strips_sample_from_matched_rules(self):
        logger, cursor = self._make_logger()
        logger.log(
            action="content_blocked",
            user_id=1,
            details={
                "matched_rules": [
                    {"type": "pii_email", "count": 1, "sample": "alice@example.com"},
                ],
                "risk_level": "medium",
            },
        )
        call_args = cursor.execute.call_args
        params = call_args[0][1]
        details_json = params[7]
        details = json.loads(details_json)
        for rule in details["matched_rules"]:
            assert "sample" not in rule


class TestAuditLogToDictSanitize:
    """Issue #2747: AuditLog.to_dict() sanitizes details for historical data."""

    def test_to_dict_strips_original_content(self):
        log = AuditLog(
            id=1,
            action="content_redacted",
            details={
                "original_content": "should not appear",
                "redacted_content": "also removed",
                "risk_level": "medium",
                "content_length": 50,
            },
        )
        d = log.to_dict()
        assert "original_content" not in d["details"]
        assert "redacted_content" not in d["details"]
        assert d["details"]["risk_level"] == "medium"
        assert d["details"]["content_length"] == 50

    def test_to_dict_strips_nested_sample(self):
        log = AuditLog(
            id=2,
            action="content_redacted",
            details={
                "matched_rules": [
                    {"type": "pii_email", "sample": "real@email.com", "count": 1},
                ],
            },
        )
        d = log.to_dict()
        for rule in d["details"]["matched_rules"]:
            assert "sample" not in rule
            assert "type" in rule

    def test_to_dict_preserves_safe_details(self):
        safe_details = {
            "risk_level": "low",
            "matched_rules": [{"type": "sensitive_keyword", "count": 2}],
            "resource_name": "test-resource",
        }
        log = AuditLog(id=3, action="content_warned", details=safe_details)
        d = log.to_dict()
        assert d["details"] == safe_details


# ── llm_proxy_handler helper tests ───────────────────────────────────────────


class TestLlmProxyHandlerHelpers:
    """Issue #2747: llm_proxy_handler helpers build safe audit details."""

    def test_sanitize_matched_rules(self):
        from app.modules.workspace.llm_proxy_handler import _sanitize_matched_rules

        rules = [
            {
                "type": "pii_email",
                "count": 1,
                "risk": "medium",
                "sample": "alice@example.com",
                "source": "builtin",
            },
            {
                "type": "sensitive_keyword",
                "count": 2,
                "keywords": ["password", "secret"],
                "source": "builtin",
            },
        ]
        safe = _sanitize_matched_rules(rules)
        assert len(safe) == 2
        # First rule: sample removed, safe fields kept
        assert "sample" not in safe[0]
        assert safe[0]["type"] == "pii_email"
        assert safe[0]["count"] == 1
        # Second rule: keywords removed
        assert "keywords" not in safe[1]
        assert safe[1]["type"] == "sensitive_keyword"

    def test_build_safe_content_details_no_content(self):
        from app.modules.workspace.llm_proxy_handler import _build_safe_content_details

        result = FilterResult(
            passed=True,
            risk_level="medium",
            action="warn",
            matched_rules=[{"type": "pii_email", "count": 1, "sample": "a@b.com"}],
            message="Content warning",
        )
        details = _build_safe_content_details(result)
        assert "original_content" not in details
        assert "redacted_content" not in details
        assert details["risk_level"] == "medium"
        assert len(details["matched_rules"]) == 1
        assert "sample" not in details["matched_rules"][0]

    def test_build_safe_content_details_with_content_meta(self):
        from app.modules.workspace.llm_proxy_handler import _build_safe_content_details

        result = FilterResult(
            passed=True,
            risk_level="medium",
            action="redact",
            matched_rules=[{"type": "pii_email", "count": 1}],
            message="Content redacted",
        )
        content = "my email is test@example.com"
        details = _build_safe_content_details(result, include_content_meta=True, content=content)
        assert "original_content" not in details
        assert "redacted_content" not in details
        assert details["content_length"] == len(content)
        expected_hash = hashlib.sha256(content.encode()).hexdigest()[:16]
        assert details["content_hash"] == expected_hash
        assert details["redacted"] is True


# ── Integration: full pipeline ───────────────────────────────────────────────


class TestFullPipeline:
    """End-to-end: content with PII → audit log → API response, no leaks."""

    def test_redact_pipeline_no_sensitive_data_in_audit(self):
        """Simulate the full content filter → audit log pipeline."""
        cf = ContentFilter(config={"redact_pii": True, "block_high_risk": False})
        content = "My email is alice@example.com and SSN is 123-45-6789"
        result = cf.check_content(content)

        # FilterResult must not contain original_content
        assert result.original_content is None

        # matched_rules must not contain sample
        for rule in result.matched_rules:
            assert "sample" not in rule

        # Simulate what llm_proxy_handler does for content_redacted
        from app.modules.workspace.llm_proxy_handler import _build_safe_content_details

        if result.action == "redact":
            details = _build_safe_content_details(
                result, include_content_meta=True, content=content
            )
        else:
            details = _build_safe_content_details(result)

        # Verify no sensitive data in details
        assert "original_content" not in details
        assert "redacted_content" not in details
        serialized = json.dumps(details)
        assert "alice@example.com" not in serialized
        assert "123-45-6789" not in serialized

        # Simulate AuditLog.to_dict() on historical data with leaked fields
        log = AuditLog(
            id=999,
            action="content_redacted",
            details={
                "original_content": content,
                "redacted_content": "My email is *** and SSN is ***",
                "matched_rules": [
                    {"type": "pii_email", "sample": "alice@example.com", "count": 1},
                    {"type": "pii_ssn", "sample": "123-45-6789", "count": 1},
                ],
            },
        )
        d = log.to_dict()
        serialized = json.dumps(d["details"])
        assert "alice@example.com" not in serialized
        assert "123-45-6789" not in serialized
        assert "original_content" not in d["details"]
        assert "redacted_content" not in d["details"]
