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

from app.modules.workspace.autonomous.artifact_text import (
    pick_best_artifact_text,
    sanitize_artifact_text,
    score_artifact_text,
)
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


MINIMAL_COMPLIANT_REVIEW = (
    'REVIEW_RESULT: {"verdict":"APPROVE","blocking_findings":[]}\n'
    "\n---\n\n"
    "**TL;DR**: 代码审查通过，CI 失败是预先存在的基础设施问题。"
)


class TestMachineContractVerdicts:
    """The review prompt's machine-readable verdict line is contract output,
    not process noise. It must survive cleaning, score above the publishable
    threshold, and reach ``_artifact_text`` — the exact path pr_review uses
    before declaring "PR review agent returned no result".

    Regression: workflow 02dae370 / PR #2578 (2026-08-16) failed with exactly
    this minimal compliant shape — the scorer graded it −58 (paragraph
    penalty, no markdown structure), so the gate returned an empty string."""

    def test_minimal_compliant_review_passes_artifact_gate(self):
        result = pick_best_artifact_text(MINIMAL_COMPLIANT_REVIEW.strip(), MINIMAL_COMPLIANT_REVIEW)

        assert "REVIEW_RESULT" in result

    def test_minimal_compliant_review_scores_above_threshold(self):
        assert score_artifact_text(sanitize_artifact_text(MINIMAL_COMPLIANT_REVIEW)) > -1

    def test_artifact_text_extracts_minimal_compliant_verdict(self):
        result = AgentTaskResult(response_text=MINIMAL_COMPLIANT_REVIEW, success=True)

        extracted = AutonomousOrchestrator._artifact_text(result)

        assert "REVIEW_RESULT" in extracted

    def test_contract_line_survives_despite_later_heading(self):
        # A process preamble plus a later markdown heading must not slice away
        # an earlier contract line (both _slice_from_structured_start and
        # clean_agent_text slice to the first structured line).
        text = (
            "Let me check the working directory first.\n\n"
            'REVIEW_RESULT: {"verdict":"APPROVE","blocking_findings":[]}\n\n'
            "## Summary\n\nAll acceptance criteria are met."
        )

        assert "REVIEW_RESULT" in sanitize_artifact_text(text)

    def test_findings_quoting_process_words_survive_end_to_end(self):
        # Blocking findings that quote agent chatter ("let me … working
        # directory") land in the scoring head and used to wipe the contract
        # paragraph (process-paragraph filter) or push the score to −116
        # (head-marker penalty). The verdict must still reach the gate exit.
        text = (
            'REVIEW_RESULT: {"verdict":"REQUEST_CHANGES","blocking_findings":'
            '["agent kept saying let me check the working directory"]}\n\n'
            "**TL;DR**: 存在阻塞项。"
        )
        result = AgentTaskResult(response_text=text, success=True)

        assert "REVIEW_RESULT" in AutonomousOrchestrator._artifact_text(result)

    def test_pure_process_noise_is_still_rejected(self):
        noise = (
            "Let me check the working directory.\n\n"
            "Hmm, actually I need to think about this more.\n\n"
            "Wait, let me look at the conversation start again."
        )

        assert pick_best_artifact_text(noise.strip(), noise) == ""

    def test_sanitize_preserves_contract_and_tldr_lines(self):
        cleaned = sanitize_artifact_text(MINIMAL_COMPLIANT_REVIEW)

        assert "REVIEW_RESULT" in cleaned
        assert "TL;DR" in cleaned
