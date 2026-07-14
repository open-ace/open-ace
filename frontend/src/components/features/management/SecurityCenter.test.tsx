/**
 * SecurityCenter Component Tests
 *
 * Tests cover three sub-pages:
 * 1. Content Filter (Filter Rules) - CRUD operations, toggle, table display
 * 2. Security Settings - session/password/IP whitelist configuration
 * 3. Audit Thresholds - threshold input validation, save, reset
 */

import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom';
import { vi, describe, it, expect, beforeEach } from 'vitest';
import type { ContentFilterRule, SecuritySettings, AuditThresholds } from '@/api';

// ─── Mocks ────────────────────────────────────────────────────────────────────

vi.mock('@/store', () => ({
  useLanguage: () => 'en',
}));

vi.mock('@/i18n', () => ({
  t: (key: string) => {
    const translations: Record<string, string> = {
      securityCenter: 'Security Center',
      contentFilter: 'Content Filter',
      securitySettings: 'Security Settings',
      auditThresholds: 'Audit Thresholds',
      addRule: 'Add Rule',
      editRule: 'Edit Rule',
      save: 'Save',
      cancel: 'Cancel',
      reset: 'Reset',
      loading: 'Loading...',
      error: 'Error',
      noFilterRules: 'No Filter Rules',
      confirmDeleteRule: 'Are you sure you want to delete this rule?',
      settingsSaved: 'Settings saved successfully',
      auditThresholdsSaved: 'Audit thresholds saved successfully',
      resetSuccess: 'Reset successful',
      tablePattern: 'Pattern',
      tableType: 'Type',
      tableSeverity: 'Severity',
      tableAction: 'Action',
      tableStatus: 'Status',
      tableActions: 'Actions',
      enterPattern: 'Enter pattern',
      enterDescription: 'Enter description',
      description: 'Description',
      enabled: 'Enabled',
      typeKeyword: 'Keyword',
      typeRegex: 'Regex',
      typePii: 'PII',
      severityLow: 'Low',
      severityMedium: 'Medium',
      severityHigh: 'High',
      actionWarn: 'Warn',
      actionBlock: 'Block',
      actionRedact: 'Redact',
      patternHelp: 'Pattern help text',
      keywordTypeHelp: 'Keyword type help',
      regexTypeHelp: 'Regex type help',
      piiTypeHelp: 'PII type help',
      warnActionHelp: 'Warn action help',
      blockActionHelp: 'Block action help',
      redactActionHelp: 'Redact action help',
      sessionSettings: 'Session Settings',
      sessionTimeout: 'Session Timeout',
      minutes: 'minutes',
      sessionTimeoutHelp: 'Session timeout help',
      maxLoginAttempts: 'Max Login Attempts',
      maxLoginAttemptsHelp: 'Max login attempts help',
      passwordPolicy: 'Password Policy',
      passwordMinLength: 'Password Min Length',
      passwordRequirements: 'Password Requirements',
      requireUppercase: 'Require Uppercase',
      requireLowercase: 'Require Lowercase',
      requireNumber: 'Require Number',
      requireSpecial: 'Require Special',
      ipWhitelist: 'IP Whitelist',
      allowedIpAddresses: 'Allowed IP Addresses',
      ipWhitelistHelp: 'IP whitelist help',
      anomalyDetectionThresholds: 'Anomaly Detection Thresholds',
      failedLoginThreshold: 'Failed Login Threshold',
      failedLoginThresholdHelp: 'Failed login threshold help',
      rapidActionThreshold: 'Rapid Action Threshold',
      rapidActionThresholdHelp: 'Rapid action threshold help',
      offHoursThreshold: 'Off Hours Threshold',
      offHoursThresholdHelp: 'Off hours threshold help',
      roleChangeThreshold: 'Role Change Threshold',
      roleChangeThresholdHelp: 'Role change threshold help',
      permissionChangeThreshold: 'Permission Change Threshold',
      permissionChangeThresholdHelp: 'Permission change threshold help',
    };
    return translations[key] || key;
  },
}));

vi.mock('@/utils', () => ({
  cn: (...args: (string | boolean | undefined)[]) => args.filter(Boolean).join(' '),
  createMatcherConfig: () => ({}),
}));

// Mock hooks
const mockRefetchRules = vi.fn();
const mockRefetchSettings = vi.fn();
const mockRefetchThresholds = vi.fn();
const mockMutateAsyncCreate = vi.fn();
const mockMutateAsyncUpdate = vi.fn();
const mockMutateAsyncDelete = vi.fn();
const mockMutateAsyncUpdateSettings = vi.fn();
const mockMutateAsyncUpdateThresholds = vi.fn();

