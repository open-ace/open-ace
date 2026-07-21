import { describe, expect, it } from 'vitest';

import {
  findForkMilestoneIndex,
  getActivityHostMilestoneId,
  isAiMilestoneType,
  isDisplayableTimelineActivity,
  parseDiffFiles,
  parseDiffStats,
} from './WorkflowTimeline.utils';

describe('WorkflowTimeline.utils', () => {
  it('filters empty assistant placeholders without hiding real activity events', () => {
    expect(isDisplayableTimelineActivity({ type: 'assistant' })).toBe(false);
    expect(isDisplayableTimelineActivity({ type: 'assistant', text: '   ' })).toBe(false);
    expect(isDisplayableTimelineActivity({ type: 'assistant', text: '-' })).toBe(false);
    expect(isDisplayableTimelineActivity({ type: 'assistant', text: 'Reviewing files' })).toBe(
      true
    );
    expect(isDisplayableTimelineActivity({ type: 'tool_use' })).toBe(true);
    expect(isDisplayableTimelineActivity({ type: 'system' })).toBe(true);
    expect(isDisplayableTimelineActivity({ type: 'usage' })).toBe(true);
  });

  it('classifies AI-backed and system-only milestone cards', () => {
    for (const type of [
      'plan_created',
      'plan_refined',
      'plan_reviewed',
      'plan_finalized',
      'dev_started',
      'tests_run',
      'pr_reviewed',
      'pr_review_summary',
      'pr_updated',
      'ci_repair_applied',
      'conflicts_resolved',
    ]) {
      expect(isAiMilestoneType(type), type).toBe(true);
    }
    for (const type of [
      'issue_linked',
      'branch_created',
      'pr_created',
      'dev_completed',
      'progress_reported',
      'round_completed',
      'merged',
      'cleaned_up',
    ]) {
      expect(isAiMilestoneType(type), type).toBe(false);
    }
  });

  it('keeps an activity host through the scheduler gap between milestones', () => {
    const milestones = [
      {
        milestone_id: 'old-round',
        milestone_type: 'plan_created',
        status: 'completed',
        dev_round: 1,
      },
      {
        milestone_id: 'current',
        milestone_type: 'dev_started',
        status: 'in_progress',
        dev_round: 2,
      },
    ];
    expect(getActivityHostMilestoneId(milestones, 2, 'developing')).toBe('current');

    const betweenMilestones = milestones.map((milestone) => ({
      ...milestone,
      status: 'completed',
    }));
    expect(getActivityHostMilestoneId(betweenMilestones, 2, 'developing')).toBe('current');
    expect(getActivityHostMilestoneId(betweenMilestones, 2, 'completed')).toBeNull();
  });

  it('never mounts AI activity on system-only cards or idle workflow phases', () => {
    const systemOnly = [
      {
        milestone_id: 'branch',
        milestone_type: 'branch_created',
        status: 'completed',
        dev_round: 1,
      },
      {
        milestone_id: 'wait',
        milestone_type: 'wait_started',
        status: 'in_progress',
        dev_round: 1,
      },
    ];
    expect(getActivityHostMilestoneId(systemOnly, 1, 'preparing')).toBeNull();
    expect(getActivityHostMilestoneId(systemOnly, 1, 'waiting')).toBeNull();
    expect(getActivityHostMilestoneId(systemOnly, 1, 'merging')).toBeNull();

    const mergeRepair = [
      ...systemOnly,
      {
        milestone_id: 'repair',
        milestone_type: 'ci_repair_applied',
        status: 'in_progress',
        dev_round: 1,
      },
    ];
    expect(getActivityHostMilestoneId(mergeRepair, 1, 'merging')).toBe('repair');
  });

  it('prefers the newest active AI milestone when a fork copied an older active card', () => {
    const forkMilestones = [
      {
        milestone_id: 'copied-parent-plan',
        milestone_type: 'plan_created',
        status: 'in_progress',
        dev_round: 1,
      },
      {
        milestone_id: 'fork-marker',
        milestone_type: 'workflow_forked',
        status: 'completed',
        dev_round: 1,
      },
      {
        milestone_id: 'child-development',
        milestone_type: 'dev_started',
        status: 'in_progress',
        dev_round: 1,
      },
    ];

    expect(getActivityHostMilestoneId(forkMilestones, 1, 'developing')).toBe('child-development');
  });

  it('ignores stale in-progress AI milestones outside an agent-running phase', () => {
    const staleForkMilestones = [
      {
        milestone_id: 'copied-parent-plan',
        milestone_type: 'plan_created',
        status: 'in_progress',
        dev_round: 1,
      },
    ];

    for (const status of [
      'queued',
      'pending',
      'preparing',
      'reporting',
      'waiting',
      'paused',
      'planning_timeout',
      'failed',
      'cancelled',
      'completed',
    ]) {
      expect(getActivityHostMilestoneId(staleForkMilestones, 1, status), status).toBeNull();
    }
  });

  it('parses diff stats json', () => {
    expect(parseDiffStats('{"additions":100,"deletions":25,"files":3,"commits":2}')).toEqual({
      additions: 100,
      deletions: 25,
      files: 3,
      commits: 2,
    });
    expect(parseDiffStats('')).toBeNull();
    expect(parseDiffStats('{bad json')).toBeNull();
  });

  it('parses per-file diff details across commits and statuses', () => {
    const diff = [
      '--- Commit: abc12345 ---',
      'diff --git a/src/new.ts b/src/new.ts',
      'new file mode 100644',
      '+++ b/src/new.ts',
      '+const answer = 42;',
      'diff --git a/src/old.ts b/src/old.ts',
      'deleted file mode 100644',
      '--- a/src/old.ts',
      '-legacy();',
      '--- Commit: def67890 ---',
      'diff --git a/src/rename.ts b/src/rename-next.ts',
      'rename from src/rename.ts',
      'rename to src/rename-next.ts',
      '@@ -1 +1 @@',
      '-before();',
      '+after();',
    ].join('\n');

    const files = parseDiffFiles(diff);

    expect(files).toHaveLength(3);
    expect(files[0]).toMatchObject({
      commitLabel: 'abc12345',
      path: 'src/new.ts',
      status: 'added',
      additions: 1,
      deletions: 0,
    });
    expect(files[1]).toMatchObject({
      commitLabel: 'abc12345',
      path: 'src/old.ts',
      status: 'deleted',
      additions: 0,
      deletions: 1,
    });
    expect(files[2]).toMatchObject({
      commitLabel: 'def67890',
      path: 'src/rename-next.ts',
      status: 'modified',
      additions: 1,
      deletions: 1,
    });
  });

  it('finds the first persisted fork marker for parent timelines', () => {
    const milestones = [
      { milestone_id: 'ms-1', milestone_type: 'dev_started', fork_workflow_id: '' },
      {
        milestone_id: 'ms-2',
        milestone_type: 'workflow_forked',
        fork_workflow_id: 'wf-child-1',
      },
      {
        milestone_id: 'ms-3',
        milestone_type: 'workflow_forked',
        fork_workflow_id: 'wf-child-2',
      },
    ];

    expect(findForkMilestoneIndex(milestones, { preferFirstForkWorkflowId: true })).toBe(1);
  });

  it('falls back from child workflow id to fork milestone id to a single marker', () => {
    const childMatchMilestones = [
      { milestone_id: 'ms-1', milestone_type: 'dev_started', fork_workflow_id: '' },
      {
        milestone_id: 'ms-2',
        milestone_type: 'workflow_forked',
        fork_workflow_id: 'wf-child-1',
      },
    ];
    expect(findForkMilestoneIndex(childMatchMilestones, { childWorkflowId: 'wf-child-1' })).toBe(1);

    const milestoneIdFallbackMilestones = [
      { milestone_id: 'ms-1', milestone_type: 'dev_started', fork_workflow_id: '' },
      { milestone_id: 'ms-fork', milestone_type: 'workflow_forked', fork_workflow_id: '' },
      { milestone_id: 'ms-3', milestone_type: 'tests_run', fork_workflow_id: '' },
    ];
    expect(
      findForkMilestoneIndex(milestoneIdFallbackMilestones, { fallbackMilestoneId: 'ms-fork' })
    ).toBe(1);

    const singleMarkerFallbackMilestones = [
      { milestone_id: 'ms-1', milestone_type: 'dev_started', fork_workflow_id: '' },
      { milestone_id: 'ms-2', milestone_type: 'workflow_forked', fork_workflow_id: '' },
      { milestone_id: 'ms-3', milestone_type: 'tests_run', fork_workflow_id: '' },
    ];
    expect(findForkMilestoneIndex(singleMarkerFallbackMilestones)).toBe(1);
  });

  it('returns -1 when no direct or fallback fork marker can be resolved', () => {
    const milestones = [
      { milestone_id: 'ms-1', milestone_type: 'dev_started', fork_workflow_id: '' },
      { milestone_id: 'ms-2', milestone_type: 'workflow_forked', fork_workflow_id: '' },
      { milestone_id: 'ms-3', milestone_type: 'workflow_forked', fork_workflow_id: '' },
    ];

    expect(findForkMilestoneIndex(milestones, { childWorkflowId: 'wf-missing' })).toBe(-1);
  });
});
