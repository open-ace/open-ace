import React from 'react';
import { Badge } from './Badge';
import type { ContentBlock, SessionMessage } from '@/api/sessions';

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

/**
 * ContentBlockRenderer - Renders a single content block based on its type.
 *
 * Extracted from SessionDetailContent so the transcript block rendering
 * contract (text / thinking / tool_use / tool_result / reasoning /
 * file_change / task_summary) is independently testable (#2047).
 */
export const ContentBlockRenderer: React.FC<{
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

/**
 * MessageContent - Renders a session message, preferring structured
 * ``content_blocks`` when present and falling back to plain ``content``.
 *
 * Extracted from SessionDetailContent so the message-rendering contract is
 * independently testable (#2047).
 */
export const MessageContent: React.FC<{
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
