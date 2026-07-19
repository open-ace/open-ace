"""Tests for _extract_cli_result_error subtype=success guard (issue #1816).

Claude SDK 2.1.x can emit a contradictory ``result`` event near the context
limit: ``is_error=true`` with ``subtype="success"`` and no actual error
message. Without a guard, ``_extract_cli_result_error`` falls through to the
``subtype`` fallback and returns ``("unknown_cli_error", "success")``, which
sets ``session.error = "success"`` → ``success = False`` → the milestone is
marked ``failed`` even though the agent produced valid output.
"""

from app.modules.workspace.autonomous.agent_runner import _extract_cli_result_error


class TestExtractCliResultErrorSubtypeSuccess:
    """``is_error=true`` + ``subtype=success`` must NOT be classified as an
    error — it's a spurious SDK signal, not a real failure."""

    def test_subtype_success_with_is_error_returns_none(self):
        """The exact #1816 scenario: is_error=true, subtype=success."""
        result = _extract_cli_result_error(
            {"is_error": True, "subtype": "success", "type": "result"},
        )
        assert result == (None, None), (
            "is_error=true + subtype=success should be treated as success, "
            "not an error (#1816 regression)"
        )

    def test_subtype_success_with_empty_errors_returns_none(self):
        """is_error=true, subtype=success, empty errors list — still success."""
        result = _extract_cli_result_error(
            {"is_error": True, "subtype": "success", "errors": [], "type": "result"},
        )
        assert result == (None, None)

    def test_real_error_with_subtype_error_still_caught(self):
        """A genuine error (is_error=true, subtype=error, error message) must
        still be caught — the guard only applies to subtype=success."""
        result = _extract_cli_result_error(
            {"is_error": True, "subtype": "error", "error": "Not logged in"},
        )
        assert result is not None
        assert result[1] == "Not logged in"

    def test_real_error_without_subtype_still_caught(self):
        """is_error=true with a real error message but no subtype."""
        result = _extract_cli_result_error(
            {"is_error": True, "error": "Context window exceeded"},
        )
        assert result is not None
        assert "Context window exceeded" in result[1]

    def test_is_error_false_returns_none(self):
        """Normal success (is_error=false) returns None."""
        result = _extract_cli_result_error(
            {"is_error": False, "subtype": "success", "type": "result"},
        )
        assert result == (None, None)

    def test_resume_session_not_found_still_caught(self):
        """The specific known error patterns are not affected by the guard."""
        result = _extract_cli_result_error(
            {"is_error": True, "errors": ["No conversation found with session id abc"]},
        )
        assert result[0] == "resume_session_not_found"
