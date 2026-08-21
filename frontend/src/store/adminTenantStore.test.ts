/**
 * Unit tests for Admin Tenant Store
 *
 * Issue #2841: Platform admin tenant selector default behavior optimization
 */

import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest';
import { useAdminTenantStore } from './adminTenantStore';
import type { User } from '@/types';
import type { Tenant } from '@/api';

// Mock dependencies
vi.mock('@/utils/permissions', () => ({
  canManageAllTenants: vi.fn((user: User | null) => {
    return user?.role === 'platform_admin' || user?.role === 'admin';
  }),
}));

vi.mock('@/api', () => ({
  tenantApi: {
    listTenants: vi.fn(),
  },
}));

vi.mock('@/utils/adminTenantStorage', () => ({
  loadSelection: vi.fn(),
  saveSelection: vi.fn(() => true),
  clearSelection: vi.fn(),
  clearOtherUsersData: vi.fn(),
  isTenantAccessible: vi.fn((tenantId: number | null, tenants: Tenant[]) => {
    if (tenantId === null) return false;
    const tenant = tenants.find((t) => t.id === tenantId);
    return tenant?.status === 'active';
  }),
  findDefaultTenant: vi.fn((tenants: Tenant[]) => {
    return tenants.find((t) => t.slug === 'default' && t.status === 'active') ?? null;
  }),
}));

// Mock useAppStore - need to return a function that works with getState() calls
const mockUser: User = {
  id: '123',
  username: 'testuser',
  email: 'test@example.com',
  role: 'platform_admin',
  tenant_id: 1,
  createdAt: '2026-01-01',
};

let currentUser: User | null = mockUser;

const mockAppStore = vi.fn(() => ({
  user: currentUser,
}));

// Add getState method to the mock
(mockAppStore as any).getState = () => ({ user: currentUser });

vi.mock('@/store', () => ({
  useAppStore: mockAppStore,
}));

// Import mocked modules
import { tenantApi } from '@/api';
import * as storage from '@/utils/adminTenantStorage';

const mockTenantApi = vi.mocked(tenantApi);
const mockStorage = vi.mocked(storage);

// Helper to create tenant
const createTenant = (id: number, slug: string, status: 'active' | 'suspended' = 'active'): Tenant => ({
  id,
  name: `Tenant ${id}`,
  slug,
  status,
  plan: 'standard',
  created_at: '2026-01-01',
});

