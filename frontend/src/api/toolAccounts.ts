/**
 * Tool Accounts API - Managing user tool account mappings
 */

import { apiClient } from './client';

// Types
export interface ToolAccount {
  id: number;
  user_id: number;
  tool_account: string;
  tool_type: string | null;
  tool_type_display?: string;
  description: string | null;
  created_at: string | null;
  updated_at: string | null;
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
    return apiClient.get<Record<number, UserToolAccounts>>('/tool-accounts');
  },

  async getByUser(userId: number): Promise<ToolAccount[]> {
    return apiClient.get<ToolAccount[]>(`/tool-accounts/user/${userId}`);
  },

  async getUnmapped(): Promise<UnmappedAccount[]> {
    return apiClient.get<UnmappedAccount[]>('/tool-accounts/unmapped');
  },

  async getToolTypes(): Promise<ToolType[]> {
    return apiClient.get<ToolType[]>('/tool-types');
  },

  async create(data: {
    user_id: number;
    tool_account: string;
    tool_type?: string;
    description?: string;
  }): Promise<{ mapping: ToolAccount; updated_messages: number }> {
    return apiClient.post<{ mapping: ToolAccount; updated_messages: number }>(
      '/tool-accounts',
      data
    );
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
    return apiClient.put<ToolAccount>(`/tool-accounts/${id}`, data);
  },

  async delete(id: number): Promise<void> {
    await apiClient.delete(`/tool-accounts/${id}`);
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
      `/tool-accounts/user/${userId}/batch`,
      { tool_accounts: toolAccounts }
    );
  },
};
