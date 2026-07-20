/* eslint-disable @typescript-eslint/no-explicit-any */

/**
 * Tests for LocalDirectoryBrowser.
 *
 * Focus: the enableFileActions prop gates the personal-files UI so the
 * component's other callers (LocalDirectoryBrowserModal → NewAutonomousModal)
 * are unaffected. When the flag is off (default), no upload button, no file
 * list, no file-action handlers are wired. When on, upload/download/delete
 * drive fsApi and refresh the listing.
 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('react-router-dom', () => ({ useNavigate: () => vi.fn() }));

const toastMock = {
  success: vi.fn(),
  error: vi.fn(),
  warning: vi.fn(),
  info: vi.fn(),
};
// Issue #1917: handleDelete uses the app's useConfirm() hook instead of
// window.confirm(). Tests drive this mock to resolve true/false per case.
const confirmMock = vi.fn<(opts?: any) => Promise<boolean>>();
vi.mock('@/components/common', () => ({
  useToast: () => toastMock,
  useConfirm: () => confirmMock,
  Loading: ({ text }: any) => <div>{text}</div>,
  Button: ({ children, onClick, disabled, variant, size }: any) => (
    <button onClick={onClick} disabled={disabled} data-variant={variant} data-size={size}>
      {children}
    </button>
  ),
  EmptyState: ({ title, description }: any) => (
    <div data-testid="empty-state">
      <span>{title}</span>
      <span>{description}</span>
    </div>
  ),
}));

vi.mock('@/store', () => ({
  useLanguage: () => 'en',
  useAppStore: () => vi.fn(),
}));

vi.mock('@/i18n', () => ({
  // Echo the key so we can assert on t('uploadFile') etc.
  t: (key: string, _lang?: any, params?: Record<string, any>) => {
    if (params) {
      let out = key;
      for (const [k, v] of Object.entries(params)) out = out.replace(`{${k}}`, String(v));
      return out;
    }
    return key;
  },
}));

const browseDirectoryMock = vi.fn();
const uploadFileMock = vi.fn();
const downloadFileMock = vi.fn();
const deleteFileMock = vi.fn();

vi.mock('@/api/fs', () => ({
  fsApi: {
    browseDirectory: (...args: any[]) => browseDirectoryMock(...args),
    uploadFile: (...args: any[]) => uploadFileMock(...args),
    downloadFile: (...args: any[]) => downloadFileMock(...args),
    deleteFile: (...args: any[]) => deleteFileMock(...args),
    createDirectory: vi.fn(),
  },
  MAX_UPLOAD_SIZE_MB: 100,
}));

// downloadBlob mock — record calls, do nothing
const downloadBlobMock = vi.fn();
vi.mock('@/utils', () => ({
  downloadBlob: (...args: any[]) => downloadBlobMock(...args),
  formatBytes: (bytes: number) => `${bytes}B`,
}));

import { LocalDirectoryBrowser } from './LocalDirectoryBrowser';

const browseResponse = (overrides: Partial<any> = {}) => ({
  path: '/home/alice',
  name: 'alice',
  directories: [],
  files: [],
  parent: null,
  homePath: '/home/alice',
  canCreate: true,
  is_writable: true,
  ...overrides,
});

describe('LocalDirectoryBrowser', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    browseDirectoryMock.mockResolvedValue(browseResponse());
  });

  it('does NOT render file-action UI by default (isolation for other callers)', async () => {
    render(<LocalDirectoryBrowser initialPath="/home/alice" onSelectPath={() => {}} />);

    await waitFor(() => {
      expect(browseDirectoryMock).toHaveBeenCalled();
    });

    // No upload button
    expect(screen.queryByText('uploadFile')).not.toBeInTheDocument();
    // No file list rendered (browseDirectory called without includeFiles)
    expect(browseDirectoryMock).toHaveBeenCalledWith('/home/alice', { includeFiles: false });
  });

  it('does NOT request files from backend when enableFileActions is false', async () => {
    render(<LocalDirectoryBrowser initialPath="/home/alice" onSelectPath={() => {}} />);

    await waitFor(() => {
      expect(browseDirectoryMock).toHaveBeenCalled();
    });
    const [, opts] = browseDirectoryMock.mock.calls[0];
    expect(opts).toEqual({ includeFiles: false });
  });

  it('renders upload button and file list when enableFileActions=true', async () => {
    browseDirectoryMock.mockResolvedValue(
      browseResponse({
        directories: [{ name: 'subdir', path: '/home/alice/subdir', is_writable: true }],
        files: [
          { name: 'report.txt', path: '/home/alice/report.txt', size: 1024, is_readable: true },
        ],
      })
    );

    render(
      <LocalDirectoryBrowser initialPath="/home/alice" onSelectPath={() => {}} enableFileActions />
    );

    // backend asked for files
    await waitFor(() => {
      expect(browseDirectoryMock).toHaveBeenCalledWith('/home/alice', {
        includeFiles: true,
      });
    });

    // Upload button present
    expect(screen.getByText('uploadFile')).toBeInTheDocument();
    // Directory entry present
    expect(screen.getByText('subdir')).toBeInTheDocument();
    // File entry present with download/delete buttons (titles)
    expect(screen.getByText('report.txt')).toBeInTheDocument();
    expect(screen.getByTitle('downloadFile')).toBeInTheDocument();
    expect(screen.getByTitle('deleteFile')).toBeInTheDocument();
  });

  it('calls fsApi.deleteFile on delete click after confirm', async () => {
    confirmMock.mockResolvedValue(true);
    deleteFileMock.mockResolvedValue({ success: true });
    browseDirectoryMock.mockResolvedValue(
      browseResponse({
        files: [{ name: 'trash.txt', path: '/home/alice/trash.txt', size: 5, is_readable: true }],
      })
    );

    render(
      <LocalDirectoryBrowser initialPath="/home/alice" onSelectPath={() => {}} enableFileActions />
    );

    await waitFor(() => {
      expect(screen.getByText('trash.txt')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTitle('deleteFile'));

    await waitFor(() => {
      expect(deleteFileMock).toHaveBeenCalledWith('/home/alice/trash.txt');
    });
    expect(toastMock.success).toHaveBeenCalledWith('deleteSuccess', 'trash.txt');
    // Issue #1917: the app confirm modal (not window.confirm) must be invoked
    // with the danger variant before the delete fires.
    expect(confirmMock).toHaveBeenCalledWith({ message: 'confirmDeleteFile', variant: 'danger' });
  });

  it('aborts delete when confirm dialog is cancelled', async () => {
    confirmMock.mockResolvedValue(false);
    deleteFileMock.mockResolvedValue({ success: true });
    browseDirectoryMock.mockResolvedValue(
      browseResponse({
        files: [{ name: 'keep.txt', path: '/home/alice/keep.txt', size: 5, is_readable: true }],
      })
    );

    render(
      <LocalDirectoryBrowser initialPath="/home/alice" onSelectPath={() => {}} enableFileActions />
    );

    await waitFor(() => {
      expect(screen.getByText('keep.txt')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTitle('deleteFile'));

    // Give async a tick to potentially fail
    await new Promise((r) => setTimeout(r, 10));
    expect(deleteFileMock).not.toHaveBeenCalled();
  });

  it('downloads a file via downloadBlob on download click', async () => {
    const blob = new Blob(['x'], { type: 'text/plain' });
    downloadFileMock.mockResolvedValue(blob);
    browseDirectoryMock.mockResolvedValue(
      browseResponse({
        files: [{ name: 'doc.txt', path: '/home/alice/doc.txt', size: 3, is_readable: true }],
      })
    );

    render(
      <LocalDirectoryBrowser initialPath="/home/alice" onSelectPath={() => {}} enableFileActions />
    );

    await waitFor(() => {
      expect(screen.getByText('doc.txt')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTitle('downloadFile'));

    await waitFor(() => {
      expect(downloadFileMock).toHaveBeenCalledWith('/home/alice/doc.txt');
    });
    await waitFor(() => {
      expect(downloadBlobMock).toHaveBeenCalledWith(blob, 'doc.txt');
    });
  });

  it('hides delete button when directory is not writable', async () => {
    browseDirectoryMock.mockResolvedValue(
      browseResponse({
        is_writable: false,
        files: [{ name: 'ro.txt', path: '/home/alice/ro.txt', size: 1, is_readable: true }],
      })
    );

    render(
      <LocalDirectoryBrowser initialPath="/home/alice" onSelectPath={() => {}} enableFileActions />
    );

    await waitFor(() => {
      expect(screen.getByText('ro.txt')).toBeInTheDocument();
    });
    // Download still available
    expect(screen.getByTitle('downloadFile')).toBeInTheDocument();
    // Delete hidden (not writable)
    expect(screen.queryByTitle('deleteFile')).not.toBeInTheDocument();
    // Upload button hidden too (only shown when isWritable)
    expect(screen.queryByText('uploadFile')).not.toBeInTheDocument();
  });
});
