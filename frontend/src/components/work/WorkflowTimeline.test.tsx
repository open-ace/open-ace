/**
 * WorkflowTimeline tests — paused-state banner variants (#2634)
 *
 * Covers the acceptance-awaiting / quota / manual pause distinction and the
 * resume-with-feedback entry point for rejected acceptance workflows.
 */

import { format as formatDateTime } from 'date-fns';
import { fireEvent, render, screen, within } from '@/test/utils';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import {
  WorkflowTimeline,
  formatAcceptanceReport,
  getAcceptanceSummaryDetail,
  MILESTONE_DISPLAY,
  MILESTONE_ICON_COLORS,
} from './WorkflowTimeline';
import { useMilestoneSession, useWorkflowTimeline } from '@/hooks/useAutonomous';
import type { AutonomousWorkflow, WorkflowMilestone } from '@/api/autonomous';

const { mockResumeWithFeedbackMutate, mockAcceptanceOverrideMutate, pendingMutation } = vi.hoisted(
  () => ({
    mockResumeWithFeedbackMutate: vi.fn(),
    mockAcceptanceOverrideMutate: vi.fn(),
    pendingMutation: () => ({ mutate: vi.fn(), isPending: false }),
  })
);

vi.mock('@/hooks/useAutonomous', () => ({
  useWorkflowTimeline: vi.fn(() => ({ data: { milestones: [] }, isLoading: false })),
  useWorkflowActivity: vi.fn(() => []),
  useWorkflow: vi.fn(() => ({ data: undefined })),
  useWorkflowForks: vi.fn(() => ({ data: { forks: [] } })),
  useWorkflowPrStats: vi.fn(() => ({ data: undefined })),
  useWorkflowPrDiff: vi.fn(() => ({ data: undefined, isLoading: false })),
  useMilestoneSession: vi.fn(() => ({ data: undefined, isLoading: false })),
  useMilestoneDiff: vi.fn(() => ({ data: undefined, isLoading: false })),
  usePauseWorkflow: pendingMutation,
  useResumeWorkflow: pendingMutation,
  useStopWorkflow: pendingMutation,
  useMarkDone: pendingMutation,
  useRetryWorkflow: pendingMutation,
  useExtendPlanningTimeout: pendingMutation,
  useResumeWithFeedback: () => ({
    mutate: mockResumeWithFeedbackMutate,
    isPending: false,
  }),
  useAcceptanceOverride: () => ({
    mutate: mockAcceptanceOverrideMutate,
    isPending: false,
  }),
}));

function pausedWorkflow(overrides: Partial<AutonomousWorkflow> = {}): AutonomousWorkflow {
  return {
    workflow_id: 'wf-1',
    title: 'Acceptance workflow',
    status: 'paused',
    requirements_text: '',
    requirements_issue_url: '',
    project_path: '/tmp/project',
    project_repo_url: '',
    is_new_project: false,
    cli_tool: 'claude-code',
    model: '',
    permission_mode: 'auto-edit',
    branch_name: 'feature-x',
    branch_strategy: 'new-branch',
    workspace_type: 'local',
    remote_machine_id: '',
    worktree_path: '',
    github_issue_number: 2634,
    github_pr_number: 42,
    github_pr_url: 'https://github.com/open-ace/open-ace/pull/42',
    batch_id: null,
    batch_order: null,
    batch_total: null,
    auto_merge: true,
    definition_snapshot: null,
    current_phase: 'acceptance_verification',
    current_round: 1,
    dev_round: 1,
    max_plan_rounds: 3,
    max_pr_review_rounds: 5,
    total_tokens: 0,
    total_input_tokens: 0,
    total_output_tokens: 0,
    total_requests: 0,
    error_message: 'Acceptance verification rejected; awaiting review',
    content_language: 'en',
    parent_workflow_id: null,
    fork_milestone_id: null,
    user_feedback: '',
    original_branch_name: '',
    created_at: '2026-08-01T00:00:00Z',
    updated_at: '2026-08-05T00:00:00Z',
    completed_at: null,
    paused_at: '2026-08-05T00:00:00Z',
    verification_status: 'rejected',
    ...overrides,
  } as AutonomousWorkflow;
}

function renderTimeline(workflow: AutonomousWorkflow) {
  return render(<WorkflowTimeline workflow={workflow} />);
}

