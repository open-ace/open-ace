/**
 * Project API client for Open-ACE management
 */

import { apiClient } from './client';

// Types
export interface Project {
  id: number;
  path: string;
  name: string | null;
  description: string | null;
  created_by: number | null;
  created_at: string;
  updated_at: string;
  is_active: boolean;
  is_shared: boolean;
}

export interface UserProject {
  id: number;
  user_id: number;
  project_id: number;
  first_access_at: string;
  last_access_at: string;
  total_sessions: number;
  total_tokens: number;
  total_requests: number;
  total_duration_seconds: number;
  username?: string;
}

export interface ProjectStats {
  project_id: number;
  project_path: string;
  project_name: string | null;
  total_users: number;
  total_sessions: number;
  total_tokens: number;
  total_requests: number;
  total_duration_seconds: number;
  total_duration_hours: number;
  first_access: string | null;
  last_access: string | null;
  user_stats: UserProject[];
}

export interface ProjectDailyStats {
  date: string;
  project_id: number;
  project_path: string;
  total_tokens: number;
  total_input_tokens: number;
  total_output_tokens: number;
  total_requests: number;
  active_users: number;
  total_duration_seconds: number;
  total_duration_hours: number;
}

/**
 * Get all projects statistics (admin only)
 */
export async function getAllProjectStats(): Promise<{
  success: boolean;
  stats: ProjectStats[];
}> {
  return apiClient.get<{ success: boolean; stats: ProjectStats[] }>('/api/projects/stats');
}

/**
 * Get project details
 */
export async function getProject(
  projectId: number
): Promise<{ success: boolean; project: Project; stats: ProjectStats | null }> {
  return apiClient.get<{ success: boolean; project: Project; stats: ProjectStats | null }>(
    `/api/projects/${projectId}`
  );
}

/**
 * Get project daily statistics
 */
export async function getProjectDailyStats(
  projectId: number,
  startDate?: string,
  endDate?: string
): Promise<{ success: boolean; stats: ProjectDailyStats[] }> {
  const params: Record<string, string> = {};
  if (startDate) params.start_date = startDate;
  if (endDate) params.end_date = endDate;

  return apiClient.get<{ success: boolean; stats: ProjectDailyStats[] }>(
    `/api/projects/${projectId}/daily`,
    params
  );
}

/**
 * Get project users
 */
export async function getProjectUsers(
  projectId: number
): Promise<{ success: boolean; users: UserProject[] }> {
  return apiClient.get<{ success: boolean; users: UserProject[] }>(
    `/api/projects/${projectId}/users`
  );
}

/**
 * Delete a project
 */
export async function deleteProject(projectId: number): Promise<{ success: boolean }> {
  return apiClient.delete<{ success: boolean }>(`/api/projects/${projectId}`);
}
