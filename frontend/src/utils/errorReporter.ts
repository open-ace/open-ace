/**
 * Error Reporter Module
 *
 * Captures and reports frontend errors with sanitization and deduplication.
 */

// Constants
const MAX_ERROR_LOGS = 50;
const MAX_PAYLOAD_SIZE = 60 * 1024; // 60KB
const DEDUPE_WINDOW = 60 * 1000; // 60 seconds
const MAX_DEDUPE_KEYS = 100;

// Types
export type ErrorCategory = 'chunk-load' | 'render-runtime';

export interface FrontendErrorPayload {
  category: ErrorCategory;
  errorId: string;
  name: string;
  message: string;
  stack?: string;
  componentStack?: string;
  pathname: string;
  buildVersion: string;
  commitSha: string;
  timestamp: number;
  userAgent: string;
}

// Deduplication cache
const dedupeCache = new Map<string, number>();

/**
 * Generate unique error ID (6-digit timestamp + 4-digit random)
 */
export function generateErrorId(): string {
  const timestamp = Date.now().toString(36).slice(-6);
  const random = Math.random().toString(36).slice(2, 6);
  return timestamp + random;
}

/**
 * Sanitize URL encoded sensitive fields
 */
function sanitizeUrlEncoded(text: string): string {
  // URL encoded patterns for sensitive fields
  const URL_ENCODED_PATTERNS: Record<string, RegExp> = {
    token: /%74%6f%6b%65%6e/i,
    session: /%73%65%73%73%69%6f%6e/i,
    password: /%70%61%73%73%77%6f%72%64/i,
    api_key: /%61%70%69%5f%6b%65%79/i,
  };

  // Try to decode and sanitize
  try {
    const decoded = decodeURIComponent(text);
    const sanitized = sanitizePlaintext(decoded);
    if (sanitized !== decoded) {
      return encodeURIComponent(sanitized);
    }
    return text;
  } catch {
    // Decoding failed, check for encoded patterns
    let result = text;
    for (const [key, pattern] of Object.entries(URL_ENCODED_PATTERNS)) {
      if (pattern.test(result)) {
        result = result.replace(pattern, `[REDACTED_${key.toUpperCase()}]`);
      }
    }
    return result;
  }
}

/**
 * Sanitize plaintext sensitive fields
 */
function sanitizePlaintext(text: string): string {
  const SENSITIVE_PATTERNS = [
    /token[=:][^\s&]+/gi,
    /session[=:][^\s&]+/gi,
    /session_id[=:][^\s&]+/gi,
    /password[=:][^\s&]+/gi,
    /api_key[=:][^\s&]+/gi,
    /apikey[=:][^\s&]+/gi,
    /secret[=:][^\s&]+/gi,
  ];

  let sanitized = text;
  SENSITIVE_PATTERNS.forEach((pattern) => {
    sanitized = sanitized.replace(pattern, '[REDACTED]');
  });

  return sanitized;
}

/**
 * Sanitize pathname by removing query parameter values
 */
export function sanitizePathname(pathname: string): string {
  return pathname.replace(/=([^&]+)/g, '=');
}

/**
 * Sanitize message (with URL encoding support)
 */
export function sanitizeMessage(message: string): string {
  // First sanitize URL encoded content
  let sanitized = sanitizeUrlEncoded(message);
  // Then sanitize plaintext
  sanitized = sanitizePlaintext(sanitized);
  return sanitized;
}

/**
 * Remove project absolute path prefixes
 */
