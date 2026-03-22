/**
 * Analysis Hooks - Custom hooks for analysis operations
 */

import { useQuery } from '@tanstack/react-query';
import { analysisApi } from '@/api';

export function useKeyMetrics(startDate?: string, endDate?: string, host?: string) {
  return useQuery({
    queryKey: ['analysis', 'key-metrics', startDate, endDate, host],
    queryFn: () => analysisApi.getKeyMetrics(startDate, endDate, host),
  });
}

export function useDailyHourlyUsage(startDate?: string, endDate?: string, host?: string) {
  return useQuery({
    queryKey: ['analysis', 'daily-hourly-usage', startDate, endDate, host],
    queryFn: () => analysisApi.getDailyHourlyUsage(startDate, endDate, host),
  });
}

export function usePeakUsage(startDate?: string, endDate?: string, host?: string) {
  return useQuery({
    queryKey: ['analysis', 'peak-usage', startDate, endDate, host],
    queryFn: () => analysisApi.getPeakUsage(startDate, endDate, host),
  });
}

export function useUserRanking(
  startDate?: string,
  endDate?: string,
  host?: string,
  limit?: number
) {
  return useQuery({
    queryKey: ['analysis', 'user-ranking', startDate, endDate, host, limit],
    queryFn: () => analysisApi.getUserRanking(startDate, endDate, host, limit),
  });
}

export function useConversationStats(startDate?: string, endDate?: string, host?: string) {
  return useQuery({
    queryKey: ['analysis', 'conversation-stats', startDate, endDate, host],
    queryFn: () => analysisApi.getConversationStats(startDate, endDate, host),
  });
}

export function useToolComparison(startDate?: string, endDate?: string, host?: string) {
  return useQuery({
    queryKey: ['analysis', 'tool-comparison', startDate, endDate, host],
    queryFn: () => analysisApi.getToolComparison(startDate, endDate, host),
  });
}

export function useUserSegmentation(startDate?: string, endDate?: string, host?: string) {
  return useQuery({
    queryKey: ['analysis', 'user-segmentation', startDate, endDate, host],
    queryFn: () => analysisApi.getUserSegmentation(startDate, endDate, host),
  });
}

export function useRecommendations(host?: string) {
  return useQuery({
    queryKey: ['analysis', 'recommendations', host],
    queryFn: () => analysisApi.getRecommendations(host),
  });
}
