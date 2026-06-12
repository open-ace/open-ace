/**
 * AutonomousWorkflowList Component - List of autonomous development workflows
 *
 * Displays workflows with status filter tabs, fork grouping, and delete capability.
 * Fork workflows are indented under their parent with a 🔀 icon.
 */

import React, { useEffect, useMemo, useState } from 'react';
import { useLanguage } from '@/store';
import { t } from '@/i18n';
import { Badge, Loading, Pagination } from '@/components/common';
import { useWorkflows, useDeleteWorkflow } from '@/hooks/useAutonomous';
import type { AutonomousWorkflow } from '@/api/autonomous';

interface AutonomousWorkflowListProps {
  selectedId: string | null;
  onSelect: (workflow: AutonomousWorkflow) => void;
  onClearSelection: () => void;
  preserveInitialSelection?: boolean;
  onListStateChange?: (state: {
    total: number;
    isLoading: boolean;
    hasLoaded: boolean;
    hasActiveFilters: boolean;
    workflows: AutonomousWorkflow[];
  }) => void;
}

const STATUS_CONFIG: Record<string, { variant: string; icon: string; labelKey: string }> = {
  queued: { variant: 'secondary', icon: 'bi-hourglass-split', labelKey: 'autoStatusQueued' },
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
  planning_timeout: {
    variant: 'warning',
    icon: 'bi-clock-history',
    labelKey: 'autoStatusPlanningTimeout',
  },
};

/** Shared active status set — used by both WorkflowList and WorkflowTimeline */
export const ACTIVE_WORKFLOW_STATUSES = [
  'pending',
  'preparing',
  'planning',
  'developing',
  'pr_review',
  'reporting',
  'waiting',
  'merging',
];

const STATUS_FILTER_TABS = [
  { key: '', labelKey: 'autoFilterAll' },
  { key: 'queued', labelKey: 'autoFilterQueued' },
  {
    key: 'pending,preparing,planning,developing,pr_review,reporting,waiting,merging,paused,planning_timeout',
    labelKey: 'autoFilterActive',
  },
  { key: 'completed', labelKey: 'autoFilterCompleted' },
  { key: 'failed', labelKey: 'autoFilterFailed' },
];

const PAGE_SIZE = 50;
const EMPTY_WORKFLOWS: AutonomousWorkflow[] = [];

