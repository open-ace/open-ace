"""Localized rendering for the system-authored progress report.

``progress_reported`` milestones store a *structured* report payload in the
milestone's ``metadata.report`` key (the single source of truth) instead of a
pre-rendered localized sentence. The one-line summary and the full markdown
report are rendered from that payload on demand.

CONTENT LANGUAGE BOUNDARY (see i18n design doc):
- System-authored structured content (this report) is *deterministic template
  interpolation* — no AI, no translation — so it is rendered in the **viewer's
  current UI language** (``content_language`` arg here is the viewer language).
- AI-authored text embedded in the report (``plan_summary`` / ``test_summary``)
  is inserted verbatim; it was generated in the workflow's content_language and
  is NOT re-translated.

Only the structural labels (section headers, "Files", "Tokens", etc.) are
localized. Numbers use comma grouping universally.
"""

from __future__ import annotations


from typing import Any

PROGRESS_REPORT_SCHEMA_VERSION = 1

# Per-language fragment templates. Placeholders are substituted at render time.
# AI-authored fields (plan_summary, test_summary) are inserted verbatim.
PROGRESS_REPORT_I18N: dict[str, dict[str, str]] = {
    "en": {
        "title": "## 📊 Dev Round {dev_round} Summary\n\n",
        "plan_section": "### 📋 Plan\n{plan_summary}\n\n",
        "changes_header": "### 📝 Changes\n",
        "changes_files": "- Files: {files} changed (+{additions}/-{deletions})\n",
        "changes_branch": "- Branch: `{branch}`\n",
        "tests_section": "### 🧪 Tests\n{test_summary}\n\n",
        "review_header": "### 🔍 Code Review\n",
        "review_rounds": "- Rounds: {review_rounds}\n",
        "review_status_passed": "- Final status: ✅ Passed\n\n",
        "review_status_issues": "- Final status: ⚠️ Issues found\n\n",
        "pr_section": "### 🔗 PR\n- PR #{pr_number}\n\n",
        "resources_header": "### 📈 Resources\n",
        "resources_tokens": "- Tokens: {total_tokens:,}\n",
        "resources_requests": "- API Requests: {total_requests}\n",
        # one-line tldr fragments
        "tldr_round": "Round {dev_round} summary",
        "tldr_files": "{files} files changed (+{additions}/-{deletions})",
        "tldr_tests": "Tests: {test_summary}",
        "tldr_review_passed": "Review: passed ({review_rounds} rounds)",
        "tldr_review_issues": "Review: issues found ({review_rounds} rounds)",
        "tldr_pr": "PR #{pr_number}",
        "tldr_sep": "; ",
    },
    "zh": {
        "title": "## 📊 第 {dev_round} 轮开发汇总\n\n",
        "plan_section": "### 📋 方案\n{plan_summary}\n\n",
        "changes_header": "### 📝 变更\n",
        "changes_files": "- 文件：{files} 个变更（+{additions}/-{deletions}）\n",
        "changes_branch": "- 分支：`{branch}`\n",
        "tests_section": "### 🧪 测试\n{test_summary}\n\n",
        "review_header": "### 🔍 代码审查\n",
        "review_rounds": "- 轮数：{review_rounds}\n",
        "review_status_passed": "- 最终状态：✅ 通过\n\n",
        "review_status_issues": "- 最终状态：⚠️ 发现问题\n\n",
        "pr_section": "### 🔗 PR\n- PR #{pr_number}\n\n",
        "resources_header": "### 📈 资源\n",
        "resources_tokens": "- Token：{total_tokens:,}\n",
        "resources_requests": "- API 请求数：{total_requests}\n",
        "tldr_round": "第 {dev_round} 轮汇总",
        "tldr_files": "{files} 个文件变更（+{additions}/-{deletions}）",
        "tldr_tests": "测试：{test_summary}",
        "tldr_review_passed": "审查：通过（{review_rounds} 轮）",
        "tldr_review_issues": "审查：发现问题（{review_rounds} 轮）",
        "tldr_pr": "PR #{pr_number}",
        "tldr_sep": "；",
    },
    "ja": {
        "title": "## 📊 開発ラウンド {dev_round} サマリー\n\n",
        "plan_section": "### 📋 計画\n{plan_summary}\n\n",
        "changes_header": "### 📝 変更\n",
        "changes_files": "- ファイル：{files} 件変更（+{additions}/-{deletions}）\n",
        "changes_branch": "- ブランチ：`{branch}`\n",
        "tests_section": "### 🧪 テスト\n{test_summary}\n\n",
        "review_header": "### 🔍 コードレビュー\n",
        "review_rounds": "- ラウンド：{review_rounds}\n",
        "review_status_passed": "- 最終ステータス：✅ 合格\n\n",
        "review_status_issues": "- 最終ステータス：⚠️ 問題あり\n\n",
        "pr_section": "### 🔗 PR\n- PR #{pr_number}\n\n",
        "resources_header": "### 📈 リソース\n",
        "resources_tokens": "- トークン：{total_tokens:,}\n",
        "resources_requests": "- API リクエスト：{total_requests}\n",
        "tldr_round": "ラウンド {dev_round} サマリー",
        "tldr_files": "{files} ファイル変更（+{additions}/-{deletions}）",
        "tldr_tests": "テスト：{test_summary}",
        "tldr_review_passed": "レビュー：合格（{review_rounds} ラウンド）",
        "tldr_review_issues": "レビュー：問題あり（{review_rounds} ラウンド）",
        "tldr_pr": "PR #{pr_number}",
        "tldr_sep": "／",
    },
    "ko": {
        "title": "## 📊 개발 라운드 {dev_round} 요약\n\n",
        "plan_section": "### 📋 계획\n{plan_summary}\n\n",
        "changes_header": "### 📝 변경 사항\n",
        "changes_files": "- 파일: {files}개 변경 (+{additions}/-{deletions})\n",
        "changes_branch": "- 브랜치: `{branch}`\n",
        "tests_section": "### 🧪 테스트\n{test_summary}\n\n",
        "review_header": "### 🔍 코드 리뷰\n",
        "review_rounds": "- 라운드: {review_rounds}\n",
        "review_status_passed": "- 최종 상태: ✅ 통과\n\n",
        "review_status_issues": "- 최종 상태: ⚠️ 문제 발견\n\n",
        "pr_section": "### 🔗 PR\n- PR #{pr_number}\n\n",
        "resources_header": "### 📈 리소스\n",
        "resources_tokens": "- 토큰: {total_tokens:,}\n",
        "resources_requests": "- API 요청: {total_requests}\n",
        "tldr_round": "라운드 {dev_round} 요약",
        "tldr_files": "{files}개 파일 변경 (+{additions}/-{deletions})",
        "tldr_tests": "테스트: {test_summary}",
        "tldr_review_passed": "리뷰: 통과 ({review_rounds}라운드)",
        "tldr_review_issues": "리뷰: 문제 발견 ({review_rounds}라운드)",
        "tldr_pr": "PR #{pr_number}",
        "tldr_sep": "; ",
    },
}

