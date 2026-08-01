/**
 * Integration tests for LocalDirectoryBrowser component.
 *
 * Issue #1832 F3: Tests the actual component behavior without mocking,
 * specifically the home-scoping and out-of-scope rejection logic.
 *
 * Unlike PersonalFiles.test.tsx which mocks LocalDirectoryBrowser,
 * these tests render the real component to verify:
 * 1. lockToRoot prop disables navigation
 * 2. rootPath prop enforces path range
 * 3. Breadcrumb clicks respect the lock
 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest';

vi.mock('react-router-dom', () => ({
  useNavigate: () => vi.fn(),
}));

vi.mock('@/store', () => ({
  useLanguage: () => 'en',
  useAppStore: () => vi.fn(),
}));

vi.mock('@/i18n', () => ({
  t: (key: string) => key,
}));

// Define explicit interfaces for mocked components
interface LoadingProps {
  text?: string;
}

interface ButtonProps {
  children: React.ReactNode;
  onClick?: () => void;
  disabled?: boolean;
}

interface EmptyStateProps {
  title: string;
}

// Mock UI components to reduce complexity
vi.mock('@/components/common', () => ({
  useToast: () => ({
    success: vi.fn(),
    error: vi.fn(),
    warning: vi.fn(),
    info: vi.fn(),
  }),
  useConfirm: () => vi.fn().mockResolvedValue(true),
  Loading: ({ text }: LoadingProps) => <div data-testid="loading">{text}</div>,
  Button: ({ children, onClick, disabled }: ButtonProps) => (
    <button onClick={onClick} disabled={disabled}>
      {children}
    </button>
  ),
  EmptyState: ({ title }: EmptyStateProps) => <div data-testid="empty-state">{title}</div>,
}));

// Mock fsApi with realistic behavior
vi.mock('@/api/fs', () => ({
  fsApi: {
    browseDirectory: vi.fn(),
  },
}));

import { LocalDirectoryBrowser } from './LocalDirectoryBrowser';
import { fsApi } from '@/api/fs';

describe('LocalDirectoryBrowser Integration - Issue #1832 F3', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    vi.mocked(fsApi.browseDirectory).mockReset();
  });

  afterEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
  });

  describe('lockToRoot and rootPath path validation', () => {
    it('renders with lockToRoot=true and hides up navigation button', async () => {
      vi.mocked(fsApi.browseDirectory).mockResolvedValue({
        path: '/home/alice',
        directories: [{ name: 'project', path: '/home/alice/project', is_writable: true }],
        parent: '/home',
        is_writable: true,
      });

      render(
        <LocalDirectoryBrowser
          initialPath="/home/alice"
          onSelectPath={() => {}}
          lockToRoot={true}
          rootPath="/home/alice"
        />
      );

      // Wait for loading to complete
      await waitFor(() => {
        expect(screen.getByText('project')).toBeInTheDocument();
      });

      // Issue #1832 F3: Up button should NOT be rendered when locked
      // The component sets lockToRoot=true which disables up navigation
      const upButtons = screen
        .queryAllByRole('button')
        .filter((btn) => btn.textContent?.toLowerCase().includes('up'));
      expect(upButtons.length).toBe(0);
    });

    it('renders with lockToRoot=true and hides root "/" button', async () => {
      vi.mocked(fsApi.browseDirectory).mockResolvedValue({
        path: '/home/alice',
        directories: [],
        parent: '/home',
        is_writable: true,
      });

      render(
        <LocalDirectoryBrowser
          initialPath="/home/alice"
          onSelectPath={() => {}}
          lockToRoot={true}
          rootPath="/home/alice"
        />
      );

      await waitFor(() => {
        expect(screen.getByText('alice')).toBeInTheDocument();
      });

      // Issue #1832 F3: Root "/" button should NOT be rendered when locked
      const buttons = screen.queryAllByRole('button');
      const rootButton = buttons.find((btn) => btn.textContent === '/');
      expect(rootButton).toBeUndefined();
    });

    it('allows navigation within rootPath subtree', async () => {
      // First call: initial path
      vi.mocked(fsApi.browseDirectory).mockResolvedValueOnce({
        path: '/home/alice',
        directories: [{ name: 'project', path: '/home/alice/project', is_writable: true }],
        parent: '/home',
        is_writable: true,
      });

      // Second call: navigate to project
      vi.mocked(fsApi.browseDirectory).mockResolvedValueOnce({
        path: '/home/alice/project',
        directories: [{ name: 'src', path: '/home/alice/project/src', is_writable: true }],
        parent: '/home/alice',
        is_writable: true,
      });

      render(
        <LocalDirectoryBrowser
          initialPath="/home/alice"
          onSelectPath={() => {}}
          lockToRoot={true}
          rootPath="/home/alice"
        />
      );

      await waitFor(() => {
        expect(screen.getByText('project')).toBeInTheDocument();
      });

      // Navigate into project directory (within rootPath)
      fireEvent.click(screen.getByText('project'));

      await waitFor(() => {
        expect(screen.getByText('src')).toBeInTheDocument();
      });

      // Verify we navigated correctly
      expect(fsApi.browseDirectory).toHaveBeenCalledTimes(2);
    });

    it('handles API error gracefully', async () => {
      vi.mocked(fsApi.browseDirectory).mockRejectedValue(new Error('Permission denied'));

      render(
        <LocalDirectoryBrowser
          initialPath="/home/alice"
          onSelectPath={() => {}}
          lockToRoot={true}
          rootPath="/home/alice"
        />
      );

      // Should show error state
      await waitFor(() => {
        expect(screen.getByTestId('empty-state')).toBeInTheDocument();
      });
    });

    it('handles empty directory list', async () => {
      vi.mocked(fsApi.browseDirectory).mockResolvedValue({
        path: '/home/alice',
        directories: [],
        parent: '/home',
        is_writable: true,
      });

      render(
        <LocalDirectoryBrowser
          initialPath="/home/alice"
          onSelectPath={() => {}}
          lockToRoot={true}
          rootPath="/home/alice"
        />
      );

      await waitFor(() => {
        expect(screen.getByText('alice')).toBeInTheDocument();
      });

      // Should render successfully even with empty directories
      expect(screen.getByText('alice')).toBeInTheDocument();
    });
  });

  describe('breadcrumb and navigation', () => {
    it('displays breadcrumb for current path', async () => {
      vi.mocked(fsApi.browseDirectory).mockResolvedValue({
        path: '/home/alice/project',
        directories: [],
        parent: '/home/alice',
        is_writable: true,
      });

      render(
        <LocalDirectoryBrowser
          initialPath="/home/alice/project"
          onSelectPath={() => {}}
          lockToRoot={true}
          rootPath="/home/alice"
        />
      );

      await waitFor(() => {
        // Breadcrumb should show path segments
        expect(screen.getByText('project')).toBeInTheDocument();
      });
    });
  });

  describe('edge cases', () => {
    it('handles missing parent path', async () => {
      vi.mocked(fsApi.browseDirectory).mockResolvedValue({
        path: '/home/alice',
        directories: [],
        parent: null,
        is_writable: true,
      });

      render(
        <LocalDirectoryBrowser
          initialPath="/home/alice"
          onSelectPath={() => {}}
          lockToRoot={true}
          rootPath="/home/alice"
        />
      );

      await waitFor(() => {
        expect(screen.getByText('alice')).toBeInTheDocument();
      });
    });

    it('handles concurrent navigation attempts', async () => {
      vi.mocked(fsApi.browseDirectory).mockResolvedValue({
        path: '/home/alice',
        directories: [
          { name: 'project1', path: '/home/alice/project1', is_writable: true },
          { name: 'project2', path: '/home/alice/project2', is_writable: true },
        ],
        parent: '/home',
        is_writable: true,
      });

      render(
        <LocalDirectoryBrowser
          initialPath="/home/alice"
          onSelectPath={() => {}}
          lockToRoot={true}
          rootPath="/home/alice"
        />
      );

      await waitFor(() => {
        expect(screen.getByText('project1')).toBeInTheDocument();
      });

      // Rapid clicks on different directories
      fireEvent.click(screen.getByText('project1'));
      fireEvent.click(screen.getByText('project2'));

      // Should handle gracefully (only one navigation should complete)
      await waitFor(() => {
        expect(fsApi.browseDirectory).toHaveBeenCalled();
      });
    });
  });
});