export const AutonomousWorkflowList: React.FC<AutonomousWorkflowListProps> = ({
  selectedId,
  onSelect,
  onClearSelection,
  preserveInitialSelection = false,
  onListStateChange,
}) => {
  const language = useLanguage();
  const [statusFilter, setStatusFilter] = useState('');
  const [searchInput, setSearchInput] = useState('');
  const [debouncedSearch, setDebouncedSearch] = useState('');
  const [page, setPage] = useState(1);
  const [hasUserChangedView, setHasUserChangedView] = useState(false);
  const filters = useMemo(() => {
    const params: Record<string, string> = {
      limit: String(PAGE_SIZE),
      offset: String((page - 1) * PAGE_SIZE),
    };
    if (statusFilter) {
      params.status = statusFilter;
    }
    if (debouncedSearch.trim()) {
      params.search = debouncedSearch.trim();
    }
    return params;
  }, [debouncedSearch, page, statusFilter]);
  const { data, isLoading } = useWorkflows(filters);
  const deleteMutation = useDeleteWorkflow();
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);

  const workflows = data?.workflows ?? EMPTY_WORKFLOWS;
  const total = data?.total ?? workflows.length;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const hasActiveFilters = Boolean(statusFilter || searchInput.trim());

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setDebouncedSearch(searchInput);
    }, 300);
    return () => window.clearTimeout(timer);
  }, [searchInput]);

  useEffect(() => {
    onListStateChange?.({
      total,
      isLoading,
      hasLoaded: !!data && !isLoading,
      hasActiveFilters,
      workflows,
    });
  }, [data, hasActiveFilters, isLoading, onListStateChange, total, workflows]);

  useEffect(() => {
    if (isLoading || !data) return;
    if (page > totalPages) {
      setPage(totalPages);
      return;
    }

    const shouldReconcileSelection = !preserveInitialSelection || hasUserChangedView || !selectedId;

    if (workflows.length === 0) {
      if (selectedId && shouldReconcileSelection) {
        onClearSelection();
      }
      return;
    }

    const selectedIsVisible = workflows.some((wf) => wf.workflow_id === selectedId);
    if (!selectedId || (shouldReconcileSelection && !selectedIsVisible)) {
      onSelect(workflows[0]);
    }
  }, [
    data,
    hasUserChangedView,
    isLoading,
    onClearSelection,
    onSelect,
    page,
    preserveInitialSelection,
    selectedId,
    totalPages,
    workflows,
  ]);

  // Build fork tree: identify parents and children
  const { rootWorkflows, childrenMap } = useMemo(() => {
    const children: Record<string, AutonomousWorkflow[]> = {};
    const childIds = new Set<string>();
    const workflowIds = new Set(workflows.map((wf) => wf.workflow_id));

    // First pass: identify fork children
    workflows.forEach((wf) => {
      if (wf.parent_workflow_id && workflowIds.has(wf.parent_workflow_id)) {
        if (!children[wf.parent_workflow_id]) {
          children[wf.parent_workflow_id] = [];
        }
        children[wf.parent_workflow_id].push(wf);
        childIds.add(wf.workflow_id);
      }
    });

    // Second pass: roots = workflows that are NOT children
    const roots = workflows.filter((wf) => !childIds.has(wf.workflow_id));

    return { rootWorkflows: roots, childrenMap: children };
  }, [workflows]);

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

  const handleStatusFilterChange = (filter: string) => {
    setStatusFilter(filter);
    setPage(1);
    setHasUserChangedView(true);
  };

  const handleSearchChange = (value: string) => {
    setSearchInput(value);
    setPage(1);
    setHasUserChangedView(true);
  };

  const handlePageChange = (nextPage: number) => {
    setPage(nextPage);
    setHasUserChangedView(true);
  };

  // Render a single workflow item
  const renderWorkflowItem = (workflow: AutonomousWorkflow, isForkChild: boolean) => {
    const statusCfg = STATUS_CONFIG[workflow.status] || STATUS_CONFIG.pending;
    const isSelected = selectedId === workflow.workflow_id;
    const isActive = ACTIVE_WORKFLOW_STATUSES.includes(workflow.status);
    const isConfirming = confirmDeleteId === workflow.workflow_id;

    return (
      <div
        key={workflow.workflow_id}
        className={`list-group-item border-0 px-3 py-2 d-flex align-items-start ${isSelected ? 'active' : ''} ${isForkChild ? 'bg-light' : ''}`}
        style={{
          cursor: 'pointer',
          ...(isForkChild
            ? { paddingLeft: '2rem', borderTop: '1px dashed var(--bs-gray-300)' }
            : {}),
        }}
        onClick={() => onSelect(workflow)}
      >
        <div className="flex-grow-1 min-width-0">
          <div className="fw-semibold text-truncate" style={{ fontSize: '0.875rem' }}>
            {isForkChild && (
              <i className="bi bi-diagram-3 text-info me-1" style={{ fontSize: '0.75rem' }}></i>
            )}
            {workflow.title ||
              workflow.requirements_text?.slice(0, 50) ||
              `Workflow ${workflow.workflow_id.slice(0, 8)}`}
          </div>
          <div className="d-flex align-items-center gap-1 mt-1">
            <Badge
              variant={
                statusCfg.variant as
                  | 'secondary'
                  | 'info'
                  | 'primary'
                  | 'warning'
                  | 'success'
                  | 'danger'
              }
            >
              <i className={`bi ${statusCfg.icon} me-1`}></i>
              {t(statusCfg.labelKey, language)}
            </Badge>
            {workflow.dev_round > 1 && <Badge variant="light">R{workflow.dev_round}</Badge>}
            {workflow.batch_order && workflow.batch_total && (
              <Badge variant="light">
                {workflow.batch_order}/{workflow.batch_total}
              </Badge>
            )}
            {isForkChild && (
              <Badge variant="info" style={{ fontSize: '0.6rem' }}>
                {t('autoForkedFrom', language)}
              </Badge>
            )}
          </div>
          <div className="text-muted mt-1" style={{ fontSize: '0.75rem' }}>
            <i
              className={`bi ${workflow.workspace_type === 'remote' ? 'bi-cloud' : 'bi-laptop'} me-1`}
            ></i>
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
              {isConfirming && <small className="ms-1">{t('autoDeleteConfirm', language)}</small>}
            </button>
          )}
        </div>
      </div>
    );
  };

  return (
    <>
      {/* Status Filter Tabs */}
      <div className="px-2 py-2 border-bottom">
        <div className="input-group input-group-sm">
          <span className="input-group-text bg-white">
            <i className="bi bi-search"></i>
          </span>
          <input
            className="form-control"
            placeholder={t('autoSearchWorkflows', language)}
            value={searchInput}
            onChange={(e) => handleSearchChange(e.target.value)}
          />
          {searchInput && (
            <button
              className="btn btn-outline-secondary"
              type="button"
              title={t('reset', language)}
              onClick={() => handleSearchChange('')}
            >
              <i className="bi bi-x-lg"></i>
            </button>
          )}
        </div>
      </div>
      <div className="d-flex border-bottom px-2 pt-1" style={{ fontSize: '0.75rem' }}>
        {STATUS_FILTER_TABS.map((tab) => (
          <button
            key={tab.key}
            className={`btn btn-sm px-2 py-1 border-0 rounded-0 ${statusFilter === tab.key ? 'fw-bold text-primary border-bottom border-2 border-primary' : 'text-muted'}`}
            style={{ borderBottomWidth: '2px' }}
            onClick={() => handleStatusFilterChange(tab.key)}
          >
            {t(tab.labelKey, language)}
          </button>
        ))}
      </div>

      {workflows.length === 0 ? (
        <div className="text-center text-muted p-4">
          <i className="bi bi-inbox fs-1 d-block mb-2"></i>
          <small>
            {searchInput || statusFilter
              ? t('autoNoMatchingWorkflows', language)
              : t('autoNoWorkflows', language)}
          </small>
        </div>
      ) : (
        <>
          <div className="list-group list-group-flush">
            {rootWorkflows.map((workflow) => {
              const forkChildren = childrenMap[workflow.workflow_id] || [];

              return (
                <React.Fragment key={workflow.workflow_id}>
                  {/* Parent/root workflow item */}
                  {renderWorkflowItem(workflow, false)}

                  {/* Fork children indented under parent */}
                  {forkChildren.map((child) => renderWorkflowItem(child, true))}
                </React.Fragment>
              );
            })}
          </div>
          {totalPages > 1 && (
            <div className="border-top p-2">
              <Pagination
                currentPage={page}
                totalPages={totalPages}
                onPageChange={handlePageChange}
                showPageInput={false}
                showPageInfo={false}
                maxVisiblePages={3}
              />
            </div>
          )}
        </>
      )}
    </>
  );
};
