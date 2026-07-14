/**
 * Tests for the ConversationHistory latency-detail ↔ message linkage feature.
 *
 * Covers:
 *  - The pure latency calculator (`computeLatencyData`): index/responseIndex
 *    mapping, content capture, tool-role handling, single-pair-per-user rule.
 *  - The preview truncator (`truncatePreview`).
 *  - The interaction: clicking a latency row jumps to the timeline tab and
 *    highlights the response message (resetting any active role filter).
 */

import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import {
  computeLatencyData,
  truncatePreview,
  ConversationDetailModal,
  type LatencyMessage,
} from './ConversationHistory';
import type { ConversationMessage } from '@/api';
import { t } from '@/i18n';

// ---------------------------------------------------------------------------
// Pure-function tests
// ---------------------------------------------------------------------------

describe('truncatePreview', () => {
  it('returns empty string for empty content', () => {
    expect(truncatePreview('')).toBe('');
  });

  it('collapses whitespace and trims', () => {
    expect(truncatePreview('  hello   world  ')).toBe('hello world');
  });

  it('leaves short content unchanged', () => {
    expect(truncatePreview('short message')).toBe('short message');
  });

  it('truncates long content to 40 chars with ellipsis', () => {
    const long = 'a'.repeat(100);
    const result = truncatePreview(long);
    expect(result).toHaveLength(41); // 40 + ellipsis
    expect(result.endsWith('…')).toBe(true);
  });
});

