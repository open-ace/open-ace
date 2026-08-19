/**
 * Tests for PlatformAdminGuard component
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor } from '@/test/utils';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { useAuth, AuthResult } from '@/hooks';
import { PlatformAdminGuard } from './PlatformAdminGuard';
import { useAppStore } from '@/store';

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

describe('PlatformAdminGuard', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    // Reset store state
    useAppStore.setState({
      user: null,
      isAuthenticated: false,
      authLoading: false,
    });
  });

  afterEach(() => {
    vi.resetModules();
  });

  describe('platform_admin role', () => {
    it('should render children for platform_admin user', async () => {
      vi.mocked(useAuth).mockReturnValue(
        createMockAuthResult({
          id: '1',
          username: 'platform_admin',
          role: 'platform_admin',
          must_change_password: false,
        })
      );

      render(
        <MemoryRouter initialEntries={['/manage/tenants']}>
          <Routes>
            <Route
              path="/manage/tenants"
              element={
                <PlatformAdminGuard>
                  <div>Tenant Management</div>
                </PlatformAdminGuard>
              }
            />
            <Route path="/manage/dashboard" element={<div>Dashboard</div>} />
          </Routes>
        </MemoryRouter>
      );

      expect(screen.getByText('Tenant Management')).toBeInTheDocument();
    });
  });

  describe('admin role', () => {
    it('should render children for admin user', async () => {
      vi.mocked(useAuth).mockReturnValue(
        createMockAuthResult({
          id: '1',
          username: 'admin',
          role: 'admin',
          must_change_password: false,
        })
      );

      render(
        <MemoryRouter initialEntries={['/manage/tenants']}>
          <Routes>
            <Route
              path="/manage/tenants"
              element={
                <PlatformAdminGuard>
                  <div>Tenant Management</div>
                </PlatformAdminGuard>
              }
            />
            <Route path="/manage/dashboard" element={<div>Dashboard</div>} />
          </Routes>
        </MemoryRouter>
      );

      expect(screen.getByText('Tenant Management')).toBeInTheDocument();
    });
  });

  describe('tenant_admin role', () => {
    it('should redirect to dashboard for tenant_admin user', async () => {
      vi.mocked(useAuth).mockReturnValue(
        createMockAuthResult({
          id: '1',
          username: 'tenant_admin',
          role: 'tenant_admin',
          must_change_password: false,
        })
      );

      render(
        <MemoryRouter initialEntries={['/manage/tenants']}>
          <Routes>
            <Route
              path="/manage/tenants"
              element={
                <PlatformAdminGuard>
                  <div>Tenant Management</div>
                </PlatformAdminGuard>
              }
            />
            <Route path="/manage/dashboard" element={<div>Dashboard</div>} />
          </Routes>
        </MemoryRouter>
      );

      // Should redirect to dashboard
      await waitFor(() => {
        expect(screen.getByText('Dashboard')).toBeInTheDocument();
      });
    });
  });

  describe('manager role', () => {
    it('should redirect to dashboard for manager user', async () => {
      vi.mocked(useAuth).mockReturnValue(
        createMockAuthResult({
          id: '1',
          username: 'manager',
          role: 'manager',
          must_change_password: false,
        })
      );

      render(
        <MemoryRouter initialEntries={['/manage/tenants']}>
          <Routes>
            <Route
              path="/manage/tenants"
              element={
                <PlatformAdminGuard>
                  <div>Tenant Management</div>
                </PlatformAdminGuard>
              }
            />
            <Route path="/manage/dashboard" element={<div>Dashboard</div>} />
          </Routes>
        </MemoryRouter>
      );

      // Should redirect to dashboard
      await waitFor(() => {
        expect(screen.getByText('Dashboard')).toBeInTheDocument();
      });
    });
  });

  describe('regular user role', () => {
    it('should redirect to dashboard for regular user', async () => {
      vi.mocked(useAuth).mockReturnValue(
        createMockAuthResult({
          id: '1',
          username: 'user',
          role: 'user',
          must_change_password: false,
        })
      );

      render(
        <MemoryRouter initialEntries={['/manage/tenants']}>
          <Routes>
            <Route
              path="/manage/tenants"
              element={
                <PlatformAdminGuard>
                  <div>Tenant Management</div>
                </PlatformAdminGuard>
              }
            />
            <Route path="/manage/dashboard" element={<div>Dashboard</div>} />
          </Routes>
        </MemoryRouter>
      );

      // Should redirect to dashboard
      await waitFor(() => {
        expect(screen.getByText('Dashboard')).toBeInTheDocument();
      });
    });
  });

  describe('unauthenticated user', () => {
    it('should render nothing for unauthenticated user', async () => {
      vi.mocked(useAuth).mockReturnValue(createMockAuthResult(null));

      const { container } = render(
        <MemoryRouter initialEntries={['/manage/tenants']}>
          <Routes>
            <Route
              path="/manage/tenants"
              element={
                <PlatformAdminGuard>
                  <div>Tenant Management</div>
                </PlatformAdminGuard>
              }
            />
            <Route path="/manage/dashboard" element={<div>Dashboard</div>} />
          </Routes>
        </MemoryRouter>
      );

      // Should render nothing (null)
      expect(container.firstChild).toBeNull();
    });
  });
});