DEFAULT_REPORT_LANGUAGE = "en"


def _fragments(language: str | None) -> dict[str, str]:
    lang = language if language in PROGRESS_REPORT_I18N else DEFAULT_REPORT_LANGUAGE
    return PROGRESS_REPORT_I18N[lang]


def build_progress_payload(
    *,
    dev_round: int,
    plan_summary: str = "",
    diff_stats: dict | None = None,
    test_summary: str = "",
    review_rounds: int = 0,
    review_passed: bool = False,
    pr_number: int | None = None,
    branch: str = "",
    total_tokens: int = 0,
    total_requests: int = 0,
) -> dict[str, Any]:
    """Build the structured progress-report payload (the persisted source of truth).

    ``plan_summary`` / ``test_summary`` are AI-authored text inserted verbatim;
    everything else is structured data rendered through i18n templates at view
    time.
    """
    diff_stats = diff_stats or {}
    return {
        "schema_version": PROGRESS_REPORT_SCHEMA_VERSION,
        "dev_round": int(dev_round),
        "plan_summary": plan_summary or "",
        "files": int(diff_stats.get("files", 0) or 0),
        "additions": int(diff_stats.get("additions", 0) or 0),
        "deletions": int(diff_stats.get("deletions", 0) or 0),
        "branch": branch or "",
        "test_summary": test_summary or "",
        "review_rounds": int(review_rounds or 0),
        "review_passed": bool(review_passed),
        "pr_number": pr_number,
        "total_tokens": int(total_tokens or 0),
        "total_requests": int(total_requests or 0),
    }


