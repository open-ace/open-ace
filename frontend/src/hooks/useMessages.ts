/**
 * useMessages Hook - Messages data fetching hook
 */

import { useQuery, useInfiniteQuery } from '@tanstack/react-query';
import { messagesApi } from '@/api';
import type { Message, MessageFilters } from '@/types';

interface UseMessagesOptions {
  filters?: MessageFilters;
  pageSize?: number;
  enabled?: boolean;
  page?: number;
}

export function useMessages(options: UseMessagesOptions = {}) {
  const { filters = {}, pageSize = 20, enabled = true, page = 1 } = options;

  return useQuery({
    queryKey: ['messages', page, { filters, pageSize }],
    queryFn: () => messagesApi.getMessages(filters, page, pageSize),
    enabled,
    staleTime: 30 * 1000, // 30 seconds
  });
}

export function useInfiniteMessages(options: UseMessagesOptions = {}) {
  const { filters = {}, pageSize = 20, enabled = true } = options;

  return useInfiniteQuery({
    queryKey: ['messages', 'infinite', { filters, pageSize }],
    queryFn: ({ pageParam = 1 }) => messagesApi.getMessages(filters, pageParam as number, pageSize),
    initialPageParam: 1,
    getNextPageParam: (lastPage) => {
      const { pagination } = lastPage;
      if (pagination && pagination.page < pagination.totalPages) {
        return pagination.page + 1;
      }
      return undefined;
    },
    enabled,
    staleTime: 30 * 1000,
  });
}

export function useMessage(id: string, enabled = true) {
  return useQuery<Message>({
    queryKey: ['messages', id],
    queryFn: () => messagesApi.getMessage(id),
    enabled: enabled && !!id,
    staleTime: 60 * 1000, // 1 minute
  });
}

export function useMessageCount(filters: MessageFilters = {}, enabled: boolean = true) {
  return useQuery<number>({
    queryKey: ['messages', 'count', filters],
    queryFn: () => messagesApi.getMessageCount(filters),
    enabled,
    staleTime: 60 * 1000, // 1 minute
  });
}

// Conversation History hooks
interface UseConversationHistoryOptions {
  date?: string;
  tool?: string;
  host?: string;
  sender?: string;
  pageSize?: number;
  page?: number;
}

export function useConversationHistory(options: UseConversationHistoryOptions = {}) {
  const { date, tool, host, sender, pageSize = 20, page = 1 } = options;

  return useQuery({
    queryKey: ['conversation-history', page, { date, tool, host, sender, pageSize }],
    queryFn: () => messagesApi.getConversationHistory({ date, tool, host, sender }, page, pageSize),
    staleTime: 60 * 1000, // 1 minute cache
  });
}

export function useConversationTimeline(sessionId: string, enabled = true) {
  return useQuery({
    queryKey: ['conversation-timeline', sessionId],
    queryFn: () => messagesApi.getConversationTimeline(sessionId),
    enabled: enabled && !!sessionId,
    staleTime: 5 * 60 * 1000, // 5 minutes cache for conversation details
  });
}

export function useSenders(host?: string) {
  return useQuery<string[]>({
    queryKey: ['senders', host],
    queryFn: () => messagesApi.getSenders(host),
    staleTime: 5 * 60 * 1000, // 5 minutes
  });
}
