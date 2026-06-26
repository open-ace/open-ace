"""Tests for workflow content-language i18n (#1284).

Two-layer content ownership:

- **AI-authored content** (tldr / plan / review / report summaries) is generated
  once in the workflow's ``content_language`` and stored verbatim — it does not
  re-translate per viewer. The per-prompt directive that pins the language is
  ``build_language_instruction``.
- **System-authored structured content** (the ``progress_reported`` milestone) is
  stored as a structured payload in ``metadata.report`` and rendered per-viewer
  via deterministic template interpolation (no AI).

These tests cover: the prompt directive builder, the language-aware review
approval marker, the localized GitHub truncation notice, the structured
progress-report payload builder/renderer across all four languages, the
``_derive_review_passed`` structured-verdict path, and the localized GitHub
report comment produced by ``_do_report``.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from app.modules.workspace.autonomous import progress_report_i18n
from app.modules.workspace.autonomous.orchestrator import (
    _GITHUB_TRUNCATION_NOTICES,
    _LANGUAGE_DIRECTIVES,
    _REVIEW_APPROVAL_PHRASES,
    _TLDR_FORMAT_INSTRUCTIONS,
    TLDR_INSTRUCTION,
    AutonomousOrchestrator,
    _github_truncation_notice,
    _review_approval_phrase,
    build_language_instruction,
)
from app.modules.workspace.autonomous.progress_report_i18n import (
    DEFAULT_REPORT_LANGUAGE,
    PROGRESS_REPORT_I18N,
    PROGRESS_REPORT_SCHEMA_VERSION,
    build_progress_payload,
    render_progress_report,
    render_progress_report_tldr,
)
from app.repositories.autonomous_repo import (
    ALLOWED_CONTENT_LANGUAGES,
    DEFAULT_CONTENT_LANGUAGE,
    AutonomousWorkflowRepository,
)

SUPPORTED_LANGUAGES = ("en", "zh", "ja", "ko")


# ── build_language_instruction (AI content language pinning) ────────────────


class TestBuildLanguageInstruction:
    """The directive appended to every agent prompt pins AI content language."""

    def test_each_supported_language_has_directive_and_format(self):
        for lang in SUPPORTED_LANGUAGES:
            instruction = build_language_instruction(lang)
            assert instruction
            # Both the language directive and the TL;DR format instruction are
            # the per-language variants (not a mismatched language).
            assert _LANGUAGE_DIRECTIVES[lang] in instruction
            assert _TLDR_FORMAT_INSTRUCTIONS[lang] in instruction

    def test_tldr_marker_is_language_neutral(self):
        # The extraction regex keys on "TL;DR:" which must appear in every
        # language's instruction, otherwise _extract_tldr returns nothing for
        # non-zh workflows.
        for lang in SUPPORTED_LANGUAGES:
            assert "TL;DR:" in build_language_instruction(lang)

    def test_unknown_language_falls_back_to_english(self):
        instruction = build_language_instruction("fr")
        assert _LANGUAGE_DIRECTIVES["en"] in instruction
        assert _LANGUAGE_DIRECTIVES["zh"] not in instruction

    def test_missing_language_falls_back_to_english(self):
        assert build_language_instruction(None) == build_language_instruction("en")

    def test_alias_matches_chinese_instruction(self):
        # The legacy TLDR_INSTRUCTION alias must equal the zh format instruction
        # so existing imports/tests keep working.
        assert _TLDR_FORMAT_INSTRUCTIONS["zh"] == TLDR_INSTRUCTION


# ── _review_approval_phrase (language-aware approval detection) ──────────────


class TestReviewApprovalPhrase:
    """The approval marker the reviewer states is language-aware."""

    def test_each_language_has_distinct_phrase(self):
        phrases = {_review_approval_phrase(lang) for lang in SUPPORTED_LANGUAGES}
        # All four markers are distinct (no two languages share a marker).
        assert len(phrases) == len(SUPPORTED_LANGUAGES)

    def test_known_values(self):
        assert _review_approval_phrase("en") == "Code review passed"
        assert _review_approval_phrase("zh") == "代码审查通过"
        assert _review_approval_phrase("ja") == "コードレビュー合格"
        assert _review_approval_phrase("ko") == "코드 리뷰 통과"

    def test_unknown_and_missing_fall_back_to_default(self):
        default = _REVIEW_APPROVAL_PHRASES[DEFAULT_CONTENT_LANGUAGE]
        assert _review_approval_phrase("fr") == default
        assert _review_approval_phrase(None) == default


# ── _github_truncation_notice ────────────────────────────────────────────────


class TestGithubTruncationNotice:
    """The "comment truncated" notice is localized."""

    def test_each_language_has_notice(self):
        for lang in SUPPORTED_LANGUAGES:
            assert lang in _GITHUB_TRUNCATION_NOTICES
            assert _GITHUB_TRUNCATION_NOTICES[lang]

    def test_falls_back_to_default(self):
        assert (
            _github_truncation_notice("fr") == _GITHUB_TRUNCATION_NOTICES[DEFAULT_CONTENT_LANGUAGE]
        )
        assert (
            _github_truncation_notice(None) == _GITHUB_TRUNCATION_NOTICES[DEFAULT_CONTENT_LANGUAGE]
        )


# ── build_progress_payload (structured source of truth) ─────────────────────


class TestBuildProgressPayload:
    """The structured payload persisted in metadata.report."""

    def _diff(self):
        return {"files": 5, "additions": 100, "deletions": 20}

    def test_builds_full_structured_payload(self):
        payload = build_progress_payload(
            dev_round=1,
            plan_summary="plan text",
            diff_stats=self._diff(),
            test_summary="All tests passed",
            review_rounds=2,
            review_passed=True,
            pr_number=99,
            branch="feat/x",
            total_tokens=5000,
            total_requests=10,
        )
        assert payload == {
            "schema_version": PROGRESS_REPORT_SCHEMA_VERSION,
            "dev_round": 1,
            "plan_summary": "plan text",
            "files": 5,
            "additions": 100,
            "deletions": 20,
            "branch": "feat/x",
            "test_summary": "All tests passed",
            "review_rounds": 2,
            "review_passed": True,
            "pr_number": 99,
            "total_tokens": 5000,
            "total_requests": 10,
        }

    def test_schema_version_is_one(self):
        assert PROGRESS_REPORT_SCHEMA_VERSION == 1

    def test_defaults_and_type_coercion(self):
        payload = build_progress_payload(dev_round="3")  # dev_round coerced to int
        assert payload["dev_round"] == 3
        assert payload["plan_summary"] == ""
        assert payload["files"] == 0
        assert payload["additions"] == 0
        assert payload["deletions"] == 0
        assert payload["branch"] == ""
        assert payload["test_summary"] == ""
        assert payload["review_rounds"] == 0
        assert payload["review_passed"] is False
        assert payload["pr_number"] is None
        assert payload["total_tokens"] == 0
        assert payload["total_requests"] == 0

    def test_none_diff_stats_is_safe(self):
        payload = build_progress_payload(dev_round=1, diff_stats=None)
        assert payload["files"] == 0
        assert payload["additions"] == 0

    def test_payload_is_json_serializable(self):
        # metadata.report is persisted as JSON; ensure_ascii=False must round-trip.
        payload = build_progress_payload(
            dev_round=1, plan_summary="中文方案", test_summary="テスト", pr_number=7
        )
        encoded = json.dumps({"report": payload}, ensure_ascii=False)
        decoded = json.loads(encoded)["report"]
        assert decoded["plan_summary"] == "中文方案"
        assert decoded["pr_number"] == 7


# ── render_progress_report / render_progress_report_tldr ────────────────────


class TestRenderProgressReport:
    """Full + one-line rendering from the structured payload, per language."""

    def _full_payload(self):
        return build_progress_payload(
            dev_round=2,
            plan_summary="Implemented the feature",
            diff_stats={"files": 3, "additions": 40, "deletions": 5},
            test_summary="All 12 tests passed",
            review_rounds=2,
            review_passed=True,
            pr_number=1284,
            branch="feat/content-i18n",
            total_tokens=50000,
            total_requests=120,
        )

    def test_full_report_localized_title(self):
        payload = self._full_payload()
        assert "Dev Round 2 Summary" in render_progress_report(payload, "en")
        assert "开发汇总" in render_progress_report(payload, "zh")
        assert "サマリー" in render_progress_report(payload, "ja")
        assert "요약" in render_progress_report(payload, "ko")

    def test_full_report_unknown_language_defaults_to_english(self):
        report = render_progress_report(self._full_payload(), "fr")
        assert "Dev Round 2 Summary" in report
        assert render_progress_report(self._full_payload(), None) == render_progress_report(
            self._full_payload(), "en"
        )

    def test_full_report_token_formatting(self):
        report = render_progress_report(self._full_payload(), "en")
        assert "50,000" in report  # comma grouping

    def test_full_report_conditional_sections(self):
        # No review rounds -> review section omitted.
        payload = build_progress_payload(
            dev_round=1,
            diff_stats={"files": 1, "additions": 1, "deletions": 0},
            total_tokens=100,
            total_requests=1,
        )
        report = render_progress_report(payload, "en")
        assert "Code Review" not in report
        assert "Resources" in report  # resources always render

        # No diff / branch -> changes section omitted.
        payload = build_progress_payload(dev_round=1, total_tokens=0, total_requests=0)
        report = render_progress_report(payload, "en")
        assert "Changes" not in report

    def test_tldr_localized(self):
        payload = self._full_payload()
        tldr_en = render_progress_report_tldr(payload, "en")
        tldr_zh = render_progress_report_tldr(payload, "zh")
        assert "Round 2 summary" in tldr_en
        assert "1284" in tldr_en  # PR number
        assert "开发" not in tldr_en  # English tldr has no zh
        assert "汇总" in tldr_zh  # zh tldr

    def test_tldr_omits_empty_sections(self):
        payload = build_progress_payload(dev_round=1)  # nothing else set
        tldr = render_progress_report_tldr(payload, "en")
        assert tldr == "Round 1 summary"

    def test_tldr_truncates_to_200_chars(self):
        payload = build_progress_payload(
            dev_round=1,
            test_summary="x" * 300,  # long test summary
        )
        assert len(render_progress_report_tldr(payload, "en")) <= 200


# ── _derive_review_passed (structured verdict preferred over substring) ──────


def _review_milestone(metadata=None, review_content=""):
    return {
        "milestone_type": "pr_reviewed",
        "phase": "pr_review",
        "metadata": metadata if metadata is not None else "",
        "review_content": review_content,
    }


class TestDeriveReviewPassed:
    """progress_reported's approval flag prefers the structured verdict."""

    def test_structured_verdict_passed_wins(self):
        ms = _review_milestone(
            metadata=json.dumps({"review_verdict": {"passed": True, "round": 1}}),
            review_content="some text without the phrase",
        )
        # Any content_language: the structured verdict is authoritative.
        assert AutonomousOrchestrator._derive_review_passed([ms], "en") is True
        assert AutonomousOrchestrator._derive_review_passed([ms], None) is True

    def test_structured_verdict_failed_is_respected(self):
        ms = _review_milestone(
            metadata=json.dumps({"review_verdict": {"passed": False, "round": 1}}),
            # Even though the legacy zh marker is present, a structured verdict
            # exists and says NOT passed -> must return False (no substring scan).
            review_content="代码审查通过",
        )
        assert AutonomousOrchestrator._derive_review_passed([ms], "zh") is False

    def test_no_structured_verdict_falls_back_to_language_phrase(self):
        ms_en = _review_milestone(review_content="Code review passed. Looks good.")
        assert AutonomousOrchestrator._derive_review_passed([ms_en], "en") is True
        assert AutonomousOrchestrator._derive_review_passed([ms_en], "zh") is False

    def test_no_structured_verdict_legacy_zh_marker_still_detected(self):
        # A workflow whose reviews predate content_language produced zh text.
        ms = _review_milestone(review_content="代码审查通过。没有遗留问题。")
        # content_language None/legacy: legacy zh marker still recognized.
        assert AutonomousOrchestrator._derive_review_passed([ms], None) is True

    def test_empty_returns_false(self):
        assert AutonomousOrchestrator._derive_review_passed([], "en") is False


