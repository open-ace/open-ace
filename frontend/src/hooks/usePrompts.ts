/**
 * Prompts Hooks - Custom hooks for prompt template operations
 */

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { promptsApi } from '@/api';
import type { PromptFilters, CreatePromptRequest, UpdatePromptRequest } from '@/api';

export function usePrompts(filters?: PromptFilters) {
  return useQuery({
    queryKey: ['prompts', filters?.category, filters?.search, filters?.page, filters?.limit],
    queryFn: () => promptsApi.list(filters),
    staleTime: 30 * 1000,
  });
}

export function usePromptCategories() {
  return useQuery({
    queryKey: ['prompts', 'categories'],
    queryFn: () => promptsApi.getCategories(),
    staleTime: 5 * 60 * 1000,
  });
}

export function useCreatePrompt() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (data: CreatePromptRequest) => promptsApi.create(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['prompts'] });
    },
  });
}

export function useUpdatePrompt() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ id, data }: { id: number; data: UpdatePromptRequest }) =>
      promptsApi.update(id, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['prompts'] });
    },
  });
}

export function useDeletePrompt() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (id: number) => promptsApi.delete(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['prompts'] });
    },
  });
}

export function useCopyPrompt() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (id: number) => promptsApi.copy(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['prompts'] });
    },
  });
}
