/**
 * Encryption Keys Hooks - 加密密钥管理相关 Hooks
 */

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { encryptionKeysApi } from '@/api/encryptionKeys';

/**
 * 获取所有加密密钥
 */
export function useEncryptionKeys() {
  return useQuery({
    queryKey: ['encryption-keys'],
    queryFn: () => encryptionKeysApi.getEncryptionKeys(),
    staleTime: 30000, // 30 seconds
  });
}

/**
 * 验证密钥格式
 */
export function useValidateKey() {
  return useMutation({
    mutationFn: (key?: string) => encryptionKeysApi.validateKey({ key }),
  });
}

/**
 * 执行密钥轮换
 */
export function useRotateKey() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (data: { confirmation: string; expected_version?: number }) =>
      encryptionKeysApi.rotateKey(data),
    onSuccess: () => {
      // 刷新密钥列表
      queryClient.invalidateQueries({ queryKey: ['encryption-keys'] });
    },
  });
}

/**
 * 生成环境变量配置
 */
export function useGenerateEnvConfig() {
  return useMutation({
    mutationFn: () => encryptionKeysApi.generateEnvConfig(),
  });
}

/**
 * 查询审计日志
 */
export function useEncryptionKeysAuditLog(params?: {
  limit?: number;
  offset?: number;
  action?: string;
}) {
  return useQuery({
    queryKey: ['encryption-keys', 'audit-log', params],
    queryFn: () => encryptionKeysApi.getAuditLog(params),
    staleTime: 60000, // 1 minute
  });
}

/**
 * 获取多副本同步状态
 */
export function useEncryptionKeysSyncStatus() {
  return useQuery({
    queryKey: ['encryption-keys', 'sync-status'],
    queryFn: () => encryptionKeysApi.getSyncStatus(),
    staleTime: 30000, // 30 seconds
  });
}

/**
 * re-encrypt 预检查
 */
export function useReEncryptPreCheck() {
  return useMutation({
    mutationFn: () => encryptionKeysApi.reEncryptPreCheck(),
  });
}

/**
 * 重新加密存量密文
 */
export function useReEncrypt() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (data: { confirmation: string; batch_size?: number }) =>
      encryptionKeysApi.reEncrypt(data),
    onSuccess: () => {
      // 刷新密钥列表
      queryClient.invalidateQueries({ queryKey: ['encryption-keys'] });
    },
  });
}
