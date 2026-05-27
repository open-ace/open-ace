/**
 * File System API Client
 *
 * API methods for browsing directories and creating projects (local and remote).
 */

import { apiClient } from './client';

// ==================== Types ====================

export interface DirectoryInfo {
  path: string;
  name: string;
  is_writable: boolean;
  exists: boolean;
  is_directory: boolean;
}

export interface BrowseResult {
  path: string;
  name: string;
  directories: DirectoryEntry[];
  parent: string | null;
  homePath: string;
  canCreate: boolean;
  is_writable: boolean;
  fallback_note?: string;  // Note when path was changed to fallback
}

export interface DirectoryEntry {
  name: string;
  path: string;
  is_writable: boolean;
}

export interface CreateDirectoryRequest {
  path: string;
  system_account?: string;
}

export interface CreateDirectoryResult {
  success: boolean;
  path: string;
  message?: string;
  error?: string;
}

// ==================== API Methods ====================

export const fsApi = {
  // Local file system browse
  browseDirectory(path?: string): Promise<BrowseResult> {
    const params: Record<string, string> = {};
    if (path) params.path = path;
    return apiClient.get('/api/fs/browse', params);
  },

  // Create directory (local)
  createDirectory(data: CreateDirectoryRequest): Promise<CreateDirectoryResult> {
    return apiClient.post('/api/fs/create-directory', data);
  },

  // Get home directory
  getHomeDirectory(): Promise<{ homePath: string; is_writable: boolean }> {
    return apiClient.get('/api/fs/home');
  },

  // Remote directory browse (via HTTP proxy)
  browseRemoteDirectory(
    machineId: string,
    path?: string
  ): Promise<{ success: boolean; result?: BrowseResult; error?: string }> {
    const params: Record<string, string> = {};
    if (path) params.path = path;
    return apiClient.get(`/api/remote/machines/${machineId}/browse`, params);
  },
};
