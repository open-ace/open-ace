/**
 * useApiError Hook
 *
 * Unified error handling hook for API calls.
 * Provides consistent error logging and toast notification.
 */

import { useCallback } from 'react';
import { useToast } from '@/components/common';
import { useLanguage } from '@/store';
import { mapApiError } from '@/utils/error';

/**
 * useApiError hook return type
 */
interface UseApiErrorReturn {
  /**
   * Handle API error with logging and toast
   * @param error - Error object from API call
   * @param context - Context string for logging (e.g., 'Failed to register provider')
   * @param fallbackKey - Fallback translation key
   */
  handleError: (error: unknown, context: string, fallbackKey?: string) => void;

  /**
   * Get localized error message without showing toast
   * @param error - Error object
   * @param fallbackKey - Fallback translation key
   * @returns Localized error message
   */
  getErrorMessage: (error: unknown, fallbackKey?: string) => string;

  /**
   * Handle API error and return the message (for setting state)
   * @param error - Error object
   * @param context - Context string for logging
   * @param fallbackKey - Fallback translation key
   * @returns Error message string
   */
  handleAndGetMessage: (error: unknown, context: string, fallbackKey?: string) => string;
}

/**
 * Hook for unified API error handling
 *
 * Usage:
 * ```tsx
 * const { handleError, getErrorMessage } = useApiError();
 *
 * try {
 *   await api.call();
 * } catch (err) {
 *   handleError(err, 'Failed to load data', 'loadFailed');
 * }
 * ```
 */
export function useApiError(): UseApiErrorReturn {
  const { error: toastError } = useToast();
  const language = useLanguage();

  const getErrorMessage = useCallback(
    (error: unknown, fallbackKey: string = 'unknownError'): string => {
      return mapApiError(error, language, fallbackKey);
    },
    [language]
  );

  const handleError = useCallback(
    (error: unknown, context: string, fallbackKey: string = 'unknownError'): void => {
      console.error(`${context}:`, error);
      const message = getErrorMessage(error, fallbackKey);
      toastError(message);
    },
    [getErrorMessage, toastError]
  );

  const handleAndGetMessage = useCallback(
    (error: unknown, context: string, fallbackKey: string = 'unknownError'): string => {
      console.error(`${context}:`, error);
      return getErrorMessage(error, fallbackKey);
    },
    [getErrorMessage]
  );

  return {
    handleError,
    getErrorMessage,
    handleAndGetMessage,
  };
}