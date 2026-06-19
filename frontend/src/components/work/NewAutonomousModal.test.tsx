/* eslint-disable @typescript-eslint/no-explicit-any */
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { vi, describe, it, expect, beforeEach } from 'vitest';

const mutateAsyncMock = vi.fn();

vi.mock('@/store', () => ({
  useLanguage: () => 'en',
}));

vi.mock('@/i18n', () => ({
  t: (key: string) => key,
}));

vi.mock('@/hooks/useAutonomous', () => ({
  useCreateWorkflow: () => ({
    mutateAsync: mutateAsyncMock,
    isPending: false,
  }),
  useAvailableTools: () => ({
    data: {
      tools: [
        { id: 'claude-code', name: 'Claude Code' },
        { id: 'zcode', name: 'ZCode' },
      ],
    },
  }),
  useAvailableModels: () => ({
    data: {
      models: [],
    },
  }),
}));

vi.mock('@/components/common', () => ({
  Modal: ({ isOpen, title, footer, children }: any) =>
    isOpen ? (
      <div data-testid="modal">
        <h5>{title}</h5>
        {children}
        <div>{footer}</div>
      </div>
    ) : null,
  Button: ({ onClick, disabled, children, variant }: any) => (
    <button onClick={onClick} disabled={disabled} data-testid={`btn-${variant ?? 'primary'}`}>
      {children}
    </button>
  ),
}));

vi.mock('./RemoteMachineSelector', () => ({
  RemoteMachineSelector: ({ onSelectMachine, selectedMachineId }: any) => (
    <div data-testid="remote-machine-selector" data-selected-machine={selectedMachineId}>
      <button
        data-testid="select-remote-machine-1"
        onClick={() =>
          onSelectMachine('machine-1', {
            machine_id: 'machine-1',
            machine_name: 'Windows Box',
            os_type: 'windows',
            work_dir: 'C:\\workspace',
          })
        }
      >
        Select Machine 1
      </button>
      <button
        data-testid="select-remote-machine-2"
        onClick={() =>
          onSelectMachine('machine-2', {
            machine_id: 'machine-2',
            machine_name: 'Linux Box',
            os_type: 'linux',
            work_dir: '/srv/workspace',
          })
        }
      >
        Select Machine 2
      </button>
    </div>
  ),
}));

vi.mock('./LocalDirectoryBrowserModal', () => ({
  LocalDirectoryBrowserModal: ({ isOpen, onSelectPath }: any) =>
    isOpen ? (
      <div data-testid="local-directory-browser-modal">
        <button onClick={() => onSelectPath('/Users/test/project')}>Choose Local Path</button>
      </div>
    ) : null,
}));

vi.mock('./DirectoryBrowserModal', () => ({
  DirectoryBrowserModal: ({ isOpen, osType, onSelectPath }: any) =>
    isOpen ? (
      <div data-testid="remote-directory-browser-modal">
        <span data-testid="remote-os-type">{osType}</span>
        <button onClick={() => onSelectPath('C:\\workspace\\repo')}>Choose Remote Path</button>
      </div>
    ) : null,
}));

import { NewAutonomousModal } from './NewAutonomousModal';

