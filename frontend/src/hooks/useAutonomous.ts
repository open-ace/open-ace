/**
 * useAutonomous Hook - Data fetching hooks for AI autonomous development
 */

import { useCallback, useEffect, useRef } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { autonomousApi } from '@/api/autonomous';
import type { CreateWorkflowRequest } from '@/api/autonomous';

// ── Workflow Queries ───────────────────────────────────────────────

export function useWorkflows(filters?: { status?: string }) {
  return useQuery({
    queryKey: ['autonomous', 'workflows', filters],
    queryFn: () => autonomousApi.listWorkflows(filters as Record<string, string> | undefined),
    staleTime: 30 * 1000,
    refetchInterval: 30 * 1000,
  });
}

export function useWorkflow(workflowId: string, enabled = true) {
  const isActive = enabled && !!workflowId;
  return useQuery({
    queryKey: ['autonomous', 'workflow', workflowId],
    queryFn: () => autonomousApi.getWorkflow(workflowId),
    enabled: isActive,
    staleTime: 5 * 1000,
    refetchInterval: isActive ? 5 * 1000 : false,
  });
}

export function useWorkflowTimeline(workflowId: string, enabled = true) {
  return useQuery({
    queryKey: ['autonomous', 'timeline', workflowId],
    queryFn: () => autonomousApi.getTimeline(workflowId),
    enabled: enabled && !!workflowId,
    staleTime: 3 * 1000,
    refetchInterval: enabled && !!workflowId ? 5 * 1000 : false,
  });
}

// ── SSE Event Stream ───────────────────────────────────────────────

/**
 * Subscribe to SSE events for a workflow.
 * Uses debounced invalidation (500ms) to avoid flooding queries on rapid events.
 */
export function useWorkflowEvents(workflowId: string, enabled = true) {
  const queryClient = useQueryClient();
  const eventSourceRef = useRef<EventSource | null>(null);
  const debounceTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const debouncedInvalidate = useCallback(() => {
    if (debounceTimerRef.current) {
      clearTimeout(debounceTimerRef.current);
    }
    debounceTimerRef.current = setTimeout(() => {
      queryClient.invalidateQueries({ queryKey: ['autonomous', 'workflow', workflowId] });
      queryClient.invalidateQueries({ queryKey: ['autonomous', 'timeline', workflowId] });
      queryClient.invalidateQueries({ queryKey: ['autonomous', 'workflows'] });
    }, 500);
  }, [workflowId, queryClient]);

  useEffect(() => {
    if (!enabled || !workflowId) return;

    const url = autonomousApi.getEventStreamUrl(workflowId);
    const es = new EventSource(url);
    eventSourceRef.current = es;

    es.onmessage = (event) => {
      try {
        JSON.parse(event.data); // validate SSE data is JSON
        debouncedInvalidate();
      } catch {
        // Ignore parse errors (keepalive messages, etc.)
      }
    };

    es.onerror = () => {
      es.close();
      eventSourceRef.current = null;
    };

    return () => {
      es.close();
      eventSourceRef.current = null;
      if (debounceTimerRef.current) {
        clearTimeout(debounceTimerRef.current);
      }
    };
  }, [workflowId, enabled, debouncedInvalidate]);
}

// ── Workflow Mutations ──────────────────────────────────────────────

export function useCreateWorkflow() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (data: CreateWorkflowRequest) => autonomousApi.createWorkflow(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['autonomous', 'workflows'] });
    },
  });
}

export function usePauseWorkflow() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (workflowId: string) => autonomousApi.pauseWorkflow(workflowId),
    onSuccess: (_, workflowId) => {
      queryClient.invalidateQueries({ queryKey: ['autonomous', 'workflow', workflowId] });
      queryClient.invalidateQueries({ queryKey: ['autonomous', 'workflows'] });
    },
  });
}

export function useResumeWorkflow() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (workflowId: string) => autonomousApi.resumeWorkflow(workflowId),
    onSuccess: (_, workflowId) => {
      queryClient.invalidateQueries({ queryKey: ['autonomous', 'workflow', workflowId] });
      queryClient.invalidateQueries({ queryKey: ['autonomous', 'workflows'] });
    },
  });
}

export function useStopWorkflow() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (workflowId: string) => autonomousApi.stopWorkflow(workflowId),
    onSuccess: (_, workflowId) => {
      queryClient.invalidateQueries({ queryKey: ['autonomous', 'workflow', workflowId] });
      queryClient.invalidateQueries({ queryKey: ['autonomous', 'workflows'] });
    },
  });
}

export function useMarkDone() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ workflowId, selectedBranch }: { workflowId: string; selectedBranch?: string }) =>
      autonomousApi.markDone(workflowId, selectedBranch),
    onSuccess: (_, { workflowId }) => {
      queryClient.invalidateQueries({ queryKey: ['autonomous', 'workflow', workflowId] });
      queryClient.invalidateQueries({ queryKey: ['autonomous', 'workflows'] });
    },
  });
}

export function useRetryWorkflow() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (workflowId: string) => autonomousApi.retryWorkflow(workflowId),
    onSuccess: (_, workflowId) => {
      queryClient.invalidateQueries({ queryKey: ['autonomous', 'workflow', workflowId] });
      queryClient.invalidateQueries({ queryKey: ['autonomous', 'workflows'] });
    },
  });
}

// ── Milestone Mutations ─────────────────────────────────────────────

export function useCancelMilestone() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ workflowId, milestoneId }: { workflowId: string; milestoneId: string }) =>
      autonomousApi.cancelMilestone(workflowId, milestoneId),
    onSuccess: (_, { workflowId }) => {
      queryClient.invalidateQueries({ queryKey: ['autonomous', 'timeline', workflowId] });
      queryClient.invalidateQueries({ queryKey: ['autonomous', 'workflow', workflowId] });
    },
  });
}

export function useForkMilestone() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      workflowId,
      milestoneId,
      branchName,
    }: {
      workflowId: string;
      milestoneId: string;
      branchName?: string;
    }) => autonomousApi.forkMilestone(workflowId, milestoneId, branchName),
    onSuccess: (_, { workflowId }) => {
      queryClient.invalidateQueries({ queryKey: ['autonomous', 'timeline', workflowId] });
      queryClient.invalidateQueries({ queryKey: ['autonomous', 'workflow', workflowId] });
    },
  });
}

export function useMilestoneSession(workflowId: string, milestoneId: string, enabled = true) {
  return useQuery({
    queryKey: ['autonomous', 'session', workflowId, milestoneId],
    queryFn: () => autonomousApi.getMilestoneSession(workflowId, milestoneId),
    enabled: enabled && !!workflowId && !!milestoneId,
    staleTime: 60 * 1000,
  });
}

export function useDeleteWorkflow() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (workflowId: string) => autonomousApi.deleteWorkflow(workflowId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['autonomous', 'workflows'] });
    },
  });
}

// ── Auxiliary Queries ──────────────────────────────────────────────

export function useAvailableTools() {
  return useQuery({
    queryKey: ['autonomous', 'tools'],
    queryFn: () => autonomousApi.getAvailableTools(),
    staleTime: 5 * 60 * 1000,
  });
}

export function useAvailableModels(
  params?: { tool?: string; workspace_type?: string; machine_id?: string },
  enabled = true
) {
  return useQuery({
    queryKey: ['autonomous', 'models', params],
    queryFn: () => autonomousApi.getAvailableModels(params),
    enabled: enabled && !!params?.tool,
    staleTime: 60 * 1000,
  });
}
