/**
 * Tests for autonomousApi.getAvailableModels (Issue #2667).
 *
 * apiClient only throws on non-2xx responses; a 200 body carrying
 * success:false must also surface as a rejection so React Query lands in its
 * error branch and the modal renders a load error instead of the misleading
 * "no models configured" hint.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { vi } from 'vitest';

vi.mock('./client', () => ({
  apiClient: {
    get: vi.fn(),
  },
}));

import { apiClient } from './client';
import { autonomousApi } from './autonomous';

const getMock = apiClient.get as ReturnType<typeof vi.fn>;

describe('autonomousApi.getAvailableModels (issue #2667)', () => {
  beforeEach(() => {
    getMock.mockReset();
  });

  it('passes a success payload through unchanged (empty_reason contract)', async () => {
    const payload = {
      success: true,
      models: [{ name: 'glm-5' }],
      empty_reason: null,
    };
    getMock.mockResolvedValueOnce(payload);

    await expect(
      autonomousApi.getAvailableModels({ tool: 'claude-code' })
    ).resolves.toEqual(payload);
  });

  it('throws on a 200 response carrying success:false', async () => {
    getMock.mockResolvedValueOnce({
      success: false,
      models: [],
      error: 'Failed to load models for this tool',
    });

    await expect(
      autonomousApi.getAvailableModels({ tool: 'claude-code' })
    ).rejects.toThrow('Failed to load models for this tool');
  });

  it('throws a generic message when success:false has no error field', async () => {
    getMock.mockResolvedValueOnce({ success: false, models: [] });

    await expect(
      autonomousApi.getAvailableModels({ tool: 'claude-code' })
    ).rejects.toThrow('Failed to load models for this tool');
  });
});
