/**
 * SessionList Component - Session list for Work Mode left panel
 *
 * Features:
 * - Display user's AI session history
 * - Group by date (Today, Yesterday, This Week, Earlier)
 * - Search and filter
 * - Click to open session in main area
 */

import React, { useState, useMemo, useEffect, useRef } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import { useLanguage } from '@/store';
import { t } from '@/i18n';
import { useSessions, useSession } from '@/hooks';
import { Loading, EmptyState, Modal, Badge } from '@/components/common';
import { formatRelativeTime, formatDateTime, formatTokens } from '@/utils';
import type { AgentSession, SessionMessage } from '@/api/sessions';
import type { Language } from '@/i18n';

interface SessionListProps {
  collapsed?: boolean;
  onSelectSession?: (sessionId: string) => void;
}

interface GroupedSessions {
  today: Array<{
    id: string;
    title: string;
    tool: string;
    time: string;
    tokens: number;
    messages: number;
    requests: number;
  }>;
  yesterday: Array<{
    id: string;
    title: string;
    tool: string;
    time: string;
    tokens: number;
    messages: number;
    requests: number;
  }>;
  thisWeek: Array<{
    id: string;
    title: string;
    tool: string;
    time: string;
    tokens: number;
    messages: number;
    requests: number;
  }>;
  earlier: Array<{
    id: string;
    title: string;
    tool: string;
    time: string;
    tokens: number;
    messages: number;
    requests: number;
  }>;
}

