/**
 * Unit tests for Admin Tenant Store
 *
 * Issue #2841: Platform admin tenant selector default behavior optimization
 */

import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest';
import type { User } from '@/types';
import type { Tenant } from '@/api';

// Global user state for mock
let mockCurrentUser: User | null = null;

// Helper to set mock user
const setMockUser = (user: User | null) => {
  mockCurrentUser = user;
};

// Mock dependencies - all mocks must be defined before any imports
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

// Mock useAppStore - provide getState that uses global mockCurrentUser
vi.mock('@/store', () => {
  const mockAppStore = vi.fn(() => ({
    user: mockCurrentUser,
  }));
  // getState returns current mock user state
  (mockAppStore as any).getState = () => ({ user: mockCurrentUser });
  return {
    useAppStore: mockAppStore,
  };
});

// Import after mocks are defined
import { useAdminTenantStore } from './adminTenantStore';
import { tenantApi } from '@/api';
import * as storage from '@/utils/adminTenantStorage';

const mockTenantApi = vi.mocked(tenantApi);
const mockStorage = vi.mocked(storage);

// Helper to create test user
const createTestUser = (role: User['role'] = 'platform_admin'): User => ({
  id: '123',
  username: 'testuser',
  email: 'test@example.com',
  role,
  tenant_id: 1,
  createdAt: '2026-01-01',
});

