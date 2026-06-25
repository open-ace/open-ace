/**
 * Business Projects API Client
 *
 * Issue #871: Predefined business projects for workspace categorization
 */

import { apiClient } from './client';

export interface BusinessProject {
  id: number;
  name: string;
  code: string;
  description?: string;
  key_patterns: string[];
  is_active: boolean;
  created_by?: number;
  created_by_username?: string;
  created_at?: string;
  updated_at?: string;
  deleted_at?: string;
}

export interface BusinessProjectMember {
  id: number;
  business_project_id: number;
  user_id: number;
  username: string;
  added_at: string;
}

export interface BusinessProjectStats {
  business_project_id: number;
  project_name: string;
  project_code: string;
  total_workspaces: number;
  total_tokens: number;
  total_requests: number;
  total_duration_seconds: number;
  first_access?: string;
  last_access?: string;
}

export interface CreateBusinessProjectRequest {
  name: string;
  code: string;
  description?: string;
  key_patterns?: string[];
}

export interface UpdateBusinessProjectRequest {
  name?: string;
  code?: string;
  description?: string;
  key_patterns?: string[];
  is_active?: boolean;
}

/**
 * List all business projects
 */
export async function listBusinessProjects(
  includeDeleted = false,
  activeOnly = true
): Promise<{ success: boolean; projects: BusinessProject[] }> {
  const params = new URLSearchParams({
    include_deleted: includeDeleted.toString(),
    active_only: activeOnly.toString(),
  });
  const response = await apiClient.get(`/api/business-projects?${params}`);
  return response.data;
}

/**
 * Get a single business project
 */
export async function getBusinessProject(
  projectId: number
): Promise<{ success: boolean; project: BusinessProject }> {
  const response = await apiClient.get(`/api/business-projects/${projectId}`);
  return response.data;
}

/**
 * Create a new business project
 */
export async function createBusinessProject(
  data: CreateBusinessProjectRequest
): Promise<{ success: boolean; project: BusinessProject }> {
  const response = await apiClient.post('/api/business-projects', data);
  return response.data;
}

/**
 * Update a business project
 */
export async function updateBusinessProject(
  projectId: number,
  data: UpdateBusinessProjectRequest
): Promise<{ success: boolean; project: BusinessProject }> {
  const response = await apiClient.put(`/api/business-projects/${projectId}`, data);
  return response.data;
}

/**
 * Delete a business project (soft delete)
 */
export async function deleteBusinessProject(projectId: number): Promise<{ success: boolean }> {
  const response = await apiClient.delete(`/api/business-projects/${projectId}`);
  return response.data;
}

/**
 * Get members of a business project
 */
export async function getBusinessProjectMembers(
  projectId: number
): Promise<{ success: boolean; members: BusinessProjectMember[] }> {
  const response = await apiClient.get(`/api/business-projects/${projectId}/members`);
  return response.data;
}

/**
 * Add a member to a business project
 */
export async function addBusinessProjectMember(
  projectId: number,
  userId: number
): Promise<{ success: boolean; member: BusinessProjectMember }> {
  const response = await apiClient.post(`/api/business-projects/${projectId}/members`, {
    user_id: userId,
  });
  return response.data;
}

/**
 * Remove a member from a business project
 */
export async function removeBusinessProjectMember(
  projectId: number,
  memberId: number
): Promise<{ success: boolean }> {
  const response = await apiClient.delete(
    `/api/business-projects/${projectId}/members/${memberId}`
  );
  return response.data;
}

/**
 * Get statistics for a business project
 */
export async function getBusinessProjectStats(
  projectId: number
): Promise<{ success: boolean; stats: BusinessProjectStats }> {
  const response = await apiClient.get(`/api/business-projects/${projectId}/stats`);
  return response.data;
}
