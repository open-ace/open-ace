/**
 * Project Categories API client
 *
 * Issue #1278: Project categorization for workspace grouping display
 */

import { apiClient } from './client';

export interface ProjectCategory {
  id: number;
  name: string;
  key_patterns: string[];
  sort_order: number;
  is_active: boolean;
  created_at: string | null;
  updated_at: string | null;
}

/**
 * List all project categories
 */
export async function listProjectCategories(): Promise<{
  success: boolean;
  categories: ProjectCategory[];
}> {
  return apiClient.get<{ success: boolean; categories: ProjectCategory[] }>(
    '/api/project-categories'
  );
}

/**
 * Create a new project category (admin only)
 */
export async function createProjectCategory(data: {
  name: string;
  key_patterns: string[];
  sort_order?: number;
}): Promise<{ success: boolean; category: ProjectCategory }> {
  return apiClient.post<{ success: boolean; category: ProjectCategory }>(
    '/api/project-categories',
    data
  );
}

/**
 * Update a project category (admin only)
 */
export async function updateProjectCategory(
  id: number,
  data: Partial<{
    name: string;
    key_patterns: string[];
    sort_order: number;
    is_active: boolean;
  }>
): Promise<{ success: boolean; category: ProjectCategory }> {
  return apiClient.put<{ success: boolean; category: ProjectCategory }>(
    `/api/project-categories/${id}`,
    data
  );
}

/**
 * Delete a project category (admin only)
 */
export async function deleteProjectCategory(id: number): Promise<{ success: boolean }> {
  return apiClient.delete<{ success: boolean }>(`/api/project-categories/${id}`);
}
