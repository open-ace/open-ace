/**
 * Messages Component - Messages list with filters
 *
 * UI improvements for Issue #1434:
 * - Responsive filter layout (no fixed widths)
 * - Optimized role-badge design (icon + lowercase text)
 * - Meta info for all roles (not just user)
 * - Debounced search input
 * - Default filter: all roles + last 7 days
 */

import React, { useState, useMemo, useCallback, useRef, useEffect } from 'react';
import { cn } from '@/utils';
import {
  useMessages,
  useMessageCount,
  useHosts,
  useSenders,
  useTools,
  usePageRefresh,
} from '@/hooks';
import { useLanguage } from '@/store';
import { t, type Language } from '@/i18n';
import {
  Card,
  Select,
  SearchableSelect,
  Loading,
  Error,
  EmptyState,
  Pagination,
  PageRefreshControl,
} from '@/components/common';
import {
  formatDateTime,
  formatDate,
  formatTokens,
  formatToolName,
  createMatcherConfig,
} from '@/utils';
import type { Message, MessageFilters } from '@/types';

const ITEMS_PER_PAGE = 20;
const SEARCH_DEBOUNCE_MS = 300;

// Get date N days ago in ISO format
const getDateDaysAgo = (days: number) => {
  const date = new Date();
  date.setDate(date.getDate() - days);
  return formatDate(date, 'iso');
};

// Custom hook for debounced value
function useDebouncedValue<T>(value: T, delay: number): T {
  const [debouncedValue, setDebouncedValue] = useState(value);

  useEffect(() => {
    const timer = setTimeout(() => setDebouncedValue(value), delay);
    return () => clearTimeout(timer);
  }, [value, delay]);

  return debouncedValue;
}

