/**
 * ConversationHistory Component - Conversation history table with filters
 *
 * Performance optimizations:
 * - Skeleton loading for better perceived performance
 * - React Query caching with staleTime
 */

import React, { useState, useMemo, useRef, useEffect } from 'react';
import { cn } from '@/utils';
import { useConversationHistory, useConversationTimeline, useHosts } from '@/hooks';
import { useLanguage } from '@/store';
import { t, type Language } from '@/i18n';
import {
  Card,
  Button,
  Select,
  Loading,
  Error,
  EmptyState,
  Modal,
  Dropdown,
  LineChart,
  Skeleton,
} from '@/components/common';
import { formatDateTime, formatTokens } from '@/utils';
import type { ConversationHistory as ConversationHistoryType } from '@/api';

const ITEMS_PER_PAGE = 20;

// Column definitions
interface ColumnDef {
  key: string;
  label: string;
  visible: boolean;
  sortable?: boolean;
}

const defaultColumns: ColumnDef[] = [
  { key: 'date', label: 'tableDate', visible: true, sortable: true },
  { key: 'tool_name', label: 'tableTool', visible: true, sortable: true },
  { key: 'host_name', label: 'tableHost', visible: true, sortable: true },
  { key: 'sender_name', label: 'tableSender', visible: true, sortable: true },
  { key: 'message_count', label: 'tableMessages', visible: true, sortable: true },
  { key: 'total_tokens', label: 'tableTokens', visible: true, sortable: true },
  { key: 'last_message_time', label: 'lastMessageTime', visible: true, sortable: true },
  { key: 'actions', label: 'actions', visible: true, sortable: false },
];