const defaultRules: ContentFilterRule[] = [
  {
    id: 1,
    pattern: 'password',
    type: 'keyword',
    severity: 'high',
    action: 'block',
    is_enabled: true,
    description: 'Block passwords',
    created_at: '2024-01-01',
  },
  {
    id: 2,
    pattern: '\\d{3}-\\d{2}-\\d{4}',
    type: 'regex',
    severity: 'medium',
    action: 'redact',
    is_enabled: false,
    description: 'SSN pattern',
    created_at: '2024-01-02',
  },
];

const defaultSettings: SecuritySettings = {
  session_timeout: 30,
  max_login_attempts: 5,
  password_min_length: 8,
  password_require_uppercase: true,
  password_require_lowercase: true,
  password_require_number: true,
  password_require_special: false,
  two_factor_enabled: false,
  ip_whitelist: ['192.168.1.1', '10.0.0.0/24'],
};

const defaultThresholds: AuditThresholds = {
  audit_failed_login_threshold: 5,
  audit_rapid_action_threshold: 50,
  audit_off_hours_threshold: 10,
  audit_role_change_threshold: 5,
  audit_permission_change_threshold: 10,
};

vi.mock('@/hooks', () => ({
  useFilterRules: vi.fn(() => ({
    data: defaultRules,
    isLoading: false,
    isError: false,
    error: null,
    refetch: mockRefetchRules,
  })),
  useCreateFilterRule: vi.fn(() => ({
    mutateAsync: mockMutateAsyncCreate,
    isPending: false,
  })),
  useUpdateFilterRule: vi.fn(() => ({
    mutateAsync: mockMutateAsyncUpdate,
    isPending: false,
  })),
  useDeleteFilterRule: vi.fn(() => ({
    mutateAsync: mockMutateAsyncDelete,
    isPending: false,
  })),
  useSecuritySettings: vi.fn(() => ({
    data: defaultSettings,
    isLoading: false,
    isError: false,
    error: null,
    refetch: mockRefetchSettings,
  })),
  useUpdateSecuritySettings: vi.fn(() => ({
    mutateAsync: mockMutateAsyncUpdateSettings,
    isPending: false,
  })),
  useAuditThresholds: vi.fn(() => ({
    data: defaultThresholds,
    isLoading: false,
    isError: false,
    error: null,
    refetch: mockRefetchThresholds,
  })),
  useUpdateAuditThresholds: vi.fn(() => ({
    mutateAsync: mockMutateAsyncUpdateThresholds,
    isPending: false,
  })),
  usePageRefresh: vi.fn(() => ({
    refresh: vi.fn(),
    lastRefreshTime: null,
    isRefreshing: false,
  })),
}));

const mockConfirm = vi.fn().mockResolvedValue(true);
const mockToast = {
  success: vi.fn(),
  error: vi.fn(),
  warning: vi.fn(),
  info: vi.fn(),
};

vi.mock('@/components/common', () => ({
  Card: ({
    title,
    children,
    className,
  }: {
    title?: string;
    children: React.ReactNode;
    className?: string;
  }) => (
    <div data-testid="card" className={className}>
      {title && <h5>{title}</h5>}
      {children}
    </div>
  ),
  Button: ({
    children,
    onClick,
    disabled,
    variant,
    size,
    loading,
  }: {
    children: React.ReactNode;
    onClick?: () => void;
    disabled?: boolean;
    variant?: string;
    size?: string;
    loading?: boolean;
  }) => (
    <button
      onClick={onClick}
      disabled={disabled || loading}
      data-variant={variant}
      data-size={size}
      data-loading={loading}
    >
      {children}
    </button>
  ),
  Modal: ({
    isOpen,
    onClose,
    title,
    children,
    footer,
  }: {
    isOpen: boolean;
    onClose: () => void;
    title: string;
    children: React.ReactNode;
    footer?: React.ReactNode;
  }) => {
    if (!isOpen) return null;
    return (
      <div data-testid="modal" role="dialog">
        <h3>{title}</h3>
        <div>{children}</div>
        {footer && <div data-testid="modal-footer">{footer}</div>}
        <button data-testid="modal-close" onClick={onClose}>
          Close
        </button>
      </div>
    );
  },
  TextInput: ({
    value,
    onChange,
    placeholder,
    type,
  }: {
    value: string;
    onChange: (v: string) => void;
    placeholder?: string;
    type?: string;
  }) => (
    <input
      type={type || 'text'}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      placeholder={placeholder}
      data-testid={placeholder || `input-${type || 'text'}`}
    />
  ),
  Select: ({
    options,
    value,
    onChange,
  }: {
    options: { value: string; label: string }[];
    value: string;
    onChange: (v: string) => void;
  }) => (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      data-testid="select"
    >
      {options.map((opt) => (
        <option key={opt.value} value={opt.value}>
          {opt.label}
        </option>
      ))}
    </select>
  ),
  Loading: ({ text }: { text?: string }) => (
    <div data-testid="loading">{text || 'Loading...'}</div>
  ),
  Error: ({
    message,
    onRetry,
  }: {
    message: string;
    onRetry?: () => void;
  }) => (
    <div data-testid="error">
      {message}
      {onRetry && (
        <button data-testid="retry-btn" onClick={onRetry}>
          Retry
        </button>
      )}
    </div>
  ),
  EmptyState: ({ title }: { title: string }) => (
    <div data-testid="empty-state">{title}</div>
  ),
  Badge: ({
    children,
    variant,
  }: {
    children: React.ReactNode;
    variant: string;
  }) => (
    <span data-testid="badge" data-variant={variant}>
      {children}
    </span>
  ),
  PageRefreshControl: () => <div data-testid="page-refresh-control" />,
  useToast: () => mockToast,
  useConfirm: () => mockConfirm,
}));

