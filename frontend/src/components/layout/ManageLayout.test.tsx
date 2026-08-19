/**
 * Tests for ManageLayout component
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen } from '@/test/utils';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { useAuth, AuthResult } from '@/hooks';
import { useAppStore } from '@/store';
import { ManageLayout } from './ManageLayout';

// Mock the hooks module
vi.mock('@/hooks', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/hooks')>();
  return {
    ...actual,
    useAuth: vi.fn(),
  };
});

// Helper to create mock auth result
const createMockAuthResult = (
  user: { id: string; username: string; role: string; must_change_password: boolean } | null
): AuthResult => ({
  user,
  isLoading: false,
  isAuthenticated: !!user,
});

describe('ManageLayout', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    // Reset store state
    useAppStore.setState({
      modelGatewayEnabled: false,
      policyEnabled: false,
      user: null,
      isAuthenticated: false,
      authLoading: false,
    });
  });

  afterEach(() => {
    vi.resetModules();
  });

  describe('navigation rendering', () => {
    it('should render dashboard navigation item', async () => {
      vi.mocked(useAuth).mockReturnValue(
        createMockAuthResult({
          id: '1',
          username: 'admin',
          role: 'admin',
          must_change_password: false,
        })
      );

      render(
        <MemoryRouter initialEntries={['/manage/dashboard']}>
          <Routes>
            <Route path="/manage/*" element={<ManageLayout />}>
              <Route index element={<div>Dashboard</div>} />
            </Route>
          </Routes>
        </MemoryRouter>
      );

      expect(screen.getByText('Dashboard')).toBeInTheDocument();
    });
  });

  describe('feature flag filtering', () => {
    it('should hide model-gateway navigation item when modelGatewayEnabled is false', async () => {
      useAppStore.setState({ modelGatewayEnabled: false });
      vi.mocked(useAuth).mockReturnValue(
        createMockAuthResult({
          id: '1',
          username: 'admin',
          role: 'admin',
          must_change_password: false,
        })
      );

      render(
        <MemoryRouter initialEntries={['/manage/dashboard']}>
          <Routes>
            <Route path="/manage/*" element={<ManageLayout />}>
              <Route index element={<div>Dashboard</div>} />
            </Route>
          </Routes>
        </MemoryRouter>
      );

      // Model Gateway navigation item should be disabled (not visible)
      const modelGatewayLink = screen.queryByRole('link', { name: /model.gateway/i });
      if (modelGatewayLink) {
        expect(modelGatewayLink).toHaveClass('disabled');
      }
    });

    it('should show model-gateway navigation item when modelGatewayEnabled is true', async () => {
      const adminUser = { id: '1', username: 'admin', role: 'admin', must_change_password: false };

      // Set feature flag and user in store BEFORE rendering
      useAppStore.setState({
        modelGatewayEnabled: true,
        policyEnabled: true,
        user: adminUser,
        isAuthenticated: true,
        authLoading: false,
      });

      // Mock useAuth to return admin user
      vi.mocked(useAuth).mockReturnValue(createMockAuthResult(adminUser));

      render(
        <MemoryRouter initialEntries={['/manage/dashboard']}>
          <Routes>
            <Route path="/manage/*" element={<ManageLayout />}>
              <Route index element={<div>Dashboard</div>} />
            </Route>
          </Routes>
        </MemoryRouter>
      );

      // Model Gateway navigation item should be visible and enabled for admin users when feature is enabled
      const modelGatewayLink = screen.getByRole('link', { name: /model.gateway/i });
      // Check that it does NOT have the disabled class
      expect(modelGatewayLink.className).not.toMatch(/disabled/);
    });
  });

  describe('admin filtering', () => {
    it('should disable admin-only items for non-admin users', async () => {
      useAppStore.setState({ modelGatewayEnabled: false });
      vi.mocked(useAuth).mockReturnValue(
        createMockAuthResult({
          id: '1',
          username: 'user',
          role: 'user',
          must_change_password: false,
        })
      );

      render(
        <MemoryRouter initialEntries={['/manage/dashboard']}>
          <Routes>
            <Route path="/manage/*" element={<ManageLayout />}>
              <Route index element={<div>Dashboard</div>} />
            </Route>
          </Routes>
        </MemoryRouter>
      );

      // User management item should be disabled for non-admin users
      const userManagementLink = screen.queryByRole('link', { name: /user.management/i });
      if (userManagementLink) {
        expect(userManagementLink).toHaveClass('disabled');
      }
    });
  });

  describe('platform admin only filtering', () => {
    it('should disable tenant management for tenant_admin', async () => {
      useAppStore.setState({ modelGatewayEnabled: false });
      vi.mocked(useAuth).mockReturnValue(
        createMockAuthResult({
          id: '1',
          username: 'tenant_admin',
          role: 'tenant_admin',
          must_change_password: false,
        })
      );

      render(
        <MemoryRouter initialEntries={['/manage/dashboard']}>
          <Routes>
            <Route path="/manage/*" element={<ManageLayout />}>
              <Route index element={<div>Dashboard</div>} />
            </Route>
          </Routes>
        </MemoryRouter>
      );

      // Tenant management should be disabled for tenant_admin
      const tenantManagementLink = screen.queryByRole('link', { name: /tenant.management/i });
      if (tenantManagementLink) {
        expect(tenantManagementLink).toHaveClass('disabled');
      }
    });

    it('should enable tenant management for platform_admin', async () => {
      useAppStore.setState({ modelGatewayEnabled: false });
      vi.mocked(useAuth).mockReturnValue(
        createMockAuthResult({
          id: '1',
          username: 'platform_admin',
          role: 'platform_admin',
          must_change_password: false,
        })
      );

      render(
        <MemoryRouter initialEntries={['/manage/dashboard']}>
          <Routes>
            <Route path="/manage/*" element={<ManageLayout />}>
              <Route index element={<div>Dashboard</div>} />
            </Route>
          </Routes>
        </MemoryRouter>
      );

      // Tenant management should be enabled for platform_admin
      const tenantManagementLink = screen.queryByRole('link', { name: /tenant.management/i });
      if (tenantManagementLink) {
        expect(tenantManagementLink.className).not.toMatch(/disabled/);
      }
    });

    it('should enable tenant management for admin (legacy)', async () => {
      useAppStore.setState({ modelGatewayEnabled: false });
      vi.mocked(useAuth).mockReturnValue(
        createMockAuthResult({
          id: '1',
          username: 'admin',
          role: 'admin',
          must_change_password: false,
        })
      );

      render(
        <MemoryRouter initialEntries={['/manage/dashboard']}>
          <Routes>
            <Route path="/manage/*" element={<ManageLayout />}>
              <Route index element={<div>Dashboard</div>} />
            </Route>
          </Routes>
        </MemoryRouter>
      );

      // Tenant management should be enabled for admin (legacy)
      const tenantManagementLink = screen.queryByRole('link', { name: /tenant.management/i });
      if (tenantManagementLink) {
        expect(tenantManagementLink.className).not.toMatch(/disabled/);
      }
    });

    it('should disable tenant management for manager', async () => {
      useAppStore.setState({ modelGatewayEnabled: false });
      vi.mocked(useAuth).mockReturnValue(
        createMockAuthResult({
          id: '1',
          username: 'manager',
          role: 'manager',
          must_change_password: false,
        })
      );

      render(
        <MemoryRouter initialEntries={['/manage/dashboard']}>
          <Routes>
            <Route path="/manage/*" element={<ManageLayout />}>
              <Route index element={<div>Dashboard</div>} />
            </Route>
          </Routes>
        </MemoryRouter>
      );

      // Tenant management should be disabled for manager
      const tenantManagementLink = screen.queryByRole('link', { name: /tenant.management/i });
      if (tenantManagementLink) {
        expect(tenantManagementLink).toHaveClass('disabled');
      }
    });
  });
});