function stateBanner(container: ReturnType<typeof render>['container']) {
  const banner = container.querySelector('.timeline-state-banner');
  if (!banner) throw new Error('state banner not rendered');
  return banner;
}

describe('WorkflowTimeline paused-state banner (#2634)', () => {
  beforeEach(() => {
    mockResumeWithFeedbackMutate.mockReset();
    mockAcceptanceOverrideMutate.mockReset();
  });

  it('shows the awaiting-human banner variant for paused @ acceptance_verification', () => {
    const { container } = renderTimeline(pausedWorkflow());

    const banner = stateBanner(container);
    expect(banner.className).toContain('timeline-state-banner--acceptance');
    expect(within(banner).getByText('Awaiting human acceptance review')).toBeInTheDocument();
    // Existing message text is still displayed.
    expect(
      within(banner).getByText('Acceptance verification rejected; awaiting review')
    ).toBeInTheDocument();
  });

  it('labels quota pauses distinctly and shows no resume-with-feedback entry', () => {
    const { container } = renderTimeline(
      pausedWorkflow({
        current_phase: 'developing',
        error_message: 'Quota exceeded: daily usage at 100% (1000/1000)',
      })
    );

    const banner = stateBanner(container);
    expect(within(banner).getByText('Quota paused')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /resume with feedback/i })).not.toBeInTheDocument();
  });

  it('keeps the generic paused banner for manual pauses', () => {
    const { container } = renderTimeline(
      pausedWorkflow({ current_phase: 'developing', error_message: '' })
    );

    const banner = stateBanner(container);
    expect(within(banner).getByText('Paused')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /resume with feedback/i })).not.toBeInTheDocument();
  });

  it('#2658 shows BOTH exits (override + resume-with-feedback) for rejected AND indeterminate acceptance pauses', () => {
    for (const status of ['rejected', 'indeterminate'] as const) {
      const view = renderTimeline(pausedWorkflow({ verification_status: status }));
      // The banner button's accessible name is the help text (title -> aria-label).
      expect(screen.getByRole('button', { name: /new development round/i })).toBeInTheDocument();
      // Override button: accessible name is its descriptive title (reworded for
      // #2658 — covers rejected-overturn and indeterminate alike).
      expect(
        screen.getByRole('button', { name: /accept the delivered result/i })
      ).toBeInTheDocument();
      view.unmount();
    }
  });

  it('opens the feedback modal, blocks empty submit, and submits valid feedback', () => {
    renderTimeline(pausedWorkflow({ verification_status: 'rejected' }));

    fireEvent.click(screen.getByRole('button', { name: /new development round/i }));

    const dialog = screen.getByRole('dialog');
    expect(within(dialog).getByText(/new development round/i)).toBeInTheDocument();

    const textarea = within(dialog).getByRole('textbox');
    const submit = within(dialog).getByRole('button', { name: /resume with feedback/i });
    expect(submit).toBeDisabled();

    fireEvent.change(textarea, { target: { value: '   ' } });
    expect(submit).toBeDisabled();

    fireEvent.change(textarea, { target: { value: 'Please fix the flaky e2e test.' } });
    expect(submit).not.toBeDisabled();
    fireEvent.click(submit);

    expect(mockResumeWithFeedbackMutate).toHaveBeenCalledWith(
      { workflowId: 'wf-1', feedback: 'Please fix the flaky e2e test.' },
      expect.objectContaining({ onSuccess: expect.any(Function) })
    );
  });

  it('closes the feedback modal after a successful submit', () => {
    mockResumeWithFeedbackMutate.mockImplementation(
      (_vars: unknown, options?: { onSuccess?: () => void }) => {
        options?.onSuccess?.();
      }
    );

    renderTimeline(pausedWorkflow({ verification_status: 'rejected' }));
    fireEvent.click(screen.getByRole('button', { name: /new development round/i }));

    const dialog = screen.getByRole('dialog');
    fireEvent.change(within(dialog).getByRole('textbox'), {
      target: { value: 'Re-run with the new fixture.' },
    });
    fireEvent.click(within(dialog).getByRole('button', { name: /resume with feedback/i }));

    expect(mockResumeWithFeedbackMutate).toHaveBeenCalled();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });
});

