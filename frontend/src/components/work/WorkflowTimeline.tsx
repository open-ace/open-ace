/**
 * WorkflowTimeline Component - Timeline view for an autonomous development workflow
 *
 * Features:
 * - Vertical timeline grouped by dev_round
 * - Fork visualization: shared milestones → Y-connector → parallel branch columns
 * - Draggable column resize for parallel view
 * - Milestone cards with status indicators and expandable details
 * - Controls bar (pause/resume/stop/complete)
 * - Token usage display
 * - GitHub links, diff viewer, session detail modal
 */

import React, { useState, useMemo, useCallback, useRef, useEffect } from 'react';
import { useQueries } from '@tanstack/react-query';
import { useAppStore, useLanguage, useWorkspaceFullscreen } from '@/store';
import { t } from '@/i18n';
import { Button, Badge, Loading, Modal } from '@/components/common';
import {
  useWorkflowTimeline,
  useWorkflowActivity,
  usePauseWorkflow,
  useResumeWorkflow,
  useStopWorkflow,
  useMarkDone,
  useRetryWorkflow,
  useExtendPlanningTimeout,
  useMilestoneSession,
  useMilestoneDiff,
  useWorkflowPrDiff,
  useWorkflowForks,
  useWorkflow,
} from '@/hooks/useAutonomous';
import type { MilestoneSession } from '@/hooks/useAutonomous';
import { autonomousApi } from '@/api/autonomous';
import CancelRoundModal from './CancelRoundModal';
import ForkFromHereModal from './ForkFromHereModal';
import { MarkdownContent } from './MarkdownContent';
import { ForkConnector, BranchColumn } from './ForkConnector';
import { ACTIVE_WORKFLOW_STATUSES } from './AutonomousWorkflowList';
import { getAutonomousWorkflowStatusConfig } from './autonomousWorkflowStatus';
import type {
  AutonomousWorkflow,
  WorkflowDefinitionSnapshot,
  WorkflowMilestone,
} from '@/api/autonomous';
import {
  formatTokens,
  parseDiffFiles,
  parseDiffStats,
  type ParsedDiffFileStatus,
} from './WorkflowTimeline.utils';
import './WorkflowTimeline.css';

interface WorkflowTimelineProps {
  workflow: AutonomousWorkflow;
  onNavigateToWorkflow?: (workflowId: string) => void;
}

// Status icon map
const STATUS_ICONS: Record<string, string> = {
  completed: 'bi-check-circle-fill text-success',
  in_progress: 'bi-arrow-repeat text-primary',
  failed: 'bi-x-circle-fill text-danger',
  cancelled: 'bi-slash-circle-fill text-secondary',
  forked: 'bi-diagram-3-fill text-info',
  pending: 'bi-circle text-muted',
};

// Milestone type display config
const MILESTONE_DISPLAY: Record<string, { icon: string; color: string }> = {
  repo_setup: { icon: 'bi-github', color: 'dark' },
  issue_created: { icon: 'bi-card-text', color: 'info' },
  branch_created: { icon: 'bi-git', color: 'success' },
  plan_created: { icon: 'bi-lightbulb', color: 'warning' },
  plan_reviewed: { icon: 'bi-eye', color: 'info' },
  plan_refined: { icon: 'bi-pencil', color: 'warning' },
  plan_finalized: { icon: 'bi-clipboard-check', color: 'success' },
  dev_started: { icon: 'bi-code-slash', color: 'primary' },
  dev_completed: { icon: 'bi-check2-square', color: 'success' },
  tests_run: { icon: 'bi-activity', color: 'info' },
  pr_created: { icon: 'bi-git-pull-request', color: 'success' },
  pr_reviewed: { icon: 'bi-chat-left-text', color: 'warning' },
  pr_updated: { icon: 'bi-pencil-square', color: 'primary' },
  pr_review_summary: { icon: 'bi-check2-circle', color: 'success' },
  conflicts_resolved: { icon: 'bi-cone-striped', color: 'warning' },
  progress_reported: { icon: 'bi-file-earmark-text', color: 'info' },
  requirement_received: { icon: 'bi-inbox', color: 'secondary' },
  round_completed: { icon: 'bi-flag-fill', color: 'success' },
  merged: { icon: 'bi-sign-merge-right', color: 'success' },
  cleaned_up: { icon: 'bi-trash', color: 'secondary' },
};

// Milestone types whose `plan_content` / `review_content` holds full-text output
// worth viewing after the run finishes (the live activity stream is run-time only).
const PLAN_CONTENT_TYPES = ['plan_created', 'plan_refined', 'plan_finalized'];
const REVIEW_CONTENT_TYPES = ['plan_reviewed', 'pr_reviewed', 'pr_review_summary'];
const PHASE_LABEL_KEYS: Record<string, string> = {
  preparation: 'autoPhasePreparation',
  planning: 'autoPhasePlanning',
  development: 'autoPhaseDevelopment',
  pr_review: 'autoPhasePRReview',
  report: 'autoPhaseReport',
  wait: 'autoPhaseWait',
  merge: 'autoPhaseMerge',
};

const ACTIVE_STATUS_HINT_KEYS: Record<string, string> = {
  queued: 'autoActiveHintQueued',
  pending: 'autoActiveHintPending',
  preparing: 'autoActiveHintPreparing',
  planning: 'autoActiveHintPlanning',
  developing: 'autoActiveHintDeveloping',
  pr_review: 'autoActiveHintPrReview',
  reporting: 'autoActiveHintReporting',
  merging: 'autoActiveHintMerging',
};

const MILESTONE_ICON_COLORS: Record<string, string> = {
  dark: 'var(--text-primary)',
  info: 'var(--color-info)',
  success: 'var(--color-success)',
  warning: 'var(--color-warning)',
  primary: 'var(--color-primary)',
  secondary: 'var(--text-tertiary)',
};

const TIMELINE_AUTO_SCROLL_THRESHOLD = 24;
const MAX_DIFF_VIEW_CHARS = 50000;

function truncateInlineText(text: string, max = 220): string {
  const normalized = text.replace(/\s+/g, ' ').trim();
  if (normalized.length <= max) return normalized;
  return `${normalized.slice(0, max).trimEnd()}...`;
}

function truncateDiffText(
  text: string,
  max = MAX_DIFF_VIEW_CHARS
): {
  content: string;
  isTruncated: boolean;
} {
  if (text.length <= max) {
    return { content: text, isTruncated: false };
  }
  return { content: text.slice(0, max), isTruncated: true };
}

function getDiffLineClass(line: string): string {
  if (line.startsWith('+') && !line.startsWith('+++')) {
    return 'workflow-timeline-diff-line-add';
  }
  if (line.startsWith('-') && !line.startsWith('---')) {
    return 'workflow-timeline-diff-line-del';
  }
  if (line.startsWith('@@')) {
    return 'workflow-timeline-diff-line-hunk';
  }
  if (
    line.startsWith('diff --git') ||
    line.startsWith('index ') ||
    line.startsWith('--- ') ||
    line.startsWith('+++ ') ||
    line.startsWith('new file mode ') ||
    line.startsWith('deleted file mode ') ||
    line.startsWith('rename from ') ||
    line.startsWith('rename to ')
  ) {
    return 'workflow-timeline-diff-line-meta';
  }
  return '';
}

function getActivityStableKey(activity: {
  session_id: string;
  type: 'assistant' | 'tool_use' | 'usage';
  timestamp?: string;
  text?: string;
  tool_name?: string;
}): string {
  return [
    activity.session_id,
    activity.type,
    activity.timestamp ?? '',
    activity.tool_name ?? '',
    (activity.text ?? '').slice(0, 40),
  ].join(':');
}

// ── Branch data type for parallel view ──────────────────────────────

interface BranchData {
  id: string;
  title: string;
  status: string;
  branchName: string;
  milestones: WorkflowMilestone[];
  colorIndex: number;
  isLoading?: boolean;
}

