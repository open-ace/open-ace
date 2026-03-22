/**
 * useDashboard Hook - Dashboard data fetching hook
 */

import { useQuery } from '@tanstack/react-query';
import { dashboardApi } from '@/api';
import type { ToolUsage, SummaryData } from '@/types';

interface UseDashboardOptions {
  tool?: string;
  host?: string;
  autoRefresh?: boolean;
  refreshInterval?: number;
}

export function useTodayUsage(options: UseDashboardOptions = {}) {
  const { tool, host, autoRefresh = false, refreshInterval = 60000 } = options;

  return useQuery<ToolUsage[]>({
    queryKey: ['dashboard', 'today', { tool, host }],
    queryFn: () => dashboardApi.getTodayUsage(tool, host),
    staleTime: 30 * 1000, // 30 seconds
    refetchInterval: autoRefresh ? refreshInterval : false,
    refetchOnWindowFocus: true,
  });
}

export function useSummary(host?: string) {
  return useQuery<SummaryData>({
    queryKey: ['dashboard', 'summary', { host }],
    queryFn: () => dashboardApi.getSummary(host),
    staleTime: 5 * 60 * 1000, // 5 minutes
    refetchOnWindowFocus: true,
  });
}

export function useTrendData(startDate: string, endDate: string, host?: string) {
  return useQuery({
    queryKey: ['dashboard', 'trend', { startDate, endDate, host }],
    queryFn: () => dashboardApi.getTrendData(startDate, endDate, host),
    staleTime: 5 * 60 * 1000, // 5 minutes
    enabled: !!startDate && !!endDate,
  });
}

export function useHosts() {
  return useQuery<string[]>({
    queryKey: ['dashboard', 'hosts'],
    queryFn: dashboardApi.getHosts,
    staleTime: 30 * 60 * 1000, // 30 minutes
  });
}

export function useDashboard(options: UseDashboardOptions = {}) {
  const todayQuery = useTodayUsage(options);
  const summaryQuery = useSummary(options.host);
  const hostsQuery = useHosts();

  return {
    todayData: todayQuery.data ?? [],
    summaryData: summaryQuery.data ?? {},
    hosts: hostsQuery.data ?? [],
    isLoading: todayQuery.isLoading || summaryQuery.isLoading,
    isFetching: todayQuery.isFetching || summaryQuery.isFetching,
    isError: todayQuery.isError || summaryQuery.isError,
    error: todayQuery.error || summaryQuery.error,
    refetch: async () => {
      await Promise.all([todayQuery.refetch(), summaryQuery.refetch()]);
    },
  };
}