describe('WorkflowTimeline feedback-pending restart state (#2491 UX)', () => {
  beforeEach(() => {
    mockResumeWithFeedbackMutate.mockReset();
    mockAcceptanceOverrideMutate.mockReset();
  });

  it('shows the feedback-received banner and hides Complete Development for waiting+feedback rows', () => {
    const { container } = renderTimeline(
      pausedWorkflow({
        status: 'waiting',
        current_phase: 'wait',
        user_feedback: 'wire CI lanes before re-verify',
        error_message: '',
      })
    );

    const banner = stateBanner(container);
    expect(banner.className).toContain('timeline-state-banner--feedback');
    expect(
      within(banner).getByText('Feedback received — a new development round will start shortly')
    ).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /complete development/i })).not.toBeInTheDocument();
  });

  it('keeps Complete Development for waiting rows WITHOUT feedback', () => {
    renderTimeline(
      pausedWorkflow({
        status: 'waiting',
        current_phase: 'wait',
        user_feedback: '',
        error_message: '',
      })
    );

    expect(screen.getByRole('button', { name: /complete development/i })).toBeInTheDocument();
  });

  it('development-phase rows with leftover user_feedback do not show the feedback banner', () => {
    const { container } = renderTimeline(
      pausedWorkflow({
        status: 'developing',
        current_phase: 'development',
        user_feedback: 'wire CI lanes',
        error_message: '',
      })
    );

    expect(container.querySelector('.timeline-state-banner--feedback')).toBeNull();
  });

  it('pre-fills the feedback modal with the verifier failed-items prefill', () => {
    const prefill =
      'Rejected / missing:\n- [verifier] `wire CI lanes` (rejected) — no producer job in workflows';
    renderTimeline(
      pausedWorkflow({ verification_status: 'rejected', acceptance_feedback_prefill: prefill })
    );

    fireEvent.click(screen.getByRole('button', { name: /new development round/i }));

    const dialog = screen.getByRole('dialog');
    const textarea = within(dialog).getByRole('textbox');
    expect(textarea).toHaveValue(prefill);
    const submit = within(dialog).getByRole('button', { name: /resume with feedback/i });
    expect(submit).not.toBeDisabled();
  });
});

describe('formatAcceptanceReport (#2658)', () => {
  it('renders markdown structure: status heading, meta list, sections, nested evidence', () => {
    const report = {
      merge_sha: 'abc123',
      status: 'rejected',
      verified_by: 'glm-5',
      scope: [{ item: 'src/app.py', verdict: 'confirmed', evidence: [] }],
      gates: [],
      verifier: [
        {
          item: '修复登录失败',
          verdict: 'rejected',
          evidence: [{ ref: 'tests/test_login.py:12', note: '用例仍然失败' }],
          rationale: '合并后用例依旧失败',
        },
      ],
    };
    const out = formatAcceptanceReport(JSON.stringify(report), 'en');
    // Status heading (i18n label) + meta bullet list items on separate lines.
    expect(out).toContain('## ❌ Not accepted');
    expect(out).toContain('- **Merge SHA:** `abc123`');
    expect(out).toContain('- **Verifier:** `glm-5`');
    // Section subheadings.
    expect(out).toContain('### Scope check');
    // Verdict item as a bolded list entry with rationale tail.
    expect(out).toContain('- ✅ **src/app.py**');
    expect(out).toContain('- ❌ **修复登录失败** — 合并后用例依旧失败');
    // Evidence as a NESTED list item (2-space indent — never a 4-space code
    // block) with the ref in inline code.
    expect(out).toContain('  - ↳ `tests/test_login.py:12`（用例仍然失败）');
    // Empty sections are skipped (gates omitted above).
    expect(out).not.toContain('Mechanical gates');
    // No plain-text emission artifacts (soft-wrap paragraphs / code blocks).
    expect(out).not.toContain('──');
    expect(out).not.toMatch(/^ {4}/);
  });

  it('localizes section headings and status label', () => {
    const out = formatAcceptanceReport(
      JSON.stringify({
        status: 'rejected',
        verifier: [{ item: 'x', verdict: 'rejected', evidence: [] }],
      }),
      'zh'
    );
    expect(out).toContain('## ❌ 未通过');
    expect(out).toContain('### 验收器结论');
  });

  it('falls back to raw metadata when the JSON is malformed', () => {
    expect(formatAcceptanceReport('not-json{', 'en')).toBe('not-json{');
  });

  it('renders advisory scope entries with the info tone (#2982)', () => {
    const out = formatAcceptanceReport(
      JSON.stringify({
        status: 'confirmed',
        scope: [
          { item: 'src/app.py', verdict: 'confirmed', evidence: [] },
          { item: 'src/other.py', verdict: 'advisory', evidence: [] },
        ],
      }),
      'en'
    );
    expect(out).toContain('- ✅ **src/app.py**');
    // Advisory is informational (never a failure icon) but still visible.
    expect(out).toContain('- ℹ️ **src/other.py**');
  });

  it('omits optional fields gracefully', () => {
    const out = formatAcceptanceReport(JSON.stringify({ status: 'indeterminate' }), 'en');
    expect(out).toContain('## ⚠️ Inconclusive');
    expect(out).not.toContain('Merge SHA');
    expect(out).not.toContain('Infra error');
  });
});

