/**
 * SecurityCenter Component Tests
 *
 * Tests cover four sub-pages:
 * 1. Content Filter (Filter Rules) - CRUD operations, toggle, table display
 * 2. Security Settings - session/password/IP whitelist configuration
 * 3. Audit Thresholds - threshold input validation, save, reset
 * 4. Sensitive Keywords - CRUD operations, permission check, validation (Issue #3059)
 * 5. Filter Statistics - filter status, cache performance, loaded patterns
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
  t: (key: string, _lang?: string, params?: Record<string, unknown>) => {
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
      // Filter Statistics
      filterStats: 'Filter Statistics',
      filterStatus: 'Filter Status',
      filterEnabled: 'Enabled',
      filterDisabled: 'Disabled',
      piiRedaction: 'PII Redaction',
      highRiskBlock: 'High Risk Block',
      patternRules: 'Pattern Rules',
      keywordRules: 'Keyword Rules',
      cachePerformance: 'Cache Performance',
      cacheHitRate: 'Cache Hit Rate',
      cacheHits: 'Cache Hits',
      cacheMisses: 'Cache Misses',
      cacheSize: 'Cache Size',
      loadedPatterns: 'Loaded Patterns',
      noPatternsLoaded: 'No patterns loaded',
      viewAllPatterns: 'View all ({count})',
      showLess: 'Show less',
      refreshStats: 'Refresh',
      noData: 'No data available',
      // Sensitive Keywords (Issue #3059)
      sensitiveKeywords: 'Sensitive Keywords',
      sensitiveKeywordsDesc: 'Manage sensitive keywords for content filtering',
      addKeyword: 'Add Keyword',
      keyword: 'Keyword',
      keywordPlaceholder: 'Enter sensitive keyword',
      keywordHelp: 'Keyword will be used for content filtering',
      confirmDeleteKeyword: 'Are you sure you want to delete this keyword?',
      keywordCreated: 'Keyword created successfully',
      keywordAlreadyExists: 'Keyword already exists',
      keywordCreateFailed: 'Failed to create keyword',
      keywordUpdateFailed: 'Failed to update keyword',
      keywordDeleteFailed: 'Failed to delete keyword',
      noKeywords: 'No sensitive keywords configured',
      noPermission: 'No permission to access',
      filterByStatus: 'Filter by status',
      keywordEmpty: 'Keyword cannot be empty',
      keywordTooLong: 'Keyword cannot exceed 255 characters',
      status: 'Status',
      actions: 'Actions',
    };
    let text = translations[key] || key;
    if (params) {
      Object.entries(params).forEach(([paramKey, paramValue]) => {
        text = text.replace(`{${paramKey}}`, String(paramValue));
      });
    }
    return text;
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
  useFilterStats: vi.fn(() => ({
    data: {
      enabled: true,
      redact_pii: true,
      block_high_risk: true,
      pattern_count: 5,
      keyword_count: 10,
      patterns: ['password', 'ssn', 'credit_card'],
      compiled_cache_size: 100,
      compiled_cache_hits: 500,
      compiled_cache_misses: 50,
      compiled_cache_hit_rate: 90.91,
      compiled_cache_max_size: 1000,
    },
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
    isFetching: false,
  })),
  useAdminTenant: vi.fn(() => ({
    effectiveTenantId: 1,
  })),
  useSensitiveKeywords: vi.fn(() => ({
    data: {
      keywords: [
        {
          id: 1,
          tenant_id: 1,
          keyword: 'password',
          is_enabled: true,
          created_by: 1,
          created_at: '2025-01-01T00:00:00Z',
        },
      ],
      total: 1,
      limit: 20,
      offset: 0,
      tenant_id: 1,
    },
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  })),
  useCreateSensitiveKeyword: vi.fn(() => ({
    mutateAsync: vi.fn(),
    isPending: false,
  })),
  useUpdateSensitiveKeyword: vi.fn(() => ({
    mutateAsync: vi.fn(),
    isPending: false,
  })),
  useDeleteSensitiveKeyword: vi.fn(() => ({
    mutateAsync: vi.fn(),
    isPending: false,
  })),
  // SSRF Protection Status hooks (Issue #3328)
  useSsrfStatus: vi.fn(() => ({
    data: {
      ssrf_protection_enabled: true,
      emergency_mode: false,
      config_source: 'environment',
      config_version: 1,
      port_whitelist: {
        value: [80, 443, 8080],
        is_customized: false,
        default_value: [80, 443, 8080],
      },
      global_allowlist: {
        count: 0,
        entries: [],
        is_customized: false,
      },
      tenant_allowlist: {
        enabled: false,
        tenant_count: 0,
      },
      default_policy: {
        blocked_private_networks: [
          '10.0.0.0/8',
          '172.16.0.0/12',
          '192.168.0.0/16',
          '127.0.0.0/8',
          '169.254.0.0/16',
        ],
        blocked_hostnames: ['localhost', 'metadata.google.internal'],
        default_port_whitelist: [80, 443, 8080],
      },
      interception_stats: {
        last_24h: 0,
        last_7d: 0,
        last_30d: 0,
      },
      can_reset: true,
    },
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  })),
  useResetSsrfConfig: vi.fn(() => ({
    mutateAsync: vi.fn(),
    isPending: false,
  })),
  useUser: vi.fn(() => ({
    id: 1,
    username: 'testuser',
    role: 'platform_admin',
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
    actions,
  }: {
    title?: string;
    children: React.ReactNode;
    className?: string;
    actions?: React.ReactNode;
  }) => (
    <div data-testid="card" className={className}>
      <div className="d-flex justify-content-between align-items-center">
        {title && <h5>{title}</h5>}
        {actions && <div className="card-actions">{actions}</div>}
      </div>
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
    <select value={value} onChange={(e) => onChange(e.target.value)} data-testid="select">
      {options.map((opt) => (
        <option key={opt.value} value={opt.value}>
          {opt.label}
        </option>
      ))}
    </select>
  ),
  Loading: ({ text }: { text?: string }) => <div data-testid="loading">{text || 'Loading...'}</div>,
  Error: ({ message, onRetry }: { message: string; onRetry?: () => void }) => (
    <div data-testid="error">
      {message}
      {onRetry && (
        <button data-testid="retry-btn" onClick={onRetry}>
          Retry
        </button>
      )}
    </div>
  ),
  EmptyState: ({
    title,
    icon: _icon,
    size: _size,
  }: {
    title: string;
    icon?: string;
    size?: string;
  }) => <div data-testid="empty-state">{title}</div>,
  Badge: ({ children, variant }: { children: React.ReactNode; variant: string }) => (
    <span data-testid="badge" data-variant={variant}>
      {children}
    </span>
  ),
  PageRefreshControl: () => <div data-testid="page-refresh-control" />,
  StatCard: ({
    label,
    value,
    variant,
  }: {
    label: string;
    value: number | string;
    variant?: string;
  }) => (
    <div data-testid="stat-card" data-variant={variant}>
      <span>{label}</span>
      <span>{value}</span>
    </div>
  ),
  Progress: ({ value, max, variant }: { value: number; max?: number; variant?: string }) => (
    <div data-testid="progress" data-value={value} data-max={max} data-variant={variant}>
      Progress: {value}%
    </div>
  ),
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
  useAdminTenant,
  useSensitiveKeywords,
  useCreateSensitiveKeyword,
  useUpdateSensitiveKeyword,
  useDeleteSensitiveKeyword,
  useFilterStats,
} from '@/hooks';

// ─── Helper to override hooks for specific tests ──────────────────────────────

function setFilterRulesHook(overrides: Record<string, unknown>) {
  vi.mocked(useFilterRules).mockReturnValue(overrides as ReturnType<typeof useFilterRules>);
}

function setCreateRuleHook(overrides: Record<string, unknown>) {
  vi.mocked(useCreateFilterRule).mockReturnValue(
    overrides as ReturnType<typeof useCreateFilterRule>
  );
}

function setUpdateRuleHook(overrides: Record<string, unknown>) {
  vi.mocked(useUpdateFilterRule).mockReturnValue(
    overrides as ReturnType<typeof useUpdateFilterRule>
  );
}

function setDeleteRuleHook(overrides: Record<string, unknown>) {
  vi.mocked(useDeleteFilterRule).mockReturnValue(
    overrides as ReturnType<typeof useDeleteFilterRule>
  );
}

function setSecuritySettingsHook(overrides: Record<string, unknown>) {
  vi.mocked(useSecuritySettings).mockReturnValue(
    overrides as ReturnType<typeof useSecuritySettings>
  );
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
    // Reset useFilterStats to default mock
    vi.mocked(useFilterStats).mockReturnValue({
      data: {
        enabled: true,
        redact_pii: true,
        block_high_risk: true,
        pattern_count: 5,
        keyword_count: 10,
        patterns: ['password', 'ssn', 'credit_card'],
        compiled_cache_size: 100,
        compiled_cache_hits: 500,
        compiled_cache_misses: 50,
        compiled_cache_hit_rate: 90.91,
        compiled_cache_max_size: 1000,
      },
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
      isFetching: false,
    } as ReturnType<typeof useFilterStats>);
    mockConfirm.mockResolvedValue(true);
  });

  // ─── Page & Tab Rendering ─────────────────────────────────────────────────

  describe('Page & Tab Rendering', () => {
    it('renders the page title', () => {
      render(<SecurityCenter />);
      expect(screen.getByText('Security Center')).toBeInTheDocument();
    });

    it('renders five tab buttons', () => {
      render(<SecurityCenter />);
      expect(screen.getByText('Content Filter')).toBeInTheDocument();
      expect(screen.getByText('Security Settings')).toBeInTheDocument();
      expect(screen.getByText('Audit Thresholds')).toBeInTheDocument();
      expect(screen.getByText('Sensitive Keywords')).toBeInTheDocument();
      expect(screen.getByText('Filter Statistics')).toBeInTheDocument();
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
      fireEvent.click(editBtn as HTMLElement);
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
      fireEvent.click(editBtn as HTMLElement);

      // Click save
      const saveButton = screen.getByText('Save');
      fireEvent.click(saveButton);

      await waitFor(() => {
        expect(mockMutateAsyncUpdate).toHaveBeenCalledWith(expect.objectContaining({ ruleId: 1 }));
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

      const deleteBtn = screen.getAllByRole('button').find((btn) => btn.querySelector('.bi-trash'));
      fireEvent.click(deleteBtn as HTMLElement);

      await waitFor(() => {
        expect(mockConfirm).toHaveBeenCalledWith(expect.objectContaining({ variant: 'danger' }));
        expect(mockMutateAsyncDelete).toHaveBeenCalledWith(1);
      });
    });

    it('does not delete when confirmation is rejected', async () => {
      mockConfirm.mockResolvedValue(false);
      render(<SecurityCenter />);

      const deleteBtn = screen.getAllByRole('button').find((btn) => btn.querySelector('.bi-trash'));
      fireEvent.click(deleteBtn as HTMLElement);

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
      fireEvent.click(settingsSaveBtn as HTMLElement);

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
      fireEvent.change(textarea as HTMLElement, {
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
        const saveBtn = saveButtons.find((btn) => !btn.closest('[data-testid="modal-footer"]'));
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
        const saveBtn = saveButtons.find((btn) => !btn.closest('[data-testid="modal-footer"]'));
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

  // ─── Sensitive Keywords Tab (Issue #3059) ────────────────────────────────────

  describe('Sensitive Keywords Tab', () => {
    const mockMutateAsyncCreateKeyword = vi.fn();
    const mockMutateAsyncUpdateKeyword = vi.fn();
    const mockMutateAsyncDeleteKeyword = vi.fn();
    const mockRefetchKeywords = vi.fn();

    beforeEach(() => {
      vi.clearAllMocks();
      mockMutateAsyncCreateKeyword.mockResolvedValue({ id: 2, is_new: true });
      mockMutateAsyncUpdateKeyword.mockResolvedValue({ success: true });
      mockMutateAsyncDeleteKeyword.mockResolvedValue({ success: true });

      // Reset useAdminTenant to return valid tenant ID
      vi.mocked(useAdminTenant).mockReturnValue({
        effectiveTenantId: 1,
      } as ReturnType<typeof useAdminTenant>);

      // Reset useSensitiveKeywords to return default data
      vi.mocked(useSensitiveKeywords).mockReturnValue({
        data: {
          keywords: [
            {
              id: 1,
              tenant_id: 1,
              keyword: 'password',
              is_enabled: true,
              created_by: 1,
              created_at: '2025-01-01T00:00:00Z',
            },
          ],
          total: 1,
          limit: 20,
          offset: 0,
          tenant_id: 1,
        },
        isLoading: false,
        isError: false,
        error: null,
        refetch: mockRefetchKeywords,
      } as ReturnType<typeof useSensitiveKeywords>);

      // Reset mutation hooks
      vi.mocked(useCreateSensitiveKeyword).mockReturnValue({
        mutateAsync: mockMutateAsyncCreateKeyword,
        isPending: false,
      } as ReturnType<typeof useCreateSensitiveKeyword>);

      vi.mocked(useUpdateSensitiveKeyword).mockReturnValue({
        mutateAsync: mockMutateAsyncUpdateKeyword,
        isPending: false,
      } as ReturnType<typeof useUpdateSensitiveKeyword>);

      vi.mocked(useDeleteSensitiveKeyword).mockReturnValue({
        mutateAsync: mockMutateAsyncDeleteKeyword,
        isPending: false,
      } as ReturnType<typeof useDeleteSensitiveKeyword>);

      mockConfirm.mockResolvedValue(true);
    });

    it('shows Sensitive Keywords tab button', () => {
      render(<SecurityCenter />);
      expect(screen.getByText('Sensitive Keywords')).toBeInTheDocument();
    });

    it('switches to Sensitive Keywords tab when clicked', () => {
      render(<SecurityCenter />);
      fireEvent.click(screen.getByText('Sensitive Keywords'));
      const keywordTab = screen.getByText('Sensitive Keywords').closest('button');
      expect(keywordTab).toHaveClass('active');
    });

    it('shows Add Keyword button only on Sensitive Keywords tab when tenant is selected', () => {
      render(<SecurityCenter />);
      fireEvent.click(screen.getByText('Sensitive Keywords'));
      expect(screen.getByText('Add Keyword')).toBeInTheDocument();
    });

    it('shows error when no tenant is selected (no permission)', () => {
      vi.mocked(useAdminTenant).mockReturnValue({
        effectiveTenantId: null,
      } as ReturnType<typeof useAdminTenant>);
      render(<SecurityCenter />);
      fireEvent.click(screen.getByText('Sensitive Keywords'));
      expect(screen.getByTestId('error')).toBeInTheDocument();
      expect(screen.getByText('No permission to access')).toBeInTheDocument();
    });

    it('shows loading state for keywords', () => {
      vi.mocked(useSensitiveKeywords).mockReturnValue({
        data: undefined,
        isLoading: true,
        isError: false,
        error: null,
        refetch: vi.fn(),
      } as ReturnType<typeof useSensitiveKeywords>);
      render(<SecurityCenter />);
      fireEvent.click(screen.getByText('Sensitive Keywords'));
      expect(screen.getByTestId('loading')).toBeInTheDocument();
    });

    it('shows error state for keywords', () => {
      vi.mocked(useSensitiveKeywords).mockReturnValue({
        data: undefined,
        isLoading: false,
        isError: true,
        error: new Error('Keywords fetch failed'),
        refetch: vi.fn(),
      } as ReturnType<typeof useSensitiveKeywords>);
      render(<SecurityCenter />);
      fireEvent.click(screen.getByText('Sensitive Keywords'));
      expect(screen.getByTestId('error')).toBeInTheDocument();
    });

    it('shows empty state when no keywords exist', () => {
      vi.mocked(useSensitiveKeywords).mockReturnValue({
        data: { keywords: [], total: 0, limit: 20, offset: 0, tenant_id: 1 },
        isLoading: false,
        isError: false,
        error: null,
        refetch: vi.fn(),
      } as ReturnType<typeof useSensitiveKeywords>);
      render(<SecurityCenter />);
      fireEvent.click(screen.getByText('Sensitive Keywords'));
      expect(screen.getByTestId('empty-state')).toBeInTheDocument();
      expect(screen.getByText('No sensitive keywords configured')).toBeInTheDocument();
    });

    it('renders keywords table with data', () => {
      render(<SecurityCenter />);
      fireEvent.click(screen.getByText('Sensitive Keywords'));
      expect(screen.getByText('password')).toBeInTheDocument();
    });

    it('opens create modal when Add Keyword is clicked', () => {
      render(<SecurityCenter />);
      fireEvent.click(screen.getByText('Sensitive Keywords'));
      expect(screen.queryByTestId('modal')).not.toBeInTheDocument();
      const addKeywordElements = screen.getAllByText('Add Keyword');
      fireEvent.click(addKeywordElements[0]);
      expect(screen.getByTestId('modal')).toBeInTheDocument();
    });

    it('validates empty keyword with error message', async () => {
      render(<SecurityCenter />);
      fireEvent.click(screen.getByText('Sensitive Keywords'));
      fireEvent.click(screen.getByText('Add Keyword'));

      const saveButton = screen.getByText('Save');
      fireEvent.click(saveButton);

      await waitFor(() => {
        expect(mockToast.error).toHaveBeenCalledWith('Keyword cannot be empty');
      });
    });

    it('validates keyword exceeding 255 characters', async () => {
      render(<SecurityCenter />);
      fireEvent.click(screen.getByText('Sensitive Keywords'));
      fireEvent.click(screen.getByText('Add Keyword'));

      const longKeyword = 'a'.repeat(256);
      const input = screen.getByPlaceholderText('Enter sensitive keyword');
      fireEvent.change(input, { target: { value: longKeyword } });

      const saveButton = screen.getByText('Save');
      fireEvent.click(saveButton);

      await waitFor(() => {
        expect(mockToast.error).toHaveBeenCalledWith('Keyword cannot exceed 255 characters');
      });
    });

    it('trims whitespace from keyword input', async () => {
      vi.mocked(useCreateSensitiveKeyword).mockReturnValue({
        mutateAsync: mockMutateAsyncCreateKeyword,
        isPending: false,
      } as ReturnType<typeof useCreateSensitiveKeyword>);
      render(<SecurityCenter />);
      fireEvent.click(screen.getByText('Sensitive Keywords'));
      fireEvent.click(screen.getByText('Add Keyword'));

      const input = screen.getByPlaceholderText('Enter sensitive keyword');
      fireEvent.change(input, { target: { value: '  test-keyword  ' } });

      const saveButton = screen.getByText('Save');
      fireEvent.click(saveButton);

      await waitFor(() => {
        expect(mockMutateAsyncCreateKeyword).toHaveBeenCalledWith(
          expect.objectContaining({
            tenantId: 1,
            data: { keyword: 'test-keyword' },
          })
        );
      });
    });

    it('shows success toast when keyword is created (is_new: true)', async () => {
      vi.mocked(useCreateSensitiveKeyword).mockReturnValue({
        mutateAsync: mockMutateAsyncCreateKeyword,
        isPending: false,
      } as ReturnType<typeof useCreateSensitiveKeyword>);
      mockMutateAsyncCreateKeyword.mockResolvedValue({ id: 2, is_new: true });
      render(<SecurityCenter />);
      fireEvent.click(screen.getByText('Sensitive Keywords'));
      fireEvent.click(screen.getByText('Add Keyword'));

      const input = screen.getByPlaceholderText('Enter sensitive keyword');
      fireEvent.change(input, { target: { value: 'new-keyword' } });

      const saveButton = screen.getByText('Save');
      fireEvent.click(saveButton);

      await waitFor(() => {
        expect(mockToast.success).toHaveBeenCalledWith('Keyword created successfully');
      });
    });

    it('shows info toast when keyword already exists (is_new: false)', async () => {
      vi.mocked(useCreateSensitiveKeyword).mockReturnValue({
        mutateAsync: mockMutateAsyncCreateKeyword,
        isPending: false,
      } as ReturnType<typeof useCreateSensitiveKeyword>);
      mockMutateAsyncCreateKeyword.mockResolvedValue({ id: 1, is_new: false });
      render(<SecurityCenter />);
      fireEvent.click(screen.getByText('Sensitive Keywords'));
      fireEvent.click(screen.getByText('Add Keyword'));

      const input = screen.getByPlaceholderText('Enter sensitive keyword');
      fireEvent.change(input, { target: { value: 'existing-keyword' } });

      const saveButton = screen.getByText('Save');
      fireEvent.click(saveButton);

      await waitFor(() => {
        expect(mockToast.info).toHaveBeenCalledWith('Keyword already exists');
      });
    });

    it('shows error toast when keyword creation fails', async () => {
      vi.mocked(useCreateSensitiveKeyword).mockReturnValue({
        mutateAsync: mockMutateAsyncCreateKeyword,
        isPending: false,
      } as ReturnType<typeof useCreateSensitiveKeyword>);
      mockMutateAsyncCreateKeyword.mockRejectedValue(new Error('Create failed'));
      render(<SecurityCenter />);
      fireEvent.click(screen.getByText('Sensitive Keywords'));
      fireEvent.click(screen.getByText('Add Keyword'));

      const input = screen.getByPlaceholderText('Enter sensitive keyword');
      fireEvent.change(input, { target: { value: 'test-keyword' } });

      const saveButton = screen.getByText('Save');
      fireEvent.click(saveButton);

      await waitFor(() => {
        expect(mockToast.error).toHaveBeenCalledWith('Failed to create keyword');
      });
    });

    it('toggles keyword enabled state when switch is clicked', async () => {
      vi.mocked(useUpdateSensitiveKeyword).mockReturnValue({
        mutateAsync: mockMutateAsyncUpdateKeyword,
        isPending: false,
      } as ReturnType<typeof useUpdateSensitiveKeyword>);
      render(<SecurityCenter />);
      fireEvent.click(screen.getByText('Sensitive Keywords'));

      const checkboxes = screen.getAllByRole('checkbox');
      // Find the keyword enabled toggle (first checkbox in sensitive keywords tab)
      const keywordCheckbox = checkboxes[0];
      fireEvent.click(keywordCheckbox);

      await waitFor(() => {
        expect(mockMutateAsyncUpdateKeyword).toHaveBeenCalledWith(
          expect.objectContaining({
            tenantId: 1,
            keywordId: 1,
            data: { is_enabled: false },
          })
        );
      });
    });

    it('shows error toast when toggling keyword fails', async () => {
      vi.mocked(useUpdateSensitiveKeyword).mockReturnValue({
        mutateAsync: mockMutateAsyncUpdateKeyword,
        isPending: false,
      } as ReturnType<typeof useUpdateSensitiveKeyword>);
      mockMutateAsyncUpdateKeyword.mockRejectedValue(new Error('Update failed'));
      render(<SecurityCenter />);
      fireEvent.click(screen.getByText('Sensitive Keywords'));

      const checkboxes = screen.getAllByRole('checkbox');
      fireEvent.click(checkboxes[0]);

      await waitFor(() => {
        expect(mockToast.error).toHaveBeenCalledWith('Failed to update keyword');
      });
    });

    it('calls delete API when delete is confirmed', async () => {
      mockConfirm.mockResolvedValue(true);
      render(<SecurityCenter />);
      fireEvent.click(screen.getByText('Sensitive Keywords'));

      // Find the delete button by its variant (outline-danger)
      const buttons = screen.getAllByRole('button');
      const deleteBtn = buttons.find(
        (btn) => btn.getAttribute('data-variant') === 'outline-danger'
      );
      expect(deleteBtn).toBeDefined();
      fireEvent.click(deleteBtn!);

      await waitFor(() => {
        expect(mockConfirm).toHaveBeenCalledWith(expect.objectContaining({ variant: 'danger' }));
        expect(mockMutateAsyncDeleteKeyword).toHaveBeenCalledWith({
          tenantId: 1,
          keywordId: 1,
        });
      });
    });

    it('does not delete when confirmation is rejected', async () => {
      mockConfirm.mockResolvedValue(false);
      render(<SecurityCenter />);
      fireEvent.click(screen.getByText('Sensitive Keywords'));

      const buttons = screen.getAllByRole('button');
      const deleteBtn = buttons.find(
        (btn) => btn.getAttribute('data-variant') === 'outline-danger'
      );
      expect(deleteBtn).toBeDefined();
      fireEvent.click(deleteBtn!);

      await waitFor(() => {
        expect(mockConfirm).toHaveBeenCalled();
        expect(mockMutateAsyncDeleteKeyword).not.toHaveBeenCalled();
      });
    });

    it('shows error toast when delete fails', async () => {
      mockMutateAsyncDeleteKeyword.mockRejectedValue(new Error('Delete failed'));
      mockConfirm.mockResolvedValue(true);
      render(<SecurityCenter />);
      fireEvent.click(screen.getByText('Sensitive Keywords'));

      const buttons = screen.getAllByRole('button');
      const deleteBtn = buttons.find(
        (btn) => btn.getAttribute('data-variant') === 'outline-danger'
      );
      expect(deleteBtn).toBeDefined();
      fireEvent.click(deleteBtn!);

      await waitFor(() => {
        expect(mockToast.error).toHaveBeenCalledWith('Failed to delete keyword');
      });
    });

    it('closes modal when cancel is clicked', () => {
      render(<SecurityCenter />);
      fireEvent.click(screen.getByText('Sensitive Keywords'));
      const addKeywordElements = screen.getAllByText('Add Keyword');
      fireEvent.click(addKeywordElements[0]);
      expect(screen.getByTestId('modal')).toBeInTheDocument();

      fireEvent.click(screen.getByText('Cancel'));
      expect(screen.queryByTestId('modal')).not.toBeInTheDocument();
    });
  });

  // ─── Filter Statistics Tab ──────────────────────────────────────────────────

  describe('Filter Statistics Tab', () => {
    it('switches to Filter Statistics tab when clicked', async () => {
      render(<SecurityCenter />);
      fireEvent.click(screen.getByText('Filter Statistics'));
      const statsTab = await waitFor(() => screen.getByText('Filter Statistics').closest('button'));
      expect(statsTab).toHaveClass('active');
    });

    it('shows loading state when stats are loading', () => {
      vi.mocked(useFilterStats).mockReturnValue({
        data: undefined,
        isLoading: true,
        isError: false,
        error: null,
        refetch: vi.fn(),
        isFetching: false,
      } as ReturnType<typeof useFilterStats>);
      render(<SecurityCenter />);
      fireEvent.click(screen.getByText('Filter Statistics'));
      expect(screen.getByTestId('loading')).toBeInTheDocument();
    });

    it('shows error state when stats fetch fails', () => {
      vi.mocked(useFilterStats).mockReturnValue({
        data: undefined,
        isLoading: false,
        isError: true,
        error: new Error('Stats fetch failed'),
        refetch: vi.fn(),
        isFetching: false,
      } as ReturnType<typeof useFilterStats>);
      render(<SecurityCenter />);
      fireEvent.click(screen.getByText('Filter Statistics'));
      expect(screen.getByTestId('error')).toBeInTheDocument();
    });

    it('renders filter status badges', async () => {
      render(<SecurityCenter />);
      fireEvent.click(screen.getByText('Filter Statistics'));
      await waitFor(() => {
        expect(screen.getByText('Filter Status')).toBeInTheDocument();
      });
      expect(screen.getByText('PII Redaction')).toBeInTheDocument();
      expect(screen.getByText('High Risk Block')).toBeInTheDocument();
    });

    it('renders pattern and keyword rule counts', async () => {
      render(<SecurityCenter />);
      fireEvent.click(screen.getByText('Filter Statistics'));
      await waitFor(() => {
        expect(screen.getByText('Pattern Rules')).toBeInTheDocument();
      });
      expect(screen.getByText('Keyword Rules')).toBeInTheDocument();
    });

    it('renders cache performance card', async () => {
      render(<SecurityCenter />);
      fireEvent.click(screen.getByText('Filter Statistics'));
      await waitFor(() => {
        expect(screen.getByText('Cache Performance')).toBeInTheDocument();
      });
      expect(screen.getByText('Cache Hit Rate')).toBeInTheDocument();
    });

    it('renders loaded patterns section', async () => {
      render(<SecurityCenter />);
      fireEvent.click(screen.getByText('Filter Statistics'));
      await waitFor(() => {
        expect(screen.getByText('Loaded Patterns')).toBeInTheDocument();
      });
    });

    it('calls refetch when refresh button is clicked', async () => {
      const mockRefetch = vi.fn();
      vi.mocked(useFilterStats).mockReturnValue({
        data: {
          enabled: true,
          redact_pii: true,
          block_high_risk: true,
          pattern_count: 5,
          keyword_count: 10,
          patterns: ['password', 'ssn'],
          compiled_cache_size: 100,
          compiled_cache_hits: 500,
          compiled_cache_misses: 50,
          compiled_cache_hit_rate: 90.91,
          compiled_cache_max_size: 1000,
        },
        isLoading: false,
        isError: false,
        error: null,
        refetch: mockRefetch,
        isFetching: false,
      } as ReturnType<typeof useFilterStats>);
      render(<SecurityCenter />);
      fireEvent.click(screen.getByText('Filter Statistics'));
      await waitFor(() => {
        expect(screen.getByText('Cache Performance')).toBeInTheDocument();
      });
      fireEvent.click(screen.getByText('Refresh'));
      expect(mockRefetch).toHaveBeenCalled();
    });

    it('shows empty state when patterns array is empty', async () => {
      vi.mocked(useFilterStats).mockReturnValue({
        data: {
          enabled: true,
          redact_pii: true,
          block_high_risk: true,
          pattern_count: 0,
          keyword_count: 0,
          patterns: [],
          compiled_cache_size: 0,
          compiled_cache_hits: 0,
          compiled_cache_misses: 0,
          compiled_cache_hit_rate: 0,
          compiled_cache_max_size: 1000,
        },
        isLoading: false,
        isError: false,
        error: null,
        refetch: vi.fn(),
        isFetching: false,
      } as ReturnType<typeof useFilterStats>);
      render(<SecurityCenter />);
      fireEvent.click(screen.getByText('Filter Statistics'));
      await waitFor(() => {
        expect(screen.getByText('Loaded Patterns')).toBeInTheDocument();
      });
      expect(screen.getByText('No patterns loaded')).toBeInTheDocument();
    });

    it('shows view all button when patterns exceed 20 items', async () => {
      const manyPatterns = Array.from({ length: 25 }, (_, i) => `pattern_${i}`);
      vi.mocked(useFilterStats).mockReturnValue({
        data: {
          enabled: true,
          redact_pii: true,
          block_high_risk: true,
          pattern_count: 25,
          keyword_count: 0,
          patterns: manyPatterns,
          compiled_cache_size: 25,
          compiled_cache_hits: 100,
          compiled_cache_misses: 10,
          compiled_cache_hit_rate: 90.91,
          compiled_cache_max_size: 1000,
        },
        isLoading: false,
        isError: false,
        error: null,
        refetch: vi.fn(),
        isFetching: false,
      } as ReturnType<typeof useFilterStats>);
      render(<SecurityCenter />);
      fireEvent.click(screen.getByText('Filter Statistics'));
      await waitFor(() => {
        expect(screen.getByText('Loaded Patterns')).toBeInTheDocument();
      });
      // Should show "View all (25)" button
      expect(screen.getByText('View all (25)')).toBeInTheDocument();
      // Should only show first 20 patterns
      expect(screen.getByText('pattern_0')).toBeInTheDocument();
      expect(screen.queryByText('pattern_20')).not.toBeInTheDocument();

      // Click to expand
      fireEvent.click(screen.getByText('View all (25)'));
      await waitFor(() => {
        expect(screen.getByText('pattern_20')).toBeInTheDocument();
      });
      expect(screen.getByText('Show less')).toBeInTheDocument();
    });

    it('shows correct progress bar variant for different hit rates', async () => {
      // Test high hit rate (>= 90%) -> success
      vi.mocked(useFilterStats).mockReturnValue({
        data: {
          enabled: true,
          redact_pii: true,
          block_high_risk: true,
          pattern_count: 5,
          keyword_count: 10,
          patterns: ['password'],
          compiled_cache_size: 100,
          compiled_cache_hits: 900,
          compiled_cache_misses: 100,
          compiled_cache_hit_rate: 90,
          compiled_cache_max_size: 1000,
        },
        isLoading: false,
        isError: false,
        error: null,
        refetch: vi.fn(),
        isFetching: false,
      } as ReturnType<typeof useFilterStats>);
      const { unmount } = render(<SecurityCenter />);
      fireEvent.click(screen.getByText('Filter Statistics'));
      await waitFor(() => {
        expect(screen.getByTestId('progress')).toBeInTheDocument();
      });
      expect(screen.getByTestId('progress')).toHaveAttribute('data-variant', 'success');
      unmount();

      // Test medium hit rate (70-90%) -> warning
      vi.mocked(useFilterStats).mockReturnValue({
        data: {
          enabled: true,
          redact_pii: true,
          block_high_risk: true,
          pattern_count: 5,
          keyword_count: 10,
          patterns: ['password'],
          compiled_cache_size: 100,
          compiled_cache_hits: 800,
          compiled_cache_misses: 200,
          compiled_cache_hit_rate: 80,
          compiled_cache_max_size: 1000,
        },
        isLoading: false,
        isError: false,
        error: null,
        refetch: vi.fn(),
        isFetching: false,
      } as ReturnType<typeof useFilterStats>);
      render(<SecurityCenter />);
      fireEvent.click(screen.getByText('Filter Statistics'));
      await waitFor(() => {
        expect(screen.getByTestId('progress')).toBeInTheDocument();
      });
      expect(screen.getByTestId('progress')).toHaveAttribute('data-variant', 'warning');
    });
  });
});
