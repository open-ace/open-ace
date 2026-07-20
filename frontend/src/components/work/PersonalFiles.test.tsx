/* eslint-disable @typescript-eslint/no-explicit-any */

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const navigateMock = vi.fn();

vi.mock('react-router-dom', () => ({
  useNavigate: () => navigateMock,
}));

vi.mock('@/store', () => ({
  useLanguage: () => 'en',
  useAppStore: () => vi.fn(),
}));

vi.mock('@/i18n', () => ({
  t: (key: string) => key,
}));

// Mock fsApi to return home directory path
vi.mock('@/api/fs', () => ({
  fsApi: {
    browseDirectory: vi.fn().mockResolvedValue({
      path: '/home/alice',
      directories: [],
      parent: null,
      is_writable: true,
    }),
  },
}));

const createSessionMock = vi.fn();

vi.mock('@/api/sessions', () => ({
  sessionsApi: {
    createSession: (...args: unknown[]) => createSessionMock(...args),
  },
}));

vi.mock('./LocalDirectoryBrowser', () => ({
  LocalDirectoryBrowser: ({
    initialPath,
    onSelectPath,
    listMaxHeight,
    lockToRoot,
    rootPath,
    hideManualInput,
    hideRecentPaths,
    enableFileActions,
  }: any) => (
    <div data-testid="local-directory-browser">
      <span data-testid="initial-path">{initialPath}</span>
      <span data-testid="list-max-height">{listMaxHeight}</span>
      <span data-testid="lock-to-root">{lockToRoot ? 'true' : 'false'}</span>
      <span data-testid="root-path">{rootPath || 'none'}</span>
      <span data-testid="hide-manual-input">{hideManualInput ? 'true' : 'false'}</span>
      <span data-testid="hide-recent-paths">{hideRecentPaths ? 'true' : 'false'}</span>
      <span data-testid="enable-file-actions">{enableFileActions ? 'true' : 'false'}</span>
      <button onClick={() => onSelectPath('/home/alice/project')} data-testid="select-path">
        Select
      </button>
    </div>
  ),
}));

import { PersonalFiles } from './PersonalFiles';

describe('PersonalFiles', () => {
  beforeEach(() => {
    navigateMock.mockClear();
    createSessionMock.mockReset();
  });

  it('renders the home directory browser', async () => {
    render(<PersonalFiles />);

    // Wait for loading to complete and LocalDirectoryBrowser to appear
    await waitFor(() => {
      expect(screen.getByTestId('local-directory-browser')).toBeInTheDocument();
    });

    expect(screen.getByText('personalFiles')).toBeInTheDocument();
    expect(screen.getByTestId('initial-path')).toHaveTextContent('home');
    // Verify path locking props (Issue #1813)
    expect(screen.getByTestId('lock-to-root')).toHaveTextContent('true');
    expect(screen.getByTestId('root-path')).toHaveTextContent('/home/alice');
    expect(screen.getByTestId('hide-manual-input')).toHaveTextContent('true');
    expect(screen.getByTestId('hide-recent-paths')).toHaveTextContent('true');
    // Personal files page enables upload/download/delete UI
    expect(screen.getByTestId('enable-file-actions')).toHaveTextContent('true');
  });

  it('pre-creates a session and navigates with sessionId (Issue #1924)', async () => {
    createSessionMock.mockResolvedValue({
      success: true,
      data: { session_id: 'sess-123' },
    });

    render(<PersonalFiles />);

    // Wait for loading to complete
    await waitFor(() => {
      expect(screen.getByTestId('local-directory-browser')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId('select-path'));

    await waitFor(() => expect(navigateMock).toHaveBeenCalledTimes(1));

    // Should pre-create a session with the local tool name and the chosen path
    expect(createSessionMock).toHaveBeenCalledTimes(1);
    const payload = createSessionMock.mock.calls[0][0];
    expect(payload.tool_name).toBe('qwen-code');
    expect(payload.project_path).toBe('/home/alice/project');

    // Navigation target must include sessionId so qwen-code-webui opens
    // ChatPage directly instead of the project picker.
    const target = navigateMock.mock.calls[0][0] as string;
    expect(target).toContain('/work?newTab=true');
    expect(target).toContain('workspaceType=local');
    expect(target).toContain('projectPath=%2Fhome%2Falice%2Fproject');
    expect(target).toContain('sessionId=sess-123');
  });

  it('shows an error and stays on the page when session creation fails (Issue #1924)', async () => {
    createSessionMock.mockResolvedValue({
      success: false,
      error: 'Invalid project path',
    });

    render(<PersonalFiles />);

    await waitFor(() => {
      expect(screen.getByTestId('local-directory-browser')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId('select-path'));

    await waitFor(() => expect(createSessionMock).toHaveBeenCalledTimes(1));

    // Must NOT navigate away when session creation failed
    expect(navigateMock).not.toHaveBeenCalled();
    // Should surface the failure to the user
    expect(screen.getByText('openSessionHereFailed')).toBeInTheDocument();
  });
});
