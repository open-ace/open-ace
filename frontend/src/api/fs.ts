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
  files: FileEntry[];
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

export interface FileEntry {
  name: string;
  path: string;
  size: number;
  is_readable: boolean;
}

interface LocalDirectoryEntryApi {
  name: string;
  path: string;
  isWritable?: boolean;
  isReadable?: boolean;
}

interface LocalFileEntryApi {
  name: string;
  path: string;
  size?: number;
  is_readable?: boolean;
}

interface LocalBrowseResultApi {
  currentPath?: string;
  path?: string;
  name?: string;
  directories?: LocalDirectoryEntryApi[] | DirectoryEntry[];
  files?: LocalFileEntryApi[];
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
    files: Array.isArray(source.files)
      ? source.files.map((f) => ({
          name: f.name,
          path: f.path,
          size: f.size ?? 0,
          is_readable: Boolean(f.is_readable),
        }))
      : [],
    parent: source.parent ?? source.parentPath ?? null,
    homePath: source.homePath ?? '',
    canCreate: source.canCreate ?? source.is_writable ?? false,
    is_writable: source.is_writable ?? source.canCreate ?? false,
    fallback_note: payload.error && payload.fallback ? payload.error : undefined,
  };
}

// ==================== API Methods ====================

/**
 * Per-file upload size cap (MB). Mirror of the backend MAX_UPLOAD_SIZE_MB env
 * var in app/routes/fs.py. Keep both in sync — the frontend value is used for
 * pre-flight UX (reject before the request), the backend value is the real
 * enforcement.
 */
export const MAX_UPLOAD_SIZE_MB = 100;

export interface UploadFileResult {
  success: boolean;
  path?: string;
  size?: number;
  error?: string;
}

export const fsApi = {
  // Local file system browse.
  // opts.includeFiles controls whether the backend also returns regular files
  // (?include_files=1). Default false keeps existing directory-only callers
  // (project selector, remote fallback) unchanged.
  async browseDirectory(path?: string, opts?: { includeFiles?: boolean }): Promise<BrowseResult> {
    const params: Record<string, string> = {};
    if (path) params.path = path;
    if (opts?.includeFiles) params.include_files = '1';
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

  /**
   * Upload a single file into a directory under the user's home subtree.
   *
   * Bypasses apiClient (which forces JSON Content-Type and JSON.parse on the
   * response) — mirrors authApi.uploadAvatar with a raw fetch + FormData so
   * the browser sets the correct multipart boundary.
   */
  async uploadFile(file: File, targetDir: string): Promise<UploadFileResult> {
    const formData = new FormData();
    formData.append('file', file);
    formData.append('path', targetDir);

    const response = await fetch('/api/fs/upload', {
      method: 'POST',
      body: formData,
      credentials: 'include',
    });

    if (!response.ok) {
      const data = await response.json().catch(() => ({ error: 'Upload failed' }));
      return { success: false, error: data.error ?? 'Upload failed' };
    }
    return response.json();
  },

  /**
   * Download a file as a Blob. Bypasses apiClient (which only parses JSON).
   * The caller is responsible for triggering the browser save (see the
   * downloadBlob helper used by LocalDirectoryBrowser).
   */
  async downloadFile(path: string): Promise<Blob> {
    const response = await fetch(`/api/fs/download?path=${encodeURIComponent(path)}`, {
      credentials: 'include',
    });
    if (!response.ok) {
      const data = await response.json().catch(() => ({ error: 'Download failed' }));
      throw new Error(data.error ?? 'Download failed');
    }
    return response.blob();
  },

  /**
   * Delete a single file (not a directory) under the user's home subtree.
   */
  deleteFile(path: string): Promise<{ success: boolean; error?: string }> {
    return apiClient.post('/api/fs/delete-file', { path });
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
