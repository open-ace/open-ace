/**
 * Autonomous Development API - API client for AI autonomous development workflows
 */

import { apiClient } from './client';

// ── Types ──────────────────────────────────────────────────────────

export interface AutonomousWorkflow {
  id?: number;
  workflow_id: string;
  user_id?: number;
  title: string;
  status: string;
  requirements_text: string;
  requirements_issue_url: string;
  project_path: string;
  project_repo_url: string;
  is_new_project: boolean;
  cli_tool: string;
  model: string;
  permission_mode: string;
  branch_name: string;
  branch_strategy: string;
  workspace_type: string;
  remote_machine_id: string;
  worktree_path: string;
  github_issue_number: number | null;
  github_pr_number: number | null;
  github_pr_url: string;
  current_phase: string;
  current_round: number;
  dev_round: number;
  max_plan_rounds: number;
  max_pr_review_rounds: number;
  total_tokens: number;
  total_input_tokens: number;
  total_output_tokens: number;
  total_requests: number;
  error_message: string;
  parent_workflow_id: string | null;
  fork_milestone_id: string | null;
  user_feedback: string;
  original_branch_name: string;
  created_at: string | null;
  updated_at: string | null;
  completed_at: string | null;
  paused_at: string | null;
}

export interface WorkflowMilestone {
  id?: number;
  workflow_id: string;
  milestone_id: string;
  phase: string;
  dev_round: number;
  round_number: number;
  milestone_type: string;
  status: string;
  title: string;
  description: string;
  session_id: string;
  review_session_id: string;
  github_issue_number: number | null;
  github_pr_number: number | null;
  github_comment_id: string;
  commit_shas: string;
  diff_stats: string;
  result_summary: string;
  plan_content: string;
  review_content: string;
  error_message: string;
  parent_milestone_id: string;
  fork_branch: string;
  fork_workflow_id: string;
  metadata: string;
  started_at: string | null;
  completed_at: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface WorkflowEvent {
  id?: number;
  workflow_id: string;
  milestone_id: string;
  event_type: string;
  event_data: string;
  created_at: string | null;
}

export interface AgentTool {
  id: string;
  name: string;
  executable: string;
}

export interface CreateWorkflowRequest {
  title?: string;
  requirements_text?: string;
  requirements_issue_url?: string;
  project_path?: string;
  project_repo_url?: string;
  is_new_project?: boolean;
  is_private?: boolean;
  cli_tool: string;
  model?: string;
  permission_mode?: string;
  branch_name?: string;
  branch_strategy?: string;
  workspace_type?: string;
  remote_machine_id?: string;
  max_plan_rounds?: number;
  max_pr_review_rounds?: number;
}

// ── API Client ─────────────────────────────────────────────────────

export const autonomousApi = {
  // Workflow CRUD
  async createWorkflow(
    data: CreateWorkflowRequest
  ): Promise<{ success: boolean; workflow: AutonomousWorkflow }> {
    return apiClient.post('/api/autonomous/workflows', data);
  },

  async listWorkflows(
    params?: Record<string, string>
  ): Promise<{ success: boolean; workflows: AutonomousWorkflow[] }> {
    return apiClient.get('/api/autonomous/workflows', params);
  },

  async getWorkflow(
    workflowId: string
  ): Promise<{ success: boolean; workflow: AutonomousWorkflow }> {
    return apiClient.get(`/api/autonomous/workflows/${workflowId}`);
  },

  async deleteWorkflow(workflowId: string): Promise<{ success: boolean }> {
    return apiClient.delete(`/api/autonomous/workflows/${workflowId}`);
  },

  // Workflow Control
  async pauseWorkflow(workflowId: string): Promise<{ success: boolean }> {
    return apiClient.post(`/api/autonomous/workflows/${workflowId}/pause`);
  },

  async resumeWorkflow(workflowId: string): Promise<{ success: boolean }> {
    return apiClient.post(`/api/autonomous/workflows/${workflowId}/resume`);
  },

  async stopWorkflow(workflowId: string): Promise<{ success: boolean }> {
    return apiClient.post(`/api/autonomous/workflows/${workflowId}/stop`);
  },

  async markDone(workflowId: string, selectedBranch?: string): Promise<{ success: boolean }> {
    return apiClient.post(`/api/autonomous/workflows/${workflowId}/done`, {
      selected_branch: selectedBranch,
    });
  },

  async retryWorkflow(workflowId: string): Promise<{ success: boolean }> {
    return apiClient.post(`/api/autonomous/workflows/${workflowId}/retry`);
  },

  async extendPlanningTimeout(
    workflowId: string,
    additionalSeconds: number = 600
  ): Promise<{ success: boolean; new_planning_timeout: number }> {
    return apiClient.post(`/api/autonomous/workflows/${workflowId}/extend-planning-timeout`, {
      additional_seconds: additionalSeconds,
    });
  },

  // Milestone Operations
  async getTimeline(
    workflowId: string
  ): Promise<{ success: boolean; milestones: WorkflowMilestone[] }> {
    return apiClient.get(`/api/autonomous/workflows/${workflowId}/timeline`);
  },

  async cancelMilestone(
    workflowId: string,
    milestoneId: string,
    feedback: string
  ): Promise<{ success: boolean; cancelled: number }> {
    return apiClient.post(
      `/api/autonomous/workflows/${workflowId}/milestones/${milestoneId}/cancel`,
      { user_feedback: feedback }
    );
  },

  async forkMilestone(
    workflowId: string,
    milestoneId: string,
    options: { feedback: string; pauseOriginal: boolean; branchName?: string }
  ): Promise<{ success: boolean; fork_workflow: AutonomousWorkflow }> {
    return apiClient.post(
      `/api/autonomous/workflows/${workflowId}/milestones/${milestoneId}/fork`,
      {
        user_feedback: options.feedback,
        pause_original: options.pauseOriginal,
        branch_name: options.branchName,
      }
    );
  },

  async getWorkflowForks(
    workflowId: string
  ): Promise<{ success: boolean; forks: AutonomousWorkflow[] }> {
    return apiClient.get(`/api/autonomous/workflows/${workflowId}/forks`);
  },

  async resumeWithFeedback(
    workflowId: string,
    feedback: string
  ): Promise<{ success: boolean }> {
    return apiClient.post(
      `/api/autonomous/workflows/${workflowId}/resume-with-feedback`,
      { user_feedback: feedback }
    );
  },

  async getMilestoneSession(
    workflowId: string,
    milestoneId: string
  ): Promise<{ success: boolean; session: Record<string, unknown> }> {
    return apiClient.get(
      `/api/autonomous/workflows/${workflowId}/milestones/${milestoneId}/session`
    );
  },

  async getMilestoneDiff(
    workflowId: string,
    milestoneId: string
  ): Promise<{ success: boolean; diff: string }> {
    return apiClient.get(`/api/autonomous/workflows/${workflowId}/milestones/${milestoneId}/diff`);
  },

  // SSE Event Stream URL
  getEventStreamUrl(workflowId: string): string {
    return `/api/autonomous/workflows/${workflowId}/events/stream`;
  },

  // Auxiliary
  async getAvailableTools(): Promise<{ success: boolean; tools: AgentTool[] }> {
    return apiClient.get('/api/autonomous/tools');
  },

  async getAvailableModels(params?: {
    tool?: string;
    workspace_type?: string;
    machine_id?: string;
  }): Promise<{ success: boolean; models: { name: string }[] }> {
    return apiClient.get('/api/autonomous/models', params);
  },
};