# ── _do_report writes a structured payload + localized GitHub comment ────────


def _make_workflow(**overrides):
    base = {
        "workflow_id": "wf-i18n",
        "user_id": 1,
        "title": "I18n Workflow",
        "status": "reporting",
        "current_phase": "report",
        "dev_round": 1,
        "current_round": 0,
        "branch_name": "feat/i18n",
        "github_issue_number": None,
        "github_pr_number": None,
        "total_tokens": 5000,
        "total_requests": 10,
        "content_language": "en",
    }
    base.update(overrides)
    return base


def _make_orchestrator(wf, milestones=None):
    from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

    with (
        patch("app.modules.workspace.autonomous.orchestrator.Database"),
        patch(
            "app.modules.workspace.autonomous.orchestrator.AutonomousWorkflowRepository"
        ) as mock_repo_cls,
    ):
        mock_repo = MagicMock()
        mock_repo.get_workflow.return_value = wf
        mock_repo.list_milestones.return_value = milestones or []
        mock_repo.create_milestone.return_value = {
            "milestone_id": "ms-new",
            "workflow_id": wf["workflow_id"],
        }
        mock_repo.update_workflow.return_value = wf
        mock_repo_cls.return_value = mock_repo

        orch = AutonomousOrchestrator(wf["workflow_id"])
        orch.repo = mock_repo
        orch.emitter = MagicMock()
        orch._gh = MagicMock()
        orch._gh.get_diff_stats.return_value = {
            "additions": 100,
            "deletions": 20,
            "files": 5,
        }
    return orch, mock_repo


