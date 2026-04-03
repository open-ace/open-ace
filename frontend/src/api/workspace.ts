/**
 * Workspace API - Workspace related API calls
 */

import { apiClient } from './client';

// Types
export interface WorkspaceConfig {
  enabled: boolean;
  url: string;
  multi_user_mode: boolean;
  port_range_start: number;
  port_range_end: number;
  max_instances: number;
  idle_timeout_minutes: number;
}

export interface UserWebUIResponse {
  success: boolean;
  url: string;
  token: string;
  system_account: string;
  multi_user_mode: boolean;
  error?: string;
}

export interface WebUIInstance {
  user_id: number;
  system_account: string;
  port: number;
  pid: number | null;
  url: string;
  allocated_at: string;
  last_activity: string;
  is_alive: boolean;
}

export interface WebUIInstancesResponse {
  success: boolean;
  instances: WebUIInstance[];
  active_count: number;
  max_instances: number;
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
      return {
        enabled: false,
        url: '',
        multi_user_mode: false,
        port_range_start: 9000,
        port_range_end: 9999,
        max_instances: 30,
        idle_timeout_minutes: 30,
      };
    }
  },

  /**
   * Get user-specific webui URL with authentication token
   * In multi-user mode, this starts a new instance if needed
   */
  async getUserWebUIUrl(): Promise<UserWebUIResponse> {
    try {
      const response = await apiClient.get<UserWebUIResponse>('/api/workspace/user-url');
      return response;
    } catch (error) {
      return {
        success: false,
        url: '',
        token: '',
        system_account: '',
        multi_user_mode: false,
        error: error instanceof Error ? error.message : 'Failed to get user webui URL',
      };
    }
  },

  /**
   * List all active webui instances (admin only)
   */
  async listInstances(): Promise<WebUIInstancesResponse | null> {
    try {
      const response = await apiClient.get<WebUIInstancesResponse>('/api/workspace/instances');
      return response;
    } catch {
      return null;
    }
  },

  /**
   * Stop a specific user's webui instance (admin only)
   */
  async stopInstance(userId: number): Promise<boolean> {
    try {
      await apiClient.post(`/api/workspace/instances/${userId}/stop`);
      return true;
    } catch {
      return false;
    }
  },

  /**
   * Stop all webui instances (admin only)
   */
  async stopAllInstances(): Promise<boolean> {
    try {
      await apiClient.post('/api/workspace/instances/stop-all');
      return true;
    } catch {
      return false;
    }
  },
};
