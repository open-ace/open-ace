/**
 * DirectoryBrowserModal Unit Tests
 *
 * Issue #584: Remote workspace directory browser
 */

import { render, screen, fireEvent } from '@testing-library/react';
import { vi, describe, it, expect, beforeEach } from 'vitest';

// Mock dependencies
vi.mock('@/store', () => ({
  useLanguage: () => 'en',
}));

vi.mock('@/i18n', () => ({
  t: (key: string) => key,
}));

vi.mock('@/components/common', () => ({
  Modal: ({ isOpen, onClose, title, children }: any) =>
    isOpen ? (
      <div data-testid="modal">
        <h5>{title}</h5>
        <button onClick={onClose} data-testid="close-btn">
          Close
        </button>
        {children}
      </div>
    ) : null,
}));

vi.mock('./RemoteDirectoryBrowser', () => ({
  RemoteDirectoryBrowser: ({ machineId, initialPath, onSelectPath, onClose }: any) => (
    <div data-testid="remote-directory-browser">
      <span data-testid="machine-id">{machineId}</span>
      <span data-testid="initial-path">{initialPath}</span>
      <button onClick={() => onSelectPath('/test/path')} data-testid="select-btn">
        Select Path
      </button>
      <button onClick={onClose} data-testid="browser-close-btn">
        Close Browser
      </button>
    </div>
  ),
}));

import { DirectoryBrowserModal } from './DirectoryBrowserModal';

describe('DirectoryBrowserModal', () => {
  const defaultProps = {
    isOpen: true,
    onClose: vi.fn(),
    machineId: 'machine-123',
    initialPath: '/root/workspace',
    onSelectPath: vi.fn(),
  };

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders RemoteDirectoryBrowser inside modal', () => {
    render(<DirectoryBrowserModal {...defaultProps} />);

    expect(screen.getByTestId('modal')).toBeInTheDocument();
    expect(screen.getByTestId('remote-directory-browser')).toBeInTheDocument();
  });

  it('passes correct props to RemoteDirectoryBrowser', () => {
    render(<DirectoryBrowserModal {...defaultProps} />);

    expect(screen.getByTestId('machine-id')).toHaveTextContent('machine-123');
    expect(screen.getByTestId('initial-path')).toHaveTextContent('/root/workspace');
  });

  it('calls onSelectPath when path selected', () => {
    const onSelectPath = vi.fn();
    render(<DirectoryBrowserModal {...defaultProps} onSelectPath={onSelectPath} />);

    fireEvent.click(screen.getByTestId('select-btn'));

    expect(onSelectPath).toHaveBeenCalledWith('/test/path');
  });

  it('closes modal on close button click', () => {
    const onClose = vi.fn();
    render(<DirectoryBrowserModal {...defaultProps} onClose={onClose} />);

    fireEvent.click(screen.getByTestId('close-btn'));

    expect(onClose).toHaveBeenCalled();
  });

  it('closes modal when path is selected', () => {
    const onClose = vi.fn();
    const onSelectPath = vi.fn();
    render(
      <DirectoryBrowserModal {...defaultProps} onClose={onClose} onSelectPath={onSelectPath} />
    );

    fireEvent.click(screen.getByTestId('select-btn'));

    expect(onSelectPath).toHaveBeenCalled();
    expect(onClose).toHaveBeenCalled();
  });

  it('does not render when isOpen is false', () => {
    render(<DirectoryBrowserModal {...defaultProps} isOpen={false} />);

    expect(screen.queryByTestId('modal')).not.toBeInTheDocument();
  });
});
