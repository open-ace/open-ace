/**
 * Image Upload API
 *
 * API functions for image upload, retrieval, and management.
 */

import { apiClient } from './client';

export interface UploadedImage {
  id: number;
  user_id: number;
  tenant_id?: number;
  session_id?: string;
  project_id?: number;
  filename: string;
  stored_path: string;
  file_size: number;
  mime_type: string;
  width?: number;
  height?: number;
  expires_at: string;
  created_at: string;
  is_svg: boolean;
  preview_url: string;
}

export interface UploadResponse {
  success: boolean;
  image: UploadedImage;
}

export interface ImageListResponse {
  success: boolean;
  images: UploadedImage[];
  count: number;
}

export interface StorageQuota {
  quota_bytes: number;
  quota_mb: number;
  used_bytes: number;
  used_mb: number;
  remaining_bytes: number;
  remaining_mb: number;
  usage_percentage: number;
}

export interface QuotaResponse {
  success: boolean;
  quota: StorageQuota;
}

export interface StorageStatus {
  total_used_bytes: number;
  total_used_mb: number;
  total_files: number;
  disk_space_ok: boolean;
  disk_space_warning?: string;
  storage_path: string;
  threshold_pct: number;
  user_stats: Array<{
    user_id: number;
    username: string;
    storage_used_bytes: number;
    storage_quota_bytes: number;
    storage_used_mb: number;
    storage_quota_mb: number;
    usage_percentage: number;
    file_count: number;
  }>;
}

export interface StorageStatusResponse {
  success: boolean;
  status: StorageStatus;
}

/**
 * Get auth token from cookies or localStorage
 */
function getAuthToken(): string | null {
  // Try cookie first
  const cookies = document.cookie.split(';');
  for (const cookie of cookies) {
    const [name, value] = cookie.trim().split('=');
    if (name === 'session_token') {
      return value;
    }
  }

  // Try localStorage
  return localStorage.getItem('session_token');
}

/**
 * Upload an image file with optional progress tracking
 */
export async function uploadImage(
  file: File,
  sessionId?: string,
  projectId?: number,
  onProgress?: (progress: number) => void
): Promise<UploadResponse> {
  const formData = new FormData();
  formData.append('file', file);

  if (sessionId) {
    formData.append('session_id', sessionId);
  }
  if (projectId) {
    formData.append('project_id', projectId.toString());
  }

  // Use XMLHttpRequest for progress tracking
  if (onProgress) {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();

      xhr.upload.addEventListener('progress', (e) => {
        if (e.lengthComputable) {
          const progress = (e.loaded / e.total) * 100;
          onProgress(progress);
        }
      });

      xhr.addEventListener('load', () => {
        if (xhr.status >= 200 && xhr.status < 300) {
          try {
            const response = JSON.parse(xhr.responseText);
            resolve(response);
          } catch {
            reject(new Error('Failed to parse response'));
          }
        } else {
          try {
            const error = JSON.parse(xhr.responseText);
            reject(new Error(error.error ?? 'Upload failed'));
          } catch {
            reject(new Error('Upload failed'));
          }
        }
      });

      xhr.addEventListener('error', () => {
        reject(new Error('Upload failed'));
      });

      xhr.open('POST', '/api/images/upload');

      // Add auth token
      const token = getAuthToken();
      if (token) {
        xhr.setRequestHeader('Authorization', `Bearer ${token}`);
      }

      xhr.send(formData);
    });
  }

  // Without progress tracking, use fetch directly (FormData requires special handling)
  const token = getAuthToken();
  const headers: Record<string, string> = {};
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }
  // Don't set Content-Type for FormData - let browser handle it

  const response = await fetch('/api/images/upload', {
    method: 'POST',
    headers,
    body: formData,
    credentials: 'include',
  });

  if (!response.ok) {
    const errorData = await response.json();
    throw new Error(errorData.error ?? 'Upload failed');
  }

  return response.json();
}

/**
 * Get image information by ID
 */
export async function getImage(
  imageId: number
): Promise<{ success: boolean; image: UploadedImage }> {
  return apiClient.get(`/images/${imageId}`);
}

/**
 * Delete an image by ID
 */
export async function deleteImage(imageId: number): Promise<{ success: boolean }> {
  return apiClient.delete(`/images/${imageId}`);
}

/**
 * List images for current user
 */
export async function listImages(
  sessionId?: string,
  limit?: number,
  offset?: number
): Promise<ImageListResponse> {
  const params: Record<string, string> = {};
  if (sessionId) {
    params['session_id'] = sessionId;
  }
  if (limit !== undefined) {
    params['limit'] = limit.toString();
  }
  if (offset !== undefined) {
    params['offset'] = offset.toString();
  }

  return apiClient.get('/images/list', params);
}

/**
 * Get image serve URL
 */
export function getImageServeUrl(imageId: number): string {
  return `/api/images/serve/${imageId}`;
}

/**
 * Get user's storage quota
 */
export async function getUserQuota(): Promise<QuotaResponse> {
  return apiClient.get('/images/quota');
}

/**
 * Get storage status (admin only)
 */
export async function getStorageStatus(): Promise<StorageStatusResponse> {
  return apiClient.get('/images/storage-status');
}

/**
 * Validate file before upload
 */
export function validateFile(
  file: File,
  maxSizeMb: number = 10,
  allowedTypes: string[] = ['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp'],
  allowSvg: boolean = false
): { valid: boolean; error?: string } {
  // Check size
  const maxSizeBytes = maxSizeMb * 1024 * 1024;
  if (file.size > maxSizeBytes) {
    return {
      valid: false,
      error: `文件大小超过限制 (最大 ${maxSizeMb}MB)`,
    };
  }

  if (file.size === 0) {
    return {
      valid: false,
      error: '文件为空',
    };
  }

  // Check extension
  const extension = file.name.split('.').pop()?.toLowerCase() ?? '';
  if (extension === 'svg') {
    if (!allowSvg) {
      return {
        valid: false,
        error: 'SVG 文件不允许上传',
      };
    }
  } else if (!allowedTypes.includes(extension)) {
    return {
      valid: false,
      error: `不支持的文件格式 (支持: ${allowedTypes.join(', ')})`,
    };
  }

  // Check MIME type
  const allowedMimes: Record<string, string[]> = {
    png: ['image/png'],
    jpg: ['image/jpeg'],
    jpeg: ['image/jpeg'],
    gif: ['image/gif'],
    webp: ['image/webp'],
    bmp: ['image/bmp'],
    svg: ['image/svg+xml'],
  };

  const expectedMimes = allowedMimes[extension] || [];
  if (expectedMimes.length > 0 && !expectedMimes.includes(file.type)) {
    return {
      valid: false,
      error: `文件类型不匹配`,
    };
  }

  return { valid: true };
}
