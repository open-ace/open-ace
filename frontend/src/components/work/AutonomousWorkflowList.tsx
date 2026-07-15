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
import { useWorkflows, useDeleteBatch, useDeleteWorkflow } from '@/hooks/useAutonomous';
import type { AutonomousWorkflow } from '@/api/autonomous';
import './AutonomousWorkflowList.css';

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
  'queued',
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
const STATUS_ORDER = Object.keys(STATUS_CONFIG);

function isBatchWorkflow(workflow: AutonomousWorkflow): boolean {
  return Boolean(workflow.batch_id && (workflow.batch_total ?? 0) > 1);
}

function compareBatchWorkflows(a: AutonomousWorkflow, b: AutonomousWorkflow): number {
  const aOrder = typeof a.batch_order === 'number' ? a.batch_order : Number.MAX_SAFE_INTEGER;
  const bOrder = typeof b.batch_order === 'number' ? b.batch_order : Number.MAX_SAFE_INTEGER;
  if (aOrder !== bOrder) {
    return aOrder - bOrder;
  }

  const aCreated = a.created_at ? Date.parse(a.created_at) : Number.MAX_SAFE_INTEGER;
  const bCreated = b.created_at ? Date.parse(b.created_at) : Number.MAX_SAFE_INTEGER;
  if (aCreated !== bCreated) {
    return aCreated - bCreated;
  }

  return a.workflow_id.localeCompare(b.workflow_id);
}

type WorkflowListEntry =
  | { type: 'workflow'; workflow: AutonomousWorkflow }
  | {
      type: 'batch';
      batchId: string;
      representative: AutonomousWorkflow;
      workflows: AutonomousWorkflow[];
    };

