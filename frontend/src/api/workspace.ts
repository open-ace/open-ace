/**
 * Workspace API - Workspace related API calls
 */

import { apiClient } from './client';

// Types
export interface WorkspaceConfig {
  enabled: boolean;
  url: string;
}

// API
export const workspaceApi = {
  /**
   * Get workspace configuration
   */
  async getConfig(): Promise<WorkspaceConfig> {
    try {
      const response = await apiClient.get<WorkspaceConfig>('/api/workspace/config');
      return response;
    } catch {
      // Return default config if API fails
      return { enabled: false, url: '' };
    }
  },
};
