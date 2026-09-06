/**
 * System API - System settings and configuration API calls
 */

import { apiClient } from './client';

// Types
export interface SystemSettings {
  sso_enabled?: boolean;
  // Issue #3271: Branding settings
  brand_logo_url?: string | null;
  brand_system_name?: string | null;
  brand_welcome_message?: Record<string, string> | null;
  brand_copyright_text?: string | null;
  [key: string]: unknown;
}

// Issue #3271: Branding configuration returned by public API
export interface BrandingConfig {
  logo_url: string | null;
  system_name: string | null;
  welcome_message: Record<string, string>;
  copyright_text: string | null;
  is_custom: boolean;
}

/**
 * Get SSO enabled status (public endpoint)
 */
export async function getSSOEnabled(): Promise<{ sso_enabled: boolean }> {
  const response = await apiClient.get<{
    success: boolean;
    data: { sso_enabled: boolean };
  }>('/api/settings/sso-enabled');

  return response.data;
}

/**
 * Get all system settings (requires authentication)
 */
export async function getSystemSettings(): Promise<SystemSettings> {
  const response = await apiClient.get<{
    success: boolean;
    data: SystemSettings;
  }>('/api/settings');

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
  }>('/api/settings', settings);

  return response;
}

/**
 * Get public branding configuration (public endpoint)
 * Issue #3271: Used by login page to display custom branding
 */
export async function getPublicBranding(tenantSlug?: string): Promise<BrandingConfig> {
  const queryParams: Record<string, string> = tenantSlug ? { tenant_slug: tenantSlug } : {};
  const response = await apiClient.get<{
    success: boolean;
    data: BrandingConfig;
  }>('/api/public/branding', queryParams);

  return response.data;
}

// Export all
export const systemApi = {
  getSSOEnabled,
  getSystemSettings,
  updateSystemSettings,
  getPublicBranding,
};
