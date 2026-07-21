import { formatTokens } from '@/utils';

const AI_MILESTONE_TYPES = new Set([
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
]);

export function isAiMilestoneType(milestoneType: string): boolean {
  return AI_MILESTONE_TYPES.has(milestoneType);
}

export type ParsedDiffFileStatus = 'added' | 'modified' | 'deleted';

export interface ForkTimelineMilestoneLike {
  milestone_id: string;
  milestone_type: string;
  fork_workflow_id: string;
}

export interface ParsedDiffFile {
  id: string;
  path: string;
  status: ParsedDiffFileStatus;
  additions: number;
  deletions: number;
  patch: string;
  commitLabel?: string;
}

export interface ActivityHostMilestoneLike {
  milestone_id: string;
  milestone_type: string;
  status: string;
  dev_round: number;
}

export function getActivityHostMilestoneId(
  milestones: ActivityHostMilestoneLike[],
  workflowDevRound: number,
  workflowStatus: string
): string | null {
  const agentPhaseStatuses = ['planning', 'developing', 'pr_review'];
  if (![...agentPhaseStatuses, 'merging'].includes(workflowStatus)) return null;

  // Forked workflows can retain a copied in-progress milestone from the
  // parent. Timeline order is oldest-first, so always choose the newest
  // in-progress AI step from the current branch/round.
  const active = [...milestones]
    .reverse()
    .find(
      (milestone) =>
        milestone.status === 'in_progress' &&
        (milestone.dev_round || 1) === workflowDevRound &&
        isAiMilestoneType(milestone.milestone_type)
    );
  if (active) return active.milestone_id;

  // Preserve the panel only across short scheduler gaps in phases that
  // actively run an agent. System-only phases (queue/preparation/report/wait)
  // must not impersonate an AI request, and merge shows a panel only while an
  // explicit AI repair/conflict milestone is in progress (handled above).
  if (!agentPhaseStatuses.includes(workflowStatus)) return null;

  const fallback = [...milestones]
    .reverse()
    .find(
      (milestone) =>
        (milestone.dev_round || 1) === workflowDevRound &&
        isAiMilestoneType(milestone.milestone_type)
    );
  return fallback?.milestone_id ?? null;
}

export function parseDiffStats(
  statsJson: string
): { additions: number; deletions: number; files: number; commits: number } | null {
  try {
    return statsJson ? JSON.parse(statsJson) : null;
  } catch {
    return null;
  }
}

export function parseDiffFiles(diffText: string): ParsedDiffFile[] {
  if (!diffText.trim()) return [];

  const files: ParsedDiffFile[] = [];
  const lines = diffText.split('\n');
  let commitLabel = '';
  let current: {
    path: string;
    status: ParsedDiffFileStatus;
    additions: number;
    deletions: number;
    patchLines: string[];
    commitLabel?: string;
  } | null = null;

  const pushCurrent = () => {
    if (!current) return;
    files.push({
      id: `${current.commitLabel ?? 'no-commit'}:${current.path}:${files.length}`,
      path: current.path,
      status: current.status,
      additions: current.additions,
      deletions: current.deletions,
      patch: current.patchLines.join('\n').trim(),
      commitLabel: current.commitLabel,
    });
    current = null;
  };

  for (const line of lines) {
    if (line.startsWith('--- Commit: ')) {
      pushCurrent();
      commitLabel = line
        .replace(/^--- Commit:\s*/, '')
        .replace(/\s*---$/, '')
        .trim();
      continue;
    }

    if (line.startsWith('diff --git ')) {
      pushCurrent();
      const match = line.match(/^diff --git a\/(.+?) b\/(.+)$/);
      const nextPath = match?.[2] ?? match?.[1] ?? line.replace('diff --git ', '').trim();
      current = {
        path: nextPath,
        status: 'modified',
        additions: 0,
        deletions: 0,
        patchLines: [line],
        commitLabel,
      };
      continue;
    }

    if (!current) {
      continue;
    }

    current.patchLines.push(line);

    if (line.startsWith('new file mode ')) {
      current.status = 'added';
    } else if (line.startsWith('deleted file mode ')) {
      current.status = 'deleted';
    } else if (line.startsWith('rename to ')) {
      current.path = line.replace('rename to ', '').trim();
    } else if (line.startsWith('+') && !line.startsWith('+++')) {
      current.additions += 1;
    } else if (line.startsWith('-') && !line.startsWith('---')) {
      current.deletions += 1;
    }
  }

  pushCurrent();
  return files;
}

export function findForkMilestoneIndex(
  sourceMilestones: ForkTimelineMilestoneLike[],
  options?: {
    childWorkflowId?: string | null;
    fallbackMilestoneId?: string | null;
    preferFirstForkWorkflowId?: boolean;
  }
): number {
  if (!sourceMilestones.length) return -1;

  if (options?.preferFirstForkWorkflowId) {
    const firstForkWorkflowIndex = sourceMilestones.findIndex((m) => m.fork_workflow_id.trim());
    if (firstForkWorkflowIndex >= 0) return firstForkWorkflowIndex;
  }

  const childWorkflowId = options?.childWorkflowId?.trim() ?? '';
  if (childWorkflowId) {
    const directIndex = sourceMilestones.findIndex(
      (m) => m.fork_workflow_id.trim() === childWorkflowId
    );
    if (directIndex >= 0) return directIndex;
  }

  const fallbackMilestoneId = options?.fallbackMilestoneId?.trim() ?? '';
  if (fallbackMilestoneId) {
    const milestoneIdIndex = sourceMilestones.findIndex(
      (m) => m.milestone_id === fallbackMilestoneId
    );
    if (milestoneIdIndex >= 0) return milestoneIdIndex;
  }

  const forkMarkers = sourceMilestones
    .map((milestone, index) => ({ milestone, index }))
    .filter(({ milestone }) => milestone.milestone_type === 'workflow_forked');
  if (forkMarkers.length === 1) {
    return forkMarkers[0].index;
  }

  return -1;
}

export { formatTokens };
