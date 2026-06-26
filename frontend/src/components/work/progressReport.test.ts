/**
 * Tests for the system-authored progress report renderer.
 *
 * progress_reported milestones store a structured payload in metadata.report
 * (single source of truth). The one-line summary + full report render from
 * that payload in the VIEWER's UI language (deterministic template
 * interpolation — no AI). Legacy milestones without a payload fall back to
 * verbatim tldr/result_summary, so getProgressReportView returns null.
 */

import { describe, it, expect } from 'vitest';
import type { WorkflowMilestone } from '@/api/autonomous';
import {
  parseProgressReport,
  renderProgressReportTldr,
  renderProgressReport,
  getProgressReportView,
  type ProgressReportPayload,
} from './progressReport';

const FULL_PAYLOAD: ProgressReportPayload = {
  schema_version: 1,
  dev_round: 2,
  plan_summary: 'Implemented the feature',
  files: 3,
  additions: 40,
  deletions: 5,
  branch: 'feat/content-i18n',
  test_summary: 'All 12 tests passed',
  review_rounds: 2,
  review_passed: true,
  pr_number: 1284,
  total_tokens: 50000,
  total_requests: 120,
};

function milestoneWith(report: ProgressReportPayload | null): WorkflowMilestone {
  return {
    milestone_id: 'ms-1',
    workflow_id: 'wf-1',
    milestone_type: 'progress_reported',
    status: 'completed',
    phase: 'report',
    metadata: report ? JSON.stringify({ report }) : '',
  } as WorkflowMilestone;
}

describe('parseProgressReport', () => {
  it('parses a valid structured payload', () => {
    const payload = parseProgressReport(milestoneWith(FULL_PAYLOAD));
    expect(payload).not.toBeNull();
    expect(payload?.dev_round).toBe(2);
    expect(payload?.files).toBe(3);
    expect(payload?.pr_number).toBe(1284);
  });

  it('returns null when metadata is empty (legacy milestone)', () => {
    expect(parseProgressReport(milestoneWith(null))).toBeNull();
  });

  it('returns null for malformed JSON', () => {
    const ms = milestoneWith(null);
    ms.metadata = 'not json';
    expect(parseProgressReport(ms)).toBeNull();
  });

  it('returns null when report key is absent', () => {
    const ms = milestoneWith(null);
    ms.metadata = JSON.stringify({ other: { dev_round: 1 } });
    expect(parseProgressReport(ms)).toBeNull();
  });
});

describe('renderProgressReportTldr', () => {
  it('renders in English', () => {
    const tldr = renderProgressReportTldr(FULL_PAYLOAD, 'en');
    expect(tldr).toContain('Round 2 summary');
    expect(tldr).toContain('1284');
    expect(tldr).not.toContain('汇总');
  });

  it('renders in Chinese', () => {
    const tldr = renderProgressReportTldr(FULL_PAYLOAD, 'zh');
    expect(tldr).toContain('汇总');
  });

  it('renders in Japanese and Korean', () => {
    expect(renderProgressReportTldr(FULL_PAYLOAD, 'ja')).toContain('サマリー');
    expect(renderProgressReportTldr(FULL_PAYLOAD, 'ko')).toContain('요약');
  });

  it('falls back to English for unknown language', () => {
    expect(renderProgressReportTldr(FULL_PAYLOAD, 'fr')).toContain('Round 2 summary');
  });

  it('omits empty sections', () => {
    const minimal: ProgressReportPayload = {
      dev_round: 1,
      files: 0,
      additions: 0,
      deletions: 0,
      review_rounds: 0,
      review_passed: false,
      total_tokens: 0,
      total_requests: 0,
    };
    expect(renderProgressReportTldr(minimal, 'en')).toBe('Round 1 summary');
  });

  it('truncates to 200 characters', () => {
    const long: ProgressReportPayload = { ...FULL_PAYLOAD, test_summary: 'x'.repeat(300) };
    expect(renderProgressReportTldr(long, 'en').length).toBeLessThanOrEqual(200);
  });
});

describe('renderProgressReport', () => {
  it('localizes the title per language', () => {
    expect(renderProgressReport(FULL_PAYLOAD, 'en')).toContain('Dev Round 2 Summary');
    expect(renderProgressReport(FULL_PAYLOAD, 'zh')).toContain('开发汇总');
    expect(renderProgressReport(FULL_PAYLOAD, 'ja')).toContain('サマリー');
    expect(renderProgressReport(FULL_PAYLOAD, 'ko')).toContain('요약');
  });

  it('formats tokens with comma grouping', () => {
    expect(renderProgressReport(FULL_PAYLOAD, 'en')).toContain('50,000');
  });

  it('always renders the resources section', () => {
    expect(renderProgressReport(FULL_PAYLOAD, 'en')).toContain('Resources');
  });

  it('omits the review section when there are no review rounds', () => {
    const noReview: ProgressReportPayload = { ...FULL_PAYLOAD, review_rounds: 0 };
    expect(renderProgressReport(noReview, 'en')).not.toContain('Code Review');
  });
});

describe('getProgressReportView', () => {
  it('returns a rendered view for a payload milestone', () => {
    const view = getProgressReportView(milestoneWith(FULL_PAYLOAD), 'en');
    expect(view).not.toBeNull();
    expect(view?.tldr).toContain('Round 2 summary');
    expect(view?.fullReport).toContain('Dev Round 2 Summary');
  });

  it('returns null for a legacy milestone without a payload (verbatim fallback)', () => {
    expect(getProgressReportView(milestoneWith(null), 'en')).toBeNull();
  });
});
