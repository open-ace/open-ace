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
  token_status: string; // "active" | "revoked" | "legacy" | "none"
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
  cli_tools: string | null; // JSON array: ["claude-code", "qwen-code"]
  cli_settings: string | null; // JSON object: {"claude-code": {...}, "qwen-code": {...}}
  scope: string; // 'local', 'remote', or 'shared'
  priority: number;
  weight: number;
}

export interface StoreApiKeyRequest {
  provider: string;
  key_name: string;
  api_key: string;
  base_url?: string;
  tenant_id?: number;
  cli_tools?: string; // JSON array: ["claude-code", "qwen-code"]
  cli_settings?: string; // JSON object: {"claude-code": {...}, "qwen-code": {...}}
  scope?: string; // 'local', 'remote', or 'shared' (default 'remote')
  priority?: number;
  weight?: number;
}

export interface UpdateApiKeyRequest {
  keyId: number;
  key_name?: string;
  base_url?: string;
  cli_tools?: string;
  cli_settings?: string;
  is_active?: boolean;
  tenant_id?: number;
  scope?: string;
  priority?: number;
  weight?: number;
}

export interface RemoteSession {
  session_id: string;
  machine_id: string;
  status: string;
  project_path: string;
  model: string | null;
  total_tokens: number;
  message_count: number;
  request_count: number;
  output: RemoteSessionOutput[];
  created_at: string | null;
  paused_at: string | null;
}

export interface RemoteSessionOutput {
  data: string;
  stream: string;
  is_complete: boolean;
  timestamp: string;
}

export interface CreateRemoteSessionRequest {
  machine_id: string;
  project_path: string;
  cli_tool?: string;
  model?: string;
  title?: string;
  ha_pool_token?: string;
}

export interface SessionModelsResponse {
  success: boolean;
  models: string[];
  empty_reason?: string;
  ha_pool_token?: string;
}

// ==================== Run Timeline Types ====================

/** Persisted provenance record: one per remote session. */
export interface AgentRun {
  run_id: string;
  session_id: string;
  user_id: number | null;
  tenant_id: number | null;
  machine_id: string | null;
  tool_name: string | null;
  provider: string | null;
  cli_tool: string | null;
  model: string | null;
  status: string;
  started_at: string | null;
  ended_at: string | null;
  total_tokens: number;
  total_input_tokens: number;
  total_output_tokens: number;
  total_requests: number;
  metadata: Record<string, unknown>;
  created_at: string | null;
  updated_at: string | null;
}

/** One append-only entry in a run's timeline. */
export interface RunEvent {
  id: number | null;
  run_id: string;
  session_id: string;
  event_type: string;
  event_subtype: string | null;
  role: string | null;
  content: string | null;
  tool_name: string | null;
  provider: string | null;
  model: string | null;
  key_id: string | null;
  user_id: number | null;
  tenant_id: number | null;
  machine_id: string | null;
  metadata: Record<string, unknown>;
  event_ts: string | null;
  created_at: string | null;
}

