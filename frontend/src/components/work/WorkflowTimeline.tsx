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

  const { data: timelineData, isLoading } = useWorkflowTimeline(workflow.workflow_id);
  const pauseMutation = usePauseWorkflow();
  const resumeMutation = useResumeWorkflow();
  const stopMutation = useStopWorkflow();
  const markDoneMutation = useMarkDone();
  const retryMutation = useRetryWorkflow();

  const milestones = timelineData?.milestones ?? [];

  const isActive = ACTIVE_WORKFLOW_STATUSES.includes(workflow.status);
  const isPaused = workflow.status === 'paused';
  const isWaiting = workflow.current_phase === 'wait';

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
    markDoneMutation.mutate({ workflowId: workflow.workflow_id });
  };
  const handleRetry = () => retryMutation.mutate(workflow.workflow_id);

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
                            </div>
                          )}

                          {/* Session link */}
                          {milestone.session_id && (
                            <small>
                              <i className="bi bi-chat-square-text me-1"></i>
                              {t('autoViewSession', language)}: <code>{milestone.session_id.slice(0, 8)}</code>
                            </small>
                          )}

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
    </div>
  );
};
