/**
 * Tests for the shared SessionStatisticsCard.
 *
 * Most importantly: pins down the field-name contract between the backend
 * payload and the component, so the "平均 Tokens/对话" row can never silently
 * fall back to 0 again (a regression where the component read
 * `avg_tokens_per_conversation` while the backend emits
 * `average_tokens_per_conversation`).
 */

import { describe, expect, it } from 'vitest';
import { render, screen } from '@/test/utils';
import type { ConversationStats } from '@/api/analysis';
import { SessionStatisticsCard } from './SessionStatisticsCard';

const stats: ConversationStats = {
  total_conversations: 5,
  total_messages: 12,
  multi_turn_session_count: 3,
  multi_turn_ratio: 0.6,
  average_messages_per_conversation: 2.4,
  average_tokens_per_conversation: 1234,
  avg_conversation_length: 2.4,
};

describe('SessionStatisticsCard', () => {
  it('renders the real average-tokens value, not a 0 fallback', () => {
    render(<SessionStatisticsCard conversationStats={stats} />);
    // formatTokens(1234) === '1.23K'. The field-name regression rendered '0'.
    const cell = screen.getByTestId('session-avg-tokens').querySelector('.text-end');
    expect(cell?.textContent?.trim()).toBe('1.23K');
  });

  it('renders multi-turn ratio as a percentage derived from multi_turn_ratio', () => {
    render(<SessionStatisticsCard conversationStats={stats} />);
    const cell = screen.getByTestId('session-multi-turn-ratio').querySelector('.text-end');
    expect(cell?.textContent?.trim()).toBe('60.0%');
  });

  it('renders all rows from the single conversation_stats source', () => {
    render(<SessionStatisticsCard conversationStats={stats} />);
    expect(
      screen
        .getByTestId('session-total-conversations')
        .querySelector('.text-end')
        ?.textContent?.trim()
    ).toBe('5');
    expect(
      screen.getByTestId('session-total-messages').querySelector('.text-end')?.textContent?.trim()
    ).toBe('12');
    expect(
      screen.getByTestId('session-avg-messages').querySelector('.text-end')?.textContent?.trim()
    ).toBe('2.4');
  });

  it('clamps an out-of-range ratio into a valid percentage', () => {
    render(<SessionStatisticsCard conversationStats={{ ...stats, multi_turn_ratio: 1.5 }} />);
    const cell = screen.getByTestId('session-multi-turn-ratio').querySelector('.text-end');
    expect(cell?.textContent?.trim()).toBe('100.0%');
  });
});
