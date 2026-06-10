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
import { formatDateTime, formatTimestampWithSeconds, formatTokens } from '@/utils';
import { useRemoteSession } from '@/hooks';
import type { AgentSession, SessionMessage, ContentBlock } from '@/api/sessions';

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
  // Filter state: default to show user, assistant, and system messages
  const [showUser, setShowUser] = useState(true);
  const [showAssistant, setShowAssistant] = useState(true);
  const [showSystem, setShowSystem] = useState(true);
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

  // Get role label based on message role
  const getRoleLabel = (role: string): string => {
    switch (role) {
      case 'user':
        return t('messageRoleUser', language);
      case 'assistant':
        return t('messageRoleAssistant', language);
      case 'system':
        return t('messageRoleSystem', language);
      case 'toolResult':
        return t('messageRoleToolResult', language);
      default:
        return role;
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
            {session.workspace_type === 'terminal' && (
              <Badge variant="info" className="ms-1">
                <i className="bi bi-terminal-fill me-1" />
                Terminal
              </Badge>
            )}
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
            {t('messageRoleUser', language)}
          </button>
          <button
            type="button"
            className={`btn btn-sm ${showAssistant ? 'btn-success' : 'btn-outline-success'}`}
            onClick={() => setShowAssistant(!showAssistant)}
          >
            {t('messageRoleAssistant', language)}
          </button>
          <button
            type="button"
            className={`btn btn-sm ${showSystem ? 'btn-secondary' : 'btn-outline-secondary'}`}
            onClick={() => setShowSystem(!showSystem)}
          >
            {t('messageRoleSystem', language)}
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
                <Badge variant={getRoleVariant(msg.role)}>{getRoleLabel(msg.role)}</Badge>
                <small className="text-muted">
                  {msg.timestamp ? formatDateTime(msg.timestamp) : ''}
                  {msg.tokens_used > 0 && ` • ${formatTokens(msg.tokens_used)} tokens`}
                </small>
              </div>
              <MessageContent msg={msg} searchText={searchText} highlightText={highlightText} />
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
 * Output segment type for remote session display (Issue #354)
 */
type OutputStatus = 'in_progress' | 'completed' | 'warning' | 'error';

interface OutputSegment {
  text: string;
  type: 'text' | 'error';
  status: OutputStatus;
  toolName?: string;
  timestamp: string;
  /** Unique key for React rendering - combination of timestamp and index */
  key: string;
}

/**
 * Error message friendly mapping (Issue #140)
 * Converts technical CLI error messages to user-friendly descriptions.
 * Each entry maps to friendly messages in all supported languages.
 */
const ERROR_FRIENDLY_MESSAGES: Record<string, { en: string; zh: string; ja: string; ko: string }> =
  {
    'Model stream ended with empty response text': {
      en: 'API response interrupted. Please check your network connection and try again.',
      zh: 'API 响应中断，请检查网络连接后重试。',
      ja: 'API応答が中断されました。ネットワーク接続を確認して再試行してください。',
      ko: 'API 응답이 중단되었습니다. 네트워크 연결을 확인하고 다시 시도하세요.',
    },
    'Model stream ended unexpectedly': {
      en: 'Model stream response terminated unexpectedly. Please try again.',
      zh: '模型流式响应异常终止，请重试。',
      ja: 'モデルストリーム応答が予期せず終了しました。再試行してください。',
      ko: '모델 스트림 응답이 예기치 않게 종료되었습니다. 다시 시도하세요.',
    },
    'Connection timeout': {
      en: 'API connection timed out. Please check your network and try again.',
      zh: 'API 连接超时，请检查网络后重试。',
      ja: 'API接続がタイムアウトしました。ネットワークを確認して再試行してください。',
      ko: 'API 연결 시간이 초과되었습니다. 네트워크를 확인하고 다시 시도하세요.',
    },
    'Rate limit exceeded': {
      en: 'API rate limit reached. Please wait a moment and try again.',
      zh: 'API 请求频率已达上限，请稍后重试。',
      ja: 'APIレート制限に達しました。少し待ってから再試行してください。',
      ko: 'API 요청 제한에 도달했습니다. 잠시 후 다시 시도하세요.',
    },
    'Network error': {
      en: 'Network connection error. Please check your internet and try again.',
      zh: '网络连接错误，请检查网络后重试。',
      ja: 'ネットワーク接続エラー。インターネット接続を確認して再試行してください。',
      ko: '네트워크 연결 오류. 인터넷 연결을 확인하고 다시 시도하세요.',
    },
    'Authentication failed': {
      en: 'API authentication failed. Please check your API key settings.',
      zh: 'API 认证失败，请检查 API 密钥设置。',
      ja: 'API認証に失敗しました。APIキー設定を確認してください。',
      ko: 'API 인증 실패. API 키 설정을 확인하세요.',
    },
    'Model not found': {
      en: 'The specified model is not available. Please check your model settings.',
      zh: '指定的模型不可用，请检查模型设置。',
      ja: '指定されたモデルは利用できません。モデル設定を確認してください。',
      ko: '지정된 모델을 사용할 수 없습니다. 모델 설정을 확인하세요.',
    },
  };

/**
 * Error keyword mapping for fuzzy matching (Issue #140)
 * Maps keywords to the corresponding friendly message key.
 * Used when exact match fails but error contains recognizable keywords.
 */
const ERROR_KEYWORDS: Record<string, string[]> = {
  'Model stream ended with empty response text': ['empty response', 'stream ended', 'empty stream'],
  'Model stream ended unexpectedly': ['stream ended', 'stream terminated', 'unexpectedly ended'],
  'Connection timeout': ['timeout', 'timed out', 'connection timeout'],
  'Rate limit exceeded': ['rate limit', 'limit exceeded', 'too many requests'],
  'Network error': ['network error', 'connection refused', 'network failed'],
  'Authentication failed': ['authentication', 'auth failed', 'invalid key', 'api key'],
  'Model not found': ['model not found', 'unknown model', 'invalid model'],
};

/**
 * Get user-friendly error message (Issue #140)
 * Converts technical CLI error messages to user-friendly descriptions.
 * Falls back to original message if no friendly version is available.
 *
 * @param originalError - The original error message from CLI
 * @param language - Current language setting
 * @returns User-friendly error message or original if no mapping exists
 */
function getFriendlyErrorMessage(originalError: string, language: Language): string {
  // Try exact match first
  const friendlyMessage = ERROR_FRIENDLY_MESSAGES[originalError];
  if (friendlyMessage) {
    return friendlyMessage[language] || friendlyMessage.en;
  }

  // Try keyword-based fuzzy match (more reliable than simple string containment)
  const lowerError = originalError.toLowerCase();
  for (const [targetKey, keywords] of Object.entries(ERROR_KEYWORDS)) {
    for (const keyword of keywords) {
      if (lowerError.includes(keyword.toLowerCase())) {
        const targetMessage = ERROR_FRIENDLY_MESSAGES[targetKey];
        if (targetMessage) {
          return targetMessage[language] || targetMessage.en;
        }
      }
    }
  }

  // No friendly version available, return original message
  // The error will still be displayed with error styling and icon
  return originalError;
}

/**
 * Determine output status based on content (Issue #354)
 * Note: Status is determined by content analysis only, not by message completion state.
 * - tool_use blocks represent "actions being performed" → in_progress
 * - text blocks are analyzed for keywords (warning/error)
 * - result type determines final completion status (handled separately)
 */
function determineTextStatus(text: string): OutputStatus {
  const lowerText = text.toLowerCase();
  if (lowerText.includes('error') || lowerText.startsWith('error:')) return 'error';
  if (lowerText.includes('warning') || lowerText.includes('warn')) return 'warning';
  return 'in_progress';
}

/**
 * Get status icon based on output status (Issue #354)
 */
function getStatusIcon(status: OutputStatus): string {
  switch (status) {
    case 'in_progress':
      return '🔄';
    case 'completed':
      return '✅';
    case 'warning':
      return '⚠️';
    case 'error':
      return '❌';
    default:
      return '';
  }
}

/**
 * Generate unique key for output segment
 * Uses timestamp + text hash to ensure stable keys across renders
 */
function generateSegmentKey(timestamp: string, text: string, index: number): string {
  // Use first 8 chars of timestamp and first 8 chars of text for stable key
  const tsPart = timestamp?.slice(0, 8) || 'no-ts';
  const textPart = text?.slice(0, 8).replace(/\s/g, '_') || 'no-txt';
  return `${tsPart}-${textPart}-${index}`;
}

/**
 * Parse qwen CLI stream-json output line into displayable segments.
 * Enhanced with status detection for Issue #354 and error friendly messages for Issue #140.
 *
 * The stream-json format produces JSON lines with these types:
 * - system/init: session initialization metadata → skip
 * - assistant: LLM response with content blocks (text, thinking, tool_use) → extract text
 * - result: completion status → only show errors (success results produce no output to avoid redundancy)
 *
 * Key insight: A message may contain multiple tool_use blocks. We display them as "in_progress"
 * because they represent actions being performed. The final "completed" status is only shown
 * when we see a successful "result" type (but we don't add extra "Completed" text to avoid redundancy).
 *
 * Issue #140: CLI error messages are converted to user-friendly descriptions.
 */
function parseStreamJsonLine(
  data: string,
  timestamp: string,
  segmentIndex: number,
  language: Language
): OutputSegment[] {
  const trimmed = data.trim();
  if (!trimmed) return [];

  // Try to parse as JSON
  let parsed: Record<string, unknown>;
  try {
    parsed = JSON.parse(trimmed);
  } catch {
    // Not JSON — display as-is with status detection
    const status = determineTextStatus(trimmed);
    return [
      {
        text: trimmed,
        type: 'text',
        status,
        timestamp,
        key: generateSegmentKey(timestamp, trimmed, segmentIndex),
      },
    ];
  }

  const msgType = parsed.type as string;

  if (msgType === 'assistant') {
    const message = parsed.message as Record<string, unknown> | undefined;
    if (!message) return [];
    const content = message.content as Array<Record<string, unknown>> | undefined;
    if (!Array.isArray(content)) return [];

    const segments: OutputSegment[] = [];
    let blockIdx = 0;
    for (const block of content) {
      const blockType = block.type as string;
      if (blockType === 'text') {
        const text = (block.text as string) || '';
        if (text) {
          const status = determineTextStatus(text);
          segments.push({
            text,
            type: 'text',
            status,
            timestamp,
            key: generateSegmentKey(timestamp, text, segmentIndex + blockIdx),
          });
          blockIdx++;
        }
      }
      // Extract tool_use name for display (Issue #354)
      // Tool calls are always shown as in_progress since they represent actions
      if (blockType === 'tool_use') {
        const toolName = (block.name as string) || '';
        if (toolName) {
          segments.push({
            text: `Tool: ${toolName}`,
            type: 'text',
            status: 'in_progress', // Tool calls are actions being performed
            toolName,
            timestamp,
            key: generateSegmentKey(timestamp, `tool-${toolName}`, segmentIndex + blockIdx),
          });
          blockIdx++;
        }
      }
    }
    return segments;
  }

  // OpenAI-compatible chat completion response: {"type":"message","role":"assistant","content":"..."}
  if (msgType === 'message') {
    const role = parsed.role as string;
    if (role === 'assistant') {
      const content = parsed.content as string | Array<Record<string, unknown>> | undefined;
      if (typeof content === 'string' && content) {
        const status = determineTextStatus(content);
        return [
          {
            text: content,
            type: 'text',
            status,
            timestamp,
            key: generateSegmentKey(timestamp, content, segmentIndex),
          },
        ];
      }
      if (Array.isArray(content)) {
        return content
          .filter((b) => b.type === 'text' && b.text)
          .map((b, idx) => {
            const text = b.text as string;
            const status = determineTextStatus(text);
            return {
              text,
              type: 'text' as const,
              status,
              timestamp,
              key: generateSegmentKey(timestamp, text, segmentIndex + idx),
            };
          });
      }
    }
    return [];
  }

  if (msgType === 'result') {
    const subtype = parsed.subtype as string;
    if (subtype === 'error') {
      const rawError = (parsed.error as string) || (parsed.result as string) || 'Unknown error';
      // Issue #140: Convert CLI error message to user-friendly description
      const friendlyError = getFriendlyErrorMessage(rawError, language);
      return [
        {
          text: friendlyError,
          type: 'error',
          status: 'error',
          timestamp,
          key: generateSegmentKey(timestamp, `error-${rawError}`, segmentIndex),
        },
      ];
    }
    // Success result — show ✅ completed icon without text to avoid redundancy (Issue #354)
    // Code review fix: The completed status should be visible via icon, but without "Completed" text
    return [
      {
        text: '', // Empty text — only show ✅ icon, no redundant "Completed" text
        type: 'text',
        status: 'completed',
        timestamp,
        key: generateSegmentKey(timestamp, 'completed', segmentIndex),
      },
    ];
  }

  // system/init and other types — skip
  return [];
}

/**
 * Remote Output Section - Displays remote session output with timestamps and status icons
 * Enhanced for Issue #354: Adds timestamps, status indicators, and verbose mode toggle
 *
 * Code review fixes:
 * - verboseMode defaults to true (Issue expects timestamps on every output)
 * - useMemo dependency fixed: output accessed inside useMemo to avoid new array per render
 * - React keys use stable timestamp+text combination instead of idx
 */
const RemoteOutputSection: React.FC<{ sessionId: string; language: Language }> = ({
  sessionId,
  language,
}) => {
  const outputRef = useRef<HTMLDivElement>(null);
  const { data: remoteData, isLoading } = useRemoteSession(sessionId);
  // Issue #354: Default to verbose mode since issue expects "timestamps on every output"
  const [verboseMode, setVerboseMode] = useState(true);

  // Parse all output entries into displayable segments with timestamps and status
  // Code review fix: access output inside useMemo to avoid creating new array per render
  // Issue #140: Pass language for error friendly messages
  const displaySegments = useMemo(() => {
    const output = remoteData?.session?.output;
    if (!output) return [];

    const segments: OutputSegment[] = [];
    let globalIdx = 0;
    for (const entry of output) {
      // Only process stdout entries (skip stderr noise)
      if (entry.stream === 'stderr') continue;
      const parsed = parseStreamJsonLine(entry.data, entry.timestamp, globalIdx, language);
      for (const seg of parsed) {
        segments.push(seg);
        globalIdx++;
      }
    }
    return segments;
  }, [remoteData?.session?.output, language]);

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

  // Get status color class
  const getStatusClass = (status: OutputStatus): string => {
    switch (status) {
      case 'error':
        return 'text-danger';
      case 'warning':
        return 'text-warning';
      case 'in_progress':
        return 'text-info';
      case 'completed':
        return 'text-success';
      default:
        return '';
    }
  };

  return (
    <div className="mt-3">
      {/* Header with verbose mode toggle */}
      <div className="d-flex justify-content-between align-items-center mb-2">
        <h6 className="mb-0">
          <i className="bi bi-terminal me-1" />
          {t('remoteOutput', language)}
        </h6>
        <button
          type="button"
          className={`btn btn-sm ${verboseMode ? 'btn-outline-primary' : 'btn-outline-secondary'}`}
          onClick={() => setVerboseMode(!verboseMode)}
          title={verboseMode ? t('compactMode', language) : t('verboseMode', language)}
        >
          {verboseMode ? (
            <>
              <i className="bi bi-arrows-collapse me-1" />
              {t('compact', language) ?? 'Compact'}
            </>
          ) : (
            <>
              <i className="bi bi-arrows-expand me-1" />
              {t('verbose', language) ?? 'Verbose'}
            </>
          )}
        </button>
      </div>

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
          {displaySegments.map((seg) => (
            <div key={seg.key} className={`mb-1 ${getStatusClass(seg.status)}`}>
              {/* Timestamp (Issue #354) */}
              {verboseMode && seg.timestamp && (
                <span className="text-secondary me-2">
                  {formatTimestampWithSeconds(seg.timestamp)}
                </span>
              )}
              {/* Status icon (Issue #354) */}
              <span className="me-1">{getStatusIcon(seg.status)}</span>
              {/* Content */}
              <span>{seg.text}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};

/**
 * MessageContent - Renders message content with structured block support.
 * Falls back to plain text when no content_blocks are available.
 */
const MessageContent: React.FC<{
  msg: SessionMessage;
  searchText: string;
  highlightText: (text: string, search: string) => string;
}> = ({ msg, searchText, highlightText }) => {
  const blocks = msg.metadata?.content_blocks as ContentBlock[] | undefined;

  if (!blocks || blocks.length === 0) {
    return (
      <div
        className="message-content"
        style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}
        dangerouslySetInnerHTML={{ __html: highlightText(msg.content, searchText) }}
      />
    );
  }

  return (
    <div className="message-content">
      {blocks.map((block, idx) => (
        <ContentBlockRenderer
          key={idx}
          block={block}
          searchText={searchText}
          highlightText={highlightText}
        />
      ))}
    </div>
  );
};

/**
 * ContentBlockRenderer - Renders a single content block based on its type.
 */
const ContentBlockRenderer: React.FC<{
  block: ContentBlock;
  searchText: string;
  highlightText: (text: string, search: string) => string;
}> = ({ block, searchText, highlightText }) => {
  switch (block.type) {
    case 'text':
      return (
        <div
          style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}
          dangerouslySetInnerHTML={{ __html: highlightText(block.text, searchText) }}
        />
      );

    case 'thinking':
      return (
        <details className="border-start border-3 border-secondary ps-2 mb-1">
          <summary className="small text-muted" style={{ cursor: 'pointer' }}>
            <i className="bi bi-lightbulb me-1" />
            Thinking
          </summary>
          <div
            className="mt-1 small text-muted"
            style={{
              whiteSpace: 'pre-wrap',
              wordBreak: 'break-word',
              maxHeight: '200px',
              overflowY: 'auto',
            }}
          >
            {block.thinking}
          </div>
        </details>
      );

    case 'tool_use':
      return (
        <details className="border-start border-3 border-info ps-2 mb-1">
          <summary className="small" style={{ cursor: 'pointer' }}>
            <Badge variant="info" className="me-1">
              Tool
            </Badge>
            <span className="fw-medium">{block.name}</span>
          </summary>
          <div
            className="mt-1 bg-dark text-light rounded p-2 small"
            style={{
              fontFamily: 'monospace',
              whiteSpace: 'pre-wrap',
              wordBreak: 'break-word',
              maxHeight: '200px',
              overflowY: 'auto',
            }}
          >
            {formatToolInput(block.input)}
          </div>
        </details>
      );

    case 'tool_result':
      return (
        <details className="border-start border-3 border-success ps-2 mb-1">
          <summary className="small" style={{ cursor: 'pointer' }}>
            <Badge variant="success" className="me-1">
              Result
            </Badge>
          </summary>
          <div
            className="mt-1 bg-dark text-light rounded p-2 small"
            style={{
              fontFamily: 'monospace',
              whiteSpace: 'pre-wrap',
              wordBreak: 'break-word',
              maxHeight: '200px',
              overflowY: 'auto',
            }}
          >
            {typeof block.content === 'string'
              ? block.content
              : (block.content
                  ?.filter((b) => b.type === 'text')
                  .map((b) => b.text)
                  .join('\n') ?? '')}
          </div>
        </details>
      );

    case 'reasoning':
      return (
        <details className="border-start border-3 border-secondary ps-2 mb-1">
          <summary className="small text-muted" style={{ cursor: 'pointer' }}>
            <i className="bi bi-lightbulb me-1" />
            Reasoning
          </summary>
          <div
            className="mt-1 small text-muted"
            style={{
              whiteSpace: 'pre-wrap',
              wordBreak: 'break-word',
              maxHeight: '200px',
              overflowY: 'auto',
            }}
          >
            {block.summary}
          </div>
        </details>
      );

    case 'file_change':
      return (
        <details
          className={`border-start border-3 ps-2 mb-1 ${block.status === 'accepted' ? 'border-success' : 'border-danger'}`}
        >
          <summary className="small" style={{ cursor: 'pointer' }}>
            <Badge variant={block.status === 'accepted' ? 'success' : 'danger'} className="me-1">
              {block.status === 'accepted' ? 'Accepted' : 'Declined'}
            </Badge>
            <span className="fw-medium">
              {block.changes.length} file change{block.changes.length !== 1 ? 's' : ''}
            </span>
          </summary>
          <div className="mt-1 small">
            {block.changes.map((change, i) => (
              <div key={i} className="d-flex align-items-center mb-1">
                <Badge
                  variant={
                    change.change_type === 'add'
                      ? 'success'
                      : change.change_type === 'delete'
                        ? 'danger'
                        : 'warning'
                  }
                  className="me-1"
                  pill
                >
                  {change.change_type.toUpperCase()}
                </Badge>
                <code className="small">{change.path}</code>
              </div>
            ))}
          </div>
        </details>
      );

    case 'task_summary':
      return (
        <div className="border-top pt-2 mt-2 mb-2">
          <div className="d-flex align-items-center mb-1">
            <i className="bi bi-check-circle me-1 text-success" />
            <span className="small fw-medium">Task Complete</span>
            {block.duration_ms > 0 && (
              <span className="ms-2 small text-muted">
                ({(block.duration_ms / 1000).toFixed(1)}s)
              </span>
            )}
          </div>
          <div className="small" style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
            {block.text}
          </div>
        </div>
      );

    default:
      return null;
  }
};

function formatToolInput(input: Record<string, unknown>): string {
  try {
    const sanitized: Record<string, unknown> = {};
    for (const [key, value] of Object.entries(input)) {
      if (typeof value === 'string' && value.length > 500) {
        sanitized[key] = value.slice(0, 500) + '... [truncated]';
      } else {
        sanitized[key] = value;
      }
    }
    return JSON.stringify(sanitized, null, 2);
  } catch {
    return String(input);
  }
}
