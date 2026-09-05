/**
 * Encryption Keys API - 加密密钥管理 API
 */

import { apiClient } from './client';

// Types
export interface EncryptionKey {
  key_id: number;
  fingerprint: string;
  status: 'active' | 'deprecated' | 'revoked';
  created_at: string;
  rotated_at: string | null;
  config_version: number;
  last_used_at: string | null;
}

export interface EncryptionKeysResponse {
  success: boolean;
  keys: EncryptionKey[];
  config_version: number;
  primary_key_id: number;
  rotation_in_progress: boolean;
  consistency_status: 'consistent' | 'inconsistent';
}

export interface ValidateKeyRequest {
  key?: string;
}

export interface ValidateKeyResponse {
  success: boolean;
  valid: boolean;
  fingerprint: string | null;
  error: string | null;
  generated_key?: string;
}

export interface RotateKeyRequest {
  confirmation: string;
  expected_version?: number;
}

export interface RotateKeyResponse {
  success: boolean;
  new_key_id?: number;
  previous_key_id?: number;
  previous_key_status?: string;
  rotated_at?: string;
  new_config_version?: number;
  error?: string;
  message?: string;
  current_version?: number;
}

export interface GenerateEnvConfigResponse {
  success: boolean;
  env_var_name: string;
  env_var_value: string;
  instructions: string;
  config_file_example: string;
}

export interface AuditLogEntry {
  id: number;
  action: string;
  operator: string;
  ip_address: string;
  timestamp: string;
  details: Record<string, unknown>;
}

export interface AuditLogResponse {
  success: boolean;
  logs: AuditLogEntry[];
  total: number;
}

export interface SyncStatusResponse {
  success: boolean;
  local_version: number;
  remote_versions: Record<string, number | null>;
  sync_status: 'synchronized' | 'diverged' | 'unknown';
}

export interface CiphertextStats {
  total: number;
  with_key_id_prefix: number;
  legacy_format: number;
}

export interface ReEncryptPreCheckResponse {
  success: boolean;
  ciphertext_stats: CiphertextStats;
  decryption_test: {
    all_decryptable: boolean;
    failed_count: number;
    failed_items: Array<{
      type: string;
      name?: string;
      id?: number;
      error: string;
    }>;
  };
  recommendations: string[];
}

export interface ReEncryptRequest {
  confirmation: string;
  batch_size?: number;
}

export interface ReEncryptResponse {
  success: boolean;
  re_encrypted: {
    sso_providers: number;
    api_keys: number;
    smtp_passwords: number;
  };
  failed: Array<{
    type: string;
    name?: string;
    id?: number;
    error: string;
  }>;
  retry_endpoint: string;
}

// API functions
export const encryptionKeysApi = {
  /**
   * 获取所有加密密钥元数据
   */
  getEncryptionKeys(): Promise<EncryptionKeysResponse> {
    return apiClient.get('/api/encryption-keys');
  },

  /**
   * 验证密钥格式
   */
  validateKey(data: ValidateKeyRequest): Promise<ValidateKeyResponse> {
    return apiClient.post('/api/encryption-keys/validate', data);
  },

  /**
   * 执行密钥轮换
   */
  rotateKey(data: RotateKeyRequest): Promise<RotateKeyResponse> {
    return apiClient.post('/api/encryption-keys/rotate', data);
  },

  /**
   * 生成环境变量配置
   */
  generateEnvConfig(): Promise<GenerateEnvConfigResponse> {
    return apiClient.post('/api/encryption-keys/generate-env-config');
  },

  /**
   * 查询审计日志
   */
  getAuditLog(params?: {
    limit?: number;
    offset?: number;
    action?: string;
  }): Promise<AuditLogResponse> {
    return apiClient.get('/api/encryption-keys/audit-log', params);
  },

  /**
   * 获取多副本同步状态
   */
  getSyncStatus(): Promise<SyncStatusResponse> {
    return apiClient.get('/api/encryption-keys/sync-status');
  },

  /**
   * re-encrypt 预检查
   */
  reEncryptPreCheck(): Promise<ReEncryptPreCheckResponse> {
    return apiClient.post('/api/encryption-keys/re-encrypt/pre-check');
  },

  /**
   * 重新加密存量密文
   */
  reEncrypt(data: ReEncryptRequest): Promise<ReEncryptResponse> {
    return apiClient.post('/api/encryption-keys/re-encrypt', data);
  },
};