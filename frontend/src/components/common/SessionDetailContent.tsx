/**
 * SessionDetailContent Component - Shared session detail display
 *
 * Used in:
 * - Work mode SessionList (left panel)
 * - Sessions page (feature page)
 */

import React, { useState, useMemo, useEffect, useRef } from 'react';
import { t, type Language } from '@/i18n';
import { Badge } from './Badge';
import type { BadgeVariant } from './Badge';
import { formatDateTime, formatTokens } from '@/utils';
import { useRemoteSession } from '@/hooks';
import type { AgentSession, SessionMessage } from '@/api/sessions';

interface SessionDetailContentProps {
  session: AgentSession;
  language: Language;
  onRestore?: (sessionId: string) => void;
  restorePending?: boolean;
}

/**
 * Session Detail Content Component
 * Displays session metadata and messages with filtering capabilities
 */
export const SessionDetailContent: React.FC<SessionDetailContentProps> = ({
  session,
  language,
  onRestore,
  restorePending,
}) => {
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

  // Get badge variant based on session status
  const getStatusVariant = (status: string): BadgeVariant => {
    switch (status) {
      case 'active':
        return 'success';
      case 'completed':
        return 'secondary';
      default:
        return 'warning';
    }
  };

  // Get badge variant based on message role
  const getRoleVariant = (role: string): BadgeVariant => {
    switch (role) {
      case 'user':
        return 'primary';
      case 'assistant':
        return 'success';
      default:
        return 'secondary';
    }
  };

  return (
    <div className="session-detail-content">
      {/* Session Meta Info - Three column layout */}
      <div className="session-meta mb-3 p-3 bg-light rounded">
        <div className="row g-3">
          {/* Row 1: Token Breakdown, Requests/Messages, Model */}
          <div className="col-md-4">
            <small className="text-muted d-block">{t('totalTokens', language)}</small>
            <span>{formatTokens(session.total_tokens)}</span>
          </div>
          <div className="col-md-4">
            <small className="text-muted d-block">{t('requestsMessages', language)}</small>
            <span>
              {session.request_count ?? 0} / {session.message_count ?? 0}
            </span>
          </div>
          <div className="col-md-4">
            <small className="text-muted d-block">{t('model', language) ?? 'Model'}</small>
            <span>{session.model ?? '-'}</span>
          </div>
          {/* Row 2: Status, Created, Last Active */}
          <div className="col-md-4">
            <small className="text-muted d-block">{t('status', language) ?? 'Status'}</small>
            <Badge variant={getStatusVariant(session.status)}>{session.status}</Badge>
            {session.workspace_type === 'remote' && (
              <Badge variant="info" className="ms-1">
                <i className="bi bi-cloud-fill me-1" />
                {session.machine_name ?? 'Remote'}
              </Badge>
            )}
          </div>
          <div className="col-md-4">
            <small className="text-muted d-block">{t('created', language) ?? 'Created'}</small>
            <span>{session.created_at ? formatDateTime(session.created_at) : '-'}</span>
          </div>
          <div className="col-md-4">
            <small className="text-muted d-block">
              {t('lastActive', language) ?? 'Last Active'}
            </small>
            <span>{session.updated_at ? formatDateTime(session.updated_at) : '-'}</span>
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
              className={`message-item p-2 mb-2 rounded ${
                msg.role === 'user' ? 'bg-light' : 'bg-white border'
              }`}
            >
              <div className="d-flex justify-content-between align-items-center mb-1">
                <Badge variant={getRoleVariant(msg.role)}>{msg.role}</Badge>
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

      {/* Remote Output */}
      {session.workspace_type === 'remote' && (
        <RemoteOutputSection sessionId={session.session_id} language={language} />
      )}

      {/* Restore Button */}
      {onRestore && (
        <div className="mt-3 d-flex justify-content-end">
          <button
            className="btn btn-primary btn-sm"
            onClick={() => onRestore(session.session_id)}
            disabled={restorePending}
          >
            <i className="bi bi-arrow-repeat me-1" />
            {t('restoreSession', language)}
          </button>
        </div>
      )}
    </div>
  );
};

