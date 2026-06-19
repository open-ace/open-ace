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
  fallback_note?: string; // Note when path was changed to fallback
}

export interface DirectoryEntry {
  name: string;
  path: string;
  is_writable: boolean;
}

interface LocalDirectoryEntryApi {
  name: string;
  path: string;
  isWritable?: boolean;
  isReadable?: boolean;
}

interface LocalBrowseResultApi {
  currentPath?: string;
  path?: string;
  name?: string;
  directories?: LocalDirectoryEntryApi[] | DirectoryEntry[];
  parentPath?: string | null;
  parent?: string | null;
  homePath?: string;
  canCreate?: boolean;
  is_writable?: boolean;
}

interface LocalBrowseResponseApi extends LocalBrowseResultApi {
  error?: string;
  fallback?: LocalBrowseResultApi;
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

export interface RemoteOperationResult<T = unknown> {
  success: boolean;
  result?: T;
  error?: string;
}

function getPathName(path: string): string {
  const normalized = path.replace(/[\\/]+$/, '');
  if (!normalized) {
    return '/';
  }
  const parts = normalized.split(/[/\\]/).filter(Boolean);
  return parts[parts.length - 1] ?? normalized;
}

function normalizeDirectoryEntry(entry: LocalDirectoryEntryApi | DirectoryEntry): DirectoryEntry {
  return {
    name: entry.name,
    path: entry.path,
    is_writable:
      'is_writable' in entry
        ? Boolean(entry.is_writable)
        : Boolean((entry as LocalDirectoryEntryApi).isWritable),
  };
}

function normalizeBrowseResult(payload: LocalBrowseResponseApi): BrowseResult {
  const source = payload.fallback ?? payload;
  const path = source.path ?? source.currentPath ?? '';

  return {
    path,
    name: source.name ?? getPathName(path),
    directories: Array.isArray(source.directories)
      ? source.directories.map(normalizeDirectoryEntry)
      : [],
    parent: source.parent ?? source.parentPath ?? null,
    homePath: source.homePath ?? '',
    canCreate: source.canCreate ?? source.is_writable ?? false,
    is_writable: source.is_writable ?? source.canCreate ?? false,
    fallback_note: payload.error && payload.fallback ? payload.error : undefined,
  };
}

// ==================== API Methods ====================

export const fsApi = {
  // Local file system browse
  async browseDirectory(path?: string): Promise<BrowseResult> {
    const params: Record<string, string> = {};
    if (path) params.path = path;
    const response = await apiClient.get<LocalBrowseResponseApi>('/api/fs/browse', params);
    return normalizeBrowseResult(response);
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
  ): Promise<RemoteOperationResult<BrowseResult>> {
    const params: Record<string, string> = {};
    if (path) params.path = path;
    return apiClient.get(`/api/remote/machines/${machineId}/browse`, params);
  },

  // Create directory on remote machine (via HTTP proxy)
  createRemoteDirectory(
    machineId: string,
    path: string
  ): Promise<RemoteOperationResult<{ path: string; message?: string }>> {
    return apiClient.post(`/api/remote/machines/${machineId}/create-directory`, { path });
  },
};
