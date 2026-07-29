/**
 * System API - System settings and configuration API calls
 */

import { apiClient } from './client';

// Types
export interface SystemSettings {
  sso_enabled?: boolean;
  [key: string]: unknown;
}

/**
 * Get SSO enabled status (public endpoint)
 */
export async function getSSOEnabled(): Promise<{ sso_enabled: boolean }> {
  const response = await apiClient.get<{
    success: boolean;
    data: { sso_enabled: boolean };
  }>('/api/system/settings/sso-enabled');

  return response.data;
}

/**
 * Get all system settings (requires authentication)
 */
export async function getSystemSettings(): Promise<SystemSettings> {
  const response = await apiClient.get<{
    success: boolean;
    data: SystemSettings;
  }>('/api/system/settings');

  return response.data;
}

/**
 * Update system settings (admin only)
 */
export async function updateSystemSettings(
  settings: Partial<SystemSettings>
): Promise<{ success: boolean; updated: string[] }> {
  const response = await apiClient.put<{
    success: boolean;
    message: string;
    updated: string[];
  }>('/api/system/settings', settings);

  return response;
}

// Export all
export const systemApi = {
  getSSOEnabled,
  getSystemSettings,
  updateSystemSettings,
};
