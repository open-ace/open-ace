/**
 * Localized rendering for the system-authored progress report (mirrors the
 * backend `progress_report_i18n.py`).
 *
 * `progress_reported` milestones store a structured payload in
 * `metadata.report` (single source of truth). The one-line summary and full
 * markdown report are rendered here from that payload in the VIEWER's current
 * UI language — this is deterministic template interpolation (no AI, no
 * translation), which is why structured content can safely render per-viewer
 * while AI-authored content (plan/test summaries embedded below) is inserted
 * verbatim in the workflow's content_language.
 */
import type { Language } from '@/types';
import type { WorkflowMilestone } from '@/api/autonomous';

export interface ProgressReportPayload {
  schema_version?: number;
  dev_round: number;
  plan_summary?: string;
  files: number;
  additions: number;
  deletions: number;
  branch?: string;
  test_summary?: string;
  review_rounds: number;
  review_passed: boolean;
  pr_number?: number | null;
  total_tokens: number;
  total_requests: number;
}

interface ReportFragments {
  title: string;
  plan_section: string;
  changes_header: string;
  changes_files: string;
  changes_branch: string;
  tests_section: string;
  review_header: string;
  review_rounds: string;
  review_status_passed: string;
  review_status_issues: string;
  pr_section: string;
  resources_header: string;
  resources_tokens: string;
  resources_requests: string;
  tldr_round: string;
  tldr_files: string;
  tldr_tests: string;
  tldr_review_passed: string;
  tldr_review_issues: string;
  tldr_pr: string;
  tldr_sep: string;
}

