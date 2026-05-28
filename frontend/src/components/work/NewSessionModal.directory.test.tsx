/**
 * NewSessionModal Directory Browser Tests
 *
 * Issue #584: Tests for directory browser integration
 */

import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { vi, describe, it, expect, beforeEach } from 'vitest';

// Mock dependencies
vi.mock('@/hooks', () => ({
  useAvailableMachines: () => ({
    data: {
      machines: [
        { machine_id: 'machine-1', machine_name: 'Test Machine', os_type: 'linux', work_dir: '/root/workspace' }
      ]
    },
    isLoading: false,
  }),
  useCreateRemoteSession: () => ({
    mutateAsync: vi.fn(),
    isPending: false,
    isError: false,
  }),
}));

vi.mock('@/store', () => ({
  useLanguage: () => 'en',
}));

vi.mock('@/i18n', () => ({
  t: (key: string) => key,
}));

vi.mock('@/components/common', () => ({
  Modal: ({ isOpen, title, footer, children }: any) =>
    isOpen ? (
      <div data-testid="new-session-modal">
        <h5>{title}</h5>
        {children}
        <div data-testid="modal-footer">{footer}</div>
      </div>
    ) : null,
  Button: ({ variant, onClick, disabled, loading, children }: any) => (
    <button
      onClick={onClick}
      disabled={disabled || loading}
      data-testid={`btn-${variant}`}
      className={`btn btn-${variant}`}
    >
      {children}
    </button>
  ),
}));

vi.mock('./RemoteMachineSelector', () => ({
  RemoteMachineSelector: ({ onSelectMachine, selectedMachineId, machines }: any) => (
    <div data-testid="machine-selector">
      <select
        value={selectedMachineId}
        onChange={(e) => onSelectMachine(e.target.value, machines[0])}
        data-testid="machine-select"
      >
        {machines.map((m: any) => (
          <option key={m.machine_id} value={m.machine_id}>{m.machine_name}</option>
        ))}
      </select>
    </div>
  ),
}));

vi.mock('./DirectoryBrowserModal', () => ({
  DirectoryBrowserModal: ({ isOpen, onClose, machineId, onSelectPath }: any) =>
    isOpen ? (
      <div data-testid="directory-browser-modal">
        <span data-testid="browser-machine-id">{machineId}</span>
        <button onClick={() => onSelectPath('/selected/path')} data-testid="select-path-btn">
          Select Path
        </button>
        <button onClick={onClose} data-testid="close-browser-btn">
          Close
        </button>
      </div>
    ) : null,
}));

vi.mock('react-router-dom', () => ({
  useNavigate: () => vi.fn(),
  useLocation: () => ({ pathname: '/work' }),
}));

import { NewSessionModal } from './NewSessionModal';