def render_progress_report(payload: dict[str, Any], language: str | None) -> str:
    """Render the full markdown progress report from a structured payload.

    Sections are only included when their data is present, mirroring the legacy
    behavior. ``language`` is the viewer's UI language (structured content is
    rendered per-viewer; it is not AI-generated).
    """
    f = _fragments(language)
    out = f["title"].format(dev_round=payload.get("dev_round", 1))

    plan_summary = payload.get("plan_summary") or ""
    if plan_summary:
        out += f["plan_section"].format(plan_summary=plan_summary)

    files = payload.get("files", 0)
    additions = payload.get("additions", 0)
    deletions = payload.get("deletions", 0)
    branch = payload.get("branch") or ""
    if files or additions or deletions or branch:
        out += f["changes_header"]
        if files or additions or deletions:
            out += f["changes_files"].format(files=files, additions=additions, deletions=deletions)
        if branch:
            out += f["changes_branch"].format(branch=branch)
        out += "\n"

    test_summary = payload.get("test_summary") or ""
    if test_summary:
        out += f["tests_section"].format(test_summary=test_summary)

    review_rounds = payload.get("review_rounds", 0)
    if review_rounds > 0:
        out += f["review_header"]
        out += f["review_rounds"].format(review_rounds=review_rounds)
        out += (
            f["review_status_passed"] if payload.get("review_passed") else f["review_status_issues"]
        )

    pr_number = payload.get("pr_number")
    if pr_number:
        out += f["pr_section"].format(pr_number=pr_number)

    out += f["resources_header"]
    out += f["resources_tokens"].format(total_tokens=payload.get("total_tokens", 0))
    out += f["resources_requests"].format(total_requests=payload.get("total_requests", 0))
    return out


def render_progress_report_tldr(payload: dict[str, Any], language: str | None) -> str:
    """Render the compact one-line summary from a structured payload.

    Mirrors the legacy TLDR shape but localized. Truncated to 200 chars to fit
    the milestone card.
    """
    f = _fragments(language)
    parts = [f["tldr_round"].format(dev_round=payload.get("dev_round", 1))]

    files = payload.get("files", 0)
    additions = payload.get("additions", 0)
    deletions = payload.get("deletions", 0)
    if files or additions or deletions:
        parts.append(f["tldr_files"].format(files=files, additions=additions, deletions=deletions))

    test_summary = payload.get("test_summary") or ""
    if test_summary:
        parts.append(f["tldr_tests"].format(test_summary=test_summary[:60].rstrip(" ,.;:")))

    review_rounds = payload.get("review_rounds", 0)
    if review_rounds > 0:
        parts.append(
            f["tldr_review_passed"].format(review_rounds=review_rounds)
            if payload.get("review_passed")
            else f["tldr_review_issues"].format(review_rounds=review_rounds)
        )

    pr_number = payload.get("pr_number")
    if pr_number:
        parts.append(f["tldr_pr"].format(pr_number=pr_number))

    return (f["tldr_sep"].join(parts))[:200]
