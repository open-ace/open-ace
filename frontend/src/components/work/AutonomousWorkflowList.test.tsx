import { fireEvent, render, screen, act } from '@/test/utils';
import { vi, describe, it, expect, beforeEach, afterEach } from 'vitest';
import { ACTIVE_WORKFLOW_STATUSES, AutonomousWorkflowList } from './AutonomousWorkflowList';
import { useWorkflows } from '@/hooks/useAutonomous';
import type { AutonomousWorkflow } from '@/api/autonomous';

const { mockDeleteWorkflowMutate, mockDeleteBatchMutate } = vi.hoisted(() => ({
  mockDeleteWorkflowMutate: vi.fn(),
  mockDeleteBatchMutate: vi.fn(),
}));

vi.mock('@/hooks/useAutonomous', () => ({
  useWorkflows: vi.fn(),
  useDeleteWorkflow: () => ({
    mutate: mockDeleteWorkflowMutate,
    isPending: false,
  }),
  useDeleteBatch: () => ({
    mutate: mockDeleteBatchMutate,
    isPending: false,
  }),
}));

const mockUseWorkflows = vi.mocked(useWorkflows);

function lastWorkflowFilters() {
  return mockUseWorkflows.mock.calls[mockUseWorkflows.mock.calls.length - 1]?.[0];
}

function workflow(overrides: Partial<AutonomousWorkflow> = {}): AutonomousWorkflow {
  return {
    workflow_id: 'wf-1',
    title: 'Queued batch task',
    status: 'queued',
    requirements_text: '',
    requirements_issue_url: '',
    project_path: '/tmp/project',
    project_repo_url: '',
    is_new_project: false,
    cli_tool: 'claude-code',
    model: '',
    permission_mode: 'auto-edit',
    branch_name: '',
    branch_strategy: 'new-branch',
    workspace_type: 'local',
    remote_machine_id: '',
    worktree_path: '',
    github_issue_number: null,
    github_pr_number: null,
    github_pr_url: '',
    batch_id: null,
    batch_order: null,
    batch_total: null,
    auto_merge: true,
    definition_snapshot: null,
    current_phase: 'preparation',
    current_round: 0,
    dev_round: 1,
    max_plan_rounds: 3,
    max_pr_review_rounds: 5,
    require_full_review_rounds: false,
    total_tokens: 0,
    total_input_tokens: 0,
    total_output_tokens: 0,
    total_requests: 0,
    error_message: '',
    parent_workflow_id: null,
    fork_milestone_id: null,
    user_feedback: '',
    original_branch_name: '',
    created_at: '2026-06-11T00:00:00Z',
    updated_at: '2026-06-11T00:00:00Z',
    completed_at: null,
    paused_at: null,
    ...overrides,
  };
}

function mockWorkflowList(workflows: AutonomousWorkflow[], total = workflows.length) {
  mockUseWorkflows.mockReturnValue({
    data: {
      success: true,
      workflows,
      total,
      limit: 50,
      offset: 0,
    },
    isLoading: false,
  } as ReturnType<typeof useWorkflows>);
}

