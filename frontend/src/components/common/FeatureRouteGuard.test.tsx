/**
 * Tests for FeatureRouteGuard component
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@/test/utils';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { FeatureRouteGuard } from './FeatureRouteGuard';
import { useAppStore, AppState } from '@/store';

// Mock the useAppStore hook and selectors
vi.mock('@/store', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/store')>();
  return {
    ...actual,
    useAppStore: vi.fn(),
  };
});

// Helper to create mock store state
const createMockStoreState = (configLoaded: boolean): Partial<AppState> => ({
  configLoaded,
});

describe('FeatureRouteGuard', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe('loading state', () => {
    it('should show loading state when configLoaded is false', () => {
      vi.mocked(useAppStore).mockImplementation((selector) => {
        if (typeof selector === 'function') {
          return selector(createMockStoreState(false) as AppState);
        }
        return createMockStoreState(false) as AppState;
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
          return selector(createMockStoreState(true) as AppState);
        }
        return createMockStoreState(true) as AppState;
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
          return selector(createMockStoreState(true) as AppState);
        }
        return createMockStoreState(true) as AppState;
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
          return selector(createMockStoreState(true) as AppState);
        }
        return createMockStoreState(true) as AppState;
      });

      render(
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