describe('NewAutonomousModal', () => {
  const defaultProps = {
    show: true,
    onClose: vi.fn(),
    onCreated: vi.fn(),
  };

  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    mutateAsyncMock.mockResolvedValue({ workflow: { id: 'wf-1' } });
  });

  it('shows a browse button for local project paths', () => {
    render(<NewAutonomousModal {...defaultProps} />);

    expect(screen.getByText('browse')).toBeInTheDocument();
  });

  it('uses updated default branch and review round values', () => {
    render(<NewAutonomousModal {...defaultProps} />);

    const selects = screen.getAllByRole('combobox');
    const sliders = screen.getAllByRole('slider');

    expect(selects[2]).toHaveValue('worktree');
    expect(sliders[0]).toHaveValue('2');
    expect(sliders[1]).toHaveValue('3');
    expect(screen.getByText('autoMaxPlanRounds: 2')).toBeInTheDocument();
    expect(screen.getByText('autoMaxPRReviewRounds: 3')).toBeInTheDocument();
  });

  it('opens local directory browser and updates the project path', () => {
    render(<NewAutonomousModal {...defaultProps} />);

    fireEvent.click(screen.getByText('browse'));
    expect(screen.getByTestId('local-directory-browser-modal')).toBeInTheDocument();

    fireEvent.click(screen.getByText('Choose Local Path'));
    expect(screen.getByPlaceholderText('autoProjectPathPlaceholder')).toHaveValue(
      '/Users/test/project'
    );
    expect(localStorage.getItem('local-last-project-path')).toBe('/Users/test/project');
  });

  it('prefills local project path from the last confirmed selection when reopened', () => {
    localStorage.setItem('local-last-project-path', '/Users/saved/project');

    const { rerender } = render(<NewAutonomousModal {...defaultProps} show={false} />);
    rerender(<NewAutonomousModal {...defaultProps} show={true} />);

    expect(screen.getByPlaceholderText('autoProjectPathPlaceholder')).toHaveValue(
      '/Users/saved/project'
    );
  });

  it('does not overwrite remembered local path with an unconfirmed manual input', () => {
    localStorage.setItem('local-last-project-path', '/Users/saved/project');

    render(<NewAutonomousModal {...defaultProps} />);

    const input = screen.getByPlaceholderText('autoProjectPathPlaceholder');
    fireEvent.change(input, { target: { value: '/Users/typing/project' } });

    expect(localStorage.getItem('local-last-project-path')).toBe('/Users/saved/project');
  });

  it('saves the final local path after successful task creation', async () => {
    render(<NewAutonomousModal {...defaultProps} />);

    fireEvent.change(screen.getByPlaceholderText('autoRequirementsPlaceholder'), {
      target: { value: 'Build a feature' },
    });
    fireEvent.change(screen.getByPlaceholderText('autoProjectPathPlaceholder'), {
      target: { value: '/Users/final/project' },
    });

    fireEvent.click(screen.getByText('autoCreateTask'));

    await waitFor(() => {
      expect(mutateAsyncMock).toHaveBeenCalledTimes(1);
    });
    expect(localStorage.getItem('local-last-project-path')).toBe('/Users/final/project');
  });

  it('disables remote browse until a machine is selected', () => {
    render(<NewAutonomousModal {...defaultProps} />);

    fireEvent.click(screen.getByText('autoRemoteWorkspace'));
    expect(screen.getByText('browse')).toBeDisabled();
  });

  it('opens remote directory browser after machine selection', () => {
    render(<NewAutonomousModal {...defaultProps} />);

    fireEvent.click(screen.getByText('autoRemoteWorkspace'));
    fireEvent.click(screen.getByTestId('select-remote-machine-1'));

    const browseButton = screen.getByText('browse');
    expect(browseButton).not.toBeDisabled();

    fireEvent.click(browseButton);
    expect(screen.getByTestId('remote-directory-browser-modal')).toBeInTheDocument();
    expect(screen.getByTestId('remote-os-type')).toHaveTextContent('windows');

    fireEvent.click(screen.getByText('Choose Remote Path'));
    expect(screen.getByPlaceholderText('autoProjectPathPlaceholder')).toHaveValue(
      'C:\\workspace\\repo'
    );
    expect(localStorage.getItem('remote-last-project-path-machine-1')).toBe('C:\\workspace\\repo');
  });

  it('prefills remote project path from the last confirmed machine-specific selection', () => {
    localStorage.setItem('remote-last-project-path-machine-1', 'C:\\saved\\repo');

    render(<NewAutonomousModal {...defaultProps} />);

    fireEvent.click(screen.getByText('autoRemoteWorkspace'));
    fireEvent.click(screen.getByTestId('select-remote-machine-1'));

    expect(screen.getByPlaceholderText('autoProjectPathPlaceholder')).toHaveValue(
      'C:\\saved\\repo'
    );
  });

  it('falls back to machine work_dir when the remote machine has no remembered path', () => {
    render(<NewAutonomousModal {...defaultProps} />);

    fireEvent.click(screen.getByText('autoRemoteWorkspace'));
    fireEvent.click(screen.getByTestId('select-remote-machine-2'));

    expect(screen.getByPlaceholderText('autoProjectPathPlaceholder')).toHaveValue('/srv/workspace');
  });

  it('replaces the current path when switching to another remote machine', () => {
    render(<NewAutonomousModal {...defaultProps} />);

    fireEvent.click(screen.getByText('autoRemoteWorkspace'));
    fireEvent.click(screen.getByTestId('select-remote-machine-1'));
    fireEvent.change(screen.getByPlaceholderText('autoProjectPathPlaceholder'), {
      target: { value: 'C:\\custom\\repo' },
    });

    fireEvent.click(screen.getByTestId('select-remote-machine-2'));

    expect(screen.getByPlaceholderText('autoProjectPathPlaceholder')).toHaveValue('/srv/workspace');
  });

  it('uses the new machine remembered path when switching remote machines', () => {
    localStorage.setItem('remote-last-project-path-machine-2', '/saved/machine-2-repo');

    render(<NewAutonomousModal {...defaultProps} />);

    fireEvent.click(screen.getByText('autoRemoteWorkspace'));
    fireEvent.click(screen.getByTestId('select-remote-machine-1'));
    fireEvent.change(screen.getByPlaceholderText('autoProjectPathPlaceholder'), {
      target: { value: 'C:\\custom\\repo' },
    });

    fireEvent.click(screen.getByTestId('select-remote-machine-2'));

    expect(screen.getByPlaceholderText('autoProjectPathPlaceholder')).toHaveValue(
      '/saved/machine-2-repo'
    );
  });

  it('preserves the current path when re-selecting the same remote machine', () => {
    render(<NewAutonomousModal {...defaultProps} />);

    fireEvent.click(screen.getByText('autoRemoteWorkspace'));
    fireEvent.click(screen.getByTestId('select-remote-machine-1'));
    fireEvent.change(screen.getByPlaceholderText('autoProjectPathPlaceholder'), {
      target: { value: 'C:\\custom\\repo' },
    });

    fireEvent.click(screen.getByTestId('select-remote-machine-1'));

    expect(screen.getByPlaceholderText('autoProjectPathPlaceholder')).toHaveValue(
      'C:\\custom\\repo'
    );
  });

  it('saves the final remote path after successful task creation', async () => {
    render(<NewAutonomousModal {...defaultProps} />);

    fireEvent.click(screen.getByText('autoRemoteWorkspace'));
    fireEvent.click(screen.getByTestId('select-remote-machine-1'));
    fireEvent.change(screen.getByPlaceholderText('autoRequirementsPlaceholder'), {
      target: { value: 'Build a remote feature' },
    });
    fireEvent.change(screen.getByPlaceholderText('autoProjectPathPlaceholder'), {
      target: { value: 'C:\\final\\repo' },
    });

    fireEvent.click(screen.getByText('autoCreateTask'));

    await waitFor(() => {
      expect(mutateAsyncMock).toHaveBeenCalledTimes(1);
    });
    expect(localStorage.getItem('remote-last-project-path-machine-1')).toBe('C:\\final\\repo');
  });

  it('does not persist path memory in new project mode', async () => {
    render(<NewAutonomousModal {...defaultProps} />);

    fireEvent.click(screen.getByLabelText('autoNewProject'));
    fireEvent.change(screen.getByPlaceholderText('autoRequirementsPlaceholder'), {
      target: { value: 'Create a new repo' },
    });
    fireEvent.change(screen.getByPlaceholderText('autoRepoNamePlaceholder'), {
      target: { value: 'my-new-project' },
    });

    fireEvent.click(screen.getByText('autoCreateTask'));

    await waitFor(() => {
      expect(mutateAsyncMock).toHaveBeenCalledTimes(1);
    });
    expect(localStorage.getItem('local-last-project-path')).toBeNull();
  });
});