describe('AdminTenantStore', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    // Reset store state
    useAdminTenantStore.setState({
      tenants: [],
      selectedTenantId: null,
      isLoading: false,
      error: null,
      abortController: null,
      isInitialized: false,
    });
    currentUser = mockUser;
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  describe('initialize', () => {
    it('should not initialize for non-platform-admin users', async () => {
      currentUser = { ...mockUser, role: 'user' };
      const { initialize } = useAdminTenantStore.getState();

      await initialize(currentUser);

      const state = useAdminTenantStore.getState();
      expect(state.tenants).toEqual([]);
      expect(state.isInitialized).toBe(false);
    });

    it('should load tenant list for platform-admin users', async () => {
      const tenants = [createTenant(1, 'default'), createTenant(2, 'tenant-2')];
      mockTenantApi.listTenants.mockResolvedValueOnce({ tenants, count: 2 });
      mockStorage.loadSelection.mockReturnValue(null);

      const { initialize } = useAdminTenantStore.getState();
      await initialize(currentUser);

      const state = useAdminTenantStore.getState();
      expect(state.tenants).toEqual(tenants);
      expect(state.isLoading).toBe(false);
      expect(state.error).toBeNull();
    });

    it('should restore previous selection from localStorage', async () => {
      const tenants = [createTenant(1, 'default'), createTenant(2, 'tenant-2')];
      mockTenantApi.listTenants.mockResolvedValueOnce({ tenants, count: 2 });
      mockStorage.loadSelection.mockReturnValue(2);
      mockStorage.isTenantAccessible.mockReturnValue(true);

      const { initialize } = useAdminTenantStore.getState();
      await initialize(currentUser);

      const state = useAdminTenantStore.getState();
      expect(state.selectedTenantId).toBe(2);
    });

    it('should select default tenant when no previous selection', async () => {
      const tenants = [createTenant(2, 'tenant-2'), createTenant(1, 'default')];
      mockTenantApi.listTenants.mockResolvedValueOnce({ tenants, count: 2 });
      mockStorage.loadSelection.mockReturnValue(null);
      mockStorage.findDefaultTenant.mockReturnValue(tenants[1]);

      const { initialize } = useAdminTenantStore.getState();
      await initialize(currentUser);

      const state = useAdminTenantStore.getState();
      expect(state.selectedTenantId).toBe(1);
    });

    it('should keep null when no default tenant exists', async () => {
      const tenants = [createTenant(2, 'tenant-2')];
      mockTenantApi.listTenants.mockResolvedValueOnce({ tenants, count: 1 });
      mockStorage.loadSelection.mockReturnValue(null);
      mockStorage.findDefaultTenant.mockReturnValue(null);

      const { initialize } = useAdminTenantStore.getState();
      await initialize(currentUser);

      const state = useAdminTenantStore.getState();
      expect(state.selectedTenantId).toBeNull();
    });

    it('should set error state on load failure', async () => {
      mockTenantApi.listTenants.mockRejectedValueOnce(new Error('Network error'));

      const { initialize } = useAdminTenantStore.getState();
      await initialize(currentUser);

      const state = useAdminTenantStore.getState();
      expect(state.error).toBe('加载租户列表失败，请检查网络连接后重试');
      expect(state.isLoading).toBe(false);
    });

    it('should call clearOtherUsersData on first login', async () => {
      const tenants = [createTenant(1, 'default')];
      mockTenantApi.listTenants.mockResolvedValueOnce({ tenants, count: 1 });
      mockStorage.loadSelection.mockReturnValue(null);

      const { initialize } = useAdminTenantStore.getState();
      await initialize(currentUser);

      expect(mockStorage.clearOtherUsersData).toHaveBeenCalledWith('123');
    });
  });

  describe('selectTenant', () => {
    it('should update state and localStorage', async () => {
      // Setup: Load tenants first
      const tenants = [createTenant(1, 'default'), createTenant(2, 'tenant-2')];
      mockTenantApi.listTenants.mockResolvedValueOnce({ tenants, count: 2 });
      mockStorage.loadSelection.mockReturnValue(null);

      const { initialize, selectTenant } = useAdminTenantStore.getState();
      await initialize(currentUser);

      // Select tenant
      selectTenant(2);

      const state = useAdminTenantStore.getState();
      expect(state.selectedTenantId).toBe(2);
      expect(mockStorage.saveSelection).toHaveBeenCalledWith('123', 2);
    });

    it('should not select inactive tenant', async () => {
      const tenants = [createTenant(1, 'default'), createTenant(2, 'tenant-2', 'suspended')];
      mockTenantApi.listTenants.mockResolvedValueOnce({ tenants, count: 2 });
      mockStorage.loadSelection.mockReturnValue(null);

      const { initialize, selectTenant } = useAdminTenantStore.getState();
      await initialize(currentUser);

      // Try to select inactive tenant
      selectTenant(2);

      const state = useAdminTenantStore.getState();
      // Should not change from default selection
      expect(state.selectedTenantId).not.toBe(2);
    });
  });

  describe('clearSelection', () => {
    it('should clear state and localStorage', async () => {
      const tenants = [createTenant(1, 'default')];
      mockTenantApi.listTenants.mockResolvedValueOnce({ tenants, count: 1 });
      mockStorage.loadSelection.mockReturnValue(null);

      const { initialize, clearSelection } = useAdminTenantStore.getState();
      await initialize(currentUser);

      clearSelection();

      const state = useAdminTenantStore.getState();
      expect(state.selectedTenantId).toBeNull();
      expect(mockStorage.clearSelection).toHaveBeenCalledWith('123');
    });
  });

  describe('retry', () => {
    it('should re-initialize after failure', async () => {
      // First call fails
      mockTenantApi.listTenants.mockRejectedValueOnce(new Error('Network error'));

      const { initialize, retry } = useAdminTenantStore.getState();
      await initialize(currentUser);

      const errorState = useAdminTenantStore.getState();
      expect(errorState.error).toBeTruthy();

      // Second call succeeds
      const tenants = [createTenant(1, 'default')];
      mockTenantApi.listTenants.mockResolvedValueOnce({ tenants, count: 1 });
      mockStorage.loadSelection.mockReturnValue(null);

      await retry();

      const successState = useAdminTenantStore.getState();
      expect(successState.error).toBeNull();
      expect(successState.tenants).toEqual(tenants);
    });
  });

  describe('reset', () => {
    it('should reset store to initial state', async () => {
      const tenants = [createTenant(1, 'default')];
      mockTenantApi.listTenants.mockResolvedValueOnce({ tenants, count: 1 });
      mockStorage.loadSelection.mockReturnValue(null);

      const { initialize, reset } = useAdminTenantStore.getState();
      await initialize(currentUser);

      reset();

      const state = useAdminTenantStore.getState();
      expect(state.tenants).toEqual([]);
      expect(state.selectedTenantId).toBeNull();
      expect(state.isLoading).toBe(false);
      expect(state.error).toBeNull();
      expect(state.isInitialized).toBe(false);
    });
  });
});