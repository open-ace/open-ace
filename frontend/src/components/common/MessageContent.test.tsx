/**
 * Tests for MessageContent / ContentBlockRenderer transcript rendering (#2047).
 *
 * Locks the frontend side of the transcript contract: structured
 * ``content_blocks`` render the right card per block type, and a message with
 * only ``content`` (no blocks) renders the plain text rather than a blank
 * bubble. These are characterization tests for the extracted components.
 */

import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MessageContent, ContentBlockRenderer } from './MessageContent';
import type { ContentBlock, SessionMessage } from '@/api/sessions';

const identity = (text: string) => text;
const renderBlock = (block: ContentBlock) =>
  render(
    <ContentBlockRenderer block={block} searchText="" highlightText={identity} />
  );

describe('ContentBlockRenderer', () => {
  it('renders a text block', () => {
    renderBlock({ type: 'text', text: 'hello world' });
    expect(screen.getByText('hello world')).toBeTruthy();
  });

  it('renders a thinking block with a collapsible summary', () => {
    renderBlock({ type: 'thinking', thinking: 'pondering the result' });
    expect(screen.getByText('Thinking')).toBeTruthy();
    expect(screen.getByText('pondering the result')).toBeTruthy();
  });

  it('renders a tool_use block with a Tool badge and tool name', () => {
    renderBlock({ type: 'tool_use', id: 'tu1', name: 'bash', input: { cmd: 'ls' } });
    expect(screen.getByText('Tool')).toBeTruthy();
    expect(screen.getByText('bash')).toBeTruthy();
  });

  it('renders a tool_result block with a Result badge and content', () => {
    renderBlock({
      type: 'tool_result',
      tool_use_id: 'tu1',
      content: '9 passed in 0.8s',
      is_error: false,
    } as ContentBlock);
    expect(screen.getByText('Result')).toBeTruthy();
    expect(screen.getByText('9 passed in 0.8s')).toBeTruthy();
  });

  it('renders a reasoning block', () => {
    renderBlock({ type: 'reasoning', summary: 'deducing next step' } as ContentBlock);
    expect(screen.getByText('Reasoning')).toBeTruthy();
    expect(screen.getByText('deducing next step')).toBeTruthy();
  });
});

describe('MessageContent', () => {
  const baseMsg = (overrides: Partial<SessionMessage>): SessionMessage => ({
    id: 1,
    session_id: 's1',
    role: 'assistant',
    content: '',
    metadata: {},
    ...overrides,
  } as SessionMessage);

  it('renders structured content_blocks when present', () => {
    render(
      <MessageContent
        msg={baseMsg({
          content: '',
          metadata: {
            content_blocks: [
              { type: 'tool_use', id: 'tu1', name: 'bash', input: {} },
              { type: 'text', text: 'done' },
            ],
          },
        })}
        searchText=""
        highlightText={identity}
      />
    );
    expect(screen.getByText('bash')).toBeTruthy();
    expect(screen.getByText('done')).toBeTruthy();
  });

  it('falls back to plain content when there are no content_blocks', () => {
    render(
      <MessageContent
        msg={baseMsg({ content: 'plain answer', metadata: {} })}
        searchText=""
        highlightText={identity}
      />
    );
    expect(screen.getByText('plain answer')).toBeTruthy();
  });
});