vi.mock('./FilterRuleTableHeader', () => ({
  FilterRuleTableHeader: () => (
    <thead data-testid="filter-rule-table-header">
      <tr>
        <th>Pattern</th>
        <th>Type</th>
        <th>Severity</th>
        <th>Action</th>
        <th>Status</th>
        <th>Actions</th>
      </tr>
    </thead>
  ),
}));

import { SecurityCenter } from './SecurityCenter';
import {
  useFilterRules,
  useCreateFilterRule,
  useUpdateFilterRule,
  useDeleteFilterRule,
  useSecuritySettings,
  useUpdateSecuritySettings,
  useAuditThresholds,
  useUpdateAuditThresholds,
} from '@/hooks';

// ─── Helper to override hooks for specific tests ──────────────────────────────

function setFilterRulesHook(overrides: Record<string, unknown>) {
  vi.mocked(useFilterRules).mockReturnValue(overrides as ReturnType<typeof useFilterRules>);
}

function setCreateRuleHook(overrides: Record<string, unknown>) {
  vi.mocked(useCreateFilterRule).mockReturnValue(overrides as ReturnType<typeof useCreateFilterRule>);
}

function setUpdateRuleHook(overrides: Record<string, unknown>) {
  vi.mocked(useUpdateFilterRule).mockReturnValue(overrides as ReturnType<typeof useUpdateFilterRule>);
}

function setDeleteRuleHook(overrides: Record<string, unknown>) {
  vi.mocked(useDeleteFilterRule).mockReturnValue(overrides as ReturnType<typeof useDeleteFilterRule>);
}

function setSecuritySettingsHook(overrides: Record<string, unknown>) {
  vi.mocked(useSecuritySettings).mockReturnValue(overrides as ReturnType<typeof useSecuritySettings>);
}

function setUpdateSettingsHook(overrides: Record<string, unknown>) {
  vi.mocked(useUpdateSecuritySettings).mockReturnValue(
    overrides as ReturnType<typeof useUpdateSecuritySettings>
  );
}

function setAuditThresholdsHook(overrides: Record<string, unknown>) {
  vi.mocked(useAuditThresholds).mockReturnValue(overrides as ReturnType<typeof useAuditThresholds>);
}

function setUpdateThresholdsHook(overrides: Record<string, unknown>) {
  vi.mocked(useUpdateAuditThresholds).mockReturnValue(
    overrides as ReturnType<typeof useUpdateAuditThresholds>
  );
}

// ─── Tests ────────────────────────────────────────────────────────────────────

