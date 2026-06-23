"""Unit tests for the artifact-text extraction helpers in the orchestrator.

These helpers directly produce the text written into public PR comments and
timeline milestones, so their de-noising regexes and precedence rules are
regression-sensitive. Coverage:

- ``_sanitize_artifact_text``: leaked preamble / tool-JSON stripping, and that
  legitimate prose is NOT误删 (the key risk called out in review).
- ``_artifact_text``: picks the best publishable candidate across response /
  visible text, instead of blindly trusting the last assistant turn.
- ``_artifact_tldr``: prefers the structured ``TL;DR:`` tag over raw slicing,
  and falls back correctly.
- ``_artifact_status_tag``: reads structured tags with type safety.

The helpers are classmethods/staticmethods, so they are exercised on the class
without instantiating ``AutonomousOrchestrator`` (whose ``__init__`` needs a
workflow id + DB).
"""

from app.modules.workspace.autonomous.models import AgentTaskResult
from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator


class TestSanitizeArtifactText:
    def test_strips_leaked_let_me_preamble(self):
        text = "Let me analyze the requirements.\n\nThe plan adds a new endpoint."
        result = AutonomousOrchestrator._sanitize_artifact_text(text)
        assert "Let me" not in result
        assert "The plan adds a new endpoint." in result

    def test_strips_leaked_i_need_to_preamble(self):
        text = "I need to:\n1. read files\n\nActual result: tests pass."
        result = AutonomousOrchestrator._sanitize_artifact_text(text)
        assert "I need to" not in result
        assert "Actual result: tests pass." in result

    def test_strips_single_line_tool_call_json(self):
        text = '{"description": "do something", "prompt": "run it"}\n\nReal summary here.'
        result = AutonomousOrchestrator._sanitize_artifact_text(text)
        assert "Real summary here." in result
        assert '"description"' not in result

    def test_preserves_legitimate_prose_containing_json_snippet(self):
        # A prose paragraph that merely contains an inline JSON example must NOT
        # be wiped — only lines that ARE tool-call JSON are stripped.
        text = 'Config example: {"a": 1} shown inline for reference.'
        result = AutonomousOrchestrator._sanitize_artifact_text(text)
        assert "Config example" in result

    def test_collapses_repeated_adjacent_paragraphs(self):
        text = "Same paragraph.\n\nSame paragraph.\n\nSame paragraph.\n\nUnique."
        result = AutonomousOrchestrator._sanitize_artifact_text(text)
        # Only one copy of the repeated paragraph survives, plus the unique one.
        assert result.count("Same paragraph.") == 1
        assert "Unique." in result

    def test_slices_from_heading_after_process_chatter(self):
        text = (
            "Let me update the todo list and provide a summary.\n"
            "## Test Summary\n\nAll tests passed.\n"
        )
        result = AutonomousOrchestrator._sanitize_artifact_text(text)
        assert result.startswith("## Test Summary")
        assert "todo list" not in result.lower()

    def test_truncates_repeated_heading_block(self):
        text = (
            "## Test Summary\n\nPass.\n\n### Root Cause\nA\n\n" "### Root Cause\nA\n\n### Fix\nB\n"
        )
        result = AutonomousOrchestrator._sanitize_artifact_text(text)
        assert result.count("### Root Cause") == 1

    def test_empty_and_none_safe(self):
        assert AutonomousOrchestrator._sanitize_artifact_text("") == ""


class TestArtifactText:
    def test_prefers_clean_response_text_when_publishable(self):
        result = AgentTaskResult(
            response_text="Final concise plan.",
            visible_response_text="Turn 1 text.\n\nFinal concise plan.",
        )
        assert AutonomousOrchestrator._artifact_text(result) == "Final concise plan."

    def test_prefers_visible_when_response_is_process_chatter(self):
        result = AgentTaskResult(
            response_text=(
                "The user wants me to integrate the plan. "
                "I'm in plan mode and should call ExitPlanMode."
            ),
            visible_response_text="## Final Plan\n\n1. Fix the backend filter.",
        )
        assert AutonomousOrchestrator._artifact_text(result).startswith("## Final Plan")

    def test_falls_back_to_visible_when_response_empty(self):
        result = AgentTaskResult(
            response_text="",
            visible_response_text="Only visible text remains.",
        )
        out = AutonomousOrchestrator._artifact_text(result)
        assert "Only visible text remains." in out

    def test_none_result_returns_empty(self):
        assert AutonomousOrchestrator._artifact_text(None) == ""


class TestArtifactTldr:
    def test_prefers_structured_tag(self):
        result = AgentTaskResult(
            response_text="Some prose TL;DR: stale slicing value",
            visible_response_text="Intro text.\nTL;DR: structured tag wins",
            structured_tags={"tldr": "structured tag wins"},
        )
        assert AutonomousOrchestrator._artifact_tldr(result) == "structured tag wins"

    def test_truncates_structured_tag_to_200(self):
        long = "x" * 500
        result = AgentTaskResult(structured_tags={"tldr": long})
        assert len(AutonomousOrchestrator._artifact_tldr(result)) == 200

    def test_falls_back_to_extract_when_no_structured_tag(self):
        result = AgentTaskResult(
            response_text="Work done.\nTL;DR: extracted from visible text",
            visible_response_text="Work done.\nTL;DR: extracted from visible text",
        )
        assert AutonomousOrchestrator._artifact_tldr(result) == "extracted from visible text"

    def test_none_result_returns_empty(self):
        assert AutonomousOrchestrator._artifact_tldr(None) == ""


class TestArtifactStatusTag:
    def test_reads_structured_test_status(self):
        result = AgentTaskResult(structured_tags={"test_status": "PASS"})
        assert AutonomousOrchestrator._artifact_status_tag(result, "test_status") == "PASS"

    def test_missing_key_returns_empty(self):
        result = AgentTaskResult(structured_tags={})
        assert AutonomousOrchestrator._artifact_status_tag(result, "ci_status") == ""

    def test_non_string_value_returns_empty(self):
        # Defensive: a malformed tag value (e.g. a list) must not leak through.
        result = AgentTaskResult(structured_tags={"test_status": ["PASS"]})
        assert AutonomousOrchestrator._artifact_status_tag(result, "test_status") == ""

    def test_none_result_returns_empty(self):
        assert AutonomousOrchestrator._artifact_status_tag(None, "test_status") == ""
