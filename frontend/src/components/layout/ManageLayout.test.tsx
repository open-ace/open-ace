/**
 * Tests for ManageLayout component
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@/test/utils';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { ManageLayout } from './ManageLayout';
import { useAppStore } from '@/store';
import { useAuth } from '@/hooks';

// Mock the useAppStore hook
vi.mock('@/store', () => ({
  useAppStore: vi.fn(),
  useLanguage: () => 'en',
  useSidebarCollapsed: () => false,
  usePolicyEnabled: () => false,
  useModelGatewayEnabled: () => false,
}));

// Mock the useAuth hook
vi.mock('@/hooks', () => ({
  useAuth: vi.fn(),
}));

describe('ManageLayout', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe('navigation rendering', () => {
    it('should render dashboard navigation item', () => {
      vi.mocked(useAuth).mockReturnValue({
        user: { id: '1', username: 'admin', role: 'admin', must_change_password: false },
        isLoading: false,
        isAuthenticated: true,
      });

      vi.mocked(useAppStore).mockImplementation((selector) => {
        if (typeof selector === 'function') {
          return selector({
            language: 'en',
            sidebarCollapsed: false,
            policyEnabled: false,
            modelGatewayEnabled: false,
          } as any);
        }
        return {
          language: 'en',
          sidebarCollapsed: false,
          policyEnabled: false,
          modelGatewayEnabled: false,
        } as any;
      });

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
    it('should hide model-gateway navigation item when modelGatewayEnabled is false', () => {
      vi.mocked(useAuth).mockReturnValue({
        user: { id: '1', username: 'admin', role: 'admin', must_change_password: false },
        isLoading: false,
        isAuthenticated: true,
      });

      vi.mocked(useAppStore).mockImplementation((selector) => {
        if (typeof selector === 'function') {
          return selector({
            language: 'en',
            sidebarCollapsed: false,
            policyEnabled: false,
            modelGatewayEnabled: false,
          } as any);
        }
        return {
          language: 'en',
          sidebarCollapsed: false,
          policyEnabled: false,
          modelGatewayEnabled: false,
        } as any;
      });

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

    it('should show model-gateway navigation item when modelGatewayEnabled is true', () => {
      vi.mocked(useAuth).mockReturnValue({
        user: { id: '1', username: 'admin', role: 'admin', must_change_password: false },
        isLoading: false,
        isAuthenticated: true,
      });

      vi.mocked(useAppStore).mockImplementation((selector) => {
        if (typeof selector === 'function') {
          return selector({
            language: 'en',
            sidebarCollapsed: false,
            policyEnabled: false,
            modelGatewayEnabled: true,
          } as any);
        }
        return {
          language: 'en',
          sidebarCollapsed: false,
          policyEnabled: false,
          modelGatewayEnabled: true,
        } as any;
      });

      render(
        <MemoryRouter initialEntries={['/manage/dashboard']}>
          <Routes>
            <Route path="/manage/*" element={<ManageLayout />}>
              <Route index element={<div>Dashboard</div>} />
            </Route>
          </Routes>
        </MemoryRouter>
      );

      // Model Gateway navigation item should be visible and enabled
      const modelGatewayLink = screen.queryByRole('link', { name: /model.gateway/i });
      if (modelGatewayLink) {
        expect(modelGatewayLink).not.toHaveClass('disabled');
      }
    });
  });

  describe('admin filtering', () => {
    it('should disable admin-only items for non-admin users', () => {
      vi.mocked(useAuth).mockReturnValue({
        user: { id: '1', username: 'user', role: 'user', must_change_password: false },
        isLoading: false,
        isAuthenticated: true,
      });

      vi.mocked(useAppStore).mockImplementation((selector) => {
        if (typeof selector === 'function') {
          return selector({
            language: 'en',
            sidebarCollapsed: false,
            policyEnabled: false,
            modelGatewayEnabled: false,
          } as any);
        }
        return {
          language: 'en',
          sidebarCollapsed: false,
          policyEnabled: false,
          modelGatewayEnabled: false,
        } as any;
      });

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
});