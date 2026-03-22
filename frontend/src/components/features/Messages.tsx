/**
 * Messages Component - Messages list with filters
 */

import React, { useState, useMemo } from 'react';
import { cn } from '@/utils';
import { useMessages, useMessageCount } from '@/hooks';
import { useLanguage } from '@/store';
import { t, type Language } from '@/i18n';
import { Card, Button, Select, Loading, Error, EmptyState } from '@/components/common';
import { formatDateTime, formatTokens } from '@/utils';
import type { Message, MessageFilters } from '@/types';

const ITEMS_PER_PAGE = 20;

export const Messages: React.FC = () => {
  const language = useLanguage();
  const [filters, setFilters] = useState<MessageFilters>({});
  const [page, setPage] = useState(1);

  const { data, isLoading, isFetching, isError, error, refetch } = useMessages({
    filters,
    pageSize: ITEMS_PER_PAGE,
    page,
  });

  const { data: totalCount } = useMessageCount(filters);

  const messages = data?.data ?? [];
  const pagination = data?.pagination;

  // Tool options
  const toolOptions = useMemo(
    () => [
      { value: '', label: t('dashboardFilterAllTools', language) },
      { value: 'openclaw', label: 'OpenClaw' },
      { value: 'claude', label: 'Claude' },
      { value: 'qwen', label: 'Qwen' },
    ],
    [language]
  );

  // Role options
  const roleOptions = useMemo(
    () => [
      { value: '', label: 'All Roles' },
      { value: 'user', label: 'User' },
      { value: 'assistant', label: 'Assistant' },
      { value: 'system', label: 'System' },
    ],
    []
  );

  const handleFilterChange = (key: keyof MessageFilters, value: string) => {
    setFilters((prev) => ({ ...prev, [key]: value || undefined }));
    setPage(1);
  };

  const handleReset = () => {
    setFilters({});
    setPage(1);
  };

  if (isError) {
    return <Error message={error?.message || t('error', language)} onRetry={() => refetch()} />;
  }

  return (
    <div className="messages">
      {/* Header */}
      <div className="messages-header d-flex justify-content-between align-items-center mb-4">
        <h2>{t('messages', language)}</h2>
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

      {/* Filters */}
      <Card className="mb-4">
        <div className="row g-3">
          <div className="col-md-3">
            <label className="form-label">{t('tableTool', language)}</label>
            <Select
              options={toolOptions}
              value={filters.tool || ''}
              onChange={(value) => handleFilterChange('tool', value)}
            />
          </div>
          <div className="col-md-3">
            <label className="form-label">{t('tableRole', language)}</label>
            <Select
              options={roleOptions}
              value={filters.role || ''}
              onChange={(value) => handleFilterChange('role', value)}
            />
          </div>
          <div className="col-md-3">
            <label className="form-label">Start Date</label>
            <input
              type="date"
              className="form-control"
              value={filters.startDate || ''}
              onChange={(e) => handleFilterChange('startDate', e.target.value)}
            />
          </div>
          <div className="col-md-3">
            <label className="form-label">End Date</label>
            <input
              type="date"
              className="form-control"
              value={filters.endDate || ''}
              onChange={(e) => handleFilterChange('endDate', e.target.value)}
            />
          </div>
        </div>
        <div className="mt-3">
          <Button variant="secondary" size="sm" onClick={handleReset}>
            {t('reset', language)}
          </Button>
        </div>
      </Card>

      {/* Stats */}
      {totalCount !== undefined && (
        <div className="mb-3">
          <span className="text-muted">Total: {totalCount.toLocaleString()} messages</span>
        </div>
      )}

      {/* Messages List */}
      {isLoading ? (
        <Loading size="lg" text={t('loading', language)} />
      ) : messages.length === 0 ? (
        <EmptyState
          icon="bi-chat-dots"
          title={t('noData', language)}
          description="No messages found with current filters"
        />
      ) : (
        <>
          <div className="messages-list">
            {messages.map((message) => (
              <MessageCard key={message.id} message={message} language={language} />
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
                      Previous
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
                      Next
                    </button>
                  </li>
                </ul>
              </nav>
            </div>
          )}
        </>
      )}
    </div>
  );
};

/**
 * Message Card Component
 */
interface MessageCardProps {
  message: Message;
  language: Language;
}

const MessageCard: React.FC<MessageCardProps> = ({ message, language }) => {
  const [expanded, setExpanded] = React.useState(false);

  const roleColors: Record<string, string> = {
    user: 'border-primary',
    assistant: 'border-success',
    system: 'border-warning',
  };

  const roleIcons: Record<string, string> = {
    user: 'bi-person',
    assistant: 'bi-robot',
    system: 'bi-gear',
  };

  return (
    <Card className={cn('mb-3', roleColors[message.role] || '')} variant="default">
      <div className="d-flex justify-content-between align-items-start mb-2">
        <div className="d-flex align-items-center">
          <i className={cn('bi', roleIcons[message.role] || 'bi-chat', 'me-2')} />
          <strong className="text-capitalize">{message.role}</strong>
          {message.tool_name && (
            <span className="badge bg-secondary ms-2">{message.tool_name}</span>
          )}
        </div>
        <small className="text-muted">{formatDateTime(message.timestamp)}</small>
      </div>

      <div className={cn('message-content', !expanded && 'truncated')}>{message.content}</div>

      {message.content.length > 200 && (
        <button className="btn btn-link btn-sm p-0 mt-2" onClick={() => setExpanded(!expanded)}>
          {expanded ? 'Show less' : 'Show more'}
        </button>
      )}

      {message.tokens !== undefined && message.tokens > 0 && (
        <small className="text-muted d-block mt-2">
          {formatTokens(message.tokens)} {t('tokens', language)}
        </small>
      )}
    </Card>
  );
};
