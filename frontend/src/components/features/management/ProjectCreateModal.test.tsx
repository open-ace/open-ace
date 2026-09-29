/**
 * Tests for ProjectCreateModal Component
 * Issue #3372: Project management page create project functionality
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

// Mock API
vi.mock('@/api/projects', () => ({
  createProject: vi.fn(),
}));

// Mock store
vi.mock('@/store', () => ({
  useLanguage: () => 'en',
}));

// Mock i18n
vi.mock('@/i18n', () => ({
  t: (key: string) => {
    const translations: Record<string, string> = {
      createProject: 'Create Project',
      projectCreated: 'Project created successfully',
      projectAlreadyExists: 'Project already exists',
      projectCreateNoPermission: 'No permission',
      projectCreateFailed: 'Failed to create project',
      projectPath: 'Project Path',
      projectPathRequired: 'Project path is required',
      projectPathAbsolute: 'Project path must be absolute',
      projectPathPlaceholder: 'Enter absolute path',
      projectCreatePathHint: 'Absolute path for the project directory',
      projectName: 'Project Name',
      projectNameMaxLength: 'Project name must be less than 255 characters',
      projectNameInvalidChars: 'Invalid characters',
      projectNamePlaceholder: 'Enter project name',
      projectNameHint: 'Max 255 chars',
      projectDescription: 'Description',
      projectDescriptionPlaceholder: 'Enter description',
      projectDescriptionMaxLength: 'Max 1000 chars',
      projectShared: 'Shared',
      projectSharedHint: 'Share hint',
      projectCreateDir: 'Create directory if not exists',
      projectCreateDirHint: 'Auto-create directory',
      cancel: 'Cancel',
    };
    return translations[key] || key;
  },
}));

// Mock common components
vi.mock('@/components/common', () => ({
  Modal: ({
    children,
    isOpen,
    title,
  }: {
    children: React.ReactNode;
    isOpen: boolean;
    title: string;
  }) =>
    isOpen ? (
      <div data-testid="modal" aria-label={title}>
        {children}
      </div>
    ) : null,
  Button: ({ children, onClick, disabled, loading, type, variant }: any) => (
    <button
      data-testid={`btn-${variant || 'default'}`}
      onClick={onClick}
      disabled={disabled || loading}
      type={type}
    >
      {children}
    </button>
  ),
  TextInput: ({ label, value, onChange, error, placeholder, hint, required }: any) => (
    <div>
      <label>{label}</label>
      <input
        data-testid={`input-${label.toLowerCase().replace(/\s+/g, '-')}`}
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        required={required}
      />
      {hint && <small>{hint}</small>}
      {error && (
        <span data-testid={`error-${label.toLowerCase().replace(/\s+/g, '-')}`}>{error}</span>
      )}
    </div>
  ),
  Textarea: ({ label, value, onChange, error, placeholder, maxLength, showCount }: any) => (
    <div>
      <label>{label}</label>
      <textarea
        data-testid="textarea-description"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        maxLength={maxLength}
      />
      {showCount && maxLength && (
        <small>
          {value.length}/{maxLength}
        </small>
      )}
      {error && <span data-testid="error-description">{error}</span>}
    </div>
  ),
  Switch: ({ label, checked, onChange }: any) => (
    <div>
      <label>{label}</label>
      <input
        data-testid={`switch-${label.toLowerCase().replace(/\s+/g, '-')}`}
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
      />
    </div>
  ),
  useToast: () => ({
    success: vi.fn(),
    error: vi.fn(),
    warning: vi.fn(),
  }),
}));

import { ProjectCreateModal } from './ProjectCreateModal';
import { createProject } from '@/api/projects';

describe('ProjectCreateModal', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders modal when open', () => {
    render(<ProjectCreateModal isOpen={true} onClose={vi.fn()} onSuccess={vi.fn()} />);
    expect(screen.getByTestId('modal')).toBeInTheDocument();
  });

  it('does not render when closed', () => {
    const { container } = render(
      <ProjectCreateModal isOpen={false} onClose={vi.fn()} onSuccess={vi.fn()} />
    );
    expect(container.querySelector('[data-testid="modal"]')).not.toBeInTheDocument();
  });

  it('shows validation error for empty path', async () => {
    render(<ProjectCreateModal isOpen={true} onClose={vi.fn()} onSuccess={vi.fn()} />);

    const submitButton = screen.getByTestId('btn-primary');
    fireEvent.click(submitButton);

    await waitFor(() => {
      expect(screen.getByTestId('error-project-path')).toBeInTheDocument();
    });

    expect(createProject).not.toHaveBeenCalled();
  });

  it('shows validation error for non-absolute path', async () => {
    render(<ProjectCreateModal isOpen={true} onClose={vi.fn()} onSuccess={vi.fn()} />);

    const pathInput = screen.getByTestId('input-project-path');
    fireEvent.change(pathInput, { target: { value: 'relative/path' } });

    const submitButton = screen.getByTestId('btn-primary');
    fireEvent.click(submitButton);

    await waitFor(() => {
      expect(screen.getByTestId('error-project-path')).toBeInTheDocument();
    });

    expect(createProject).not.toHaveBeenCalled();
  });

  it('calls createProject on valid submit', async () => {
    (createProject as any).mockResolvedValue({
      success: true,
      project: { id: 1, path: '/test/path' },
      dir_created: true,
    });

    render(<ProjectCreateModal isOpen={true} onClose={vi.fn()} onSuccess={vi.fn()} />);

    const pathInput = screen.getByTestId('input-project-path');
    fireEvent.change(pathInput, { target: { value: '/test/path' } });

    const submitButton = screen.getByTestId('btn-primary');
    fireEvent.click(submitButton);

    await waitFor(() => {
      expect(createProject).toHaveBeenCalledWith({
        path: '/test/path',
        name: undefined,
        description: undefined,
        is_shared: false,
        create_dir: true,
      });
    });
  });

  it('calls onSuccess and onClose after successful creation', async () => {
    (createProject as any).mockResolvedValue({
      success: true,
      project: { id: 1, path: '/test/path' },
      dir_created: true,
    });

    const onSuccess = vi.fn();
    const onClose = vi.fn();

    render(<ProjectCreateModal isOpen={true} onClose={onClose} onSuccess={onSuccess} />);

    const pathInput = screen.getByTestId('input-project-path');
    fireEvent.change(pathInput, { target: { value: '/test/path' } });

    const submitButton = screen.getByTestId('btn-primary');
    fireEvent.click(submitButton);

    await waitFor(() => {
      expect(onSuccess).toHaveBeenCalled();
      expect(onClose).toHaveBeenCalled();
    });
  });

  it('shows error toast on 409 conflict', async () => {
    (createProject as any).mockRejectedValue({
      status: 409,
      message: 'Project already exists',
    });

    render(<ProjectCreateModal isOpen={true} onClose={vi.fn()} onSuccess={vi.fn()} />);

    const pathInput = screen.getByTestId('input-project-path');
    fireEvent.change(pathInput, { target: { value: '/test/existing' } });

    const submitButton = screen.getByTestId('btn-primary');
    fireEvent.click(submitButton);

    await waitFor(() => {
      expect(createProject).toHaveBeenCalled();
    });
  });

  it('handles name validation with invalid characters', async () => {
    render(<ProjectCreateModal isOpen={true} onClose={vi.fn()} onSuccess={vi.fn()} />);

    const pathInput = screen.getByTestId('input-project-path');
    fireEvent.change(pathInput, { target: { value: '/test/path' } });

    const nameInput = screen.getByTestId('input-project-name');
    fireEvent.change(nameInput, { target: { value: 'test<project>' } });

    const submitButton = screen.getByTestId('btn-primary');
    fireEvent.click(submitButton);

    await waitFor(() => {
      expect(screen.getByTestId('error-project-name')).toBeInTheDocument();
    });

    expect(createProject).not.toHaveBeenCalled();
  });
});
