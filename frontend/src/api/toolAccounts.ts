/**
 * Tool Accounts API - Managing user tool account mappings
 */

import { apiClient } from './client';

// Types
export type MappingSource = 'manual' | 'auto' | 'predeclared' | 'import' | 'discovered' | 'legacy_predeclared';

export type MappingStatus = 'pending' | 'active' | 'stale' | 'conflict_type' | 'conflict_owner' | 'conflict_tenant';

export interface ToolAccount {
  id: number;
  user_id: number;
  tool_account: string;
  tool_type: string | null;
  tool_type_display?: string;
  description: string | null;
  created_at: string | null;
  updated_at: string | null;
  // Issue #2761: New fields
  mapping_source?: MappingSource | null;
  mapping_status?: MappingStatus | null;
  discovered_at?: string | null;
  last_activity_at?: string | null;
  observed_message_count?: number;
  created_by?: number | null;
  tenant_id?: number | null;
  version?: number;
}

export interface UnmappedAccount {
  sender_name: string;
  tool_type: string | null;
  tool_type_display: string;
  message_count: number;
  first_date: string;
  last_date: string;
}

export interface ToolType {
  value: string;
  display: string;
}

export interface UserToolAccounts {
  user: {
    id: number;
    username: string;
    email: string;
    linux_account?: string;
  };
  tool_accounts: ToolAccount[];
}

// API
export const toolAccountsApi = {
  async getAll(): Promise<Record<number, UserToolAccounts>> {
    return apiClient.get<Record<number, UserToolAccounts>>('/api/tool-accounts');
  },

  async getByUser(userId: number): Promise<ToolAccount[]> {
    return apiClient.get<ToolAccount[]>(`/api/tool-accounts/user/${userId}`);
  },

  async getUnmapped(): Promise<UnmappedAccount[]> {
    return apiClient.get<UnmappedAccount[]>('/api/tool-accounts/unmapped');
  },

  async getToolTypes(): Promise<ToolType[]> {
    return apiClient.get<ToolType[]>('/api/tool-types');
  },

  async create(data: {
    user_id: number;
    tool_account: string;
    tool_type?: string;
    description?: string;
    // Issue #2761: New fields for predeclared accounts
    mapping_source?: MappingSource;
    mapping_status?: MappingStatus;
  }): Promise<{ mapping: ToolAccount; updated_messages: number }> {
    return apiClient.post<{ mapping: ToolAccount; updated_messages: number }>('/api/tool-accounts', data);
  },

  async update(
    id: number,
    data: {
      user_id?: number;
      tool_account?: string;
      tool_type?: string;
      description?: string;
    }
  ): Promise<ToolAccount> {
    return apiClient.put<ToolAccount>(`/api/tool-accounts/${id}`, data);
  },

  async delete(id: number): Promise<void> {
    await apiClient.delete(`/api/tool-accounts/${id}`);
  },

  async batchCreate(
    userId: number,
    toolAccounts: Array<{
      tool_account: string;
      tool_type?: string;
      description?: string;
    }>
  ): Promise<{ created_count: number; mappings: ToolAccount[] }> {
    return apiClient.post<{ created_count: number; mappings: ToolAccount[] }>(
      `/api/tool-accounts/user/${userId}/batch`,
      { tool_accounts: toolAccounts }
    );
  },
};