describe('AutonomousWorkflowList', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    mockUseWorkflows.mockReset();
    mockDeleteWorkflowMutate.mockReset();
    mockDeleteBatchMutate.mockReset();
  });

  afterEach(() => {
    vi.runOnlyPendingTimers();
    vi.useRealTimers();
  });

  it('adds a queued tab and keeps queued out of the active filter', () => {
    mockWorkflowList([workflow()]);

    render(
      <AutonomousWorkflowList selectedId={null} onSelect={vi.fn()} onClearSelection={vi.fn()} />
    );

    expect(screen.getByRole('button', { name: 'Queued' })).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Active' }));

    const lastFilters = lastWorkflowFilters();
    expect(lastFilters?.status).toContain('pending');
    expect(lastFilters?.status).toContain('verification_pending');
    expect(lastFilters?.status).not.toContain('queued');
  });

  it('renders verification_pending as an active acceptance state', () => {
    mockWorkflowList([
      workflow({ status: 'verification_pending', current_phase: 'acceptance_verification' }),
    ]);

    render(
      <AutonomousWorkflowList selectedId={null} onSelect={vi.fn()} onClearSelection={vi.fn()} />
    );

    expect(ACTIVE_WORKFLOW_STATUSES).toContain('verification_pending');
    expect(screen.getByText('Verifying acceptance')).toBeInTheDocument();
  });

  it('sends debounced search and resets to the first page', () => {
    mockWorkflowList([workflow()]);

    render(
      <AutonomousWorkflowList selectedId={null} onSelect={vi.fn()} onClearSelection={vi.fn()} />
    );

    fireEvent.change(screen.getByPlaceholderText('Search workflows...'), {
      target: { value: 'issue 123' },
    });

    act(() => {
      vi.advanceTimersByTime(300);
    });

    const lastFilters = lastWorkflowFilters();
    expect(lastFilters?.search).toBe('issue 123');
    expect(lastFilters?.offset).toBe('0');
  });

  it('uses semantic workflow buttons and exposes accessible controls', () => {
    const onSelect = vi.fn();
    mockWorkflowList([
      workflow({
        title: 'Keyboard workflow',
        status: 'completed',
      }),
    ]);

    const { container } = render(
      <AutonomousWorkflowList selectedId={null} onSelect={onSelect} onClearSelection={vi.fn()} />
    );

    const workflowButton = screen.getByRole('button', { name: 'Keyboard workflow' });
    expect(workflowButton.tagName).toBe('BUTTON');
    expect(workflowButton.querySelector('div')).toBeNull();
    onSelect.mockClear();
    fireEvent.click(workflowButton);

    expect(onSelect).toHaveBeenCalledWith(
      expect.objectContaining({ workflow_id: 'wf-1', title: 'Keyboard workflow' })
    );
    expect(screen.getByRole('textbox')).toHaveAttribute('aria-label');
    expect(container.querySelector('.auto-workflow-delete-btn')).toHaveAttribute('aria-label');
  });

  it('requests the next page with a 50 item offset', () => {
    mockWorkflowList([workflow()], 75);

    render(
      <AutonomousWorkflowList selectedId="wf-1" onSelect={vi.fn()} onClearSelection={vi.fn()} />
    );

    fireEvent.click(screen.getAllByRole('button', { name: /next page/i })[0]);

    const lastFilters = lastWorkflowFilters();
    expect(lastFilters?.limit).toBe('50');
    expect(lastFilters?.offset).toBe('50');
  });

  it('resets to the last available page before reconciling selection', () => {
    const onSelect = vi.fn();
    const onClearSelection = vi.fn();
    let shrinkResults = false;

    mockUseWorkflows.mockImplementation((filters) => {
      const offset = filters?.offset ?? '0';
      if (offset === '50') {
        if (shrinkResults) {
          return {
            data: {
              success: true,
              workflows: [],
              total: 1,
              limit: 50,
              offset: 50,
            },
            isLoading: false,
            isError: false,
            error: null,
            isPending: false,
            isFetching: false,
            refetch: vi.fn(),
          } as unknown as ReturnType<typeof useWorkflows>;
        }

        return {
          data: {
            success: true,
            workflows: [workflow({ workflow_id: 'wf-51', title: 'Second page workflow' })],
            total: 75,
            limit: 50,
            offset: 50,
          },
          isLoading: false,
        } as ReturnType<typeof useWorkflows>;
      }

      return {
        data: {
          success: true,
          workflows: [workflow()],
          total: shrinkResults ? 1 : 75,
          limit: 50,
          offset: 0,
        },
        isLoading: false,
      } as ReturnType<typeof useWorkflows>;
    });

    const { rerender } = render(
      <AutonomousWorkflowList
        selectedId="wf-51"
        onSelect={onSelect}
        onClearSelection={onClearSelection}
        preserveInitialSelection
      />
    );

    fireEvent.click(screen.getAllByRole('button', { name: /next page/i })[0]);
    shrinkResults = true;

    rerender(
      <AutonomousWorkflowList
        selectedId="wf-51"
        onSelect={onSelect}
        onClearSelection={onClearSelection}
        preserveInitialSelection
      />
    );

    expect(onClearSelection).not.toHaveBeenCalled();
    expect(onSelect).toHaveBeenCalledWith(expect.objectContaining({ workflow_id: 'wf-1' }));
  });

  it('keeps the current selection when filters hide it from the list', () => {
    const onSelect = vi.fn();
    const onClearSelection = vi.fn();

    mockUseWorkflows.mockImplementation((filters) => {
      if (filters?.search === 'other') {
        return {
          data: {
            success: true,
            workflows: [workflow({ workflow_id: 'wf-2', title: 'Other workflow' })],
            total: 1,
            limit: 50,
            offset: 0,
          },
          isLoading: false,
        } as ReturnType<typeof useWorkflows>;
      }

      return {
        data: {
          success: true,
          workflows: [workflow({ workflow_id: 'wf-1', title: 'Selected workflow' })],
          total: 1,
          limit: 50,
          offset: 0,
        },
        isLoading: false,
      } as ReturnType<typeof useWorkflows>;
    });

    render(
      <AutonomousWorkflowList
        selectedId="wf-1"
        onSelect={onSelect}
        onClearSelection={onClearSelection}
      />
    );

    onSelect.mockClear();
    fireEvent.change(screen.getByPlaceholderText('Search workflows...'), {
      target: { value: 'other' },
    });

    act(() => {
      vi.advanceTimersByTime(300);
    });

    expect(onSelect).not.toHaveBeenCalled();
    expect(onClearSelection).not.toHaveBeenCalled();
  });

  it('collapses batch workflows and expands them in ascending batch order', () => {
    mockWorkflowList([
      workflow({
        workflow_id: 'wf-3',
        title: 'Task 3',
        batch_id: 'batch-33',
        batch_order: 3,
        batch_total: 3,
      }),
      workflow({
        workflow_id: 'wf-1',
        title: 'Task 1',
        batch_id: 'batch-33',
        batch_order: 1,
        batch_total: 3,
      }),
      workflow({
        workflow_id: 'wf-2',
        title: 'Task 2',
        batch_id: 'batch-33',
        batch_order: 2,
        batch_total: 3,
      }),
    ]);

    render(
      <AutonomousWorkflowList selectedId={null} onSelect={vi.fn()} onClearSelection={vi.fn()} />
    );

    expect(screen.queryByText('1/3')).not.toBeInTheDocument();
    expect(screen.queryByText('2/3')).not.toBeInTheDocument();
    expect(screen.queryByText('3/3')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Expand Batch' }));

    const first = screen.getByText('1/3');
    const second = screen.getByText('2/3');
    const third = screen.getByText('3/3');

    expect(first.compareDocumentPosition(second) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(second.compareDocumentPosition(third) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('deletes an entire batch from the batch header action', () => {
    mockWorkflowList([
      workflow({
        workflow_id: 'wf-1',
        title: 'Task 1',
        status: 'completed',
        batch_id: 'batch-33',
        batch_order: 1,
        batch_total: 2,
      }),
      workflow({
        workflow_id: 'wf-2',
        title: 'Task 2',
        status: 'completed',
        batch_id: 'batch-33',
        batch_order: 2,
        batch_total: 2,
      }),
    ]);

    render(
      <AutonomousWorkflowList selectedId={null} onSelect={vi.fn()} onClearSelection={vi.fn()} />
    );

    // Let component state updates complete (debounce, useEffect, etc.)
    act(() => {
      vi.runAllTimers();
    });

    const deleteButton = screen.getByTitle('Delete');
    fireEvent.click(deleteButton);

    // Let confirmation state update complete
    act(() => {
      vi.runAllTimers();
    });

    fireEvent.click(screen.getByText('Delete this workflow?'));

    expect(mockDeleteBatchMutate).toHaveBeenCalledWith('batch-33');
    expect(mockDeleteWorkflowMutate).not.toHaveBeenCalled();
  });

  it('shows a partial count pill when a batch is split across pages', () => {
    // Only one member of a 3-item batch lands on the current page (e.g. after a
    // status filter or pagination). The pill must reflect the visible subset and
    // flag it as partial, instead of showing the true total of 3.
    mockWorkflowList([
      workflow({
        workflow_id: 'wf-1',
        title: 'Task 1',
        batch_id: 'batch-split',
        batch_order: 1,
        batch_total: 3,
        status: 'completed',
      }),
    ]);

    const { container } = render(
      <AutonomousWorkflowList selectedId={null} onSelect={vi.fn()} onClearSelection={vi.fn()} />
    );

    const pill = container.querySelector('.auto-workflow-batch-count');
    expect(pill?.textContent).toContain('1 / 3');
    // status badge sums to the visible subset (1), matching the leading count
    expect(screen.getByText(/Completed\s+1/i)).toBeInTheDocument();
  });

  it('shows the plain count pill when the whole batch fits on the page', () => {
    mockWorkflowList([
      workflow({
        workflow_id: 'wf-1',
        title: 'Task 1',
        batch_id: 'batch-full',
        batch_order: 1,
        batch_total: 2,
        status: 'completed',
      }),
      workflow({
        workflow_id: 'wf-2',
        title: 'Task 2',
        batch_id: 'batch-full',
        batch_order: 2,
        batch_total: 2,
        status: 'failed',
      }),
    ]);

    const { container } = render(
      <AutonomousWorkflowList selectedId={null} onSelect={vi.fn()} onClearSelection={vi.fn()} />
    );

    const pill = container.querySelector('.auto-workflow-batch-count');
    expect(pill?.textContent).toContain('2');
    expect(pill?.textContent).not.toContain('/');
    // badges (Completed 1 + Failed 1) sum to the visible count
    expect(screen.getByText(/Completed\s+1/i)).toBeInTheDocument();
    expect(screen.getByText(/Failed\s+1/i)).toBeInTheDocument();
  });

  // ── Paused workflows (#2634) ───────────────────────────────────────

  it('adds a paused tab that filters exactly by paused status', () => {
    mockWorkflowList([
      workflow({ workflow_id: 'wf-paused', status: 'paused' }),
      workflow({ workflow_id: 'wf-active', status: 'developing' }),
    ]);

    render(
      <AutonomousWorkflowList selectedId={null} onSelect={vi.fn()} onClearSelection={vi.fn()} />
    );

    const pausedTab = screen.getByRole('button', { name: 'Paused' });
    fireEvent.click(pausedTab);

    const lastFilters = lastWorkflowFilters();
    // Exact match — a substring assertion would pass trivially. planning_timeout
    // is the paused-variant state and belongs here, not in the active tab.
    expect(lastFilters?.status).toBe('paused,planning_timeout');
  });

  it('keeps paused workflows out of the active tab (#2985)', () => {
    // The active tab used to include paused(+planning_timeout) in its comma
    // list, so it rendered the exact same rows as the paused tab whenever
    // only paused workflows remained.
    mockWorkflowList([
      workflow({ workflow_id: 'wf-paused', status: 'paused' }),
      workflow({ workflow_id: 'wf-active', status: 'developing' }),
    ]);

    render(
      <AutonomousWorkflowList selectedId={null} onSelect={vi.fn()} onClearSelection={vi.fn()} />
    );

    fireEvent.click(screen.getByRole('button', { name: 'Active' }));

    const lastFilters = lastWorkflowFilters();
    expect(lastFilters?.status).toBe(
      'pending,preparing,planning,developing,pr_review,reporting,waiting,merging,verification_pending'
    );
  });

  it('labels pause reasons for acceptance, quota, and manual pauses', () => {
    mockWorkflowList([
      workflow({
        workflow_id: 'wf-accept',
        status: 'paused',
        current_phase: 'acceptance_verification',
        error_message: 'Acceptance verification rejected; awaiting review',
        paused_at: '2026-06-10T00:00:00Z',
      }),
      workflow({
        workflow_id: 'wf-quota',
        status: 'paused',
        current_phase: 'developing',
        error_message: 'Quota exceeded: daily usage at 100% (1000/1000)',
        paused_at: '2026-06-10T00:00:00Z',
      }),
      workflow({
        workflow_id: 'wf-manual',
        status: 'paused',
        current_phase: 'developing',
        error_message: '',
        paused_at: '2026-06-10T00:00:00Z',
      }),
    ]);

    const { container } = render(
      <AutonomousWorkflowList selectedId={null} onSelect={vi.fn()} onClearSelection={vi.fn()} />
    );

    expect(screen.getByText('Awaiting acceptance review')).toBeInTheDocument();
    expect(screen.getByText('Quota paused')).toBeInTheDocument();
    expect(screen.getByText('Paused manually')).toBeInTheDocument();

    // Visual distinction: acceptance=danger, quota=warning, manual=secondary
    const badgeFor = (text: string) => screen.getByText(text).closest('.badge') as HTMLElement;
    expect(badgeFor('Awaiting acceptance review').className).toContain('danger');
    expect(badgeFor('Quota paused').className).toContain('warning');
    expect(badgeFor('Paused manually').className).toContain('secondary');
    expect(container).toBeTruthy();
  });

  it('does not show pause reason badges for non-paused workflows', () => {
    mockWorkflowList([
      workflow({
        workflow_id: 'wf-active',
        status: 'developing',
        current_phase: 'acceptance_verification',
      }),
    ]);

    render(
      <AutonomousWorkflowList selectedId={null} onSelect={vi.fn()} onClearSelection={vi.fn()} />
    );

    expect(screen.queryByText('Awaiting acceptance review')).not.toBeInTheDocument();
    expect(screen.queryByText('Quota paused')).not.toBeInTheDocument();
  });

  it('flags acceptance-awaiting workflows paused more than 3 days ago', () => {
    // Fake timers freeze Date.now(), so compute the fixture relative to now.
    const now = Date.now();
    const iso = (daysAgo: number) => new Date(now - daysAgo * 24 * 60 * 60 * 1000).toISOString();

    mockWorkflowList([
      workflow({
        workflow_id: 'wf-old',
        status: 'paused',
        current_phase: 'acceptance_verification',
        error_message: 'Acceptance verification rejected; awaiting review',
        paused_at: iso(5),
      }),
      workflow({
        workflow_id: 'wf-fresh',
        status: 'paused',
        current_phase: 'acceptance_verification',
        error_message: 'Acceptance verification rejected; awaiting review',
        paused_at: iso(1),
      }),
      workflow({
        workflow_id: 'wf-old-quota',
        status: 'paused',
        current_phase: 'developing',
        error_message: 'Quota exceeded: daily usage at 100% (1000/1000)',
        paused_at: iso(5),
      }),
    ]);

    render(
      <AutonomousWorkflowList selectedId="wf-old" onSelect={vi.fn()} onClearSelection={vi.fn()} />
    );

    expect(screen.getAllByText('Unreviewed >3 days')).toHaveLength(1);
    // The fresh (<3d) acceptance pause and the old quota pause stay unflagged.
  });
});
