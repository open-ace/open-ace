/**
 * Messages API - Messages related API calls
 */

import { apiClient } from './client';
import type { Message, MessageFilters } from '@/types';

// Backend response format
interface BackendMessagesResponse {
  messages: Message[];
  total: number;
  limit: number;
  offset: number;
  has_more: boolean;
}

// Frontend response format
export interface MessagesResponse {
  data: Message[];
  pagination: {
    page: number;
    totalPages: number;
    total: number;
    hasMore: boolean;
  };
}

// Conversation history types
export interface ConversationHistory {
  session_id: string;
  tool_name: string;
  host_name: string;
  sender_name: string;
  sender_id: string;
  date: string;
  message_count: number;
  total_tokens: number;
  total_input_tokens: number;
  total_output_tokens: number;
  first_message_time: string;
  last_message_time: string;
}

export interface ConversationMessage {
  id: number;
  feishu_conversation_id: string;
  tool_name: string;
  host_name: string;
  sender_name: string;
  sender_id: string;
  date: string;
  role: string;
  content: string;
  tokens_used: number;
  input_tokens: number;
  output_tokens: number;
  timestamp: string;
}

export const messagesApi = {
  /**
   * Get messages with filters and pagination
   */
  async getMessages(
    filters: MessageFilters = {},
    page: number = 1,
    pageSize: number = 20
  ): Promise<MessagesResponse> {
    // Convert page-based pagination to offset-based for backend
    const offset = (page - 1) * pageSize;

    const params: Record<string, string> = {
      limit: String(pageSize),
      offset: String(offset),
    };

    if (filters.tool) params.tool = filters.tool;
    if (filters.host) params.host = filters.host;
    if (filters.sender) params.sender = filters.sender;
    if (filters.startDate) params.start_date = filters.startDate;
    if (filters.endDate) params.end_date = filters.endDate;
    if (filters.role && filters.role.length > 0) params.role = filters.role.join(',');
    if (filters.search) params.search = filters.search;

    const response = await apiClient.get<BackendMessagesResponse>('/api/messages', params);

    // Transform backend response to frontend format
    return {
      data: response.messages,
      pagination: {
        page,
        totalPages: Math.ceil(response.total / pageSize),
        total: response.total,
        hasMore: response.has_more,
      },
    };
  },

  /**
   * Get a single message by ID
   */
  async getMessage(id: string): Promise<Message> {
    const response = await apiClient.get<Message>(`/api/messages/${id}`);
    return response;
  },

  /**
   * Get message count
   */
  async getMessageCount(filters: MessageFilters = {}): Promise<number> {
    const params: Record<string, string> = {};

    if (filters.tool) params.tool = filters.tool;
    if (filters.host) params.host = filters.host;
    if (filters.sender) params.sender = filters.sender;
    if (filters.startDate) params.start_date = filters.startDate;
    if (filters.endDate) params.end_date = filters.endDate;
    if (filters.role && filters.role.length > 0) params.role = filters.role.join(',');
    if (filters.search) params.search = filters.search;

    const response = await apiClient.get<{ count: number }>('/api/messages/count', params);
    return response.count;
  },

  /**
   * Get conversation history
   */
  async getConversationHistory(
    filters: {
      date?: string;
      tool?: string;
      host?: string;
      sender?: string;
    } = {},
    page: number = 1,
    pageSize: number = 20
  ): Promise<{ data: ConversationHistory[]; total: number }> {
    const offset = (page - 1) * pageSize;

    const params: Record<string, string> = {
      limit: String(pageSize),
      offset: String(offset),
    };

    if (filters.date) params.date = filters.date;
    if (filters.tool) params.tool = filters.tool;
    if (filters.host) params.host = filters.host;
    if (filters.sender) params.sender = filters.sender;

    const response = await apiClient.get<ConversationHistory[]>(
      '/api/conversation-history',
      params
    );

    return {
      data: response,
      total: response.length,
    };
  },

  /**
   * Get conversation timeline (messages in a conversation)
   */
  async getConversationTimeline(sessionId: string): Promise<ConversationMessage[]> {
    return apiClient.get<ConversationMessage[]>(`/api/conversation-timeline/${sessionId}`);
  },

  /**
   * Get conversation timeline with latency data
   */
  async getConversationTimelineWithLatency(sessionId: string): Promise<{
    timeline: Array<{
      timestamp: string;
      role: string;
      tokens_used: number;
      model?: string;
      sender_name?: string;
    }>;
    latency_curve: Array<{
      index: number;
      role: string;
      latency: number;
    }>;
  }> {
    return apiClient.get(`/api/conversation-timeline/${sessionId}`);
  },

  /**
   * Get conversation details
   */
  async getConversationDetails(
    sessionId: string
  ): Promise<ConversationHistory & { messages: ConversationMessage[] } | null> {
    return apiClient.get<ConversationHistory & { messages: ConversationMessage[] } | null>(
      `/api/conversation-details/${sessionId}`
    );
  },

  /**
   * Get list of all senders
   */
  async getSenders(host?: string): Promise<string[]> {
    const params: Record<string, string> = {};
    if (host) params.host = host;

    return apiClient.get<string[]>('/api/senders', params);
  },
};
