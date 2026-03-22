/**
 * ConversationHistory Component - Conversation history table with filters
 */

import React, { useState, useMemo } from 'react';
import { cn } from '@/utils';
import { useConversationHistory, useConversationTimeline } from '@/hooks';
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
} from '@/components/common';
import { formatDateTime, formatTokens } from '@/utils';
import type { ConversationHistory as ConversationHistoryType } from '@/api';

const ITEMS_PER_PAGE = 20;

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

  const handleFilterChange = (key: string, value: string) => {
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
    <div className="conversation-history">
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
            <input
              type="text"
              className="form-control"
              placeholder="Filter by host"
              value={filters.host || ''}
              onChange={(e) => handleFilterChange('host', e.target.value)}
            />
          </div>
          <div className="col-md-3">
            <label className="form-label">{t('tableSender', language)}</label>
            <input
              type="text"
              className="form-control"
              placeholder="Filter by sender"
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
      ) : conversations.length === 0 ? (
        <EmptyState
          icon="bi-chat-history"
          title={t('noData', language)}
          description="No conversation history found"
        />
      ) : (
        <Card>
          <div className="table-responsive">
            <table className="table table-hover">
              <thead>
                <tr>
                  <th>{t('tableDate', language)}</th>
                  <th>{t('tableTool', language)}</th>
                  <th>{t('tableHost', language)}</th>
                  <th>{t('tableSender', language)}</th>
                  <th className="text-end">{t('tableMessages', language)}</th>
                  <th className="text-end">{t('tableTokens', language)}</th>
                  <th>{t('lastMessageTime', language)}</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {conversations.map((conv) => (
                  <ConversationRow
                    key={conv.session_id}
                    conversation={conv}
                    language={language}
                    onViewDetails={() => setSelectedSession(conv.session_id)}
                  />
                ))}
              </tbody>
            </table>
          </div>

          {/* Pagination */}
          <div className="d-flex justify-content-between align-items-center mt-3">
            <span className="text-muted">
              {t('total', language)}: {conversations.length} {t('conversations', language)}
            </span>
          </div>
        </Card>
      )}

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
  language: Language;
  onViewDetails: () => void;
}

const ConversationRow: React.FC<ConversationRowProps> = ({
  conversation,
  language: _language,
  onViewDetails,
}) => {
  return (
    <tr>
      <td>{conversation.date}</td>
      <td>
        <span className="badge bg-secondary">{conversation.tool_name}</span>
      </td>
      <td>{conversation.host_name || '-'}</td>
      <td>{conversation.sender_name || '-'}</td>
      <td className="text-end">{conversation.message_count}</td>
      <td className="text-end">{formatTokens(conversation.total_tokens)}</td>
      <td>
        <small className="text-muted">
          {formatDateTime(conversation.last_message_time)}
        </small>
      </td>
      <td>
        <Button variant="outline-primary" size="sm" onClick={onViewDetails}>
          <i className="bi bi-eye" />
        </Button>
      </td>
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
  const { data: messages, isLoading, isError } = useConversationTimeline(sessionId);

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
        <div className="conversation-messages">
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
        <EmptyState icon="bi-chat-dots" title={t('noData', language)} />
      )}
    </Modal>
  );
};