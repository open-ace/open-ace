/**
 * ProjectManagement Component - Project management page
 *
 * Issue #1278: Project view with categorized workspace grouping
 *
 * Features:
 * - Categorized project list with aggregated statistics
 * - Expandable rows to view workspace details
 * - Delete projects
 */

import React, { useState, useEffect, useMemo, startTransition } from 'react';
import { useLanguage } from '@/store';
import { t } from '@/i18n';
import {
  Card,
  StatCard,
  Button,
  Loading,
  Error,
  EmptyState,
  Modal,
  Badge,
  Avatar,
  Divider,
  PageRefreshControl,
} from '@/components/common';
import { getAllProjectStats, deleteProject, type ProjectStats } from '@/api/projects';
import { listProjectCategories, type ProjectCategory } from '@/api/projectCategories';
import { formatDateTime, createMatcherConfig } from '@/utils';
import { usePageRefresh } from '@/hooks';

type CategorySortKey = 'name' | 'total_workspaces' | 'total_users' | 'total_tokens' | 'last_access';
type SortDirection = 'asc' | 'desc';

// Functional directories that should not be considered as project names
// These are common subdirectories that indicate project structure, not project identity
const FUNCTIONAL_DIRS = [
  'test',
  'tests',
  'testing',
  'spec',
  'specs',
  'src',
  'frontend',
  'backend',
  'web',
  'ui',
  'api',
  'server',
  'lib',
  'libs',
  'utils',
  'scripts',
  'docs',
  'config',
  'data',
];

// Aggregated stats for a category
interface CategoryAggregatedStats {
  category_id: number;
  category_name: string;
  key_patterns: string[];
  total_workspaces: number;
  total_users: number;
  total_tokens: number;
  total_requests: number;
  total_duration_seconds: number;
  first_access: string | null;
  last_access: string | null;
  workspaces: ProjectStats[];
}

// Extract project name from path, skipping functional directories
// Issue #1371: Auto-categorize by project name instead of generic "uncategorized"
function extractProjectName(path: string): string {
  const parts = path.split(/[/\\]/).filter((p) => p);
  // Search from end, skip functional directories
  for (let i = parts.length - 1; i >= 0; i--) {
    const part = parts[i].toLowerCase();
    if (!FUNCTIONAL_DIRS.includes(part)) {
      return parts[i]; // Return original case
    }
  }
  return parts[parts.length - 1] || 'unknown'; // Fallback
}

// Match project path against patterns (case-insensitive, contains match)
function matchCategory(projectPath: string, patterns: string[]): boolean {
  const lowerPath = projectPath.toLowerCase();
  return patterns.some((p) => p && lowerPath.includes(p.toLowerCase()));
}