// ── Acceptance summary + verdict status (#2985) ────────────────────────

function acceptanceReport(
  status: string,
  groups: { scope?: unknown[]; gates?: unknown[]; verifier?: unknown[] } = {}
): string {
  return JSON.stringify({ status, scope: [], gates: [], verifier: [], ...groups });
}

describe('getAcceptanceSummaryDetail (#2985)', () => {
  it('builds a localized confirmed summary with per-group counts', () => {
    const report = acceptanceReport('confirmed', {
      scope: [{ item: 'a.py', verdict: 'confirmed' }],
      gates: [],
      verifier: [
        { item: 'x', verdict: 'confirmed' },
        { item: 'y', verdict: 'confirmed' },
        { item: 'z', verdict: 'confirmed' },
      ],
    });
    // Empty groups are omitted rather than shown as 0/0.
    expect(getAcceptanceSummaryDetail(report, 'en')).toBe(
      'Required paths 1/1 · Semantic checks 3/3'
    );
  });

  it('lists failed item names for a rejected report and caps at three', () => {
    const report = acceptanceReport('rejected', {
      scope: [
        { item: 'a.py', verdict: 'rejected' },
        { item: 'b.py', verdict: 'rejected' },
        { item: 'very/long/path/that/keeps/going/and/going/and/going/x.py', verdict: 'rejected' },
        { item: 'd.py', verdict: 'rejected' },
      ],
      verifier: [{ item: 'ok', verdict: 'confirmed' }],
    });
    const detail = getAcceptanceSummaryDetail(report, 'en');
    expect(detail).toContain('Failed: a.py, b.py');
    expect(detail).toContain('(+1 items)');
    expect(detail).not.toContain('d.py');
  });

  it('labels unresolved items for an indeterminate report', () => {
    const report = acceptanceReport('indeterminate', {
      gates: [{ item: 'deployment:artifacts', verdict: 'indeterminate' }],
    });
    expect(getAcceptanceSummaryDetail(report, 'en')).toBe('Unresolved: deployment:artifacts');
  });

  it('appends the advisory count and excludes advisory from failures', () => {
    const report = acceptanceReport('confirmed', {
      scope: [
        { item: 'a.py', verdict: 'confirmed' },
        { item: 'other.py', verdict: 'advisory' },
      ],
    });
    expect(getAcceptanceSummaryDetail(report, 'en')).toBe(
      'Required paths 1/2 · advisory (not counted) 1'
    );
  });

  it('renders the zh summary with localized labels and separators', () => {
    const report = acceptanceReport('rejected', {
      scope: [
        { item: 'a.py', verdict: 'rejected' },
        { item: 'b.py', verdict: 'rejected' },
      ],
    });
    expect(getAcceptanceSummaryDetail(report, 'zh')).toBe('未过项: a.py、b.py');
  });

  it('returns null for absent, malformed, or non-acceptance metadata', () => {
    expect(getAcceptanceSummaryDetail(null, 'en')).toBeNull();
    expect(getAcceptanceSummaryDetail('not-json', 'en')).toBeNull();
    expect(getAcceptanceSummaryDetail('{"status":"completed"}', 'en')).toBeNull();
  });
});