const PROGRESS_REPORT_I18N: Record<Language, ReportFragments> = {
  en: {
    title: '## 📊 Dev Round {dev_round} Summary\n\n',
    plan_section: '### 📋 Plan\n{plan_summary}\n\n',
    changes_header: '### 📝 Changes\n',
    changes_files: '- Files: {files} changed (+{additions}/-{deletions})\n',
    changes_branch: '- Branch: `{branch}`\n',
    tests_section: '### 🧪 Tests\n{test_summary}\n\n',
    review_header: '### 🔍 Code Review\n',
    review_rounds: '- Rounds: {review_rounds}\n',
    review_status_passed: '- Final status: ✅ Passed\n\n',
    review_status_issues: '- Final status: ⚠️ Issues found\n\n',
    pr_section: '### 🔗 PR\n- PR #{pr_number}\n\n',
    resources_header: '### 📈 Resources\n',
    resources_tokens: '- Tokens: {total_tokens}\n',
    resources_requests: '- API Requests: {total_requests}\n',
    tldr_round: 'Round {dev_round} summary',
    tldr_files: '{files} files changed (+{additions}/-{deletions})',
    tldr_tests: 'Tests: {test_summary}',
    tldr_review_passed: 'Review: passed ({review_rounds} rounds)',
    tldr_review_issues: 'Review: issues found ({review_rounds} rounds)',
    tldr_pr: 'PR #{pr_number}',
    tldr_sep: '; ',
  },
  zh: {
    title: '## 📊 第 {dev_round} 轮开发汇总\n\n',
    plan_section: '### 📋 方案\n{plan_summary}\n\n',
    changes_header: '### 📝 变更\n',
    changes_files: '- 文件：{files} 个变更（+{additions}/-{deletions}）\n',
    changes_branch: '- 分支：`{branch}`\n',
    tests_section: '### 🧪 测试\n{test_summary}\n\n',
    review_header: '### 🔍 代码审查\n',
    review_rounds: '- 轮数：{review_rounds}\n',
    review_status_passed: '- 最终状态：✅ 通过\n\n',
    review_status_issues: '- 最终状态：⚠️ 发现问题\n\n',
    pr_section: '### 🔗 PR\n- PR #{pr_number}\n\n',
    resources_header: '### 📈 资源\n',
    resources_tokens: '- Token：{total_tokens}\n',
    resources_requests: '- API 请求数：{total_requests}\n',
    tldr_round: '第 {dev_round} 轮汇总',
    tldr_files: '{files} 个文件变更（+{additions}/-{deletions}）',
    tldr_tests: '测试：{test_summary}',
    tldr_review_passed: '审查：通过（{review_rounds} 轮）',
    tldr_review_issues: '审查：发现问题（{review_rounds} 轮）',
    tldr_pr: 'PR #{pr_number}',
    tldr_sep: '；',
  },
  ja: {
    title: '## 📊 開発ラウンド {dev_round} サマリー\n\n',
    plan_section: '### 📋 計画\n{plan_summary}\n\n',
    changes_header: '### 📝 変更\n',
    changes_files: '- ファイル：{files} 件変更（+{additions}/-{deletions}）\n',
    changes_branch: '- ブランチ：`{branch}`\n',
    tests_section: '### 🧪 テスト\n{test_summary}\n\n',
    review_header: '### 🔍 コードレビュー\n',
    review_rounds: '- ラウンド：{review_rounds}\n',
    review_status_passed: '- 最終ステータス：✅ 合格\n\n',
    review_status_issues: '- 最終ステータス：⚠️ 問題あり\n\n',
    pr_section: '### 🔗 PR\n- PR #{pr_number}\n\n',
    resources_header: '### 📈 リソース\n',
    resources_tokens: '- トークン：{total_tokens}\n',
    resources_requests: '- API リクエスト：{total_requests}\n',
    tldr_round: 'ラウンド {dev_round} サマリー',
    tldr_files: '{files} ファイル変更（+{additions}/-{deletions}）',
    tldr_tests: 'テスト：{test_summary}',
    tldr_review_passed: 'レビュー：合格（{review_rounds} ラウンド）',
    tldr_review_issues: 'レビュー：問題あり（{review_rounds} ラウンド）',
    tldr_pr: 'PR #{pr_number}',
    tldr_sep: '／',
  },
  ko: {
    title: '## 📊 개발 라운드 {dev_round} 요약\n\n',
    plan_section: '### 📋 계획\n{plan_summary}\n\n',
    changes_header: '### 📝 변경 사항\n',
    changes_files: '- 파일: {files}개 변경 (+{additions}/-{deletions})\n',
    changes_branch: '- 브랜치: `{branch}`\n',
    tests_section: '### 🧪 테스트\n{test_summary}\n\n',
    review_header: '### 🔍 코드 리뷰\n',
    review_rounds: '- 라운드: {review_rounds}\n',
    review_status_passed: '- 최종 상태: ✅ 통과\n\n',
    review_status_issues: '- 최종 상태: ⚠️ 문제 발견\n\n',
    pr_section: '### 🔗 PR\n- PR #{pr_number}\n\n',
    resources_header: '### 📈 리소스\n',
    resources_tokens: '- 토큰: {total_tokens}\n',
    resources_requests: '- API 요청: {total_requests}\n',
    tldr_round: '라운드 {dev_round} 요약',
    tldr_files: '{files}개 파일 변경 (+{additions}/-{deletions})',
    tldr_tests: '테스트: {test_summary}',
    tldr_review_passed: '리뷰: 통과 ({review_rounds}라운드)',
    tldr_review_issues: '리뷰: 문제 발견 ({review_rounds}라운드)',
    tldr_pr: 'PR #{pr_number}',
    tldr_sep: '; ',
  },
};

const DEFAULT_REPORT_LANGUAGE: Language = 'en';

/** Format an integer with comma grouping (universal in technical reports). */
function formatNumber(value: number | undefined | null): string {
  const n = Number(value ?? 0);
  return n.toLocaleString('en-US');
}

function fragments(language: Language | undefined): ReportFragments {
  return PROGRESS_REPORT_I18N[
    language && language in PROGRESS_REPORT_I18N ? (language as Language) : DEFAULT_REPORT_LANGUAGE
  ];
}