/**
 * Parse qwen CLI stream-json output line into displayable segments.
 *
 * The stream-json format produces JSON lines with these types:
 * - system/init: session initialization metadata → skip
 * - assistant: LLM response with content blocks (text, thinking, tool_use) → extract text
 * - result: completion status → show errors only
 */
function parseStreamJsonLine(data: string): { text: string; type: 'text' | 'error' | 'info' }[] {
  const trimmed = data.trim();
  if (!trimmed) return [];

  // Try to parse as JSON
  let parsed: Record<string, unknown>;
  try {
    parsed = JSON.parse(trimmed);
  } catch {
    // Not JSON — display as-is
    return [{ text: trimmed, type: 'text' }];
  }

  const msgType = parsed.type as string;

  if (msgType === 'assistant') {
    const message = parsed.message as Record<string, unknown> | undefined;
    if (!message) return [];
    const content = message.content as Array<Record<string, unknown>> | undefined;
    if (!Array.isArray(content)) return [];

    const segments: { text: string; type: 'text' | 'error' | 'info' }[] = [];
    for (const block of content) {
      const blockType = block.type as string;
      if (blockType === 'text') {
        const text = (block.text as string) || '';
        if (text) segments.push({ text, type: 'text' });
      }
      // Skip thinking, tool_use, tool_result blocks
    }
    return segments;
  }

  // OpenAI-compatible chat completion response: {"type":"message","role":"assistant","content":"..."}
  if (msgType === 'message') {
    const role = parsed.role as string;
    if (role === 'assistant') {
      const content = parsed.content as string | Array<Record<string, unknown>> | undefined;
      if (typeof content === 'string' && content) {
        return [{ text: content, type: 'text' }];
      }
      if (Array.isArray(content)) {
        return content
          .filter((b) => b.type === 'text' && b.text)
          .map((b) => ({ text: b.text as string, type: 'text' as const }));
      }
    }
    return [];
  }

  if (msgType === 'result') {
    const subtype = parsed.subtype as string;
    if (subtype === 'error') {
      const error = (parsed.error as string) || (parsed.result as string) || 'Unknown error';
      return [{ text: `Error: ${error}`, type: 'error' }];
    }
    // Success result — nothing to display
    return [];
  }

  // system/init and other types — skip
  return [];
}

/**
 * Remote Output Section - Displays remote session output in terminal style
 */
const RemoteOutputSection: React.FC<{ sessionId: string; language: Language }> = ({
  sessionId,
  language,
}) => {
  const outputRef = useRef<HTMLDivElement>(null);
  const { data: remoteData, isLoading } = useRemoteSession(sessionId);
  const output = remoteData?.session?.output ?? [];

  // Parse all output entries into displayable segments
  const displaySegments = useMemo(() => {
    const segments: { text: string; type: 'text' | 'error' | 'info'; stream: string }[] = [];
    for (const entry of output) {
      // Only process stdout entries (skip stderr noise)
      if (entry.stream === 'stderr') continue;
      const parsed = parseStreamJsonLine(entry.data);
      for (const seg of parsed) {
        segments.push({ ...seg, stream: entry.stream });
      }
    }
    return segments;
  }, [output]);

  // Auto-scroll to bottom only when user is near the bottom
  useEffect(() => {
    const el = outputRef.current;
    if (el) {
      const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
      if (nearBottom) {
        el.scrollTop = el.scrollHeight;
      }
    }
  }, [displaySegments]);

  return (
    <div className="mt-3">
      <h6 className="mb-2">
        <i className="bi bi-terminal me-1" />
        {t('remoteOutput', language)}
      </h6>
      {isLoading ? (
        <div className="text-muted small p-2">{t('loading', language)}</div>
      ) : displaySegments.length === 0 ? (
        <div className="text-muted small p-2">{t('noOutput', language)}</div>
      ) : (
        <div
          ref={outputRef}
          className="bg-dark text-light p-3 rounded"
          style={{
            maxHeight: '300px',
            overflowY: 'auto',
            fontFamily: 'monospace',
            fontSize: '0.85rem',
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-word',
          }}
        >
          {displaySegments.map((seg, idx) => (
            <div key={idx} className={seg.type === 'error' ? 'text-danger' : undefined}>
              {seg.text}
            </div>
          ))}
        </div>
      )}
    </div>
  );
};