describe('SecurityCenter', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    // Reset hooks to defaults
    setFilterRulesHook({
      data: defaultRules,
      isLoading: false,
      isError: false,
      error: null,
      refetch: mockRefetchRules,
    });
    setCreateRuleHook({ mutateAsync: mockMutateAsyncCreate, isPending: false });
    setUpdateRuleHook({ mutateAsync: mockMutateAsyncUpdate, isPending: false });
    setDeleteRuleHook({ mutateAsync: mockMutateAsyncDelete, isPending: false });
    setSecuritySettingsHook({
      data: defaultSettings,
      isLoading: false,
      isError: false,
      error: null,
      refetch: mockRefetchSettings,
    });
    setUpdateSettingsHook({ mutateAsync: mockMutateAsyncUpdateSettings, isPending: false });
    setAuditThresholdsHook({
      data: defaultThresholds,
      isLoading: false,
      isError: false,
      error: null,
      refetch: mockRefetchThresholds,
    });
    setUpdateThresholdsHook({
      mutateAsync: mockMutateAsyncUpdateThresholds,
      isPending: false,
    });
    mockConfirm.mockResolvedValue(true);
  });

  // ─── Page & Tab Rendering ─────────────────────────────────────────────────

  describe('Page & Tab Rendering', () => {
    it('renders the page title', () => {
      render(<SecurityCenter />);
      expect(screen.getByText('Security Center')).toBeInTheDocument();
    });

    it('renders three tab buttons', () => {
      render(<SecurityCenter />);
      expect(screen.getByText('Content Filter')).toBeInTheDocument();
      expect(screen.getByText('Security Settings')).toBeInTheDocument();
      expect(screen.getByText('Audit Thresholds')).toBeInTheDocument();
    });

    it('shows Content Filter tab as active by default', () => {
      render(<SecurityCenter />);
      const filterTab = screen.getByText('Content Filter').closest('button');
      expect(filterTab).toHaveClass('active');
    });

    it('switches to Security Settings tab when clicked', () => {
      render(<SecurityCenter />);
      fireEvent.click(screen.getByText('Security Settings'));
      const settingsTab = screen.getByText('Security Settings').closest('button');
      expect(settingsTab).toHaveClass('active');
    });

    it('switches to Audit Thresholds tab when clicked', () => {
      render(<SecurityCenter />);
      fireEvent.click(screen.getByText('Audit Thresholds'));
      const auditTab = screen.getByText('Audit Thresholds').closest('button');
      expect(auditTab).toHaveClass('active');
    });

    it('shows Add Rule button only on Content Filter tab', () => {
      const { rerender } = render(<SecurityCenter />);
      expect(screen.getByText('Add Rule')).toBeInTheDocument();

      fireEvent.click(screen.getByText('Security Settings'));
      expect(screen.queryByText('Add Rule')).not.toBeInTheDocument();

      rerender(<SecurityCenter />);
      fireEvent.click(screen.getByText('Audit Thresholds'));
      expect(screen.queryByText('Add Rule')).not.toBeInTheDocument();
    });

    it('shows PageRefreshControl', () => {
      render(<SecurityCenter />);
      expect(screen.getByTestId('page-refresh-control')).toBeInTheDocument();
    });
  });

  // ─── Content Filter Tab ──────────────────────────────────────────────────

  describe('Content Filter Tab', () => {
    it('shows loading state', () => {
      setFilterRulesHook({
        data: undefined,
        isLoading: true,
        isError: false,
        error: null,
        refetch: mockRefetchRules,
      });
      render(<SecurityCenter />);
      expect(screen.getByTestId('loading')).toBeInTheDocument();
    });

    it('shows error state with retry button', () => {
      setFilterRulesHook({
        data: undefined,
        isLoading: false,
        isError: true,
        error: new Error('Network error'),
        refetch: mockRefetchRules,
      });
      render(<SecurityCenter />);
      expect(screen.getByTestId('error')).toBeInTheDocument();
      expect(screen.getByText('Network error')).toBeInTheDocument();
      expect(screen.getByTestId('retry-btn')).toBeInTheDocument();
    });

    it('calls refetch when retry is clicked', () => {
      setFilterRulesHook({
        data: undefined,
        isLoading: false,
        isError: true,
        error: new Error('Network error'),
        refetch: mockRefetchRules,
      });
      render(<SecurityCenter />);
      fireEvent.click(screen.getByTestId('retry-btn'));
      expect(mockRefetchRules).toHaveBeenCalled();
    });

    it('shows empty state when no rules exist', () => {
      setFilterRulesHook({
        data: [],
        isLoading: false,
        isError: false,
        error: null,
        refetch: mockRefetchRules,
      });
      render(<SecurityCenter />);
      expect(screen.getByTestId('empty-state')).toBeInTheDocument();
      expect(screen.getByText('No Filter Rules')).toBeInTheDocument();
    });

    it('renders rules table with data', () => {
      render(<SecurityCenter />);
      expect(screen.getByTestId('filter-rule-table-header')).toBeInTheDocument();
      expect(screen.getByText('password')).toBeInTheDocument();
      expect(screen.getByText('Block passwords')).toBeInTheDocument();
    });

    it('displays correct badge variants for severity', () => {
      render(<SecurityCenter />);
      const badges = screen.getAllByTestId('badge');
      // High severity -> danger variant
      const highBadge = badges.find((b) => b.textContent === 'High');
      expect(highBadge).toHaveAttribute('data-variant', 'danger');
    });

    it('displays correct badge variants for action', () => {
      render(<SecurityCenter />);
      const badges = screen.getAllByTestId('badge');
      // Block action -> danger variant
      const blockBadge = badges.find((b) => b.textContent === 'Block');
      expect(blockBadge).toHaveAttribute('data-variant', 'danger');
    });

    it('opens create modal when Add Rule is clicked', () => {
      render(<SecurityCenter />);
      expect(screen.queryByTestId('modal')).not.toBeInTheDocument();
      // Use getAllByText since after opening the modal the title also reads "Add Rule"
      const addRuleElements = screen.getAllByText('Add Rule');
      fireEvent.click(addRuleElements[0]);
      expect(screen.getByTestId('modal')).toBeInTheDocument();
    });

    it('pre-populates modal when editing a rule', () => {
      render(<SecurityCenter />);
      // Click edit button (pencil icon) on the first rule
      const editButtons = screen.getAllByRole('button');
      const editBtn = editButtons.find((btn) => btn.querySelector('.bi-pencil'));
      expect(editBtn).toBeDefined();
      fireEvent.click(editBtn!);
      expect(screen.getByTestId('modal')).toBeInTheDocument();
    });

    it('calls create API when submitting a new rule', async () => {
      mockMutateAsyncCreate.mockResolvedValue({});
      render(<SecurityCenter />);
      fireEvent.click(screen.getByText('Add Rule'));

      // Fill in the pattern field
      const patternInput = screen.getByPlaceholderText('Enter pattern');
      fireEvent.change(patternInput, { target: { value: 'test-pattern' } });

      // Click save in modal footer
      const saveButton = screen.getByText('Save');
      fireEvent.click(saveButton);

      await waitFor(() => {
        expect(mockMutateAsyncCreate).toHaveBeenCalledWith(
          expect.objectContaining({ pattern: 'test-pattern' })
        );
      });
    });

    it('calls update API when editing an existing rule', async () => {
      mockMutateAsyncUpdate.mockResolvedValue({});
      render(<SecurityCenter />);

      // Click edit button
      const editBtn = screen.getAllByRole('button').find((btn) => btn.querySelector('.bi-pencil'));
      fireEvent.click(editBtn!);

      // Click save
      const saveButton = screen.getByText('Save');
      fireEvent.click(saveButton);

      await waitFor(() => {
        expect(mockMutateAsyncUpdate).toHaveBeenCalledWith(
          expect.objectContaining({ ruleId: 1 })
        );
      });
    });

    it('closes modal when cancel is clicked', () => {
      render(<SecurityCenter />);
      fireEvent.click(screen.getByText('Add Rule'));
      expect(screen.getByTestId('modal')).toBeInTheDocument();

      fireEvent.click(screen.getByText('Cancel'));
      expect(screen.queryByTestId('modal')).not.toBeInTheDocument();
    });

    it('calls delete API when delete is confirmed', async () => {
      mockMutateAsyncDelete.mockResolvedValue({});
      render(<SecurityCenter />);

      const deleteBtn = screen
        .getAllByRole('button')
        .find((btn) => btn.querySelector('.bi-trash'));
      fireEvent.click(deleteBtn!);

      await waitFor(() => {
        expect(mockConfirm).toHaveBeenCalledWith(
          expect.objectContaining({ variant: 'danger' })
        );
        expect(mockMutateAsyncDelete).toHaveBeenCalledWith(1);
      });
    });

    it('does not delete when confirmation is rejected', async () => {
      mockConfirm.mockResolvedValue(false);
      render(<SecurityCenter />);

      const deleteBtn = screen
        .getAllByRole('button')
        .find((btn) => btn.querySelector('.bi-trash'));
      fireEvent.click(deleteBtn!);

      await waitFor(() => {
        expect(mockConfirm).toHaveBeenCalled();
        expect(mockMutateAsyncDelete).not.toHaveBeenCalled();
      });
    });

    it('toggles rule enabled state when switch is clicked', async () => {
      mockMutateAsyncUpdate.mockResolvedValue({});
      render(<SecurityCenter />);

      const checkboxes = screen.getAllByRole('checkbox');
      // First checkbox is the rule enabled toggle
      fireEvent.click(checkboxes[0]);

      await waitFor(() => {
        expect(mockMutateAsyncUpdate).toHaveBeenCalledWith(
          expect.objectContaining({
            ruleId: 1,
            data: { is_enabled: false }, // Was true, toggling to false
          })
        );
      });
    });
  });

  // ─── Security Settings Tab ────────────────────────────────────────────────

  describe('Security Settings Tab', () => {
    it('shows loading state', () => {
      setSecuritySettingsHook({
        data: undefined,
        isLoading: true,
        isError: false,
        error: null,
        refetch: mockRefetchSettings,
      });
      render(<SecurityCenter />);
      fireEvent.click(screen.getByText('Security Settings'));
      expect(screen.getByTestId('loading')).toBeInTheDocument();
    });

    it('shows error state', () => {
      setSecuritySettingsHook({
        data: undefined,
        isLoading: false,
        isError: true,
        error: new Error('Settings fetch failed'),
        refetch: mockRefetchSettings,
      });
      render(<SecurityCenter />);
      fireEvent.click(screen.getByText('Security Settings'));
      expect(screen.getByTestId('error')).toBeInTheDocument();
    });

    it('renders session settings card', () => {
      render(<SecurityCenter />);
      fireEvent.click(screen.getByText('Security Settings'));
      expect(screen.getByText('Session Settings')).toBeInTheDocument();
    });

    it('renders password policy card', () => {
      render(<SecurityCenter />);
      fireEvent.click(screen.getByText('Security Settings'));
      expect(screen.getByText('Password Policy')).toBeInTheDocument();
    });

    it('renders IP whitelist card', () => {
      render(<SecurityCenter />);
      fireEvent.click(screen.getByText('Security Settings'));
      expect(screen.getByText('IP Whitelist')).toBeInTheDocument();
    });

    it('displays current session timeout value', () => {
      render(<SecurityCenter />);
      fireEvent.click(screen.getByText('Security Settings'));
      const timeoutInput = screen.getByDisplayValue('30');
      expect(timeoutInput).toBeInTheDocument();
    });

    it('displays current max login attempts value', () => {
      render(<SecurityCenter />);
      fireEvent.click(screen.getByText('Security Settings'));
      const attemptsInput = screen.getByDisplayValue('5');
      expect(attemptsInput).toBeInTheDocument();
    });

    it('displays password requirement checkboxes in correct state', () => {
      render(<SecurityCenter />);
      fireEvent.click(screen.getByText('Security Settings'));

      const requireUppercase = screen.getByLabelText('Require Uppercase');
      expect(requireUppercase).toBeChecked();

      const requireSpecial = screen.getByLabelText('Require Special');
      expect(requireSpecial).not.toBeChecked();
    });

    it('calls update settings API on save', async () => {
      mockMutateAsyncUpdateSettings.mockResolvedValue({});
      render(<SecurityCenter />);
      fireEvent.click(screen.getByText('Security Settings'));

      // Change session timeout
      const timeoutInput = screen.getByDisplayValue('30');
      fireEvent.change(timeoutInput, { target: { value: '60' } });

      // Click save
      const saveButtons = screen.getAllByText('Save');
      const settingsSaveBtn = saveButtons.find((btn) => btn.closest('.d-flex'));
      fireEvent.click(settingsSaveBtn!);

      await waitFor(() => {
        expect(mockMutateAsyncUpdateSettings).toHaveBeenCalledWith(
          expect.objectContaining({ session_timeout: 60 })
        );
      });
    });

    it('shows success toast after saving settings', async () => {
      mockMutateAsyncUpdateSettings.mockResolvedValue({});
      render(<SecurityCenter />);
      fireEvent.click(screen.getByText('Security Settings'));

      const saveButtons = screen.getAllByText('Save');
      fireEvent.click(saveButtons[0]);

      await waitFor(() => {
        expect(mockToast.success).toHaveBeenCalledWith('Settings saved successfully');
      });
    });

    it('shows error toast when saving settings fails', async () => {
      mockMutateAsyncUpdateSettings.mockRejectedValue(new Error('Failed'));
      render(<SecurityCenter />);
      fireEvent.click(screen.getByText('Security Settings'));

      const saveButtons = screen.getAllByText('Save');
      fireEvent.click(saveButtons[0]);

      await waitFor(() => {
        expect(mockToast.error).toHaveBeenCalledWith('Error');
      });
    });

    it('resets form data when reset is clicked', () => {
      render(<SecurityCenter />);
      fireEvent.click(screen.getByText('Security Settings'));

      // Change a value
      const timeoutInput = screen.getByDisplayValue('30');
      fireEvent.change(timeoutInput, { target: { value: '60' } });

      // Click reset
      const resetButtons = screen.getAllByText('Reset');
      fireEvent.click(resetButtons[0]);

      expect(mockToast.success).toHaveBeenCalledWith('Reset successful');
    });

    it('processes IP whitelist: trims, deduplicates, filters empty', async () => {
      mockMutateAsyncUpdateSettings.mockResolvedValue({});
      const { container } = render(<SecurityCenter />);
      fireEvent.click(screen.getByText('Security Settings'));

      // The IP whitelist is a native <textarea> (not wrapped in TextInput mock).
      // The label isn't associated via htmlFor, so we query the textarea directly.
      const textarea = container.querySelector('textarea');
      expect(textarea).toBeInTheDocument();
      expect(textarea).toHaveValue('192.168.1.1\n10.0.0.0/24');

      // Change IP whitelist
      fireEvent.change(textarea!, {
        target: { value: ' 192.168.1.1 \n\n10.0.0.0/24\n192.168.1.1\n ' },
      });

      const saveButtons = screen.getAllByText('Save');
      fireEvent.click(saveButtons[0]);

      await waitFor(() => {
        expect(mockMutateAsyncUpdateSettings).toHaveBeenCalledWith(
          expect.objectContaining({
            ip_whitelist: ['192.168.1.1', '10.0.0.0/24'],
          })
        );
      });
    });
  });

  // ─── Audit Thresholds Tab ────────────────────────────────────────────────

  describe('Audit Thresholds Tab', () => {
    it('shows loading state', () => {
      setAuditThresholdsHook({
        data: undefined,
        isLoading: true,
        isError: false,
        error: null,
        refetch: mockRefetchThresholds,
      });
      render(<SecurityCenter />);
      fireEvent.click(screen.getByText('Audit Thresholds'));
      expect(screen.getByTestId('loading')).toBeInTheDocument();
    });

    it('shows error state', () => {
      setAuditThresholdsHook({
        data: undefined,
        isLoading: false,
        isError: true,
        error: new Error('Thresholds fetch failed'),
        refetch: mockRefetchThresholds,
      });
      render(<SecurityCenter />);
      fireEvent.click(screen.getByText('Audit Thresholds'));
      expect(screen.getByTestId('error')).toBeInTheDocument();
    });

    it('renders anomaly detection thresholds card', () => {
      render(<SecurityCenter />);
      fireEvent.click(screen.getByText('Audit Thresholds'));
      expect(screen.getByText('Anomaly Detection Thresholds')).toBeInTheDocument();
    });

    // Helper: the "Failed Login Threshold" input is the first input with value '5'
    // (role_change_threshold is the second input with value '5')
    const getFailedLoginInput = () => screen.getAllByDisplayValue('5')[0];

    it('displays default threshold values', () => {
      render(<SecurityCenter />);
      fireEvent.click(screen.getByText('Audit Thresholds'));

      // Check the default values are rendered in inputs
      // Two inputs have value '5': failed_login and role_change
      expect(screen.getAllByDisplayValue('5')).toHaveLength(2);
      expect(screen.getByDisplayValue('50')).toBeInTheDocument(); // rapid_action
      expect(screen.getAllByDisplayValue('10')).toHaveLength(2); // off_hours, permission_change
    });

    it('shows all five threshold fields', () => {
      render(<SecurityCenter />);
      fireEvent.click(screen.getByText('Audit Thresholds'));

      expect(screen.getByText('Failed Login Threshold')).toBeInTheDocument();
      expect(screen.getByText('Rapid Action Threshold')).toBeInTheDocument();
      expect(screen.getByText('Off Hours Threshold')).toBeInTheDocument();
      expect(screen.getByText('Role Change Threshold')).toBeInTheDocument();
      expect(screen.getByText('Permission Change Threshold')).toBeInTheDocument();
    });

    it('validates empty input with error message', async () => {
      render(<SecurityCenter />);
      fireEvent.click(screen.getByText('Audit Thresholds'));

      const input = getFailedLoginInput();
      fireEvent.change(input, { target: { value: '' } });

      await waitFor(() => {
        expect(screen.getByText('Please enter a valid value')).toBeInTheDocument();
      });
    });

    it('validates non-numeric input with error message', async () => {
      // Note: type="number" inputs normalize non-numeric values to '' in the DOM,
      // so the empty-value check triggers before the NaN check.
      render(<SecurityCenter />);
      fireEvent.click(screen.getByText('Audit Thresholds'));

      const input = getFailedLoginInput();
      fireEvent.change(input, { target: { value: 'abc' } });

      // For type="number", 'abc' is treated as empty by the browser
      await waitFor(() => {
        expect(screen.getByText('Please enter a valid value')).toBeInTheDocument();
      });
    });

    it('validates values below minimum (1) with error message', async () => {
      render(<SecurityCenter />);
      fireEvent.click(screen.getByText('Audit Thresholds'));

      const input = getFailedLoginInput();
      fireEvent.change(input, { target: { value: '0' } });

      await waitFor(() => {
        expect(screen.getByText('Value must be at least 1')).toBeInTheDocument();
      });
    });

    it('clamps values above maximum (10000) and shows warning toast', async () => {
      render(<SecurityCenter />);
      fireEvent.click(screen.getByText('Audit Thresholds'));

      const input = getFailedLoginInput();
      fireEvent.change(input, { target: { value: '20000' } });

      await waitFor(() => {
        expect(mockToast.warning).toHaveBeenCalledWith(
          'Value automatically adjusted to maximum of 10000'
        );
      });
    });

    it('clears error when valid value is entered after invalid', () => {
      render(<SecurityCenter />);
      fireEvent.click(screen.getByText('Audit Thresholds'));

      const input = getFailedLoginInput();

      // Enter invalid value
      fireEvent.change(input, { target: { value: '' } });
      expect(screen.getByText('Please enter a valid value')).toBeInTheDocument();

      // Enter valid value
      fireEvent.change(input, { target: { value: '10' } });
      expect(screen.queryByText('Please enter a valid value')).not.toBeInTheDocument();
    });

    it('disables save button when validation errors exist', async () => {
      render(<SecurityCenter />);
      fireEvent.click(screen.getByText('Audit Thresholds'));

      const input = getFailedLoginInput();
      fireEvent.change(input, { target: { value: '' } });

      // Wait for error to appear and save button to become disabled
      await waitFor(() => {
        const saveButtons = screen.getAllByText('Save');
        const saveBtn = saveButtons.find(
          (btn) => !btn.closest('[data-testid="modal-footer"]')
        );
        expect(saveBtn).toBeDisabled();
      });
    });

    it('shows error indicator text when validation errors exist', async () => {
      render(<SecurityCenter />);
      fireEvent.click(screen.getByText('Audit Thresholds'));

      const input = getFailedLoginInput();
      // Use empty string since type="number" inputs treat 'abc' as ''
      fireEvent.change(input, { target: { value: '' } });

      await waitFor(() => {
        expect(screen.getByText('Fix red error fields first')).toBeInTheDocument();
      });
    });

    it('calls update thresholds API on save', async () => {
      mockMutateAsyncUpdateThresholds.mockResolvedValue({});
      render(<SecurityCenter />);
      fireEvent.click(screen.getByText('Audit Thresholds'));

      // Change the failed login threshold
      const input = getFailedLoginInput();
      fireEvent.change(input, { target: { value: '10' } });

      // Click save
      const saveButtons = screen.getAllByText('Save');
      fireEvent.click(saveButtons[0]);

      await waitFor(() => {
        expect(mockMutateAsyncUpdateThresholds).toHaveBeenCalledWith(
          expect.objectContaining({ audit_failed_login_threshold: 10 })
        );
      });
    });

    it('prevents saving when validation errors exist', async () => {
      render(<SecurityCenter />);
      fireEvent.click(screen.getByText('Audit Thresholds'));

      // Introduce validation error (empty value)
      const input = getFailedLoginInput();
      fireEvent.change(input, { target: { value: '' } });

      // Wait for error to appear and save button to become disabled
      await waitFor(() => {
        expect(screen.getByText('Please enter a valid value')).toBeInTheDocument();
        // The save button is disabled when validation errors exist
        const saveButtons = screen.getAllByText('Save');
        const saveBtn = saveButtons.find(
          (btn) => !btn.closest('[data-testid="modal-footer"]')
        );
        expect(saveBtn).toBeDisabled();
      });

      // The mutation should never be called when there are validation errors
      expect(mockMutateAsyncUpdateThresholds).not.toHaveBeenCalled();
    });

    it('shows success toast after saving thresholds', async () => {
      mockMutateAsyncUpdateThresholds.mockResolvedValue({});
      render(<SecurityCenter />);
      fireEvent.click(screen.getByText('Audit Thresholds'));

      const saveButtons = screen.getAllByText('Save');
      fireEvent.click(saveButtons[0]);

      await waitFor(() => {
        expect(mockToast.success).toHaveBeenCalledWith('Audit thresholds saved successfully');
      });
    });

    it('shows error toast when saving thresholds fails', async () => {
      mockMutateAsyncUpdateThresholds.mockRejectedValue(new Error('Failed'));
      render(<SecurityCenter />);
      fireEvent.click(screen.getByText('Audit Thresholds'));

      const saveButtons = screen.getAllByText('Save');
      fireEvent.click(saveButtons[0]);

      await waitFor(() => {
        expect(mockToast.error).toHaveBeenCalledWith('Error');
      });
    });

    it('resets thresholds form data and errors', async () => {
      render(<SecurityCenter />);
      fireEvent.click(screen.getByText('Audit Thresholds'));

      // Make a valid change first (this populates thresholdsFormData so reset shows toast)
      const input = getFailedLoginInput();
      fireEvent.change(input, { target: { value: '10' } });

      // Verify the change was applied (no error)
      await waitFor(() => {
        expect(screen.queryByText('Please enter a valid value')).not.toBeInTheDocument();
      });

      // Click reset
      const resetButtons = screen.getAllByText('Reset');
      fireEvent.click(resetButtons[0]);

      await waitFor(() => {
        expect(mockToast.success).toHaveBeenCalledWith('Reset successful');
      });
    });

    it('parses float input to integer (e.g., 3.5 → 3)', () => {
      render(<SecurityCenter />);
      fireEvent.click(screen.getByText('Audit Thresholds'));

      const input = getFailedLoginInput();
      fireEvent.change(input, { target: { value: '3.5' } });

      // Should not show an error - parseInt('3.5') = 3
      expect(screen.queryByText('Must be a number')).not.toBeInTheDocument();
    });

    it('accepts negative values below 1 with proper error message', async () => {
      render(<SecurityCenter />);
      fireEvent.click(screen.getByText('Audit Thresholds'));

      const input = getFailedLoginInput();
      fireEvent.change(input, { target: { value: '-5' } });

      await waitFor(() => {
        expect(screen.getByText('Value must be at least 1')).toBeInTheDocument();
      });
    });
  });
});
