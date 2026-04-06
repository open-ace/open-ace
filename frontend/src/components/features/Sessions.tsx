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
} from '@/components/common';
import type { BadgeVariant } from '@/components/common';
import { formatDateTime, formatTokens } from '@/utils';
import type { AgentSession, SessionFilters, SessionMessage } from '@/api/sessions';

const ITEMS_PER_PAGE = 20;

// Status badge colors
const statusColors: Record<string, BadgeVariant> = {
  active: 'success',
  paused: 'warning',
  completed: 'secondary',
  archived: 'dark',
  error: 'danger',
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

  const sessions = data?.data?.sessions ?? [];

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
            onClick={() => refetch()}
            loading={isFetching}
            icon={isFetching ? undefined : <i className="bi bi-arrow-clockwise" />}
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
      <Card className="mb-3">
        <div className="d-flex flex-wrap align-items-center gap-2">
          {/* Tool Filter */}
          <div className="d-flex align-items-center gap-1">
            <small className="text-muted">{t('tableTool', language)}:</small>
            <Select
              options={toolOptions}
              value={filters.tool_name ?? ''}
              onChange={(value) => handleFilterChange('tool_name', value)}
              size="sm"
            />
          </div>
          {/* Status Filter */}
          <div className="d-flex align-items-center gap-1">
            <small className="text-muted">{t('status', language) ?? 'Status'}:</small>
            <Select
              options={statusOptions}
              value={filters.status ?? ''}
              onChange={(value) => handleFilterChange('status', value)}
              size="sm"
            />
          </div>
          {/* Type Filter */}
          <div className="d-flex align-items-center gap-1">
            <small className="text-muted">{t('type', language) ?? 'Type'}:</small>
            <Select
              options={typeOptions}
              value={filters.session_type ?? ''}
              onChange={(value) => handleFilterChange('session_type', value)}
              size="sm"
            />
          </div>
          {/* Search */}
          <div className="d-flex align-items-center gap-1 ms-auto" style={{ minWidth: '250px' }}>
            <div className="input-group input-group-sm" style={{ width: '200px' }}>
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
          {/* Reset Button */}
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
                isDeleting={deleteMutation.isPending}
                isCompleting={completeMutation.isPending}
                isRestoring={restoreMutation.isPending}
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
        title={sessionDetail?.data?.title ?? t('sessionDetails', language) ?? 'Session Details'}
        size="lg"
      >
        {isLoadingDetail ? (
          <Loading size="sm" text={t('loading', language)} />
        ) : sessionDetail?.data ? (
          <SessionDetailContent session={sessionDetail.data} language={language} />
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
  isDeleting: boolean;
  isCompleting: boolean;
  isRestoring: boolean;
}

const SessionCard: React.FC<SessionCardProps> = ({
  session,
  language,
  isSelected,
  onClick,
  onDelete,
  onComplete,
  onRestore,
  isDeleting,
  isCompleting,
  isRestoring,
}) => {
  return (
    <div
      className={cn('session-item card mb-2', isSelected && 'border-primary')}
      onClick={onClick}
      style={{ cursor: 'pointer' }}
    >
      <div className="card-body py-4 px-4">
        {/* Header Row - Spacious Layout */}
        <div className="d-flex justify-content-between align-items-start">
          <div className="flex-grow-1">
            {/* Title and Badges */}
            <div className="d-flex align-items-center gap-3 mb-3">
              <h5 className="mb-0" style={{ fontSize: '1.1rem', fontWeight: 600 }}>
                {session.title ?? session.session_id.substring(0, 8)}
              </h5>
              <Badge variant={statusColors[session.status] ?? 'secondary'}>{session.status}</Badge>
              <Badge variant={typeColors[session.session_type] ?? 'secondary'}>
                {session.session_type}
              </Badge>
            </div>

            {/* Meta Info - Two rows for better spacing */}
            <div className="d-flex flex-wrap gap-4 text-muted" style={{ fontSize: '0.875rem' }}>
              <span>
                <i className="bi bi-tools me-2" />
                {session.tool_name}
              </span>
              <span>
                <i className="bi bi-pc-display me-2" />
                {session.host_name}
              </span>
              {session.model && (
                <span>
                  <i className="bi bi-cpu me-2" />
                  {session.model}
                </span>
              )}
              <span>
                <i className="bi bi-chat-dots me-2" />
                {session.message_count} {t('messages', language)}
              </span>
              <span>
                <i className="bi bi-cpu me-2" />
                {formatTokens(session.total_tokens)} {t('tokens', language)}
              </span>
            </div>
          </div>

          {/* Actions */}
          <div className="d-flex flex-column gap-2 ms-4" onClick={(e) => e.stopPropagation()}>
            {/* Restore to Workspace button - always show for completed sessions */}
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
            {session.status === 'active' && (
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

        {/* Timestamps - Two separate lines */}
        <div className="d-flex flex-column gap-2 text-muted mt-4" style={{ fontSize: '0.8rem' }}>
          <span>
            <i className="bi bi-clock me-2" />
            {t('created', language) ?? 'Created'}:{' '}
            {session.created_at ? formatDateTime(session.created_at) : '-'}
          </span>
          {session.completed_at && (
            <span>
              <i className="bi bi-check-circle me-2" />
              {t('completed', language) ?? 'Completed'}: {formatDateTime(session.completed_at)}
            </span>
          )}
        </div>
      </div>
    </div>
  );
};

/**
 * Session Detail Content Component (for Modal)
 */
interface SessionDetailContentProps {
  session: AgentSession;
  language: Language;
}

const SessionDetailContent: React.FC<SessionDetailContentProps> = ({ session, language }) => {
  // Filter state: default to show user and assistant messages
  const [showUser, setShowUser] = useState(true);
  const [showAssistant, setShowAssistant] = useState(true);
  const [showSystem, setShowSystem] = useState(false);
  const [searchText, setSearchText] = useState('');

  // Filter messages based on role and search text
  const filteredMessages = useMemo(() => {
    if (!session.messages) return [];

    return session.messages.filter((msg: SessionMessage) => {
      // Role filter
      const roleMatch =
        (showUser && msg.role === 'user') ||
        (showAssistant && msg.role === 'assistant') ||
        (showSystem && msg.role === 'system');

      // Search filter (case-insensitive)
      const searchMatch =
        !searchText || msg.content.toLowerCase().includes(searchText.toLowerCase());

      return roleMatch && searchMatch;
    });
  }, [session.messages, showUser, showAssistant, showSystem, searchText]);

  // Highlight search text in content
  const highlightText = (text: string, search: string) => {
    if (!search) return text;
    const lowerText = text.toLowerCase();
    const lowerSearch = search.toLowerCase();
    const index = lowerText.indexOf(lowerSearch);
    if (index === -1) return text;
    return (
      text.slice(0, index) +
      '<mark>' +
      text.slice(index, index + search.length) +
      '</mark>' +
      text.slice(index + search.length)
    );
  };

  return (
    <div className="session-detail-content">
      {/* Session Meta Info */}
      <div className="session-meta mb-3 p-3 bg-light rounded">
        <div className="row g-3">
          <div className="col-md-6">
            <small className="text-muted d-block">{t('tableTool', language)}</small>
            <span>{session.tool_name}</span>
          </div>
          <div className="col-md-6">
            <small className="text-muted d-block">{t('status', language) ?? 'Status'}</small>
            <Badge
              variant={
                session.status === 'active'
                  ? 'success'
                  : session.status === 'completed'
                    ? 'secondary'
                    : 'warning'
              }
            >
              {session.status}
            </Badge>
          </div>
          <div className="col-md-6">
            <small className="text-muted d-block">{t('totalRequests', language)}</small>
            <span>{session.message_count}</span>
          </div>
          <div className="col-md-6">
            <small className="text-muted d-block">{t('totalTokens', language)}</small>
            <span>{formatTokens(session.total_tokens)}</span>
          </div>
          <div className="col-md-6">
            <small className="text-muted d-block">{t('created', language) ?? 'Created'}</small>
            <span>{session.created_at ? formatDateTime(session.created_at) : '-'}</span>
          </div>
          <div className="col-md-6">
            <small className="text-muted d-block">{t('model', language) ?? 'Model'}</small>
            <span>{session.model ?? '-'}</span>
          </div>
        </div>
      </div>

      {/* Filter and Search */}
      <div className="mb-3">
        {/* Role filters */}
        <div className="d-flex gap-2 mb-2">
          <button
            type="button"
            className={`btn btn-sm ${showUser ? 'btn-primary' : 'btn-outline-primary'}`}
            onClick={() => setShowUser(!showUser)}
          >
            {t('user', language) ?? 'User'}
          </button>
          <button
            type="button"
            className={`btn btn-sm ${showAssistant ? 'btn-success' : 'btn-outline-success'}`}
            onClick={() => setShowAssistant(!showAssistant)}
          >
            {t('assistant', language) ?? 'Assistant'}
          </button>
          <button
            type="button"
            className={`btn btn-sm ${showSystem ? 'btn-secondary' : 'btn-outline-secondary'}`}
            onClick={() => setShowSystem(!showSystem)}
          >
            {t('system', language) ?? 'System'}
          </button>
        </div>
        {/* Search input */}
        <div className="input-group input-group-sm">
          <input
            type="text"
            className="form-control"
            placeholder={t('searchMessages', language) ?? 'Search messages...'}
            value={searchText}
            onChange={(e) => setSearchText(e.target.value)}
          />
          {searchText && (
            <button
              type="button"
              className="btn btn-outline-secondary"
              onClick={() => setSearchText('')}
            >
              <i className="bi bi-x-lg"></i>
            </button>
          )}
        </div>
      </div>

      {/* Messages */}
      <h6 className="mb-2">
        {t('messages', language)}
        {filteredMessages.length !== session.message_count &&
          ` (${filteredMessages.length}/${session.message_count})`}
      </h6>
      <div className="messages-container" style={{ maxHeight: '400px', overflowY: 'auto' }}>
        {filteredMessages.length > 0 ? (
          filteredMessages.map((msg: SessionMessage, idx: number) => (
            <div
              key={msg.id ?? idx}
              className={`message-item p-2 mb-2 rounded ${msg.role === 'user' ? 'bg-light' : 'bg-white border'}`}
            >
              <div className="d-flex justify-content-between align-items-center mb-1">
                <Badge
                  variant={
                    msg.role === 'user'
                      ? 'primary'
                      : msg.role === 'assistant'
                        ? 'success'
                        : 'secondary'
                  }
                >
                  {msg.role}
                </Badge>
                <small className="text-muted">
                  {msg.timestamp ? formatDateTime(msg.timestamp) : ''}
                  {msg.tokens_used > 0 && ` • ${formatTokens(msg.tokens_used)} tokens`}
                </small>
              </div>
              <div
                className="message-content"
                style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}
                dangerouslySetInnerHTML={{ __html: highlightText(msg.content, searchText) }}
              />
            </div>
          ))
        ) : (
          <div className="text-muted text-center py-3">
            {searchText || !(showUser || showAssistant || showSystem)
              ? (t('noMatchingMessages', language) ?? 'No matching messages')
              : (t('noMessages', language) ?? 'No messages in this session')}
          </div>
        )}
      </div>
    </div>
  );
};
