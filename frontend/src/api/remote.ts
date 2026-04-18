/**
 * Remote Workspace API Client
 *
 * API methods for remote machine management and API key management.
 */

import { apiClient } from './client';

// ==================== Types ====================

export interface RemoteMachine {
  id: number;
  machine_id: string;
  machine_name: string;
  hostname: string | null;
  os_type: string | null;
  os_version: string | null;
  ip_address: string | null;
  status: string;
  agent_version: string | null;
  capabilities: Record<string, unknown> | null;
  cli_path: string | null;
  work_dir: string | null;
  tenant_id: number | null;
  created_by: number | null;
  created_at: string | null;
  updated_at: string | null;
  last_heartbeat: string | null;
  connected: boolean;
  current_user_permission?: string;
}

export interface MachineAssignment {
  user_id: number;
  username: string;
  permission: string;
  granted_at: string;
}

export interface ApiKey {
  id: number;
  provider: string;
  key_name: string;
  base_url: string | null;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface StoreApiKeyRequest {
  provider: string;
  key_name: string;
  api_key: string;
  base_url?: string;
  tenant_id?: number;
}

// ==================== API Methods ====================

export const remoteApi = {
  // Machine management
  listMachines(): Promise<{ success: boolean; machines: RemoteMachine[] }> {
    return apiClient.get('/api/remote/machines');
  },

  getMachine(machineId: string): Promise<{ success: boolean; machine: RemoteMachine }> {
    return apiClient.get(`/api/remote/machines/${machineId}`);
  },

  generateRegistrationToken(tenantId?: number): Promise<{ success: boolean; registration_token: string; message: string }> {
    return apiClient.post('/api/remote/machines/register', { tenant_id: tenantId || 1 });
  },

  deregisterMachine(machineId: string): Promise<{ success: boolean; message: string }> {
    return apiClient.delete(`/api/remote/machines/${machineId}`);
  },

  getMachineUsers(machineId: string): Promise<{ success: boolean; users: MachineAssignment[] }> {
    return apiClient.get(`/api/remote/machines/${machineId}/users`);
  },

  assignUser(machineId: string, userId: number, permission: string = 'user'): Promise<{ success: boolean; message: string }> {
    return apiClient.post(`/api/remote/machines/${machineId}/assign`, { user_id: userId, permission });
  },

  revokeUser(machineId: string, userId: number): Promise<{ success: boolean; message: string }> {
    return apiClient.delete(`/api/remote/machines/${machineId}/assign/${userId}`);
  },

  // API Key management
  listApiKeys(tenantId?: number): Promise<{ success: boolean; keys: ApiKey[] }> {
    const params: Record<string, string> = {};
    if (tenantId) params.tenant_id = String(tenantId);
    return apiClient.get('/api/remote/api-keys', params);
  },

  storeApiKey(data: StoreApiKeyRequest): Promise<{ success: boolean; key: { provider: string; key_name: string } }> {
    return apiClient.post('/api/remote/api-keys', data);
  },

  deleteApiKey(keyId: number, tenantId?: number): Promise<{ success: boolean; message: string }> {
    return apiClient.delete(`/api/remote/api-keys/${keyId}`, { tenant_id: tenantId || 1 });
  },
};
