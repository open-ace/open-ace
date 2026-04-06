/**
 * Sessions API - Sessions related API calls
 */

import { apiClient } from './client';

// Session types matching backend AgentSession
export interface AgentSession {
  id: number | null;
  session_id: string;
  session_type: string;
  title: string;
  tool_name: string;
  host_name: string;
  user_id: number | null;
  status: string;
  context: Record<string, unknown>;
  settings: Record<string, unknown>;
  total_tokens: number;
  total_input_tokens: number;
  total_output_tokens: number;
  message_count: number;
  request_count: number;
  model: string | null;
  tags: string[];
  created_at: string | null;
  updated_at: string | null;
  completed_at: string | null;
  expires_at: string | null;
  messages: SessionMessage[];
}

export interface SessionMessage {
  id: number | null;
  session_id: string;
  role: string;
  content: string;
  tokens_used: number;
  model: string | null;
  timestamp: string | null;
  metadata: Record<string, unknown>;
}

export interface SessionFilters {
  tool_name?: string;
  status?: string;
  session_type?: string;
  search?: string;
}

export interface SessionsListResponse {
  success: boolean;
  data: {
    sessions: AgentSession[];
    total: number;
    page: number;
    limit: number;
    total_pages: number;
  };
  error?: string;
}

export interface SessionStatsResponse {
  success: boolean;
  data: {
    total_sessions: number;
    active_sessions: number;
    completed_sessions: number;
    total_messages: number;
    total_tokens: number;
    by_tool: Record<string, number>;
    by_status: Record<string, number>;
  };
  error?: string;
}

export const sessionsApi = {
  /**
   * Get sessions with filters and pagination
   */
  async getSessions(
    filters: SessionFilters = {},
    page: number = 1,
    limit: number = 20
  ): Promise<SessionsListResponse> {
    const params: Record<string, string> = {
      page: String(page),
      limit: String(limit),
    };

    if (filters.tool_name) params.tool_name = filters.tool_name;
    if (filters.status) params.status = filters.status;
    if (filters.session_type) params.session_type = filters.session_type;
    if (filters.search) params.search = filters.search;

    const response = await apiClient.get<SessionsListResponse>('/api/workspace/sessions', params);
    return response;
  },

  /**
   * Get a single session by ID
   */
  async getSession(
    sessionId: string,
    includeMessages: boolean = false
  ): Promise<{ success: boolean; data: AgentSession; error?: string }> {
    const params: Record<string, string> = {};
    if (includeMessages) params.include_messages = 'true';

    const response = await apiClient.get<{ success: boolean; data: AgentSession; error?: string }>(
      `/api/workspace/sessions/${sessionId}`,
      params
    );
    return response;
  },

  /**
   * Get session statistics
   */
  async getSessionStats(): Promise<SessionStatsResponse> {
    const response = await apiClient.get<SessionStatsResponse>('/api/workspace/sessions/stats');
    return response;
  },

  /**
   * Delete a session
   */
  async deleteSession(sessionId: string): Promise<{ success: boolean; error?: string }> {
    const response = await apiClient.delete<{ success: boolean; error?: string }>(
      `/api/workspace/sessions/${sessionId}`
    );
    return response;
  },

  /**
   * Complete a session
   */
  async completeSession(sessionId: string): Promise<{ success: boolean; error?: string }> {
    const response = await apiClient.post<{ success: boolean; error?: string }>(
      `/api/workspace/sessions/${sessionId}/complete`,
      {}
    );
    return response;
  },

  /**
   * Rename a session
   */
  async renameSession(
    sessionId: string,
    newName: string
  ): Promise<{ success: boolean; data?: { session_id: string; title: string }; error?: string }> {
    const response = await apiClient.post<{
      success: boolean;
      data?: { session_id: string; title: string };
      error?: string;
    }>(`/api/workspace/sessions/${sessionId}/rename`, { name: newName });
    return response;
  },

  /**
   * Restore a historical session to workspace
   */
  async restoreSession(
    sessionId: string
  ): Promise<{
    success: boolean;
    data?: {
      session_id: string;
      encoded_project_name: string;
      tool_name: string;
      url: string;
    };
    error?: string;
  }> {
    const response = await apiClient.post<{
      success: boolean;
      data?: {
        session_id: string;
        encoded_project_name: string;
        tool_name: string;
        url: string;
      };
      error?: string;
    }>(`/api/workspace/sessions/${sessionId}/restore`, {});
    return response;
  },
};
