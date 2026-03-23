/**
 * SSO API - Single Sign-On management API calls
 */

import { apiClient } from './client';

// Types
export interface SSOProvider {
  name: string;
  type: 'oauth2' | 'oidc';
  is_enabled: boolean;
  is_predefined?: boolean;
  client_id?: string;
  redirect_uri?: string;
  scope?: string;
  authorization_url?: string;
  token_url?: string;
  userinfo_url?: string;
  issuer_url?: string;
}

export interface PredefinedProvider {
  name: string;
  type: 'oauth2' | 'oidc';
  display_name: string;
  icon?: string;
}

export interface RegisterProviderRequest {
  name: string;
  provider_type?: 'oauth2' | 'oidc';
  client_id: string;
  client_secret: string;
  redirect_uri?: string;
  scope?: string;
  predefined?: boolean;
  authorization_url?: string;
  token_url?: string;
  userinfo_url?: string;
  issuer_url?: string;
  tenant_id?: number;
  extra_params?: Record<string, unknown>;
}

export interface SSOIdentity {
  provider_name: string;
  provider_user_id: string;
  created_at: string;
  last_used_at?: string;
}

export interface SSOSession {
  user_id: number;
  provider_name: string;
  access_token: string;
  expires_at: string;
  created_at: string;
}

// API
export const ssoApi = {
  async getProviders(tenantId?: number): Promise<{
    registered: SSOProvider[];
    predefined: PredefinedProvider[];
  }> {
    const queryParams: Record<string, string> = tenantId ? { tenant_id: String(tenantId) } : {};
    return apiClient.get<{
      registered: SSOProvider[];
      predefined: PredefinedProvider[];
    }>('/api/sso/providers', queryParams);
  },

  async registerProvider(data: RegisterProviderRequest): Promise<void> {
    await apiClient.post<{ message: string }>('/api/sso/providers', data);
  },

  async disableProvider(providerName: string): Promise<void> {
    await apiClient.delete<{ message: string }>(`/api/sso/providers/${providerName}`);
  },

  async startLogin(
    providerName: string,
    redirectUri?: string
  ): Promise<{ authorization_url: string; state: string }> {
    const queryParams: Record<string, string> = { json: 'true' };
    if (redirectUri) queryParams.redirect_uri = redirectUri;

    return apiClient.get<{ authorization_url: string; state: string }>(
      `/api/sso/login/${providerName}`,
      queryParams
    );
  },

  async getSession(): Promise<SSOSession> {
    return apiClient.get<SSOSession>('/api/sso/session');
  },

  async logout(): Promise<void> {
    await apiClient.delete<{ message: string }>('/api/sso/session');
  },

  async getUserIdentities(userId: number): Promise<{ user_id: number; identities: SSOIdentity[] }> {
    return apiClient.get<{ user_id: number; identities: SSOIdentity[] }>(
      `/api/sso/identities/${userId}`
    );
  },

  async unlinkIdentity(userId: number, providerName: string): Promise<void> {
    await apiClient.delete<{ message: string }>(
      `/api/sso/identities/${userId}/${providerName}`
    );
  },
};