/**
 * ConversationHistory Component - Conversation history table with filters
 */

import React, { useState, useMemo } from 'react';
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

  // Get hosts for filter
  const { data: hostsData } = useHosts();
  const hosts = hostsData || [];

  const { data, isLoading, isFetching, isError, error, refetch } = useConversationHistory({
    ...filters,
    page,
    pageSize: ITEMS_PER_PAGE,
  });

  const conversations = data?.data ?? [];

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
    return <Error message={error?.message || t('error', language)} onRetry={() => refetch()} />;
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
              value={filters.date || ''}
              onChange={(e) => handleFilterChange('date', e.target.value)}
            />
          </div>
          <div className="col-md-3">
            <label className="form-label">{t('tableTool', language)}</label>
            <Select
              options={toolOptions}
              value={filters.tool || ''}
              onChange={(value) => handleFilterChange('tool', value)}
            />
          </div>
          <div className="col-md-3">
            <label className="form-label">{t('tableHost', language)}</label>
            <Select
              options={hostOptions}
              value={filters.host || ''}
              onChange={(value) => handleFilterChange('host', value)}
            />
          </div>
          <div className="col-md-3">
            <label className="form-label">{t('tableSender', language)}</label>
            <input
              type="text"
              className="form-control"
              placeholder={t('searchSender', language)}
              value={filters.sender || ''}
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
      {isLoading ? (
        <Loading size="lg" text={t('loading', language)} />
      ) : sortedConversations.length === 0 ? (
        <EmptyState
          icon="bi-chat-history"
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
                    key={conv.session_id}
                    conversation={conv}
                    columns={columns}
                    language={language}
                    onViewDetails={() => setSelectedSession(conv.session_id)}
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
      <div className="conversation-history-fullscreen position-fixed top-0 start-0 w-100 h-100 bg-white p-4 overflow-auto" style={{ zIndex: 1050 }}>
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
        return conversation.host_name || '-';
      case 'sender_name':
        return conversation.sender_name || '-';
      case 'message_count':
        return <span className="text-end d-block">{conversation.message_count}</span>;
      case 'total_tokens':
        return <span className="text-end d-block">{formatTokens(conversation.total_tokens)}</span>;
      case 'last_message_time':
        return (
          <small className="text-muted">
            {formatDateTime(conversation.last_message_time)}
          </small>
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

const ConversationDetailModal: React.FC<ConversationDetailModalProps> = ({
  sessionId,
  language,
  onClose,
}) => {
  const [activeTab, setActiveTab] = useState<'timeline' | 'latency'>('timeline');
  const { data: messages, isLoading, isError } = useConversationTimeline(sessionId);

  // Calculate latency data from messages
  const latencyData = useMemo(() => {
    if (!messages || messages.length < 2) return [];

    const latencies: Array<{ index: number; role: string; latency: number }> = [];
    let lastUserTime: Date | null = null;

    messages.forEach((msg, index) => {
      const msgTime = new Date(msg.timestamp);
      if (msg.role === 'user') {
        lastUserTime = msgTime;
      } else if (msg.role === 'assistant' && lastUserTime) {
        const latency = (msgTime.getTime() - lastUserTime.getTime()) / 1000; // seconds
        if (latency > 0) {
          latencies.push({
            index: index + 1,
            role: msg.role,
            latency: Math.round(latency * 100) / 100,
          });
        }
        lastUserTime = null;
      }
    });

    return latencies;
  }, [messages]);

  return (
    <Modal
      isOpen={true}
      onClose={onClose}
      title={t('conversationDetails', language)}
      size="lg"
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
          {/* Tab Navigation */}
          <ul className="nav nav-tabs mb-3">
            <li className="nav-item">
              <button
                className={cn('nav-link', activeTab === 'timeline' && 'active')}
                onClick={() => setActiveTab('timeline')}
              >
                <i className="bi bi-chat-text me-1" />
                {t('timeline', language)}
              </button>
            </li>
            <li className="nav-item">
              <button
                className={cn('nav-link', activeTab === 'latency' && 'active')}
                onClick={() => setActiveTab('latency')}
              >
                <i className="bi bi-graph-up me-1" />
                {t('latencyCurve', language)}
              </button>
            </li>
          </ul>

          {/* Tab Content */}
          {activeTab === 'timeline' ? (
            <div className="conversation-messages" style={{ maxHeight: '400px', overflowY: 'auto' }}>
              {messages.map((msg, index) => (
                <div
                  key={msg.id || index}
                  className={cn(
                    'message-item mb-3 p-3 rounded',
                    msg.role === 'user' ? 'bg-light' : 'bg-white border'
                  )}
                >
                  <div className="d-flex justify-content-between align-items-start mb-2">
                    <div className="d-flex align-items-center">
                      <i
                        className={cn(
                          'bi me-2',
                          msg.role === 'user' ? 'bi-person' : 'bi-robot'
                        )}
                      />
                      <strong className="text-capitalize">{msg.role}</strong>
                    </div>
                    <small className="text-muted">{formatDateTime(msg.timestamp)}</small>
                  </div>
                  <div className="message-content">
                    <pre className="mb-0" style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
                      {msg.content}
                    </pre>
                  </div>
                  {msg.tokens_used > 0 && (
                    <small className="text-muted d-block mt-2">
                      {formatTokens(msg.tokens_used)} {t('tokens', language)}
                    </small>
                  )}
                </div>
              ))}
            </div>
          ) : (
            <div className="latency-chart">
              {latencyData.length > 0 ? (
                <LineChart
                  labels={latencyData.map((d) => `#${d.index}`)}
                  datasets={[
                    {
                      label: t('latency', language) + ' (s)',
                      data: latencyData.map((d) => d.latency),
                      borderColor: 'rgba(75, 192, 192, 1)',
                      backgroundColor: 'rgba(75, 192, 192, 0.2)',
                    },
                  ]}
                  height={300}
                />
              ) : (
                <EmptyState icon="bi-graph-up" title={t('noLatencyData', language)} />
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