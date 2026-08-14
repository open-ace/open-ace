/**
 * WorkflowTimeline tests — paused-state banner variants (#2634)
 *
 * Covers the acceptance-awaiting / quota / manual pause distinction and the
 * resume-with-feedback entry point for rejected acceptance workflows.
 */

import { fireEvent, render, screen, within } from '@/test/utils';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { WorkflowTimeline, formatAcceptanceReport } from './WorkflowTimeline';
import type { AutonomousWorkflow } from '@/api/autonomous';

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

describe('formatAcceptanceReport (#2658)', () => {
  it('renders per-item verdicts, rationale and evidence refs from metadata JSON', () => {
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
    const out = formatAcceptanceReport(JSON.stringify(report));
    expect(out).toContain('❌ rejected');
    expect(out).toContain('Merge SHA: abc123');
    expect(out).toContain('── Scope ──');
    expect(out).toContain('✅ src/app.py');
    expect(out).toContain('❌ 修复登录失败 — 合并后用例依旧失败');
    expect(out).toContain('↳ tests/test_login.py:12 (用例仍然失败)');
    // Empty sections are skipped (gates omitted above).
    expect(out).not.toContain('── Gates ──');
  });

  it('falls back to raw metadata when the JSON is malformed', () => {
    expect(formatAcceptanceReport('not-json{')).toBe('not-json{');
  });

  it('omits optional fields gracefully', () => {
    const out = formatAcceptanceReport(JSON.stringify({ status: 'indeterminate' }));
    expect(out).toContain('⚠️ indeterminate');
    expect(out).not.toContain('Merge SHA');
    expect(out).not.toContain('Infra error');
  });
});