// Helper to create tenant
const createTenant = (
  id: number,
  slug: string,
  status: 'active' | 'suspended' = 'active'
): Tenant => ({
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
    // Reset mock user to platform admin by default
    setMockUser(createTestUser());
  });

  afterEach(() => {
    vi.restoreAllMocks();
    setMockUser(null);
  });

  describe('initialize', () => {
    it('should not initialize for non-platform-admin users', async () => {
      const user = createTestUser('user');
      const { initialize } = useAdminTenantStore.getState();

      await initialize(user);

      const state = useAdminTenantStore.getState();
      expect(state.tenants).toEqual([]);
      expect(state.isInitialized).toBe(false);
    });

    it('should load tenant list for platform-admin users', async () => {
      const user = createTestUser();
      const tenants = [createTenant(1, 'default'), createTenant(2, 'tenant-2')];
      mockTenantApi.listTenants.mockResolvedValueOnce({ tenants, count: 2 });
      mockStorage.loadSelection.mockReturnValue(null);

      const { initialize } = useAdminTenantStore.getState();
      await initialize(user);

      const state = useAdminTenantStore.getState();
      expect(state.tenants).toEqual(tenants);
      expect(state.isLoading).toBe(false);
      expect(state.error).toBeNull();
    });

    it('should restore previous selection from localStorage', async () => {
      const user = createTestUser();
      const tenants = [createTenant(1, 'default'), createTenant(2, 'tenant-2')];
      mockTenantApi.listTenants.mockResolvedValueOnce({ tenants, count: 2 });
      mockStorage.loadSelection.mockReturnValue(2);
      mockStorage.isTenantAccessible.mockReturnValue(true);

      const { initialize } = useAdminTenantStore.getState();
      await initialize(user);

      const state = useAdminTenantStore.getState();
      expect(state.selectedTenantId).toBe(2);
    });

    it('should select default tenant when no previous selection', async () => {
      const user = createTestUser();
      const tenants = [createTenant(2, 'tenant-2'), createTenant(1, 'default')];
      mockTenantApi.listTenants.mockResolvedValueOnce({ tenants, count: 2 });
      mockStorage.loadSelection.mockReturnValue(null);
      mockStorage.findDefaultTenant.mockReturnValue(tenants[1]);

      const { initialize } = useAdminTenantStore.getState();
      await initialize(user);

      const state = useAdminTenantStore.getState();
      expect(state.selectedTenantId).toBe(1);
    });

    it('should keep null when no default tenant exists', async () => {
      const user = createTestUser();
      const tenants = [createTenant(2, 'tenant-2')];
      mockTenantApi.listTenants.mockResolvedValueOnce({ tenants, count: 1 });
      mockStorage.loadSelection.mockReturnValue(null);
      mockStorage.findDefaultTenant.mockReturnValue(null);

      const { initialize } = useAdminTenantStore.getState();
      await initialize(user);

      const state = useAdminTenantStore.getState();
      expect(state.selectedTenantId).toBeNull();
    });

    it('should set error state on load failure', async () => {
      const user = createTestUser();
      mockTenantApi.listTenants.mockRejectedValueOnce(new Error('Network error'));

      const { initialize } = useAdminTenantStore.getState();
      await initialize(user);

      const state = useAdminTenantStore.getState();
      expect(state.error).toBe('加载租户列表失败，请检查网络连接后重试');
      expect(state.isLoading).toBe(false);
    });

    it('should call clearOtherUsersData on first login', async () => {
      const user = createTestUser();
      const tenants = [createTenant(1, 'default')];
      mockTenantApi.listTenants.mockResolvedValueOnce({ tenants, count: 1 });
      mockStorage.loadSelection.mockReturnValue(null);

      const { initialize } = useAdminTenantStore.getState();
      await initialize(user);

      expect(mockStorage.clearOtherUsersData).toHaveBeenCalledWith('123');
    });
  });

  describe('selectTenant', () => {
    it('should update state and localStorage', async () => {
      const user = createTestUser();
      // Setup: Load tenants first
      const tenants = [createTenant(1, 'default'), createTenant(2, 'tenant-2')];
      mockTenantApi.listTenants.mockResolvedValueOnce({ tenants, count: 2 });
      mockStorage.loadSelection.mockReturnValue(null);

      const { initialize, selectTenant } = useAdminTenantStore.getState();
      await initialize(user);

      // Select tenant
      selectTenant(2);

      const state = useAdminTenantStore.getState();
      expect(state.selectedTenantId).toBe(2);
      expect(mockStorage.saveSelection).toHaveBeenCalledWith('123', 2);
    });

    it('should not select inactive tenant', async () => {
      const user = createTestUser();
      const tenants = [createTenant(1, 'default'), createTenant(2, 'tenant-2', 'suspended')];
      mockTenantApi.listTenants.mockResolvedValueOnce({ tenants, count: 2 });
      mockStorage.loadSelection.mockReturnValue(null);

      const { initialize, selectTenant } = useAdminTenantStore.getState();
      await initialize(user);

      // Try to select inactive tenant
      selectTenant(2);

      const state = useAdminTenantStore.getState();
      // Should not change from default selection
      expect(state.selectedTenantId).not.toBe(2);
    });
  });

  describe('clearSelection', () => {
    it('should clear state and localStorage', async () => {
      const user = createTestUser();
      const tenants = [createTenant(1, 'default')];
      mockTenantApi.listTenants.mockResolvedValueOnce({ tenants, count: 1 });
      mockStorage.loadSelection.mockReturnValue(null);

      const { initialize, clearSelection } = useAdminTenantStore.getState();
      await initialize(user);

      clearSelection();

      const state = useAdminTenantStore.getState();
      expect(state.selectedTenantId).toBeNull();
      expect(mockStorage.clearSelection).toHaveBeenCalledWith('123');
    });
  });

  describe('retry', () => {
    it('should re-initialize after failure', async () => {
      const user = createTestUser();
      // First call fails
      mockTenantApi.listTenants.mockRejectedValueOnce(new Error('Network error'));

      const { initialize, retry } = useAdminTenantStore.getState();
      await initialize(user);

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
      const user = createTestUser();
      const tenants = [createTenant(1, 'default')];
      mockTenantApi.listTenants.mockResolvedValueOnce({ tenants, count: 1 });
      mockStorage.loadSelection.mockReturnValue(null);

      const { initialize, reset } = useAdminTenantStore.getState();
      await initialize(user);

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