function acceptanceMilestone(overrides: Partial<WorkflowMilestone> = {}): WorkflowMilestone {
  return {
    workflow_id: 'wf-1',
    milestone_id: 'ms-acc-1',
    phase: 'acceptance_verification',
    dev_round: 1,
    round_number: 1,
    milestone_type: 'acceptance_verification',
    status: 'confirmed',
    title: 'Acceptance verification: confirmed',
    description: '',
    session_id: '',
    review_session_id: '',
    github_issue_number: null,
    github_pr_number: null,
    github_comment_id: '',
    commit_shas: '',
    diff_stats: '',
    result_summary: 'status=confirmed; scope=1 gates=0 verifier=3',
    tldr: '',
    plan_content: '',
    review_content: '',
    error_message: '',
    parent_milestone_id: '',
    fork_branch: '',
    fork_workflow_id: '',
    metadata: acceptanceReport('confirmed', {
      scope: [{ item: 'src/app.py', verdict: 'confirmed' }],
      verifier: [
        { item: 'a', verdict: 'confirmed' },
        { item: 'b', verdict: 'confirmed' },
      ],
    }),
    started_at: '2026-08-21T15:00:00Z',
    completed_at: '2026-08-21T15:05:00Z',
    created_at: '2026-08-21T15:00:00Z',
    updated_at: '2026-08-21T15:05:00Z',
    ...overrides,
  };
}

function renderWithMilestones(workflow: AutonomousWorkflow, milestones: WorkflowMilestone[]) {
  vi.mocked(useWorkflowTimeline).mockReturnValueOnce({
    data: { milestones },
    isLoading: false,
  } as never);
  return renderTimeline(workflow);
}

describe('acceptance milestone card (#2985)', () => {
  it('shows the verdict icon/label and the structured summary instead of the raw backend string', () => {
    const { container } = renderWithMilestones(
      pausedWorkflow({ status: 'completed', verification_status: 'confirmed' }),
      [acceptanceMilestone()]
    );

    // Verdict mapping: confirmed gets the green check + Accepted label — not
    // the pending hollow circle ("等待中") every verdict used to fall into.
    expect(screen.getByText('Accepted')).toBeInTheDocument();
    const statusBadge = container.querySelector('.timeline-milestone-status');
    expect(statusBadge).toBeTruthy();
    expect(statusBadge?.querySelector('.bi-check-circle-fill')).toBeTruthy();
    expect(statusBadge?.querySelector('.bi-circle')).toBeFalsy();
    expect(statusBadge?.className).toContain('timeline-milestone-status--success');

    // Structured, human-readable summary replaces the diagnostic string.
    expect(screen.getByText('Required paths 1/1 · Semantic checks 2/2')).toBeInTheDocument();
    expect(screen.queryByText(/status=confirmed; scope=1/)).not.toBeInTheDocument();
  });

  it('maps rejected verdicts to the danger icon and label', () => {
    const { container } = renderWithMilestones(
      pausedWorkflow({ status: 'paused', verification_status: 'rejected' }),
      [
        acceptanceMilestone({
          milestone_id: 'ms-acc-2',
          status: 'rejected',
          title: 'Acceptance verification: rejected',
          result_summary: 'status=rejected; not-verified: a.py',
          metadata: acceptanceReport('rejected', {
            scope: [{ item: 'a.py', verdict: 'rejected' }],
          }),
        }),
      ]
    );

    expect(screen.getByText('Not accepted')).toBeInTheDocument();
    expect(container.querySelector('.bi-x-circle-fill')).toBeTruthy();
    expect(screen.getByText('Failed: a.py')).toBeInTheDocument();
  });

  it('falls back to the persisted raw summary when metadata is unusable', () => {
    renderWithMilestones(
      pausedWorkflow({ status: 'completed', verification_status: 'confirmed' }),
      [acceptanceMilestone({ metadata: '' })]
    );

    expect(screen.getByText(/status=confirmed; scope=1/)).toBeInTheDocument();
  });
});

