import React from 'react';

import { Card, Tooltip } from '@/components/common';
import { t } from '@/i18n';
import { useLanguage } from '@/store';
import { formatTokens } from '@/utils';
import type { ConversationStats } from '@/api/analysis';

/**
 * Shared health-score calculation for analysis pages.
 *
 * Extracted from the (previously duplicated) `calculateHealthScore` helpers in
 * `TrendAnalysis.tsx` and `Analysis.tsx` so the two pages can no longer drift
 * apart. The thresholds and the `avg_conversation_length` dependency are
 * intentionally unchanged.
 */
export function calculateHealthScore(
  keyMetrics: { total_sessions?: number; avg_tokens_per_session?: number } | undefined,
  conversationStats: { avg_conversation_length?: number } | undefined
): number {
  let score = 100;

  // Deduct points for low engagement
  if (keyMetrics?.avg_tokens_per_session && keyMetrics.avg_tokens_per_session < 1000) {
    score -= 20;
  }

  // Deduct points for short conversations
  if (conversationStats?.avg_conversation_length && conversationStats.avg_conversation_length < 2) {
    score -= 15;
  }

  return Math.max(0, Math.min(100, score));
}

/** Small "?" affordance that surfaces a metric's tooltip via a stable anchor. */
const HelpIcon: React.FC<{ tip: string; testId: string }> = ({ tip, testId }) => (
  <Tooltip content={tip} placement="top">
    <i
      data-testid={testId}
      className="bi bi-info-circle text-muted ms-1"
      style={{ cursor: 'help', fontSize: '0.8em' }}
      aria-label={tip}
    />
  </Tooltip>
);

interface SessionStatisticsCardProps {
  conversationStats: ConversationStats | undefined;
}

/**
 * Session statistics card — single, shared rendering for the Token Trend and
 * Analysis overview pages.
 *
 * Every metric is derived from one real, date-scoped calculation
 * (`conversation_stats`) instead of the previous mix of synthetic key-metrics
 * approximations. "多轮对话比例" now renders a true percentage
 * (`multi_turn_ratio`, conversations with >= 2 messages) rather than the
 * average message count that was previously mislabelled as a ratio.
 */
export const SessionStatisticsCard: React.FC<SessionStatisticsCardProps> = ({
  conversationStats,
}) => {
  const language = useLanguage();

  const totalConversations = conversationStats?.total_conversations ?? 0;
  const totalMessages = conversationStats?.total_messages ?? 0;
  const avgMessages = conversationStats?.average_messages_per_conversation ?? 0;
  const avgTokens = conversationStats?.average_tokens_per_conversation ?? 0;
  const multiTurnRatio = conversationStats?.multi_turn_ratio ?? 0;

  const rows: Array<{
    testId: string;
    label: string;
    helpKey: string;
    value: string;
  }> = [
    {
      testId: 'session-total-conversations',
      label: t('totalConversations', language),
      helpKey: 'totalConversationsHelp',
      value: totalConversations.toLocaleString(),
    },
    {
      testId: 'session-total-messages',
      label: t('totalMessages', language),
      helpKey: 'totalMessagesHelp',
      value: totalMessages.toLocaleString(),
    },
    {
      testId: 'session-avg-messages',
      label: t('avgMessagesPerConversation', language),
      helpKey: 'avgMessagesPerConversationHelp',
      value: avgMessages.toFixed(1),
    },
    {
      testId: 'session-avg-tokens',
      label: t('avgTokensPerConversation', language),
      helpKey: 'avgTokensPerConversationHelp',
      value: formatTokens(avgTokens),
    },
    {
      testId: 'session-multi-turn-ratio',
      label: t('multiTurnRatio', language),
      helpKey: 'multiTurnRatioHelp',
      // multi_turn_ratio is in [0, 1]; render as a percentage
      value: `${(Math.max(0, Math.min(1, multiTurnRatio)) * 100).toFixed(1)}%`,
    },
  ];

  return (
    <Card
      title={t('sessionStatistics', language)}
      helpTooltip={t('sessionStatisticsHelp', language)}
    >
      <table className="table table-sm" data-testid="session-statistics-card">
        <tbody>
          {rows.map((row) => (
            <tr key={row.testId} data-testid={row.testId}>
              <td>
                {row.label}
                <HelpIcon tip={t(row.helpKey, language)} testId={`${row.testId}-help`} />
              </td>
              <td className="text-end">{row.value}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
};

export default SessionStatisticsCard;
