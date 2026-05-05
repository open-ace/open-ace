/**
 * ProjectManagement Component - Project management page
 *
 * Features:
 * - Project list with statistics
 * - View project details
 * - Delete projects
 */

import React, { useState, useEffect, useMemo } from 'react';
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
} from '@/components/common';
import { getAllProjectStats, deleteProject, type ProjectStats } from '@/api/projects';
import { formatDateTime } from '@/utils';

export const ProjectManagement: React.FC = () => {
  const language = useLanguage();
  const [stats, setStats] = useState<ProjectStats[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedProject, setSelectedProject] = useState<ProjectStats | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<ProjectStats | null>(null);
  const [isDeleting, setIsDeleting] = useState(false);

  const fetchStats = async () => {
    setIsLoading(true);
    setError(null);
    try {
      const response = await getAllProjectStats();
      setStats(response.stats || []);
    } catch (err) {
      const errorMessage =
        err instanceof Error ? (err as Error).message : 'Failed to load project stats';
      setError(errorMessage);
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    fetchStats();
  }, []);

  const handleDelete = async () => {
    if (!deleteTarget) return;

    setIsDeleting(true);
    try {
      await deleteProject(deleteTarget.project_id);
      await fetchStats();
      setDeleteTarget(null);
    } catch (err) {
      console.error('Failed to delete project:', err);
    } finally {
      setIsDeleting(false);
    }
  };

  // Calculate summary statistics
  const summary = useMemo(() => {
    const totalProjects = stats.length;
    const totalUsers = stats.reduce((sum, p) => sum + p.total_users, 0);
    const totalTokens = stats.reduce((sum, p) => sum + Number(p.total_tokens), 0);
    const totalDuration = stats.reduce((sum, p) => sum + p.total_duration_seconds, 0);
    return { totalProjects, totalUsers, totalTokens, totalDuration };
  }, [stats]);

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
    return <Error message={error} onRetry={fetchStats} />;
  }

  return (
    <div className="space-y-4">
      {/* Page Header */}
      <div className="page-header d-flex justify-content-between align-items-center mb-4">
        <h2>{t('projectManagement', language)}</h2>
      </div>

      {/* Summary Cards */}
      <div className="row g-3 mb-4">
        <div className="col-md-3">
          <StatCard
            icon={<i className="bi bi-folder fs-4" />}
            label={t('totalProjects', language)}
            value={summary.totalProjects}
            variant="primary"
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
        <div className="col-md-3">
          <StatCard
            icon={<i className="bi bi-clock fs-4" />}
            label={t('totalWorkTime', language)}
            value={formatDuration(summary.totalDuration)}
            variant="warning"
          />
        </div>
      </div>

      {/* Project List */}
      {stats.length === 0 ? (
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
              {t('projects', language)}
            </h5>
            <Button variant="primary" size="sm" onClick={fetchStats}>
              <i className="bi bi-arrow-clockwise me-1" />
              {t('refresh', language)}
            </Button>
          </div>

          <div className="table-responsive">
            <table className="table table-hover">
              <thead>
                <tr>
                  <th>{t('project', language)}</th>
                  <th>{t('users', language)}</th>
                  <th>{t('tokens', language)}</th>
                  <th>{t('requests', language)}</th>
                  <th>{t('workTime', language)}</th>
                  <th>{t('lastActive', language)}</th>
                  <th>{t('tableActions', language)}</th>
                </tr>
              </thead>
              <tbody>
                {stats.map((project) => (
                  <tr key={project.project_id}>
                    <td>
                      <div className="d-flex align-items-center">
                        <i className="bi bi-folder text-warning me-2" />
                        <div>
                          <strong>
                            {project.project_name || project.project_path.split(/[/\\]/).pop()}
                          </strong>
                          <small className="d-block text-muted font-monospace" style={{ maxWidth: '250px' }}>
                            {project.project_path}
                          </small>
                        </div>
                      </div>
                    </td>
                    <td>
                      <div className="d-flex align-items-center">
                        <i className="bi bi-person text-muted me-1" />
                        {project.total_users}
                      </div>
                    </td>
                    <td>{formatNumber(Number(project.total_tokens))}</td>
                    <td>{formatNumber(project.total_requests)}</td>
                    <td>{formatDuration(project.total_duration_seconds)}</td>
                    <td>
                      <small>
                        {project.last_access
                          ? formatDateTime(project.last_access)
                          : t('never', language)}
                      </small>
                    </td>
                    <td>
                      <div className="d-flex gap-1">
                        <Button
                          variant="outline-primary"
                          size="sm"
                          onClick={() => setSelectedProject(project)}
                        >
                          <i className="bi bi-eye me-1" />
                          {t('viewDetails', language)}
                        </Button>
                        <Button
                          variant="outline-danger"
                          size="sm"
                          onClick={() => setDeleteTarget(project)}
                        >
                          <i className="bi bi-trash me-1" />
                          {t('delete', language)}
                        </Button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      {/* Project Detail Modal */}
      <Modal
        isOpen={selectedProject !== null}
        onClose={() => setSelectedProject(null)}
        title={selectedProject?.project_name || t('projectDetails', language)}
        size="lg"
      >
        {selectedProject && (
          <ProjectDetailContent project={selectedProject} formatDuration={formatDuration} />
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
            <strong>{deleteTarget?.project_name || deleteTarget?.project_path}</strong>?
            <br />
            <small className="text-muted">
              {t('deleteProjectWarning', language)}
            </small>
          </div>
        </div>
      </Modal>
    </div>
  );
};

// Project Detail Content Component
const ProjectDetailContent: React.FC<{
  project: ProjectStats;
  formatDuration: (seconds: number) => string;
}> = ({ project, formatDuration }) => {
  const language = useLanguage();

  const projectName = project.project_name || project.project_path.split(/[/\\]/).pop();

  return (
    <div className="space-y-4">
      {/* Project Header */}
      <div className="d-flex align-items-start gap-3">
        <div
          className="d-flex align-items-center justify-content-center rounded-3 flex-shrink-0"
          style={{ width: 48, height: 48, backgroundColor: 'var(--bs-warning-bg-subtle, #fff3cd)' }}
        >
          <i className="bi bi-folder-fill text-warning fs-4" />
        </div>
        <div className="flex-grow-1 min-width-0">
          <h5 className="mb-1">{projectName}</h5>
          <div className="font-monospace text-muted small text-truncate" title={project.project_path}>
            {project.project_path}
          </div>
          <div className="d-flex gap-2 mt-2">
            <Badge variant="primary" pill>{t('users', language)}: {project.total_users}</Badge>
            <Badge variant="info" pill>{t('sessions', language)}: {project.total_sessions}</Badge>
          </div>
        </div>
      </div>

      <Divider />

      {/* Stats Grid */}
      <div className="row g-3">
        <div className="col-6 col-md-3">
          <div className="text-center p-3 rounded-3" style={{ backgroundColor: 'var(--bs-primary-bg-subtle, #cfe2ff)' }}>
            <i className="bi bi-people text-primary d-block mb-1" />
            <div className="text-muted small">{t('users', language)}</div>
            <div className="fs-4 fw-bold text-primary">{project.total_users}</div>
          </div>
        </div>
        <div className="col-6 col-md-3">
          <div className="text-center p-3 rounded-3" style={{ backgroundColor: 'var(--bs-info-bg-subtle, #cff4fc)' }}>
            <i className="bi bi-chat-square-text text-info d-block mb-1" />
            <div className="text-muted small">{t('sessions', language)}</div>
            <div className="fs-4 fw-bold text-info">{project.total_sessions}</div>
          </div>
        </div>
        <div className="col-6 col-md-3">
          <div className="text-center p-3 rounded-3" style={{ backgroundColor: 'var(--bs-success-bg-subtle, #d1e7dd)' }}>
            <i className="bi bi-cpu text-success d-block mb-1" />
            <div className="text-muted small">{t('tokens', language)}</div>
            <div className="fs-4 fw-bold text-success">{Number(project.total_tokens).toLocaleString()}</div>
          </div>
        </div>
        <div className="col-6 col-md-3">
          <div className="text-center p-3 rounded-3" style={{ backgroundColor: 'var(--bs-warning-bg-subtle, #fff3cd)' }}>
            <i className="bi bi-clock text-warning d-block mb-1" />
            <div className="text-muted small">{t('workTime', language)}</div>
            <div className="fs-4 fw-bold text-warning">{formatDuration(project.total_duration_seconds)}</div>
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
                {project.first_access
                  ? formatDateTime(project.first_access)
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
                {project.last_access
                  ? formatDateTime(project.last_access)
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
          <Badge variant="secondary" pill className="ms-2">{project.user_stats.length}</Badge>
        </h6>
        {project.user_stats.length === 0 ? (
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
                {project.user_stats.map((user) => (
                  <tr key={user.id}>
                    <td>
                      <div className="d-flex align-items-center">
                        <Avatar name={user.username || 'User'} size="xs" className="me-2" />
                        <strong>{user.username || `User ${user.user_id}`}</strong>
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
