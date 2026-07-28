import { render, screen } from '@/test/utils';
import { describe, it, expect } from 'vitest';
import { RuntimeIsolationPanel, parseEffectivePolicy } from './RuntimeIsolationPanel';
import type { AutonomousWorkflow } from '@/api/autonomous';

function workflow(policy: string | null | undefined): AutonomousWorkflow {
  return {
    workflow_id: 'wf-1',
    title: 't',
    status: 'completed',
    requirements_text: '',
    requirements_issue_url: '',
    project_path: '',
    project_repo_url: '',
    is_new_project: false,
    cli_tool: 'claude-code',
    model: '',
    permission_mode: 'auto-edit',
    branch_name: '',
    branch_strategy: '',
    workspace_type: 'local',
    remote_machine_id: '',
    worktree_path: '',
    github_issue_number: null,
    github_pr_number: null,
    github_pr_url: '',
    batch_id: null,
    batch_order: null,
    batch_total: null,
    auto_merge: false,
    definition_snapshot: null,
    current_phase: '',
    current_round: 0,
    dev_round: 0,
    max_plan_rounds: 0,
    max_pr_review_rounds: 0,
    error_message: '',
    content_language: 'en',
    parent_workflow_id: null,
    fork_milestone_id: null,
    user_feedback: '',
    original_branch_name: '',
    created_at: null,
    updated_at: null,
    completed_at: null,
    paused_at: null,
    sandbox_effective_policy: policy ?? undefined,
  } as AutonomousWorkflow;
}

const LEGACY_SNAPSHOT = JSON.stringify({
  schema_version: 1,
  provider: 'legacy_posix',
  capabilities: ['private_home_tmp_xdg', 'filesystem_acl', 'cpu_mem_pids_time_quota'],
  limits: {
    memory_max_bytes: 2147483648,
    pids_max: 512,
    cpu_max: '200000/100000',
    wall_clock_limit: 3600,
    ephemeral_storage_limit: 0,
    inode_limit: 0,
  },
  cgroup_enabled: 'auto',
  task_root: '/run/openace-agent-tasks',
  enforced: {
    memory: true,
    pids: true,
    cpu: true,
    wall_clock: true,
    ephemeral_storage: false,
    inode: false,
  },
});

describe('parseEffectivePolicy', () => {
  it('returns null for absent / malformed input', () => {
    expect(parseEffectivePolicy(null)).toBeNull();
    expect(parseEffectivePolicy(undefined)).toBeNull();
    expect(parseEffectivePolicy('')).toBeNull();
    expect(parseEffectivePolicy('not json')).toBeNull();
  });

  it('parses a valid snapshot', () => {
    const snap = parseEffectivePolicy(LEGACY_SNAPSHOT);
    expect(snap?.provider).toBe('legacy_posix');
    expect(snap?.limits?.memory_max_bytes).toBe(2147483648);
  });
});

describe('RuntimeIsolationPanel', () => {
  it('renders nothing when no snapshot is present', () => {
    const { container } = render(<RuntimeIsolationPanel workflow={workflow(null)} />);
    expect(container.querySelector('[data-testid="runtime-isolation-panel"]')).toBeNull();
  });

  it('renders nothing when the snapshot is malformed', () => {
    const { container } = render(<RuntimeIsolationPanel workflow={workflow('{broken')} />);
    expect(container.querySelector('[data-testid="runtime-isolation-panel"]')).toBeNull();
  });

  it('renders provider, capabilities, and the limits table for a legacy snapshot', () => {
    render(<RuntimeIsolationPanel workflow={workflow(LEGACY_SNAPSHOT)} />);
    // Provider badge
    expect(screen.getByText('legacy_posix')).toBeInTheDocument();
    // Capability chip
    expect(screen.getByText('cpu_mem_pids_time_quota')).toBeInTheDocument();
    // Limits table: formatted memory (2 GiB) and wall-clock (1h)
    expect(screen.getByText('2 GiB')).toBeInTheDocument();
    expect(screen.getByText('1h')).toBeInTheDocument();
  });

  it('shows enforced/not-enforced badges honestly from declared capabilities', () => {
    render(<RuntimeIsolationPanel workflow={workflow(LEGACY_SNAPSHOT)} />);
    // Legacy declares CPU_MEM_PIDS_TIME_QUOTA → memory enforced
    const enforcedBadges = screen.getAllByText('enforced');
    expect(enforcedBadges.length).toBeGreaterThanOrEqual(4); // memory/pids/cpu/wall_clock
    // Legacy does NOT declare STORAGE_INODE_QUOTA → storage/inode not enforced
    const notEnforcedBadges = screen.getAllByText('not enforced');
    expect(notEnforcedBadges.length).toBe(2);
  });

  it('reports nothing enforced for a backend that declares no capabilities', () => {
    const remoteSnap = JSON.stringify({
      provider: 'remote_machine',
      capabilities: [],
      limits: {},
      enforced: { memory: false, pids: false, cpu: false, wall_clock: false },
    });
    render(<RuntimeIsolationPanel workflow={workflow(remoteSnap)} />);
    expect(screen.getByText('remote_machine')).toBeInTheDocument();
    // No capability chips, and the "no capabilities" notice shows
    expect(screen.getByText(/No isolation capabilities declared by this backend/)).toBeInTheDocument();
  });
});
