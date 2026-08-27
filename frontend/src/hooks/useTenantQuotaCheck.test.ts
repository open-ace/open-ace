/**
 * useTenantQuotaCheck Hook Tests
 *
 * Issue #3132: Tenant management page quota check functionality
 */

import { renderHook, act, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { useTenantQuotaCheck } from './useTenantQuotaCheck';
import { tenantApi } from '@/api';
import type { Tenant } from '@/api';

// Mock dependencies
vi.mock('@/components/common', () => ({
  useToast: () => ({
    error: vi.fn(),
    success: vi.fn(),
  }),
}));

vi.mock('@/store', () => ({
  useLanguage: () => 'en',
}));

vi.mock('@/api', () => ({
  tenantApi: {
    checkQuota: vi.fn(),
  },
}));

describe('useTenantQuotaCheck', () => {
  const mockTenant: Tenant = {
    id: 1,
    name: 'Test Tenant',
    slug: 'test-tenant',
    plan: 'standard',
    status: 'active',
    created_at: '2024-01-01T00:00:00Z',
  };

  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('should have initial state with empty checkingTenants and null checkResult', () => {
    const { result } = renderHook(() => useTenantQuotaCheck());

    expect(result.current.checkingTenants).toBeInstanceOf(Set);
    expect(result.current.checkingTenants.size).toBe(0);
    expect(result.current.checkResult).toBeNull();
  });

  it('should call tenantApi.checkQuota with correct parameters', async () => {
    vi.mocked(tenantApi.checkQuota).mockResolvedValueOnce({ allowed: true });

    const { result } = renderHook(() => useTenantQuotaCheck());

    await act(async () => {
      await result.current.checkQuota(mockTenant);
    });

    expect(tenantApi.checkQuota).toHaveBeenCalledWith(1, { tokens: 0 });
  });

  it('should set checkResult on successful check', async () => {
    vi.mocked(tenantApi.checkQuota).mockResolvedValueOnce({ allowed: true, reason: undefined });

    const { result } = renderHook(() => useTenantQuotaCheck());

    await act(async () => {
      await result.current.checkQuota(mockTenant);
    });

    await waitFor(() => {
      expect(result.current.checkResult).not.toBeNull();
      expect(result.current.checkResult?.allowed).toBe(true);
      expect(result.current.checkResult?.tenant).toEqual(mockTenant);
      expect(result.current.checkResult?.checkedAt).toBeInstanceOf(Date);
    });
  });

  it('should set checkResult with reason when allowed is false', async () => {
    vi.mocked(tenantApi.checkQuota).mockResolvedValueOnce({
      allowed: false,
      reason: 'Daily token quota exceeded',
    });

    const { result } = renderHook(() => useTenantQuotaCheck());

    await act(async () => {
      await result.current.checkQuota(mockTenant);
    });

    await waitFor(() => {
      expect(result.current.checkResult?.allowed).toBe(false);
      expect(result.current.checkResult?.reason).toBe('Daily token quota exceeded');
    });
  });

  it('should prevent duplicate requests for the same tenant using functional state updates', async () => {
    vi.mocked(tenantApi.checkQuota).mockImplementation(
      () =>
        new Promise((resolve) => {
          setTimeout(() => resolve({ allowed: true }), 100);
        })
    );

    const { result } = renderHook(() => useTenantQuotaCheck());

    // Start first check
    act(() => {
      result.current.checkQuota(mockTenant);
    });

    // Try to check again immediately (should be prevented by functional state update)
    await act(async () => {
      await result.current.checkQuota(mockTenant);
    });

    // Should only be called once
    expect(tenantApi.checkQuota).toHaveBeenCalledTimes(1);
  });

  it('should add and remove tenant from checkingTenants during check', async () => {
    vi.mocked(tenantApi.checkQuota).mockImplementation(
      () =>
        new Promise((resolve) => {
          setTimeout(() => resolve({ allowed: true }), 50);
        })
    );

    const { result } = renderHook(() => useTenantQuotaCheck());

    // Start check
    act(() => {
      result.current.checkQuota(mockTenant);
    });

    // Check that tenant is added to checkingTenants
    expect(result.current.checkingTenants.has(1)).toBe(true);

    // Wait for check to complete
    await waitFor(() => {
      expect(result.current.checkingTenants.has(1)).toBe(false);
    });
  });

  it('should handle network error', async () => {
    const networkError = new Error('fetch failed');
    vi.mocked(tenantApi.checkQuota).mockRejectedValueOnce(networkError);

    const { result } = renderHook(() => useTenantQuotaCheck());

    await act(async () => {
      await result.current.checkQuota(mockTenant);
    });

    // Should not have result on error
    expect(result.current.checkResult).toBeNull();
    // Should remove tenant from checkingTenants
    expect(result.current.checkingTenants.has(1)).toBe(false);
  });

  it('should handle tenant not found error via status code', async () => {
    const notFoundError = { message: 'Tenant not found', status: 404 };
    vi.mocked(tenantApi.checkQuota).mockRejectedValueOnce(notFoundError);

    const { result } = renderHook(() => useTenantQuotaCheck());

    await act(async () => {
      await result.current.checkQuota(mockTenant);
    });

    expect(result.current.checkResult).toBeNull();
  });

  it('should handle tenant not found error via string matching', async () => {
    const notFoundError = { message: 'Tenant not found' };
    vi.mocked(tenantApi.checkQuota).mockRejectedValueOnce(notFoundError);

    const { result } = renderHook(() => useTenantQuotaCheck());

    await act(async () => {
      await result.current.checkQuota(mockTenant);
    });

    expect(result.current.checkResult).toBeNull();
  });

  it('should handle permission denied error (403)', async () => {
    const forbiddenError = { message: 'Forbidden', status: 403 };
    vi.mocked(tenantApi.checkQuota).mockRejectedValueOnce(forbiddenError);

    const { result } = renderHook(() => useTenantQuotaCheck());

    await act(async () => {
      await result.current.checkQuota(mockTenant);
    });

    expect(result.current.checkResult).toBeNull();
  });

  it('should clear result when clearResult is called', async () => {
    vi.mocked(tenantApi.checkQuota).mockResolvedValueOnce({ allowed: true });

    const { result } = renderHook(() => useTenantQuotaCheck());

    await act(async () => {
      await result.current.checkQuota(mockTenant);
    });

    await waitFor(() => {
      expect(result.current.checkResult).not.toBeNull();
    });

    act(() => {
      result.current.clearResult();
    });

    expect(result.current.checkResult).toBeNull();
  });

  it('should handle reason being undefined in API response', async () => {
    vi.mocked(tenantApi.checkQuota).mockResolvedValueOnce({ allowed: false, reason: undefined });

    const { result } = renderHook(() => useTenantQuotaCheck());

    await act(async () => {
      await result.current.checkQuota(mockTenant);
    });

    await waitFor(() => {
      expect(result.current.checkResult).not.toBeNull();
      expect(result.current.checkResult?.allowed).toBe(false);
      // reason should be undefined (API returned undefined, we preserve it)
      expect(result.current.checkResult?.reason).toBeUndefined();
    });
  });
});
