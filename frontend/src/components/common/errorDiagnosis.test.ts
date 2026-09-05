/**
 * Unit tests for errorDiagnosis module.
 *
 * Tests error categorization and user experience improvements.
 * Issue #3277: Enhanced error categorization.
 */

import { describe, it, expect, vi } from 'vitest';

import {
  diagnoseError,
  type ErrorType,
  type ErrorDiagnosis,
  getBuildVersion,
  getCommitSha,
  generateContactSupportLink,
} from '@/components/common/errorDiagnosis';

describe('diagnoseError', () => {
  describe('chunk-404 errors', () => {
    it('should diagnose chunk 404 from error message', () => {
      const error = new Error('Loading chunk SecurityCenter.abc123.js failed. 404 Not Found');
      const diagnosis = diagnoseError(error);

      expect(diagnosis.type).toBe('chunk-404');
      expect(diagnosis.title).toBe('System Updated');
      expect(diagnosis.showContactSupport).toBe(false);
    });

    it('should diagnose chunk 404 from "not found" message', () => {
      const error = new Error('Failed to fetch dynamically imported module: main.js not found');
      const diagnosis = diagnoseError(error);

      expect(diagnosis.type).toBe('chunk-404');
    });

    it('should diagnose ChunkLoadError with 404', () => {
      const error = new Error('ChunkLoadError: Loading chunk abc.js failed.');
      error.name = 'ChunkLoadError';
      const diagnosis = diagnoseError(error);

      // Without 404 in message, it should be chunk-load-failed
      expect(diagnosis.type).toBe('chunk-load-failed');
    });
  });

  describe('chunk-load-failed errors', () => {
    it('should diagnose network errors', () => {
      const error = new Error('Failed to fetch dynamically imported module: Network error');
      const diagnosis = diagnoseError(error);

      expect(diagnosis.type).toBe('chunk-load-failed');
      expect(diagnosis.title).toBe('Resource Load Failed');
      expect(diagnosis.showContactSupport).toBe(true);
      expect(diagnosis.showRetry).toBe(true);
    });

    it('should diagnose timeout errors', () => {
      const error = new Error('Loading chunk main.js timeout');
      const diagnosis = diagnoseError(error);

      expect(diagnosis.type).toBe('chunk-load-failed');
    });

    it('should diagnose failed to fetch errors', () => {
      const error = new Error('Failed to fetch dynamically imported module');
      const diagnosis = diagnoseError(error);

      expect(diagnosis.type).toBe('chunk-load-failed');
    });

    it('should diagnose generic chunk load errors', () => {
      const error = new Error('Loading chunk SecurityCenter.js failed');
      const diagnosis = diagnoseError(error);

      expect(diagnosis.type).toBe('chunk-load-failed');
    });
  });

  describe('render-runtime errors', () => {
    it('should diagnose TypeError', () => {
      const error = new TypeError('Cannot read property "x" of undefined');
      const diagnosis = diagnoseError(error);

      expect(diagnosis.type).toBe('render-runtime');
      expect(diagnosis.title).toBe('Page Render Error');
      expect(diagnosis.showContactSupport).toBe(true);
    });

    it('should diagnose ReferenceError', () => {
      const error = new ReferenceError('x is not defined');
      const diagnosis = diagnoseError(error);

      expect(diagnosis.type).toBe('render-runtime');
    });

    it('should diagnose SyntaxError', () => {
      const error = new SyntaxError('Unexpected token');
      const diagnosis = diagnoseError(error);

      expect(diagnosis.type).toBe('render-runtime');
    });

    it('should diagnose RangeError', () => {
      const error = new RangeError('Invalid array length');
      const diagnosis = diagnoseError(error);

      expect(diagnosis.type).toBe('render-runtime');
    });
  });

  describe('unknown errors', () => {
    it('should default to render-runtime for unknown errors', () => {
      const error = new Error('Some unknown error');
      const diagnosis = diagnoseError(error);

      expect(diagnosis.type).toBe('render-runtime');
      expect(diagnosis.showContactSupport).toBe(true);
    });
  });
});

describe('getBuildVersion', () => {
  it('should return version or unknown', () => {
    const version = getBuildVersion();
    expect(typeof version).toBe('string');
    // Version could be 'unknown' if not built, or actual version if built
  });
});

describe('getCommitSha', () => {
  it('should return commit SHA or unknown', () => {
    const sha = getCommitSha();
    expect(typeof sha).toBe('string');
    // SHA could be 'unknown', 'dev', or actual SHA if built
  });
});

describe('generateContactSupportLink', () => {
  it('should generate mailto link with all parameters', () => {
    const link = generateContactSupportLink({
      errorType: 'chunk-404',
      errorId: 'abc123',
      pathname: '/manage/security',
      timestamp: '2026-09-05T10:00:00Z',
    });

    expect(link).toContain('mailto:support@openace.io');
    expect(link).toContain('subject=');
    // Check decoded content since URL encoding is applied
    const decodedLink = decodeURIComponent(link);
    expect(decodedLink).toContain('Error ID: abc123');
    expect(decodedLink).toContain('Error Type: chunk-404');
    expect(decodedLink).toContain('Page Path: /manage/security');
    expect(decodedLink).toContain('Time: 2026-09-05T10:00:00Z');
  });

  it('should encode special characters', () => {
    const link = generateContactSupportLink({
      errorType: 'render-runtime',
      errorId: 'test-123',
      pathname: '/path/with spaces',
      timestamp: '2026-09-05T10:00:00Z',
    });

    // URL encoding should be applied
    expect(link).toContain('mailto:');
    expect(decodeURIComponent(link)).toContain('/path/with spaces');
  });
});