/** A durable permission request + its response. */
export interface AgentApproval {
  id: number | null;
  request_id: string;
  run_id: string;
  session_id: string;
  tool_name: string | null;
  request_subtype: string | null;
  request_details: Record<string, unknown>;
  status: string;
  decision: string | null;
  decided_by: number | null;
  decided_by_name: string | null;
  decision_metadata: Record<string, unknown>;
  requested_at: string | null;
  decided_at: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface RunTimelineParams {
  limit?: number;
  offset?: number;
  /** Event id cursor for live streaming (only events after this id). */
  after?: number;
  event_type?: string;
  order?: 'asc' | 'desc';
}

export interface RunEventsResponse {
  success: boolean;
  /** Present (true) when the run_timeline feature flag is disabled. */
  disabled?: boolean;
  run: AgentRun | null;
  events: RunEvent[];
  total: number;
  limit: number;
  offset: number;
}

export interface RunApprovalsResponse {
  success: boolean;
  /** Present (true) when the run_timeline feature flag is disabled. */
  disabled?: boolean;
  approvals: AgentApproval[];
  total: number;
}

// ==================== API Methods ====================

export const remoteApi = {
  // Machine management
  listMachines(): Promise<{ success: boolean; machines: RemoteMachine[]; user_role?: string }> {
    return apiClient.get('/api/remote/machines');
  },

  getMachine(machineId: string): Promise<{ success: boolean; machine: RemoteMachine }> {
    return apiClient.get(`/api/remote/machines/${machineId}`);
  },

  generateRegistrationToken(
    tenantId?: number
  ): Promise<{ success: boolean; registration_token: string; message: string }> {
    return apiClient.post('/api/remote/machines/register', { tenant_id: tenantId ?? 1 });
  },

  deregisterMachine(machineId: string): Promise<{ success: boolean; message: string }> {
    return apiClient.delete(`/api/remote/machines/${machineId}`);
  },

  rotateMachineToken(
    machineId: string
  ): Promise<{ success: boolean; agent_token: string; message: string }> {
    return apiClient.post(`/api/remote/machines/${machineId}/token/rotate`);
  },

  revokeMachineToken(machineId: string): Promise<{ success: boolean; message: string }> {
    return apiClient.post(`/api/remote/machines/${machineId}/token/revoke`);
  },

  getMachineUsers(machineId: string): Promise<{ success: boolean; users: MachineAssignment[] }> {
    return apiClient.get(`/api/remote/machines/${machineId}/users`);
  },

  assignUser(
    machineId: string,
    userId: number,
    permission: string = 'user'
  ): Promise<{ success: boolean; message: string }> {
    return apiClient.post(`/api/remote/machines/${machineId}/assign`, {
      user_id: userId,
      permission,
    });
  },

  revokeUser(machineId: string, userId: number): Promise<{ success: boolean; message: string }> {
    return apiClient.delete(`/api/remote/machines/${machineId}/assign/${userId}`);
  },

  // API Key management
  listApiKeys(tenantId?: number): Promise<{ success: boolean; keys: ApiKey[] }> {
    const params: Record<string, string> = {};
    if (tenantId) params.tenant_id = String(tenantId);
    return apiClient.get('/api/api-keys', params);
  },

  storeApiKey(
    data: StoreApiKeyRequest
  ): Promise<{ success: boolean; key: { provider: string; key_name: string } }> {
    return apiClient.post('/api/api-keys', data);
  },

  deleteApiKey(keyId: number, tenantId?: number): Promise<{ success: boolean; message: string }> {
    return apiClient.delete(`/api/api-keys/${keyId}`, { tenant_id: tenantId ?? 1 });
  },

  updateApiKey(data: UpdateApiKeyRequest): Promise<{ success: boolean; message: string }> {
    const { keyId, ...body } = data;
    return apiClient.put(`/api/api-keys/${keyId}`, body);
  },

  // Available machines (for session creation)
  getAvailableMachines(): Promise<{ success: boolean; machines: RemoteMachine[] }> {
    return apiClient.get('/api/remote/machines/available');
  },

  // Session management
  getSessionModels(params: {
    workspace_type: 'local' | 'remote';
    machine_id?: string;
  }): Promise<SessionModelsResponse> {
    return apiClient.get('/api/workspace/session-models', params);
  },

  createSession(
    data: CreateRemoteSessionRequest
  ): Promise<{ success: boolean; session: RemoteSession }> {
    return apiClient.post('/api/remote/sessions', data);
  },

  getSession(sessionId: string): Promise<{ success: boolean; session: RemoteSession }> {
    return apiClient.get(`/api/remote/sessions/${sessionId}`);
  },

  sendMessage(sessionId: string, content: string): Promise<{ success: boolean }> {
    return apiClient.post(`/api/remote/sessions/${sessionId}/chat`, { content });
  },

  stopSession(sessionId: string): Promise<{ success: boolean }> {
    return apiClient.post(`/api/remote/sessions/${sessionId}/stop`, {});
  },

  pauseSession(sessionId: string): Promise<{ success: boolean }> {
    return apiClient.post(`/api/remote/sessions/${sessionId}/pause`, {});
  },

  resumeSession(sessionId: string): Promise<{ success: boolean }> {
    return apiClient.post(`/api/remote/sessions/${sessionId}/resume`, {});
  },

  // Terminal management
  startTerminal(data: { machine_id: string; work_dir?: string }): Promise<{
    success: boolean;
    terminal?: {
      terminal_id: string;
      ws_url: string;
      token: string;
      status: string;
    };
    error?: string;
  }> {
    return apiClient.post('/api/remote/terminal/start', data);
  },

  stopTerminal(data: { terminal_id: string; machine_id: string }): Promise<{ success: boolean }> {
    return apiClient.post('/api/remote/terminal/stop', data);
  },

  attachTerminal(data: { terminal_id: string; machine_id: string }): Promise<{
    success: boolean;
    terminal?: {
      terminal_id: string;
      machine_id: string;
      status: string;
    };
    error?: string;
  }> {
    return apiClient.post(`/api/remote/terminal/${data.terminal_id}/attach`, data);
  },

  getTerminalStatus(
    terminalId: string,
    machineId: string
  ): Promise<{
    success: boolean;
    terminal: {
      status: string;
      ws_url?: string;
      token?: string;
      error?: string;
    };
  }> {
    return apiClient.get(`/api/remote/terminal/${terminalId}/status`, {
      machine_id: machineId,
    });
  },

  // Run timeline (persisted provenance). Returns { disabled: true } when the
  // backend feature flag run_timeline.enabled is off, so the UI can hide itself.
  getRunEvents(sessionId: string, params?: RunTimelineParams): Promise<RunEventsResponse> {
    const query: Record<string, string> = {};
    // typeof checks include 0 (a valid limit/offset/after value) but exclude
    // undefined/null.
    if (typeof params?.limit === 'number') query.limit = String(params.limit);
    if (typeof params?.offset === 'number') query.offset = String(params.offset);
    if (typeof params?.after === 'number') query.after = String(params.after);
    if (params?.event_type) query.event_type = params.event_type;
    if (params?.order) query.order = params.order;
    return apiClient.get(`/api/remote/sessions/${sessionId}/events`, query);
  },

  getRunApprovals(sessionId: string): Promise<RunApprovalsResponse> {
    return apiClient.get(`/api/remote/sessions/${sessionId}/approvals`);
  },
};