// Categorize projects into groups
function categorizeProjects(
  stats: ProjectStats[],
  categories: ProjectCategory[]
): CategoryAggregatedStats[] {
  const result: CategoryAggregatedStats[] = [];
  const matchedProjectIds = new Set<number>();

  // Process each category (active only, sorted by sort_order)
  const activeCategories = categories
    .filter((c) => c.is_active)
    .sort((a, b) => a.sort_order - b.sort_order);

  for (const category of activeCategories) {
    const workspaces: ProjectStats[] = [];

    for (const stat of stats) {
      if (matchedProjectIds.has(stat.project_id)) continue;
      if (matchCategory(stat.project_path, category.key_patterns)) {
        workspaces.push(stat);
        matchedProjectIds.add(stat.project_id);
      }
    }

    // Aggregate stats
    const uniqueUserIds = new Set(workspaces.flatMap((w) => w.user_stats.map((u) => u.user_id)));

    const aggregated: CategoryAggregatedStats = {
      category_id: category.id,
      category_name: category.name,
      key_patterns: category.key_patterns,
      total_workspaces: workspaces.length,
      total_users: uniqueUserIds.size,
      total_tokens: workspaces.reduce((sum, w) => sum + Number(w.total_tokens), 0),
      total_requests: workspaces.reduce((sum, w) => sum + w.total_requests, 0),
      total_duration_seconds: workspaces.reduce((sum, w) => sum + w.total_duration_seconds, 0),
      first_access: workspaces.reduce(
        (min, w) => (w.first_access && (!min || w.first_access < min) ? w.first_access : min),
        null as string | null
      ),
      last_access: workspaces.reduce(
        (max, w) => (w.last_access && (!max || w.last_access > max) ? w.last_access : max),
        null as string | null
      ),
      workspaces,
    };

    result.push(aggregated);
  }

  // Auto-categorize remaining projects by extracted project name (Issue #1371)
  const uncategorizedWorkspaces = stats.filter((s) => !matchedProjectIds.has(s.project_id));
  if (uncategorizedWorkspaces.length > 0) {
    // Group by extracted project name
    const projectGroups: Map<string, ProjectStats[]> = new Map();
    for (const workspace of uncategorizedWorkspaces) {
      const projectName = extractProjectName(workspace.project_path);
      if (!projectGroups.has(projectName)) {
        projectGroups.set(projectName, []);
      }
      projectGroups.get(projectName)!.push(workspace);
    }

    // Create aggregated stats for each project group
    for (const [projectName, workspaces] of projectGroups) {
      const uniqueUserIds = new Set(workspaces.flatMap((w) => w.user_stats.map((u) => u.user_id)));

      result.push({
        category_id: -1, // Use -1 for auto-generated categories
        category_name: projectName,
        key_patterns: [],
        total_workspaces: workspaces.length,
        total_users: uniqueUserIds.size,
        total_tokens: workspaces.reduce((sum, w) => sum + Number(w.total_tokens), 0),
        total_requests: workspaces.reduce((sum, w) => sum + w.total_requests, 0),
        total_duration_seconds: workspaces.reduce((sum, w) => sum + w.total_duration_seconds, 0),
        first_access: workspaces.reduce(
          (min, w) => (w.first_access && (!min || w.first_access < min) ? w.first_access : min),
          null as string | null
        ),
        last_access: workspaces.reduce(
          (max, w) => (w.last_access && (!max || w.last_access > max) ? w.last_access : max),
          null as string | null
        ),
        workspaces,
      });
    }
  }

  return result;
}