function sanitizePath(path: string): string {
  return path
    .replace(/\/home\/[^/]+\/[^/]+\//g, '')
    .replace(/\/Users\/[^/]+\/[^/]+\//g, '')
    .replace(/\/var\/www\/[^/]+\//g, '')
    .replace(/[A-Z]:\\Users\\[^\\]+\\[^\\]+\\/gi, '');
}

/**
 * Truncate component stack (preserve last component name)
 */
export function truncateComponentStack(stack: string, maxLines: number = 10): string {
  const lines = stack.split('\n');
  if (lines.length <= maxLines) return stack;

  const truncated = lines.slice(0, maxLines);

  // Extract last component name
  const lastComponentMatch = lines[maxLines - 1]?.match(/at\s+(\w+)/);
  const lastComponent = lastComponentMatch ? lastComponentMatch[1] : 'unknown';

  return truncated.join('\n') + `\n... (truncated, last component: ${lastComponent})`;
}

/**
 * Truncate stack trace (preserve last function name)
 */
export function truncateStack(stack: string, maxLines: number = 20): string {
  const lines = stack.split('\n');
  if (lines.length <= maxLines) return stack;

  const truncated = lines.slice(0, maxLines);

  // Extract last function name
  const lastFunctionMatch = lines[maxLines - 1]?.match(/at\s+(\w+)/);
  const lastFunction = lastFunctionMatch ? lastFunctionMatch[1] : 'unknown';

  return truncated.join('\n') + `\n... (truncated, last function: ${lastFunction})`;
}

/**
 * Sanitize error payload
 */
export function sanitizeErrorPayload(payload: FrontendErrorPayload): FrontendErrorPayload {
  return {
    ...payload,
    pathname: sanitizePathname(payload.pathname),
    message: sanitizeMessage(payload.message),
    stack: payload.stack ? truncateStack(sanitizePath(sanitizeMessage(payload.stack))) : undefined,
    componentStack: payload.componentStack
      ? truncateComponentStack(sanitizePath(payload.componentStack))
      : undefined,
  };
}

/**
 * Check and truncate payload size
 */
export function checkAndTruncatePayload(payload: FrontendErrorPayload): FrontendErrorPayload {
  let size = new Blob([JSON.stringify(payload)]).size;

  if (size <= MAX_PAYLOAD_SIZE) return payload;

  // Truncate componentStack
  if (payload.componentStack) {
    payload.componentStack = truncateComponentStack(payload.componentStack, 10);
  }

  size = new Blob([JSON.stringify(payload)]).size;
  if (size <= MAX_PAYLOAD_SIZE) return payload;

  // Truncate stack
  if (payload.stack) {
    payload.stack = truncateStack(payload.stack, 20);
  }

  size = new Blob([JSON.stringify(payload)]).size;
  if (size <= MAX_PAYLOAD_SIZE) return payload;

  // Remove componentStack as last resort
  delete payload.componentStack;

  return payload;
}

/**
 * Check if error should be deduplicated
 */
export function shouldDedupe(error: Error): boolean {
  const key = `${error.name}:${error.message}`;
  const now = Date.now();

  // Clean up expired entries
  for (const [k, timestamp] of dedupeCache.entries()) {
    if (now - timestamp > DEDUPE_WINDOW) {
      dedupeCache.delete(k);
    }
  }

  // LRU eviction
  if (dedupeCache.size >= MAX_DEDUPE_KEYS) {
    const firstKey = dedupeCache.keys().next().value;
    if (firstKey) {
      dedupeCache.delete(firstKey);
    }
  }

  // Check dedupe
  const lastTime = dedupeCache.get(key);
  if (lastTime && now - lastTime < DEDUPE_WINDOW) {
    return true;
  }

  // Record this error
  dedupeCache.set(key, now);
  return false;
}

/**
 * Log fallback to window.__errorLogs__
 */
export function logFallback(...args: unknown[]): void {
  try {
    const errorLogs = (window as any).__errorLogs__ ?? [];
    errorLogs.push({
      timestamp: Date.now(),
      args: args.map((arg) =>
        arg instanceof Error ? { name: arg.name, message: arg.message, stack: arg.stack } : arg
      ),
    });

    // FIFO eviction
    if (errorLogs.length > MAX_ERROR_LOGS) {
      errorLogs.shift();
    }

    (window as any).__errorLogs__ = errorLogs;

    // Dev mode console output
    if (import.meta.env.DEV) {
      console.error('[ErrorReporter]', ...args);
    }
  } catch {
    // Completely swallow exceptions
  }
}

/**
 * Send error report via fetch (with CORS and keepalive)
 */
function sendWithFetch(payload: FrontendErrorPayload): boolean {
  try {
    fetch('/api/frontend-errors', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
      credentials: 'include',
      keepalive: true,
    }).catch(() => {
      logFallback('Fetch failed for error report', payload);
    });
    return true;
  } catch {
    return false;
  }
}

/**
 * Send error report via sendBeacon or fetch
 */
function sendWithBeacon(payload: FrontendErrorPayload): boolean {
  try {
    // Try sendBeacon first
    if (navigator.sendBeacon) {
      const blob = new Blob([JSON.stringify(payload)], { type: 'application/json' });
      const sent = navigator.sendBeacon('/api/frontend-errors', blob);
      if (sent) return true;
    }

    // Fallback to fetch
    return sendWithFetch(payload);
  } catch {
    return sendWithFetch(payload);
  }
}

/**
 * Main error reporting function
 */
export function reportFrontendError(params: {
  error: Error;
  errorInfo?: { componentStack?: string };
  category: ErrorCategory;
}): string {
  const { error, errorInfo, category } = params;

  // Generate error ID
  const errorId = generateErrorId();

  // Build payload
  let payload: FrontendErrorPayload = {
    category,
    errorId,
    name: error.name || 'Error',
    message: error.message || 'Unknown error',
    stack: error.stack,
    componentStack: errorInfo?.componentStack,
    pathname: window.location.pathname,
    buildVersion: (typeof __BUILD_VERSION__ !== 'undefined' && __BUILD_VERSION__) || 'unknown',
    commitSha: (typeof __COMMIT_SHA__ !== 'undefined' && __COMMIT_SHA__) || 'dev',
    timestamp: Date.now(),
    userAgent: navigator.userAgent,
  };

  // Sanitize
  payload = sanitizeErrorPayload(payload);

  // Check size and truncate
  payload = checkAndTruncatePayload(payload);

  // Dedupe
  if (shouldDedupe(error)) {
    logFallback('Duplicate error suppressed', errorId);
    return errorId;
  }

  // Send
  const sent = sendWithBeacon(payload);
  if (!sent) {
    logFallback('Failed to send error report', payload);
  }

  return errorId;
}

export default {
  generateErrorId,
  sanitizePathname,
  sanitizeMessage,
  truncateComponentStack,
  truncateStack,
  sanitizeErrorPayload,
  checkAndTruncatePayload,
  shouldDedupe,
  logFallback,
  reportFrontendError,
};
