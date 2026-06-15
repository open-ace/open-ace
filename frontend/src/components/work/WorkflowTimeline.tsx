/**
 * WorkflowTimeline Component - Timeline view for an autonomous development workflow
 *
 * Features:
 * - Semantic workflow header with summary, meta, outputs, and state banner
 * - Vertical timeline grouped by development round
 * - Fork visualization with shared history and parallel branch columns
 * - Expandable milestone cards with structured detail sections
 * - Existing workflow controls, diff viewer, session detail modal, and fork/cancel actions
 */

import React, { useMemo, useCallback, useRef, useState } from 'react';
import { useQueries } from '@tanstack/react-query';
import { useLanguage } from '@/store';
import { t } from '@/i18n';
import type { Language } from '@/i18n';
import { Button, Badge, Loading, Modal, type BadgeVariant } from '@/components/common';
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
  useWorkflowForks,
  useWorkflow,
} from '@/hooks/useAutonomous';
import type { MilestoneSession } from '@/hooks/useAutonomous';
import { autonomousApi } from '@/api/autonomous';
import CancelRoundModal from './CancelRoundModal';
import ForkFromHereModal from './ForkFromHereModal';
import { ForkConnector, BranchColumn } from './ForkConnector';
import { ACTIVE_WORKFLOW_STATUSES } from './AutonomousWorkflowList';
import { cn, formatDateTime, formatDuration, formatTokens, formatToolName } from '@/utils';
import type { AutonomousWorkflow, WorkflowMilestone } from '@/api/autonomous';
import './WorkflowTimeline.css';

interface WorkflowTimelineProps {
  workflow: AutonomousWorkflow;
  onNavigateToWorkflow?: (workflowId: string) => void;
}

interface BranchData {
  id: string;
  title: string;
  status: string;
  branchName: string;
  milestones: WorkflowMilestone[];
  colorIndex: number;
  isLoading?: boolean;
}

interface ArtifactPreviewState {
  title: string;
  content: string;
}

const WORKFLOW_STATUS_CONFIG: Record<
  string,
  { variant: BadgeVariant; icon: string; labelKey: string; tone: 'info' | 'warning' | 'success' | 'danger' }
> = {
  queued: {
    variant: 'secondary',
    icon: 'bi-hourglass-split',
    labelKey: 'autoStatusQueued',
    tone: 'info',
  },
  pending: {
    variant: 'secondary',
    icon: 'bi-hourglass',
    labelKey: 'autoStatusPending',
    tone: 'info',
  },
  preparing: {
    variant: 'info',
    icon: 'bi-gear',
    labelKey: 'autoStatusPreparing',
    tone: 'info',
  },
  planning: {
    variant: 'info',
    icon: 'bi-lightbulb',
    labelKey: 'autoStatusPlanning',
    tone: 'info',
  },
  developing: {
    variant: 'primary',
    icon: 'bi-code-slash',
    labelKey: 'autoStatusDeveloping',
    tone: 'info',
  },
  pr_review: {
    variant: 'warning',
    icon: 'bi-eye',
    labelKey: 'autoStatusPRReview',
    tone: 'warning',
  },
  reporting: {
    variant: 'info',
    icon: 'bi-file-text',
    labelKey: 'autoStatusReporting',
    tone: 'info',
  },
  waiting: {
    variant: 'secondary',
    icon: 'bi-clock',
    labelKey: 'autoStatusWaiting',
    tone: 'warning',
  },
  merging: {
    variant: 'info',
    icon: 'bi-git-merge',
    labelKey: 'autoStatusMerging',
    tone: 'info',
  },
  completed: {
    variant: 'success',
    icon: 'bi-check-circle',
    labelKey: 'autoStatusCompleted',
    tone: 'success',
  },
  failed: {
    variant: 'danger',
    icon: 'bi-x-circle',
    labelKey: 'autoStatusFailed',
    tone: 'danger',
  },
  cancelled: {
    variant: 'secondary',
    icon: 'bi-slash-circle',
    labelKey: 'autoStatusCancelled',
    tone: 'warning',
  },
  paused: {
    variant: 'warning',
    icon: 'bi-pause-circle',
    labelKey: 'autoStatusPaused',
    tone: 'warning',
  },
  planning_timeout: {
    variant: 'warning',
    icon: 'bi-clock-history',
    labelKey: 'autoStatusPlanningTimeout',
    tone: 'warning',
  },
};

const MILESTONE_STATUS_CONFIG: Record<
  string,
  {
    icon: string;
    tone: 'success' | 'info' | 'warning' | 'danger' | 'muted';
    labelKey?: string;
  }
> = {
  completed: { icon: 'bi-check-circle-fill', tone: 'success', labelKey: 'autoStatusCompleted' },
  in_progress: { icon: 'bi-arrow-repeat', tone: 'info', labelKey: 'autoStatusDeveloping' },
  failed: { icon: 'bi-x-circle-fill', tone: 'danger', labelKey: 'autoStatusFailed' },
  cancelled: { icon: 'bi-slash-circle-fill', tone: 'warning', labelKey: 'autoStatusCancelled' },
  forked: { icon: 'bi-diagram-3-fill', tone: 'info' },
  pending: { icon: 'bi-circle', tone: 'muted', labelKey: 'autoStatusPending' },
};