describe('computeLatencyData', () => {
  // Build a message with sane defaults so tests stay readable.
  const mk = (
    role: string,
    ts: string,
    content = 'msg',
    extra: Partial<ConversationMessage> = {}
  ): ConversationMessage =>
    ({
      id: 0,
      feishu_conversation_id: '',
      tool_name: '',
      host_name: '',
      sender_name: '',
      sender_id: '',
      date: '',
      role,
      content,
      tokens_used: 0,
      input_tokens: 0,
      output_tokens: 0,
      timestamp: ts,
      ...extra,
    }) as ConversationMessage;

  it('returns [] for fewer than 2 messages', () => {
    expect(computeLatencyData([])).toEqual([]);
    expect(computeLatencyData([mk('user', '2024-01-01T00:00:00Z')])).toEqual([]);
  });

  it('records the user index and response index (1-based) for a single pair', () => {
    const messages: LatencyMessage[] = [
      mk('user', '2024-01-01T00:00:00Z', 'what is 1+1?', { id: 1 }),
      mk('assistant', '2024-01-01T00:00:02Z', '2', { id: 2 }),
    ];
    const data = computeLatencyData(messages);
    expect(data).toHaveLength(1);
    // user message is at position 0 -> index 1; response at position 1 -> responseIndex 2
    expect(data[0].index).toBe(1);
    expect(data[0].responseIndex).toBe(2);
    expect(data[0].latency).toBe(2);
    // content is the response message content
    expect(data[0].content).toBe('2');
    // userContent is the triggering user message content
    expect(data[0].userContent).toBe('what is 1+1?');
    expect(data[0].role).toBe('assistant');
  });

  it('handles multiple user/assistant turns with correct indices', () => {
    const messages: LatencyMessage[] = [
      mk('user', '2024-01-01T00:00:00Z', 'q1', { id: 1 }),
      mk('assistant', '2024-01-01T00:00:03Z', 'a1', { id: 2 }),
      mk('user', '2024-01-01T00:00:10Z', 'q2', { id: 3 }),
      mk('assistant', '2024-01-01T00:00:16Z', 'a2', { id: 4 }),
    ];
    const data = computeLatencyData(messages);
    expect(data).toHaveLength(2);
    expect(data[0].index).toBe(1);
    expect(data[0].responseIndex).toBe(2);
    expect(data[0].latency).toBe(3);
    expect(data[1].index).toBe(3);
    expect(data[1].responseIndex).toBe(4);
    expect(data[1].latency).toBe(6);
  });

  it('pairs the canonical "tool" role as a response', () => {
    const messages: LatencyMessage[] = [
      mk('user', '2024-01-01T00:00:00Z', 'use tool', { id: 1 }),
      mk('tool', '2024-01-01T00:00:05Z', 'tool result', { id: 2 }),
    ];
    const data = computeLatencyData(messages);
    expect(data).toHaveLength(1);
    expect(data[0].role).toBe('tool');
    expect(data[0].latency).toBe(5);
    expect(data[0].content).toBe('tool result');
  });

  it('also pairs the legacy "toolResult" spelling as a response', () => {
    const messages: LatencyMessage[] = [
      mk('user', '2024-01-01T00:00:00Z', 'use tool', { id: 1 }),
      mk('toolResult', '2024-01-01T00:00:05Z', 'legacy result', { id: 2 }),
    ];
    const data = computeLatencyData(messages);
    expect(data).toHaveLength(1);
    expect(data[0].role).toBe('toolResult');
    expect(data[0].content).toBe('legacy result');
  });

  it('records only the first response after a user message (existing behavior)', () => {
    const messages: LatencyMessage[] = [
      mk('user', '2024-01-01T00:00:00Z', 'q', { id: 1 }),
      mk('assistant', '2024-01-01T00:00:02Z', 'a-first', { id: 2 }),
      // Second response is not paired because lastUserTime was reset.
      mk('assistant', '2024-01-01T00:00:04Z', 'a-second', { id: 3 }),
    ];
    const data = computeLatencyData(messages);
    expect(data).toHaveLength(1);
    expect(data[0].content).toBe('a-first');
  });

  it('skips non-positive latency (response at or before user time)', () => {
    const messages: LatencyMessage[] = [
      mk('user', '2024-01-01T00:00:00Z', 'q', { id: 1 }),
      // Same timestamp -> 0 latency, dropped.
      mk('assistant', '2024-01-01T00:00:00Z', 'a', { id: 2 }),
    ];
    expect(computeLatencyData(messages)).toEqual([]);
  });

  it('ignores assistant messages with no preceding user message', () => {
    const messages: LatencyMessage[] = [
      mk('assistant', '2024-01-01T00:00:00Z', 'orphan', { id: 1 }),
      mk('user', '2024-01-01T00:00:05Z', 'q', { id: 2 }),
    ];
    expect(computeLatencyData(messages)).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// Interaction tests
// ---------------------------------------------------------------------------

// The modal pulls its data via useConversationTimeline; mock it so the test
// controls the rendered conversation.
vi.mock('@/hooks', async () => {
  const actual = await vi.importActual<typeof import('@/hooks')>('@/hooks');
  return {
    ...actual,
    useConversationTimeline: vi.fn(),
  };
});

import { useConversationTimeline } from '@/hooks';
const mockedUseConversationTimeline = vi.mocked(useConversationTimeline);

const sampleMessages: ConversationMessage[] = [
  {
    id: 1,
    feishu_conversation_id: '',
    tool_name: '',
    host_name: '',
    sender_name: 'alice',
    sender_id: '',
    date: '',
    role: 'user',
    content: 'Hello, can you help me?',
    tokens_used: 0,
    input_tokens: 0,
    output_tokens: 0,
    timestamp: '2024-01-01T00:00:00Z',
  },
  {
    id: 2,
    feishu_conversation_id: '',
    tool_name: '',
    host_name: '',
    sender_name: 'assistant',
    sender_id: '',
    date: '',
    role: 'assistant',
    content: 'Sure, here is the answer.',
    tokens_used: 0,
    input_tokens: 0,
    output_tokens: 0,
    timestamp: '2024-01-01T00:00:04Z',
  },
  {
    id: 3,
    feishu_conversation_id: '',
    tool_name: '',
    host_name: '',
    sender_name: 'alice',
    sender_id: '',
    date: '',
    role: 'user',
    content: 'Another question here.',
    tokens_used: 0,
    input_tokens: 0,
    output_tokens: 0,
    timestamp: '2024-01-01T00:00:10Z',
  },
  {
    id: 4,
    feishu_conversation_id: '',
    tool_name: '',
    host_name: '',
    sender_name: 'assistant',
    sender_id: '',
    date: '',
    role: 'assistant',
    content: 'Another answer here.',
    tokens_used: 0,
    input_tokens: 0,
    output_tokens: 0,
    timestamp: '2024-01-01T00:00:20Z',
  },
];

const renderWithProviders = (ui: ReactNode) => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0, staleTime: 0 } },
  });
  return render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>);
};

