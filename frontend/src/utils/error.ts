/**
 * Error Utilities
 *
 * Shared error handling functions for API error mapping and display.
 */

import { t, type Language } from '@/i18n';

/**
 * API error response type with optional error field
 */
interface ApiErrorResponse {
  message?: string;
  code?: string;
  error?: string;
  status?: number;
  details?: Record<string, unknown>;
}

/**
 * API error code to translation key mapping
 */
const ERROR_CODE_MAP: Record<string, string> = {
  provider_already_exists: 'providerAlreadyExists',
  invalid_client_id: 'clientIdInvalid',
  invalid_client_secret: 'clientSecretInvalid',
  failed_to_register_provider: 'failedToRegisterProvider',
  failed_to_disable_provider: 'failedToDisableProvider',
  failed_to_enable_provider: 'failedToEnableProvider',
  provider_not_found: 'providerNotFound',
  tenant_not_found: 'tenantNotFound',
  unauthorized: 'unauthorized',
  forbidden: 'forbidden',
};

/**
 * Map API error to localized error message
 *
 * @param error - Error object from API call
 * @param language - Current language for localization
 * @param fallbackKey - Fallback translation key if error code not found
 * @returns Localized error message string
 *
 * @example
 * mapApiError({ code: 'provider_already_exists' }, 'en')
 * // returns translated message for providerAlreadyExists
 */
export function mapApiError(
  error: unknown,
  language: Language,
  fallbackKey: string = 'unknownError'
): string {
  // Handle null/undefined
  if (!error) {
    return t(fallbackKey, language);
  }

  // Handle Error instances
  if (error instanceof Error) {
    return error.message || t(fallbackKey, language);
  }

  // Handle object errors (API error response)
  if (typeof error === 'object' && error !== null) {
    const apiError = error as ApiErrorResponse;

    // Check for specific error codes
    if (apiError.code) {
      const translationKey = ERROR_CODE_MAP[apiError.code];
      if (translationKey) {
        return t(translationKey, language);
      }
    }

    // Check for error message
    if (apiError.message) {
      return apiError.message;
    }

    // Check for error field
    if (apiError.error) {
      const translationKey = ERROR_CODE_MAP[apiError.error];
      if (translationKey) {
        return t(translationKey, language);
      }
      return apiError.error;
    }
  }

  // Fallback to unknown error
  return t(fallbackKey, language);
}

/**
 * Extract error message from various error types
 *
 * @param error - Error object
 * @returns Error message string
 */
export function getErrorMessage(error: unknown): string {
  if (!error) {
    return 'Unknown error';
  }

  if (error instanceof Error) {
    return error.message;
  }

  if (typeof error === 'object' && error !== null) {
    const apiError = error as ApiErrorResponse;
    if (apiError.message) {
      return apiError.message;
    }
    if (apiError.error) {
      return apiError.error;
    }
  }

  return String(error);
}

/**
 * Check if error is an API error with code
 *
 * @param error - Error object
 * @param code - Expected error code
 * @returns true if error matches the code
 */
export function isApiErrorCode(error: unknown, code: string): boolean {
  if (typeof error === 'object' && error !== null) {
    const apiError = error as ApiErrorResponse;
    return apiError.code === code || apiError.error === code;
  }
  return false;
}