export const SessionList: React.FC<SessionListProps> = ({ collapsed = false, onSelectSession }) => {
  const language = useLanguage();
  const navigate = useNavigate();
  const location = useLocation();
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(null);
  const [showDetailModal, setShowDetailModal] = useState(false);
  const sessionListRef = useRef<HTMLDivElement>(null);
  const selectedRef = useRef<HTMLButtonElement>(null);

  // Fetch sessions
  const {
    data: sessionsData,
    isLoading,
    error,
  } = useSessions({
    page: 1,
    pageSize: 50,
  });

  // Fetch selected session details with messages
  const { data: sessionDetail, isLoading: isLoadingDetail } = useSession(
    selectedSessionId ?? '',
    true,
    !!selectedSessionId
  );

  const sessions = sessionsData?.data?.sessions ?? [];

  // Filter sessions by search query
  const filteredSessions = useMemo(() => {
    if (!searchQuery.trim()) return sessions;
    const query = searchQuery.toLowerCase();
    return sessions.filter(
      (s: { title?: string; tool_name?: string }) =>
        (s.title ?? '').toLowerCase().includes(query) ||
        (s.tool_name ?? '').toLowerCase().includes(query)
    );
  }, [sessions, searchQuery]);

  // Group sessions by date
  const groupedSessions = useMemo((): GroupedSessions => {
    const now = new Date();
    const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    const yesterday = new Date(today.getTime() - 24 * 60 * 60 * 1000);
    const weekAgo = new Date(today.getTime() - 7 * 24 * 60 * 60 * 1000);

    const groups: GroupedSessions = {
      today: [],
      yesterday: [],
      thisWeek: [],
      earlier: [],
    };

    filteredSessions.forEach((session: AgentSession) => {
      const sessionDate = new Date(session.updated_at ?? session.created_at ?? now);
      const sessionItem = {
        id: session.session_id,
        title: session.title ?? `Session ${session.session_id.slice(0, 8)}`,
        tool: session.tool_name ?? 'unknown',
        time: formatRelativeTime(session.updated_at ?? session.created_at ?? '', {
          justNow: t('justNow', language),
          minAgo: t('minAgo', language),
          hourAgo: t('hourAgo', language),
          dayAgo: t('dayAgo', language),
        }),
        tokens: session.total_tokens ?? 0,
        messages: session.message_count ?? 0,
        requests: session.request_count ?? 0,
      };

      if (sessionDate >= today) {
        groups.today.push(sessionItem);
      } else if (sessionDate >= yesterday) {
        groups.yesterday.push(sessionItem);
      } else if (sessionDate >= weekAgo) {
        groups.thisWeek.push(sessionItem);
      } else {
        groups.earlier.push(sessionItem);
      }
    });

    return groups;
  }, [filteredSessions, language]);

  // Scroll selected session into view (only within the session list container)
  useEffect(() => {
    if (selectedRef.current && sessionListRef.current) {
      // Use 'nearest' to avoid scrolling the entire page
      selectedRef.current.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }
  }, [selectedSessionId]);

  const handleSessionClick = (sessionId: string) => {
    setSelectedSessionId(sessionId);
    setShowDetailModal(true);
    if (onSelectSession) {
      onSelectSession(sessionId);
    }
  };

  const handleCloseModal = () => {
    setShowDetailModal(false);
  };

  const handleNewSession = () => {
    // Check if we're already on the workspace page (conversation mode)
    const isWorkspacePage = location.pathname === '/work' || location.pathname === '/work/';

    if (isWorkspacePage) {
      // If already on workspace page, navigate with newTab parameter to create a new tab
      navigate('/work?newTab=true', { replace: true });
    } else {
      // Otherwise, navigate to workspace page
      navigate('/work');
    }
  };

  if (collapsed) {
    // Show collapsed view with icon
    return (
      <div className="session-list-collapsed">
        <button
          className="session-list-collapsed-btn"
          onClick={handleNewSession}
          title={t('newSession', language)}
        >
          <i className="bi bi-plus-lg" />
        </button>
        <div className="session-list-collapsed-divider" />
        {sessions.slice(0, 5).map((session: AgentSession) => (
          <button
            key={session.session_id}
            className="session-list-collapsed-item"
            onClick={() => handleSessionClick(session.session_id)}
            title={session.title ?? `Session ${session.session_id.slice(0, 8)}`}
          >
            <i className="bi bi-chat-dots" />
          </button>
        ))}
      </div>
    );
  }

  return (
    <div className="session-list" ref={sessionListRef}>
      {/* Search */}
      <div className="session-search mb-2">
        <div className="input-group input-group-sm">
          <span className="input-group-text">
            <i className="bi bi-search" />
          </span>
          <input
            type="text"
            className="form-control"
            placeholder={t('search', language)}
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
          />
        </div>
      </div>

      {/* New Session Button */}
      <button className="btn btn-primary btn-sm w-100 mb-3" onClick={handleNewSession}>
        <i className="bi bi-plus-lg me-1" />
        {t('newSession', language)}
      </button>

      {/* Session Groups */}
      {isLoading ? (
        <Loading size="sm" text={t('loading', language)} />
      ) : error ? (
        <div className="text-danger small">{t('error', language)}</div>
      ) : (
        <div className="session-groups">
          {groupedSessions.today.length > 0 && (
            <SessionGroup
              title={t('today', language)}
              sessions={groupedSessions.today}
              onSessionClick={handleSessionClick}
              selectedSessionId={selectedSessionId}
            />
          )}

          {/* Yesterday */}
          {groupedSessions.yesterday.length > 0 && (
            <SessionGroup
              title={t('yesterday', language)}
              sessions={groupedSessions.yesterday}
              onSessionClick={handleSessionClick}
              selectedSessionId={selectedSessionId}
            />
          )}

          {/* This Week */}
          {groupedSessions.thisWeek.length > 0 && (
            <SessionGroup
              title={t('thisWeek', language)}
              sessions={groupedSessions.thisWeek}
              onSessionClick={handleSessionClick}
              selectedSessionId={selectedSessionId}
            />
          )}

          {/* Earlier */}
          {groupedSessions.earlier.length > 0 && (
            <SessionGroup
              title={t('earlier', language)}
              sessions={groupedSessions.earlier}
              onSessionClick={handleSessionClick}
              selectedSessionId={selectedSessionId}
            />
          )}

          {/* Empty State */}
          {filteredSessions.length === 0 && (
            <EmptyState
              icon="bi-chat-dots"
              title={searchQuery ? t('noResults', language) : t('noSessionsFound', language)}
            />
          )}
        </div>
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
 * Session Group Component
 */
interface SessionGroupProps {
  title: string;
  sessions: Array<{
    id: string;
    title: string;
    tool: string;
    time: string;
    tokens: number;
    messages: number;
    requests: number;
  }>;
  onSessionClick: (sessionId: string) => void;
  selectedSessionId: string | null;
}

const SessionGroup: React.FC<SessionGroupProps> = ({
  title,
  sessions,
  onSessionClick,
  selectedSessionId,
}) => {
  return (
    <div className="session-group mb-3">
      <div className="session-group-title text-muted small mb-1">{title}</div>
      <ul className="session-group-items list-unstyled">
        {sessions.map((session) => (
          <li key={session.id}>
            <button
              className={`session-item w-100 p-2 ${selectedSessionId === session.id ? 'selected' : ''}`}
              onClick={() => onSessionClick(session.id)}
            >
              <span className="session-id text-truncate">
                {session.id.slice(0, 4)}
              </span>
              <span className="session-time text-muted">{session.time}</span>
              <span className="session-requests text-muted">
                <i className="bi bi-arrow-up-circle" />
                <span className="ms-1">{session.requests} req</span>
              </span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
};

/**
 * Session Detail Content Component
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
