/**
 * AutonomousWorkflowList Component - List of autonomous development workflows
 *
 * Displays workflows with status filter tabs and delete capability.
 */

import React, { useState } from 'react';
import { useLanguage } from '@/store';
import { t } from '@/i18n';
import { Badge, Loading } from '@/components/common';
import { useWorkflows, useDeleteWorkflow } from '@/hooks/useAutonomous';
import type { AutonomousWorkflow } from '@/api/autonomous';

interface AutonomousWorkflowListProps {
  selectedId: string | null;
  onSelect: (workflow: AutonomousWorkflow) => void;
}

const STATUS_CONFIG: Record<string, { variant: string; icon: string; labelKey: string }> = {
  pending: { variant: 'secondary', icon: 'bi-hourglass', labelKey: 'autoStatusPending' },
  preparing: { variant: 'info', icon: 'bi-gear', labelKey: 'autoStatusPreparing' },
  planning: { variant: 'info', icon: 'bi-lightbulb', labelKey: 'autoStatusPlanning' },
  developing: { variant: 'primary', icon: 'bi-code-slash', labelKey: 'autoStatusDeveloping' },
  pr_review: { variant: 'warning', icon: 'bi-eye', labelKey: 'autoStatusPRReview' },
  reporting: { variant: 'info', icon: 'bi-file-text', labelKey: 'autoStatusReporting' },
  waiting: { variant: 'secondary', icon: 'bi-clock', labelKey: 'autoStatusWaiting' },
  merging: { variant: 'info', icon: 'bi-git-merge', labelKey: 'autoStatusMerging' },
  completed: { variant: 'success', icon: 'bi-check-circle', labelKey: 'autoStatusCompleted' },
  failed: { variant: 'danger', icon: 'bi-x-circle', labelKey: 'autoStatusFailed' },
  cancelled: { variant: 'secondary', icon: 'bi-slash-circle', labelKey: 'autoStatusCancelled' },
  paused: { variant: 'warning', icon: 'bi-pause-circle', labelKey: 'autoStatusPaused' },
  planning_timeout: { variant: 'warning', icon: 'bi-clock-history', labelKey: 'autoStatusPlanningTimeout' },
};

/** Shared active status set — used by both WorkflowList and WorkflowTimeline */
export const ACTIVE_WORKFLOW_STATUSES = [
  'pending', 'preparing', 'planning', 'developing',
  'pr_review', 'reporting', 'waiting', 'merging',
];

const STATUS_FILTER_TABS = [
  { key: '', labelKey: 'autoFilterAll' },
  { key: 'pending,preparing,planning,developing,pr_review,reporting,waiting,merging,paused,planning_timeout', labelKey: 'autoFilterActive' },
  { key: 'completed', labelKey: 'autoFilterCompleted' },
  { key: 'failed', labelKey: 'autoFilterFailed' },
];

export const AutonomousWorkflowList: React.FC<AutonomousWorkflowListProps> = ({
  selectedId,
  onSelect,
}) => {
  const language = useLanguage();
  const [statusFilter, setStatusFilter] = useState('');
  const { data, isLoading } = useWorkflows(statusFilter ? { status: statusFilter } : undefined);
  const deleteMutation = useDeleteWorkflow();
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);

  const workflows = data?.workflows ?? [];

  if (isLoading) {
    return (
      <div className="d-flex justify-content-center p-4">
        <Loading />
      </div>
    );
  }

  const handleDeleteClick = (e: React.MouseEvent, workflowId: string) => {
    e.stopPropagation();
    if (confirmDeleteId === workflowId) {
      deleteMutation.mutate(workflowId);
      setConfirmDeleteId(null);
    } else {
      setConfirmDeleteId(workflowId);
    }
  };

  return (
    <>
      {/* Status Filter Tabs */}
      <div className="d-flex border-bottom px-2 pt-1" style={{ fontSize: '0.75rem' }}>
        {STATUS_FILTER_TABS.map((tab) => (
          <button
            key={tab.key}
            className={`btn btn-sm px-2 py-1 border-0 rounded-0 ${statusFilter === tab.key ? 'fw-bold text-primary border-bottom border-2 border-primary' : 'text-muted'}`}
            style={{ borderBottomWidth: '2px' }}
            onClick={() => setStatusFilter(tab.key)}
          >
            {t(tab.labelKey, language)}
          </button>
        ))}
      </div>

      {workflows.length === 0 ? (
        <div className="text-center text-muted p-4">
          <i className="bi bi-inbox fs-1 d-block mb-2"></i>
          <small>{t('autoNoWorkflows', language)}</small>
        </div>
      ) : (
        <div className="list-group list-group-flush">
          {workflows.map((workflow) => {
            const statusCfg = STATUS_CONFIG[workflow.status] || STATUS_CONFIG.pending;
            const isSelected = selectedId === workflow.workflow_id;
            const isActive = ACTIVE_WORKFLOW_STATUSES.includes(workflow.status);
            const isConfirming = confirmDeleteId === workflow.workflow_id;

            return (
              <div
                key={workflow.workflow_id}
                className={`list-group-item border-0 px-3 py-2 d-flex align-items-start ${isSelected ? 'active' : ''}`}
                style={{ cursor: 'pointer' }}
                onClick={() => onSelect(workflow)}
              >
                <div className="flex-grow-1 min-width-0">
                  <div className="fw-semibold text-truncate" style={{ fontSize: '0.875rem' }}>
                    {workflow.title || workflow.requirements_text?.slice(0, 50) || `Workflow ${workflow.workflow_id.slice(0, 8)}`}
                  </div>
                  <div className="d-flex align-items-center gap-1 mt-1">
                    <Badge variant={statusCfg.variant as 'secondary' | 'info' | 'primary' | 'warning' | 'success' | 'danger'}>
                      <i className={`bi ${statusCfg.icon} me-1`}></i>
                      {t(statusCfg.labelKey, language)}
                    </Badge>
                    {workflow.dev_round > 1 && (
                      <Badge variant="light">R{workflow.dev_round}</Badge>
                    )}
                  </div>
                  <div className="text-muted mt-1" style={{ fontSize: '0.75rem' }}>
                    <i className={`bi ${workflow.workspace_type === 'remote' ? 'bi-cloud' : 'bi-laptop'} me-1`}></i>
                    {workflow.cli_tool}
                    {workflow.created_at && (
                      <span className="ms-2">{new Date(workflow.created_at).toLocaleDateString()}</span>
                    )}
                  </div>
                </div>
                <div className="d-flex align-items-center gap-1 ms-1">
                  {isActive && (
                    <span className="spinner-border spinner-border-sm text-primary" role="status">
                      <span className="visually-hidden">...</span>
                    </span>
                  )}
                  {!isActive && (
                    <button
                      className={`btn btn-sm border-0 p-0 ${isConfirming ? 'btn-outline-danger' : 'btn-outline-secondary'}`}
                      title={t('autoDeleteWorkflow', language)}
                      disabled={deleteMutation.isPending}
                      onClick={(e) => handleDeleteClick(e, workflow.workflow_id)}
                    >
                      <i className="bi bi-trash" style={{ fontSize: '0.75rem' }}></i>
                      {isConfirming && (
                        <small className="ms-1">{t('autoDeleteConfirm', language)}</small>
                      )}
                    </button>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </>
  );
};
