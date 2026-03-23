/**
 * SessionList Component - Session list for Work Mode left panel
 *
 * Features:
 * - Display user's AI session history
 * - Group by date (Today, Yesterday, This Week, Earlier)
 * - Search and filter
 * - Click to open session in main area
 */

import React, { useState, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { useLanguage } from '@/store';
import { t } from '@/i18n';
import { useSessions } from '@/hooks';
import { Loading, EmptyState } from '@/components/common';
import { formatRelativeTime, formatTokens } from '@/utils';
import type { AgentSession } from '@/api/sessions';

interface SessionListProps {
  collapsed?: boolean;
  onSelectSession?: (sessionId: string) => void;
}

interface GroupedSessions {
  today: Array<{ id: string; title: string; tool: string; time: string; tokens: number; messages: number }>;
  yesterday: Array<{ id: string; title: string; tool: string; time: string; tokens: number; messages: number }>;
  thisWeek: Array<{ id: string; title: string; tool: string; time: string; tokens: number; messages: number }>;
  earlier: Array<{ id: string; title: string; tool: string; time: string; tokens: number; messages: number }>;
}

export const SessionList: React.FC<SessionListProps> = ({
  collapsed = false,
  onSelectSession,
}) => {
  const language = useLanguage();
  const navigate = useNavigate();
  const [searchQuery, setSearchQuery] = useState('');

  // Fetch sessions
  const { data: sessionsData, isLoading, error } = useSessions({
    page: 1,
    pageSize: 50,
  });

  const sessions = sessionsData?.data?.sessions || [];

  // Filter sessions by search query
  const filteredSessions = useMemo(() => {
    if (!searchQuery.trim()) return sessions;
    const query = searchQuery.toLowerCase();
    return sessions.filter((s: { title?: string; tool_name?: string }) =>
      (s.title || '').toLowerCase().includes(query) ||
      (s.tool_name || '').toLowerCase().includes(query)
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
      const sessionDate = new Date(session.updated_at || session.created_at || now);
      const sessionItem = {
        id: session.session_id,
        title: session.title || `Session ${session.session_id.slice(0, 8)}`,
        tool: session.tool_name || 'unknown',
        time: formatRelativeTime(session.updated_at || session.created_at || ''),
        tokens: session.total_tokens || 0,
        messages: session.message_count || 0,
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

  const handleSessionClick = (sessionId: string) => {
    if (onSelectSession) {
      onSelectSession(sessionId);
    } else {
      navigate(`/work/sessions?id=${sessionId}`);
    }
  };

  const handleNewSession = () => {
    navigate('/work');
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
            title={session.title || `Session ${session.session_id.slice(0, 8)}`}
          >
            <i className="bi bi-chat-dots" />
          </button>
        ))}
      </div>
    );
  }

  return (
    <div className="session-list">
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
      <button
        className="btn btn-primary btn-sm w-100 mb-3"
        onClick={handleNewSession}
      >
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
          {/* Today */}
          {groupedSessions.today.length > 0 && (
            <SessionGroup
              title={t('today', language)}
              sessions={groupedSessions.today}
              onSessionClick={handleSessionClick}
            />
          )}

          {/* Yesterday */}
          {groupedSessions.yesterday.length > 0 && (
            <SessionGroup
              title={t('yesterday', language)}
              sessions={groupedSessions.yesterday}
              onSessionClick={handleSessionClick}
            />
          )}

          {/* This Week */}
          {groupedSessions.thisWeek.length > 0 && (
            <SessionGroup
              title={t('thisWeek', language)}
              sessions={groupedSessions.thisWeek}
              onSessionClick={handleSessionClick}
            />
          )}

          {/* Earlier */}
          {groupedSessions.earlier.length > 0 && (
            <SessionGroup
              title={t('earlier', language)}
              sessions={groupedSessions.earlier}
              onSessionClick={handleSessionClick}
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
  }>;
  onSessionClick: (sessionId: string) => void;
}

const SessionGroup: React.FC<SessionGroupProps> = ({
  title,
  sessions,
  onSessionClick,
}) => {
  return (
    <div className="session-group mb-3">
      <div className="session-group-title text-muted small mb-1">{title}</div>
      <ul className="session-group-items list-unstyled">
        {sessions.map((session) => (
          <li key={session.id}>
            <button
              className="session-item btn btn-link text-start w-100 p-2"
              onClick={() => onSessionClick(session.id)}
            >
              <div className="session-item-header d-flex justify-content-between align-items-start">
                <span className="session-title text-truncate">{session.title}</span>
                <span className="session-time small text-muted">{session.time}</span>
              </div>
              <div className="session-item-meta d-flex gap-2 small text-muted">
                <span className="session-tool">
                  <i className="bi bi-tools me-1" />
                  {session.tool}
                </span>
                <span className="session-tokens">
                  <i className="bi bi-cpu me-1" />
                  {formatTokens(session.tokens)}
                </span>
                <span className="session-messages">
                  <i className="bi bi-chat-dots me-1" />
                  {session.messages}
                </span>
              </div>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
};