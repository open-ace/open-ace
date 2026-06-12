import { fireEvent, render, screen, act } from '@/test/utils';
import { vi, describe, it, expect, beforeEach, afterEach } from 'vitest';
import { AutonomousWorkflowList } from './AutonomousWorkflowList';
import { useWorkflows } from '@/hooks/useAutonomous';
import type { AutonomousWorkflow } from '@/api/autonomous';

vi.mock('@/hooks/useAutonomous', () => ({
  useWorkflows: vi.fn(),
  useDeleteWorkflow: () => ({
    mutate: vi.fn(),
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
    batch_id: 'batch-1',
    batch_order: 2,
    batch_total: 3,
    auto_merge: true,
    definition_snapshot: null,
    current_phase: 'preparation',
    current_round: 0,
    dev_round: 1,
    max_plan_rounds: 3,
    max_pr_review_rounds: 5,
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
    expect(lastFilters?.status).not.toContain('queued');
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
});
