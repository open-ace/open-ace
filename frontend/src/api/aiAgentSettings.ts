/**
 * AI Agent Settings API - AI agent configuration API calls
 */

import { apiClient } from './client';

// Types
export interface AiAgentSettings {
  ai_github_token: string;
  ai_github_author_name: string;
  ai_github_author_email: string;
}

export interface TokenValidationResult {
  valid: boolean;
  username?: string;
  error?: string;
}

// API
export const aiAgentSettingsApi = {
  async getSettings(): Promise<AiAgentSettings> {
    return apiClient.get<AiAgentSettings>('/api/ai-agent/settings');
  },

  async updateSettings(data: Partial<AiAgentSettings>): Promise<{ success: boolean }> {
    return apiClient.put<{ success: boolean }>('/api/ai-agent/settings', data);
  },

  async validateGithubToken(token: string): Promise<TokenValidationResult> {
    return apiClient.post<TokenValidationResult>(
      '/api/ai-agent/settings/validate-github-token',
      { token }
    );
  },
};
