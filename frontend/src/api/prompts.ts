/**
 * Prompts API - Prompt template management API calls
 */

import { apiClient } from './client';

// Types
export interface PromptVariable {
  name: string;
  description?: string;
  required?: boolean;
  default?: string;
}

export interface PromptTemplate {
  id: number;
  name: string;
  description: string;
  category: string;
  content: string;
  variables: PromptVariable[];
  tags: string[];
  author_id: number | null;
  author_name: string;
  is_public: boolean;
  is_featured: boolean;
  use_count: number;
  created_at: string | null;
  updated_at: string | null;
}

export interface PromptListResponse {
  templates: PromptTemplate[];
  total: number;
  page: number;
  limit: number;
  total_pages: number;
}

export interface PromptFilters {
  category?: string;
  search?: string;
  tags?: string[];
  page?: number;
  limit?: number;
}

export interface CreatePromptRequest {
  name: string;
  description?: string;
  category?: string;
  content: string;
  variables?: PromptVariable[];
  tags?: string[];
  is_public?: boolean;
}

export interface UpdatePromptRequest {
  name?: string;
  description?: string;
  category?: string;
  content?: string;
  variables?: PromptVariable[];
  tags?: string[];
  is_public?: boolean;
}

export interface RenderPromptRequest {
  variables: Record<string, string>;
}

export interface RenderPromptResponse {
  rendered: string;
}

export interface CategoryInfo {
  category: string;
  count: number;
}

// API
export const promptsApi = {
  /**
   * List prompt templates
   */
  async list(filters?: PromptFilters): Promise<PromptListResponse> {
    const params = new URLSearchParams();
    if (filters?.category) params.append('category', filters.category);
    if (filters?.search) params.append('search', filters.search);
    if (filters?.tags?.length) {
      filters.tags.forEach(tag => params.append('tags', tag));
    }
    if (filters?.page) params.append('page', filters.page.toString());
    if (filters?.limit) params.append('limit', filters.limit.toString());

    const queryString = params.toString();
    const url = queryString ? `/api/workspace/prompts?${queryString}` : '/api/workspace/prompts';
    
    const response = await apiClient.get<{ success: boolean; data: PromptListResponse }>(url);
    return response.data;
  },

  /**
   * Get a single prompt template
   */
  async get(id: number): Promise<PromptTemplate> {
    const response = await apiClient.get<{ success: boolean; data: PromptTemplate }>(`/api/workspace/prompts/${id}`);
    return response.data;
  },

  /**
   * Create a new prompt template
   */
  async create(data: CreatePromptRequest): Promise<number> {
    const response = await apiClient.post<{ success: boolean; data: { id: number } }>(
      '/api/workspace/prompts',
      data
    );
    return response.data.id;
  },

  /**
   * Update a prompt template
   */
  async update(id: number, data: UpdatePromptRequest): Promise<boolean> {
    const response = await apiClient.put<{ success: boolean }>(
      `/api/workspace/prompts/${id}`,
      data
    );
    return response.success;
  },

  /**
   * Delete a prompt template
   */
  async delete(id: number): Promise<boolean> {
    const response = await apiClient.delete<{ success: boolean }>(`/api/workspace/prompts/${id}`);
    return response.success;
  },

  /**
   * Render a prompt template with variables
   */
  async render(id: number, variables: Record<string, string>): Promise<string> {
    const response = await apiClient.post<{ success: boolean; data: RenderPromptResponse }>(
      `/api/workspace/prompts/${id}/render`,
      { variables }
    );
    return response.data.rendered;
  },

  /**
   * Get categories with counts
   */
  async getCategories(): Promise<CategoryInfo[]> {
    const response = await apiClient.get<{ success: boolean; data: CategoryInfo[] }>(
      '/api/workspace/prompts/categories'
    );
    return response.data;
  },

  /**
   * Get featured templates
   */
  async getFeatured(limit?: number): Promise<PromptTemplate[]> {
    const params = limit ? `?limit=${limit}` : '';
    const response = await apiClient.get<{ success: boolean; data: PromptTemplate[] }>(
      `/api/workspace/prompts/featured${params}`
    );
    return response.data;
  },
};