function getIssueLabel(workflow: AutonomousWorkflow): string {
  if (typeof workflow.github_issue_number === 'number' && workflow.github_issue_number > 0) {
    return `#${workflow.github_issue_number}`;
  }

  const title = workflow.title?.trim();
  if (!title) {
    return workflow.batch_order
      ? `#${workflow.batch_order}`
      : `#${workflow.workflow_id.slice(0, 8)}`;
  }

  const issueMatch = title.match(/\(#?(\d+)\)\s*$/);
  if (issueMatch) {
    return `#${issueMatch[1]}`;
  }

  return title;
}

function getCommonTitlePrefix(workflows: AutonomousWorkflow[]): string {
  const titles = workflows.map((workflow) => workflow.title?.trim()).filter(Boolean) as string[];
  if (titles.length === 0) {
    return '';
  }

  let prefix = titles[0];
  for (const title of titles.slice(1)) {
    let index = 0;
    while (
      index < prefix.length &&
      index < title.length &&
      prefix[index].toLowerCase() === title[index].toLowerCase()
    ) {
      index += 1;
    }
    prefix = prefix.slice(0, index);
    if (!prefix) {
      break;
    }
  }

  return prefix
    .replace(/\s*\(#?\d*$/g, '')
    .replace(/[\s\-–—(:,#]+$/g, '')
    .trim();
}

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
  const [expandedBatchIds, setExpandedBatchIds] = useState<Record<string, boolean>>({});
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
  const deleteBatchMutation = useDeleteBatch();
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

  const displayEntries = useMemo<WorkflowListEntry[]>(() => {
    const batchRoots = new Map<string, AutonomousWorkflow[]>();
    rootWorkflows.forEach((workflow) => {
      if (workflow.batch_id && isBatchWorkflow(workflow)) {
        const entries = batchRoots.get(workflow.batch_id) ?? [];
        entries.push(workflow);
        batchRoots.set(workflow.batch_id, entries);
      }
    });

    const seenBatchIds = new Set<string>();
    const entries: WorkflowListEntry[] = [];

    rootWorkflows.forEach((workflow) => {
      if (!workflow.batch_id || !isBatchWorkflow(workflow)) {
        entries.push({ type: 'workflow', workflow });
        return;
      }

      if (seenBatchIds.has(workflow.batch_id)) {
        return;
      }

      seenBatchIds.add(workflow.batch_id);
      const batchWorkflows = [...(batchRoots.get(workflow.batch_id) ?? [workflow])].sort(
        compareBatchWorkflows
      );

      entries.push({
        type: 'batch',
        batchId: workflow.batch_id,
        representative: batchWorkflows[0] ?? workflow,
        workflows: batchWorkflows,
      });
    });

    return entries;
  }, [rootWorkflows]);

  const formatWorkflowDate = (value: string | null | undefined): string | null => {
    if (!value) {
      return null;
    }
    return new Date(value).toLocaleDateString();
  };

  const buildBatchTitle = (batchWorkflows: AutonomousWorkflow[]): string => {
    const prefix = getCommonTitlePrefix(batchWorkflows);
    if (prefix.length >= 8) {
      return prefix;
    }

    const issueNumbers = batchWorkflows
      .map((workflow) => workflow.github_issue_number)
      .filter((value): value is number => typeof value === 'number' && value > 0)
      .sort((a, b) => a - b);

    if (issueNumbers.length >= 2) {
      return `gh issue ${issueNumbers[0]}-${issueNumbers[issueNumbers.length - 1]}`;
    }

    return (
      batchWorkflows[0]?.title ||
      batchWorkflows[0]?.requirements_text?.slice(0, 70) ||
      `Workflow ${batchWorkflows[0]?.workflow_id.slice(0, 8) ?? ''}`
    );
  };

  const buildBatchToolSummary = (batchWorkflows: AutonomousWorkflow[]): string => {
    const tools = Array.from(
      new Set(batchWorkflows.map((workflow) => workflow.cli_tool).filter(Boolean))
    );
    if (tools.length <= 2) {
      return tools.join(', ');
    }
    return `${tools.slice(0, 2).join(', ')} +${tools.length - 2}`;
  };

  const buildBatchDateSummary = (batchWorkflows: AutonomousWorkflow[]): string | null => {
    const dates = Array.from(
      new Set(
        batchWorkflows
          .map((workflow) => formatWorkflowDate(workflow.created_at))
          .filter((value): value is string => Boolean(value))
      )
    );
    if (dates.length === 0) {
      return null;
    }
    if (dates.length === 1) {
      return dates[0];
    }
    return `${dates[0]} - ${dates[dates.length - 1]}`;
  };

  const buildBatchStatusSummary = (batchWorkflows: AutonomousWorkflow[]) => {
    const counts = new Map<string, number>();
    batchWorkflows.forEach((workflow) => {
      counts.set(workflow.status, (counts.get(workflow.status) ?? 0) + 1);
    });

    return STATUS_ORDER.filter((status) => counts.has(status)).map((status) => ({
      status,
      count: counts.get(status) ?? 0,
    }));
  };

  useEffect(() => {
    if (!selectedId) return;
    const selectedWorkflow = workflows.find((workflow) => workflow.workflow_id === selectedId);
    if (!selectedWorkflow?.batch_id || !isBatchWorkflow(selectedWorkflow)) return;

    setExpandedBatchIds((current) => {
      if (current[selectedWorkflow.batch_id!]) {
        return current;
      }

      return {
        ...current,
        [selectedWorkflow.batch_id!]: true,
      };
    });
  }, [selectedId, workflows]);

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

  const handleBatchDeleteClick = (e: React.MouseEvent, batchId: string) => {
    e.stopPropagation();
    const confirmKey = `batch:${batchId}`;
    if (confirmDeleteId === confirmKey) {
      deleteBatchMutation.mutate(batchId);
      setConfirmDeleteId(null);
    } else {
      setConfirmDeleteId(confirmKey);
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

  const toggleBatch = (batchId: string) => {
    setExpandedBatchIds((current) => ({
      ...current,
      [batchId]: !current[batchId],
    }));
  };

  const handleBatchKeyDown = (event: React.KeyboardEvent, batchId: string) => {
    if (event.key !== 'Enter' && event.key !== ' ') {
      return;
    }
    event.preventDefault();
    toggleBatch(batchId);
  };

  // Render a single workflow item
  const renderWorkflowItem = (
    workflow: AutonomousWorkflow,
    isForkChild: boolean,
    compact = false
  ) => {
    const statusCfg = STATUS_CONFIG[workflow.status] || STATUS_CONFIG.pending;
    const isSelected = selectedId === workflow.workflow_id;
    const isActive = ACTIVE_WORKFLOW_STATUSES.includes(workflow.status);
    const isConfirming = confirmDeleteId === workflow.workflow_id;
    const workflowDate = formatWorkflowDate(workflow.created_at);
    const itemClasses = [
      'list-group-item',
      'list-group-item-action',
      'border-0',
      'px-3',
      'py-2',
      'd-flex',
      'align-items-start',
      'auto-workflow-item',
      compact ? 'auto-workflow-item-compact' : 'auto-workflow-item-standard',
      isSelected ? 'auto-workflow-item-selected' : '',
      isForkChild ? 'auto-workflow-item-indented' : '',
    ]
      .filter(Boolean)
      .join(' ');

    return (
      <div key={workflow.workflow_id} className={itemClasses} onClick={() => onSelect(workflow)}>
        <div className="flex-grow-1 min-width-0">
          {compact ? (
            <div className="d-flex align-items-start justify-content-between gap-2">
              <div className="min-width-0">
                <div className="d-flex align-items-center gap-2 flex-wrap">
                  <div className="auto-workflow-issue-label">
                    {isForkChild && <i className="bi bi-diagram-3 text-info me-1"></i>}
                    {getIssueLabel(workflow)}
                  </div>
                  {workflow.batch_order && workflow.batch_total && (
                    <Badge variant="light">
                      {workflow.batch_order}/{workflow.batch_total}
                    </Badge>
                  )}
                  {workflow.dev_round > 1 && <Badge variant="light">R{workflow.dev_round}</Badge>}
                </div>
                <div className="d-flex align-items-center gap-1 mt-2 flex-wrap">
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
                  {isForkChild && (
                    <Badge variant="info" style={{ fontSize: '0.6rem' }}>
                      {t('autoForkedFrom', language)}
                    </Badge>
                  )}
                </div>
              </div>
            </div>
          ) : (
            <>
              <div className="fw-semibold text-truncate auto-workflow-title">
                {isForkChild && (
                  <i className="bi bi-diagram-3 text-info me-1" style={{ fontSize: '0.75rem' }}></i>
                )}
                {workflow.title ||
                  workflow.requirements_text?.slice(0, 50) ||
                  `Workflow ${workflow.workflow_id.slice(0, 8)}`}
              </div>
              <div className="d-flex align-items-center gap-1 mt-1 flex-wrap">
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
              <div className="text-muted mt-1 auto-workflow-meta">
                <i
                  className={`bi ${workflow.workspace_type === 'remote' ? 'bi-cloud' : 'bi-laptop'} me-1`}
                ></i>
                {workflow.cli_tool}
                {workflowDate && <span className="ms-2">{workflowDate}</span>}
              </div>
            </>
          )}
        </div>
        <div className="d-flex align-items-center gap-1 ms-2">
          {isActive && (
            <span className="spinner-border spinner-border-sm text-primary" role="status">
              <span className="visually-hidden">...</span>
            </span>
          )}
          {!isActive && (
            <button
              className={`btn btn-sm border-0 p-0 auto-workflow-delete-btn ${isConfirming ? 'btn-outline-danger' : 'btn-outline-secondary'}`}
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

  const renderWorkflowBranch = (workflow: AutonomousWorkflow, compact = false) => (
    <React.Fragment key={workflow.workflow_id}>
      {renderWorkflowItem(workflow, false, compact)}
      {(childrenMap[workflow.workflow_id] || []).map((child) =>
        renderWorkflowItem(child, true, compact)
      )}
    </React.Fragment>
  );

  const renderBatchGroup = (entry: Extract<WorkflowListEntry, { type: 'batch' }>) => {
    const isExpanded = !!expandedBatchIds[entry.batchId];
    const batchTitle = buildBatchTitle(entry.workflows);
    const batchTools = buildBatchToolSummary(entry.workflows);
    const batchDate = buildBatchDateSummary(entry.workflows);
    const batchStatuses = buildBatchStatusSummary(entry.workflows);
    const visibleCount = entry.workflows.length;
    const batchTotalCount = entry.representative.batch_total ?? visibleCount;
    const batchCountSplit = visibleCount < batchTotalCount;
    const hasSelectedWorkflow = entry.workflows.some((workflow) => {
      if (workflow.workflow_id === selectedId) {
        return true;
      }
      return (childrenMap[workflow.workflow_id] || []).some(
        (child) => child.workflow_id === selectedId
      );
    });
    const hasActiveWorkflow = entry.workflows.some((workflow) => {
      if (ACTIVE_WORKFLOW_STATUSES.includes(workflow.status)) {
        return true;
      }
      return (childrenMap[workflow.workflow_id] || []).some((child) =>
        ACTIVE_WORKFLOW_STATUSES.includes(child.status)
      );
    });
    const batchDeleteConfirmKey = `batch:${entry.batchId}`;

    return (
      <React.Fragment key={`batch-${entry.batchId}`}>
        <div
          className={`list-group-item list-group-item-action border-0 px-3 py-3 text-start auto-workflow-batch ${hasSelectedWorkflow ? 'auto-workflow-batch-selected' : ''}`}
          role="button"
          tabIndex={0}
          aria-expanded={isExpanded}
          aria-label={`${isExpanded ? t('collapse', language) : t('expand', language)} ${t('autoBatchInfo', language)}`}
          onClick={() => toggleBatch(entry.batchId)}
          onKeyDown={(event) => handleBatchKeyDown(event, entry.batchId)}
        >
          <div className="d-flex align-items-start justify-content-between gap-3">
            <div className="min-width-0">
              <div className="auto-workflow-batch-kicker">
                <i
                  className={`bi ${isExpanded ? 'bi-chevron-down' : 'bi-chevron-right'} me-2`}
                  aria-hidden="true"
                ></i>
                {t('autoBatchInfo', language)}
              </div>
              <div className="auto-workflow-batch-title">{batchTitle}</div>
              <div className="auto-workflow-batch-meta-stack">
                {batchDate && (
                  <span className="auto-workflow-batch-meta-pill auto-workflow-batch-meta-line">
                    <i className="bi bi-calendar3 me-1"></i>
                    {batchDate}
                  </span>
                )}
                {batchTools && (
                  <span className="auto-workflow-batch-meta-pill auto-workflow-batch-meta-line">
                    <i className="bi bi-terminal me-1"></i>
                    {batchTools}
                  </span>
                )}
                <span className="auto-workflow-batch-meta-pill auto-workflow-batch-count auto-workflow-batch-meta-line">
                  {batchCountSplit ? `${visibleCount} / ${batchTotalCount}` : visibleCount}{' '}
                  {t('autoBatchWorkflowUnit', language)}
                </span>
              </div>
              <div className="auto-workflow-batch-status-row">
                {batchStatuses.map(({ status, count }) => {
                  const cfg = STATUS_CONFIG[status] || STATUS_CONFIG.pending;
                  return (
                    <Badge
                      key={status}
                      variant={
                        cfg.variant as
                          | 'secondary'
                          | 'info'
                          | 'primary'
                          | 'warning'
                          | 'success'
                          | 'danger'
                      }
                    >
                      {t(cfg.labelKey, language)} {count}
                    </Badge>
                  );
                })}
              </div>
            </div>
            <div className="d-flex align-items-center gap-2 auto-workflow-batch-side">
              {hasActiveWorkflow && (
                <span className="spinner-border spinner-border-sm text-primary" role="status">
                  <span className="visually-hidden">...</span>
                </span>
              )}
              {!hasActiveWorkflow && (
                <button
                  className={`btn btn-sm border-0 p-0 auto-workflow-delete-btn ${confirmDeleteId === batchDeleteConfirmKey ? 'btn-outline-danger' : 'btn-outline-secondary'}`}
                  title={t('autoDeleteWorkflow', language)}
                  disabled={deleteBatchMutation.isPending}
                  onClick={(event) => handleBatchDeleteClick(event, entry.batchId)}
                >
                  <i className="bi bi-trash" style={{ fontSize: '0.85rem' }}></i>
                  {confirmDeleteId === batchDeleteConfirmKey && (
                    <small className="ms-1">{t('autoDeleteConfirm', language)}</small>
                  )}
                </button>
              )}
              <span className="auto-workflow-batch-chevron">
                <i className={`bi ${isExpanded ? 'bi-dash-lg' : 'bi-plus-lg'}`}></i>
              </span>
            </div>
          </div>
        </div>
        {isExpanded && (
          <div className="list-group list-group-flush auto-workflow-batch-children">
            {entry.workflows.map((workflow) => renderWorkflowBranch(workflow, true))}
          </div>
        )}
      </React.Fragment>
    );
  };

  return (
    <>
      {/* Status Filter Tabs */}
      <div className="px-2 py-2 border-bottom auto-workflow-search">
        <div className="input-group input-group-sm">
          <span className="input-group-text">
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
            {displayEntries.map((entry) =>
              entry.type === 'batch'
                ? renderBatchGroup(entry)
                : renderWorkflowBranch(entry.workflow)
            )}
          </div>
          {totalPages > 1 && (
            <div className="border-top p-2">
              <Pagination
                currentPage={page}
                totalPages={totalPages}
                onPageChange={handlePageChange}
                showPageInput={false}
                maxVisiblePages={3}
              />
            </div>
          )}
        </>
      )}
    </>
  );
};
