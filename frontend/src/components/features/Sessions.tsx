/**
 * Sessions Component - Agent sessions list with filters and details
 */

import React, { useState, useMemo, useEffect } from 'react';
import { useSearchParams } from 'react-router-dom';
import { cn } from '@/utils';
import {
  useSessions,
  useSessionStats,
  useDeleteSession,
  useCompleteSession,
  useSession,
  useRestoreSession,
  useStopRemoteSession,
  usePauseRemoteSession,
  useResumeRemoteSession,
} from '@/hooks';
import { useLanguage } from '@/store';
import { t, type Language } from '@/i18n';
import {
  Card,
  Button,
  Select,
  Loading,
  Error,
  EmptyState,
  Badge,
  Modal,
  SessionDetailContent,
} from '@/components/common';
import type { BadgeVariant } from '@/components/common';
import { formatDateTime, formatTokens, formatToolName } from '@/utils';
import type { AgentSession, SessionFilters } from '@/api/sessions';

const ITEMS_PER_PAGE = 20;

// Status badge colors
const statusColors: Record<string, BadgeVariant> = {
  active: 'success',
  paused: 'warning',
  completed: 'secondary',
  archived: 'dark',
  error: 'danger',
};

// Status icons for better visibility
const statusIcons: Record<string, string> = {
  active: 'bi-circle-fill',
  paused: 'bi-pause-circle-fill',
  completed: 'bi-check-circle-fill',
  archived: 'bi-archive-fill',
  error: 'bi-exclamation-circle-fill',
};

// Session type badge colors
const typeColors: Record<string, BadgeVariant> = {
  chat: 'primary',
  task: 'info',
  workflow: 'warning',
  agent: 'success',
};