/** Parse the structured progress-report payload from a milestone, if present. */
export function parseProgressReport(milestone: WorkflowMilestone): ProgressReportPayload | null {
  const raw = milestone?.metadata;
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as { report?: ProgressReportPayload };
    if (parsed?.report && typeof parsed.report.dev_round === 'number') {
      return parsed.report;
    }
  } catch {
    return null;
  }
  return null;
}

/** Render the compact one-line summary in the viewer's language. */
export function renderProgressReportTldr(
  payload: ProgressReportPayload,
  language: Language | undefined
): string {
  const f = fragments(language);
  const parts = [f.tldr_round.replace('{dev_round}', String(payload.dev_round))];
  if (payload.files || payload.additions || payload.deletions) {
    parts.push(
      f.tldr_files
        .replace('{files}', String(payload.files))
        .replace('{additions}', String(payload.additions))
        .replace('{deletions}', String(payload.deletions))
    );
  }
  const testSummary = (payload.test_summary ?? '').slice(0, 60).replace(/[\s,.;:]+$/, '');
  if (testSummary) {
    parts.push(f.tldr_tests.replace('{test_summary}', testSummary));
  }
  if (payload.review_rounds > 0) {
    parts.push(
      (payload.review_passed ? f.tldr_review_passed : f.tldr_review_issues).replace(
        '{review_rounds}',
        String(payload.review_rounds)
      )
    );
  }
  if (payload.pr_number) {
    parts.push(f.tldr_pr.replace('{pr_number}', String(payload.pr_number)));
  }
  return parts.join(f.tldr_sep).slice(0, 200);
}

/** Render the full markdown progress report in the viewer's language. */
export function renderProgressReport(
  payload: ProgressReportPayload,
  language: Language | undefined
): string {
  const f = fragments(language);
  let out = f.title.replace('{dev_round}', String(payload.dev_round));

  const planSummary = payload.plan_summary ?? '';
  if (planSummary) {
    out += f.plan_section.replace('{plan_summary}', planSummary);
  }

  const branch = payload.branch ?? '';
  if (payload.files || payload.additions || payload.deletions || branch) {
    out += f.changes_header;
    if (payload.files || payload.additions || payload.deletions) {
      out += f.changes_files
        .replace('{files}', String(payload.files))
        .replace('{additions}', String(payload.additions))
        .replace('{deletions}', String(payload.deletions));
    }
    if (branch) {
      out += f.changes_branch.replace('{branch}', branch);
    }
    out += '\n';
  }

  const testSummary = payload.test_summary ?? '';
  if (testSummary) {
    out += f.tests_section.replace('{test_summary}', testSummary);
  }

  if (payload.review_rounds > 0) {
    out += f.review_header;
    out += f.review_rounds.replace('{review_rounds}', String(payload.review_rounds));
    out += payload.review_passed ? f.review_status_passed : f.review_status_issues;
  }

  if (payload.pr_number) {
    out += f.pr_section.replace('{pr_number}', String(payload.pr_number));
  }

  out += f.resources_header;
  out += f.resources_tokens.replace('{total_tokens}', formatNumber(payload.total_tokens));
  out += f.resources_requests.replace('{total_requests}', formatNumber(payload.total_requests));
  return out;
}

/**
 * Resolve the rendered view for a progress_reported milestone in the viewer's
 * language. Returns null when the milestone has no structured payload (legacy
 * milestones), so callers fall back to verbatim tldr/result_summary rendering.
 */
export function getProgressReportView(
  milestone: WorkflowMilestone,
  language: Language | undefined
): { tldr: string; fullReport: string } | null {
  const payload = parseProgressReport(milestone);
  if (!payload) return null;
  return {
    tldr: renderProgressReportTldr(payload, language),
    fullReport: renderProgressReport(payload, language),
  };
}
