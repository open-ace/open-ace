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

interface ListBusinessProjectsResponse {
  success: boolean;
  projects: BusinessProject[];
}

interface GetBusinessProjectResponse {
  success: boolean;
  project: BusinessProject;
}

interface CreateBusinessProjectResponse {
  success: boolean;
  project: BusinessProject;
}

interface UpdateBusinessProjectResponse {
  success: boolean;
  project: BusinessProject;
}

interface DeleteBusinessProjectResponse {
  success: boolean;
}

interface GetBusinessProjectMembersResponse {
  success: boolean;
  members: BusinessProjectMember[];
}

interface AddBusinessProjectMemberResponse {
  success: boolean;
  member: BusinessProjectMember;
}

interface RemoveBusinessProjectMemberResponse {
  success: boolean;
}

interface GetBusinessProjectStatsResponse {
  success: boolean;
  stats: BusinessProjectStats;
}

/**
 * List all business projects
 */
export async function listBusinessProjects(
  includeDeleted = false,
  activeOnly = true
): Promise<ListBusinessProjectsResponse> {
  const params = new URLSearchParams({
    include_deleted: includeDeleted.toString(),
    active_only: activeOnly.toString(),
  });
  return apiClient.get<ListBusinessProjectsResponse>(`/api/business-projects?${params}`);
}

/**
 * Get a single business project
 */
export async function getBusinessProject(projectId: number): Promise<GetBusinessProjectResponse> {
  return apiClient.get<GetBusinessProjectResponse>(`/api/business-projects/${projectId}`);
}

/**
 * Create a new business project
 */
export async function createBusinessProject(
  data: CreateBusinessProjectRequest
): Promise<CreateBusinessProjectResponse> {
  return apiClient.post<CreateBusinessProjectResponse>('/api/business-projects', data);
}

/**
 * Update a business project
 */
export async function updateBusinessProject(
  projectId: number,
  data: UpdateBusinessProjectRequest
): Promise<UpdateBusinessProjectResponse> {
  return apiClient.put<UpdateBusinessProjectResponse>(`/api/business-projects/${projectId}`, data);
}

/**
 * Delete a business project (soft delete)
 */
export async function deleteBusinessProject(
  projectId: number
): Promise<DeleteBusinessProjectResponse> {
  return apiClient.delete<DeleteBusinessProjectResponse>(`/api/business-projects/${projectId}`);
}

/**
 * Get members of a business project
 */
export async function getBusinessProjectMembers(
  projectId: number
): Promise<GetBusinessProjectMembersResponse> {
  return apiClient.get<GetBusinessProjectMembersResponse>(
    `/api/business-projects/${projectId}/members`
  );
}

/**
 * Add a member to a business project
 */
export async function addBusinessProjectMember(
  projectId: number,
  userId: number
): Promise<AddBusinessProjectMemberResponse> {
  return apiClient.post<AddBusinessProjectMemberResponse>(
    `/api/business-projects/${projectId}/members`,
    { user_id: userId }
  );
}

/**
 * Remove a member from a business project
 */
export async function removeBusinessProjectMember(
  projectId: number,
  memberId: number
): Promise<RemoveBusinessProjectMemberResponse> {
  return apiClient.delete<RemoveBusinessProjectMemberResponse>(
    `/api/business-projects/${projectId}/members/${memberId}`
  );
}

/**
 * Get statistics for a business project
 */
export async function getBusinessProjectStats(
  projectId: number
): Promise<GetBusinessProjectStatsResponse> {
  return apiClient.get<GetBusinessProjectStatsResponse>(
    `/api/business-projects/${projectId}/stats`
  );
}