export const Messages: React.FC = () => {
  const language = useLanguage();

  // Default: all roles selected + last 7 days
  const defaultRoles = ['user', 'assistant', 'system'];
  const [selectedRoles, setSelectedRoles] = useState<string[]>(defaultRoles);
  const [searchInput, setSearchInput] = useState('');
  const debouncedSearch = useDebouncedValue(searchInput, SEARCH_DEBOUNCE_MS);

  const [filters, setFilters] = useState<MessageFilters>(() => ({
    startDate: getDateDaysAgo(7),
    endDate: formatDate(new Date(), 'iso'),
    role: defaultRoles,
  }));
  const [page, setPage] = useState(1);

  // Ref to track if we should apply debounced search
  const filtersRef = useRef(filters);

  // Apply debounced search to filters
  useEffect(() => {
    if (debouncedSearch !== filtersRef.current.search) {
      setFilters((prev) => ({ ...prev, search: debouncedSearch || undefined }));
      setPage(1);
    }
  }, [debouncedSearch]);

  // Get hosts for filter
  const { data: hostsData } = useHosts();
  const hosts = useMemo(() => hostsData ?? [], [hostsData]);

  // Get senders for filter
  const { data: sendersData } = useSenders(filters.host);
  const senders = useMemo(() => sendersData ?? [], [sendersData]);

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

  // Page refresh control
  const pageRefresh = usePageRefresh({
    page: '/manage/messages',
    refreshKey: createMatcherConfig([['messages']], 'prefix'),
    interval: 60000,
    enabled: false,
  });

  // Get tools for filter
  const { data: toolsData } = useTools();
  const tools = useMemo(() => toolsData ?? [], [toolsData]);

  // Tool options
  const toolOptions = useMemo(
    () => [
      { value: '', label: t('dashboardFilterAllTools', language) },
      ...tools.map((tool) => ({ value: tool, label: formatToolName(tool) })),
    ],
    [tools, language]
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
  const handleRoleChange = useCallback((role: string, checked: boolean) => {
    setSelectedRoles((prev) => {
      const newRoles = checked ? [...prev, role] : prev.filter((r) => r !== role);
      return newRoles;
    });
    setFilters((prev) => {
      const newRoles = checked
        ? [...(prev.role ?? []), role]
        : (prev.role ?? []).filter((r) => r !== role);
      return { ...prev, role: newRoles.length > 0 ? newRoles : undefined };
    });
    setPage(1);
  }, []);

  const handleFilterChange = useCallback((key: keyof MessageFilters, value: string) => {
    setFilters((prev) => {
      const next = { ...prev, [key]: value || undefined };
      if (next.startDate && next.endDate && next.endDate < next.startDate) {
        next.endDate = undefined;
      }
      filtersRef.current = next;
      return next;
    });
    setPage(1);
  }, []);

  const handleSearchChange = useCallback((value: string) => {
    setSearchInput(value);
  }, []);

  if (isError) {
    return <Error message={error?.message || t('error', language)} onRetry={() => refetch()} />;
  }

  return (
    <div className="messages">
      {/* Header */}
      <div className="messages-header d-flex justify-content-between align-items-center mb-4">
        <h2>{t('messages', language)}</h2>
        <PageRefreshControl
          refresh={pageRefresh}
          showLastRefreshTime={true}
          showNextRefreshTime={false}
          compact={true}
        />
      </div>

      {/* Filters - Responsive layout */}
      <Card className="mb-3 messages-filter-card">
        {/* Filter Row 1: Date Range */}
        <div className="messages-filter-row">
          <div className="messages-filter-group messages-filter-dates">
            <label className="messages-filter-label">
              <i className="bi bi-calendar-range me-1" />
              {t('dateRange', language)}
            </label>
            <div className="messages-filter-dates-inputs">
              <input
                type="date"
                className="form-control form-control-sm"
                value={filters.startDate ?? ''}
                aria-label={t('startDate', language)}
                onChange={(e) => handleFilterChange('startDate', e.target.value)}
              />
              <span className="messages-filter-dates-separator">~</span>
              <input
                type="date"
                className="form-control form-control-sm"
                value={filters.endDate ?? ''}
                aria-label={t('endDate', language)}
                onChange={(e) => handleFilterChange('endDate', e.target.value)}
              />
            </div>
          </div>
        </div>

        {/* Filter Row 2: Host + Tool + Sender */}
        <div className="messages-filter-row">
          <div className="messages-filter-group">
            <label className="messages-filter-label">
              <i className="bi bi-pc-display-horizontal me-1" />
              {t('tableHost', language)}
            </label>
            <Select
              options={hostOptions}
              value={filters.host ?? ''}
              onChange={(value) => handleFilterChange('host', value)}
              size="sm"
            />
          </div>
          <div className="messages-filter-group">
            <label className="messages-filter-label">
              <i className="bi bi-tools me-1" />
              {t('tableTool', language)}
            </label>
            <Select
              options={toolOptions}
              value={filters.tool ?? ''}
              onChange={(value) => handleFilterChange('tool', value)}
              size="sm"
            />
          </div>
          <div className="messages-filter-group messages-filter-sender">
            <label className="messages-filter-label">
              <i className="bi bi-person-badge me-1" />
              {t('tableSender', language)}
            </label>
            <SearchableSelect
              options={senderOptions}
              value={filters.sender ?? ''}
              onChange={(value) => handleFilterChange('sender', value)}
              placeholder={t('dashboardFilterAllSenders', language) || 'All Senders'}
              searchPlaceholder={t('searchSender', language)}
              size="sm"
            />
          </div>
        </div>

        {/* Filter Row 3: Role + Search */}
        <div className="messages-filter-row">
          <div className="messages-filter-group messages-filter-roles">
            <label className="messages-filter-label">
              <i className="bi bi-person-workspace me-1" />
              {t('role', language)}
            </label>
            <div className="messages-filter-roles-checkboxes">
              {[
                { key: 'user', icon: 'bi-person', label: t('messageRoleUser', language) },
                { key: 'assistant', icon: 'bi-robot', label: t('messageRoleAssistant', language) },
                { key: 'system', icon: 'bi-gear', label: t('messageRoleSystem', language) },
              ].map(({ key, icon, label }) => (
                <label key={key} className="form-check form-check-inline">
                  <input
                    className="form-check-input"
                    type="checkbox"
                    checked={selectedRoles.includes(key)}
                    onChange={(e) => handleRoleChange(key, e.target.checked)}
                  />
                  <span className="form-check-label">
                    <i className={`bi ${icon} me-1`} />
                    {label}
                  </span>
                </label>
              ))}
            </div>
          </div>
          <div className="messages-filter-group messages-filter-search">
            <label className="messages-filter-label">
              <i className="bi bi-search me-1" />
              {t('search', language)}
            </label>
            <input
              type="text"
              className="form-control form-control-sm"
              placeholder={t('searchMessages', language) ?? 'Search messages...'}
              value={searchInput}
              onChange={(e) => handleSearchChange(e.target.value)}
            />
          </div>
        </div>
      </Card>

      {/* Stats */}
      {totalCount !== undefined && (
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
            <Pagination
              currentPage={page}
              totalPages={pagination.totalPages}
              onPageChange={setPage}
              className="mt-4"
            />
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
 * - Role badge with icon and translated text
 * - Meta info for all roles (not just user)
 * - Smooth shadow transition on hover and expand
 * - Chevron rotation animation
 */
interface MessageCardProps {
  message: Message;
  language: Language;
}

const MessageCard = React.memo<MessageCardProps>(({ message, language }) => {
  const [expanded, setExpanded] = React.useState(false);

  // Role display config with icons and colors
  const roleConfig: Record<
    string,
    { icon: string; borderClass: string; bgClass: string; textClass: string }
  > = {
    user: {
      icon: 'bi-person-fill',
      borderClass: 'message-border-user',
      bgClass: 'role-badge-user',
      textClass: 'role-text-user',
    },
    assistant: {
      icon: 'bi-robot',
      borderClass: 'message-border-assistant',
      bgClass: 'role-badge-assistant',
      textClass: 'role-text-assistant',
    },
    system: {
      icon: 'bi-gear-fill',
      borderClass: 'message-border-system',
      bgClass: 'role-badge-system',
      textClass: 'role-text-system',
    },
  };

  const config = roleConfig[message.role] || roleConfig.user;

  // Get role display text using i18n
  const roleDisplayText: Record<string, string> = {
    user: t('messageRoleUser', language) || 'User',
    assistant: t('messageRoleAssistant', language) || 'Assistant',
    system: t('messageRoleSystem', language) || 'System',
  };

  // Toggle expand/collapse
  const handleToggle = () => {
    setExpanded(!expanded);
  };

  // Check if content can be expanded
  const canExpand = message.content.length > 200 || message.full_entry;

  return (
    <div
      className={cn('message-item', config.borderClass, expanded && 'expanded')}
      onClick={handleToggle}
      style={{ cursor: canExpand ? 'pointer' : 'default' }}
    >
      {/* Header */}
      <div className="message-header">
        {/* Role Badge - Compact design */}
        <div className="message-role">
          <span className={cn('role-badge', config.bgClass)}>
            <i className={`bi ${config.icon}`} />
            <span className="role-badge-text">{roleDisplayText[message.role] || message.role}</span>
          </span>
        </div>

        {/* Content area */}
        <div className="message-content">
          {/* Meta info - Show for all roles */}
          <div className="message-meta">
            {/* Host Name - for all roles */}
            {(message.host_name ?? message.host) && (
              <span className="message-meta-item">
                <i className="bi bi-pc-display-horizontal" />
                {message.host_name ?? message.host}
              </span>
            )}

            {/* Message Source - for all roles */}
            {message.message_source && (
              <span className={cn('message-source', message.message_source)}>
                {message.message_source}
              </span>
            )}

            {/* Sender Name - primarily for user, but show if available */}
            {message.sender_name && (
              <span className="message-meta-item message-meta-sender">
                <i className="bi bi-person-circle" />
                {message.sender_name}
              </span>
            )}

            {/* Model info - for assistant messages */}
            {message.role === 'assistant' && message.model && (
              <span className="message-meta-item message-meta-model">
                <i className="bi bi-cpu" />
                {message.model}
              </span>
            )}
          </div>

          {/* Truncated content */}
          <div className="message-content-truncated">
            {message.content.length > 200
              ? `${message.content.substring(0, 200)}...`
              : message.content}
          </div>
        </div>

        {/* Tokens display */}
        {message.tokens !== undefined && message.tokens > 0 && (
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
        <small className="message-footer-time">
          <i className="bi bi-clock me-1" />
          {formatDateTime(message.timestamp)}
        </small>
        {message.model && message.role !== 'assistant' && (
          <small className="text-muted">
            <i className="bi bi-cpu me-1" />
            {message.model}
          </small>
        )}
      </div>
    </div>
  );
});