// Skeleton components
const TableSkeleton: React.FC<{ rows?: number }> = ({ rows = 10 }) => (
  <div className="table-responsive">
    <table className="table table-hover">
      <thead>
        <tr>
          {['Date', 'Tool', 'Host', 'Sender', 'Messages', 'Tokens', 'Last Message', 'Actions'].map(
            (header) => (
              <th key={header}>
                <Skeleton height={16} width="80%" />
              </th>
            )
          )}
        </tr>
      </thead>
      <tbody>
        {Array.from({ length: rows }).map((_, i) => (
          <tr key={i}>
            {Array.from({ length: 8 }).map((_, j) => (
              <td key={j}>
                <Skeleton height={14} width={j === 7 ? 40 : '90%'} />
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  </div>
);

export const ConversationHistory: React.FC = () => {
  const language = useLanguage();
  const [filters, setFilters] = useState<{
    date?: string;
    tool?: string;
    host?: string;
    sender?: string;
  }>({});
  const [page, setPage] = useState(1);
  const [selectedSession, setSelectedSession] = useState<string | null>(null);
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [columns, setColumns] = useState<ColumnDef[]>(defaultColumns);
  const [sortColumn, setSortColumn] = useState<string>('');
  const [sortDirection, setSortDirection] = useState<'asc' | 'desc'>('asc');

  // Track if this is the initial load
  const isInitialLoad = useRef(true);

  // Get hosts for filter
  const { data: hostsData } = useHosts();
  const hosts = hostsData ?? [];

  const { data, isLoading, isFetching, isError, error, refetch } = useConversationHistory({
    ...filters,
    page,
    pageSize: ITEMS_PER_PAGE,
  });

  const conversations = data?.data ?? [];

  // Mark initial load complete
  useEffect(() => {
    if (!isLoading && data) {
      isInitialLoad.current = false;
    }
  }, [isLoading, data]);

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

  const handleFilterChange = (key: string, value: string) => {
    setFilters((prev) => ({ ...prev, [key]: value || undefined }));
    setPage(1);
  };

  const handleReset = () => {
    setFilters({});
    setPage(1);
  };

  // Toggle column visibility
  const toggleColumn = (key: string) => {
    setColumns((prev) =>
      prev.map((col) => (col.key === key ? { ...col, visible: !col.visible } : col))
    );
  };

  // Handle sort
  const handleSort = (key: string) => {
    if (sortColumn === key) {
      setSortDirection((prev) => (prev === 'asc' ? 'desc' : 'asc'));
    } else {
      setSortColumn(key);
      setSortDirection('asc');
    }
  };

  // Sort conversations
  const sortedConversations = useMemo(() => {
    if (!sortColumn) return conversations;

    return [...conversations].sort((a, b) => {
      const aVal = a[sortColumn as keyof ConversationHistoryType];
      const bVal = b[sortColumn as keyof ConversationHistoryType];

      if (aVal === undefined || aVal === null) return 1;
      if (bVal === undefined || bVal === null) return -1;

      let comparison = 0;
      if (typeof aVal === 'string' && typeof bVal === 'string') {
        comparison = aVal.localeCompare(bVal);
      } else if (typeof aVal === 'number' && typeof bVal === 'number') {
        comparison = aVal - bVal;
      }

      return sortDirection === 'asc' ? comparison : -comparison;
    });
  }, [conversations, sortColumn, sortDirection]);

  // Column selector dropdown items
  const columnSelectorItems = columns
    .filter((col) => col.key !== 'actions')
    .map((col) => ({
      id: col.key,
      label: (
        <div className="form-check">
          <input
            className="form-check-input"
            type="checkbox"
            checked={col.visible}
            onChange={() => toggleColumn(col.key)}
            onClick={(e) => e.stopPropagation()}
          />
          <label className="form-check-label">{t(col.label, language)}</label>
        </div>
      ),
      onClick: () => toggleColumn(col.key),
    }));

  if (isError) {
    return <Error message={error?.message ?? t('error', language)} onRetry={() => refetch()} />;
  }

  const tableContent = (
    <>
      {/* Filters */}
      <Card className="mb-3">
        <div className="row g-3">
          <div className="col-md-3">
            <label className="form-label">{t('tableDate', language)}</label>
            <input
              type="date"
              className="form-control"
              value={filters.date ?? ''}
              onChange={(e) => handleFilterChange('date', e.target.value)}
            />
          </div>
          <div className="col-md-3">
            <label className="form-label">{t('tableTool', language)}</label>
            <Select
              options={toolOptions}
              value={filters.tool ?? ''}
              onChange={(value) => handleFilterChange('tool', value)}
            />
          </div>
          <div className="col-md-3">
            <label className="form-label">{t('tableHost', language)}</label>
            <Select
              options={hostOptions}
              value={filters.host ?? ''}
              onChange={(value) => handleFilterChange('host', value)}
            />
          </div>
          <div className="col-md-3">
            <label className="form-label">{t('tableSender', language)}</label>
            <input
              type="text"
              className="form-control"
              placeholder={t('searchSender', language)}
              value={filters.sender ?? ''}
              onChange={(e) => handleFilterChange('sender', e.target.value)}
            />
          </div>
        </div>
        <div className="mt-3 d-flex gap-2">
          <Button variant="secondary" size="sm" onClick={handleReset}>
            {t('reset', language)}
          </Button>
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
      </Card>

      {/* Table */}
      {isLoading && isInitialLoad.current ? (
        <Card>
          <TableSkeleton rows={ITEMS_PER_PAGE} />
        </Card>
      ) : isLoading ? (
        <Loading size="lg" text={t('loading', language)} />
      ) : sortedConversations.length === 0 ? (
        <EmptyState
          icon="bi-chat-square-text"
          title={t('noData', language)}
          description="No conversation history found"
        />
      ) : (
        <Card>
          {/* Table Header with Column Selector and Fullscreen */}
          <div className="d-flex justify-content-between align-items-center mb-3">
            <span className="text-muted">
              {t('total', language)}: {sortedConversations.length} {t('conversations', language)}
            </span>
            <div className="d-flex gap-2">
              {/* Column Selector */}
              <Dropdown
                trigger={
                  <Button variant="outline-secondary" size="sm">
                    <i className="bi bi-columns-gap me-1" />
                    {t('columns', language)}
                  </Button>
                }
                items={columnSelectorItems}
              />
              {/* Fullscreen Button */}
              <Button
                variant="outline-secondary"
                size="sm"
                onClick={() => setIsFullscreen(!isFullscreen)}
              >
                <i className={cn('bi', isFullscreen ? 'bi-fullscreen-exit' : 'bi-fullscreen')} />
              </Button>
            </div>
          </div>

          <div className="table-responsive">
            <table className="table table-hover">
              <thead>
                <tr>
                  {columns
                    .filter((col) => col.visible)
                    .map((col) => (
                      <th
                        key={col.key}
                        className={cn(col.sortable && 'cursor-pointer')}
                        onClick={() => col.sortable && handleSort(col.key)}
                      >
                        {t(col.label, language)}
                        {col.sortable && sortColumn === col.key && (
                          <i
                            className={cn(
                              'bi ms-1',
                              sortDirection === 'asc' ? 'bi-arrow-up' : 'bi-arrow-down'
                            )}
                          />
                        )}
                      </th>
                    ))}
                </tr>
              </thead>
              <tbody>
                {sortedConversations.map((conv) => (
                  <ConversationRow
                    key={conv.conversation_id}
                    conversation={conv}
                    columns={columns}
                    language={language}
                    onViewDetails={() => setSelectedSession(conv.conversation_id)}
                  />
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </>
  );

  // Fullscreen mode
  if (isFullscreen) {
    return (
      <div
        className="conversation-history-fullscreen position-fixed top-0 start-0 w-100 h-100 bg-white p-4 overflow-auto"
        style={{ zIndex: 1050 }}
      >
        <div className="d-flex justify-content-between align-items-center mb-3">
          <h4>{t('conversationHistory', language)}</h4>
          <Button variant="outline-secondary" size="sm" onClick={() => setIsFullscreen(false)}>
            <i className="bi bi-x-lg" /> {t('close', language)}
          </Button>
        </div>
        {tableContent}
        {/* Conversation Detail Modal */}
        {selectedSession && (
          <ConversationDetailModal
            sessionId={selectedSession}
            language={language}
            onClose={() => setSelectedSession(null)}
          />
        )}
      </div>
    );
  }

  return (
    <div className="conversation-history">
      {/* Page Header */}
      <div className="page-header mb-4">
        <h2>{t('conversationHistory', language)}</h2>
      </div>
      {tableContent}
      {/* Conversation Detail Modal */}
      {selectedSession && (
        <ConversationDetailModal
          sessionId={selectedSession}
          language={language}
          onClose={() => setSelectedSession(null)}
        />
      )}
    </div>
  );
};

/**
 * Conversation Row Component
 */
interface ConversationRowProps {
  conversation: ConversationHistoryType;
  columns: ColumnDef[];
  language: Language;
  onViewDetails: () => void;
}

const ConversationRow: React.FC<ConversationRowProps> = ({
  conversation,
  columns,
  language: _language,
  onViewDetails,
}) => {
  const visibleColumns = columns.filter((col) => col.visible);

  const renderCell = (key: string) => {
    switch (key) {
      case 'date':
        return conversation.date;
      case 'tool_name':
        return <span className="badge bg-secondary">{conversation.tool_name}</span>;
      case 'host_name':
        return conversation.host_name ?? '-';
      case 'sender_name':
        return conversation.sender_name ?? '-';
      case 'message_count':
        return <span className="text-end d-block">{conversation.message_count}</span>;
      case 'total_tokens':
        return <span className="text-end d-block">{formatTokens(conversation.total_tokens)}</span>;
      case 'last_message_time':
        return (
          <small className="text-muted">{formatDateTime(conversation.last_message_time)}</small>
        );
      case 'actions':
        return (
          <Button variant="outline-primary" size="sm" onClick={onViewDetails}>
            <i className="bi bi-eye" />
          </Button>
        );
      default:
        return null;
    }
  };

  return (
    <tr>
      {visibleColumns.map((col) => (
        <td key={col.key}>{renderCell(col.key)}</td>
      ))}
    </tr>
  );
};

/**
 * Conversation Detail Modal Component
 */
interface ConversationDetailModalProps {
  sessionId: string;
  language: Language;
  onClose: () => void;
}

// Message item component with expand/collapse functionality
interface MessageItemProps {
  msg: {
    id: number;
    role: string;
    content: string;
    timestamp: string;
    tokens_used: number;
    input_tokens: number;
    output_tokens: number;
    model?: string;
    sender_name?: string;
  };
  language: Language;
}

const MessageItem: React.FC<MessageItemProps> = ({ msg, language }) => {
  const [isExpanded, setIsExpanded] = useState(false);
  const MAX_CONTENT_LENGTH = 500;

  const shouldTruncate = msg.content && msg.content.length > MAX_CONTENT_LENGTH;
  const displayContent =
    shouldTruncate && !isExpanded
      ? msg.content.substring(0, MAX_CONTENT_LENGTH) + '...'
      : msg.content;

  const getRoleIcon = (role: string) => {
    switch (role) {
      case 'user':
        return 'bi-person-fill';
      case 'assistant':
        return 'bi-robot';
      case 'system':
        return 'bi-gear-fill';
      case 'toolResult':
        return 'bi-wrench';
      default:
        return 'bi-chat-dots';
    }
  };

  const getRoleBadgeClass = (role: string) => {
    switch (role) {
      case 'user':
        return 'bg-primary';
      case 'assistant':
        return 'bg-success';
      case 'system':
        return 'bg-secondary';
      case 'toolResult':
        return 'bg-info';
      default:
        return 'bg-secondary';
    }
  };

  return (
    <div
      className={cn(
        'message-item mb-3 p-3 rounded',
        msg.role === 'user' ? 'bg-light' : 'bg-white border'
      )}
    >
      {/* Header: Role, Sender, Model, Time */}
      <div className="d-flex justify-content-between align-items-start mb-2">
        <div className="d-flex align-items-center flex-wrap gap-2">
          <i className={cn('bi', getRoleIcon(msg.role))} />
          <span className={cn('badge', getRoleBadgeClass(msg.role))}>{msg.role}</span>
          {msg.sender_name && (
            <span className="text-muted small">
              <i className="bi bi-person me-1" />
              {msg.sender_name}
            </span>
          )}
          {msg.model && (
            <span className="badge bg-dark">
              <i className="bi bi-cpu me-1" />
              {msg.model}
            </span>
          )}
        </div>
        <small className="text-muted">
          <i className="bi bi-clock me-1" />
          {formatDateTime(msg.timestamp)}
        </small>
      </div>

      {/* Content */}
      <div className="message-content">
        <pre
          className="mb-0"
          style={{
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-word',
            fontSize: '0.875rem',
            maxHeight: isExpanded ? 'none' : '300px',
            overflow: isExpanded ? 'visible' : 'auto',
          }}
        >
          {displayContent}
        </pre>
      </div>

      {/* Expand/Collapse Button */}
      {shouldTruncate && (
        <Button
          variant="link"
          size="sm"
          className="p-0 mt-2"
          onClick={() => setIsExpanded(!isExpanded)}
        >
          <i className={cn('bi me-1', isExpanded ? 'bi-chevron-up' : 'bi-chevron-down')} />
          {isExpanded ? t('collapse', language) : t('expand', language)}
        </Button>
      )}

      {/* Token Info */}
      {(msg.tokens_used > 0 || msg.input_tokens > 0 || msg.output_tokens > 0) && (
        <div className="d-flex gap-3 mt-2 flex-wrap">
          {msg.tokens_used > 0 && (
            <small className="text-muted">
              <i className="bi bi-coin me-1" />
              {t('tokens', language)}: {formatTokens(msg.tokens_used)}
            </small>
          )}
          {msg.input_tokens > 0 && (
            <small className="text-muted">
              <i className="bi bi-box-arrow-in-right me-1" />
              {t('inputTokens', language)}: {formatTokens(msg.input_tokens)}
            </small>
          )}
          {msg.output_tokens > 0 && (
            <small className="text-muted">
              <i className="bi bi-box-arrow-right me-1" />
              {t('outputTokens', language)}: {formatTokens(msg.output_tokens)}
            </small>
          )}
        </div>
      )}
    </div>
  );
};

const ConversationDetailModal: React.FC<ConversationDetailModalProps> = ({
  sessionId,
  language,
  onClose,
}) => {
  const [activeTab, setActiveTab] = useState<'timeline' | 'latency'>('timeline');
  const [roleFilter, setRoleFilter] = useState<string>('all');
  const { data: messages, isLoading, isError } = useConversationTimeline(sessionId);

  // Filter messages by role
  const filteredMessages = useMemo(() => {
    if (!messages) return [];
    if (roleFilter === 'all') return messages;
    return messages.filter((msg) => msg.role === roleFilter);
  }, [messages, roleFilter]);

  // Calculate latency data from messages
  const latencyData = useMemo(() => {
    if (!messages || messages.length < 2) return [];

    const latencies: Array<{ index: number; role: string; latency: number; timestamp: string }> =
      [];
    let lastUserTime: Date | null = null;
    let lastUserIndex = 0;

    messages.forEach((msg, index) => {
      const msgTime = new Date(msg.timestamp);
      if (msg.role === 'user') {
        lastUserTime = msgTime;
        lastUserIndex = index;
      } else if ((msg.role === 'assistant' || msg.role === 'toolResult') && lastUserTime) {
        const latency = (msgTime.getTime() - lastUserTime.getTime()) / 1000; // seconds
        if (latency > 0) {
          latencies.push({
            index: lastUserIndex + 1,
            role: msg.role,
            latency: Math.round(latency * 100) / 100,
            timestamp: msg.timestamp,
          });
        }
        lastUserTime = null;
      }
    });

    return latencies;
  }, [messages]);

  // Calculate latency statistics
  const latencyStats = useMemo(() => {
    if (latencyData.length === 0) return null;

    const latencies = latencyData.map((d) => d.latency);
    const avg = latencies.reduce((a, b) => a + b, 0) / latencies.length;
    const max = Math.max(...latencies);
    const min = Math.min(...latencies);
    const sorted = [...latencies].sort((a, b) => a - b);
    const median =
      sorted.length % 2 === 0
        ? (sorted[sorted.length / 2 - 1] + sorted[sorted.length / 2]) / 2
        : sorted[Math.floor(sorted.length / 2)];

    return {
      avg: Math.round(avg * 100) / 100,
      max: Math.round(max * 100) / 100,
      min: Math.round(min * 100) / 100,
      median: Math.round(median * 100) / 100,
      count: latencies.length,
    };
  }, [latencyData]);

  // Calculate message statistics
  const messageStats = useMemo(() => {
    if (!messages) return null;

    const stats = {
      total: messages.length,
      user: 0,
      assistant: 0,
      system: 0,
      toolResult: 0,
      totalTokens: 0,
      totalInputTokens: 0,
      totalOutputTokens: 0,
    };

    messages.forEach((msg) => {
      if (msg.role === 'user') stats.user++;
      else if (msg.role === 'assistant') stats.assistant++;
      else if (msg.role === 'system') stats.system++;
      else if (msg.role === 'toolResult') stats.toolResult++;
      stats.totalTokens += msg.tokens_used ?? 0;
      stats.totalInputTokens += msg.input_tokens ?? 0;
      stats.totalOutputTokens += msg.output_tokens ?? 0;
    });

    return stats;
  }, [messages]);

  // Role filter options
  const roleFilterOptions = [
    { value: 'all', label: t('all', language) },
    { value: 'user', label: 'User' },
    { value: 'assistant', label: 'Assistant' },
    { value: 'system', label: 'System' },
    { value: 'toolResult', label: 'Tool Result' },
  ];

  return (
    <Modal
      isOpen={true}
      onClose={onClose}
      title={t('conversationDetails', language)}
      size="xl"
      footer={
        <Button variant="secondary" onClick={onClose}>
          {t('close', language)}
        </Button>
      }
    >
      {isLoading ? (
        <Loading text={t('loading', language)} />
      ) : isError ? (
        <Error message={t('error', language)} />
      ) : messages && messages.length > 0 ? (
        <>
          {/* Message Statistics */}
          {messageStats && (
            <div className="row mb-3 g-2">
              <div className="col-auto">
                <span className="badge bg-secondary me-1">
                  <i className="bi bi-chat-dots me-1" />
                  {messageStats.total} {t('messages', language)}
                </span>
              </div>
              {messageStats.user > 0 && (
                <div className="col-auto">
                  <span className="badge bg-primary me-1">
                    <i className="bi bi-person me-1" />
                    User: {messageStats.user}
                  </span>
                </div>
              )}
              {messageStats.assistant > 0 && (
                <div className="col-auto">
                  <span className="badge bg-success me-1">
                    <i className="bi bi-robot me-1" />
                    Assistant: {messageStats.assistant}
                  </span>
                </div>
              )}
              {messageStats.toolResult > 0 && (
                <div className="col-auto">
                  <span className="badge bg-info me-1">
                    <i className="bi bi-wrench me-1" />
                    Tool: {messageStats.toolResult}
                  </span>
                </div>
              )}
              {messageStats.totalTokens > 0 && (
                <div className="col-auto">
                  <span className="badge bg-dark me-1">
                    <i className="bi bi-coin me-1" />
                    {formatTokens(messageStats.totalTokens)} {t('tokens', language)}
                  </span>
                </div>
              )}
            </div>
          )}

          {/* Tab Navigation */}
          <ul className="nav nav-tabs mb-3">
            <li className="nav-item">
              <button
                className={cn('nav-link', activeTab === 'timeline' && 'active')}
                onClick={() => setActiveTab('timeline')}
              >
                <i className="bi bi-chat-text me-1" />
                {t('timeline', language)}
                {filteredMessages.length > 0 && (
                  <span className="badge bg-secondary ms-1">{filteredMessages.length}</span>
                )}
              </button>
            </li>
            <li className="nav-item">
              <button
                className={cn('nav-link', activeTab === 'latency' && 'active')}
                onClick={() => setActiveTab('latency')}
              >
                <i className="bi bi-graph-up me-1" />
                {t('latencyCurve', language)}
                {latencyData.length > 0 && (
                  <span className="badge bg-secondary ms-1">{latencyData.length}</span>
                )}
              </button>
            </li>
          </ul>

          {/* Tab Content */}
          {activeTab === 'timeline' ? (
            <>
              {/* Role Filter */}
              <div className="mb-3">
                <div className="d-flex align-items-center gap-2">
                  <span className="text-muted small">{t('filterByRole', language)}:</span>
                  <div className="btn-group btn-group-sm">
                    {roleFilterOptions.map((opt) => (
                      <button
                        key={opt.value}
                        className={cn(
                          'btn',
                          roleFilter === opt.value ? 'btn-primary' : 'btn-outline-secondary'
                        )}
                        onClick={() => setRoleFilter(opt.value)}
                      >
                        {opt.label}
                      </button>
                    ))}
                  </div>
                </div>
              </div>

              {/* Message List */}
              <div
                className="conversation-messages"
                style={{ maxHeight: '500px', overflowY: 'auto' }}
              >
                {filteredMessages.length > 0 ? (
                  filteredMessages.map((msg, index) => (
                    <MessageItem key={msg.id ?? index} msg={msg} language={language} />
                  ))
                ) : (
                  <EmptyState icon="bi-chat-dots" title={t('noMessages', language)} />
                )}
              </div>
            </>
          ) : (
            <div className="latency-chart">
              {latencyData.length > 0 ? (
                <>
                  {/* Latency Statistics */}
                  {latencyStats && (
                    <div className="row mb-3 g-2">
                      <div className="col-md-3">
                        <div className="card bg-light">
                          <div className="card-body py-2 px-3">
                            <small className="text-muted d-block">
                              {t('averageLatency', language)}
                            </small>
                            <strong className="text-primary">{latencyStats.avg}s</strong>
                          </div>
                        </div>
                      </div>
                      <div className="col-md-3">
                        <div className="card bg-light">
                          <div className="card-body py-2 px-3">
                            <small className="text-muted d-block">
                              {t('medianLatency', language)}
                            </small>
                            <strong className="text-info">{latencyStats.median}s</strong>
                          </div>
                        </div>
                      </div>
                      <div className="col-md-3">
                        <div className="card bg-light">
                          <div className="card-body py-2 px-3">
                            <small className="text-muted d-block">
                              {t('minLatency', language)}
                            </small>
                            <strong className="text-success">{latencyStats.min}s</strong>
                          </div>
                        </div>
                      </div>
                      <div className="col-md-3">
                        <div className="card bg-light">
                          <div className="card-body py-2 px-3">
                            <small className="text-muted d-block">
                              {t('maxLatency', language)}
                            </small>
                            <strong className="text-danger">{latencyStats.max}s</strong>
                          </div>
                        </div>
                      </div>
                    </div>
                  )}

                  {/* Latency Chart */}
                  <LineChart
                    labels={latencyData.map((d) => `#${d.index}`)}
                    datasets={[
                      {
                        label: t('latency', language) + ' (s)',
                        data: latencyData.map((d) => d.latency),
                        borderColor: 'rgba(75, 192, 192, 1)',
                        backgroundColor: 'rgba(75, 192, 192, 0.2)',
                        fill: true,
                        tension: 0.3,
                      },
                    ]}
                    height={300}
                  />

                  {/* Latency Table */}
                  <div className="mt-3">
                    <h6 className="text-muted mb-2">{t('latencyDetails', language)}</h6>
                    <div
                      className="table-responsive"
                      style={{ maxHeight: '200px', overflowY: 'auto' }}
                    >
                      <table className="table table-sm table-hover">
                        <thead>
                          <tr>
                            <th>#</th>
                            <th>{t('messageIndex', language)}</th>
                            <th>{t('latency', language)} (s)</th>
                            <th>{t('timestamp', language)}</th>
                          </tr>
                        </thead>
                        <tbody>
                          {latencyData.map((d, i) => (
                            <tr key={i}>
                              <td>{i + 1}</td>
                              <td>#{d.index}</td>
                              <td>
                                <span
                                  className={cn(
                                    'badge',
                                    d.latency > 10
                                      ? 'bg-danger'
                                      : d.latency > 5
                                        ? 'bg-warning'
                                        : 'bg-success'
                                  )}
                                >
                                  {d.latency}s
                                </span>
                              </td>
                              <td>
                                <small>{formatDateTime(d.timestamp)}</small>
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                </>
              ) : (
                <EmptyState
                  icon="bi-graph-up"
                  title={t('noLatencyData', language)}
                  description={t('noLatencyDataDesc', language)}
                />
              )}
            </div>
          )}
        </>
      ) : (
        <EmptyState icon="bi-chat-dots" title={t('noData', language)} />
      )}
    </Modal>
  );
};
