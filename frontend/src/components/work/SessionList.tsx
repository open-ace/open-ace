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
import { Loading, EmptyState, Modal, SessionDetailContent } from '@/components/common';
import { formatRelativeTime } from '@/utils';
import type { AgentSession } from '@/api/sessions';

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
    refetch,
  } = useSessions({
    page: 1,
    pageSize: 50,
  });

  // Auto refresh session list every 1 minute (Issue #64)
  useEffect(() => {
    const interval = setInterval(() => {
      // Only refresh if not already loading and modal is not open
      if (!isLoading && !showDetailModal) {
        refetch();
      }
    }, 60000); // 1 minute = 60000ms

    return () => clearInterval(interval);
  }, [isLoading, showDetailModal, refetch]);

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
        title={
          (() => {
            const sessionIdShort = sessionDetail?.data?.session_id?.slice(0, 8) ?? '';
            const sessionTitle = sessionDetail?.data?.title ?? '';
            // Check if title is meaningful (not just a default pattern like "qwen - 994f805a")
            const isDefaultTitle = sessionTitle.includes(sessionIdShort) || 
                                   sessionTitle.match(/^[a-z]+ - [a-f0-9]{8}$/i);
            // Format: "994f805a（会话名字）" or just "994f805a"
            if (sessionTitle && !isDefaultTitle) {
              return `${sessionIdShort}（${sessionTitle}）`;
            }
            return sessionIdShort || 'Session';
          })()
        }
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
  const language = useLanguage();
  
  return (
    <div className="session-group mb-3">
      <div className="session-group-title text-muted small mb-1">{title}</div>
      <ul className="session-group-items list-unstyled">
        {sessions.map((session) => (
          <li key={session.id}>
            <button
              className={`session-item w-100 p-2 ${selectedSessionId === session.id ? 'selected' : ''}`}
              onClick={() => onSessionClick(session.id)}
              title={session.title}  // Issue #66: Show session name on hover
            >
              <span className="session-id text-truncate">
                {session.id.slice(0, 4)}
              </span>
              <span className="session-time text-muted">{session.time}</span>
              <span className="session-requests text-muted">
                <i className="bi bi-arrow-up-circle" />
                <span className="ms-1">{session.requests} {t('request', language)}</span>  {/* Issue #66: i18n for "req" */}
              </span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
};