export const WorkflowTimeline: React.FC<WorkflowTimelineProps> = ({
  workflow,
  onNavigateToWorkflow,
}) => {
  const language = useLanguage();
  const workspaceFullscreen = useWorkspaceFullscreen();
  const { toggleWorkspaceFullscreen } = useAppStore();
  const timelineBodyRef = useRef<HTMLDivElement>(null);
  const [expandedMilestone, setExpandedMilestone] = useState<string | null>(null);
  const [showStopConfirm, setShowStopConfirm] = useState(false);
  const [shouldAutoScroll, setShouldAutoScroll] = useState(true);
  const [viewingSession, setViewingSession] = useState<{
    milestoneId: string;
    sessionId: string;
  } | null>(null);
  const [showBranchSelector, setShowBranchSelector] = useState(false);
  const [viewingDiff, setViewingDiff] = useState<string | null>(null);
  const [selectedDiffFileId, setSelectedDiffFileId] = useState<string | null>(null);
  const [selectedPrDiffFileId, setSelectedPrDiffFileId] = useState<string | null>(null);
  const [diffSidebarWidth, setDiffSidebarWidth] = useState(320);
  const [diffFullscreen, setDiffFullscreen] = useState(false);
  const [showDefinitionSnapshot, setShowDefinitionSnapshot] = useState(false);
  const [viewingContent, setViewingContent] = useState<{
    title: string;
    content: string;
  } | null>(null);
  const [contentFullscreen, setContentFullscreen] = useState(false);
  const [viewingPrDiff, setViewingPrDiff] = useState(false);

  const { data: timelineData, isLoading } = useWorkflowTimeline(workflow.workflow_id);
  const pauseMutation = usePauseWorkflow();
  const resumeMutation = useResumeWorkflow();
  const stopMutation = useStopWorkflow();
  const markDoneMutation = useMarkDone();
  const retryMutation = useRetryWorkflow();
  const extendTimeoutMutation = useExtendPlanningTimeout();

  // Session detail query
  const { data: sessionData, isLoading: sessionLoading } = useMilestoneSession(
    workflow.workflow_id,
    viewingSession?.milestoneId ?? '',
    !!viewingSession
  );

  // Diff query
  const { data: diffData, isLoading: diffLoading } = useMilestoneDiff(
    workflow.workflow_id,
    viewingDiff ?? '',
    !!viewingDiff
  );

  // Cumulative PR diff (head vs base) for the header "Final Code Changes" button
  const { data: prDiffData, isLoading: prDiffLoading } = useWorkflowPrDiff(
    workflow.workflow_id,
    viewingPrDiff
  );

  // Real-time agent activity (only when workflow is active)
  const isWorkflowActive = ACTIVE_WORKFLOW_STATUSES.includes(workflow.status);
  const activities = useWorkflowActivity(workflow.workflow_id, isWorkflowActive);

  const milestones = useMemo(() => timelineData?.milestones ?? [], [timelineData?.milestones]);

  // Latest (highest dev_round, non-empty content) finalized plan / PR review summary.
  // Each dev_round produces its own; the header "final" buttons surface the newest,
  // and a round badge shows which round it came from (fallback to an earlier round
  // if the newest round hasn't produced one yet).
  const latestPlanFinalized = useMemo(() => {
    const candidates = milestones.filter(
      (m) => m.milestone_type === 'plan_finalized' && m.plan_content?.trim()
    );
    if (!candidates.length) return undefined;
    return candidates.reduce((best, m) => ((m.dev_round || 0) > (best.dev_round || 0) ? m : best));
  }, [milestones]);

  const latestPrReviewSummary = useMemo(() => {
    const candidates = milestones.filter(
      (m) => m.milestone_type === 'pr_review_summary' && m.review_content?.trim()
    );
    if (!candidates.length) return undefined;
    return candidates.reduce((best, m) => ((m.dev_round || 0) > (best.dev_round || 0) ? m : best));
  }, [milestones]);

  const hasPr = !!workflow.github_pr_number;

  const definitionSnapshot = workflow.definition_snapshot;
  const normalizeGithubRepoUrl = useCallback((value: string) => {
    const text = value.trim();
    if (!text) return '';
    const cleaned = text.replace(/\.git$/i, '').replace(/\/+$/, '');
    if (/^https:\/\/github\.com\/[^/]+\/[^/]+$/i.test(cleaned)) {
      return cleaned;
    }
    const sshMatch = cleaned.match(/^git@github\.com:([^/]+)\/(.+)$/i);
    if (sshMatch) {
      return `https://github.com/${sshMatch[1]}/${sshMatch[2]}`;
    }
    const shortMatch = cleaned.match(/^([^/\s]+)\/([^/\s]+)$/);
    if (shortMatch) {
      return `https://github.com/${shortMatch[1]}/${shortMatch[2]}`;
    }
    return '';
  }, []);
  const resolvedIssueUrl = useMemo(() => {
    const directUrl =
      workflow.requirements_issue_url ??
      definitionSnapshot?.resolved_issue_url ??
      definitionSnapshot?.parsed_issue_selectors?.find(
        (selector) => selector.requirements_issue_url
      )?.requirements_issue_url ??
      '';

    if (directUrl) {
      return directUrl;
    }

    const issueNumber = workflow.github_issue_number;
    const repoUrl = normalizeGithubRepoUrl(
      workflow.project_repo_url ??
        definitionSnapshot?.project_repo_url ??
        (workflow.github_pr_url
          ? workflow.github_pr_url.replace(/\/pull\/\d+(?:[/?#].*)?$/i, '')
          : '')
    );
    if (!issueNumber || !repoUrl) {
      return '';
    }

    return `${repoUrl}/issues/${issueNumber}`;
  }, [
    definitionSnapshot?.parsed_issue_selectors,
    definitionSnapshot?.project_repo_url,
    definitionSnapshot?.resolved_issue_url,
    normalizeGithubRepoUrl,
    workflow.github_issue_number,
    workflow.github_pr_url,
    workflow.project_repo_url,
    workflow.requirements_issue_url,
  ]);

  const isActive = ACTIVE_WORKFLOW_STATUSES.includes(workflow.status);
  const isPaused = workflow.status === 'paused';
  const isWaiting = workflow.current_phase === 'wait';
  const allowMilestoneActions =
    isActive || isPaused || isWaiting || workflow.status === 'planning_timeout';

  // Modal state for cancel/fork
  const [showCancelModal, setShowCancelModal] = useState<string | null>(null);
  const [showForkModal, setShowForkModal] = useState<string | null>(null);

  // ── Fork Detection ────────────────────────────────────────────────

  const isForkChild = !!workflow.parent_workflow_id;
  const { data: forksData } = useWorkflowForks(workflow.workflow_id, !isForkChild);
  const forks = useMemo(() => forksData?.forks ?? [], [forksData?.forks]);
  const isForkParent = forks.length > 0;

  // Load fork timelines (for parent view)
  const forkTimelineQueries = useQueries({
    queries: forks.map((fork) => ({
      queryKey: ['autonomous', 'timeline', fork.workflow_id],
      queryFn: () => autonomousApi.getTimeline(fork.workflow_id),
      enabled: isForkParent,
      staleTime: 3 * 1000,
      refetchInterval: isForkParent ? 5 * 1000 : (false as const),
    })),
  });

  // Load parent data (for fork child view)
  const { data: parentData } = useWorkflow(workflow.parent_workflow_id ?? '', isForkChild);
  const { data: parentTimelineData } = useWorkflowTimeline(
    workflow.parent_workflow_id ?? '',
    isForkChild
  );
  const parentWorkflow = parentData?.workflow;
  const parentMilestones = useMemo(
    () => parentTimelineData?.milestones ?? [],
    [parentTimelineData?.milestones]
  );

  // ── Compute Fork Visualization Data ───────────────────────────────

  const forkViz = useMemo<{
    sharedMilestones: WorkflowMilestone[];
    branches: BranchData[];
    feedback: string;
  } | null>(() => {
    if (isForkParent) {
      // Case 1: This workflow has forks — we are the parent
      const forkIdx = milestones.findIndex(
        (m) => m.fork_workflow_id && m.fork_workflow_id.trim() !== ''
      );
      if (forkIdx < 0) return null;

      const shared = milestones.slice(0, forkIdx + 1);
      const originalPostFork = milestones.slice(forkIdx + 1);

      const branches: BranchData[] = [
        {
          id: workflow.workflow_id,
          title: workflow.title || 'Original',
          status: workflow.status,
          branchName: workflow.branch_name,
          milestones: originalPostFork,
          colorIndex: 0,
        },
        ...forks.map((fork, i) => {
          const forkTlData = forkTimelineQueries[i]?.data?.milestones ?? [];
          // Fork has copied milestones up to forkIdx, then its own
          const newMilestones = forkTlData.slice(forkIdx + 1);
          return {
            id: fork.workflow_id,
            title: fork.title || `Fork ${i + 1}`,
            status: fork.status,
            branchName: fork.branch_name,
            milestones: newMilestones,
            colorIndex: i + 1,
            isLoading: forkTimelineQueries[i]?.isLoading,
          };
        }),
      ];

      return {
        sharedMilestones: shared,
        branches,
        feedback: forks[0]?.user_feedback || '',
      };
    }

    if (isForkChild && parentWorkflow && parentMilestones.length > 0) {
      // Case 2: This workflow is a fork — show from child perspective
      const forkIdx = parentMilestones.findIndex(
        (m) => m.fork_workflow_id === workflow.workflow_id
      );
      if (forkIdx < 0) return null;

      const shared = parentMilestones.slice(0, forkIdx + 1);
      const parentPostFork = parentMilestones.slice(forkIdx + 1);

      // Our new milestones (after copied ones)
      const ourNewMilestones = milestones.slice(forkIdx + 1);

      const branches: BranchData[] = [
        {
          id: parentWorkflow.workflow_id,
          title: parentWorkflow.title || 'Original',
          status: parentWorkflow.status,
          branchName: parentWorkflow.branch_name,
          milestones: parentPostFork,
          colorIndex: 0,
        },
        {
          id: workflow.workflow_id,
          title: workflow.title || 'Fork',
          status: workflow.status,
          branchName: workflow.branch_name,
          milestones: ourNewMilestones,
          colorIndex: 1,
        },
      ];

      return {
        sharedMilestones: shared,
        branches,
        feedback: workflow.user_feedback || '',
      };
    }

    return null;
  }, [
    isForkParent,
    isForkChild,
    milestones,
    forks,
    forkTimelineQueries,
    parentWorkflow,
    parentMilestones,
    workflow,
  ]);

  // ── Draggable Column Width ────────────────────────────────────────

  const [leftWidth, setLeftWidth] = useState(50);
  const parallelContainerRef = useRef<HTMLDivElement>(null);

  const handleResizeStart = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault();
      const container = parallelContainerRef.current;
      if (!container) return;

      const startX = e.clientX;
      const startWidth = leftWidth;
      const containerWidth = container.getBoundingClientRect().width;

      const handleMove = (moveEvent: MouseEvent) => {
        const deltaX = moveEvent.clientX - startX;
        const deltaPercent = (deltaX / containerWidth) * 100;
        setLeftWidth(Math.max(20, Math.min(80, startWidth + deltaPercent)));
      };

      const handleUp = () => {
        document.removeEventListener('mousemove', handleMove);
        document.removeEventListener('mouseup', handleUp);
        document.body.style.cursor = '';
        document.body.style.userSelect = '';
      };

      document.body.style.cursor = 'col-resize';
      document.body.style.userSelect = 'none';
      document.addEventListener('mousemove', handleMove);
      document.addEventListener('mouseup', handleUp);
    },
    [leftWidth]
  );

  // ── Helpers ───────────────────────────────────────────────────────

  // Collect available branches for merge selection
  const availableBranches = React.useMemo(() => {
    const branches = [workflow.branch_name].filter(Boolean);
    milestones.forEach((ms) => {
      if (ms.fork_branch && !branches.includes(ms.fork_branch)) {
        branches.push(ms.fork_branch);
      }
    });
    return branches;
  }, [workflow.branch_name, milestones]);

  // Group milestones by dev_round
  const groupedMilestones = milestones.reduce<Record<number, WorkflowMilestone[]>>((acc, ms) => {
    const round = ms.dev_round || 1;
    if (!acc[round]) acc[round] = [];
    acc[round].push(ms);
    return acc;
  }, {});

  const sortedRounds = Object.keys(groupedMilestones)
    .map(Number)
    .sort((a, b) => a - b);

  const handlePause = () => pauseMutation.mutate(workflow.workflow_id);
  const handleResume = () => resumeMutation.mutate(workflow.workflow_id);
  const handleStopConfirm = () => {
    stopMutation.mutate(workflow.workflow_id);
    setShowStopConfirm(false);
  };
  const handleMarkDone = () => {
    if (availableBranches.length <= 1) {
      markDoneMutation.mutate({
        workflowId: workflow.workflow_id,
        selectedBranch: availableBranches[0],
      });
    } else {
      setShowBranchSelector(true);
    }
  };
  const handleBranchSelect = (branch: string) => {
    markDoneMutation.mutate({ workflowId: workflow.workflow_id, selectedBranch: branch });
    setShowBranchSelector(false);
  };
  const handleRetry = () => retryMutation.mutate(workflow.workflow_id);

  const formatDefinitionValue = (value: unknown) => {
    if (value === null || value === undefined || value === '') {
      return '-';
    }
    if (typeof value === 'boolean') {
      return value ? t('autoYes', language) : t('autoNo', language);
    }
    return String(value);
  };

  const renderDefinitionRows = (
    rows: Array<[string, unknown]>,
    snapshot: WorkflowDefinitionSnapshot
  ) => (
    <div className="row g-2">
      {rows.map(([label, value]) => (
        <div key={label} className="col-md-6">
          <div className="border rounded p-2 h-100">
            <div className="text-muted small mb-1">{label}</div>
            <div className="fw-semibold text-break">{formatDefinitionValue(value)}</div>
          </div>
        </div>
      ))}
      {snapshot.batch_id && (
        <div className="col-12">
          <div className="border rounded p-2">
            <div className="text-muted small mb-1">{t('autoBatchInfo', language)}</div>
            <div className="d-flex flex-wrap gap-2">
              <Badge variant="light">
                {snapshot.batch_order}/{snapshot.batch_total}
              </Badge>
              <code>{snapshot.batch_id}</code>
            </div>
          </div>
        </div>
      )}
    </div>
  );

  const getDiffStatusClass = (status: ParsedDiffFileStatus) => {
    switch (status) {
      case 'added':
        return 'success';
      case 'deleted':
        return 'danger';
      default:
        return 'warning';
    }
  };

  const formatMilestoneTime = (value: string | null) => {
    if (!value) return '';
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) return '';
    return new Intl.DateTimeFormat(undefined, {
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hour12: false,
    }).format(parsed);
  };

  const formatLiveActivityLine = (activity: {
    timestamp?: string;
    type: 'assistant' | 'tool_use' | 'usage';
    text?: string;
    tool_name?: string;
    tool_input?: string;
    total_tokens?: number;
  }) => {
    const timestamp = formatMilestoneTime(activity.timestamp ?? null) || '--:--:--';
    if (activity.type === 'tool_use') {
      const snippet = activity.tool_input?.trim();
      return {
        icon: 'bi-tools text-warning',
        timestamp,
        content: (
          <>
            <strong>{activity.tool_name ?? 'tool'}</strong>
            {snippet && (
              <span className="ms-1 opacity-75">
                {snippet.length > 96 ? `${snippet.slice(0, 96)}...` : snippet}
              </span>
            )}
          </>
        ),
      };
    }

    if (activity.type === 'usage') {
      return {
        icon: 'bi-bar-chart-line text-primary',
        timestamp,
        content: <>Token: {formatTokens(activity.total_tokens ?? 0)}</>,
      };
    }

    const text = activity.text?.trim() ?? '';
    return {
      icon: 'bi-chat-text text-info',
      timestamp,
      content: <>{text.length > 120 ? `${text.slice(0, 120)}...` : text}</>,
    };
  };

  const formatSessionMessageContent = useCallback((content: string) => {
    const trim = content.trim();
    if (!trim) return '';

    const extractFromBlock = (block: unknown): string[] => {
      if (!block || typeof block !== 'object') return [];
      const item = block as Record<string, unknown>;
      const type = String(item.type ?? '');
      if (type === 'thinking') {
        const thinking = item.thinking;
        return typeof thinking === 'string' && thinking.trim() ? [thinking.trim()] : [];
      }
      if (type === 'text') {
        const text = item.text;
        return typeof text === 'string' && text.trim() ? [text.trim()] : [];
      }
      if (Array.isArray(item.content)) {
        return item.content.flatMap(extractFromBlock);
      }
      return [];
    };

    try {
      const parsed = JSON.parse(trim) as unknown;
      if (Array.isArray(parsed)) {
        const parts = parsed.flatMap(extractFromBlock).filter(Boolean);
        if (parts.length > 0) {
          return parts.join('\n\n');
        }
      } else if (parsed && typeof parsed === 'object') {
        const parts = extractFromBlock(parsed).filter(Boolean);
        if (parts.length > 0) {
          return parts.join('\n\n');
        }
      }
    } catch {
      // Fall back to raw text
    }

    return trim;
  }, []);

  // Round-bearing milestone types grouped by phase. When a phase only ran one
  // round, its milestones show a numberless label (e.g. "方案评审"); when a
  // phase ran multiple rounds, they show the round number (e.g. "方案评审 轮次 2").
  // Singular milestones (plan_finalized / pr_review_summary) never carry a round.
  const ROUND_PHASE: Record<string, string> = {
    plan_created: 'planning',
    plan_refined: 'planning',
    plan_reviewed: 'planning',
    pr_reviewed: 'pr_review',
    pr_updated: 'pr_review',
    dev_started: 'development',
    tests_run: 'development',
  };

  const maxRoundByPhase = useMemo(() => {
    const map: Record<string, number> = {};
    for (const ms of milestones) {
      const phase = ROUND_PHASE[ms.milestone_type];
      if (!phase) continue;
      const r = ms.round_number || ms.dev_round || 0;
      if (r > (map[phase] || 0)) map[phase] = r;
    }
    return map;
    // ROUND_PHASE is a stable constant; depend on milestones only.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [milestones]);

  const formatMilestoneTitle = useCallback(
    (milestone: WorkflowMilestone) => {
      const title = milestone.title?.trim() || '';
      const round = milestone.round_number || milestone.dev_round || 0;
      const issueNumber = milestone.github_issue_number ?? workflow.github_issue_number ?? null;
      const prNumber = milestone.github_pr_number ?? workflow.github_pr_number ?? null;
      // Multi-round phase → show round number; single-round phase → numberless label.
      const multiRound = (phase: string) => (maxRoundByPhase[phase] ?? 0) > 1;
      const planningMulti = multiRound('planning');
      const prReviewMulti = multiRound('pr_review');
      const devMulti = multiRound('development');

      switch (milestone.milestone_type) {
        case 'issue_linked':
          return issueNumber
            ? `${t('autoMsIssueLinked', language)} #${issueNumber}`
            : t('autoMsIssueLinked', language);
        case 'issue_created':
          return issueNumber
            ? `${t('autoMsIssueCreated', language)} #${issueNumber}`
            : t('autoMsIssueCreated', language);
        case 'branch_created': {
          const branchMatch = title.match(/Branch ['"]?(.+?)['"]? created/i);
          const branchName = branchMatch?.[1] ?? workflow.branch_name ?? '';
          return branchName
            ? t('autoMsBranchCreated', language).replace('{branch}', branchName)
            : t('autoMsBranchCreatedGeneric', language);
        }
        case 'plan_created':
          return planningMulti
            ? t('autoMsPlanCreated', language).replace('{round}', String(round || 1))
            : t('autoMsPlanCreatedSingle', language);
        case 'plan_refined':
          return planningMulti
            ? t('autoMsPlanRefined', language).replace('{round}', String(round || 1))
            : t('autoMsPlanRefinedSingle', language);
        case 'plan_reviewed':
          return planningMulti
            ? t('autoMsPlanReviewed', language).replace('{round}', String(round || 1))
            : t('autoMsPlanReviewedSingle', language);
        case 'plan_finalized':
          return t('autoMsPlanFinalized', language);
        case 'dev_started':
          return devMulti
            ? t('autoMsDevelopmentStarted', language).replace('{round}', String(round || 1))
            : t('autoMsDevelopmentStartedSingle', language);
        case 'tests_run':
          return devMulti
            ? t('autoMsTestsRun', language).replace('{round}', String(round || 1))
            : t('autoMsTestsRunSingle', language);
        case 'dev_completed':
          return t('autoMsDevelopmentCompleted', language).replace('{round}', String(round || 1));
        case 'pr_created':
          return prNumber
            ? t('autoMsPrCreated', language).replace('{pr}', String(prNumber))
            : t('autoMsPrCreatedGeneric', language);
        case 'pr_reviewed':
          return prReviewMulti
            ? t('autoMsPrReviewed', language).replace('{round}', String(round || 1))
            : t('autoMsPrReviewedSingle', language);
        case 'pr_updated':
          return prReviewMulti
            ? t('autoMsPrUpdated', language).replace('{round}', String(round || 1))
            : t('autoMsPrUpdatedSingle', language);
        case 'pr_review_summary':
          return t('autoMsPrReviewSummary', language);
        case 'conflicts_resolved':
          return t('autoMsConflictsResolved', language);
        case 'progress_reported':
          return t('autoMsProgressReported', language).replace('{round}', String(round || 1));
        case 'round_completed':
          return t('autoMsRoundCompleted', language).replace('{round}', String(round || 1));
        case 'wait_started':
          return t('autoMsWaitStarted', language);
        case 'repo_setup':
          return t('autoMsRepoSetup', language);
        case 'no_changes':
          return t('autoMsNoChanges', language);
        case 'workflow_forked':
          return t('autoMsWorkflowForked', language);
        case 'merged':
          return prNumber
            ? t('autoMsMerged', language).replace('{pr}', String(prNumber))
            : t('autoMsMergedGeneric', language);
        case 'cleaned_up':
          return t('autoMsCleanedUp', language);
        case 'requirement_received':
          return t('autoMsRequirementReceived', language);
        default:
          return title || milestone.milestone_type;
      }
    },
    [
      language,
      maxRoundByPhase,
      workflow.branch_name,
      workflow.github_issue_number,
      workflow.github_pr_number,
    ]
  );

  const formatWorkflowDateTime = (value: string | null) => {
    if (!value) return '--';
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) return '--';
    return new Intl.DateTimeFormat(undefined, {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hour12: false,
    }).format(parsed);
  };

  const formatDuration = (start: string | null, end: string | null) => {
    if (!start) return '--';
    const startTime = new Date(start).getTime();
    const endTime = end ? new Date(end).getTime() : Date.now();
    if (Number.isNaN(startTime) || Number.isNaN(endTime) || endTime < startTime) return '--';

    let remainingSeconds = Math.floor((endTime - startTime) / 1000);
    const hours = Math.floor(remainingSeconds / 3600);
    remainingSeconds -= hours * 3600;
    const minutes = Math.floor(remainingSeconds / 60);
    const seconds = remainingSeconds - minutes * 60;

    if (hours > 0) {
      return `${hours}h ${minutes}m ${seconds}s`;
    }
    if (minutes > 0) {
      return `${minutes}m ${seconds}s`;
    }
    return `${seconds}s`;
  };

  const getMilestoneAnchorTime = (milestone: WorkflowMilestone) =>
    milestone.completed_at ?? milestone.started_at ?? milestone.created_at;

  const workflowStartTime =
    milestones.length > 0 ? getMilestoneAnchorTime(milestones[0]) : workflow.created_at;
  const workflowStatusConfig = getAutonomousWorkflowStatusConfig(workflow.status);
  const workflowStatusLabel = t(workflowStatusConfig.labelKey, language);
  const workflowPhaseLabel = t(
    PHASE_LABEL_KEYS[workflow.current_phase] ?? 'autoPhasePreparation',
    language
  );
  const isLiveStatus = ACTIVE_WORKFLOW_STATUSES.includes(workflow.status);
  const activeStatusHintKey = ACTIVE_STATUS_HINT_KEYS[workflow.status];
  const activeStatusHint = activeStatusHintKey ? t(activeStatusHintKey, language) : '';
  const latestMilestoneWithSession = [...milestones]
    .reverse()
    .find(
      (milestone) => milestone.llm_session_id ?? milestone.review_session_id ?? milestone.session_id
    );
  const latestFailedMilestone = [...milestones]
    .reverse()
    .find((milestone) => milestone.error_message || milestone.status === 'failed');
  const milestoneWithFinalChanges = [...milestones]
    .reverse()
    .find((milestone) => milestone.commit_shas);
  const stateBannerTone = workflow.error_message
    ? 'error'
    : workflow.status === 'planning_timeout' ||
        workflow.status === 'paused' ||
        workflow.status === 'waiting'
      ? 'warning'
      : 'info';
  const stateBannerMessage = workflow.error_message
    ? workflow.error_message
    : workflow.status === 'planning_timeout'
      ? t('autoBannerPlanningTimeout', language)
      : workflow.status === 'paused'
        ? t('autoBannerPaused', language)
        : workflow.status === 'waiting'
          ? t('autoBannerWaiting', language)
          : '';
  const showStateBanner = Boolean(
    workflow.error_message ||
    workflow.status === 'planning_timeout' ||
    workflow.status === 'paused' ||
    workflow.status === 'waiting'
  );

  const toggleExpandMilestone = (milestoneId: string) => {
    setExpandedMilestone((current) => (current === milestoneId ? null : milestoneId));
  };

  const closeViewingContent = () => {
    setViewingContent(null);
    setContentFullscreen(false);
  };

  const scrollTimelineToBottom = useCallback((behavior: ScrollBehavior = 'smooth') => {
    const timelineBody = timelineBodyRef.current;
    if (!timelineBody) return;

    timelineBody.scrollTo({
      top: timelineBody.scrollHeight,
      behavior,
    });
    setShouldAutoScroll(true);
  }, []);

  const isTimelineAtBottom = useCallback((element: HTMLDivElement) => {
    const distanceToBottom = element.scrollHeight - element.scrollTop - element.clientHeight;
    return distanceToBottom <= TIMELINE_AUTO_SCROLL_THRESHOLD;
  }, []);

  const handleTimelineScroll = useCallback(
    (event: React.UIEvent<HTMLDivElement>) => {
      setShouldAutoScroll(isTimelineAtBottom(event.currentTarget));
    },
    [isTimelineAtBottom]
  );

  const truncatedDiff = useMemo(() => truncateDiffText(diffData?.diff ?? ''), [diffData?.diff]);
  const truncatedPrDiff = useMemo(
    () => truncateDiffText(prDiffData?.diff ?? ''),
    [prDiffData?.diff]
  );
  const parsedDiffFiles = useMemo(
    () => parseDiffFiles(truncatedDiff.content),
    [truncatedDiff.content]
  );
  const parsedPrDiffFiles = useMemo(
    () => parseDiffFiles(truncatedPrDiff.content),
    [truncatedPrDiff.content]
  );
  const latestMilestoneSignature =
    milestones.length > 0
      ? [
          milestones[milestones.length - 1].milestone_id,
          milestones[milestones.length - 1].status,
          milestones[milestones.length - 1].updated_at ?? '',
        ].join(':')
      : '';
  const latestActivitySignature =
    activities.length > 0
      ? [
          activities[activities.length - 1].session_id,
          activities[activities.length - 1].type,
          activities[activities.length - 1].timestamp ?? '',
        ].join(':')
      : '';

  useEffect(() => {
    setShouldAutoScroll(true);
  }, [workflow.workflow_id]);

  useEffect(() => {
    const timelineBody = timelineBodyRef.current;
    if (!timelineBody || !shouldAutoScroll) return;

    const rafId = window.requestAnimationFrame(() => {
      scrollTimelineToBottom('auto');
    });

    return () => window.cancelAnimationFrame(rafId);
  }, [
    scrollTimelineToBottom,
    shouldAutoScroll,
    workflow.workflow_id,
    milestones.length,
    latestMilestoneSignature,
    activities.length,
    latestActivitySignature,
  ]);

  useEffect(() => {
    if (!viewingDiff) {
      setSelectedDiffFileId(null);
      setDiffFullscreen(false);
      return;
    }
    if (parsedDiffFiles.length === 0) {
      setSelectedDiffFileId(null);
      return;
    }
    setSelectedDiffFileId((prev) =>
      prev && parsedDiffFiles.some((file) => file.id === prev) ? prev : parsedDiffFiles[0].id
    );
  }, [viewingDiff, parsedDiffFiles]);

  useEffect(() => {
    if (!viewingPrDiff) {
      setSelectedPrDiffFileId(null);
      setDiffFullscreen(false);
      return;
    }
    if (parsedPrDiffFiles.length === 0) {
      setSelectedPrDiffFileId(null);
      return;
    }
    setSelectedPrDiffFileId((prev) =>
      prev && parsedPrDiffFiles.some((file) => file.id === prev) ? prev : parsedPrDiffFiles[0].id
    );
  }, [viewingPrDiff, parsedPrDiffFiles]);

  const selectedDiffFile = useMemo(
    () =>
      parsedDiffFiles.find((file) => file.id === selectedDiffFileId) ?? parsedDiffFiles[0] ?? null,
    [parsedDiffFiles, selectedDiffFileId]
  );
  const selectedPrDiffFile = useMemo(
    () =>
      parsedPrDiffFiles.find((file) => file.id === selectedPrDiffFileId) ??
      parsedPrDiffFiles[0] ??
      null,
    [parsedPrDiffFiles, selectedPrDiffFileId]
  );
  const renderDiffLines = useCallback(
    (content: string, keyPrefix: string, isTruncated = false) => {
      const lines = content.split('\n');
      return (
        <>
          {lines.map((line, index) => (
            <div
              key={`${keyPrefix}-line-${index}`}
              className={`workflow-timeline-diff-line ${getDiffLineClass(line)}`}
            >
              {line || ' '}
            </div>
          ))}
          {isTruncated && (
            <div
              key={`${keyPrefix}-truncated`}
              className="workflow-timeline-diff-line workflow-timeline-diff-line-meta"
            >
              {t('autoDiffTruncated', language)}
            </div>
          )}
        </>
      );
    },
    [language]
  );

  const handleDiffResizeStart = useCallback(
    (event: React.PointerEvent<HTMLDivElement>) => {
      event.preventDefault();
      const startX = event.clientX;
      const startWidth = diffSidebarWidth;
      const body = document.body;

      body.style.cursor = 'col-resize';
      body.style.userSelect = 'none';

      const handlePointerMove = (moveEvent: PointerEvent) => {
        const deltaX = moveEvent.clientX - startX;
        setDiffSidebarWidth(Math.max(220, Math.min(520, startWidth + deltaX)));
      };

      const handlePointerUp = () => {
        body.style.cursor = '';
        body.style.userSelect = '';
        window.removeEventListener('pointermove', handlePointerMove);
        window.removeEventListener('pointerup', handlePointerUp);
      };

      window.addEventListener('pointermove', handlePointerMove);
      window.addEventListener('pointerup', handlePointerUp);
    },
    [diffSidebarWidth]
  );

  // Extract typed session from query data
  const session = sessionData?.session as MilestoneSession | undefined;

  // ── Milestone Card Renderer ───────────────────────────────────────

  const renderMilestoneCard = (
    milestone: WorkflowMilestone,
    options?: { showForkCancel?: boolean; compact?: boolean }
  ) => {
    const showForkCancel = options?.showForkCancel ?? true;
    const compact = options?.compact ?? false;
    const isExpanded = expandedMilestone === milestone.milestone_id;
    const display = MILESTONE_DISPLAY[milestone.milestone_type] || {
      icon: 'bi-circle',
      color: 'secondary',
    };
    const statusIcon = STATUS_ICONS[milestone.status] || STATUS_ICONS.pending;
    const diffStats = parseDiffStats(milestone.diff_stats);
    const milestoneTime = formatMilestoneTime(getMilestoneAnchorTime(milestone));
    const llmSessionId =
      milestone.llm_session_id ?? milestone.review_session_id ?? milestone.session_id;
    const llmTotalTokens = milestone.llm_total_tokens ?? 0;
    const llmRequestCount = milestone.llm_request_count ?? 0;
    const showUsageMetrics = !!llmSessionId || llmTotalTokens > 0 || llmRequestCount > 0;
    const milestoneSessionIds = new Set(
      [llmSessionId, milestone.session_id, milestone.review_session_id].filter(Boolean)
    );
    const milestoneActivities =
      milestone.status === 'in_progress'
        ? activities.filter((activity) => milestoneSessionIds.has(activity.session_id))
        : [];
    const visibleMilestoneActivities = [...milestoneActivities]
      .sort((a, b) => {
        const rawATime = a.timestamp ? new Date(a.timestamp).getTime() : 0;
        const rawBTime = b.timestamp ? new Date(b.timestamp).getTime() : 0;
        const aTime = Number.isFinite(rawATime) ? rawATime : 0;
        const bTime = Number.isFinite(rawBTime) ? rawBTime : 0;
        return bTime - aTime;
      })
      .slice(0, 15);
    const hasLiveActivity = milestoneActivities.length > 0;
    const canViewChanges = !compact && !!milestone.commit_shas;
    const canViewPlanContent =
      !compact &&
      PLAN_CONTENT_TYPES.includes(milestone.milestone_type) &&
      !!milestone.plan_content?.trim();
    const canViewReviewContent =
      !compact &&
      REVIEW_CONTENT_TYPES.includes(milestone.milestone_type) &&
      !!milestone.review_content?.trim();
    const canFork =
      !compact &&
      allowMilestoneActions &&
      showForkCancel &&
      (milestone.status === 'completed' || milestone.status === 'in_progress');
    const canCancel =
      !compact && allowMilestoneActions && showForkCancel && milestone.status !== 'cancelled';
    const canExpand = hasLiveActivity || !!milestone.error_message;
    const rawSummary = (
      milestone.tldr ||
      milestone.result_summary ||
      milestone.description ||
      milestone.plan_content ||
      milestone.review_content ||
      ''
    ).trim();
    const milestoneSummary = rawSummary ? truncateInlineText(rawSummary, compact ? 120 : 220) : '';
    const statusTone =
      milestone.status === 'completed'
        ? 'success'
        : milestone.status === 'failed'
          ? 'danger'
          : milestone.status === 'in_progress'
            ? 'info'
            : milestone.status === 'cancelled'
              ? 'warning'
              : 'muted';
    const statusLabel =
      milestone.status === 'completed'
        ? t('autoStatusCompleted', language)
        : milestone.status === 'failed'
          ? t('autoStatusFailed', language)
          : milestone.status === 'in_progress'
            ? t('autoStatusDeveloping', language)
            : milestone.status === 'cancelled'
              ? t('autoStatusCancelled', language)
              : t('autoStatusPending', language);
    const milestoneDuration =
      milestone.started_at && (milestone.completed_at || milestone.status === 'in_progress')
        ? formatDuration(milestone.started_at, milestone.completed_at ?? milestone.updated_at)
        : '';
    const showDetailSections = isExpanded && canExpand;
    const showLiveActivitySection = milestone.status === 'in_progress' && hasLiveActivity;
    const showInlinePreview = !compact && milestoneSummary;
    const showInlineSessionButton = !compact && !!llmSessionId;
    const showInlineActionGroup =
      showInlineSessionButton ||
      canViewPlanContent ||
      canViewReviewContent ||
      canViewChanges ||
      canFork ||
      canCancel;

    return (
      <article
        key={milestone.milestone_id}
        className={`timeline-milestone-card ${
          compact ? 'timeline-milestone-card--compact' : ''
        } ${isExpanded ? 'timeline-milestone-card--expanded' : ''} timeline-milestone-card--${statusTone}`}
      >
        <div className="timeline-milestone-summary">
          <div className="timeline-milestone-summary-main">
            <div className={`timeline-milestone-status timeline-milestone-status--${statusTone}`}>
              <i className={`bi ${statusIcon}`}></i>
            </div>
            <div className="timeline-milestone-copy">
              <div className="timeline-milestone-title-row">
                <div className="timeline-milestone-title-group">
                  <i
                    className={`bi ${display.icon}`}
                    style={{
                      color: MILESTONE_ICON_COLORS[display.color] ?? 'var(--text-secondary)',
                    }}
                  ></i>
                  <span className="timeline-milestone-title">
                    {formatMilestoneTitle(milestone)}
                  </span>
                </div>
                <div className="timeline-milestone-time">
                  <span>{milestoneTime || '--:--:--'}</span>
                  {milestoneDuration && <span>{milestoneDuration}</span>}
                </div>
              </div>
              {showInlinePreview && (
                <p
                  className="timeline-milestone-preview"
                  title={rawSummary.length > 220 ? rawSummary : undefined}
                >
                  {milestoneSummary}
                </p>
              )}
              <div className="timeline-milestone-meta-row">
                <div className="timeline-milestone-badges">
                  <span className={`timeline-chip timeline-chip--${statusTone}`}>
                    {statusLabel}
                  </span>
                  {milestone.round_number > 0 && (
                    <span className="timeline-chip timeline-chip--subtle">
                      R{milestone.round_number}
                    </span>
                  )}
                  {showUsageMetrics && (
                    <>
                      <span className="timeline-chip timeline-chip--neutral">
                        {llmTotalTokens > 0 ? formatTokens(llmTotalTokens) : '--'}
                      </span>
                      <span className="timeline-chip timeline-chip--neutral">
                        {llmRequestCount > 0 ? llmRequestCount : '--'} {t('requests', language)}
                      </span>
                    </>
                  )}
                  {diffStats && !compact && (
                    <span className="timeline-chip timeline-chip--neutral">
                      +{diffStats.additions} / -{diffStats.deletions} / {diffStats.files}
                    </span>
                  )}
                </div>
                {showInlineActionGroup && (
                  <div className="timeline-milestone-actions">
                    {showInlineSessionButton && (
                      <Button
                        size="sm"
                        variant="outline-secondary"
                        className="timeline-inline-btn timeline-inline-btn--neutral"
                        onClick={() =>
                          setViewingSession({
                            milestoneId: milestone.milestone_id,
                            sessionId: llmSessionId ?? '',
                          })
                        }
                      >
                        <i className="bi bi-chat-square-text me-1"></i>
                        {t('autoSessionIdLabel', language)} {llmSessionId?.slice(0, 8)}
                      </Button>
                    )}
                    {canViewPlanContent && (
                      <Button
                        size="sm"
                        variant="outline-secondary"
                        className="timeline-inline-btn timeline-inline-btn--success"
                        onClick={() =>
                          setViewingContent({
                            title: t('autoViewPlanTitle', language),
                            content: milestone.plan_content,
                          })
                        }
                      >
                        <i className="bi bi-file-text me-1"></i>
                        {t('autoViewPlan', language)}
                      </Button>
                    )}
                    {canViewReviewContent && (
                      <Button
                        size="sm"
                        variant="outline-secondary"
                        className="timeline-inline-btn timeline-inline-btn--info"
                        onClick={() =>
                          setViewingContent({
                            title: t('autoViewReviewTitle', language),
                            content: milestone.review_content,
                          })
                        }
                      >
                        <i className="bi bi-chat-text me-1"></i>
                        {t('autoViewReview', language)}
                      </Button>
                    )}
                    {canViewChanges && (
                      <Button
                        size="sm"
                        variant="outline-secondary"
                        className="timeline-inline-btn timeline-inline-btn--primary"
                        onClick={() => setViewingDiff(milestone.milestone_id)}
                      >
                        <i className="bi bi-file-diff me-1"></i>
                        {t('autoViewChanges', language)}
                      </Button>
                    )}
                    {canFork && (
                      <Button
                        size="sm"
                        variant="outline-secondary"
                        className="timeline-inline-btn timeline-inline-btn--primary"
                        onClick={() => setShowForkModal(milestone.milestone_id)}
                      >
                        <i className="bi bi-diagram-3 me-1"></i>
                        {t('autoForkFromHere', language)}
                      </Button>
                    )}
                    {canCancel && (
                      <Button
                        size="sm"
                        variant="outline-secondary"
                        className="timeline-inline-btn timeline-inline-btn--danger"
                        onClick={() => setShowCancelModal(milestone.milestone_id)}
                      >
                        <i className="bi bi-x-circle me-1"></i>
                        {t('autoCancelRound', language)}
                      </Button>
                    )}
                  </div>
                )}
              </div>
            </div>
          </div>
          {canExpand && (
            <button
              type="button"
              className="timeline-milestone-chevron"
              onClick={() => toggleExpandMilestone(milestone.milestone_id)}
              aria-label={isExpanded ? t('collapse', language) : t('expand', language)}
            >
              <i className={`bi ${isExpanded ? 'bi-chevron-up' : 'bi-chevron-down'}`}></i>
            </button>
          )}
        </div>

        {showDetailSections && (
          <div className="timeline-milestone-details">
            {showLiveActivitySection && (
              <section className="timeline-milestone-section">
                <div className="timeline-milestone-section-label">
                  {t('autoAiActivity', language)}
                </div>
                <div className="timeline-milestone-section-body">
                  <div className="timeline-milestone-activity">
                    <div className="timeline-milestone-activity-title">
                      <span className="timeline-live-dot"></span>
                      {t('autoAiActivity', language)}
                    </div>
                    <div className="timeline-milestone-activity-list">
                      {visibleMilestoneActivities.map((activity) => {
                        const line = formatLiveActivityLine(activity);
                        return (
                          <div
                            key={`${milestone.milestone_id}-live-${getActivityStableKey(activity)}`}
                            className="timeline-milestone-activity-item"
                          >
                            <span className="timeline-milestone-activity-time">
                              {line.timestamp}
                            </span>
                            <i className={`bi ${line.icon}`}></i>
                            <span className="timeline-milestone-activity-text">{line.content}</span>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                </div>
              </section>
            )}

            {milestone.error_message && (
              <section className="timeline-milestone-section timeline-milestone-section--error">
                <div className="timeline-milestone-section-label">{t('error', language)}</div>
                <div className="timeline-milestone-section-body">
                  <p className="timeline-milestone-error">{milestone.error_message}</p>
                </div>
              </section>
            )}
          </div>
        )}
      </article>
    );
  };

  // ── Render Shared Milestones (for fork view) ──────────────────────

  const renderSharedSection = (sharedMilestones: WorkflowMilestone[]) => {
    // Group shared milestones by dev_round
    const grouped = sharedMilestones.reduce<Record<number, WorkflowMilestone[]>>((acc, ms) => {
      const round = ms.dev_round || 1;
      if (!acc[round]) acc[round] = [];
      acc[round].push(ms);
      return acc;
    }, {});
    const rounds = Object.keys(grouped)
      .map(Number)
      .sort((a, b) => a - b);

    return rounds.map((round) => (
      <section key={round} className="timeline-round">
        <div className="timeline-round-title">
          <div className="timeline-round-heading">
            <i className="bi bi-arrow-repeat"></i>
            <span>
              {t('autoDevRoundLabel', language)} {round}
            </span>
          </div>
        </div>
        <div className="timeline-stack">
          {grouped[round].map((ms) => renderMilestoneCard(ms, { showForkCancel: false }))}
        </div>
      </section>
    ));
  };

  // ── Render Branch Column Milestones ───────────────────────────────

  const renderBranchMilestones = (branch: BranchData) => {
    if (branch.isLoading) {
      return (
        <div className="text-center p-3">
          <Loading />
        </div>
      );
    }
    if (branch.milestones.length === 0) {
      return (
        <div className="text-center text-muted p-3" style={{ fontSize: '0.8rem' }}>
          <i className="bi bi-hourglass d-block mb-1"></i>
          {t('autoStatusPending', language)}...
        </div>
      );
    }

    // Group by dev_round within the branch
    const grouped = branch.milestones.reduce<Record<number, WorkflowMilestone[]>>((acc, ms) => {
      const round = ms.dev_round || 1;
      if (!acc[round]) acc[round] = [];
      acc[round].push(ms);
      return acc;
    }, {});
    const rounds = Object.keys(grouped)
      .map(Number)
      .sort((a, b) => a - b);

    return rounds.map((round) => (
      <section key={round} className="timeline-round timeline-round--compact">
        {rounds.length > 1 && (
          <div className="timeline-round-micro-label">
            {t('autoDevRoundLabel', language)} {round}
          </div>
        )}
        <div className="timeline-stack">
          {grouped[round].map((ms) => renderMilestoneCard(ms, { compact: true }))}
        </div>
      </section>
    ));
  };

  // ── Loading State ─────────────────────────────────────────────────

  if (isLoading) {
    return (
      <div className="d-flex align-items-center justify-content-center h-100">
        <Loading />
      </div>
    );
  }

  // ── Main Render ───────────────────────────────────────────────────

  return (
    <div className="timeline-shell d-flex flex-column h-100">
      <div className={`timeline-header ${activeStatusHint ? 'timeline-header--active' : ''}`}>
        <div className="timeline-header-top">
          <div className="timeline-header-main">
            <div className="timeline-header-title-row">
              {isForkChild && <i className="bi bi-diagram-3 timeline-header-fork-icon"></i>}
              <h5 className="timeline-header-title">
                {workflow.title || workflow.requirements_text?.slice(0, 80) || 'Workflow'}
              </h5>
            </div>
            <div className="timeline-header-badges">
              <span
                className={`timeline-status-pill timeline-status-pill--${workflowStatusConfig.tone} ${
                  isLiveStatus ? 'timeline-status-pill--live' : ''
                }`}
              >
                <span className="timeline-status-pill__icon">
                  <i className={`bi ${workflowStatusConfig.icon}`}></i>
                </span>
                {workflowStatusLabel}
              </span>
              {workflow.cli_tool && (
                <span className="timeline-chip timeline-chip--neutral">
                  <i className="bi bi-tools me-1"></i>
                  {workflow.cli_tool}
                </span>
              )}
              {definitionSnapshot && (
                <button
                  type="button"
                  className="timeline-pill-button"
                  onClick={() => setShowDefinitionSnapshot(true)}
                >
                  <i className="bi bi-file-earmark-text"></i>
                  <span>{t('autoViewDefinition', language)}</span>
                </button>
              )}
              {(resolvedIssueUrl || workflow.github_issue_number) &&
                (resolvedIssueUrl ? (
                  <a
                    href={resolvedIssueUrl}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="timeline-pill-link"
                  >
                    <i className="bi bi-card-text"></i>
                    <span>
                      {workflow.github_issue_number
                        ? `${t('autoIssueBadge', language)} #${workflow.github_issue_number}`
                        : t('autoIssueBadge', language)}
                    </span>
                  </a>
                ) : (
                  <span className="timeline-chip timeline-chip--subtle">
                    <i className="bi bi-card-text me-1"></i>
                    {t('autoIssueBadge', language)} #{workflow.github_issue_number}
                  </span>
                ))}
              {workflow.github_pr_number &&
                (workflow.github_pr_url ? (
                  <a
                    href={workflow.github_pr_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="timeline-pill-link"
                  >
                    <i className="bi bi-git-pull-request"></i>
                    <span>
                      {t('autoPrBadge', language)}
                      {workflow.github_pr_number}
                    </span>
                  </a>
                ) : (
                  <span className="timeline-chip timeline-chip--subtle">
                    <i className="bi bi-git-pull-request me-1"></i>
                    {t('autoPrBadge', language)}
                    {workflow.github_pr_number}
                  </span>
                ))}
              {isForkChild && parentWorkflow && (
                <span className="timeline-chip timeline-chip--info">
                  <i className="bi bi-diagram-3 me-1"></i>
                  {t('autoForkedFrom', language)}
                  {onNavigateToWorkflow ? (
                    <button
                      type="button"
                      className="timeline-inline-link timeline-inline-link--embedded"
                      onClick={() => onNavigateToWorkflow(parentWorkflow.workflow_id)}
                    >
                      {parentWorkflow.title?.slice(0, 30) || parentWorkflow.workflow_id.slice(0, 8)}
                    </button>
                  ) : (
                    <span className="ms-1">
                      {parentWorkflow.title?.slice(0, 30) || parentWorkflow.workflow_id.slice(0, 8)}
                    </span>
                  )}
                </span>
              )}
              {isForkParent && (
                <span className="timeline-chip timeline-chip--info">
                  <i className="bi bi-diagram-3 me-1"></i>
                  {t('autoForkedWorkflows', language)} ({forks.length})
                </span>
              )}
            </div>
          </div>

          <div className="timeline-header-actions">
            {workspaceFullscreen && (
              <Button
                size="sm"
                variant="outline-secondary"
                onClick={() => toggleWorkspaceFullscreen(false, false)}
              >
                <i className="bi bi-fullscreen-exit me-1"></i>
                {t('exitFullscreen', language)}
              </Button>
            )}
            {workflow.status === 'planning_timeout' && (
              <>
                <Button
                  size="sm"
                  variant="primary"
                  disabled={extendTimeoutMutation.isPending}
                  onClick={() =>
                    extendTimeoutMutation.mutate({
                      workflowId: workflow.workflow_id,
                      additionalSeconds: 600,
                    })
                  }
                >
                  <i className="bi bi-clock-history me-1"></i>
                  {t('autoExtendPlanning', language)} (+10min)
                </Button>
                <Button size="sm" variant="danger" onClick={() => setShowStopConfirm(true)}>
                  <i className="bi bi-stop-fill me-1"></i>
                  {t('autoStopWorkflow', language)}
                </Button>
              </>
            )}
            {workflow.status === 'failed' && (
              <Button
                size="sm"
                variant="primary"
                onClick={handleRetry}
                disabled={retryMutation.isPending}
              >
                <i className="bi bi-arrow-clockwise me-1"></i>
                {t('autoRetryWorkflow', language)}
              </Button>
            )}
            {isActive && !isPaused && (
              <Button size="sm" variant="warning" onClick={handlePause}>
                <i className="bi bi-pause-fill me-1"></i>
                {t('autoPauseWorkflow', language)}
              </Button>
            )}
            {isPaused && (
              <Button size="sm" variant="success" onClick={handleResume}>
                <i className="bi bi-play-fill me-1"></i>
                {t('autoResumeWorkflow', language)}
              </Button>
            )}
            {(isActive || isPaused) && !showStopConfirm && (
              <Button size="sm" variant="danger" onClick={() => setShowStopConfirm(true)}>
                <i className="bi bi-stop-fill me-1"></i>
                {t('autoStopWorkflow', language)}
              </Button>
            )}
            {showStopConfirm && (
              <>
                <Button size="sm" variant="danger" onClick={handleStopConfirm}>
                  {t('confirm', language)}
                </Button>
                <Button size="sm" variant="secondary" onClick={() => setShowStopConfirm(false)}>
                  {t('cancel', language)}
                </Button>
              </>
            )}
            {!showStateBanner && isWaiting && (
              <Button
                size="sm"
                variant="success"
                onClick={handleMarkDone}
                disabled={markDoneMutation.isPending}
              >
                <i className="bi bi-check-circle me-1"></i>
                {t('autoCompleteWorkflow', language)}
              </Button>
            )}
          </div>
        </div>

        {activeStatusHint && !showStateBanner && (
          <div
            className={`timeline-progress-note timeline-progress-note--${workflowStatusConfig.tone}`}
          >
            <span className="timeline-progress-note__indicator" aria-hidden="true"></span>
            <div className="timeline-progress-note__copy">
              <div className="timeline-progress-note__title">{workflowPhaseLabel}</div>
              <div className="timeline-progress-note__message">{activeStatusHint}</div>
            </div>
          </div>
        )}

        <div className="timeline-header-meta-grid">
          <div className="timeline-meta-item timeline-meta-item--phase">
            <span className="timeline-meta-item__label">{t('autoCurrentPhase', language)}</span>
            <span className="timeline-meta-item__value timeline-meta-item__value--phase">
              {workflowPhaseLabel}
            </span>
          </div>
          {workflow.model && (
            <div className="timeline-meta-item">
              <span className="timeline-meta-item__label">{t('autoModel', language)}</span>
              <span className="timeline-meta-item__value">{workflow.model}</span>
            </div>
          )}
          <div className="timeline-meta-item timeline-meta-item--start">
            <span className="timeline-meta-item__label">{t('autoStartTime', language)}</span>
            <span className="timeline-meta-item__value">
              {formatWorkflowDateTime(workflowStartTime)}
            </span>
          </div>
          <div className="timeline-meta-item">
            <span className="timeline-meta-item__label">{t('autoDuration', language)}</span>
            <span className="timeline-meta-item__value">
              {formatDuration(workflowStartTime, workflow.completed_at ?? workflow.updated_at)}
            </span>
          </div>
          <div className="timeline-meta-item">
            <span className="timeline-meta-item__label">{t('autoTokenUsage', language)}</span>
            <span className="timeline-meta-item__value">{formatTokens(workflow.total_tokens)}</span>
          </div>
          <div className="timeline-meta-item">
            <span className="timeline-meta-item__label">{t('totalRequests', language)}</span>
            <span className="timeline-meta-item__value">{workflow.total_requests}</span>
          </div>
          {workflow.dev_round > 1 && (
            <div className="timeline-meta-item">
              <span className="timeline-meta-item__label">{t('autoDevRoundLabel', language)}</span>
              <span className="timeline-meta-item__value">{workflow.dev_round}</span>
            </div>
          )}
        </div>

        <div className="timeline-output-rail">
          <div className="timeline-output-rail__buttons">
            <Button
              size="sm"
              variant="outline-secondary"
              className="timeline-output-btn"
              disabled={!latestPlanFinalized}
              onClick={() =>
                latestPlanFinalized &&
                setViewingContent({
                  title: t('autoViewPlanTitle', language),
                  content: latestPlanFinalized.plan_content,
                })
              }
            >
              <i className="bi bi-clipboard-check me-1"></i>
              {t('autoFinalPlan', language)}
              {latestPlanFinalized && (latestPlanFinalized.dev_round || 1) > 1 && (
                <Badge variant="light" className="ms-1" style={{ fontSize: '0.65rem' }}>
                  {t('autoRoundBadge', language).replace(
                    '{n}',
                    String(latestPlanFinalized.dev_round || 1)
                  )}
                </Badge>
              )}
            </Button>
            <Button
              size="sm"
              variant="outline-secondary"
              className="timeline-output-btn"
              disabled={!latestPrReviewSummary}
              onClick={() =>
                latestPrReviewSummary &&
                setViewingContent({
                  title: t('autoViewReviewTitle', language),
                  content: latestPrReviewSummary.review_content,
                })
              }
            >
              <i className="bi bi-check2-circle me-1"></i>
              {t('autoPrReviewSummary', language)}
              {latestPrReviewSummary && (latestPrReviewSummary.dev_round || 1) > 1 && (
                <Badge variant="light" className="ms-1" style={{ fontSize: '0.65rem' }}>
                  {t('autoRoundBadge', language).replace(
                    '{n}',
                    String(latestPrReviewSummary.dev_round || 1)
                  )}
                </Badge>
              )}
            </Button>
            <Button
              size="sm"
              variant="outline-secondary"
              className="timeline-output-btn"
              disabled={!hasPr}
              onClick={() => setViewingPrDiff(true)}
            >
              <i className="bi bi-file-diff me-1"></i>
              {t('autoFinalCodeChanges', language)}
            </Button>
          </div>
        </div>

        {showStateBanner && (
          <div className={`timeline-state-banner timeline-state-banner--${stateBannerTone}`}>
            <div className="timeline-state-banner__copy">
              <div className="timeline-state-banner__title">{workflowStatusLabel}</div>
              {stateBannerMessage && (
                <div className="timeline-state-banner__message">{stateBannerMessage}</div>
              )}
            </div>
            <div className="timeline-state-banner__actions">
              {isWaiting && (
                <Button
                  size="sm"
                  variant="success"
                  onClick={handleMarkDone}
                  disabled={markDoneMutation.isPending}
                >
                  <i className="bi bi-check-circle me-1"></i>
                  {t('autoCompleteWorkflow', language)}
                </Button>
              )}
              {latestFailedMilestone && (
                <button
                  type="button"
                  className="timeline-inline-link"
                  onClick={() => setExpandedMilestone(latestFailedMilestone.milestone_id)}
                >
                  {t('autoOpenLatestMilestone', language)}
                </button>
              )}
              {latestMilestoneWithSession && (
                <button
                  type="button"
                  className="timeline-inline-link"
                  onClick={() =>
                    setViewingSession({
                      milestoneId: latestMilestoneWithSession.milestone_id,
                      sessionId:
                        latestMilestoneWithSession.llm_session_id ??
                        latestMilestoneWithSession.review_session_id ??
                        latestMilestoneWithSession.session_id ??
                        '',
                    })
                  }
                >
                  {t('autoViewSession', language)}
                </button>
              )}
              {milestoneWithFinalChanges && (
                <button
                  type="button"
                  className="timeline-inline-link"
                  onClick={() => setViewingDiff(milestoneWithFinalChanges.milestone_id)}
                >
                  {t('autoViewChanges', language)}
                </button>
              )}
            </div>
          </div>
        )}
      </div>

      <div
        ref={timelineBodyRef}
        className="timeline-body flex-grow-1 overflow-auto"
        onScroll={handleTimelineScroll}
      >
        {forkViz ? (
          /* ── Fork Parallel View ────────────────────────────────── */
          <div className="timeline-layout">
            {/* Shared history section */}
            {forkViz.sharedMilestones.length > 0 && (
              <section className="timeline-shared-section">
                <div className="timeline-section-title">
                  <i className="bi bi-clock-history"></i>
                  <span>{t('autoSharedHistory', language)}</span>
                </div>
                {renderSharedSection(forkViz.sharedMilestones)}
              </section>
            )}

            {/* Fork connector */}
            <ForkConnector feedback={forkViz.feedback} branchCount={forkViz.branches.length} />

            {/* Parallel branch columns */}
            <div className="timeline-fork-branches d-flex" ref={parallelContainerRef}>
              {forkViz.branches.map((branch, idx) => (
                <React.Fragment key={branch.id}>
                  {/* Branch column */}
                  <div
                    style={{
                      width:
                        forkViz.branches.length === 1
                          ? '100%'
                          : forkViz.branches.length === 2
                            ? `${idx === 0 ? leftWidth : 100 - leftWidth}%`
                            : `${100 / forkViz.branches.length}%`,
                      minWidth: '120px',
                    }}
                  >
                    <BranchColumn
                      title={branch.title}
                      status={branch.status}
                      branchName={branch.branchName}
                      colorIndex={branch.colorIndex}
                    >
                      {renderBranchMilestones(branch)}
                    </BranchColumn>
                  </div>

                  {/* Resize handle between columns (only for 2-branch layout) */}
                  {idx < forkViz.branches.length - 1 && forkViz.branches.length === 2 && (
                    <div
                      onMouseDown={handleResizeStart}
                      style={{
                        width: '6px',
                        cursor: 'col-resize',
                        backgroundColor: 'var(--bs-gray-300)',
                        position: 'relative',
                        flexShrink: 0,
                        transition: 'background-color 0.15s',
                      }}
                      title={t('autoDragToResize', language)}
                      onMouseEnter={(e) => {
                        (e.currentTarget as HTMLDivElement).style.backgroundColor =
                          'var(--bs-primary)';
                      }}
                      onMouseLeave={(e) => {
                        (e.currentTarget as HTMLDivElement).style.backgroundColor =
                          'var(--bs-gray-300)';
                      }}
                    />
                  )}
                </React.Fragment>
              ))}
            </div>
          </div>
        ) : (
          /* ── Normal Timeline View ──────────────────────────────── */
          <>
            {sortedRounds.length === 0 ? (
              <div className="timeline-empty-state">
                <i className="bi bi-hourglass-split"></i>
                <p>{t('autoStatusPreparing', language)}...</p>
              </div>
            ) : (
              <div className="timeline-layout">
                {sortedRounds.map((round) => (
                  <section key={round} className="timeline-round">
                    <div className="timeline-round-title">
                      <div className="timeline-round-heading">
                        <i className="bi bi-arrow-repeat"></i>
                        <span>
                          {t('autoDevRoundLabel', language)} {round}
                        </span>
                      </div>
                      {round === workflow.dev_round && isActive && (
                        <span className="timeline-chip timeline-chip--info">
                          {t('autoStatusDeveloping', language)}
                        </span>
                      )}
                    </div>

                    <div className="timeline-stack">
                      {groupedMilestones[round].map((milestone) => renderMilestoneCard(milestone))}
                    </div>
                  </section>
                ))}
              </div>
            )}
          </>
        )}

        {!shouldAutoScroll && (
          <div className="timeline-scroll-hint">
            <Button
              size="sm"
              variant="primary"
              className="timeline-scroll-hint__button"
              onClick={() => scrollTimelineToBottom('smooth')}
            >
              <i className="bi bi-arrow-down-circle me-1"></i>
              {t('autoJumpToLatest', language)}
            </Button>
          </div>
        )}
      </div>

      {/* ── Modals ────────────────────────────────────────────────── */}

      {/* Session Detail Modal */}
      <Modal
        isOpen={!!viewingSession}
        onClose={() => setViewingSession(null)}
        title={
          viewingSession
            ? `${t('autoViewSession', language)}: ${viewingSession.sessionId.slice(0, 8)}`
            : ''
        }
        size="lg"
      >
        {sessionLoading ? (
          <Loading />
        ) : session ? (
          <div>
            <div className="mb-3">
              <strong>{t('status', language)}:</strong>{' '}
              <Badge variant={session.status === 'completed' ? 'success' : 'primary'}>
                {session.status}
              </Badge>
            </div>
            {Array.isArray(session.messages) &&
              session.messages.map((msg, idx) => (
                <div key={idx} className="mb-2">
                  <Badge
                    variant={
                      msg.role === 'assistant'
                        ? 'primary'
                        : msg.role === 'user'
                          ? 'success'
                          : 'secondary'
                    }
                  >
                    {(() => {
                      switch (msg.role) {
                        case 'user':
                          return t('messageRoleUser', language);
                        case 'assistant':
                          return t('messageRoleAssistant', language);
                        case 'system':
                          return t('messageRoleSystem', language);
                        case 'toolResult':
                          return t('messageRoleToolResult', language);
                        default:
                          return msg.role;
                      }
                    })()}
                  </Badge>
                  {typeof msg.content === 'string' ? (
                    <pre
                      className="bg-light p-2 rounded mt-1 mb-0"
                      style={{
                        fontSize: '0.8rem',
                        maxHeight: '200px',
                        overflow: 'auto',
                        whiteSpace: 'pre-wrap',
                      }}
                    >
                      {formatSessionMessageContent(msg.content).slice(0, 4000)}
                    </pre>
                  ) : null}
                </div>
              ))}
            {!Array.isArray(session.messages) && (
              <p className="text-muted">{t('autoNoMessagesAvailable', language)}</p>
            )}
          </div>
        ) : (
          <p className="text-muted">{t('autoNoSessionData', language)}</p>
        )}
      </Modal>

      {/* Markdown Content Modal (plan / review full text — Feature A & B share this) */}
      <Modal
        isOpen={!!viewingContent}
        onClose={closeViewingContent}
        title={viewingContent?.title ?? ''}
        size="md"
        className={`timeline-content-modal ${
          contentFullscreen ? 'timeline-content-modal--fullscreen' : ''
        }`}
        scrollable={false}
      >
        {viewingContent?.content?.trim() ? (
          <div className="timeline-content-modal__body">
            <div className="timeline-content-modal__toolbar">
              <Button
                size="sm"
                variant="outline-secondary"
                className="timeline-content-modal__toggle"
                onClick={() => setContentFullscreen((current) => !current)}
              >
                <i
                  className={`bi ${contentFullscreen ? 'bi-fullscreen-exit' : 'bi-fullscreen'} me-1`}
                ></i>
                {contentFullscreen ? t('exitFullscreen', language) : t('enterFullscreen', language)}
              </Button>
            </div>
            <div className="timeline-content-modal__document">
              <MarkdownContent
                content={viewingContent.content}
                className="timeline-content-markdown"
              />
            </div>
          </div>
        ) : (
          <p className="text-muted mb-0">{t('autoNoContent', language)}</p>
        )}
      </Modal>

      {/* PR Diff Modal (cumulative final code changes).
          Conditionally rendered (not isOpen-toggled like the content Modal
          above) so the inner <pre> remounts on every open and its scroll
          position resets to the top. */}
      {viewingPrDiff && (
        <Modal
          isOpen={true}
          onClose={() => setViewingPrDiff(false)}
          title={
            prDiffData?.pr_number
              ? `${t('autoFinalCodeChanges', language)} · PR #${prDiffData.pr_number}`
              : t('autoFinalCodeChanges', language)
          }
          size="xl"
          className={`workflow-timeline-diff-modal ${
            diffFullscreen ? 'workflow-timeline-diff-modal-fullscreen' : ''
          }`}
          scrollable={false}
        >
          {prDiffLoading ? (
            <div className="py-4">
              <Loading />
            </div>
          ) : parsedPrDiffFiles.length > 0 && selectedPrDiffFile ? (
            <div className="workflow-timeline-diff-shell">
              <div
                className="workflow-timeline-diff-sidebar"
                style={{ width: `${diffSidebarWidth}px`, minWidth: `${diffSidebarWidth}px` }}
              >
                {parsedPrDiffFiles.map((file) => (
                  <button
                    key={file.id}
                    type="button"
                    className={`workflow-timeline-diff-file ${
                      selectedPrDiffFile.id === file.id ? 'workflow-timeline-diff-file-active' : ''
                    }`}
                    onClick={() => setSelectedPrDiffFileId(file.id)}
                  >
                    <div className="d-flex align-items-center gap-2 min-width-0">
                      <Badge
                        variant={getDiffStatusClass(file.status)}
                        className="workflow-timeline-diff-file-badge"
                      >
                        {file.status === 'added' ? 'A' : file.status === 'deleted' ? 'D' : 'M'}
                      </Badge>
                      <span className="workflow-timeline-diff-file-path">{file.path}</span>
                    </div>
                    <span className="workflow-timeline-diff-file-stats">
                      {file.additions > 0 && (
                        <span className="text-success">+{file.additions}</span>
                      )}
                      {file.deletions > 0 && <span className="text-danger">-{file.deletions}</span>}
                    </span>
                  </button>
                ))}
              </div>
              <div
                className="workflow-timeline-diff-resizer"
                role="separator"
                aria-orientation="vertical"
                aria-label="Resize diff panels"
                onPointerDown={handleDiffResizeStart}
              />
              <div className="workflow-timeline-diff-viewer">
                <div className="workflow-timeline-diff-viewer-header">
                  <div className="min-width-0">
                    <div className="workflow-timeline-diff-viewer-path">
                      {selectedPrDiffFile.path}
                    </div>
                    {selectedPrDiffFile.commitLabel && (
                      <div className="workflow-timeline-diff-viewer-commit">
                        {t('autoCommits', language)} {selectedPrDiffFile.commitLabel}
                      </div>
                    )}
                  </div>
                  <div className="workflow-timeline-diff-viewer-summary">
                    {selectedPrDiffFile.additions > 0 && (
                      <span className="text-success">+{selectedPrDiffFile.additions}</span>
                    )}
                    {selectedPrDiffFile.deletions > 0 && (
                      <span className="text-danger">-{selectedPrDiffFile.deletions}</span>
                    )}
                  </div>
                  <button
                    type="button"
                    className="btn btn-sm btn-outline-secondary workflow-timeline-diff-fullscreen-btn"
                    onClick={() => setDiffFullscreen((prev) => !prev)}
                  >
                    <i
                      className={`bi ${diffFullscreen ? 'bi-fullscreen-exit' : 'bi-fullscreen'} me-1`}
                    />
                    {diffFullscreen
                      ? t('exitFullscreen', language)
                      : t('enterFullscreen', language)}
                  </button>
                </div>
                <div className="workflow-timeline-diff-code">
                  {renderDiffLines(
                    selectedPrDiffFile.patch,
                    selectedPrDiffFile.id,
                    truncatedPrDiff.isTruncated
                  )}
                </div>
              </div>
            </div>
          ) : prDiffData?.diff ? (
            <div className="workflow-timeline-diff-shell">
              <div className="workflow-timeline-diff-viewer">
                <div className="workflow-timeline-diff-code">
                  {renderDiffLines(
                    truncatedPrDiff.content,
                    'pr-diff-fallback',
                    truncatedPrDiff.isTruncated
                  )}
                </div>
              </div>
            </div>
          ) : (
            <p className="text-muted mb-0">{t('autoNoDiff', language)}</p>
          )}
        </Modal>
      )}

      {/* Branch Selector Modal */}
      <Modal
        isOpen={showBranchSelector}
        onClose={() => setShowBranchSelector(false)}
        title={t('autoSelectBranchToMerge', language)}
        footer={
          <Button variant="secondary" onClick={() => setShowBranchSelector(false)}>
            {t('cancel', language)}
          </Button>
        }
      >
        <div className="list-group">
          {availableBranches.map((branch) => (
            <button
              key={branch}
              className="list-group-item list-group-item-action d-flex align-items-center"
              onClick={() => handleBranchSelect(branch)}
            >
              <i className="bi bi-git me-2"></i>
              <code>{branch}</code>
              {branch === workflow.branch_name && (
                <Badge variant="primary" className="ms-2">
                  {t('autoCurrent', language)}
                </Badge>
              )}
            </button>
          ))}
        </div>
      </Modal>

      {/* Definition Snapshot Modal */}
      {definitionSnapshot && (
        <Modal
          isOpen={showDefinitionSnapshot}
          onClose={() => setShowDefinitionSnapshot(false)}
          title={t('autoWorkflowDefinition', language)}
          size="lg"
          footer={
            <Button variant="secondary" onClick={() => setShowDefinitionSnapshot(false)}>
              {t('close', language)}
            </Button>
          }
        >
          {(() => {
            const snapshot = definitionSnapshot;
            const rawIssueInput =
              snapshot.requirements_issue_input_raw ?? snapshot.requirements_issue_url_raw;
            const requirementText =
              snapshot.requirements_mode === 'text' ? snapshot.requirements_text : rawIssueInput;
            const creationRows: Array<[string, unknown]> = [
              [t('autoTaskTitle', language), snapshot.title],
              [t('autoAgentTool', language), snapshot.cli_tool],
              [t('autoModel', language), snapshot.model ?? t('autoDefaultModel', language)],
              [t('autoWorkspaceType', language), snapshot.workspace_type],
              [t('autoRemoteMachine', language), snapshot.remote_machine_id],
              [t('autoProjectPath', language), snapshot.project_path],
              [t('autoRepoUrl', language), snapshot.project_repo_url],
              [t('autoBranchStrategy', language), snapshot.branch_strategy],
              [t('autoBranchName', language), snapshot.branch_name],
              [t('autoMaxPlanRounds', language), snapshot.max_plan_rounds],
              [t('autoMaxPRReviewRounds', language), snapshot.max_pr_review_rounds],
              [t('autoMergeAfterPR', language), snapshot.auto_merge],
              [t('autoResolvedIssue', language), snapshot.resolved_issue_number],
              [t('autoResolvedIssueUrl', language), snapshot.resolved_issue_url],
            ];

            return (
              <div className="d-flex flex-column gap-3">
                <div>
                  <div className="d-flex align-items-center gap-2 mb-2">
                    <Badge variant="secondary">
                      {snapshot.requirements_mode === 'text'
                        ? t('autoTextDescription', language)
                        : t('autoGithubIssue', language)}
                    </Badge>
                    {snapshot.ignored_issue_tokens && snapshot.ignored_issue_tokens.length > 0 && (
                      <Badge variant="warning">
                        {t('autoIgnoredIssueTokens', language)}:{' '}
                        {snapshot.ignored_issue_tokens.join(', ')}
                      </Badge>
                    )}
                  </div>
                  <pre
                    className="bg-light border rounded p-3 mb-0"
                    style={{
                      fontSize: '0.82rem',
                      maxHeight: '220px',
                      overflow: 'auto',
                      whiteSpace: 'pre-wrap',
                    }}
                  >
                    {requirementText ?? '-'}
                  </pre>
                </div>

                {Array.isArray(snapshot.parsed_issue_selectors) &&
                  snapshot.parsed_issue_selectors.length > 0 && (
                    <div>
                      <div className="text-muted small mb-2">
                        {t('autoParsedIssueSelectors', language)}
                      </div>
                      <div className="d-flex flex-wrap gap-2">
                        {snapshot.parsed_issue_selectors.map((selector) => (
                          <Badge
                            key={`${selector.issue_number}-${selector.requirements_issue_url ?? ''}`}
                            variant={
                              selector.issue_number === snapshot.resolved_issue_number
                                ? 'primary'
                                : 'light'
                            }
                          >
                            #{selector.issue_number}
                          </Badge>
                        ))}
                      </div>
                    </div>
                  )}

                <div>
                  <div className="text-muted small mb-2">
                    {t('autoCreationParameters', language)}
                  </div>
                  {renderDefinitionRows(creationRows, snapshot)}
                </div>
              </div>
            );
          })()}
        </Modal>
      )}

      {/* Diff Viewer Modal */}
      {viewingDiff && (
        <Modal
          isOpen={true}
          onClose={() => setViewingDiff(null)}
          title={t('autoCodeChanges', language)}
          size="xl"
          className={`workflow-timeline-diff-modal ${
            diffFullscreen ? 'workflow-timeline-diff-modal-fullscreen' : ''
          }`}
          scrollable={false}
        >
          <div className="workflow-timeline-diff-shell">
            {diffLoading ? (
              <div className="py-4">
                <Loading />
              </div>
            ) : parsedDiffFiles.length > 0 && selectedDiffFile ? (
              <>
                <div
                  className="workflow-timeline-diff-sidebar"
                  style={{ width: `${diffSidebarWidth}px`, minWidth: `${diffSidebarWidth}px` }}
                >
                  {parsedDiffFiles.map((file) => (
                    <button
                      key={file.id}
                      type="button"
                      className={`workflow-timeline-diff-file ${
                        selectedDiffFile.id === file.id ? 'workflow-timeline-diff-file-active' : ''
                      }`}
                      onClick={() => setSelectedDiffFileId(file.id)}
                    >
                      <div className="d-flex align-items-center gap-2 min-width-0">
                        <Badge
                          variant={getDiffStatusClass(file.status)}
                          className="workflow-timeline-diff-file-badge"
                        >
                          {file.status === 'added' ? 'A' : file.status === 'deleted' ? 'D' : 'M'}
                        </Badge>
                        <span className="workflow-timeline-diff-file-path">{file.path}</span>
                      </div>
                      <span className="workflow-timeline-diff-file-stats">
                        {file.additions > 0 && (
                          <span className="text-success">+{file.additions}</span>
                        )}
                        {file.deletions > 0 && (
                          <span className="text-danger">-{file.deletions}</span>
                        )}
                      </span>
                    </button>
                  ))}
                </div>
                <div
                  className="workflow-timeline-diff-resizer"
                  role="separator"
                  aria-orientation="vertical"
                  aria-label="Resize diff panels"
                  onPointerDown={handleDiffResizeStart}
                />
                <div className="workflow-timeline-diff-viewer">
                  <div className="workflow-timeline-diff-viewer-header">
                    <div className="min-width-0">
                      <div className="workflow-timeline-diff-viewer-path">
                        {selectedDiffFile.path}
                      </div>
                      {selectedDiffFile.commitLabel && (
                        <div className="workflow-timeline-diff-viewer-commit">
                          {t('autoCommits', language)} {selectedDiffFile.commitLabel}
                        </div>
                      )}
                    </div>
                    <div className="workflow-timeline-diff-viewer-summary">
                      {selectedDiffFile.additions > 0 && (
                        <span className="text-success">+{selectedDiffFile.additions}</span>
                      )}
                      {selectedDiffFile.deletions > 0 && (
                        <span className="text-danger">-{selectedDiffFile.deletions}</span>
                      )}
                    </div>
                    <button
                      type="button"
                      className="btn btn-sm btn-outline-secondary workflow-timeline-diff-fullscreen-btn"
                      onClick={() => setDiffFullscreen((prev) => !prev)}
                    >
                      <i
                        className={`bi ${
                          diffFullscreen ? 'bi-fullscreen-exit' : 'bi-fullscreen'
                        } me-1`}
                      />
                      {diffFullscreen
                        ? t('exitFullscreen', language)
                        : t('enterFullscreen', language)}
                    </button>
                  </div>
                  <div className="workflow-timeline-diff-code">
                    {renderDiffLines(
                      selectedDiffFile.patch,
                      selectedDiffFile.id,
                      truncatedDiff.isTruncated
                    )}
                  </div>
                </div>
              </>
            ) : diffData?.diff ? (
              <pre
                className="bg-dark text-light p-3 rounded mb-0"
                style={{
                  fontSize: '0.75rem',
                  maxHeight: '70vh',
                  overflow: 'auto',
                  whiteSpace: 'pre-wrap',
                }}
              >
                {truncatedDiff.isTruncated
                  ? truncatedDiff.content + '\n\n' + t('autoDiffTruncated', language)
                  : truncatedDiff.content}
              </pre>
            ) : (
              <p className="text-muted mb-0">{t('autoNoDiff', language)}</p>
            )}
          </div>
        </Modal>
      )}

      {/* Cancel Round Modal */}
      {showCancelModal && (
        <CancelRoundModal
          isOpen={true}
          onClose={() => setShowCancelModal(null)}
          workflowId={workflow.workflow_id}
          milestoneId={showCancelModal}
          milestoneTitle={milestones.find((m) => m.milestone_id === showCancelModal)?.title ?? ''}
        />
      )}

      {/* Fork From Here Modal */}
      {showForkModal && (
        <ForkFromHereModal
          isOpen={true}
          onClose={() => setShowForkModal(null)}
          workflowId={workflow.workflow_id}
          milestoneId={showForkModal}
          milestoneTitle={milestones.find((m) => m.milestone_id === showForkModal)?.title ?? ''}
        />
      )}
    </div>
  );
};
