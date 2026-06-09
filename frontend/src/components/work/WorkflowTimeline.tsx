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

import React, { useState, useMemo, useCallback, useRef } from 'react';
import { useQueries } from '@tanstack/react-query';
import { useLanguage } from '@/store';
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
  useWorkflowForks,
  useWorkflow,
} from '@/hooks/useAutonomous';
import type { MilestoneSession } from '@/hooks/useAutonomous';
import { autonomousApi } from '@/api/autonomous';
import CancelRoundModal from './CancelRoundModal';
import ForkFromHereModal from './ForkFromHereModal';
import { ForkConnector, BranchColumn } from './ForkConnector';
import { ACTIVE_WORKFLOW_STATUSES } from './AutonomousWorkflowList';
import { formatTokens } from '@/utils';
import type { AutonomousWorkflow, WorkflowMilestone } from '@/api/autonomous';

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
  const [expandedMilestone, setExpandedMilestone] = useState<string | null>(null);
  const [showStopConfirm, setShowStopConfirm] = useState(false);
  const [viewingSession, setViewingSession] = useState<{
    milestoneId: string;
    sessionId: string;
  } | null>(null);
  const [showBranchSelector, setShowBranchSelector] = useState(false);
  const [viewingDiff, setViewingDiff] = useState<string | null>(null);

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

  // Real-time agent activity (only when workflow is active)
  const isWorkflowActive = ACTIVE_WORKFLOW_STATUSES.includes(workflow.status);
  const activities = useWorkflowActivity(workflow.workflow_id, isWorkflowActive);

  const milestones = timelineData?.milestones ?? [];

  const isActive = ACTIVE_WORKFLOW_STATUSES.includes(workflow.status);
  const isPaused = workflow.status === 'paused';
  const isWaiting = workflow.current_phase === 'wait';

  // Modal state for cancel/fork
  const [showCancelModal, setShowCancelModal] = useState<string | null>(null);
  const [showForkModal, setShowForkModal] = useState<string | null>(null);

  // ── Fork Detection ────────────────────────────────────────────────

  const isForkChild = !!workflow.parent_workflow_id;
  const { data: forksData } = useWorkflowForks(workflow.workflow_id, !isForkChild);
  const forks = forksData?.forks ?? [];
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
  const parentMilestones = parentTimelineData?.milestones ?? [];

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

  const toggleExpand = (milestoneId: string) => {
    setExpandedMilestone((prev) => (prev === milestoneId ? null : milestoneId));
  };

  const parseDiffStats = (
    statsJson: string
  ): { additions: number; deletions: number; files: number; commits: number } | null => {
    try {
      return statsJson ? JSON.parse(statsJson) : null;
    } catch {
      return null;
    }
  };

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

    return (
      <div key={milestone.milestone_id} className="mb-2">
        <div
          className={`d-flex align-items-start p-2 rounded ${isExpanded ? 'bg-light' : ''}`}
          style={{ cursor: 'pointer' }}
          onClick={() => toggleExpand(milestone.milestone_id)}
        >
          <div className="me-2 mt-1">
            <i className={`bi ${statusIcon}`} style={{ fontSize: compact ? '0.85rem' : '1rem' }} />
          </div>
          <div className="flex-grow-1 min-width-0">
            <div className="d-flex align-items-center gap-1 flex-wrap">
              <i className={`bi ${display.icon} text-${display.color}`}></i>
              <span className="fw-semibold" style={{ fontSize: compact ? '0.8rem' : '0.875rem' }}>
                {milestone.title || milestone.milestone_type}
              </span>
              {milestone.round_number > 0 && (
                <Badge variant="light" style={{ fontSize: '0.65rem' }}>
                  R{milestone.round_number}
                </Badge>
              )}
            </div>
            {!compact && (
              <div className="text-muted mt-1" style={{ fontSize: '0.75rem' }}>
                {milestone.result_summary && (
                  <span className="me-2">{milestone.result_summary.slice(0, 100)}</span>
                )}
                {diffStats && (
                  <span className="me-2">
                    +{diffStats.additions}/-{diffStats.deletions} ({diffStats.files} files)
                  </span>
                )}
                {milestone.started_at && (
                  <span>
                    {new Date(milestone.started_at).toLocaleTimeString()}
                    {milestone.completed_at && (
                      <> → {new Date(milestone.completed_at).toLocaleTimeString()}</>
                    )}
                  </span>
                )}
              </div>
            )}
          </div>
          <i className={`bi ${isExpanded ? 'bi-chevron-up' : 'bi-chevron-down'} text-muted`}></i>
        </div>

        {isExpanded && (
          <div
            className="ms-4 p-2 border-start border-3"
            style={{ borderColor: `var(--bs-${display.color})` }}
          >
            {milestone.plan_content && (
              <div className="mb-2">
                <strong>{t('autoPhasePlanning', language)}:</strong>
                <pre
                  className="bg-dark text-light p-2 rounded mt-1"
                  style={{ fontSize: '0.8rem', maxHeight: '300px', overflow: 'auto' }}
                >
                  {milestone.plan_content}
                </pre>
              </div>
            )}
            {milestone.review_content && (
              <div className="mb-2">
                <strong>{t('autoStatusPRReview', language)}:</strong>
                <pre
                  className="bg-dark text-light p-2 rounded mt-1"
                  style={{ fontSize: '0.8rem', maxHeight: '300px', overflow: 'auto' }}
                >
                  {milestone.review_content}
                </pre>
              </div>
            )}
            {milestone.description && (
              <p className="text-muted mb-2" style={{ fontSize: '0.85rem' }}>
                {milestone.description}
              </p>
            )}
            {milestone.commit_shas && (
              <div className="mb-2">
                <strong>{t('autoCommits', language)}:</strong>
                <code className="d-block mt-1" style={{ fontSize: '0.75rem' }}>
                  {milestone.commit_shas}
                </code>
                <div onClick={(e) => e.stopPropagation()}>
                  <Button
                    size="sm"
                    variant="outline-dark"
                    className="mt-1"
                    onClick={() => setViewingDiff(milestone.milestone_id)}
                  >
                    <i className="bi bi-file-diff me-1"></i>
                    {t('autoViewChanges', language)}
                  </Button>
                </div>
              </div>
            )}
            {milestone.session_id && (
              <small>
                <i className="bi bi-chat-square-text me-1"></i>
                <a
                  href="#"
                  className="text-decoration-none"
                  onClick={(e) => {
                    e.stopPropagation();
                    setViewingSession({
                      milestoneId: milestone.milestone_id,
                      sessionId: milestone.session_id,
                    });
                  }}
                >
                  {t('autoViewSession', language)}: <code>{milestone.session_id.slice(0, 8)}</code>
                </a>
              </small>
            )}
            {showForkCancel && (
              <div className="d-flex gap-2 mt-2" onClick={(e) => e.stopPropagation()}>
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
            )}
            {milestone.error_message && (
              <div
                className="alert alert-danger py-1 px-2 mt-2 mb-0"
                style={{ fontSize: '0.8rem' }}
              >
                {milestone.error_message}
              </div>
            )}
            {/* Real-time agent activity for in_progress milestones */}
            {milestone.status === 'in_progress' &&
              (() => {
                const milestoneActivities = milestone.session_id
                  ? activities.filter((a) => a.session_id === milestone.session_id)
                  : activities;
                return (
                  milestoneActivities.length > 0 && (
                    <div
                      className="mt-2 p-2 rounded"
                      style={{
                        backgroundColor: 'var(--bs-gray-100)',
                        maxHeight: '200px',
                        overflowY: 'auto',
                        fontSize: '0.75rem',
                        fontFamily: 'monospace',
                      }}
                    >
                      <div className="d-flex align-items-center gap-1 mb-1">
                        <span
                          className="spinner-border spinner-border-sm text-primary"
                          style={{ width: '0.8rem', height: '0.8rem' }}
                        ></span>
                        <strong className="text-primary">Agent Activity</strong>
                      </div>
                      {milestoneActivities.slice(-15).map((act, idx) => (
                        <div key={idx} className="text-muted" style={{ lineHeight: '1.4' }}>
                          {act.type === 'tool_use' && (
                            <span>
                              <i className="bi bi-tools me-1 text-warning"></i>
                              <strong>{act.tool_name}</strong>
                              {act.tool_input && (
                                <span className="ms-1" style={{ opacity: 0.7 }}>
                                  {act.tool_input.length > 60
                                    ? act.tool_input.slice(0, 60) + '...'
                                    : act.tool_input}
                                </span>
                              )}
                            </span>
                          )}
                          {act.type === 'assistant' && (
                            <span>
                              <i className="bi bi-chat-text me-1 text-info"></i>
                              {act.text && act.text.length > 80
                                ? act.text.slice(0, 80) + '...'
                                : act.text}
                            </span>
                          )}
                          {act.type === 'usage' && (
                            <span>
                              <i className="bi bi-lightning me-1"></i>
                              Token: {formatTokens(act.total_tokens ?? 0)}
                            </span>
                          )}
                        </div>
                      ))}
                    </div>
                  )
                );
              })()}
          </div>
        )}
      </div>
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
      <div key={round} className="mb-3">
        <h6 className="text-muted border-bottom pb-2 mb-2" style={{ fontSize: '0.8rem' }}>
          <i className="bi bi-arrow-repeat me-1"></i>
          {t('autoDevRoundLabel', language)} {round}
        </h6>
        <div className="ps-3">
          {grouped[round].map((ms) => renderMilestoneCard(ms, { showForkCancel: false }))}
        </div>
      </div>
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
      <div key={round} className="mb-2">
        {rounds.length > 1 && (
          <div className="text-muted mb-1" style={{ fontSize: '0.7rem' }}>
            {t('autoDevRoundLabel', language)} {round}
          </div>
        )}
        {grouped[round].map((ms) => renderMilestoneCard(ms, { compact: true }))}
      </div>
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
    <div className="d-flex flex-column h-100">
      {/* Header / Controls */}
      <div className="border-bottom p-3">
        <div className="d-flex align-items-center justify-content-between mb-2">
          <div>
            <h5 className="mb-1">
              {isForkChild && <i className="bi bi-diagram-3 me-1 text-info"></i>}
              {workflow.title || workflow.requirements_text?.slice(0, 80) || 'Workflow'}
            </h5>
            <div className="d-flex gap-2 align-items-center flex-wrap">
              <Badge variant={isActive ? 'primary' : isPaused ? 'warning' : 'secondary'}>
                {workflow.status}
              </Badge>
              {isForkChild && parentWorkflow && (
                <Badge variant="info">
                  <i className="bi bi-diagram-3 me-1"></i>
                  {t('autoForkedFrom', language)}
                  {onNavigateToWorkflow ? (
                    <a
                      href="#"
                      className="text-white text-decoration-none ms-1"
                      onClick={(e) => {
                        e.preventDefault();
                        onNavigateToWorkflow(parentWorkflow.workflow_id);
                      }}
                    >
                      {parentWorkflow.title?.slice(0, 30) || parentWorkflow.workflow_id.slice(0, 8)}
                    </a>
                  ) : (
                    <span className="ms-1">
                      {parentWorkflow.title?.slice(0, 30) || parentWorkflow.workflow_id.slice(0, 8)}
                    </span>
                  )}
                </Badge>
              )}
              {isForkParent && (
                <Badge variant="info">
                  <i className="bi bi-diagram-3 me-1"></i>
                  {t('autoForkedWorkflows', language)} ({forks.length})
                </Badge>
              )}
              {workflow.github_pr_url && (
                <a
                  href={workflow.github_pr_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-decoration-none"
                >
                  <Badge variant="success">
                    <i className="bi bi-git-pull-request me-1"></i>
                    {t('autoPrBadge', language)}
                    {workflow.github_pr_number}
                  </Badge>
                </a>
              )}
              {workflow.requirements_issue_url && (
                <a
                  href={workflow.requirements_issue_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-decoration-none"
                >
                  <Badge variant="light">
                    <i className="bi bi-card-text me-1"></i>
                    {t('autoIssueBadge', language)}
                  </Badge>
                </a>
              )}
              {workflow.cli_tool && (
                <small className="text-muted">
                  <i className="bi bi-tools me-1"></i>
                  {workflow.cli_tool}
                </small>
              )}
              {workflow.model && (
                <small className="text-muted">
                  <i className="bi bi-cpu me-1"></i>
                  {workflow.model}
                </small>
              )}
            </div>
          </div>
          <div className="d-flex gap-2">
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

        {/* Token Usage */}
        <div className="d-flex gap-3">
          <small className="text-muted">
            <i className="bi bi-lightning me-1"></i>
            {t('autoTokenUsage', language)}: {formatTokens(workflow.total_tokens)}
          </small>
          <small className="text-muted">
            <i className="bi bi-arrow-repeat me-1"></i>
            {workflow.total_requests} {t('totalRequests', language)}
          </small>
          {workflow.dev_round > 1 && (
            <small className="text-muted">
              <i className="bi bi-flag me-1"></i>
              {t('autoDevRoundLabel', language)} {workflow.dev_round}
            </small>
          )}
        </div>

        {/* Error message */}
        {workflow.error_message && (
          <div className="alert alert-danger py-1 px-2 mt-2 mb-0" style={{ fontSize: '0.8rem' }}>
            {workflow.error_message}
          </div>
        )}
      </div>

      {/* ── Timeline Body ─────────────────────────────────────────── */}
      <div className="flex-grow-1 overflow-auto p-3">
        {forkViz ? (
          /* ── Fork Parallel View ────────────────────────────────── */
          <>
            {/* Shared history section */}
            {forkViz.sharedMilestones.length > 0 && (
              <div className="mb-2">
                <h6 className="text-muted mb-2" style={{ fontSize: '0.8rem' }}>
                  <i className="bi bi-clock-history me-1"></i>
                  {t('autoSharedHistory', language)}
                </h6>
                {renderSharedSection(forkViz.sharedMilestones)}
              </div>
            )}

            {/* Fork connector */}
            <ForkConnector feedback={forkViz.feedback} branchCount={forkViz.branches.length} />

            {/* Parallel branch columns */}
            <div className="d-flex" ref={parallelContainerRef}>
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

                  {/* Resize handle between columns */}
                  {idx < forkViz.branches.length - 1 && (
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
          </>
        ) : (
          /* ── Normal Timeline View ──────────────────────────────── */
          <>
            {sortedRounds.length === 0 ? (
              <div className="text-center text-muted py-5">
                <i className="bi bi-hourglass-split fs-1 d-block mb-2"></i>
                <p>{t('autoStatusPreparing', language)}...</p>
              </div>
            ) : (
              sortedRounds.map((round) => (
                <div key={round} className="mb-4">
                  <h6 className="text-muted border-bottom pb-2 mb-3">
                    <i className="bi bi-arrow-repeat me-1"></i>
                    {t('autoDevRoundLabel', language)} {round}
                    {round === workflow.dev_round && isActive && (
                      <Badge variant="primary" className="ms-2">
                        {t('autoStatusDeveloping', language)}
                      </Badge>
                    )}
                  </h6>

                  <div className="ps-3">
                    {groupedMilestones[round].map((milestone) => renderMilestoneCard(milestone))}
                  </div>
                </div>
              ))
            )}
          </>
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
                    {msg.role}
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
                      {msg.content.slice(0, 2000)}
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

      {/* Diff Viewer Modal */}
      {viewingDiff && (
        <Modal
          isOpen={true}
          onClose={() => setViewingDiff(null)}
          title={t('autoCodeChanges', language)}
          size="xl"
        >
          <div style={{ maxHeight: '80vh', overflow: 'auto' }}>
            {diffLoading ? (
              <Loading />
            ) : diffData?.diff ? (
              <pre
                className="bg-dark text-light p-3 rounded"
                style={{
                  fontSize: '0.75rem',
                  maxHeight: '70vh',
                  overflow: 'auto',
                  whiteSpace: 'pre-wrap',
                }}
              >
                {diffData.diff.length > 50000
                  ? diffData.diff.slice(0, 50000) + '\n\n' + t('autoDiffTruncated', language)
                  : diffData.diff}
              </pre>
            ) : (
              <p className="text-muted">{t('autoNoDiff', language)}</p>
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