export const Sessions: React.FC = () => {
  const language = useLanguage();
  const [searchParams] = useSearchParams();
  const [filters, setFilters] = useState<SessionFilters>({});
  const [page, setPage] = useState(1);
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(null);
  const [showDetailModal, setShowDetailModal] = useState(false);
  const [isRefreshing, setIsRefreshing] = useState(false);

  // Queries
  const { data, isLoading, isFetching, isError, error, refetch } = useSessions({
    filters,
    pageSize: ITEMS_PER_PAGE,
    page,
  });

  const { data: statsData } = useSessionStats();

  // Fetch selected session details with messages
  const { data: sessionDetail, isLoading: isLoadingDetail } = useSession(
    selectedSessionId ?? '',
    true,
    !!selectedSessionId
  );

  // Mutations
  const deleteMutation = useDeleteSession();
  const completeMutation = useCompleteSession();
  const restoreMutation = useRestoreSession();
  const stopRemoteMutation = useStopRemoteSession();
  const pauseRemoteMutation = usePauseRemoteSession();
  const resumeRemoteMutation = useResumeRemoteSession();

  const sessions = data?.data?.sessions ?? [];

  // Status counts for quick filter pills
  const statusCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const s of sessions) {
      const st = s.status || 'active';
      counts[st] = (counts[st] || 0) + 1;
    }
    return counts;
  }, [sessions]);

  // Auto-select session from URL parameter
  useEffect(() => {
    const sessionId = searchParams.get('id');
    if (sessionId && sessions.length > 0) {
      const session = sessions.find((s: AgentSession) => s.session_id === sessionId);
      if (session && selectedSessionId !== sessionId) {
        setSelectedSessionId(sessionId);
        setShowDetailModal(true);
      }
    }
  }, [searchParams, sessions, selectedSessionId]);
  const pagination = data?.data
    ? {
        page: data.data.page,
        totalPages: data.data.total_pages,
        total: data.data.total,
      }
    : null;

  // Filter options
  const toolOptions = useMemo(
    () => [
      { value: '', label: t('dashboardFilterAllTools', language) },
      { value: 'openclaw', label: 'OpenClaw' },
      { value: 'claude', label: 'Claude' },
      { value: 'qwen', label: 'Qwen' },
    ],
    [language]
  );

  const statusOptions = useMemo(
    () => [
      { value: '', label: t('allStatus', language) ?? 'All Status' },
      { value: 'active', label: t('statusActive', language) ?? 'Active' },
      { value: 'paused', label: t('statusPaused', language) ?? 'Paused' },
      { value: 'completed', label: t('statusCompleted', language) ?? 'Completed' },
      { value: 'archived', label: t('statusArchived', language) ?? 'Archived' },
      { value: 'error', label: t('statusError', language) ?? 'Error' },
    ],
    [language]
  );

  const typeOptions = useMemo(
    () => [
      { value: '', label: t('allTypes', language) ?? 'All Types' },
      { value: 'chat', label: 'Chat' },
      { value: 'task', label: 'Task' },
      { value: 'workflow', label: 'Workflow' },
      { value: 'agent', label: 'Agent' },
    ],
    [language]
  );

  // Handlers
  const handleFilterChange = (key: keyof SessionFilters, value: string) => {
    setFilters((prev) => ({ ...prev, [key]: value || undefined }));
    setPage(1);
  };

  const handleReset = () => {
    setFilters({});
    setPage(1);
  };

  // Refresh handler - triggers backend data fetch and then refreshes frontend
  const handleRefresh = async () => {
    setIsRefreshing(true);
    try {
      // Trigger backend data fetch (fetch_qwen.py)
      const response = await fetch('/api/fetch/data', { method: 'POST' });
      const result = await response.json();

      if (result.success) {
        // Wait for backend fetch to complete (poll status)
        let attempts = 0;
        const maxAttempts = 30; // 30 seconds max
        while (attempts < maxAttempts) {
          await new Promise((resolve) => setTimeout(resolve, 1000));
          const statusResponse = await fetch('/api/fetch/status');
          const statusResult = await statusResponse.json();

          if (!statusResult.status?.is_running) {
            break;
          }
          attempts++;
        }

        // Refresh frontend data
        await refetch();
      } else {
        console.warn('Backend fetch already running or failed:', result.message);
        // Still refresh frontend data
        await refetch();
      }
    } catch (error) {
      console.error('Failed to trigger backend refresh:', error);
      // Still refresh frontend data
      await refetch();
    } finally {
      setIsRefreshing(false);
    }
  };

  const handleDelete = async (sessionId: string) => {
    if (
      window.confirm(
        t('confirmDeleteSession', language) ?? 'Are you sure you want to delete this session?'
      )
    ) {
      await deleteMutation.mutateAsync(sessionId);
      if (selectedSessionId === sessionId) {
        setSelectedSessionId(null);
        setShowDetailModal(false);
      }
    }
  };

  const handleComplete = async (sessionId: string) => {
    await completeMutation.mutateAsync(sessionId);
  };

  const handleSessionClick = (sessionId: string) => {
    setSelectedSessionId(sessionId);
    setShowDetailModal(true);
  };

  const handleRestore = async (sessionId: string) => {
    await restoreMutation.mutateAsync(sessionId);
  };

  const handleCloseModal = () => {
    setShowDetailModal(false);
    setSelectedSessionId(null);
  };

  if (isError) {
    return <Error message={error?.message ?? t('error', language)} onRetry={() => refetch()} />;
  }

  return (
    <div className="sessions">
      {/* Header */}
      <div className="sessions-header d-flex justify-content-between align-items-center mb-4">
        <h2>{t('sessions', language)}</h2>
        <div className="d-flex gap-2 align-items-center">
          <Button
            variant="primary"
            size="sm"
            onClick={handleRefresh}
            loading={isRefreshing || isFetching}
            icon={isRefreshing || isFetching ? undefined : <i className="bi bi-arrow-clockwise" />}
          >
            {t('refresh', language)}
          </Button>
        </div>
      </div>

      {/* Stats Cards */}
      {statsData?.success && statsData.data && (
        <div className="row mb-4">
          <div className="col-md-3 col-sm-6 mb-2">
            <Card className="text-center">
              <div className="text-muted small">
                {t('totalSessions', language) ?? 'Total Sessions'}
              </div>
              <div className="fs-3 fw-bold text-primary">{statsData.data.total_sessions}</div>
            </Card>
          </div>
          <div className="col-md-3 col-sm-6 mb-2">
            <Card className="text-center">
              <div className="text-muted small">
                {t('activeSessions', language) ?? 'Active Sessions'}
              </div>
              <div className="fs-3 fw-bold text-success">{statsData.data.active_sessions}</div>
            </Card>
          </div>
          <div className="col-md-3 col-sm-6 mb-2">
            <Card className="text-center">
              <div className="text-muted small">{t('totalMessages', language)}</div>
              <div className="fs-3 fw-bold text-info">{statsData.data.total_messages}</div>
            </Card>
          </div>
          <div className="col-md-3 col-sm-6 mb-2">
            <Card className="text-center">
              <div className="text-muted small">{t('totalTokens', language)}</div>
              <div className="fs-3 fw-bold text-warning">
                {formatTokens(statsData.data.total_tokens)}
              </div>
            </Card>
          </div>
        </div>
      )}

      {/* Filters */}
      <Card className="mb-3 sessions-filter-card">
        <div className="sessions-filter-row">
          <div className="sessions-filter-group">
            <label className="sessions-filter-label">{t('tableTool', language)}:</label>
            <Select
              options={toolOptions}
              value={filters.tool_name ?? ''}
              onChange={(value) => handleFilterChange('tool_name', value)}
              size="sm"
              className="sessions-filter-select"
            />
          </div>
          <div className="sessions-filter-group">
            <label className="sessions-filter-label">{t('status', language) ?? 'Status'}:</label>
            <Select
              options={statusOptions}
              value={filters.status ?? ''}
              onChange={(value) => handleFilterChange('status', value)}
              size="sm"
              className="sessions-filter-select"
            />
          </div>
          {/* Status count pills */}
          {Object.entries(statusCounts).length > 0 && (
            <div className="sessions-filter-counts">
              {Object.entries(statusCounts).map(([st, count]) => (
                <span
                  key={st}
                  className={`badge bg-${statusColors[st] ?? 'secondary'}`}
                  style={{
                    fontSize: '0.7rem',
                    cursor: 'pointer',
                    opacity: filters.status === st ? 1 : 0.7,
                  }}
                  onClick={() => handleFilterChange('status', filters.status === st ? '' : st)}
                >
                  {count}
                </span>
              ))}
            </div>
          )}
          <div className="sessions-filter-group">
            <label className="sessions-filter-label">{t('type', language) ?? 'Type'}:</label>
            <Select
              options={typeOptions}
              value={filters.session_type ?? ''}
              onChange={(value) => handleFilterChange('session_type', value)}
              size="sm"
              className="sessions-filter-select"
            />
          </div>
          <div className="sessions-filter-search">
            <div className="input-group input-group-sm">
              <span className="input-group-text">
                <i className="bi bi-search" />
              </span>
              <input
                type="text"
                className="form-control"
                placeholder={t('search', language)}
                value={filters.search ?? ''}
                onChange={(e) => handleFilterChange('search', e.target.value)}
              />
            </div>
          </div>
          <Button variant="outline-secondary" size="sm" onClick={handleReset}>
            <i className="bi bi-x-circle me-1" />
            {t('reset', language)}
          </Button>
        </div>
      </Card>

      {/* Sessions List */}
      {isLoading ? (
        <Loading size="lg" text={t('loading', language)} />
      ) : sessions.length === 0 ? (
        <EmptyState
          icon="bi-chat-dots"
          title={t('noData', language)}
          description={
            t('noAgentSessions', language) ??
            'No agent sessions found. Sessions are created when using AI tools in Workspace.'
          }
        />
      ) : (
        <>
          <div className="sessions-list">
            {sessions.map((session) => (
              <SessionCard
                key={session.session_id}
                session={session}
                language={language}
                isSelected={selectedSessionId === session.session_id}
                onClick={() => handleSessionClick(session.session_id)}
                onDelete={handleDelete}
                onComplete={handleComplete}
                onRestore={handleRestore}
                onStopRemote={(id) => stopRemoteMutation.mutate(id)}
                onPauseRemote={(id) => pauseRemoteMutation.mutate(id)}
                onResumeRemote={(id) => resumeRemoteMutation.mutate(id)}
                isDeleting={deleteMutation.isPending}
                isCompleting={completeMutation.isPending}
                isRestoring={restoreMutation.isPending}
                isRemoteControlPending={
                  stopRemoteMutation.isPending ||
                  pauseRemoteMutation.isPending ||
                  resumeRemoteMutation.isPending
                }
              />
            ))}
          </div>

          {/* Pagination */}
          {pagination && pagination.totalPages > 1 && (
            <div className="d-flex justify-content-center mt-4">
              <nav>
                <ul className="pagination">
                  <li className={cn('page-item', page === 1 && 'disabled')}>
                    <button
                      className="page-link"
                      onClick={() => setPage(page - 1)}
                      disabled={page === 1}
                    >
                      {t('previous', language) ?? 'Previous'}
                    </button>
                  </li>
                  {Array.from({ length: Math.min(5, pagination.totalPages) }, (_, i) => {
                    const pageNum = i + 1;
                    return (
                      <li key={pageNum} className={cn('page-item', page === pageNum && 'active')}>
                        <button className="page-link" onClick={() => setPage(pageNum)}>
                          {pageNum}
                        </button>
                      </li>
                    );
                  })}
                  <li className={cn('page-item', page === pagination.totalPages && 'disabled')}>
                    <button
                      className="page-link"
                      onClick={() => setPage(page + 1)}
                      disabled={page === pagination.totalPages}
                    >
                      {t('next', language) ?? 'Next'}
                    </button>
                  </li>
                </ul>
              </nav>
            </div>
          )}

          {/* Total count */}
          {pagination && (
            <div className="text-center text-muted mt-2">
              {t('total', language)}: {pagination.total.toLocaleString()} {t('sessions', language)}
            </div>
          )}
        </>
      )}

      {/* Session Detail Modal */}
      <Modal
        isOpen={showDetailModal}
        onClose={handleCloseModal}
        title={(() => {
          const sessionIdShort = sessionDetail?.data?.session_id?.slice(0, 8) ?? '';
          const sessionTitle = sessionDetail?.data?.title ?? '';
          // Check if title is meaningful (not just a default pattern like "qwen - 994f805a")
          const isDefaultTitle =
            sessionTitle.includes(sessionIdShort) || sessionTitle.match(/^[a-z]+ - [a-f0-9]{8}$/i);
          // Format: "994f805a（会话名字）" or just "994f805a"
          if (sessionTitle && !isDefaultTitle) {
            return `${sessionIdShort}（${sessionTitle}）`;
          }
          return sessionIdShort || (t('sessionDetails', language) ?? 'Session Details');
        })()}
        size="lg"
      >
        {isLoadingDetail ? (
          <Loading size="sm" text={t('loading', language)} />
        ) : sessionDetail?.data ? (
          <SessionDetailContent
            session={sessionDetail.data}
            language={language}
            onRestore={handleRestore}
            restorePending={restoreMutation.isPending}
          />
        ) : (
          <div className="text-muted">{t('noData', language)}</div>
        )}
      </Modal>
    </div>
  );
};

