/**
 * Tests for ProjectEditModal Component
 * Issue #3064: Project edit functionality
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

// Mock API
vi.mock('@/api/projects', () => ({
  updateProject: vi.fn(),
  getProject: vi.fn(),
}));

// Mock store
vi.mock('@/store', () => ({
  useLanguage: () => 'en',
}));

// Mock i18n
vi.mock('@/i18n', () => ({
  t: (key: string) => {
    const translations: Record<string, string> = {
      loading: 'Loading...',
      editProject: 'Edit Project',
      projectName: 'Project Name',
      projectNameRequired: 'Project name is required',
      projectNameMaxLength: 'Project name must be less than 255 characters',
      projectNameInvalidChars: 'Invalid characters',
      projectNamePlaceholder: 'Enter project name',
      projectNameHint: 'Max 255 chars',
      projectDescription: 'Description',
      projectDescriptionPlaceholder: 'Enter description',
      projectDescriptionMaxLength: 'Max 1000 chars',
      projectShared: 'Shared',
      projectSharedHint: 'Share hint',
      projectUpdated: 'Project updated successfully',
      projectEditNoPermission: 'No permission',
      projectEditNotFound: 'Not found',
      projectShareAsyncProcessing: 'Processing',
      cancel: 'Cancel',
      save: 'Save',
    };
    return translations[key] || key;
  },
}));

// Mock common components
vi.mock('@/components/common', () => ({
  Modal: ({ children, isOpen, title }: { children: React.ReactNode; isOpen: boolean; title: string }) =>
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
  TextInput: ({ label, value, onChange, error, placeholder, hint }: any) => (
    <div>
      <label>{label}</label>
      <input
        data-testid="input-name"
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
      />
      {hint && <small>{hint}</small>}
      {error && <span data-testid="error-name">{error}</span>}
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
        data-testid="switch-shared"
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
      />
    </div>
  ),
  Loading: ({ text }: any) => <div data-testid="loading">{text}</div>,
  useToast: () => ({
    success: vi.fn(),
    error: vi.fn(),
  }),
}));

import { ProjectEditModal } from './ProjectEditModal';
import { updateProject, getProject } from '@/api/projects';

const mockProject = {
  project_id: 1,
  project_path: '/test/path/project1',
  project_name: 'Project 1',
  total_users: 5,
  total_sessions: 10,
  total_tokens: 1000,
  total_requests: 50,
  total_duration_seconds: 3600,
  total_duration_hours: 1,
  first_access: '2026-01-01T00:00:00Z',
  last_access: '2026-08-01T00:00:00Z',
  user_stats: [],
};

describe('ProjectEditModal', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    (getProject as any).mockResolvedValue({
      success: true,
      project: {
        id: 1,
        name: 'Project 1',
        description: 'Test description',
        is_shared: false,
        created_by: 1,
        created_at: '',
        updated_at: '',
        is_active: true,
        path: '/test/path/project1',
      },
    });
  });

  it('renders modal when open with project', async () => {
    render(
      <ProjectEditModal
        isOpen={true}
        onClose={vi.fn()}
        onSuccess={vi.fn()}
        project={mockProject as any}
      />
    );

    await waitFor(() => {
      expect(screen.getByTestId('modal')).toBeInTheDocument();
    });
  });

  it('does not render when project is null', () => {
    const { container } = render(
      <ProjectEditModal
        isOpen={true}
        onClose={vi.fn()}
        onSuccess={vi.fn()}
        project={null}
      />
    );
    expect(container.querySelector('[data-testid="modal"]')).not.toBeInTheDocument();
  });

  it('loads project details on open', async () => {
    render(
      <ProjectEditModal
        isOpen={true}
        onClose={vi.fn()}
        onSuccess={vi.fn()}
        project={mockProject as any}
      />
    );

    await waitFor(() => {
      expect(getProject).toHaveBeenCalledWith(1);
    });
  });

  it('shows validation error for empty name', async () => {
    render(
      <ProjectEditModal
        isOpen={true}
        onClose={vi.fn()}
        onSuccess={vi.fn()}
        project={mockProject as any}
      />
    );

    await waitFor(() => {
      expect(screen.getByTestId('input-name')).toBeInTheDocument();
    });

    const nameInput = screen.getByTestId('input-name');
    fireEvent.change(nameInput, { target: { value: '' } });

    const submitButton = screen.getByTestId('btn-primary');
    fireEvent.click(submitButton);

    await waitFor(() => {
      expect(screen.getByTestId('error-name')).toBeInTheDocument();
    });

    expect(updateProject).not.toHaveBeenCalled();
  });

  it('calls updateProject on valid submit', async () => {
    (updateProject as any).mockResolvedValue({ success: true, project: {} });

    render(
      <ProjectEditModal
        isOpen={true}
        onClose={vi.fn()}
        onSuccess={vi.fn()}
        project={mockProject as any}
      />
    );

    await waitFor(() => {
      expect(screen.getByTestId('input-name')).toBeInTheDocument();
    });

    const submitButton = screen.getByTestId('btn-primary');
    fireEvent.click(submitButton);

    await waitFor(() => {
      expect(updateProject).toHaveBeenCalledWith(1, {
        name: 'Project 1',
        description: 'Test description',
        is_shared: false,
      });
    });
  });

  it('trims whitespace from name on submit', async () => {
    (updateProject as any).mockResolvedValue({ success: true, project: {} });

    render(
      <ProjectEditModal
        isOpen={true}
        onClose={vi.fn()}
        onSuccess={vi.fn()}
        project={mockProject as any}
      />
    );

    await waitFor(() => {
      expect(screen.getByTestId('input-name')).toBeInTheDocument();
    });

    const nameInput = screen.getByTestId('input-name');
    fireEvent.change(nameInput, { target: { value: '  Project 1  ' } });

    const submitButton = screen.getByTestId('btn-primary');
    fireEvent.click(submitButton);

    await waitFor(() => {
      expect(updateProject).toHaveBeenCalledWith(1, {
        name: 'Project 1',
        description: 'Test description',
        is_shared: false,
      });
    });
  });
});
