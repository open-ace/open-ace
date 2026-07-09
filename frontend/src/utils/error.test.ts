/**
 * Tests for Error Utilities
 */

import { describe, it, expect, vi } from 'vitest';
import { mapApiError, getErrorMessage, isApiErrorCode } from './error';
import type { Language } from '@/i18n';

// Mock the i18n module
vi.mock('@/i18n', () => ({
  t: (key: string, _lang: Language) => {
    const translations: Record<string, string> = {
      providerAlreadyExists: 'Provider already exists',
      clientIdInvalid: 'Invalid client ID',
      clientSecretInvalid: 'Invalid client secret',
      unknownError: 'An unknown error occurred',
      testError: 'Test error message',
    };
    return translations[key] || key;
  },
}));

describe('mapApiError', () => {
  it('should return fallback for null/undefined error', () => {
    expect(mapApiError(null, 'en')).toBe('An unknown error occurred');
    expect(mapApiError(undefined, 'en')).toBe('An unknown error occurred');
  });

  it('should handle Error instances', () => {
    const error = new Error('Test error message');
    expect(mapApiError(error, 'en')).toBe('Test error message');
  });

  it('should map known error codes to translations', () => {
    const error = { code: 'provider_already_exists' };
    expect(mapApiError(error, 'en')).toBe('Provider already exists');
  });

  it('should map error field if no code', () => {
    const error = { error: 'invalid_client_id' };
    expect(mapApiError(error, 'en')).toBe('Invalid client ID');
  });

  it('should return message from error object', () => {
    const error = { message: 'Custom error message' };
    expect(mapApiError(error, 'en')).toBe('Custom error message');
  });

  it('should use custom fallback key', () => {
    expect(mapApiError(null, 'en', 'testError')).toBe('Test error message');
  });
});

describe('getErrorMessage', () => {
  it('should return "Unknown error" for null/undefined', () => {
    expect(getErrorMessage(null)).toBe('Unknown error');
    expect(getErrorMessage(undefined)).toBe('Unknown error');
  });

  it('should extract message from Error instances', () => {
    const error = new Error('Test error');
    expect(getErrorMessage(error)).toBe('Test error');
  });

  it('should extract message from error object', () => {
    expect(getErrorMessage({ message: 'Test' })).toBe('Test');
    expect(getErrorMessage({ error: 'TestError' })).toBe('TestError');
  });

  it('should stringify other values', () => {
    expect(getErrorMessage('string error')).toBe('string error');
    expect(getErrorMessage(123)).toBe('123');
  });
});

describe('isApiErrorCode', () => {
  it('should return true for matching error code', () => {
    expect(isApiErrorCode({ code: 'test_code' }, 'test_code')).toBe(true);
    expect(isApiErrorCode({ error: 'test_code' }, 'test_code')).toBe(true);
  });

  it('should return false for non-matching error code', () => {
    expect(isApiErrorCode({ code: 'test_code' }, 'other_code')).toBe(false);
    expect(isApiErrorCode({ message: 'test' }, 'test_code')).toBe(false);
    expect(isApiErrorCode(null, 'test_code')).toBe(false);
  });
});
