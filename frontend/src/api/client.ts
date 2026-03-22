/**
 * API Client - Centralized HTTP client for API requests
 *
 * Features:
 * - Automatic retry on transient failures
 * - Friendly error messages
 * - Request timeout handling
 */

import type { ApiError } from '@/types';

const API_BASE_URL = '';
const DEFAULT_TIMEOUT = 30000; // 30 seconds
const MAX_RETRIES = 3;
const RETRY_DELAY = 1000; // 1 second

interface RequestConfig {
  method?: 'GET' | 'POST' | 'PUT' | 'DELETE' | 'PATCH';
  headers?: Record<string, string>;
  body?: unknown;
  signal?: AbortSignal;
  timeout?: number;
  retries?: number;
}

/**
 * Get user-friendly error message based on error type
 */
function getFriendlyErrorMessage(error: unknown, status?: number): string {
  // Network errors
  if (error instanceof TypeError && error.message.includes('fetch')) {
    return 'Network error. Please check your internet connection and try again.';
  }

  // Timeout errors
  if (error instanceof Error && error.name === 'AbortError') {
    return 'Request timed out. Please try again.';
  }

  // HTTP status based errors
  if (status) {
    switch (status) {
      case 400:
        return 'Invalid request. Please check your input and try again.';
      case 401:
        return 'Authentication required. Please log in and try again.';
      case 403:
        return 'You do not have permission to perform this action.';
      case 404:
        return 'The requested resource was not found.';
      case 409:
        return 'A conflict occurred. The resource may have been modified by another user.';
      case 429:
        return 'Too many requests. Please wait a moment and try again.';
      case 500:
        return 'Server error. Please try again later.';
      case 502:
      case 503:
      case 504:
        return 'Service temporarily unavailable. Please try again later.';
      default:
        return `Request failed with status ${status}. Please try again.`;
    }
  }

  // Generic error
  if (error instanceof Error) {
    return error.message || 'An unexpected error occurred. Please try again.';
  }

  return 'An unexpected error occurred. Please try again.';
}

/**
 * Check if error is retryable
 */
function isRetryableError(error: unknown, status?: number): boolean {
  // Network errors are retryable
  if (error instanceof TypeError) {
    return true;
  }

  // Certain HTTP status codes are retryable
  if (status) {
    return [408, 429, 500, 502, 503, 504].includes(status);
  }

  return false;
}

/**
 * Sleep for specified milliseconds
 */
function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

class ApiClient {
  private baseUrl: string;

  constructor(baseUrl: string = '') {
    this.baseUrl = baseUrl;
  }

  private async request<T>(endpoint: string, config: RequestConfig = {}): Promise<T> {
    const {
      method = 'GET',
      headers = {},
      body,
      signal,
      timeout = DEFAULT_TIMEOUT,
      retries = MAX_RETRIES,
    } = config;

    const url = `${this.baseUrl}${endpoint}`;

    const requestHeaders: Record<string, string> = {
      'Content-Type': 'application/json',
      ...headers,
    };

    // Create abort controller for timeout
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), timeout);

    // Combine user signal with timeout signal
    const combinedSignal = signal
      ? this.combineSignals(signal, controller.signal)
      : controller.signal;

    let lastError: ApiError | null = null;

    // Retry loop
    for (let attempt = 0; attempt <= retries; attempt++) {
      try {
        const response = await fetch(url, {
          method,
          headers: requestHeaders,
          body: body ? JSON.stringify(body) : undefined,
          signal: combinedSignal,
        });

        clearTimeout(timeoutId);

        if (!response.ok) {
          const error: ApiError = {
            message: getFriendlyErrorMessage(null, response.status),
            status: response.status,
          };

          try {
            const errorData = await response.json();
            error.message = errorData.error ?? errorData.message ?? error.message;
            error.code = errorData.code;
            error.details = errorData.details;
          } catch {
            // Ignore JSON parse errors
          }

          // Check if we should retry
          if (attempt < retries && isRetryableError(null, response.status)) {
            await sleep(RETRY_DELAY * (attempt + 1));
            continue;
          }

          throw error;
        }

        // Handle empty responses
        const text = await response.text();
        if (!text) {
          return {} as T;
        }

        return JSON.parse(text) as T;
      } catch (error) {
        clearTimeout(timeoutId);

        // Don't retry on abort
        if (error instanceof Error && error.name === 'AbortError') {
          throw {
            message: getFriendlyErrorMessage(error),
            status: 0,
          } as ApiError;
        }

        // Create API error
        const apiError: ApiError = {
          message: getFriendlyErrorMessage(error),
          status: 0,
        };

        // Check if we should retry
        if (attempt < retries && isRetryableError(error)) {
          lastError = apiError;
          await sleep(RETRY_DELAY * (attempt + 1));
          continue;
        }

        throw apiError;
      }
    }

    // All retries exhausted
    throw lastError ?? { message: 'Request failed after multiple attempts.', status: 0 };
  }

  /**
   * Combine multiple abort signals
   */
  private combineSignals(...signals: AbortSignal[]): AbortSignal {
    const controller = new AbortController();

    for (const signal of signals) {
      if (signal.aborted) {
        controller.abort();
        break;
      }
      signal.addEventListener('abort', () => controller.abort());
    }

    return controller.signal;
  }

  async get<T>(
    endpoint: string,
    params?: Record<string, string>,
    signal?: AbortSignal
  ): Promise<T> {
    let url = endpoint;
    if (params && Object.keys(params).length > 0) {
      const searchParams = new URLSearchParams();
      Object.entries(params).forEach(([key, value]) => {
        if (value !== undefined && value !== null && value !== '') {
          searchParams.append(key, value);
        }
      });
      const queryString = searchParams.toString();
      if (queryString) {
        url = `${endpoint}?${queryString}`;
      }
    }
    return this.request<T>(url, { method: 'GET', signal });
  }

  async post<T>(endpoint: string, body?: unknown, signal?: AbortSignal): Promise<T> {
    return this.request<T>(endpoint, { method: 'POST', body, signal });
  }

  async put<T>(endpoint: string, body?: unknown, signal?: AbortSignal): Promise<T> {
    return this.request<T>(endpoint, { method: 'PUT', body, signal });
  }

  async patch<T>(endpoint: string, body?: unknown, signal?: AbortSignal): Promise<T> {
    return this.request<T>(endpoint, { method: 'PATCH', body, signal });
  }

  async delete<T>(endpoint: string, signal?: AbortSignal): Promise<T> {
    return this.request<T>(endpoint, { method: 'DELETE', signal });
  }
}

export const apiClient = new ApiClient(API_BASE_URL);
export { ApiClient };