const MILESTONE_DISPLAY: Record<string, { icon: string; color: string }> = {
  repo_setup: { icon: 'bi-github', color: 'dark' },
  issue_created: { icon: 'bi-card-text', color: 'info' },
  branch_created: { icon: 'bi-git', color: 'success' },
  plan_created: { icon: 'bi-lightbulb', color: 'warning' },
  plan_reviewed: { icon: 'bi-eye', color: 'info' },
  plan_refined: { icon: 'bi-pencil', color: 'warning' },
  dev_started: { icon: 'bi-code-slash', color: 'primary' },
  dev_completed: { icon: 'bi-check2-square', color: 'success' },
  tests_run: { icon: 'bi-activity', color: 'info' },
  pr_created: { icon: 'bi-git-pull-request', color: 'success' },
  pr_reviewed: { icon: 'bi-chat-left-text', color: 'warning' },
  pr_updated: { icon: 'bi-pencil-square', color: 'primary' },
  progress_reported: { icon: 'bi-file-earmark-text', color: 'info' },
  requirement_received: { icon: 'bi-inbox', color: 'secondary' },
  round_completed: { icon: 'bi-flag-fill', color: 'success' },
  merged: { icon: 'bi-git-merge', color: 'success' },
  cleaned_up: { icon: 'bi-trash', color: 'secondary' },
};

const PHASE_LABEL_KEYS: Record<string, string> = {
  preparation: 'autoPhasePreparation',
  planning: 'autoPhasePlanning',
  development: 'autoPhaseDevelopment',
  pr_review: 'autoPhasePRReview',
  report: 'autoPhaseReport',
  wait: 'autoPhaseWait',
  merge: 'autoPhaseMerge',
};

function getWorkflowStatusMeta(status: string) {
  return WORKFLOW_STATUS_CONFIG[status] ?? WORKFLOW_STATUS_CONFIG.pending;
}

function getMilestoneStatusMeta(status: string) {
  return MILESTONE_STATUS_CONFIG[status] ?? MILESTONE_STATUS_CONFIG.pending;
}

function truncateText(text: string | null | undefined, max = 180): string {
  if (!text) return '';
  const normalized = text.replace(/\s+/g, ' ').trim();
  if (normalized.length <= max) return normalized;
  return `${normalized.slice(0, max).trimEnd()}...`;
}

function parseDiffStats(
  statsJson: string
): { additions: number; deletions: number; files: number; commits: number } | null {
  try {
    return statsJson ? JSON.parse(statsJson) : null;
  } catch {
    return null;
  }
}

function getDurationBetween(startedAt: string | null, endedAt: string | null): string | null {
  if (!startedAt) return null;
  const start = new Date(startedAt);
  const end = new Date(endedAt ?? Date.now());
  if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime())) return null;
  return formatDuration(Math.max(0, Math.floor((end.getTime() - start.getTime()) / 1000)));
}

function getMilestoneSummary(milestone: WorkflowMilestone): string {
  return truncateText(
    milestone.result_summary ||
      milestone.description ||
      milestone.review_content ||
      milestone.plan_content,
    160
  );
}

function getRequirementArtifact(workflow: AutonomousWorkflow): string {
  const parts: string[] = [];
  if (workflow.requirements_text?.trim()) {
    parts.push(workflow.requirements_text.trim());
  }
  if (workflow.requirements_issue_url?.trim()) {
    parts.push(workflow.requirements_issue_url.trim());
  } else if (workflow.github_issue_number) {
    parts.push(`GitHub Issue #${workflow.github_issue_number}`);
  }
  return parts.join('\n\n').trim();
}

function getMilestoneTimestampRange(milestone: WorkflowMilestone): string {
  const start = milestone.started_at ? new Date(milestone.started_at).toLocaleTimeString() : null;
  const end = milestone.completed_at ? new Date(milestone.completed_at).toLocaleTimeString() : null;
  if (start && end) return `${start} - ${end}`;
  return start ?? end ?? '-';
}

function WorkflowOutputButton({
  icon,
  label,
  disabled,
  onClick,
}: {
  icon: string;
  label: string;
  disabled?: boolean;
  onClick?: () => void;
}) {
  return (
    <button
      type="button"
      className="timeline-output-link"
      disabled={disabled}
      onClick={onClick}
    >
      <i className={`bi ${icon}`}></i>
      <span>{label}</span>
    </button>
  );
}

function TimelineTextSection({
  title,
  content,
  language,
}: {
  title: string;
  content: string;
  language: Language;
}) {
  return (
    <section className="milestone-detail-section">
      <div className="milestone-detail-label">{title}</div>
      <div className="milestone-detail-content">
        <p className="milestone-detail-preview">{truncateText(content, 260)}</p>
        {content.length > 260 && (
          <details className="timeline-text-expand">
            <summary>{t('showMore', language)}</summary>
            <pre className="timeline-code-block timeline-code-block--subtle">{content}</pre>
          </details>
        )}
      </div>
    </section>
  );
}