describe('acceptance milestone usage wiring (#2994)', () => {
  it('shows token/request chips, the session button, and no system-step chip once the milestone carries the verifier runtime', () => {
    const { container } = renderWithMilestones(
      pausedWorkflow({ status: 'completed', verification_status: 'confirmed' }),
      [
        acceptanceMilestone({
          session_id: 'verif-tracking-session-1',
          llm_total_tokens: 730000,
          llm_request_count: 33,
        }),
      ]
    );

    // Usage chips render from the milestone's phase usage.
    expect(screen.getByText('730.00K')).toBeInTheDocument();
    expect(screen.getByText('33 Requests')).toBeInTheDocument();
    // Session button opens the verifier tracking session.
    expect(screen.getByText(/Session ID.*verif-tr/)).toBeInTheDocument();
    // The verifier is an agent milestone, not a system step.
    expect(screen.queryByText('System step')).not.toBeInTheDocument();
    // #2995: the acceptance-report button carries the warning variant class,
    // pinning the class hookup for the new CSS rule (jsdom cannot assert the
    // stylesheet itself).
    expect(container.querySelector('button.timeline-inline-btn--warning')).toBeTruthy();
  });

  it('keeps the AI-session presentation for a zero-usage verified milestone', () => {
    const { container } = renderWithMilestones(
      pausedWorkflow({ status: 'completed', verification_status: 'confirmed' }),
      [
        acceptanceMilestone({
          session_id: 'verif-tracking-session-2',
          llm_total_tokens: 0,
          llm_request_count: 0,
        }),
      ]
    );

    const badges = container.querySelector('.timeline-milestone-badges');
    expect(badges?.textContent).toContain('0 Requests');
    expect(screen.queryByText('System step')).not.toBeInTheDocument();
  });

  it('no longer labels a session-less acceptance milestone a system step', () => {
    // Legacy rows predating the wiring: no session id, no usage.
    renderWithMilestones(
      pausedWorkflow({ status: 'completed', verification_status: 'confirmed' }),
      [acceptanceMilestone()]
    );

    expect(screen.queryByText('System step')).not.toBeInTheDocument();
  });
});

describe('session viewer message rendering (#3000)', () => {
  afterEach(() => {
    // renderSessionViewer installs a persistent return value (the click that
    // opens the modal triggers a re-render, so a once-value would be consumed
    // by the pre-click render); restore the shared default afterwards.
    vi.mocked(useMilestoneSession).mockReset();
    vi.mocked(useMilestoneSession).mockReturnValue({ data: undefined, isLoading: false } as never);
  });

  function renderSessionViewer(messages: Array<Record<string, unknown>>) {
    vi.mocked(useMilestoneSession).mockReturnValue({
      data: {
        success: true,
        session: {
          status: 'completed',
          messages,
        },
      },
    } as never);
    const utils = renderWithMilestones(
      pausedWorkflow({ status: 'completed', verification_status: 'confirmed' }),
      [
        acceptanceMilestone({
          session_id: 'verif-tracking-session-1',
          llm_total_tokens: 1000,
          llm_request_count: 3,
        }),
      ]
    );
    fireEvent.click(screen.getByText(/Session ID.*verif-tr/));
    return utils;
  }

  it('renders assistant turns as markdown, tool output as monospace, with timestamps', () => {
    renderSessionViewer([
      {
        role: 'assistant',
        content: '**Fixed** the `token` refresh path',
        timestamp: '2026-08-22T12:34:56+00:00',
      },
      { role: 'tool', content: 'git diff HEAD~1', timestamp: '2026-08-22T12:35:10+00:00' },
    ]);

    // Conversational turn → markdown body (strong renders as <strong>).
    const mdBody = document.querySelector('.timeline-session-msg-md');
    expect(mdBody).toBeTruthy();
    expect(mdBody?.querySelector('strong')?.textContent).toBe('Fixed');
    // Tool output stays a monospace <pre>.
    expect(document.querySelector('.timeline-session-msg-pre')?.textContent).toContain(
      'git diff HEAD~1'
    );
    // Timestamps render next to the role badges (expected value computed with
    // the same formatter so the assertion is timezone-stable).
    expect(
      screen.getByText(formatDateTime(new Date('2026-08-22T12:34:56+00:00'), 'MM-dd HH:mm'))
    ).toBeInTheDocument();
    expect(
      screen.getByText(formatDateTime(new Date('2026-08-22T12:35:10+00:00'), 'MM-dd HH:mm'))
    ).toBeInTheDocument();
  });

  it('no longer truncates long messages and offers expand/collapse', () => {
    const tail = 'END-OF-LONG-MESSAGE';
    const longContent = `${'x'.repeat(5000)}${tail}`;
    renderSessionViewer([{ role: 'assistant', content: longContent }]);

    // The 4000-char slice is gone — the tail survives.
    expect(screen.getByText(new RegExp(tail))).toBeInTheDocument();
    // Long messages get the expand toggle.
    const expandBtn = screen.getByText('Expand');
    fireEvent.click(expandBtn);
    expect(screen.getByText('Collapse')).toBeInTheDocument();
  });
});