describe('ConversationDetailModal latency linkage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockedUseConversationTimeline.mockReturnValue({
      data: sampleMessages,
      isLoading: false,
      isError: false,
    } as unknown as ReturnType<typeof useConversationTimeline>);
  });

  it('renders the latency tab with response message previews', async () => {
    renderWithProviders(
      <ConversationDetailModal sessionId="s1" language="en" onClose={() => {}} />
    );

    // Switch to the latency tab.
    fireEvent.click(screen.getByRole('button', { name: /Latency Curve/i }));

    // The latency details table shows the preview of the response content.
    expect(await screen.findByText('Sure, here is the answer.')).toBeInTheDocument();
    expect(screen.getByText('Another answer here.')).toBeInTheDocument();
  });

  it('clicking a latency row switches to the timeline and highlights the response message', async () => {
    renderWithProviders(
      <ConversationDetailModal sessionId="s1" language="en" onClose={() => {}} />
    );

    // Go to the latency tab.
    fireEvent.click(screen.getByRole('button', { name: /Latency Curve/i }));

    // The first latency row maps to the first response (id 2, "Sure...").
    const rows = screen.getAllByRole('row');
    // rows[0] is the header; the first data row is rows[1].
    fireEvent.click(rows[1]);

    // Back on the timeline, the highlighted response message carries the
    // highlight class.
    const highlighted = await screen.findByText('Sure, here is the answer.');
    const card = highlighted.closest('.message-item');
    expect(card?.className).toContain('message-highlighted');
  });

  it('clicking a latency row resets an active role filter so the target is visible', async () => {
    renderWithProviders(
      <ConversationDetailModal sessionId="s1" language="en" onClose={() => {}} />
    );

    // On the timeline, filter to user-only messages first.
    fireEvent.click(screen.getByRole('button', { name: /^User$/ }));
    // Only the two user messages are visible now.
    expect(screen.getByText('Hello, can you help me?')).toBeInTheDocument();
    // The assistant response is filtered out.
    expect(screen.queryByText('Sure, here is the answer.')).not.toBeInTheDocument();

    // Switch to latency and click the first row (response id 2).
    fireEvent.click(screen.getByRole('button', { name: /Latency Curve/i }));
    const rows = screen.getAllByRole('row');
    fireEvent.click(rows[1]);

    // After jumping, the filter is reset to "all" and the response is visible.
    await waitFor(() => {
      expect(screen.getByText('Sure, here is the answer.')).toBeInTheDocument();
    });
  });

  it('shows a message-number badge on each timeline message matching the latency index', () => {
    renderWithProviders(
      <ConversationDetailModal sessionId="s1" language="en" onClose={() => {}} />
    );

    // On the timeline, the first message (index 1) and the response (index 2)
    // both show their sequence-number badges.
    expect(screen.getByText('#1')).toBeInTheDocument();
    expect(screen.getByText('#2')).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Issue #1432: i18n tests for conversation history title
// ---------------------------------------------------------------------------

describe('Issue #1432: i18n keys for conversation history title', () => {
  it('has conversationHistoryListCount key in all languages', () => {
    const languages = ['en', 'zh', 'ja', 'ko'] as const;
    for (const lang of languages) {
      const result = t('conversationHistoryListCount', lang, { count: 25 });
      expect(result).toContain('25');
    }
  });

  it('has noConversationRecords key in all languages', () => {
    const languages = ['en', 'zh', 'ja', 'ko'] as const;
    for (const lang of languages) {
      const result = t('noConversationRecords', lang);
      expect(result.length).toBeGreaterThan(0);
    }
  });

  it('interpolates count correctly', () => {
    expect(t('conversationHistoryListCount', 'en', { count: 100 })).toBe('100 conversations');
    expect(t('conversationHistoryListCount', 'zh', { count: 100 })).toBe('100 个对话记录');
    expect(t('conversationHistoryListCount', 'ja', { count: 100 })).toBe('100件の会話記録');
    expect(t('conversationHistoryListCount', 'ko', { count: 100 })).toBe('100개 대화 기록');
  });
});
