/**
 * Tests for API Client
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { apiClient, ApiClient } from './client';

describe('ApiClient', () => {
  let client: ApiClient;

  beforeEach(() => {
    client = new ApiClient('');
    vi.resetAllMocks();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  describe('get', () => {
    it('should make a GET request', async () => {
      const mockData = { message: 'success' };
      vi.mocked(fetch).mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve(mockData),
        text: () => Promise.resolve(JSON.stringify(mockData)),
      } as Response);

      const result = await client.get('/api/test');

      expect(fetch).toHaveBeenCalledWith(
        '/api/test',
        expect.objectContaining({
          method: 'GET',
          headers: expect.objectContaining({
            'Content-Type': 'application/json',
          }),
        })
      );
      expect(result).toEqual(mockData);
    });

    it('should append query params to URL', async () => {
      const mockData = { items: [] };
      vi.mocked(fetch).mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve(mockData),
        text: () => Promise.resolve(JSON.stringify(mockData)),
      } as Response);

      await client.get('/api/test', { page: '1', limit: '10' });

      expect(fetch).toHaveBeenCalledWith('/api/test?page=1&limit=10', expect.any(Object));
    });

    it('should ignore empty params', async () => {
      const mockData = { items: [] };
      vi.mocked(fetch).mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve(mockData),
        text: () => Promise.resolve(JSON.stringify(mockData)),
      } as Response);

      await client.get('/api/test', { page: '1', empty: '' });

      expect(fetch).toHaveBeenCalledWith('/api/test?page=1', expect.any(Object));
    });
  });

  describe('post', () => {
    it('should make a POST request with body', async () => {
      const mockData = { id: 1 };
      const requestBody = { name: 'test' };
      vi.mocked(fetch).mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve(mockData),
        text: () => Promise.resolve(JSON.stringify(mockData)),
      } as Response);

      const result = await client.post('/api/test', requestBody);

      expect(fetch).toHaveBeenCalledWith(
        '/api/test',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify(requestBody),
        })
      );
      expect(result).toEqual(mockData);
    });
  });

  describe('put', () => {
    it('should make a PUT request', async () => {
      const mockData = { updated: true };
      vi.mocked(fetch).mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve(mockData),
        text: () => Promise.resolve(JSON.stringify(mockData)),
      } as Response);

      const result = await client.put('/api/test/1', { name: 'updated' });

      expect(fetch).toHaveBeenCalledWith(
        '/api/test/1',
        expect.objectContaining({
          method: 'PUT',
        })
      );
      expect(result).toEqual(mockData);
    });
  });

  describe('patch', () => {
    it('should make a PATCH request', async () => {
      const mockData = { patched: true };
      vi.mocked(fetch).mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve(mockData),
        text: () => Promise.resolve(JSON.stringify(mockData)),
      } as Response);

      const result = await client.patch('/api/test/1', { name: 'patched' });

      expect(fetch).toHaveBeenCalledWith(
        '/api/test/1',
        expect.objectContaining({
          method: 'PATCH',
        })
      );
      expect(result).toEqual(mockData);
    });
  });

  describe('delete', () => {
    it('should make a DELETE request', async () => {
      vi.mocked(fetch).mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({}),
        text: () => Promise.resolve(''),
      } as Response);

      await client.delete('/api/test/1');

      expect(fetch).toHaveBeenCalledWith(
        '/api/test/1',
        expect.objectContaining({
          method: 'DELETE',
        })
      );
    });
  });

  describe('error handling', () => {
    it('should throw error on non-ok response', async () => {
      vi.mocked(fetch).mockResolvedValueOnce({
        ok: false,
        status: 404,
        json: () => Promise.resolve({ message: 'Not found' }),
        text: () => Promise.resolve(JSON.stringify({ message: 'Not found' })),
      } as Response);

      await expect(client.get('/api/notfound')).rejects.toThrow();
    });

    it('should include error details from response', async () => {
      vi.mocked(fetch).mockResolvedValueOnce({
        ok: false,
        status: 400,
        json: () =>
          Promise.resolve({
            message: 'Validation error',
            code: 'VALIDATION_ERROR',
            details: { field: 'name' },
          }),
        text: () =>
          Promise.resolve(
            JSON.stringify({
              message: 'Validation error',
              code: 'VALIDATION_ERROR',
              details: { field: 'name' },
            })
          ),
      } as Response);

      try {
        await client.get('/api/test');
        expect.fail('Should have thrown');
      } catch (error: any) {
        expect(error.status).toBe(400);
        expect(error.message).toBe('Validation error');
        expect(error.code).toBe('VALIDATION_ERROR');
        expect(error.details).toEqual({ field: 'name' });
      }
    });

    it('should handle empty error response', async () => {
      vi.mocked(fetch).mockResolvedValueOnce({
        ok: false,
        status: 500,
        json: () => Promise.reject(new Error('Invalid JSON')),
        text: () => Promise.resolve(''),
      } as Response);

      try {
        await client.get('/api/test');
        expect.fail('Should have thrown');
      } catch (error: any) {
        expect(error.status).toBe(500);
        expect(error.message).toContain('500');
      }
    });
  });

  describe('abort signal', () => {
    it('should pass abort signal to fetch', async () => {
      const controller = new AbortController();
      const mockData = { message: 'success' };
      vi.mocked(fetch).mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve(mockData),
        text: () => Promise.resolve(JSON.stringify(mockData)),
      } as Response);

      await client.get('/api/test', undefined, controller.signal);

      expect(fetch).toHaveBeenCalledWith(
        '/api/test',
        expect.objectContaining({
          signal: controller.signal,
        })
      );
    });
  });
});

describe('apiClient instance', () => {
  it('should be an instance of ApiClient', () => {
    expect(apiClient).toBeInstanceOf(ApiClient);
  });
});