describe('NewSessionModal - Directory Browser Integration', () => {
  const defaultProps = {
    isOpen: true,
    onClose: vi.fn(),
  };

  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
  });

  describe('Browse Button', () => {
    it('renders browse button for remote workspace', () => {
      render(<NewSessionModal {...defaultProps} />);

      // Select remote workspace type
      fireEvent.click(screen.getByText('remoteWorkspace'));

      // Select a machine
      const select = screen.getByTestId('machine-select');
      fireEvent.change(select, { target: { value: 'machine-1' } });

      // Browse button should appear
      expect(screen.getByText('browse')).toBeInTheDocument();
    });

    it('browse button opens directory browser modal', () => {
      render(<NewSessionModal {...defaultProps} />);

      // Select remote workspace type
      fireEvent.click(screen.getByText('remoteWorkspace'));

      // Select a machine
      const select = screen.getByTestId('machine-select');
      fireEvent.change(select, { target: { value: 'machine-1' } });

      // Click browse button
      fireEvent.click(screen.getByText('browse'));

      // Directory browser modal should open
      expect(screen.getByTestId('directory-browser-modal')).toBeInTheDocument();
    });

    it('project path section not rendered when no machine selected', () => {
      render(<NewSessionModal {...defaultProps} />);

      // Select remote workspace type (but no machine)
      fireEvent.click(screen.getByText('remoteWorkspace'));

      // The project path section should not be rendered
      // because the condition is `selectedMachineId && workspaceType === 'remote'`
      // Since selectedMachineId is empty (no machine selected), the section should not appear

      // Check that project path section is not rendered
      // Note: The actual component doesn't render project path when no machine is selected
      expect(screen.queryByLabelText('projectPath')).toBeNull();
    });

    it('renders browse button for terminal workspace', () => {
      render(<NewSessionModal {...defaultProps} />);

      // Select terminal workspace type
      fireEvent.click(screen.getByText('terminalWorkspace'));

      // Select a machine
      const select = screen.getByTestId('machine-select');
      fireEvent.change(select, { target: { value: 'machine-1' } });

      // Browse button should appear
      expect(screen.getByText('browse')).toBeInTheDocument();
    });
  });

  describe('Path History', () => {
    it('loads path history from localStorage on mount', () => {
      // Set path history in localStorage
      localStorage.setItem('remote-path-history-machine-1', JSON.stringify(['/path1', '/path2']));

      render(<NewSessionModal {...defaultProps} />);

      // Select remote workspace type
      fireEvent.click(screen.getByText('remoteWorkspace'));

      // Select machine
      const select = screen.getByTestId('machine-select');
      fireEvent.change(select, { target: { value: 'machine-1' } });

      // Path history should be displayed
      expect(screen.getByText('recentPaths')).toBeInTheDocument();
      expect(screen.getByText('path1')).toBeInTheDocument();
      expect(screen.getByText('path2')).toBeInTheDocument();
    });

    it('clicking history button updates path', () => {
      localStorage.setItem('remote-path-history-machine-1', JSON.stringify(['/saved-path']));

      render(<NewSessionModal {...defaultProps} />);

      // Select remote workspace type
      fireEvent.click(screen.getByText('remoteWorkspace'));

      // Select machine
      const select = screen.getByTestId('machine-select');
      fireEvent.change(select, { target: { value: 'machine-1' } });

      // Click history button
      fireEvent.click(screen.getByText('saved-path'));

      // Path should be updated (check input value)
      const input = screen.getByPlaceholderText('/root/workspace');
      expect(input).toHaveValue('/saved-path');
    });

    it('saves path to history when selected from browser', async () => {
      render(<NewSessionModal {...defaultProps} />);

      // Select remote workspace type
      fireEvent.click(screen.getByText('remoteWorkspace'));

      // Select machine
      const select = screen.getByTestId('machine-select');
      fireEvent.change(select, { target: { value: 'machine-1' } });

      // Open directory browser
      fireEvent.click(screen.getByText('browse'));

      // Select a path
      fireEvent.click(screen.getByTestId('select-path-btn'));

      // Wait for state update
      await waitFor(() => {
        const saved = localStorage.getItem('remote-path-history-machine-1');
        expect(saved).toContain('/selected/path');
      });
    });

    it('displays at most 5 history items', () => {
      // Set more than 5 paths
      const paths = ['/path1', '/path2', '/path3', '/path4', '/path5', '/path6', '/path7'];
      localStorage.setItem('remote-path-history-machine-1', JSON.stringify(paths));

      render(<NewSessionModal {...defaultProps} />);

      // Select remote workspace type
      fireEvent.click(screen.getByText('remoteWorkspace'));

      // Select machine
      const select = screen.getByTestId('machine-select');
      fireEvent.change(select, { target: { value: 'machine-1' } });

      // Should only show first 5
      const historyButtons = screen.getAllByText(/path\d/);
      expect(historyButtons.length).toBeLessThanOrEqual(5);
    });
  });

  describe('Directory Browser Modal', () => {
    it('passes correct machineId to DirectoryBrowserModal', () => {
      render(<NewSessionModal {...defaultProps} />);

      // Select remote workspace type
      fireEvent.click(screen.getByText('remoteWorkspace'));

      // Select machine
      const select = screen.getByTestId('machine-select');
      fireEvent.change(select, { target: { value: 'machine-1' } });

      // Open directory browser
      fireEvent.click(screen.getByText('browse'));

      // Check machineId is passed
      expect(screen.getByTestId('browser-machine-id')).toHaveTextContent('machine-1');
    });

    it('updates path when path is selected from browser', async () => {
      render(<NewSessionModal {...defaultProps} />);

      // Select remote workspace type
      fireEvent.click(screen.getByText('remoteWorkspace'));

      // Select machine
      const select = screen.getByTestId('machine-select');
      fireEvent.change(select, { target: { value: 'machine-1' } });

      // Open directory browser
      fireEvent.click(screen.getByText('browse'));

      // Verify modal opened
      expect(screen.getByTestId('directory-browser-modal')).toBeInTheDocument();

      // Select a path
      fireEvent.click(screen.getByTestId('select-path-btn'));

      // Path should be updated (verify the core functionality)
      const input = screen.getByPlaceholderText('/root/workspace');
      expect(input).toHaveValue('/selected/path');

      // Verify path saved to history
      await waitFor(() => {
        const saved = localStorage.getItem('remote-path-history-machine-1');
        expect(saved).toContain('/selected/path');
      });
    });
  });
});
