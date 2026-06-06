/**
 * WorkflowTimeline Component - Timeline view for an autonomous development workflow
 *
 * Features:
 * - Vertical timeline grouped by dev_round
 * - Milestone cards with status indicators
 * - Controls bar (pause/resume/stop/complete)
 * - Token usage display
 * - Expandable milestone details
 */

import React, { useState } from 'react';
import { useLanguage } from '@/store';
import { t } from '@/i18n';
import { Button, Badge, Loading } from '@/components/common';
import {
  useWorkflowTimeline,
  usePauseWorkflow,
  useResumeWorkflow,
  useStopWorkflow,
  useMarkDone,
  useRetryWorkflow,
  useCancelMilestone,
  useForkMilestone,
  useMilestoneSession,
  useMilestoneDiff,
} from '@/hooks/useAutonomous';
import { ACTIVE_WORKFLOW_STATUSES } from './AutonomousWorkflowList';
import { formatTokens } from '@/utils';
import type { AutonomousWorkflow, WorkflowMilestone } from '@/api/autonomous';

interface WorkflowTimelineProps {
  workflow: AutonomousWorkflow;
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

export const WorkflowTimeline: React.FC<WorkflowTimelineProps> = ({ workflow }) => {
  const language = useLanguage();
  const [expandedMilestone, setExpandedMilestone] = useState<string | null>(null);
  const [showStopConfirm, setShowStopConfirm] = useState(false);
  const [viewingSession, setViewingSession] = useState<{ milestoneId: string; sessionId: string } | null>(null);
  const [showBranchSelector, setShowBranchSelector] = useState(false);
  const [forkBranchName] = useState('');
  const [viewingDiff, setViewingDiff] = useState<string | null>(null); // milestoneId

  const { data: timelineData, isLoading } = useWorkflowTimeline(workflow.workflow_id);
  const pauseMutation = usePauseWorkflow();
  const resumeMutation = useResumeWorkflow();
  const stopMutation = useStopWorkflow();
  const markDoneMutation = useMarkDone();
  const retryMutation = useRetryWorkflow();
  const cancelMilestoneMutation = useCancelMilestone();
  const forkMilestoneMutation = useForkMilestone();

  // Session detail query (only fetches when viewingSession is set)
  const { data: sessionData, isLoading: sessionLoading } = useMilestoneSession(
    workflow.workflow_id,
    viewingSession?.milestoneId ?? '',
    !!viewingSession,
  );

  // Diff query (only fetches when viewingDiff is set)
  const { data: diffData, isLoading: diffLoading } = useMilestoneDiff(
    workflow.workflow_id,
    viewingDiff ?? '',
    !!viewingDiff,
  );

  const milestones = timelineData?.milestones ?? [];

  const isActive = ACTIVE_WORKFLOW_STATUSES.includes(workflow.status);
  const isPaused = workflow.status === 'paused';
  const isWaiting = workflow.current_phase === 'wait';

  // Collect available branches for merge selection
  const availableBranches = React.useMemo(() => {
    const branches = [workflow.branch_name].filter(Boolean);
    milestones.forEach(ms => {
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

  const sortedRounds = Object.keys(groupedMilestones).map(Number).sort((a, b) => a - b);

  const handlePause = () => pauseMutation.mutate(workflow.workflow_id);
  const handleResume = () => resumeMutation.mutate(workflow.workflow_id);
  const handleStopConfirm = () => {
    stopMutation.mutate(workflow.workflow_id);
    setShowStopConfirm(false);
  };
  const handleMarkDone = () => {
    // If only one branch, skip selector
    if (availableBranches.length <= 1) {
      markDoneMutation.mutate({ workflowId: workflow.workflow_id, selectedBranch: availableBranches[0] });
    } else {
      setShowBranchSelector(true);
    }
  };
  const handleBranchSelect = (branch: string) => {
    markDoneMutation.mutate({ workflowId: workflow.workflow_id, selectedBranch: branch });
    setShowBranchSelector(false);
  };
  const handleRetry = () => retryMutation.mutate(workflow.workflow_id);

  const handleCancelMilestone = (milestoneId: string) => {
    cancelMilestoneMutation.mutate({ workflowId: workflow.workflow_id, milestoneId });
  };
  const handleForkMilestone = (milestoneId: string) => {
    const branch = forkBranchName || `fork/from-${milestoneId.slice(0, 8)}`;
    forkMilestoneMutation.mutate({ workflowId: workflow.workflow_id, milestoneId, branchName: branch });
  };

  const toggleExpand = (milestoneId: string) => {
    setExpandedMilestone(prev => prev === milestoneId ? null : milestoneId);
  };

  // Parse diff stats JSON
  const parseDiffStats = (statsJson: string): { additions: number; deletions: number; files: number; commits: number } | null => {
    try {
      return statsJson ? JSON.parse(statsJson) : null;
    } catch {
      return null;
    }
  };

  if (isLoading) {
    return (
      <div className="d-flex align-items-center justify-content-center h-100">
        <Loading />
      </div>
    );
  }

  return (
    <div className="d-flex flex-column h-100">
      {/* Header / Controls */}
      <div className="border-bottom p-3">
        <div className="d-flex align-items-center justify-content-between mb-2">
          <div>
            <h5 className="mb-1">
              {workflow.title || workflow.requirements_text?.slice(0, 80) || `Workflow`}
            </h5>
            <div className="d-flex gap-2 align-items-center">
              <Badge variant={isActive ? 'primary' : isPaused ? 'warning' : 'secondary'}>
                {workflow.status}
              </Badge>
              {workflow.github_pr_url && (
                <a href={workflow.github_pr_url} target="_blank" rel="noopener noreferrer" className="text-decoration-none">
                  <Badge variant="success"><i className="bi bi-git-pull-request me-1"></i>PR #{workflow.github_pr_number}</Badge>
                </a>
              )}
              {workflow.requirements_issue_url && (
                <a href={workflow.requirements_issue_url} target="_blank" rel="noopener noreferrer" className="text-decoration-none">
                  <Badge variant="light"><i className="bi bi-card-text me-1"></i>Issue</Badge>
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
            {workflow.status === 'failed' && (
              <Button size="sm" variant="primary" onClick={handleRetry}>
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
              <Button size="sm" variant="success" onClick={handleMarkDone}>
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

      {/* Timeline Body */}
      <div className="flex-grow-1 overflow-auto p-3">
        {sortedRounds.length === 0 ? (
          <div className="text-center text-muted py-5">
            <i className="bi bi-hourglass-split fs-1 d-block mb-2"></i>
            <p>{t('autoStatusPreparing', language)}...</p>
          </div>
        ) : (
          sortedRounds.map((round) => (
            <div key={round} className="mb-4">
              {/* Round Header */}
              <h6 className="text-muted border-bottom pb-2 mb-3">
                <i className="bi bi-arrow-repeat me-1"></i>
                {t('autoDevRoundLabel', language)} {round}
                {round === workflow.dev_round && isActive && (
                  <Badge variant="primary" className="ms-2">
                    {t('autoStatusDeveloping', language)}
                  </Badge>
                )}
              </h6>

              {/* Milestones */}
              <div className="ps-3">
                {groupedMilestones[round].map((milestone) => {
                  const isExpanded = expandedMilestone === milestone.milestone_id;
                  const display = MILESTONE_DISPLAY[milestone.milestone_type] || { icon: 'bi-circle', color: 'secondary' };
                  const statusIcon = STATUS_ICONS[milestone.status] || STATUS_ICONS.pending;
                  const diffStats = parseDiffStats(milestone.diff_stats);

                  return (
                    <div key={milestone.milestone_id} className="mb-2">
                      {/* Milestone Card */}
                      <div
                        className={`d-flex align-items-start p-2 rounded ${isExpanded ? 'bg-light' : ''}`}
                        style={{ cursor: 'pointer' }}
                        onClick={() => toggleExpand(milestone.milestone_id)}
                      >
                        {/* Timeline dot */}
                        <div className="me-3 mt-1">
                          <i className={`bi ${statusIcon} fs-6`} />
                        </div>

                        {/* Content */}
                        <div className="flex-grow-1 min-width-0">
                          <div className="d-flex align-items-center gap-2">
                            <i className={`bi ${display.icon} text-${display.color}`}></i>
                            <span className="fw-semibold" style={{ fontSize: '0.875rem' }}>
                              {milestone.title || milestone.milestone_type}
                            </span>
                            {milestone.round_number > 0 && (
                              <Badge variant="light">
                                {t('autoRoundLabel', language)} {milestone.round_number}
                              </Badge>
                            )}
                          </div>

                          {/* Summary info */}
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
                        </div>

                        {/* Expand indicator */}
                        <i className={`bi ${isExpanded ? 'bi-chevron-up' : 'bi-chevron-down'} text-muted`}></i>
                      </div>

                      {/* Expanded Detail */}
                      {isExpanded && (
                        <div className="ms-4 p-3 border-start border-3" style={{ borderColor: `var(--bs-${display.color})` }}>
                          {/* Plan content */}
                          {milestone.plan_content && (
                            <div className="mb-2">
                              <strong>{t('autoPhasePlanning', language)}:</strong>
                              <pre className="bg-dark text-light p-2 rounded mt-1" style={{ fontSize: '0.8rem', maxHeight: '300px', overflow: 'auto' }}>
                                {milestone.plan_content}
                              </pre>
                            </div>
                          )}

                          {/* Review content */}
                          {milestone.review_content && (
                            <div className="mb-2">
                              <strong>{t('autoStatusPRReview', language)}:</strong>
                              <pre className="bg-dark text-light p-2 rounded mt-1" style={{ fontSize: '0.8rem', maxHeight: '300px', overflow: 'auto' }}>
                                {milestone.review_content}
                              </pre>
                            </div>
                          )}

                          {/* Description */}
                          {milestone.description && (
                            <p className="text-muted mb-2" style={{ fontSize: '0.85rem' }}>{milestone.description}</p>
                          )}

                          {/* Commit SHAs */}
                          {milestone.commit_shas && (
                            <div className="mb-2">
                              <strong>Commits:</strong>
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
                                View Changes
                              </Button>
                              </div>
                            </div>
                          )}

                          {/* Session link */}
                          {milestone.session_id && (
                            <small>
                              <i className="bi bi-chat-square-text me-1"></i>
                              <a
                                href="#"
                                className="text-decoration-none"
                                onClick={(e) => {
                                  e.stopPropagation();
                                  setViewingSession({ milestoneId: milestone.milestone_id, sessionId: milestone.session_id });
                                }}
                              >
                                {t('autoViewSession', language)}: <code>{milestone.session_id.slice(0, 8)}</code>
                              </a>
                            </small>
                          )}

                          {/* Milestone Actions (Fork / Cancel) */}
                          <div className="d-flex gap-2 mt-2" onClick={(e) => e.stopPropagation()}>
                            {(milestone.status === 'completed' || milestone.status === 'in_progress') && (
                              <Button
                                size="sm"
                                variant="outline-info"
                                onClick={() => handleForkMilestone(milestone.milestone_id)}
                                disabled={forkMilestoneMutation.isPending}
                              >
                                <i className="bi bi-diagram-3 me-1"></i>
                                {t('autoForkFromHere', language)}
                              </Button>
                            )}
                            {milestone.status !== 'cancelled' && (
                              <Button
                                size="sm"
                                variant="outline-secondary"
                                onClick={() => handleCancelMilestone(milestone.milestone_id)}
                                disabled={cancelMilestoneMutation.isPending}
                              >
                                <i className="bi bi-x-circle me-1"></i>
                                {t('autoCancelRound', language)}
                              </Button>
                            )}
                          </div>

                          {/* Error */}
                          {milestone.error_message && (
                            <div className="alert alert-danger py-1 px-2 mt-2 mb-0" style={{ fontSize: '0.8rem' }}>
                              {milestone.error_message}
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
          ))
        )}
      </div>

      {/* Session Detail Modal */}
      {viewingSession && (
        <div className="modal d-block" style={{ backgroundColor: 'rgba(0,0,0,0.5)' }} onClick={() => setViewingSession(null)}>
          <div className="modal-dialog modal-lg" onClick={(e) => e.stopPropagation()}>
            <div className="modal-content">
              <div className="modal-header">
                <h5 className="modal-title">
                  <i className="bi bi-chat-square-text me-2"></i>
                  {t('autoViewSession', language)}: {viewingSession.sessionId.slice(0, 8)}
                </h5>
                <button type="button" className="btn-close" onClick={() => setViewingSession(null)} />
              </div>
              <div className="modal-body" style={{ maxHeight: '70vh', overflow: 'auto' }}>
                {sessionLoading ? (
                  <Loading />
                ) : sessionData?.session ? (
                  <div>
                    <div className="mb-3">
                      <strong>Status:</strong>{' '}
                      <Badge variant={(sessionData.session as Record<string, unknown>).status === 'completed' ? 'success' : 'primary'}>
                        {String((sessionData.session as Record<string, unknown>).status)}
                      </Badge>
                    </div>
                    {Array.isArray((sessionData.session as Record<string, unknown>).messages) &&
                      ((sessionData.session as Record<string, unknown>).messages as Array<Record<string, unknown>>).map((msg, idx) => (
                        <div key={idx} className="mb-2">
                          <Badge variant={msg.role === 'assistant' ? 'primary' : msg.role === 'user' ? 'success' : 'secondary'}>
                            {String(msg.role)}
                          </Badge>
                          {typeof msg.content === 'string' ? (
                            <pre className="bg-light p-2 rounded mt-1 mb-0" style={{ fontSize: '0.8rem', maxHeight: '200px', overflow: 'auto', whiteSpace: 'pre-wrap' }}>
                              {(msg.content as string).slice(0, 2000)}
                            </pre>
                          ) : null}
                        </div>
                      ))}
                    {!Array.isArray((sessionData.session as Record<string, unknown>).messages) && (
                      <p className="text-muted">No messages available</p>
                    )}
                  </div>
                ) : (
                  <p className="text-muted">No session data available</p>
                )}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Branch Selector Modal */}
      {showBranchSelector && (
        <div className="modal d-block" style={{ backgroundColor: 'rgba(0,0,0,0.5)' }} onClick={() => setShowBranchSelector(false)}>
          <div className="modal-dialog" onClick={(e) => e.stopPropagation()}>
            <div className="modal-content">
              <div className="modal-header">
                <h5 className="modal-title">
                  <i className="bi bi-git-merge me-2"></i>
                  {t('autoSelectBranchToMerge', language)}
                </h5>
                <button type="button" className="btn-close" onClick={() => setShowBranchSelector(false)} />
              </div>
              <div className="modal-body">
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
                        <Badge variant="primary" className="ms-2">current</Badge>
                      )}
                    </button>
                  ))}
                </div>
              </div>
              <div className="modal-footer">
                <Button variant="secondary" onClick={() => setShowBranchSelector(false)}>
                  {t('cancel', language)}
                </Button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Diff Viewer Modal */}
      {viewingDiff && (
        <div className="modal d-block" style={{ backgroundColor: 'rgba(0,0,0,0.5)' }} onClick={() => setViewingDiff(null)}>
          <div className="modal-dialog modal-xl" onClick={(e) => e.stopPropagation()}>
            <div className="modal-content">
              <div className="modal-header">
                <h5 className="modal-title">
                  <i className="bi bi-file-diff me-2"></i>
                  Code Changes
                </h5>
                <button type="button" className="btn-close" onClick={() => setViewingDiff(null)} />
              </div>
              <div className="modal-body" style={{ maxHeight: '80vh', overflow: 'auto' }}>
                {diffLoading ? (
                  <Loading />
                ) : diffData?.diff ? (
                  <pre className="bg-dark text-light p-3 rounded" style={{ fontSize: '0.75rem', maxHeight: '70vh', overflow: 'auto', whiteSpace: 'pre-wrap' }}>
                    {diffData.diff.length > 50000
                      ? diffData.diff.slice(0, 50000) + '\n\n--- Diff truncated at 50K characters ---'
                      : diffData.diff}
                  </pre>
                ) : (
                  <p className="text-muted">No diff available</p>
                )}
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
