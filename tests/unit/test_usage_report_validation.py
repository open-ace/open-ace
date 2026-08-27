"""
Unit tests for usage-report input validation and audit enum (Issue #1891).

Pure-function coverage of ``_validate_usage_report_input`` plus the
``AuditAction`` enum contract. Database/HTTP-backed coverage lives in
tests/integration/routes/test_usage_report_auth.py.
"""

from __future__ import annotations

import pytest

from app.modules.governance.audit_logger import AuditAction

pytestmark = [pytest.mark.regression, pytest.mark.issue(1891)]

# ── Input Validation Tests (R8) ───────────────────────────────────────────


class TestInputValidation:
    """Tests for input data validation (R8)."""

    def test_negative_input_tokens_rejected(self):
        """Negative tokens.input should return error."""
        from app.routes.remote import _validate_usage_report_input

        error, reason = _validate_usage_report_input({"input": -100, "output": 0}, 1)
        assert error is not None
        assert "input" in error.lower()

    def test_negative_output_tokens_rejected(self):
        """Negative tokens.output should return error."""
        from app.routes.remote import _validate_usage_report_input

        error, reason = _validate_usage_report_input({"input": 0, "output": -50}, 1)
        assert error is not None
        assert "output" in error.lower()

    def test_huge_token_values_rejected(self):
        """Tokens exceeding limit should return error."""
        from app.routes.remote import _validate_usage_report_input

        error, reason = _validate_usage_report_input({"input": 10**12, "output": 0}, 1)
        assert error is not None
        assert "tokens" in error.lower()

    def test_non_integer_token_values_rejected(self):
        """Non-integer token values should return error."""
        from app.routes.remote import _validate_usage_report_input

        error, reason = _validate_usage_report_input({"input": "abc", "output": 0}, 1)
        assert error is not None
        assert "integer" in error.lower()

    def test_requests_out_of_range_rejected(self):
        """Requests exceeding limit should return error."""
        from app.routes.remote import _validate_usage_report_input

        error, reason = _validate_usage_report_input({"input": 100, "output": 50}, 10000)
        assert error is not None
        assert "requests" in error.lower()

    def test_negative_requests_rejected(self):
        """Negative requests should return error."""
        from app.routes.remote import _validate_usage_report_input

        error, reason = _validate_usage_report_input({"input": 100, "output": 50}, -1)
        assert error is not None
        assert "requests" in error.lower()

    def test_valid_input_accepted(self):
        """Valid input should pass validation."""
        from app.routes.remote import _validate_usage_report_input

        error, reason = _validate_usage_report_input({"input": 100, "output": 50}, 5)
        assert error is None
        assert reason is None

    def test_zero_tokens_accepted(self):
        """Zero tokens should be valid."""
        from app.routes.remote import _validate_usage_report_input

        error, reason = _validate_usage_report_input({"input": 0, "output": 0}, 1)
        assert error is None


# ── Audit Logging Tests (R4) ──────────────────────────────────────────────


class TestAuditLogging:
    """Tests for audit logging (R4)."""

    def test_audit_action_enum_exists(self):
        """Audit actions should be defined."""
        assert hasattr(AuditAction, "USAGE_REPORT_AUTH_FAILURE")
        assert hasattr(AuditAction, "USAGE_REPORT_BINDING_MISMATCH")
        assert hasattr(AuditAction, "USAGE_REPORT_ACCEPTED")

        # Verify values
        assert AuditAction.USAGE_REPORT_AUTH_FAILURE.value == "usage_report_auth_failure"
        assert AuditAction.USAGE_REPORT_BINDING_MISMATCH.value == "usage_report_binding_mismatch"
        assert AuditAction.USAGE_REPORT_ACCEPTED.value == "usage_report_accepted"
