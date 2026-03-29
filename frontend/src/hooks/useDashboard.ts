/**
 * useDashboard Hook - Dashboard data fetching hook
 *
 * Performance optimizations:
 * - Uses useQueries for parallel data fetching (async-parallel)
 * - Combines results to reduce redundant state checks
 */

import { useQuery, useQueries } from '@tanstack/react-query';
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
  const { tool, host, autoRefresh = false, refreshInterval = 60000 } = options;

  // Use useQueries for parallel data fetching (async-parallel optimization)
  const queries = useQueries({
    queries: [
      {
        queryKey: ['dashboard', 'today', { tool, host }],
        queryFn: () => dashboardApi.getTodayUsage(tool, host),
        staleTime: 30 * 1000, // 30 seconds
        refetchInterval: autoRefresh ? refreshInterval : false,
        refetchOnWindowFocus: true,
      },
      {
        queryKey: ['dashboard', 'summary', { host }],
        queryFn: () => dashboardApi.getSummary(host),
        staleTime: 5 * 60 * 1000, // 5 minutes
        refetchOnWindowFocus: true,
      },
      {
        queryKey: ['dashboard', 'hosts'],
        queryFn: dashboardApi.getHosts,
        staleTime: 30 * 60 * 1000, // 30 minutes
      },
    ],
    combine: (results) => ({
      todayData: (results[0].data as ToolUsage[]) ?? [],
      summaryData: (results[1].data as SummaryData) ?? {},
      hosts: (results[2].data as string[]) ?? [],
      isLoading: results[0].isLoading || results[1].isLoading,
      isFetching: results[0].isFetching || results[1].isFetching,
      isError: results[0].isError || results[1].isError,
      error: results[0].error ?? results[1].error,
      refetch: async () => {
        await Promise.all([results[0].refetch(), results[1].refetch()]);
      },
    }),
  });

  return queries;
}
