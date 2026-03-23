/**
 * useFetch Hook - Global data fetch hook
 */

import { useState, useEffect, useCallback } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { fetchApi, type FetchStatus } from '@/api';

const AUTO_REFRESH_INTERVAL = 60000; // 1 minute

export function useGlobalFetch() {
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<FetchStatus>({
    is_running: false,
    last_run: null,
    last_result: null,
    error: null,
  });
  const [autoRefresh, setAutoRefresh] = useState(false);

  // Fetch data from all sources
  const fetchData = useCallback(async () => {
    try {
      const response = await fetchApi.fetchData();
      if (response.success) {
        setStatus(response.status);
      }
      return response;
    } catch (error) {
      console.error('Failed to fetch data:', error);
      return { success: false, message: 'Failed to fetch data' };
    }
  }, []);

  // Get fetch status
  const getFetchStatus = useCallback(async () => {
    try {
      const response = await fetchApi.getFetchStatus();
      if (response.success) {
        setStatus(response.status);
      }
    } catch (error) {
      console.error('Failed to get fetch status:', error);
    }
  }, []);

  // Refresh all data (fetch + invalidate queries)
  const refreshAll = useCallback(async () => {
    const response = await fetchData();
    if (response?.success) {
      // Invalidate all queries to refetch data
      await queryClient.invalidateQueries();
    }
    return response;
  }, [fetchData, queryClient]);

  // Auto-refresh effect
  useEffect(() => {
    if (autoRefresh) {
      const interval = setInterval(() => {
        refreshAll();
      }, AUTO_REFRESH_INTERVAL);
      return () => clearInterval(interval);
    }
    return undefined;
  }, [autoRefresh, refreshAll]);

  // Poll for status when fetch is running
  useEffect(() => {
    if (status.is_running) {
      const interval = setInterval(() => {
        getFetchStatus();
      }, 2000); // Poll every 2 seconds
      return () => clearInterval(interval);
    }
    return undefined;
  }, [status.is_running, getFetchStatus]);

  return {
    status,
    autoRefresh,
    setAutoRefresh,
    fetchData,
    refreshAll,
    getFetchStatus,
  };
}