export const WorkflowTimeline: React.FC<WorkflowTimelineProps> = ({
  workflow,
  onNavigateToWorkflow,
}) => {
  const language = useLanguage();
  const [expandedMilestone, setExpandedMilestone] = useState<string | null>(null);
  const [showStopConfirm, setShowStopConfirm] = useState(false);
  const [viewingSession, setViewingSession] = useState<{
    milestoneId: string;
    sessionId: string;
  } | null>(null);
  const [showBranchSelector, setShowBranchSelector] = useState(false);
  const [viewingDiff, setViewingDiff] = useState<string | null>(null);
  const [showCancelModal, setShowCancelModal] = useState<string | null>(null);
  const [showForkModal, setShowForkModal] = useState<string | null>(null);
  const [viewingArtifact, setViewingArtifact] = useState<ArtifactPreviewState | null>(null);

  const { data: timelineData, isLoading } = useWorkflowTimeline(workflow.workflow_id);
  const pauseMutation = usePauseWorkflow();
  const resumeMutation = useResumeWorkflow();
  const stopMutation = useStopWorkflow();
  const markDoneMutation = useMarkDone();
  const retryMutation = useRetryWorkflow();
  const extendTimeoutMutation = useExtendPlanningTimeout();

  const { data: sessionData, isLoading: sessionLoading } = useMilestoneSession(
    workflow.workflow_id,
    viewingSession?.milestoneId ?? '',
    !!viewingSession
  );

  const { data: diffData, isLoading: diffLoading } = useMilestoneDiff(
    workflow.workflow_id,
    viewingDiff ?? '',
    !!viewingDiff
  );

  const isWorkflowActive = ACTIVE_WORKFLOW_STATUSES.includes(workflow.status);
  const activities = useWorkflowActivity(workflow.workflow_id, isWorkflowActive);
  const milestones = timelineData?.milestones ?? [];

  const isActive = ACTIVE_WORKFLOW_STATUSES.includes(workflow.status);
  const isPaused = workflow.status === 'paused';
  const isWaiting = workflow.current_phase === 'wait';

  const isForkChild = !!workflow.parent_workflow_id;
  const { data: forksData } = useWorkflowForks(workflow.workflow_id, !isForkChild);
  const forks = forksData?.forks ?? [];
  const isForkParent = forks.length > 0;

  const forkTimelineQueries = useQueries({
    queries: forks.map((fork) => ({
      queryKey: ['autonomous', 'timeline', fork.workflow_id],
      queryFn: () => autonomousApi.getTimeline(fork.workflow_id),
      enabled: isForkParent,
      staleTime: 3 * 1000,
      refetchInterval: isForkParent ? 5 * 1000 : (false as const),
    })),
  });

  const { data: parentData } = useWorkflow(workflow.parent_workflow_id ?? '', isForkChild);
  const { data: parentTimelineData } = useWorkflowTimeline(
    workflow.parent_workflow_id ?? '',
    isForkChild
  );
  const parentWorkflow = parentData?.workflow;
  const parentMilestones = parentTimelineData?.milestones ?? [];

  const forkViz = useMemo<{
    sharedMilestones: WorkflowMilestone[];
    branches: BranchData[];
    feedback: string;
  } | null>(() => {
    if (isForkParent) {
      const forkIdx = milestones.findIndex(
        (milestone) => milestone.fork_workflow_id && milestone.fork_workflow_id.trim() !== ''
      );
      if (forkIdx < 0) return null;

      const sharedMilestones = milestones.slice(0, forkIdx + 1);
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
        ...forks.map((fork, index) => {
          const forkTimeline = forkTimelineQueries[index]?.data?.milestones ?? [];
          return {
            id: fork.workflow_id,
            title: fork.title || `Fork ${index + 1}`,
            status: fork.status,
            branchName: fork.branch_name,
            milestones: forkTimeline.slice(forkIdx + 1),
            colorIndex: index + 1,
            isLoading: forkTimelineQueries[index]?.isLoading,
          };
        }),
      ];

      return {
        sharedMilestones,
        branches,
        feedback: forks[0]?.user_feedback || '',
      };
    }

    if (isForkChild && parentWorkflow && parentMilestones.length > 0) {
      const forkIdx = parentMilestones.findIndex(
        (milestone) => milestone.fork_workflow_id === workflow.workflow_id
      );
      if (forkIdx < 0) return null;

      const sharedMilestones = parentMilestones.slice(0, forkIdx + 1);
      const parentPostFork = parentMilestones.slice(forkIdx + 1);
      const childMilestones = milestones.slice(forkIdx + 1);

      return {
        sharedMilestones,
        branches: [
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
            milestones: childMilestones,
            colorIndex: 1,
          },
        ],
        feedback: workflow.user_feedback || '',
      };
    }

    return null;
  }, [
    forkTimelineQueries,
    forks,
    isForkChild,
    isForkParent,
    milestones,
    parentMilestones,
    parentWorkflow,
    workflow,
  ]);

  const [leftWidth, setLeftWidth] = useState(50);
  const parallelContainerRef = useRef<HTMLDivElement>(null);

  const handleResizeStart = useCallback(
    (event: React.MouseEvent) => {
      event.preventDefault();
      const container = parallelContainerRef.current;
      if (!container) return;

      const startX = event.clientX;
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

  const availableBranches = useMemo(() => {
    const branches = [workflow.branch_name].filter(Boolean);
    milestones.forEach((milestone) => {
      if (milestone.fork_branch && !branches.includes(milestone.fork_branch)) {
        branches.push(milestone.fork_branch);
      }
    });
    return branches;
  }, [milestones, workflow.branch_name]);

  const groupedMilestones = milestones.reduce<Record<number, WorkflowMilestone[]>>((acc, milestone) => {
    const round = milestone.dev_round || 1;
    if (!acc[round]) acc[round] = [];
    acc[round].push(milestone);
    return acc;
  }, {});

  const sortedRounds = Object.keys(groupedMilestones)
    .map(Number)
    .sort((a, b) => a - b);

  const session = sessionData?.session as MilestoneSession | undefined;
  const workflowStatusMeta = getWorkflowStatusMeta(workflow.status);
  const workflowStatusLabel = t(workflowStatusMeta.labelKey, language);
  const currentPhaseLabel = t(PHASE_LABEL_KEYS[workflow.current_phase] ?? 'autoPhasePreparation', language);
  const workflowDuration = getDurationBetween(workflow.created_at, workflow.completed_at ?? workflow.updated_at);

  const latestPlanMilestone = useMemo(
    () => [...milestones].reverse().find((milestone) => milestone.plan_content?.trim()),
    [milestones]
  );
  const latestReviewMilestone = useMemo(
    () => [...milestones].reverse().find((milestone) => milestone.review_content?.trim()),
    [milestones]
  );
  const latestChangeMilestone = useMemo(
    () => [...milestones].reverse().find((milestone) => milestone.commit_shas?.trim()),
    [milestones]
  );
  const latestErrorMilestone = useMemo(
    () => [...milestones].reverse().find((milestone) => milestone.error_message?.trim()),
    [milestones]
  );
  const latestSessionMilestone = useMemo(
    () => [...milestones].reverse().find((milestone) => milestone.session_id),
    [milestones]
  );

  const handlePause = () => pauseMutation.mutate(workflow.workflow_id);
  const handleResume = () => resumeMutation.mutate(workflow.workflow_id);
  const handleRetry = () => retryMutation.mutate(workflow.workflow_id);
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
      return;
    }
    setShowBranchSelector(true);
  };
  const handleBranchSelect = (branch: string) => {
    markDoneMutation.mutate({ workflowId: workflow.workflow_id, selectedBranch: branch });
    setShowBranchSelector(false);
  };

  const toggleExpand = (milestoneId: string) => {
    setExpandedMilestone((current) => (current === milestoneId ? null : milestoneId));
  };

  const openArtifact = (title: string, content: string) => {
    setViewingArtifact({ title, content });
  };

  const renderMilestoneCard = (
    milestone: WorkflowMilestone,
    options?: { showForkCancel?: boolean; compact?: boolean }
  ) => {
    const showForkCancel = options?.showForkCancel ?? true;
    const compact = options?.compact ?? false;
    const isExpanded = expandedMilestone === milestone.milestone_id;
    const milestoneStatus = getMilestoneStatusMeta(milestone.status);
    const milestoneDisplay = MILESTONE_DISPLAY[milestone.milestone_type] ?? {
      icon: 'bi-circle',
      color: 'secondary',
    };
    const diffStats = parseDiffStats(milestone.diff_stats);
    const milestoneSummary = getMilestoneSummary(milestone);
    const duration = getDurationBetween(milestone.started_at, milestone.completed_at);
    const milestoneActivities = milestone.session_id
      ? activities.filter((activity) => activity.session_id === milestone.session_id)
      : activities;
    const canViewDiff = !!milestone.commit_shas?.trim();

    return (
      <article
        key={milestone.milestone_id}
        className={cn(
          'milestone-card',
          compact && 'milestone-card--compact',
          isExpanded && 'milestone-card--expanded',
          milestone.status === 'failed' && 'milestone-card--failed',
          milestone.status === 'completed' && 'milestone-card--completed',
          milestone.status === 'in_progress' && 'milestone-card--active',
          milestone.status === 'cancelled' && 'milestone-card--cancelled'
        )}
      >
        <button
          type="button"
          className="milestone-card-summary"
          onClick={() => toggleExpand(milestone.milestone_id)}
        >
          <div className="milestone-card-summary-main">
            <div
              className={cn(
                'milestone-card-status-icon',
                `milestone-card-status-icon--${milestoneStatus.tone}`
              )}
            >
              <i className={`bi ${milestoneStatus.icon}`}></i>
            </div>
            <div className="milestone-card-copy">
              <div className="milestone-card-title-row">
                <div className="milestone-card-title-group">
                  <span className="milestone-card-type-icon">
                    <i className={`bi ${milestoneDisplay.icon} text-${milestoneDisplay.color}`}></i>
                  </span>
                  <span className="milestone-card-title">
                    {milestone.title || milestone.milestone_type}
                  </span>
                </div>
                <div className="milestone-card-summary-meta">
                  <span>{getMilestoneTimestampRange(milestone)}</span>
                  {duration && <span>{duration}</span>}
                </div>
              </div>
              {!compact && milestoneSummary && (
                <p className="milestone-card-subtitle">{milestoneSummary}</p>
              )}
              <div className="milestone-card-badges">
                {milestoneStatus.labelKey && (
                  <span className={cn('timeline-chip', `timeline-chip--${milestoneStatus.tone}`)}>
                    {t(milestoneStatus.labelKey, language)}
                  </span>
                )}
                <span className="timeline-chip timeline-chip--neutral">
                  {milestone.milestone_type}
                </span>
                {milestone.round_number > 0 && (
                  <span className="timeline-chip timeline-chip--subtle">
                    R{milestone.round_number}
                  </span>
                )}
                {diffStats && (
                  <span className="timeline-chip timeline-chip--subtle">
                    +{diffStats.additions} / -{diffStats.deletions} / {diffStats.files}
                  </span>
                )}
              </div>
            </div>
          </div>
          <div className="milestone-card-expand-indicator">
            <i className={`bi ${isExpanded ? 'bi-chevron-up' : 'bi-chevron-down'}`}></i>
          </div>
        </button>

        {isExpanded && (
          <div className="milestone-card-details">
            {(milestone.result_summary || milestone.description) && (
              <section className="milestone-detail-section">
                <div className="milestone-detail-label">{t('autoSummarySection', language)}</div>
                <div className="milestone-detail-content">
                  {milestone.result_summary && (
                    <p className="milestone-detail-body">{milestone.result_summary}</p>
                  )}
                  {milestone.description && (
                    <p className="milestone-detail-note">{milestone.description}</p>
                  )}
                </div>
              </section>
            )}

            {milestone.plan_content && (
              <TimelineTextSection
                title={t('autoOutputSection', language)}
                content={milestone.plan_content}
                language={language}
              />
            )}

            {milestone.review_content && (
              <TimelineTextSection
                title={t('autoReviewSection', language)}
                content={milestone.review_content}
                language={language}
              />
            )}

            {canViewDiff && (
              <section className="milestone-detail-section">
                <div className="milestone-detail-label">{t('autoCodeChanges', language)}</div>
                <div className="milestone-detail-content">
                  <div className="milestone-detail-list">
                    <div className="milestone-detail-inline">
                      <span className="milestone-detail-key">{t('autoCommits', language)}</span>
                      <code className="timeline-inline-code">{milestone.commit_shas}</code>
                    </div>
                    {diffStats && (
                      <div className="milestone-detail-inline">
                        <span className="milestone-detail-key">{t('autoViewChanges', language)}</span>
                        <span>
                          +{diffStats.additions} / -{diffStats.deletions} / {diffStats.files}{' '}
                          files
                        </span>
                      </div>
                    )}
                  </div>
                  <div className="milestone-inline-actions">
                    <Button
                      size="sm"
                      variant="outline-dark"
                      onClick={() => setViewingDiff(milestone.milestone_id)}
                    >
                      <i className="bi bi-file-diff me-1"></i>
                      {t('autoViewChanges', language)}
                    </Button>
                  </div>
                </div>
              </section>
            )}

            {(milestone.session_id || milestoneActivities.length > 0) && (
              <section className="milestone-detail-section">
                <div className="milestone-detail-label">{t('autoSessionTrace', language)}</div>
                <div className="milestone-detail-content">
                  {milestone.session_id && (
                    <div className="milestone-inline-actions">
                      <button
                        type="button"
                        className="timeline-inline-link"
                        onClick={() =>
                          setViewingSession({
                            milestoneId: milestone.milestone_id,
                            sessionId: milestone.session_id,
                          })
                        }
                      >
                        <i className="bi bi-chat-square-text me-1"></i>
                        {t('autoViewSession', language)} <code>{milestone.session_id.slice(0, 8)}</code>
                      </button>
                    </div>
                  )}

                  {milestone.status === 'in_progress' && milestoneActivities.length > 0 && (
                    <div className="milestone-activity-panel">
                      <div className="milestone-activity-panel__title">
                        <span className="timeline-live-dot"></span>
                        {t('autoActivity', language)}
                      </div>
                      <div className="milestone-activity-list">
                        {milestoneActivities.slice(-15).map((activity, index) => (
                          <div key={index} className="milestone-activity-item">
                            {activity.type === 'tool_use' && (
                              <>
                                <i className="bi bi-tools"></i>
                                <span>
                                  <strong>{activity.tool_name}</strong>
                                  {activity.tool_input && (
                                    <span className="milestone-activity-muted">
                                      {truncateText(activity.tool_input, 90)}
                                    </span>
                                  )}
                                </span>
                              </>
                            )}
                            {activity.type === 'assistant' && (
                              <>
                                <i className="bi bi-chat-text"></i>
                                <span>{truncateText(activity.text, 110)}</span>
                              </>
                            )}
                            {activity.type === 'usage' && (
                              <>
                                <i className="bi bi-lightning"></i>
                                <span>{formatTokens(activity.total_tokens ?? 0)}</span>
                              </>
                            )}
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              </section>
            )}

            {milestone.error_message && (
              <section className="milestone-detail-section milestone-detail-section--error">
                <div className="milestone-detail-label">{t('error', language)}</div>
                <div className="milestone-detail-content">
                  <p className="milestone-error-text">{milestone.error_message}</p>
                </div>
              </section>
            )}

            {showForkCancel && (
              <section className="milestone-detail-section">
                <div className="milestone-detail-label">{t('autoActionsSection', language)}</div>
                <div className="milestone-detail-content">
                  <div className="milestone-inline-actions">
                    {(milestone.status === 'completed' || milestone.status === 'in_progress') && (
                      <Button
                        size="sm"
                        variant="outline-info"
                        onClick={() => setShowForkModal(milestone.milestone_id)}
                      >
                        <i className="bi bi-diagram-3 me-1"></i>
                        {t('autoForkFromHere', language)}
                      </Button>
                    )}
                    {milestone.status !== 'cancelled' && (
                      <Button
                        size="sm"
                        variant="outline-secondary"
                        onClick={() => setShowCancelModal(milestone.milestone_id)}
                      >
                        <i className="bi bi-x-circle me-1"></i>
                        {t('autoCancelRound', language)}
                      </Button>
                    )}
                  </div>
                </div>
              </section>
            )}
          </div>
        )}
      </article>
    );
  };

  const renderSharedSection = (sharedMilestones: WorkflowMilestone[]) => {
    const grouped = sharedMilestones.reduce<Record<number, WorkflowMilestone[]>>((acc, milestone) => {
      const round = milestone.dev_round || 1;
      if (!acc[round]) acc[round] = [];
      acc[round].push(milestone);
      return acc;
    }, {});

    const rounds = Object.keys(grouped)
      .map(Number)
      .sort((a, b) => a - b);

    return rounds.map((round) => (
      <section key={round} className="timeline-round timeline-round--shared">
        <div className="timeline-round-title">
          <div className="timeline-round-heading">
            <i className="bi bi-arrow-repeat"></i>
            <span>
              {t('autoDevRoundLabel', language)} {round}
            </span>
          </div>
        </div>
        <div className="milestone-stack">
          {grouped[round].map((milestone) =>
            renderMilestoneCard(milestone, { showForkCancel: false })
          )}
        </div>
      </section>
    ));
  };

  const renderBranchMilestones = (branch: BranchData) => {
    if (branch.isLoading) {
      return (
        <div className="timeline-branch-empty">
          <Loading />
        </div>
      );
    }

    if (branch.milestones.length === 0) {
      return (
        <div className="timeline-branch-empty">
          <i className="bi bi-hourglass-split"></i>
          <span>{t('autoStatusPending', language)}...</span>
        </div>
      );
    }

    const grouped = branch.milestones.reduce<Record<number, WorkflowMilestone[]>>((acc, milestone) => {
      const round = milestone.dev_round || 1;
      if (!acc[round]) acc[round] = [];
      acc[round].push(milestone);
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
        <div className="milestone-stack">
          {grouped[round].map((milestone) => renderMilestoneCard(milestone, { compact: true }))}
        </div>
      </section>
    ));
  };

  if (isLoading) {
    return (
      <div className="timeline-loading">
        <Loading />
      </div>
    );
  }

  const headerTitle = workflow.title || workflow.requirements_text?.slice(0, 80) || 'Workflow';
  const requirementArtifact = getRequirementArtifact(workflow);
  const statusBannerTone =
    workflow.error_message || workflow.status === 'failed'
      ? 'error'
      : workflow.status === 'paused' ||
          workflow.status === 'waiting' ||
          workflow.status === 'planning_timeout'
        ? 'warning'
        : 'info';

  const showStateBanner =
    !!workflow.error_message ||
    workflow.status === 'planning_timeout' ||
    workflow.status === 'paused' ||
    workflow.status === 'waiting';

  const stateBannerMessage = workflow.error_message
    ? workflow.error_message
    : workflow.status === 'planning_timeout'
      ? t('autoBannerPlanningTimeout', language)
      : workflow.status === 'paused'
        ? t('autoBannerPaused', language)
        : workflow.status === 'waiting'
          ? t('autoBannerWaiting', language)
          : '';

  return (
    <div className="timeline-shell d-flex flex-column h-100">
      <div className="timeline-header">
        <div className="timeline-header-summary">
          <div className="timeline-header-title-block">
            <div className="timeline-header-title-row">
              {isForkChild && <i className="bi bi-diagram-3 timeline-header-fork-icon"></i>}
              <h5 className="timeline-header-title">{headerTitle}</h5>
            </div>
            <div className="timeline-header-badges">
              <Badge variant={workflowStatusMeta.variant}>
                <i className={`bi ${workflowStatusMeta.icon} me-1`}></i>
                {workflowStatusLabel}
              </Badge>
              {workflow.dev_round > 0 && (
                <span className="timeline-chip timeline-chip--subtle">
                  {t('autoDevRoundLabel', language)} {workflow.dev_round}
                </span>
              )}
              {workflow.github_issue_number && (
                <a
                  href={workflow.requirements_issue_url || '#'}
                  target={workflow.requirements_issue_url ? '_blank' : undefined}
                  rel={workflow.requirements_issue_url ? 'noopener noreferrer' : undefined}
                  className="timeline-pill-link"
                  onClick={(event) => {
                    if (!workflow.requirements_issue_url) event.preventDefault();
                  }}
                >
                  <i className="bi bi-card-text"></i>
                  <span>
                    {t('autoIssueBadge', language)} #{workflow.github_issue_number}
                  </span>
                </a>
              )}
              {workflow.github_pr_url && workflow.github_pr_number && (
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
              )}
              {isForkParent && (
                <span className="timeline-chip timeline-chip--info">
                  <i className="bi bi-diagram-3 me-1"></i>
                  {t('autoForkedWorkflows', language)} ({forks.length})
                </span>
              )}
              {isForkChild && parentWorkflow && (
                <span className="timeline-chip timeline-chip--info">
                  <i className="bi bi-arrow-up-right-circle me-1"></i>
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
            </div>
          </div>

          <div className="timeline-header-actions">
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
                  {t('autoExtendPlanning', language)} (+10m)
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
          </div>
        </div>

        <div className="timeline-meta-grid">
          <div className="timeline-meta-item">
            <span className="timeline-meta-item__label">{t('status', language)}</span>
            <span className="timeline-meta-item__value">{currentPhaseLabel}</span>
          </div>
          {workflow.cli_tool && (
            <div className="timeline-meta-item">
              <span className="timeline-meta-item__label">{t('autoAgentTool', language)}</span>
              <span className="timeline-meta-item__value">{formatToolName(workflow.cli_tool)}</span>
            </div>
          )}
          {workflow.model && (
            <div className="timeline-meta-item">
              <span className="timeline-meta-item__label">{t('autoModel', language)}</span>
              <span className="timeline-meta-item__value">{workflow.model}</span>
            </div>
          )}
          {workflow.created_at && (
            <div className="timeline-meta-item">
              <span className="timeline-meta-item__label">{t('autoStartedAt', language)}</span>
              <span className="timeline-meta-item__value">{formatDateTime(workflow.created_at)}</span>
            </div>
          )}
          {workflowDuration && (
            <div className="timeline-meta-item">
              <span className="timeline-meta-item__label">{t('autoDuration', language)}</span>
              <span className="timeline-meta-item__value">{workflowDuration}</span>
            </div>
          )}
          <div className="timeline-meta-item">
            <span className="timeline-meta-item__label">{t('totalRequests', language)}</span>
            <span className="timeline-meta-item__value">{workflow.total_requests}</span>
          </div>
          <div className="timeline-meta-item">
            <span className="timeline-meta-item__label">{t('autoTokenUsage', language)}</span>
            <span className="timeline-meta-item__value">{formatTokens(workflow.total_tokens)}</span>
          </div>
        </div>

        <div className="timeline-outputs">
          <div className="timeline-output-label">{t('autoTimelineOutputs', language)}</div>
          <div className="timeline-output-links">
            <WorkflowOutputButton
              icon="bi-file-earmark-text"
              label={t('autoOriginalDefinition', language)}
              disabled={!requirementArtifact}
              onClick={
                requirementArtifact
                  ? () => openArtifact(t('autoOriginalDefinition', language), requirementArtifact)
                  : undefined
              }
            />
            <WorkflowOutputButton
              icon="bi-clipboard-check"
              label={t('autoFinalPlan', language)}
              disabled={!latestPlanMilestone?.plan_content}
              onClick={
                latestPlanMilestone?.plan_content
                  ? () =>
                      openArtifact(
                        t('autoFinalPlan', language),
                        latestPlanMilestone.plan_content
                      )
                  : undefined
              }
            />
            <WorkflowOutputButton
              icon="bi-chat-left-text"
              label={t('autoReviewSummaryLabel', language)}
              disabled={!latestReviewMilestone?.review_content}
              onClick={
                latestReviewMilestone?.review_content
                  ? () =>
                      openArtifact(
                        t('autoReviewSummaryLabel', language),
                        latestReviewMilestone.review_content
                      )
                  : undefined
              }
            />
            <WorkflowOutputButton
              icon="bi-file-diff"
              label={t('autoLatestCodeChanges', language)}
              disabled={!latestChangeMilestone}
              onClick={
                latestChangeMilestone
                  ? () => setViewingDiff(latestChangeMilestone.milestone_id)
                  : undefined
              }
            />
          </div>
        </div>

        {showStateBanner && (
          <div className={cn('timeline-state-banner', `timeline-state-banner--${statusBannerTone}`)}>
            <div className="timeline-state-banner__copy">
              <div className="timeline-state-banner__title">{workflowStatusLabel}</div>
              {stateBannerMessage && (
                <div className="timeline-state-banner__message">{stateBannerMessage}</div>
              )}
            </div>
            <div className="timeline-state-banner__actions">
              {latestErrorMilestone && (
                <button
                  type="button"
                  className="timeline-inline-link"
                  onClick={() => setExpandedMilestone(latestErrorMilestone.milestone_id)}
                >
                  {t('autoOpenLatestMilestone', language)}
                </button>
              )}
              {latestSessionMilestone?.session_id && (
                <button
                  type="button"
                  className="timeline-inline-link"
                  onClick={() =>
                    setViewingSession({
                      milestoneId: latestSessionMilestone.milestone_id,
                      sessionId: latestSessionMilestone.session_id,
                    })
                  }
                >
                  {t('autoViewSession', language)}
                </button>
              )}
              {latestChangeMilestone && (
                <button
                  type="button"
                  className="timeline-inline-link"
                  onClick={() => setViewingDiff(latestChangeMilestone.milestone_id)}
                >
                  {t('autoViewChanges', language)}
                </button>
              )}
            </div>
          </div>
        )}
      </div>

      <div className="timeline-content flex-grow-1 overflow-auto">
        {forkViz ? (
          <div className="timeline-fork-layout">
            {forkViz.sharedMilestones.length > 0 && (
              <section className="fork-shared-section">
                <div className="timeline-section-title">
                  <i className="bi bi-clock-history"></i>
                  <span>{t('autoSharedHistory', language)}</span>
                </div>
                {renderSharedSection(forkViz.sharedMilestones)}
              </section>
            )}

            <ForkConnector feedback={forkViz.feedback} branchCount={forkViz.branches.length} />

            <div className="timeline-fork-branches" ref={parallelContainerRef}>
              {forkViz.branches.map((branch, index) => (
                <React.Fragment key={branch.id}>
                  <div
                    className="timeline-fork-branch-slot"
                    style={{
                      width:
                        forkViz.branches.length === 1
                          ? '100%'
                          : forkViz.branches.length === 2
                            ? `${index === 0 ? leftWidth : 100 - leftWidth}%`
                            : `${100 / forkViz.branches.length}%`,
                      minWidth: '180px',
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

                  {index < forkViz.branches.length - 1 && forkViz.branches.length === 2 && (
                    <div
                      className="timeline-resize-handle"
                      onMouseDown={handleResizeStart}
                      title={t('autoDragToResize', language)}
                    />
                  )}
                </React.Fragment>
              ))}
            </div>
          </div>
        ) : sortedRounds.length === 0 ? (
          <div className="timeline-empty-state">
            <i className="bi bi-hourglass-split"></i>
            <p>{t('autoStatusPreparing', language)}...</p>
          </div>
        ) : (
          <div className="timeline-rounds">
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

                <div className="milestone-stack">
                  {groupedMilestones[round].map((milestone) => renderMilestoneCard(milestone))}
                </div>
              </section>
            ))}
          </div>
        )}
      </div>

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
          <div className="timeline-modal-stack">
            <div className="timeline-modal-meta">
              <strong>{t('status', language)}:</strong>{' '}
              <Badge variant={session.status === 'completed' ? 'success' : 'primary'}>
                {session.status}
              </Badge>
            </div>
            {Array.isArray(session.messages) ? (
              session.messages.map((message, index) => (
                <div key={index} className="timeline-session-message">
                  <Badge
                    variant={
                      message.role === 'assistant'
                        ? 'primary'
                        : message.role === 'user'
                          ? 'success'
                          : 'secondary'
                    }
                  >
                    {message.role}
                  </Badge>
                  {typeof message.content === 'string' && (
                    <pre className="timeline-code-block timeline-code-block--light">
                      {message.content.slice(0, 2000)}
                    </pre>
                  )}
                </div>
              ))
            ) : (
              <p className="text-muted mb-0">{t('autoNoMessagesAvailable', language)}</p>
            )}
          </div>
        ) : (
          <p className="text-muted mb-0">{t('autoNoSessionData', language)}</p>
        )}
      </Modal>

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
              type="button"
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

      {viewingDiff && (
        <Modal
          isOpen={true}
          onClose={() => setViewingDiff(null)}
          title={t('autoCodeChanges', language)}
          size="xl"
        >
          <div className="timeline-modal-scroll">
            {diffLoading ? (
              <Loading />
            ) : diffData?.diff ? (
              <pre className="timeline-code-block timeline-code-block--dark">
                {diffData.diff.length > 50000
                  ? `${diffData.diff.slice(0, 50000)}\n\n${t('autoDiffTruncated', language)}`
                  : diffData.diff}
              </pre>
            ) : (
              <p className="text-muted mb-0">{t('autoNoDiff', language)}</p>
            )}
          </div>
        </Modal>
      )}

      <Modal
        isOpen={!!viewingArtifact}
        onClose={() => setViewingArtifact(null)}
        title={viewingArtifact?.title ?? ''}
        size="lg"
      >
        {viewingArtifact && (
          <div className="timeline-modal-scroll">
            <pre className="timeline-code-block timeline-code-block--light">
              {viewingArtifact.content}
            </pre>
          </div>
        )}
      </Modal>

      {showCancelModal && (
        <CancelRoundModal
          isOpen={true}
          onClose={() => setShowCancelModal(null)}
          workflowId={workflow.workflow_id}
          milestoneId={showCancelModal}
          milestoneTitle={milestones.find((milestone) => milestone.milestone_id === showCancelModal)?.title ?? ''}
        />
      )}

      {showForkModal && (
        <ForkFromHereModal
          isOpen={true}
          onClose={() => setShowForkModal(null)}
          workflowId={workflow.workflow_id}
          milestoneId={showForkModal}
          milestoneTitle={milestones.find((milestone) => milestone.milestone_id === showForkModal)?.title ?? ''}
        />
      )}
    </div>
  );
};