class TestDoReportStructuredPayload:
    """_do_report persists a structured payload and renders the comment in-language."""

    def test_milestone_carries_structured_report_payload(self):
        wf = _make_workflow(
            content_language="en",
            github_issue_number=42,
            github_pr_number=99,
        )
        milestones = [
            {
                "milestone_type": "tests_run",
                "status": "completed",
                "result_summary": "All 5 tests passed",
            }
        ]
        orch, mock_repo = _make_orchestrator(wf, milestones=milestones)

        orch._do_report(wf)

        report_ms_dict = mock_repo.create_milestone.call_args_list[0][0][0]
        assert report_ms_dict["milestone_type"] == "progress_reported"
        report = json.loads(report_ms_dict["metadata"])["report"]
        assert report["dev_round"] == 1
        assert report["files"] == 5
        assert report["additions"] == 100
        assert report["deletions"] == 20
        assert report["test_summary"] == "All 5 tests passed"
        assert report["pr_number"] == 99
        # AI-authored content is NOT persisted as localized prose here.
        assert "tldr" not in report_ms_dict
        assert "result_summary" not in report_ms_dict

    def test_github_comment_rendered_in_content_language(self):
        wf = _make_workflow(
            content_language="zh",
            github_issue_number=42,
            github_pr_number=99,
        )
        orch, _ = _make_orchestrator(wf)

        orch._do_report(wf)

        orch._gh.add_issue_comment.assert_called_once()
        body = orch._gh.add_issue_comment.call_args[0][1]
        assert "开发汇总" in body  # zh title
        assert "Dev Round" not in body  # not the en title

    def test_github_comment_defaults_to_english_without_content_language(self):
        wf = _make_workflow(content_language=None, github_issue_number=42)
        orch, _ = _make_orchestrator(wf)

        orch._do_report(wf)

        body = orch._gh.add_issue_comment.call_args[0][1]
        assert "Dev Round 1 Summary" in body


