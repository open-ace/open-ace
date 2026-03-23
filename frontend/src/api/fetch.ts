/**
 * Fetch API - Data collection API calls
 */

import { apiClient } from './client';

export interface FetchStatus {
  is_running: boolean;
  last_run: string | null;
  last_result: Record<string, { success: boolean; error?: string }> | null;
  error: string | null;
}

export interface FetchResponse {
  success: boolean;
  message: string;
  status: FetchStatus;
}

export const fetchApi = {
  /**
   * Trigger data collection from all sources
   */
  async fetchData(): Promise<FetchResponse> {
    return apiClient.post<FetchResponse>('/api/fetch/data');
  },

  /**
   * Get data fetch status
   */
  async getFetchStatus(): Promise<{ success: boolean; status: FetchStatus }> {
    return apiClient.get<{ success: boolean; status: FetchStatus }>('/api/fetch/status');
  },
};