export const ProjectManagement: React.FC = () => {
  const language = useLanguage();
  const [stats, setStats] = useState<ProjectStats[]>([]);
  const [categories, setCategories] = useState<ProjectCategory[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expandedCategories, setExpandedCategories] = useState<Set<string>>(new Set());
  const [selectedWorkspace, setSelectedWorkspace] = useState<ProjectStats | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<ProjectStats | null>(null);
  const [isDeleting, setIsDeleting] = useState(false);
  const [sortKey, setSortKey] = useState<CategorySortKey | null>(null);
  const [sortDirection, setSortDirection] = useState<SortDirection>('desc');

  // Page refresh control
  const pageRefresh = usePageRefresh({
    page: '/manage/projects',
    refreshKey: createMatcherConfig([['projects']], 'prefix'),
    interval: 0,
    enabled: false,
    // Note: fetchData defined below, use arrow function to avoid hoisting issues
    onRefresh: () => fetchData(),
  });

  const fetchData = async () => {
    setIsLoading(true);
    setError(null);
    try {
      const [statsResponse, categoriesResponse] = await Promise.all([
        getAllProjectStats(),
        listProjectCategories(),
      ]);
      setStats(statsResponse.stats || []);
      setCategories(categoriesResponse.categories || []);
    } catch (err) {
      const errorMessage =
        err instanceof Error ? (err as Error).message : 'Failed to load project data';
      setError(errorMessage);
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    fetchData();
  }, []);

  const handleDelete = async () => {
    if (!deleteTarget) return;

    setIsDeleting(true);
    try {
      await deleteProject(deleteTarget.project_id);
      await fetchData();
      setDeleteTarget(null);
    } catch (err) {
      console.error('Failed to delete project:', err);
    } finally {
      setIsDeleting(false);
    }
  };

  // Categorize and aggregate
  const categorizedStats = useMemo(
    () => categorizeProjects(stats, categories),
    [stats, categories]
  );

  // Summary statistics (aggregated from categories)
  const summary = useMemo(() => {
    const totalCategories = categorizedStats.length;
    const totalWorkspaces = categorizedStats.reduce((sum, c) => sum + c.total_workspaces, 0);
    const totalUsers = categorizedStats.reduce((sum, c) => sum + c.total_users, 0);
    const totalTokens = categorizedStats.reduce((sum, c) => sum + c.total_tokens, 0);
    const totalDuration = categorizedStats.reduce((sum, c) => sum + c.total_duration_seconds, 0);
    return { totalCategories, totalWorkspaces, totalUsers, totalTokens, totalDuration };
  }, [categorizedStats]);

  const handleSort = (key: CategorySortKey) => {
    startTransition(() => {
      if (sortKey === key) {
        setSortDirection(sortDirection === 'asc' ? 'desc' : 'asc');
      } else {
        setSortKey(key);
        setSortDirection('desc');
      }
    });
  };

  const sortedCategorizedStats = useMemo(() => {
    if (!sortKey) return categorizedStats;
    return [...categorizedStats].sort((a, b) => {
      let cmp: number;
      if (sortKey === 'name') {
        cmp = a.category_name.localeCompare(b.category_name);
      } else if (sortKey === 'last_access') {
        const aVal = a.last_access ?? '';
        const bVal = b.last_access ?? '';
        cmp = aVal.localeCompare(bVal);
      } else {
        const aVal = a[sortKey] || 0;
        const bVal = b[sortKey] || 0;
        cmp = aVal - bVal;
      }
      return sortDirection === 'asc' ? cmp : -cmp;
    });
  }, [categorizedStats, sortKey, sortDirection]);

  const toggleExpand = (categoryName: string) => {
    setExpandedCategories((prev) => {
      const next = new Set(prev);
      if (next.has(categoryName)) {
        next.delete(categoryName);
      } else {
        next.add(categoryName);
      }
      return next;
    });
  };

  const formatDuration = (seconds: number): string => {
    if (seconds < 60) return `${seconds}s`;
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    return `${hours}h ${minutes}m`;
  };

  const formatNumber = (num: number): string => {
    if (num >= 1000000) return `${(num / 1000000).toFixed(1)}M`;
    if (num >= 1000) return `${(num / 1000).toFixed(1)}K`;
    return num.toString();
  };

  if (isLoading) {
    return <Loading />;
  }

  if (error) {
    return <Error message={error} onRetry={fetchData} />;
  }

  return (
    <div className="project-management space-y-4">
      {/* Page Header */}
      <div className="page-header d-flex justify-content-between align-items-center mb-4">
        <h2>{t('projectManagement', language)}</h2>
        <PageRefreshControl
          refresh={pageRefresh}
          compact={true}
          showAutoRefreshToggle={false}
          showIntervalSelector={false}
          showLastRefreshTime={true}
        />
      </div>

      {/* Summary Cards */}
      <div className="row g-3 mb-4">
        <div className="col-md-3">
          <StatCard
            icon={<i className="bi bi-folder fs-4" />}
            label={t('totalProjects', language)}
            value={summary.totalCategories}
            variant="primary"
          />
        </div>
        <div className="col-md-3">
          <StatCard
            icon={<i className="bi bi-folder2-open fs-4" />}
            label={t('totalWorkspaces', language)}
            value={summary.totalWorkspaces}
            variant="default"
          />
        </div>
        <div className="col-md-3">
          <StatCard
            icon={<i className="bi bi-people fs-4" />}
            label={t('totalUsers', language)}
            value={formatNumber(summary.totalUsers)}
            variant="success"
          />
        </div>
        <div className="col-md-3">
          <StatCard
            icon={<i className="bi bi-cpu fs-4" />}
            label={t('totalTokens', language)}
            value={formatNumber(summary.totalTokens)}
            variant="info"
          />
        </div>
      </div>

      {/* Project Categories List */}
      {categorizedStats.length === 0 ? (
        <EmptyState
          icon="bi-folder"
          title={t('noProjectsFound', language)}
          description={t('noProjectsDescription', language)}
        />
      ) : (
        <Card>
          <div className="d-flex justify-content-between align-items-center mb-3">
            <h5 className="mb-0">
              <i className="bi bi-folder me-2" />
              {t('projectCategories', language)}
            </h5>
          </div>

          <div className="table-responsive">
            <table className="table table-hover">
              <thead>
                <tr>
                  <th style={{ width: '40px' }}></th>
                  <th onClick={() => handleSort('name')} style={{ cursor: 'pointer' }}>
                    {t('project', language)}
                    {sortKey === 'name' && (
                      <i
                        className={`bi bi-caret-${sortDirection === 'asc' ? 'up' : 'down'}-fill ms-1`}
                      />
                    )}
                  </th>
                  <th onClick={() => handleSort('total_workspaces')} style={{ cursor: 'pointer' }}>
                    {t('workspaces', language)}
                    {sortKey === 'total_workspaces' && (
                      <i
                        className={`bi bi-caret-${sortDirection === 'asc' ? 'up' : 'down'}-fill ms-1`}
                      />
                    )}
                  </th>
                  <th onClick={() => handleSort('total_users')} style={{ cursor: 'pointer' }}>
                    {t('users', language)}
                    {sortKey === 'total_users' && (
                      <i
                        className={`bi bi-caret-${sortDirection === 'asc' ? 'up' : 'down'}-fill ms-1`}
                      />
                    )}
                  </th>
                  <th onClick={() => handleSort('total_tokens')} style={{ cursor: 'pointer' }}>
                    {t('tokens', language)}
                    {sortKey === 'total_tokens' && (
                      <i
                        className={`bi bi-caret-${sortDirection === 'asc' ? 'up' : 'down'}-fill ms-1`}
                      />
                    )}
                  </th>
                  <th>{t('requests', language)}</th>
                  <th>{t('workTime', language)}</th>
                  <th onClick={() => handleSort('last_access')} style={{ cursor: 'pointer' }}>
                    {t('lastActive', language)}
                    {sortKey === 'last_access' && (
                      <i
                        className={`bi bi-caret-${sortDirection === 'asc' ? 'up' : 'down'}-fill ms-1`}
                      />
                    )}
                  </th>
                </tr>
              </thead>
              <tbody>
                {sortedCategorizedStats.map((category) => {
                  const isExpanded = expandedCategories.has(category.category_name);
                  const isUncategorized = category.category_id === -1;

                  return (
                    <React.Fragment key={category.category_name}>
                      {/* Category Row */}
                      <tr
                        onClick={() => toggleExpand(category.category_name)}
                        style={{ cursor: 'pointer' }}
                        className={
                          isUncategorized ? 'project-category-secondary' : 'project-category-row'
                        }
                      >
                        <td>
                          <i
                            className={`bi bi-chevron-${isExpanded ? 'down' : 'right'} text-muted`}
                          />
                        </td>
                        <td>
                          <div className="d-flex align-items-center">
                            <i
                              className={`bi ${isUncategorized ? 'bi-folder2-open text-info' : 'bi-folder-fill text-warning'} me-2`}
                            />
                            <div>
                              <strong>{category.category_name}</strong>
                              {!isUncategorized && category.key_patterns.length > 0 && (
                                <small className="d-block text-muted">
                                  {category.key_patterns.slice(0, 3).join(', ')}
                                  {category.key_patterns.length > 3 && '...'}
                                </small>
                              )}
                            </div>
                          </div>
                        </td>
                        <td>
                          <Badge variant="secondary" pill>
                            {category.total_workspaces}
                          </Badge>
                        </td>
                        <td>{category.total_users}</td>
                        <td>{formatNumber(category.total_tokens)}</td>
                        <td>{formatNumber(category.total_requests)}</td>
                        <td>{formatDuration(category.total_duration_seconds)}</td>
                        <td>
                          <small>
                            {category.last_access
                              ? formatDateTime(category.last_access)
                              : t('never', language)}
                          </small>
                        </td>
                      </tr>

                      {/* Expanded Workspace Rows */}
                      {isExpanded && category.workspaces.length > 0 && (
                        <tr className="project-workspace-expand">
                          <td colSpan={8} style={{ padding: 0 }}>
                            <div className="p-2 ps-4">
                              <table className="table table-sm mb-0">
                                <thead>
                                  <tr>
                                    <th>{t('workspace', language)}</th>
                                    <th>{t('users', language)}</th>
                                    <th>{t('tokens', language)}</th>
                                    <th>{t('requests', language)}</th>
                                    <th>{t('workTime', language)}</th>
                                    <th>{t('lastActive', language)}</th>
                                    <th>{t('tableActions', language)}</th>
                                  </tr>
                                </thead>
                                <tbody>
                                  {category.workspaces.map((workspace) => (
                                    <tr key={workspace.project_id}>
                                      <td>
                                        <div className="d-flex align-items-center">
                                          <i className="bi bi-folder2-open text-info me-2" />
                                          <div>
                                            <strong>
                                              {workspace.project_name ??
                                                workspace.project_path.split(/[/\\]/).pop()}
                                            </strong>
                                            <small
                                              className="d-block text-muted font-monospace"
                                              style={{ maxWidth: '200px' }}
                                            >
                                              {workspace.project_path}
                                            </small>
                                          </div>
                                        </div>
                                      </td>
                                      <td>{workspace.total_users}</td>
                                      <td>{formatNumber(Number(workspace.total_tokens))}</td>
                                      <td>{formatNumber(workspace.total_requests)}</td>
                                      <td>{formatDuration(workspace.total_duration_seconds)}</td>
                                      <td>
                                        <small>
                                          {workspace.last_access
                                            ? formatDateTime(workspace.last_access)
                                            : t('never', language)}
                                        </small>
                                      </td>
                                      <td>
                                        <div className="d-flex gap-1">
                                          <Button
                                            variant="outline-primary"
                                            size="sm"
                                            onClick={(e) => {
                                              e?.stopPropagation();
                                              setSelectedWorkspace(workspace);
                                            }}
                                          >
                                            <i className="bi bi-eye me-1" />
                                            {t('viewDetails', language)}
                                          </Button>
                                          <Button
                                            variant="outline-danger"
                                            size="sm"
                                            onClick={(e) => {
                                              e?.stopPropagation();
                                              setDeleteTarget(workspace);
                                            }}
                                          >
                                            <i className="bi bi-trash me-1" />
                                          </Button>
                                        </div>
                                      </td>
                                    </tr>
                                  ))}
                                </tbody>
                              </table>
                            </div>
                          </td>
                        </tr>
                      )}
                    </React.Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      {/* Workspace Detail Modal */}
      <Modal
        isOpen={selectedWorkspace !== null}
        onClose={() => setSelectedWorkspace(null)}
        title={selectedWorkspace?.project_name ?? t('workspaceDetails', language)}
        size="lg"
      >
        {selectedWorkspace && (
          <WorkspaceDetailContent workspace={selectedWorkspace} formatDuration={formatDuration} />
        )}
      </Modal>

      {/* Delete Confirmation Modal */}
      <Modal
        isOpen={deleteTarget !== null}
        onClose={() => setDeleteTarget(null)}
        title={t('deleteProject', language)}
        size="md"
        footer={
          <>
            <Button variant="secondary" onClick={() => setDeleteTarget(null)}>
              {t('cancel', language)}
            </Button>
            <Button variant="danger" onClick={handleDelete} loading={isDeleting}>
              {t('delete', language)}
            </Button>
          </>
        }
      >
        <div className="alert alert-danger d-flex align-items-center">
          <i className="bi bi-exclamation-triangle me-2" />
          <div>
            {t('deleteProjectConfirm', language)}{' '}
            <strong>{deleteTarget?.project_name ?? deleteTarget?.project_path}</strong>?
            <br />
            <small className="text-muted">{t('deleteProjectWarning', language)}</small>
          </div>
        </div>
      </Modal>
    </div>
  );
};

// Workspace Detail Content Component
const WorkspaceDetailContent: React.FC<{
  workspace: ProjectStats;
  formatDuration: (seconds: number) => string;
}> = ({ workspace, formatDuration }) => {
  const language = useLanguage();

  const workspaceName = workspace.project_name ?? workspace.project_path.split(/[/\\]/).pop();

  return (
    <div className="space-y-4">
      {/* Workspace Header */}
      <div className="d-flex align-items-start gap-3">
        <div
          className="d-flex align-items-center justify-content-center rounded-3 flex-shrink-0 project-detail-icon-box"
          style={{ width: 48, height: 48 }}
        >
          <i className="bi bi-folder2-open-fill text-info fs-4" />
        </div>
        <div className="flex-grow-1 min-width-0">
          <h5 className="mb-1">{workspaceName}</h5>
          <div
            className="font-monospace text-muted small text-truncate"
            title={workspace.project_path}
          >
            {workspace.project_path}
          </div>
          <div className="d-flex gap-2 mt-2">
            <Badge variant="primary" pill>
              {t('users', language)}: {workspace.total_users}
            </Badge>
            <Badge variant="info" pill>
              {t('sessions', language)}: {workspace.total_sessions}
            </Badge>
          </div>
        </div>
      </div>

      <Divider />

      {/* Stats Grid */}
      <div className="row g-3">
        <div className="col-6 col-md-3">
          <div className="text-center p-3 rounded-3 project-detail-stat-box project-detail-stat-primary">
            <i className="bi bi-people text-primary d-block mb-1" />
            <div className="text-muted small">{t('users', language)}</div>
            <div className="fs-4 fw-bold text-primary">{workspace.total_users}</div>
          </div>
        </div>
        <div className="col-6 col-md-3">
          <div className="text-center p-3 rounded-3 project-detail-stat-box project-detail-stat-info">
            <i className="bi bi-chat-square-text text-info d-block mb-1" />
            <div className="text-muted small">{t('sessions', language)}</div>
            <div className="fs-4 fw-bold text-info">{workspace.total_sessions}</div>
          </div>
        </div>
        <div className="col-6 col-md-3">
          <div className="text-center p-3 rounded-3 project-detail-stat-box project-detail-stat-success">
            <i className="bi bi-cpu text-success d-block mb-1" />
            <div className="text-muted small">{t('tokens', language)}</div>
            <div className="fs-4 fw-bold text-success">
              {Number(workspace.total_tokens).toLocaleString()}
            </div>
          </div>
        </div>
        <div className="col-6 col-md-3">
          <div className="text-center p-3 rounded-3 project-detail-stat-box project-detail-stat-warning">
            <i className="bi bi-clock text-warning d-block mb-1" />
            <div className="text-muted small">{t('workTime', language)}</div>
            <div className="fs-4 fw-bold text-warning">
              {formatDuration(workspace.total_duration_seconds)}
            </div>
          </div>
        </div>
      </div>

      <Divider />

      {/* Time Range */}
      <div className="row g-3">
        <div className="col-6">
          <div className="d-flex align-items-center">
            <i className="bi bi-calendar-plus text-muted me-2" />
            <div>
              <div className="text-muted small">{t('firstAccess', language)}</div>
              <div className="fw-medium">
                {workspace.first_access
                  ? formatDateTime(workspace.first_access)
                  : t('never', language)}
              </div>
            </div>
          </div>
        </div>
        <div className="col-6">
          <div className="d-flex align-items-center">
            <i className="bi bi-calendar-check text-muted me-2" />
            <div>
              <div className="text-muted small">{t('lastAccess', language)}</div>
              <div className="fw-medium">
                {workspace.last_access
                  ? formatDateTime(workspace.last_access)
                  : t('never', language)}
              </div>
            </div>
          </div>
        </div>
      </div>

      <Divider />

      {/* Collaborators */}
      <div>
        <h6 className="mb-3 d-flex align-items-center">
          <i className="bi bi-people me-2" />
          {t('collaborators', language)}
          <Badge variant="secondary" pill className="ms-2">
            {workspace.user_stats.length}
          </Badge>
        </h6>
        {workspace.user_stats.length === 0 ? (
          <div className="text-muted text-center py-3">
            <i className="bi bi-person-x d-block fs-3 mb-1" />
            <small>{t('noCollaborators', language)}</small>
          </div>
        ) : (
          <div className="table-responsive">
            <table className="table table-sm table-hover align-middle">
              <thead>
                <tr>
                  <th>{t('tableUser', language)}</th>
                  <th className="text-center">{t('sessions', language)}</th>
                  <th className="text-center">{t('tokens', language)}</th>
                  <th className="text-center">{t('workTime', language)}</th>
                  <th>{t('lastAccess', language)}</th>
                </tr>
              </thead>
              <tbody>
                {workspace.user_stats.map((user) => (
                  <tr key={user.id}>
                    <td>
                      <div className="d-flex align-items-center">
                        <Avatar name={user.username ?? 'User'} size="xs" className="me-2" />
                        <strong>{user.username ?? `User ${user.user_id}`}</strong>
                      </div>
                    </td>
                    <td className="text-center">{user.total_sessions}</td>
                    <td className="text-center">{Number(user.total_tokens).toLocaleString()}</td>
                    <td className="text-center">{formatDuration(user.total_duration_seconds)}</td>
                    <td>
                      <small className="text-muted">{formatDateTime(user.last_access_at)}</small>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
};

export default ProjectManagement;
