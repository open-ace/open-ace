/**
 * Tests for Error Handling Utilities
 */

import { describe, it, expect } from 'vitest';
import {
  getErrorMessage,
  parseApiError,
  isConflictError,
  isNotFoundError,
  isUnauthorizedError,
  isForbiddenError,
  isValidationError,
} from './error';

describe('Error Handling Utilities', () => {
  describe('getErrorMessage', () => {
    it('should return message from Error object', () => {
      const err = new Error('Test error message');
      expect(getErrorMessage(err, 'fallback')).toBe('Test error message');
    });

    it('should return fallback for Error with empty message', () => {
      const err = new Error('');
      expect(getErrorMessage(err, 'fallback')).toBe('fallback');
    });

    it('should return message from object with message property', () => {
      const err = { message: 'API error', status: 500 };
      expect(getErrorMessage(err, 'fallback')).toBe('API error');
    });

    it('should return string error directly', () => {
      expect(getErrorMessage('String error', 'fallback')).toBe('String error');
    });

    it('should return fallback for empty string', () => {
      expect(getErrorMessage('', 'fallback')).toBe('fallback');
    });

    it('should return fallback for null', () => {
      expect(getErrorMessage(null, 'fallback')).toBe('fallback');
    });

    it('should return fallback for undefined', () => {
      expect(getErrorMessage(undefined, 'fallback')).toBe('fallback');
    });

    it('should return fallback for number', () => {
      expect(getErrorMessage(123, 'fallback')).toBe('fallback');
    });

    it('should return fallback for object without message', () => {
      expect(getErrorMessage({ code: 'ERROR' }, 'fallback')).toBe('fallback');
    });
  });

  describe('parseApiError', () => {
    it('should parse full API error', () => {
      const err = {
        message: 'Conflict',
        status: 409,
        code: 'RESOURCE_CONFLICT',
        details: { field: 'name' },
      };
      const result = parseApiError(err);
      expect(result.message).toBe('Conflict');
      expect(result.status).toBe(409);
      expect(result.code).toBe('RESOURCE_CONFLICT');
      expect(result.details).toEqual({ field: 'name' });
    });

    it('should parse error with only message', () => {
      const err = { message: 'Error' };
      const result = parseApiError(err);
      expect(result.message).toBe('Error');
      expect(result.status).toBeUndefined();
      expect(result.code).toBeUndefined();
    });

    it('should parse standard Error object', () => {
      const err = new Error('Network error');
      const result = parseApiError(err);
      expect(result.message).toBe('Network error');
    });

    it('should provide default message for unknown error', () => {
      const result = parseApiError(null);
      expect(result.message).toBe('An unexpected error occurred');
    });
  });

  describe('isConflictError', () => {
    it('should return true for 409 status', () => {
      expect(isConflictError({ status: 409 })).toBe(true);
    });

    it('should return false for other status codes', () => {
      expect(isConflictError({ status: 200 })).toBe(false);
      expect(isConflictError({ status: 404 })).toBe(false);
      expect(isConflictError({ status: 500 })).toBe(false);
    });

    it('should return false for non-object', () => {
      expect(isConflictError(null)).toBe(false);
      expect(isConflictError('error')).toBe(false);
      expect(isConflictError(409)).toBe(false);
    });
  });

  describe('isNotFoundError', () => {
    it('should return true for 404 status', () => {
      expect(isNotFoundError({ status: 404 })).toBe(true);
    });

    it('should return false for other status codes', () => {
      expect(isNotFoundError({ status: 200 })).toBe(false);
      expect(isNotFoundError({ status: 409 })).toBe(false);
    });
  });

  describe('isUnauthorizedError', () => {
    it('should return true for 401 status', () => {
      expect(isUnauthorizedError({ status: 401 })).toBe(true);
    });

    it('should return false for other status codes', () => {
      expect(isUnauthorizedError({ status: 200 })).toBe(false);
      expect(isUnauthorizedError({ status: 403 })).toBe(false);
    });
  });

  describe('isForbiddenError', () => {
    it('should return true for 403 status', () => {
      expect(isForbiddenError({ status: 403 })).toBe(true);
    });

    it('should return false for other status codes', () => {
      expect(isForbiddenError({ status: 200 })).toBe(false);
      expect(isForbiddenError({ status: 401 })).toBe(false);
    });
  });

  describe('isValidationError', () => {
    it('should return true for 400 status', () => {
      expect(isValidationError({ status: 400 })).toBe(true);
    });

    it('should return false for other status codes', () => {
      expect(isValidationError({ status: 200 })).toBe(false);
      expect(isValidationError({ status: 404 })).toBe(false);
    });
  });
});
