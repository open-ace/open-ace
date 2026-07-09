/**
 * Error Handling Utilities
 *
 * Common error handling functions for consistent error message handling.
 */

/**
 * API Error structure
 */
export interface ApiErrorDetail {
  message: string;
  status?: number;
  code?: string;
  details?: unknown;
}

/**
 * Get error message from various error types
 *
 * @param err - Unknown error object
 * @param fallback - Fallback message if error cannot be parsed
 * @returns Error message string
 */
export function getErrorMessage(err: unknown, fallback: string): string {
  // Already an ApiError-like object with message
  if (err && typeof err === 'object' && 'message' in err) {
    const errorObj = err as { message?: string };
    if (typeof errorObj.message === 'string' && errorObj.message.trim()) {
      return errorObj.message;
    }
  }

  // Standard Error object
  if (err instanceof Error) {
    return err.message || fallback;
  }

  // String error
  if (typeof err === 'string') {
    return err || fallback;
  }

  return fallback;
}

/**
 * Parse API error details from various error types
 *
 * @param err - Unknown error object
 * @returns Parsed API error details
 */
export function parseApiError(err: unknown): ApiErrorDetail {
  const result: ApiErrorDetail = {
    message: getErrorMessage(err, 'An unexpected error occurred'),
  };

  if (err && typeof err === 'object') {
    const errorObj = err as Record<string, unknown>;

    // Extract status code
    if (typeof errorObj.status === 'number') {
      result.status = errorObj.status;
    }

    // Extract error code
    if (typeof errorObj.code === 'string') {
      result.code = errorObj.code;
    }

    // Extract details
    if (errorObj.details !== undefined) {
      result.details = errorObj.details;
    }
  }

  return result;
}

/**
 * Check if error is a conflict error (HTTP 409)
 *
 * @param err - Unknown error object
 * @returns True if error is a conflict error
 */
export function isConflictError(err: unknown): boolean {
  if (err && typeof err === 'object') {
    const errorObj = err as { status?: number };
    return errorObj.status === 409;
  }
  return false;
}

/**
 * Check if error is a not found error (HTTP 404)
 *
 * @param err - Unknown error object
 * @returns True if error is a not found error
 */
export function isNotFoundError(err: unknown): boolean {
  if (err && typeof err === 'object') {
    const errorObj = err as { status?: number };
    return errorObj.status === 404;
  }
  return false;
}

/**
 * Check if error is an unauthorized error (HTTP 401)
 *
 * @param err - Unknown error object
 * @returns True if error is an unauthorized error
 */
export function isUnauthorizedError(err: unknown): boolean {
  if (err && typeof err === 'object') {
    const errorObj = err as { status?: number };
    return errorObj.status === 401;
  }
  return false;
}

/**
 * Check if error is a forbidden error (HTTP 403)
 *
 * @param err - Unknown error object
 * @returns True if error is a forbidden error
 */
export function isForbiddenError(err: unknown): boolean {
  if (err && typeof err === 'object') {
    const errorObj = err as { status?: number };
    return errorObj.status === 403;
  }
  return false;
}

/**
 * Check if error is a validation error (HTTP 400)
 *
 * @param err - Unknown error object
 * @returns True if error is a validation error
 */
export function isValidationError(err: unknown): boolean {
  if (err && typeof err === 'object') {
    const errorObj = err as { status?: number };
    return errorObj.status === 400;
  }
  return false;
}
