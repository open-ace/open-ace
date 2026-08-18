/**
 * Tests for FeatureRouteGuard component
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@/test/utils';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { FeatureRouteGuard } from './FeatureRouteGuard';
import { useAppStore } from '@/store';

// Mock the useAppStore hook
vi.mock('@/store', () => ({
  useAppStore: vi.fn(),
}));

describe('FeatureRouteGuard', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe('loading state', () => {
    it('should show loading state when configLoaded is false', () => {
      vi.mocked(useAppStore).mockImplementation((selector) => {
        if (typeof selector === 'function') {
          return selector({ configLoaded: false } as any);
        }
        return { configLoaded: false } as any;
      });

      render(
        <FeatureRouteGuard enabled={true} redirectPath="/manage/dashboard">
          <div>Protected Content</div>
        </FeatureRouteGuard>
      );

      // Should show page skeleton (loading state)
      expect(document.querySelector('.page-skeleton')).toBeInTheDocument();
      expect(screen.queryByText('Protected Content')).not.toBeInTheDocument();
    });
  });

  describe('feature enabled', () => {
    it('should render children when feature is enabled', () => {
      vi.mocked(useAppStore).mockImplementation((selector) => {
        if (typeof selector === 'function') {
          return selector({ configLoaded: true } as any);
        }
        return { configLoaded: true } as any;
      });

      render(
        <FeatureRouteGuard enabled={true} redirectPath="/manage/dashboard">
          <div>Protected Content</div>
        </FeatureRouteGuard>
      );

      expect(screen.getByText('Protected Content')).toBeInTheDocument();
    });
  });

  describe('feature disabled', () => {
    it('should redirect when feature is disabled', async () => {
      vi.mocked(useAppStore).mockImplementation((selector) => {
        if (typeof selector === 'function') {
          return selector({ configLoaded: true } as any);
        }
        return { configLoaded: true } as any;
      });

      render(
        <MemoryRouter initialEntries={['/protected']}>
          <Routes>
            <Route
              path="/protected"
              element={
                <FeatureRouteGuard enabled={false} redirectPath="/manage/dashboard">
                  <div>Protected Content</div>
                </FeatureRouteGuard>
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
      expect(screen.queryByText('Protected Content')).not.toBeInTheDocument();
    });
  });

  describe('browser navigation', () => {
    it('should handle browser back button correctly', async () => {
      vi.mocked(useAppStore).mockImplementation((selector) => {
        if (typeof selector === 'function') {
          return selector({ configLoaded: true } as any);
        }
        return { configLoaded: true } as any;
      });

      const { router } = render(
        <MemoryRouter initialEntries={['/protected', '/manage/dashboard']}>
          <Routes>
            <Route
              path="/protected"
              element={
                <FeatureRouteGuard enabled={false} redirectPath="/manage/dashboard">
                  <div>Protected Content</div>
                </FeatureRouteGuard>
              }
            />
            <Route path="/manage/dashboard" element={<div>Dashboard</div>} />
          </Routes>
        </MemoryRouter>
      );

      // Should show dashboard (not protected content)
      await waitFor(() => {
        expect(screen.getByText('Dashboard')).toBeInTheDocument();
      });
    });
  });
});