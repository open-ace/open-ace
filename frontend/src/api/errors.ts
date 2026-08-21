/**
 * Frontend Error API Types
 */

import type { ErrorCategory } from '@utils/errorReporter';

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

export interface FrontendErrorResponse {
  status: 'ok' | 'error';
  error?: string;
}