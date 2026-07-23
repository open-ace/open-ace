"""Regression tests for structured autonomous PR review results."""

from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator


def _approved(body: str) -> bool:
    return AutonomousOrchestrator._review_is_approved(body, "ignored localized marker")


def test_structured_approve_with_empty_blockers_is_accepted():
    text = 'All criteria pass.\nREVIEW_RESULT: {"verdict":"APPROVE","blocking_findings":[]}'

    assert _approved(text)


def test_request_changes_is_rejected():
    text = (
        'REVIEW_RESULT: {"verdict":"REQUEST_CHANGES",' '"blocking_findings":["P0 tests missing"]}'
    )

    assert not _approved(text)


def test_approve_with_blockers_is_rejected_regardless_of_natural_language():
    text = (
        "P0 は解決済みですが、P1 認証バグは未解決です。\n"
        'REVIEW_RESULT: {"verdict":"APPROVE",'
        '"blocking_findings":["P1 認証バグ"]}'
    )

    assert not _approved(text)


def test_korean_request_changes_is_language_neutral():
    text = (
        "P1 인증 버그가 남아 있습니다.\n"
        'REVIEW_RESULT: {"verdict":"REQUEST_CHANGES",'
        '"blocking_findings":["P1 인증 버그"]}'
    )

    assert not _approved(text)


def test_missing_structured_result_fails_closed():
    assert not _approved("代码审查通过。没有遗留问题。")


def test_old_verdict_marker_without_result_fails_closed():
    assert not _approved("REVIEW_VERDICT: APPROVE")


def test_malformed_json_fails_closed():
    assert not _approved("REVIEW_RESULT: {not-json}")


def test_missing_blocking_findings_fails_closed():
    assert not _approved('REVIEW_RESULT: {"verdict":"APPROVE"}')


def test_non_list_blocking_findings_fails_closed():
    text = 'REVIEW_RESULT: {"verdict":"APPROVE","blocking_findings":"none"}'

    assert not _approved(text)


def test_empty_blocker_strings_fail_closed():
    text = 'REVIEW_RESULT: {"verdict":"APPROVE","blocking_findings":[""]}'

    assert not _approved(text)


def test_multiple_structured_results_fail_closed():
    text = (
        'REVIEW_RESULT: {"verdict":"APPROVE","blocking_findings":[]}\n'
        'REVIEW_RESULT: {"verdict":"REQUEST_CHANGES",'
        '"blocking_findings":["late finding"]}'
    )

    assert not _approved(text)


def test_valid_approve_followed_by_correction_fails_closed():
    text = (
        'REVIEW_RESULT: {"verdict":"APPROVE","blocking_findings":[]}\n'
        "Actually, P1 authentication is blocking.\n"
        "TL;DR: changes requested"
    )

    assert not _approved(text)


def test_result_inside_markdown_fence_fails_closed():
    text = (
        "Example output:\n```json\n"
        'REVIEW_RESULT: {"verdict":"APPROVE","blocking_findings":[]}\n'
        "```"
    )

    assert not _approved(text)


def test_later_malformed_result_invalidates_earlier_approve():
    text = (
        'REVIEW_RESULT: {"verdict":"APPROVE","blocking_findings":[]}\n' "REVIEW_RESULT: unavailable"
    )

    assert not _approved(text)


def test_tldr_is_the_only_allowed_line_after_result():
    text = (
        "All acceptance criteria pass.\n"
        'REVIEW_RESULT: {"verdict":"APPROVE","blocking_findings":[]}\n'
        "TL;DR: approved"
    )

    assert _approved(text)
