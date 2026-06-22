/**
 * Mapping Rules API - Managing auto-mapping rules for tool accounts
 */

import { apiClient } from './client';

// Types
export interface MappingRule {
  id: number;
  user_id: number;
  pattern: string;
  match_type: 'exact' | 'prefix' | 'suffix' | 'contains' | 'regex';
  tool_type: string | null;
  priority: number;
  is_auto: boolean;
  is_active: boolean;
  description: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface MappingStats {
  total_unmapped: number;
  total_mapped: number;
  unmapped_by_tool: Record<string, number>;
  mapped_by_tool: Record<string, number>;
  unmapped_accounts: UnmappedAccount[];
}

export interface UnmappedAccount {
  sender_name: string;
  message_count: number;
  first_date: string;
  last_date: string;
  inferred_tool_type?: string;
}

export interface AutoMappingResult {
  tool_account: string;
  user_id: number;
  username: string;
  matched_by: string;
  rule_id: number | null;
  created_mapping_id: number | null;
}

export interface MatchTestResult {
  matched: boolean;
  user_id?: number;
  username?: string;
  matched_by?: string;
  rule_id?: number;
}

export interface MappingSuggestion {
  suggested_user_id: number | null;
  suggested_username: string | null;
  matched_by: string | null;
  rule_id: number | null;
}

// API
export const mappingRulesApi = {
  async getAllRules(): Promise<MappingRule[]> {
    return apiClient.get<MappingRule[]>('/mapping-rules');
  },

  async getUserRules(userId: number): Promise<MappingRule[]> {
    return apiClient.get<MappingRule[]>(`/mapping-rules/user/${userId}`);
  },

  async createRule(data: {
    user_id: number;
    pattern: string;
    match_type?: string;
    tool_type?: string;
    priority?: number;
    is_auto?: boolean;
    is_active?: boolean;
    description?: string;
  }): Promise<MappingRule> {
    return apiClient.post<MappingRule>('/mapping-rules', data);
  },

  async updateRule(
    id: number,
    data: {
      user_id?: number;
      pattern?: string;
      match_type?: string;
      tool_type?: string;
      priority?: number;
      is_auto?: boolean;
      is_active?: boolean;
      description?: string;
    }
  ): Promise<MappingRule> {
    return apiClient.put<MappingRule>(`/mapping-rules/${id}`, data);
  },

  async deleteRule(id: number): Promise<void> {
    await apiClient.delete(`/mapping-rules/${id}`);
  },

  async generateDefaultRules(userId: number): Promise<MappingRule[]> {
    return apiClient.post<MappingRule[]>(`/mapping-rules/user/${userId}/generate-default`);
  },

  async getMappingStats(): Promise<MappingStats> {
    return apiClient.get<MappingStats>('/mapping-stats');
  },

  async runAutoMapping(dryRun: boolean = false): Promise<{
    mapped_count: number;
    unmapped_count: number;
    mappings: AutoMappingResult[];
    dry_run: boolean;
  }> {
    return apiClient.post<{
      mapped_count: number;
      unmapped_count: number;
      mappings: AutoMappingResult[];
      dry_run: boolean;
    }>('/mapping-rules/auto-map', { dry_run: dryRun });
  },

  async testMatch(toolAccount: string, toolType?: string): Promise<MatchTestResult> {
    return apiClient.post<MatchTestResult>('/mapping-rules/test-match', {
      tool_account: toolAccount,
      tool_type: toolType,
    });
  },

  async getUnmappedAccounts(): Promise<UnmappedAccount[]> {
    return apiClient.get<UnmappedAccount[]>('/unmapped-accounts');
  },

  async suggestMapping(senderName: string): Promise<MappingSuggestion> {
    return apiClient.get<MappingSuggestion>(
      `/unmapped-accounts/${encodeURIComponent(senderName)}/suggest-mapping`
    );
  },

  async manualMapAccount(
    senderName: string,
    userId: number,
    toolType?: string,
    description?: string
  ): Promise<ToolAccount> {
    return apiClient.post<ToolAccount>(`/unmapped-accounts/${encodeURIComponent(senderName)}/map`, {
      user_id: userId,
      tool_type: toolType,
      description,
    });
  },
};

// Import ToolAccount type from toolAccounts.ts
import type { ToolAccount } from './toolAccounts';
