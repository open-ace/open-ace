/**
 * Users API client for Open-ACE management
 *
 * Issue #3275: Project user management
 */

import { apiClient } from './client';

// Types
export interface User {
  id: number;
  username: string | null;
  email: string | null;
  role: string;
  is_active: boolean;
  tenant_id: number | null;
  system_account: string | null;
  created_at: string;
  updated_at: string;
}

/**
 * Get all users (admin only)
 * Issue #3275: Used for project user management
 */
export async function getUsers(): Promise<{ users: User[] }> {
  return apiClient.get<{ users: User[] }>('/api/admin/users');
}

/**
 * Get users by tenant
 */
export async function getUsersByTenant(tenantId: number): Promise<{ users: User[] }> {
  return apiClient.get<{ users: User[] }>(`/api/admin/users?tenant_id=${tenantId}`);
}
