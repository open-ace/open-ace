/**
 * useTenantQuotaCheck Hook Tests
 *
 * Issue #3132: Tenant management page quota check functionality
 */

import { renderHook, act, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
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

  it('should have initial state with empty checkingTenants and null checkResult', () => {
    const { result } = renderHook(() => useTenantQuotaCheck());

    expect(result.current.checkingTenants).toBeInstanceOf(Set);
    expect(result.current.checkingTenants.size).toBe(0);
    expect(result.current.checkResult).toBeNull();
  });

  it('should call tenantApi.checkQuota with correct parameters', async () => {
    const mockResult = { allowed: true };
    vi.mocked(tenantApi.checkQuota).mockResolvedValueOnce(mockResult);

    const { result } = renderHook(() => useTenantQuotaCheck());

    await act(async () => {
      await result.current.checkQuota(mockTenant);
    });

    expect(tenantApi.checkQuota).toHaveBeenCalledWith(1, { tokens: 0 });
  });

  it('should set checkResult on successful check', async () => {
    const mockResult = { allowed: true, reason: undefined };
    vi.mocked(tenantApi.checkQuota).mockResolvedValueOnce(mockResult);

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
    const mockResult = { allowed: false, reason: 'Daily token quota exceeded' };
    vi.mocked(tenantApi.checkQuota).mockResolvedValueOnce(mockResult);

    const { result } = renderHook(() => useTenantQuotaCheck());

    await act(async () => {
      await result.current.checkQuota(mockTenant);
    });

    await waitFor(() => {
      expect(result.current.checkResult?.allowed).toBe(false);
      expect(result.current.checkResult?.reason).toBe('Daily token quota exceeded');
    });
  });

  it('should prevent duplicate requests for the same tenant', async () => {
    const mockResult = { allowed: true };
    vi.mocked(tenantApi.checkQuota).mockImplementation(
      () =>
        new Promise((resolve) => {
          setTimeout(() => resolve(mockResult), 100);
        })
    );

    const { result } = renderHook(() => useTenantQuotaCheck());

    // Start first check
    act(() => {
      result.current.checkQuota(mockTenant);
    });

    // Try to check again immediately
    await act(async () => {
      await result.current.checkQuota(mockTenant);
    });

    // Should only be called once
    expect(tenantApi.checkQuota).toHaveBeenCalledTimes(1);
  });

  it('should add and remove tenant from checkingTenants during check', async () => {
    const mockResult = { allowed: true };
    vi.mocked(tenantApi.checkQuota).mockImplementation(
      () =>
        new Promise((resolve) => {
          setTimeout(() => resolve(mockResult), 50);
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

  it('should handle tenant not found error', async () => {
    const notFoundError = { message: 'Tenant not found' };
    vi.mocked(tenantApi.checkQuota).mockRejectedValueOnce(notFoundError);

    const { result } = renderHook(() => useTenantQuotaCheck());

    await act(async () => {
      await result.current.checkQuota(mockTenant);
    });

    expect(result.current.checkResult).toBeNull();
  });

  it('should clear result when clearResult is called', async () => {
    const mockResult = { allowed: true };
    vi.mocked(tenantApi.checkQuota).mockResolvedValueOnce(mockResult);

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

  it('should handle multiple tenants independently', async () => {
    const mockTenant2: Tenant = { ...mockTenant, id: 2, name: 'Test Tenant 2' };

    const mockResult1 = { allowed: true };
    const mockResult2 = { allowed: false, reason: 'Quota exceeded' };

    vi.mocked(tenantApi.checkQuota)
      .mockImplementationOnce(
        () => new Promise((resolve) => setTimeout(() => resolve(mockResult1), 100))
      )
      .mockImplementationOnce(
        () => new Promise((resolve) => setTimeout(() => resolve(mockResult2), 50))
      );

    const { result } = renderHook(() => useTenantQuotaCheck());

    // Start both checks
    act(() => {
      result.current.checkQuota(mockTenant);
      result.current.checkQuota(mockTenant2);
    });

    // Both should be in checkingTenants
    expect(result.current.checkingTenants.has(1)).toBe(true);
    expect(result.current.checkingTenants.has(2)).toBe(true);

    // Wait for both to complete
    await waitFor(() => {
      expect(result.current.checkingTenants.size).toBe(0);
    });
  });
});
