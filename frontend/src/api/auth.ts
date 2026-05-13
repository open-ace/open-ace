/**
 * Auth API - Authentication related API calls
 */

import { apiClient } from './client';
import type { User } from '@/types';

export interface AuthCheckResponse {
  authenticated: boolean;
  user?: User;
}

export interface LoginRequest {
  username: string;
  password: string;
}

export interface LoginResponse {
  success: boolean;
  user?: User;
  message?: string;
}

export const authApi = {
  /**
   * Check authentication status
   */
  async checkAuth(): Promise<AuthCheckResponse> {
    const response = await apiClient.get<AuthCheckResponse>('/api/auth/check');
    return response;
  },

  /**
   * Login
   */
  async login(credentials: LoginRequest): Promise<LoginResponse> {
    const response = await apiClient.post<LoginResponse>('/api/auth/login', credentials);
    return response;
  },

  /**
   * Logout
   */
  async logout(): Promise<void> {
    await apiClient.post('/api/auth/logout');
  },

  /**
   * Get current user
   */
  async getCurrentUser(): Promise<User | null> {
    try {
      const response = await apiClient.get<{ user: User }>('/api/auth/me');
      return response.user;
    } catch {
      return null;
    }
  },

  /**
   * Upload avatar
   */
  async uploadAvatar(file: File): Promise<{ success: boolean; avatar_url?: string }> {
    const formData = new FormData();
    formData.append('file', file);

    const response = await fetch('/api/user/avatar', {
      method: 'POST',
      body: formData,
      credentials: 'include',
    });

    return response.json();
  },

  /**
   * Delete avatar
   */
  async deleteAvatar(): Promise<{ success: boolean }> {
    const response = await apiClient.delete<{ success: boolean }>('/api/user/avatar');
    return response;
  },
};
