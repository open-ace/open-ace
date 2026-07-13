/**
 * WorkLayout Component Tests - Feature Flags Loading
 *
 * Tests cover:
 * - Feature flags loading on mount
 * - Navigation visibility based on autonomousEnabled
 * - Error handling when config/flags fetch fails
 */

import { render, screen, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom';
import { vi, describe, it, expect, beforeEach } from 'vitest';
import { MemoryRouter } from 'react-router-dom';
import { WorkLayout } from './WorkLayout';

// Mock store hooks - use a mutable object to allow dynamic values
const mockStore = {
  exitWorkspaceFullscreen: vi.fn(),
  previousLeftPanelCollapsed: false,
  previousRightPanelCollapsed: false,
  autonomousEnabled: false,
  setAutonomousEnabled: vi.fn(),
  setModelGatewayEnabled: vi.fn(),
  setRunTimelineEnabled: vi.fn(),
  setPolicyEnabled: vi.fn(),
  setConfigLoaded: vi.fn(),
};

vi.mock('@/store', () => ({
  useLanguage: () => 'en',
  useAppStore: () => mockStore,
  useWorkspaceFullscreen: () => false,
}));

// Mock i18n
vi.mock('@/i18n', () => ({
  t: (key: string) => {
    const translations: Record<string, string> = {
      navigation: 'Navigation',
      workspace: 'Workspace',
      autonomousDev: 'Autonomous Dev',
      sessionHistory: 'Session History',
      prompts: 'Prompts',
      myUsage: 'My Usage',
      insights: 'Insights',
      showMore: 'Show More',
      showLess: 'Show Less',
    };
    return translations[key] || key;
  },
}));

// Mock API - must use vi.fn() inside the mock factory
vi.mock('@/api/workspace', () => ({
  workspaceApi: {
    getConfig: vi.fn(),
  },
}));

vi.mock('@/api/featureFlags', () => ({
  featureFlagsApi: {
    getFlags: vi.fn(),
  },
}));

// Mock child components
vi.mock('@/components/common', () => ({
  ModeSwitcher: () => <div data-testid="mode-switcher">Mode Switcher</div>,
}));

vi.mock('./Header', () => ({
  Header: () => <div data-testid="header">Header</div>,
}));

vi.mock('@/components/work', () => ({
  SessionList: ({ collapsed }: { collapsed: boolean }) => (
    <div data-testid="session-list">Session List {collapsed ? '(collapsed)' : ''}</div>
  ),
  AssistPanel: ({ collapsed }: { collapsed: boolean }) => (
    <div data-testid="assist-panel">Assist Panel {collapsed ? '(collapsed)' : ''}</div>
  ),
  StatusBar: () => <div data-testid="status-bar">Status Bar</div>,
}));

// Import mocked APIs after vi.mock calls
import { workspaceApi } from '@/api/workspace';
import { featureFlagsApi } from '@/api/featureFlags';

describe('WorkLayout', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    // Reset mock store values
    mockStore.autonomousEnabled = false;
    mockStore.exitWorkspaceFullscreen = vi.fn();
    mockStore.setAutonomousEnabled = vi.fn();
    mockStore.setModelGatewayEnabled = vi.fn();
    mockStore.setRunTimelineEnabled = vi.fn();
    mockStore.setPolicyEnabled = vi.fn();
    mockStore.setConfigLoaded = vi.fn();
    // Suppress console.error during tests
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  describe('Feature Flags Loading', () => {
    it('loads workspace config and feature flags on mount', async () => {
      vi.mocked(workspaceApi.getConfig).mockResolvedValue({
        autonomous_enabled: true,
      });
      vi.mocked(featureFlagsApi.getFlags).mockResolvedValue({
        model_gateway: true,
        run_timeline: false,
        policy: true,
        autonomous: true,
      });

      render(
        <MemoryRouter initialEntries={['/work']}>
          <WorkLayout />
        </MemoryRouter>
      );

      await waitFor(() => {
        expect(workspaceApi.getConfig).toHaveBeenCalled();
        expect(featureFlagsApi.getFlags).toHaveBeenCalled();
      });
    });

    it('calls setAutonomousEnabled with config value', async () => {
      vi.mocked(workspaceApi.getConfig).mockResolvedValue({
        autonomous_enabled: true,
      });
      vi.mocked(featureFlagsApi.getFlags).mockResolvedValue({
        model_gateway: false,
        run_timeline: false,
        policy: false,
        autonomous: false,
      });

      render(
        <MemoryRouter initialEntries={['/work']}>
          <WorkLayout />
        </MemoryRouter>
      );

      await waitFor(() => {
        expect(mockStore.setAutonomousEnabled).toHaveBeenCalledWith(true);
      });
    });

    it('calls setModelGatewayEnabled with flags value', async () => {
      vi.mocked(workspaceApi.getConfig).mockResolvedValue({
        autonomous_enabled: false,
      });
      vi.mocked(featureFlagsApi.getFlags).mockResolvedValue({
        model_gateway: true,
        run_timeline: false,
        policy: false,
        autonomous: false,
      });

      render(
        <MemoryRouter initialEntries={['/work']}>
          <WorkLayout />
        </MemoryRouter>
      );

      await waitFor(() => {
        expect(mockStore.setModelGatewayEnabled).toHaveBeenCalledWith(true);
      });
    });

    it('calls setRunTimelineEnabled and setPolicyEnabled', async () => {
      vi.mocked(workspaceApi.getConfig).mockResolvedValue({
        autonomous_enabled: false,
      });
      vi.mocked(featureFlagsApi.getFlags).mockResolvedValue({
        model_gateway: false,
        run_timeline: true,
        policy: true,
        autonomous: false,
      });

      render(
        <MemoryRouter initialEntries={['/work']}>
          <WorkLayout />
        </MemoryRouter>
      );

      await waitFor(() => {
        expect(mockStore.setRunTimelineEnabled).toHaveBeenCalledWith(true);
        expect(mockStore.setPolicyEnabled).toHaveBeenCalledWith(true);
      });
    });

    it('logs error when config fetch fails', async () => {
      const mockConsoleError = vi.spyOn(console, 'error').mockImplementation(() => {});

      vi.mocked(workspaceApi.getConfig).mockRejectedValue(new Error('Network error'));
      vi.mocked(featureFlagsApi.getFlags).mockResolvedValue({
        model_gateway: false,
        run_timeline: false,
        policy: false,
        autonomous: false,
      });

      render(
        <MemoryRouter initialEntries={['/work']}>
          <WorkLayout />
        </MemoryRouter>
      );

      await waitFor(() => {
        expect(mockConsoleError).toHaveBeenCalledWith(
          'Failed to load config or feature flags:',
          expect.any(Error)
        );
        expect(mockStore.setConfigLoaded).toHaveBeenCalledWith(true);
      });
    });

    it('logs error when flags fetch fails', async () => {
      const mockConsoleError = vi.spyOn(console, 'error').mockImplementation(() => {});

      vi.mocked(workspaceApi.getConfig).mockResolvedValue({
        autonomous_enabled: false,
      });
      vi.mocked(featureFlagsApi.getFlags).mockRejectedValue(new Error('API error'));

      render(
        <MemoryRouter initialEntries={['/work']}>
          <WorkLayout />
        </MemoryRouter>
      );

      await waitFor(() => {
        expect(mockConsoleError).toHaveBeenCalledWith(
          'Failed to load config or feature flags:',
          expect.any(Error)
        );
      });
    });

    it('marks configLoaded as true after successful load', async () => {
      vi.mocked(workspaceApi.getConfig).mockResolvedValue({
        autonomous_enabled: false,
      });
      vi.mocked(featureFlagsApi.getFlags).mockResolvedValue({
        model_gateway: false,
        run_timeline: false,
        policy: false,
        autonomous: false,
      });

      render(
        <MemoryRouter initialEntries={['/work']}>
          <WorkLayout />
        </MemoryRouter>
      );

      await waitFor(() => {
        expect(mockStore.setConfigLoaded).toHaveBeenCalledWith(true);
      });
    });
  });

  describe('Navigation Visibility', () => {
    it('shows all nav items when autonomous is enabled', async () => {
      mockStore.autonomousEnabled = true;

      vi.mocked(workspaceApi.getConfig).mockResolvedValue({
        autonomous_enabled: true,
      });
      vi.mocked(featureFlagsApi.getFlags).mockResolvedValue({
        model_gateway: false,
        run_timeline: false,
        policy: false,
        autonomous: true,
      });

      render(
        <MemoryRouter initialEntries={['/work']}>
          <WorkLayout />
        </MemoryRouter>
      );

      await waitFor(() => {
        // Should show autonomous nav item
        expect(screen.getByTitle('Autonomous Dev')).toBeInTheDocument();
      });

      // Should show all items
      expect(screen.getByTitle('Workspace')).toBeInTheDocument();
      expect(screen.getByTitle('Session History')).toBeInTheDocument();
      expect(screen.getByTitle('Prompts')).toBeInTheDocument();
      expect(screen.getByTitle('My Usage')).toBeInTheDocument();
      expect(screen.getByTitle('Insights')).toBeInTheDocument();
    });

    it('hides autonomous nav item when autonomous is disabled', async () => {
      mockStore.autonomousEnabled = false;

      vi.mocked(workspaceApi.getConfig).mockResolvedValue({
        autonomous_enabled: false,
      });
      vi.mocked(featureFlagsApi.getFlags).mockResolvedValue({
        model_gateway: false,
        run_timeline: false,
        policy: false,
        autonomous: false,
      });

      render(
        <MemoryRouter initialEntries={['/work']}>
          <WorkLayout />
        </MemoryRouter>
      );

      await waitFor(() => {
        expect(workspaceApi.getConfig).toHaveBeenCalled();
      });

      // Should NOT show autonomous nav item
      expect(screen.queryByTitle('Autonomous Dev')).not.toBeInTheDocument();

      // Should show other items
      expect(screen.getByTitle('Workspace')).toBeInTheDocument();
      expect(screen.getByTitle('Session History')).toBeInTheDocument();
    });
  });

  describe('Layout Structure', () => {
    it('renders header, panels, and status bar', async () => {
      vi.mocked(workspaceApi.getConfig).mockResolvedValue({
        autonomous_enabled: false,
      });
      vi.mocked(featureFlagsApi.getFlags).mockResolvedValue({
        model_gateway: false,
        run_timeline: false,
        policy: false,
        autonomous: false,
      });

      render(
        <MemoryRouter initialEntries={['/work']}>
          <WorkLayout>
            <div data-testid="main-content">Main Content</div>
          </WorkLayout>
        </MemoryRouter>
      );

      await waitFor(() => {
        expect(screen.getByTestId('header')).toBeInTheDocument();
      });

      expect(screen.getByTestId('session-list')).toBeInTheDocument();
      expect(screen.getByTestId('assist-panel')).toBeInTheDocument();
      expect(screen.getByTestId('status-bar')).toBeInTheDocument();
      expect(screen.getByTestId('main-content')).toBeInTheDocument();
    });

    it('renders children in main area', async () => {
      vi.mocked(workspaceApi.getConfig).mockResolvedValue({
        autonomous_enabled: false,
      });
      vi.mocked(featureFlagsApi.getFlags).mockResolvedValue({
        model_gateway: false,
        run_timeline: false,
        policy: false,
        autonomous: false,
      });

      render(
        <MemoryRouter initialEntries={['/work']}>
          <WorkLayout>
            <div data-testid="child-component">Child Component</div>
          </WorkLayout>
        </MemoryRouter>
      );

      await waitFor(() => {
        expect(screen.getByTestId('child-component')).toBeInTheDocument();
      });
    });
  });
});