# ── repo content_language normalization (no DB needed) ──────────────────────


class TestContentLanguageNormalization:
    """_normalize_content_language validates and falls back without a DB."""

    @pytest.mark.parametrize("lang", SUPPORTED_LANGUAGES)
    def test_supported_languages_pass_through(self, lang):
        assert AutonomousWorkflowRepository._normalize_content_language(lang) == lang

    def test_unsupported_falls_back_to_default(self):
        assert (
            AutonomousWorkflowRepository._normalize_content_language("fr")
            == DEFAULT_CONTENT_LANGUAGE
        )

    def test_missing_falls_back_to_default(self):
        assert (
            AutonomousWorkflowRepository._normalize_content_language(None)
            == DEFAULT_CONTENT_LANGUAGE
        )

    def test_whitespace_only_value_falls_back(self):
        assert (
            AutonomousWorkflowRepository._normalize_content_language("  ")
            == DEFAULT_CONTENT_LANGUAGE
        )

    def test_allowed_set_matches_supported_languages(self):
        assert tuple(sorted(ALLOWED_CONTENT_LANGUAGES)) == tuple(sorted(SUPPORTED_LANGUAGES))
        assert DEFAULT_CONTENT_LANGUAGE in ALLOWED_CONTENT_LANGUAGES


# ── route-layer _workflow_response normalization (legacy fallback) ───────────


class TestWorkflowResponseNormalization:
    """The API normalizes content_language for legacy workflows (NULL/empty)."""

    def _response(self, workflow):
        # Lazy import: app.routes.autonomous initializes a Flask blueprint.
        from app.routes.autonomous import _workflow_response

        return _workflow_response(workflow)

    @pytest.mark.parametrize("lang", SUPPORTED_LANGUAGES)
    def test_valid_language_passes_through(self, lang):
        out = self._response({"workflow_id": "x", "content_language": lang})
        assert out["content_language"] == lang

    def test_legacy_null_falls_back_to_default(self):
        out = self._response({"workflow_id": "x", "content_language": None})
        assert out["content_language"] == DEFAULT_CONTENT_LANGUAGE

    def test_legacy_empty_falls_back_to_default(self):
        out = self._response({"workflow_id": "x", "content_language": ""})
        assert out["content_language"] == DEFAULT_CONTENT_LANGUAGE

    def test_missing_key_falls_back_to_default(self):
        out = self._response({"workflow_id": "x"})
        assert out["content_language"] == DEFAULT_CONTENT_LANGUAGE

    def test_invalid_falls_back_to_default(self):
        out = self._response({"workflow_id": "x", "content_language": "fr"})
        assert out["content_language"] == DEFAULT_CONTENT_LANGUAGE

    def test_none_workflow_returns_none(self):
        assert self._response(None) is None
