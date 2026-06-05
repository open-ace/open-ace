/**
 * AutonomousWorkflowList Component - List of autonomous development workflows
 *
 * Displays workflows grouped by status with filtering.
 */

import React from 'react';
import { useLanguage } from '@/store';
import { t } from '@/i18n';
import { Badge, Loading } from '@/components/common';
import { useWorkflows } from '@/hooks/useAutonomous';
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
};

export const AutonomousWorkflowList: React.FC<AutonomousWorkflowListProps> = ({
  selectedId,
  onSelect,
}) => {
  const language = useLanguage();
  const { data, isLoading } = useWorkflows();

  const workflows = data?.workflows ?? [];

  if (isLoading) {
    return (
      <div className="d-flex justify-content-center p-4">
        <Loading />
      </div>
    );
  }

  if (workflows.length === 0) {
    return (
      <div className="text-center text-muted p-4">
        <i className="bi bi-inbox fs-1 d-block mb-2"></i>
        <small>{t('noWorkflows', language)}</small>
      </div>
    );
  }

  return (
    <div className="list-group list-group-flush">
      {workflows.map((workflow) => {
        const statusCfg = STATUS_CONFIG[workflow.status] || STATUS_CONFIG.pending;
        const isSelected = selectedId === workflow.workflow_id;
        const isActive = ['pending', 'preparing', 'planning', 'developing', 'pr_review', 'reporting', 'waiting', 'merging'].includes(workflow.status);

        return (
          <button
            key={workflow.workflow_id}
            className={`list-group-item list-group-item-action border-0 px-3 py-2 ${isSelected ? 'active' : ''}`}
            onClick={() => onSelect(workflow)}
            style={{ cursor: 'pointer' }}
          >
            <div className="d-flex align-items-start justify-content-between">
              <div className="flex-grow-1 min-width-0">
                <div className="fw-semibold text-truncate" style={{ fontSize: '0.875rem' }}>
                  {workflow.title || workflow.requirements_text?.slice(0, 50) || `Workflow ${workflow.workflow_id.slice(0, 8)}`}
                </div>
                <div className="d-flex align-items-center gap-1 mt-1">
                  <Badge variant={statusCfg.variant as any}>
                    <i className={`bi ${statusCfg.icon} me-1`}></i>
                    {t(statusCfg.labelKey, language)}
                  </Badge>
                  {workflow.dev_round > 1 && (
                    <Badge variant="light">
                      R{workflow.dev_round}
                    </Badge>
                  )}
                </div>
                <div className="text-muted mt-1" style={{ fontSize: '0.75rem' }}>
                  <i className={`bi ${workflow.workspace_type === 'remote' ? 'bi-cloud' : 'bi-laptop'} me-1`}></i>
                  {workflow.cli_tool}
                  {workflow.created_at && (
                    <span className="ms-2">
                      {new Date(workflow.created_at).toLocaleDateString()}
                    </span>
                  )}
                </div>
              </div>
              {isActive && (
                <span className="spinner-border spinner-border-sm text-primary ms-2" role="status">
                  <span className="visually-hidden">...</span>
                </span>
              )}
            </div>
          </button>
        );
      })}
    </div>
  );
};