/**
 * Session Card Component
 */
interface SessionCardProps {
  session: AgentSession;
  language: Language;
  isSelected: boolean;
  onClick: () => void;
  onDelete: (sessionId: string) => void;
  onComplete: (sessionId: string) => void;
  onRestore: (sessionId: string) => void;
  onStopRemote: (sessionId: string) => void;
  onPauseRemote: (sessionId: string) => void;
  onResumeRemote: (sessionId: string) => void;
  isDeleting: boolean;
  isCompleting: boolean;
  isRestoring: boolean;
  isRemoteControlPending: boolean;
}

const SessionCard: React.FC<SessionCardProps> = ({
  session,
  language,
  isSelected,
  onClick,
  onDelete,
  onComplete,
  onRestore,
  onStopRemote,
  onPauseRemote,
  onResumeRemote,
  isDeleting,
  isCompleting,
  isRestoring,
  isRemoteControlPending,
}) => {
  const isRemote = session.workspace_type === 'remote';
  return (
    <div
      className={cn('session-item card mb-2', isSelected && 'border-primary')}
      onClick={onClick}
      style={{ cursor: 'pointer' }}
    >
      <div className="card-body session-card-body">
        {/* Row 1: Title | Badges | Actions — three-column grid */}
        <div className="session-card-header">
          {/* Left: Title + ID */}
          <div className="session-card-title">
            <h5 className="mb-0" style={{ fontSize: '0.95rem', fontWeight: 600 }}>
              {session.title ?? session.session_id.substring(0, 8)}
            </h5>
            <code className="text-muted" style={{ fontSize: '0.72rem' }}>
              {session.session_id.substring(0, 8)}
            </code>
          </div>

          {/* Center: Badges */}
          <div className="session-card-badges">
            <Badge variant={statusColors[session.status] ?? 'secondary'}>
              {statusIcons[session.status] && (
                <i className={`bi ${statusIcons[session.status]} me-1`} />
              )}
              {session.status}
            </Badge>
            <Badge variant={typeColors[session.session_type] ?? 'secondary'}>
              {session.session_type}
            </Badge>
            {isRemote && (
              <Badge variant="info">
                <i className="bi bi-cloud-fill me-1" />
                {session.machine_name ?? 'Remote'}
              </Badge>
            )}
          </div>

          {/* Right: Actions */}
          <div className="session-card-actions" onClick={(e) => e.stopPropagation()}>
            <span title={t('restoreToWorkspace', language) ?? 'Restore to Workspace'}>
              <Button
                variant="outline-primary"
                size="sm"
                onClick={() => onRestore(session.session_id)}
                loading={isRestoring}
              >
                <i className="bi bi-box-arrow-in-right" />
              </Button>
            </span>
            {isRemote && session.status === 'active' && (
              <>
                <span title={t('pauseSession', language)}>
                  <Button
                    variant="outline-warning"
                    size="sm"
                    onClick={() => onPauseRemote(session.session_id)}
                    loading={isRemoteControlPending}
                  >
                    <i className="bi bi-pause-fill" />
                  </Button>
                </span>
                <span title={t('stopSession', language)}>
                  <Button
                    variant="outline-danger"
                    size="sm"
                    onClick={() => onStopRemote(session.session_id)}
                    loading={isRemoteControlPending}
                  >
                    <i className="bi bi-stop-fill" />
                  </Button>
                </span>
              </>
            )}
            {isRemote && session.status === 'paused' && (
              <span title={t('resumeSession', language)}>
                <Button
                  variant="outline-success"
                  size="sm"
                  onClick={() => onResumeRemote(session.session_id)}
                  loading={isRemoteControlPending}
                >
                  <i className="bi bi-play-fill" />
                </Button>
              </span>
            )}
            {!isRemote && session.status === 'active' && (
              <span title={t('complete', language) ?? 'Complete'}>
                <Button
                  variant="outline-success"
                  size="sm"
                  onClick={() => onComplete(session.session_id)}
                  loading={isCompleting}
                >
                  <i className="bi bi-check-circle" />
                </Button>
              </span>
            )}
            <span title={t('delete', language)}>
              <Button
                variant="outline-danger"
                size="sm"
                onClick={() => onDelete(session.session_id)}
                loading={isDeleting}
              >
                <i className="bi bi-trash" />
              </Button>
            </span>
          </div>
        </div>

        {/* Row 2: Meta info — grid aligned columns */}
        <div className="session-card-meta">
          <span>
            <i className="bi bi-tools me-1" />
            {formatToolName(session.tool_name)}
          </span>
          <span>
            <i className="bi bi-pc-display me-1" />
            {session.host_name}
          </span>
          <span className="meta-model">
            {session.model && (
              <>
                <i className="bi bi-cpu me-1" />
                {session.model}
              </>
            )}
          </span>
          <span>
            <i className="bi bi-chat-dots me-1" />
            {session.message_count} {t('messages', language)}
          </span>
          <span>
            <i className="bi bi-cpu me-1" />
            {formatTokens(session.total_tokens)} {t('tokens', language)}
          </span>
          <span>
            <i className="bi bi-clock me-1" />
            {session.created_at ? formatDateTime(session.created_at) : '-'}
          </span>
          {session.completed_at && (
            <span>
              <i className="bi bi-check-circle me-1" />
              {formatDateTime(session.completed_at)}
            </span>
          )}
        </div>
      </div>
    </div>
  );
};
