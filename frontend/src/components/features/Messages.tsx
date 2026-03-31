/**
 * Messages Component - Messages list with filters
 */

import React, { useState, useMemo } from 'react';
import { cn } from '@/utils';
import { useMessages, useMessageCount, useHosts, useSenders } from '@/hooks';
import { useLanguage } from '@/store';
import { t, type Language } from '@/i18n';
import { Card, Select, SearchableSelect, Loading, Error, EmptyState } from '@/components/common';
import { formatDateTime, formatDate, formatTokens } from '@/utils';
import type { Message, MessageFilters } from '@/types';

const ITEMS_PER_PAGE = 20;

// Get today's date in ISO format for default filter
const getTodayDate = () => formatDate(new Date(), 'iso');

export const Messages: React.FC = () => {
  const language = useLanguage();
  const [selectedRoles, setSelectedRoles] = useState<string[]>(['user']);
  const [filters, setFilters] = useState<MessageFilters>(() => ({
    startDate: getTodayDate(),
    endDate: getTodayDate(),
    role: ['user'],
  }));
  const [page, setPage] = useState(1);

  // Get hosts for filter
  const { data: hostsData } = useHosts();
  const hosts = hostsData ?? [];

  // Get senders for filter
  const { data: sendersData } = useSenders(filters.host);
  const senders = sendersData ?? [];

  // Only fetch messages when at least one role is selected
  const hasRoleFilter = selectedRoles.length > 0;

  const { data, isLoading, isError, error, refetch } = useMessages({
    filters,
    pageSize: ITEMS_PER_PAGE,
    page,
    enabled: hasRoleFilter,
  });

  const { data: totalCount } = useMessageCount(filters, hasRoleFilter);

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

  // Host options
  const hostOptions = useMemo(
    () => [
      { value: '', label: t('dashboardFilterAllHosts', language) },
      ...hosts.map((host) => ({ value: host, label: host })),
    ],
    [hosts, language]
  );

  // Sender options
  const senderOptions = useMemo(
    () => [
      { value: '', label: t('dashboardFilterAllSenders', language) ?? 'All Senders' },
      ...senders.map((sender: string) => ({ value: sender, label: sender })),
    ],
    [senders, language]
  );

  // Handle role checkbox change
  const handleRoleChange = (role: string, checked: boolean) => {
    const newRoles = checked ? [...selectedRoles, role] : selectedRoles.filter((r) => r !== role);
    setSelectedRoles(newRoles);
    setFilters((prev) => ({ ...prev, role: newRoles.length > 0 ? newRoles : undefined }));
    setPage(1);
  };

  const handleFilterChange = (key: keyof MessageFilters, value: string) => {
    setFilters((prev) => ({ ...prev, [key]: value || undefined }));
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
      </div>

      {/* Filters - Two row layout */}
      <Card className="mb-3">
        {/* Row 1: Date, Host, Tool, Sender, Search */}
        <div className="d-flex flex-wrap align-items-center gap-3 mb-3">
          {/* Date Filter */}
          <div className="d-flex align-items-center gap-1">
            <small className="text-muted">{t('date', language)}:</small>
            <input
              type="date"
              className="form-control form-control-sm"
              style={{ width: '150px' }}
              value={filters.startDate ?? ''}
              onChange={(e) => handleFilterChange('startDate', e.target.value)}
            />
          </div>
          {/* Host Filter */}
          <div className="d-flex align-items-center gap-1">
            <small className="text-muted">{t('tableHost', language)}:</small>
            <Select
              options={hostOptions}
              value={filters.host ?? ''}
              onChange={(value) => handleFilterChange('host', value)}
              size="sm"
              className="flex-grow-0"
              style={{ width: '150px' } as React.CSSProperties}
            />
          </div>
          {/* Tool Filter */}
          <div className="d-flex align-items-center gap-1">
            <small className="text-muted">{t('tableTool', language)}:</small>
            <Select
              options={toolOptions}
              value={filters.tool ?? ''}
              onChange={(value) => handleFilterChange('tool', value)}
              size="sm"
              className="flex-grow-0"
              style={{ width: '150px' } as React.CSSProperties}
            />
          </div>
          {/* Sender Filter */}
          <div className="d-flex align-items-center gap-1">
            <small className="text-muted">{t('tableSender', language)}:</small>
            <SearchableSelect
              options={senderOptions}
              value={filters.sender ?? ''}
              onChange={(value) => handleFilterChange('sender', value)}
              placeholder={t('dashboardFilterAllSenders', language) || 'All Senders'}
              searchPlaceholder={t('searchSender', language)}
              size="sm"
              className="flex-grow-0"
              style={{ width: '150px' } as React.CSSProperties}
            />
          </div>
          {/* Search Filter */}
          <div className="d-flex align-items-center gap-1 ms-auto">
            <small className="text-muted">{t('search', language)}:</small>
            <input
              type="text"
              className="form-control form-control-sm"
              placeholder={t('searchMessages', language) ?? 'Search messages...'}
              style={{ width: '250px' }}
              value={filters.search ?? ''}
              onChange={(e) => handleFilterChange('search', e.target.value)}
            />
          </div>
        </div>
        {/* Row 2: Role */}
        <div className="d-flex flex-wrap align-items-center gap-2">
          {/* Role Filter */}
          <div className="d-flex align-items-center gap-1">
            <small className="text-muted">{t('role', language)}:</small>
            <div className="form-check form-check-inline mb-0">
              <input
                className="form-check-input"
                type="checkbox"
                id="roleUser"
                checked={selectedRoles.includes('user')}
                onChange={(e) => handleRoleChange('user', e.target.checked)}
              />
              <label className="form-check-label" htmlFor="roleUser">
                User
              </label>
            </div>
            <div className="form-check form-check-inline mb-0">
              <input
                className="form-check-input"
                type="checkbox"
                id="roleAssistant"
                checked={selectedRoles.includes('assistant')}
                onChange={(e) => handleRoleChange('assistant', e.target.checked)}
              />
              <label className="form-check-label" htmlFor="roleAssistant">
                Assistant
              </label>
            </div>
            <div className="form-check form-check-inline mb-0">
              <input
                className="form-check-input"
                type="checkbox"
                id="roleSystem"
                checked={selectedRoles.includes('system')}
                onChange={(e) => handleRoleChange('system', e.target.checked)}
              />
              <label className="form-check-label" htmlFor="roleSystem">
                System
              </label>
            </div>
          </div>
        </div>
      </Card>

      {/* Stats */}
      {totalCount != null && (
        <div className="mb-3 messages-stats">
          <span className="text-muted">
            {t('total', language)}: {totalCount.toLocaleString()} {t('messages', language)}
          </span>
        </div>
      )}

      {/* Messages List */}
      {!hasRoleFilter ? (
        <EmptyState
          icon="bi-funnel"
          title={t('selectRole', language) ?? 'Select Role'}
          description="Please select at least one role to view messages"
        />
      ) : isLoading ? (
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
 *
 * Features:
 * - Click entire card to expand/collapse
 * - Smooth shadow transition on hover and expand
 * - Chevron rotation animation
 * - Memoized for performance (rerender-memo optimization)
 */
interface MessageCardProps {
  message: Message;
  language: Language;
}

const MessageCard = React.memo<MessageCardProps>(({ message, language }) => {
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

  // Toggle expand/collapse
  const handleToggle = () => {
    setExpanded(!expanded);
  };

  // Check if content can be expanded
  const canExpand = message.content.length > 200 || message.full_entry;

  return (
    <div
      className={cn('message-item', roleColors[message.role] || '', expanded && 'expanded')}
      onClick={handleToggle}
      style={{ cursor: canExpand ? 'pointer' : 'default' }}
    >
      {/* Header with tags */}
      <div className="message-header">
        {/* Role Badge */}
        <div className="message-role">
          <span className={cn('role-badge', message.role)}>
            <i className={cn('bi', roleIcons[message.role] || 'bi-chat', 'me-1')} />
            {message.role.toUpperCase()}
          </span>
        </div>

        {/* Content area */}
        <div className="message-content">
          {/* Meta info for user messages */}
          {message.role === 'user' && (
            <div className="message-meta">
              {/* Host Name */}
              {(message.host_name ?? message.host) && (
                <span className="text-muted">
                  <i className="bi bi-pc-display-horizontal me-1" />
                  {message.host_name ?? message.host}
                </span>
              )}

              {/* Message Source */}
              {message.message_source && (
                <span className={cn('message-source', message.message_source)}>
                  {message.message_source.toUpperCase()}
                </span>
              )}

              {/* Sender Name */}
              {message.sender_name && (
                <span className="text-primary fw-semibold">
                  <i className="bi bi-person-circle me-1" />
                  {message.sender_name}
                </span>
              )}
            </div>
          )}

          {/* Truncated content */}
          <div className="message-content-truncated">
            {message.content.length > 200
              ? `${message.content.substring(0, 200)}...`
              : message.content}
          </div>
        </div>

        {/* Tokens display */}
        {message.tokens != null && message.tokens > 0 && (
          <div className="message-tokens">
            <i className="bi bi-cpu me-1" />
            {formatTokens(message.tokens)} {t('tokens', language)}
          </div>
        )}

        {/* Expand/Collapse chevron */}
        {canExpand && (
          <div className="message-chevron">
            <i
              className={cn('bi bi-chevron-down', 'transition-transform', expanded && 'rotate-180')}
            />
          </div>
        )}
      </div>

      {/* Expanded content */}
      {canExpand && (
        <div className="message-content-expanded" style={{ display: expanded ? 'block' : 'none' }}>
          <pre className="mb-0" style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
            {expanded && message.full_entry
              ? JSON.stringify(message.full_entry, null, 2)
              : message.content}
          </pre>
        </div>
      )}

      {/* Footer */}
      <div className="message-footer">
        <small>{formatDateTime(message.timestamp)}</small>
        {message.model && <small className="text-muted">Model: {message.model}</small>}
      </div>
    </div>
  );
});