describe('milestone time format (#2985)', () => {
  it('shows month-day with the time; adds the year only across years', () => {
    // formatMilestoneTime compares the milestone year against the wall
    // clock; pin the clock so the same-year assertion cannot flip into the
    // cross-year branch on 2027-01-01 (repo convention: format.test.ts).
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-08-23T00:00:00Z'));
    try {
      // The anchor time prefers completed_at ?? started_at ?? created_at, so
      // override all three together.
      const { container } = renderWithMilestones(
        pausedWorkflow({ status: 'completed', verification_status: 'confirmed' }),
        [
          acceptanceMilestone({
            milestone_id: 'ms-t1',
            started_at: '2026-02-03T04:05:06Z',
            completed_at: '2026-02-03T04:05:06Z',
            created_at: '2026-02-03T04:05:06Z',
          }),
        ]
      );
      const timeEl = container.querySelector('.timeline-milestone-time span');
      expect(timeEl?.textContent).toMatch(/\d{2}\/\d{2}[,.]? ?\d{2}:\d{2}:\d{2}/);
      expect(timeEl?.textContent).not.toMatch(/2026/);

      const second = renderWithMilestones(
        pausedWorkflow({ status: 'completed', verification_status: 'confirmed' }),
        [
          acceptanceMilestone({
            milestone_id: 'ms-t2',
            started_at: '2024-02-03T04:05:06Z',
            completed_at: '2024-02-03T04:05:06Z',
            created_at: '2024-02-03T04:05:06Z',
          }),
        ]
      );
      const staleEl = second.container.querySelector('.timeline-milestone-time span');
      expect(staleEl?.textContent).toMatch(/2024/);
    } finally {
      vi.useRealTimers();
    }
  });
});

describe('MILESTONE_DISPLAY icon coverage', () => {
  // Every milestone_type the backend can emit (orchestrator + phases/* +
  // git_workspace literals) or that exists on historical rows. Unmapped types
  // fall back to a gray bi-circle, which is how the pre-#3036 gap was spotted.
  const ALL_TYPES = [
    'repo_setup',
    'issue_created',
    'issue_linked',
    'branch_created',
    'branch_mismatch',
    'plan_created',
    'plan_reviewed',
    'plan_refined',
    'plan_finalized',
    'dev_started',
    'dev_completed',
    'tests_run',
    'pr_created',
    'pr_reviewed',
    'pr_updated',
    'pr_review_summary',
    'pr_head_unverified',
    'pr_zero_check_runs',
    'conflicts_resolved',
    'conflicts_pushed',
    'worktree_restored',
    'progress_reported',
    'requirement_received',
    'round_completed',
    'merged',
    'cleaned_up',
    'cleanup_pending',
    'wait_started',
    'no_changes',
    'timing_issue',
    'terminal_report_posted',
    'workflow_forked',
    'ci_repair_started',
    'ci_repair_applied',
    'ci_repair_exhausted',
    'ci_repair_no_change_exhausted',
    'ci_repair_transient_exhausted',
    'ci_repair_escalated_to_development',
    'ci_repair_environment_mismatch',
    'ci_diagnostics_pending',
    'ci_failed_before_report',
    'acceptance_verification',
    'acceptance_rejected_cap_exhausted',
    'acceptance_rejected_reopened',
    'recovery_evidence_missing',
    'frontend_node_modules_shim_failed',
  ] as const;

  it('maps every backend milestone type to a dedicated icon (no bi-circle fallback)', () => {
    for (const type of ALL_TYPES) {
      expect(MILESTONE_DISPLAY[type], `unmapped type: ${type}`).toBeDefined();
    }
  });

  it('uses unique icon+color pairs and known color keys', () => {
    const seen = new Set<string>();
    for (const [type, display] of Object.entries(MILESTONE_DISPLAY)) {
      expect(MILESTONE_ICON_COLORS[display.color], `unknown color for ${type}`).toBeDefined();
      const key = `${display.icon}/${display.color}`;
      expect(seen.has(key), `duplicate icon+color pair: ${key}`).toBe(false);
      seen.add(key);
    }
  });

  it('references only icon classes that exist in bootstrap-icons', () => {
    const css = readFileSync(
      join(process.cwd(), 'node_modules/bootstrap-icons/font/bootstrap-icons.css'),
      'utf8'
    );
    for (const [type, display] of Object.entries(MILESTONE_DISPLAY)) {
      expect(css, `missing bootstrap icon for ${type}: ${display.icon}`).toContain(
        `.${display.icon}::before`
      );
    }
  });
});
