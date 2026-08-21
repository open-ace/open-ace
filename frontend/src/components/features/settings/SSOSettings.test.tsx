/**
 * SSOSettings Component Tests - Accessibility & Autofill Prevention
 *
 * Tests cover:
 * - aria-describedby attributes
 * - Visually hidden description elements
 * - Checkbox state and keyboard interaction
 * - Issue #2895: OAuth form autofill prevention (name/autoComplete attributes)
 */

import { render, screen, fireEvent } from '@testing-library/react';
import '@testing-library/jest-dom';
import { vi, describe, it, expect, beforeEach } from 'vitest';
import { SSOSettings } from './SSOSettings';

// Mock language hook
vi.mock('@/store', () => ({
  useLanguage: () => 'en',
}));

// Mock auth hook and admin tenant hook
vi.mock('@/hooks', () => ({
  useAuth: () => ({
    user: { tenant_id: 1, role: 'admin' },
  }),
  useAdminTenant: () => ({
    tenants: [
      {
        id: 1,
        name: 'Test Tenant',
        slug: 'test',
        status: 'active',
        plan: 'standard',
        created_at: '2026-01-01',
      },
    ],
    selectedTenantId: null,
    effectiveTenantId: 1,
    isLoading: false,
    error: null,
    selectTenant: vi.fn(),
    clearSelection: vi.fn(),
    retry: vi.fn(),
  }),
}));

// Mock permissions
vi.mock('@/utils/permissions', () => ({
  canManageAllTenants: () => false,
}));

// Mock i18n
vi.mock('@/i18n', () => ({
  t: (key: string) => {
    const translations: Record<string, string> = {
      ssoSettings: 'SSO Settings',
      ssoConfiguration: 'SSO Configuration',
      enableSSO: 'Enable SSO',
      // Issue #2128: Global SSO settings
      enableGlobalSSO: 'Enable Global SSO Login',
      globalSSODesc:
        'Control whether SSO login is available on the login page (affects all tenants)',
      globalSSOHint:
        'When enabled, users can sign in through configured SSO providers. This setting affects all tenants.',
      globalSSOWarning:
        'This setting affects all tenants. Disable with caution during security incidents.',
      ssoEnabledDesc: 'Enable SSO login for users through configured providers',
      ssoSystemSettingHint:
        'SSO enable switch has been moved to System Settings. Please configure SSO providers here.',
      autoProvisionUsers: 'Auto Provision Users',
      autoProvisionDesc: 'Automatically create user accounts on first SSO login',
      autoProvisionHint: 'Automatically create user accounts for this tenant on first SSO login',
      refresh: 'Refresh',
      addProvider: 'Add Provider',
      save: 'Save',
      loading: 'Loading...',
      settingsSaved: 'Settings saved successfully',
      saveFailed: 'Failed to save settings',
      failedToLoadSSOSettings: 'Failed to load SSO settings. Please refresh the page.',
      ssoSettingNotLoaded: 'SSO setting is still loading. Please wait.',
      ssoSettingVerificationFailed:
        'SSO setting verification failed. The saved value does not match. Please try again.',
      registeredProviders: 'Registered Providers',
      noProvidersRegistered: 'No SSO providers registered',
      availableProviders: 'Available Providers',
      providerName: 'Provider Name',
      type: 'Type',
      status: 'Status',
      tableActions: 'Actions',
      enabled: 'Enabled',
      disabled: 'Disabled',
      // Issue #2895: SSO Provider registration form
      registerProvider: 'Register Provider',
      selectProvider: 'Select Provider',
      providerType: 'Provider Type',
      clientId: 'Client ID',
      clientSecret: 'Client Secret',
      clientSecretConfirm: 'Confirm Client Secret',
      redirectUri: 'Redirect URI',
      scope: 'Scope',
      enterClientId: 'Enter Client ID',
      enterClientSecret: 'Enter Client Secret',
      enterClientSecretConfirm: 'Confirm Client Secret',
      enterRedirectUri: 'Enter Redirect URI',
      enterProviderName: 'Enter Provider Name',
      register: 'Register',
      cancel: 'Cancel',
    };
    return translations[key] || key;
  },
}));

// Mock API
vi.mock('@/api', () => ({
  ssoApi: {
    getProviders: vi.fn().mockResolvedValue({
      registered: [],
      predefined: [],
    }),
    registerProvider: vi.fn(),
    disableProvider: vi.fn(),
  },
  systemApi: {
    getSSOEnabled: vi.fn().mockResolvedValue({ sso_enabled: false }),
    updateSystemSettings: vi.fn().mockResolvedValue({ success: true, updated: ['sso_enabled'] }),
  },
  tenantApi: {
    getTenant: vi.fn().mockResolvedValue({
      id: 1,
      name: 'Test Tenant',
      settings: { sso_enabled: false, auto_provision_users: false },
    }),
    updateSettings: vi.fn().mockResolvedValue(undefined),
    listTenants: vi.fn(),
  },
}));

// Mock common components
vi.mock('@/components/common', () => ({
  Card: ({ title, children }: { title?: string; children: React.ReactNode }) => (
    <div className="card">
      {title && <div className="card-header">{title}</div>}
      <div className="card-body">{children}</div>
    </div>
  ),
  Button: ({
    children,
    onClick,
    type,
    form,
    loading,
    disabled,
    variant,
    size,
    ariaLabel,
  }: {
    children: React.ReactNode;
    onClick?: () => void;
    type?: string;
    form?: string;
    loading?: boolean;
    disabled?: boolean;
    variant?: string;
    size?: string;
    ariaLabel?: string;
  }) => (
    <button
      type={type || 'button'}
      form={form}
      onClick={onClick}
      disabled={disabled || loading}
      className={`btn btn-${variant || 'primary'} ${size ? `btn-${size}` : ''}`}
      aria-label={ariaLabel}
    >
      {loading ? 'Loading...' : children}
    </button>
  ),
  Loading: ({ text }: { text?: string }) => <div>{text || 'Loading...'}</div>,
  Error: ({ message, onRetry }: { message: string; onRetry?: () => void }) => (
    <div>
      <span>{message}</span>
      {onRetry && <button onClick={onRetry}>Retry</button>}
    </div>
  ),
  EmptyState: ({ title }: { title: string }) => <div>{title}</div>,
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
  }) =>
    isOpen ? (
      <div className="modal">
        <div className="modal-header">
          <h5>{title}</h5>
          <button onClick={onClose}>Close</button>
        </div>
        <div className="modal-body">{children}</div>
        {footer && <div className="modal-footer">{footer}</div>}
      </div>
    ) : null,
  TextInput: ({
    value,
    onChange,
    placeholder,
    type,
    id,
    name,
    autoComplete,
  }: {
    value: string;
    onChange: (value: string) => void;
    placeholder?: string;
    type?: string;
    id?: string;
    name?: string;
    autoComplete?: string;
  }) => (
    <input
      type={type || 'text'}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      placeholder={placeholder}
      id={id}
      name={name}
      autoComplete={autoComplete}
    />
  ),
  Select: ({
    options,
    value,
    onChange,
  }: {
    options: Array<{ value: string; label: string }>;
    value: string;
    onChange: (value: string) => void;
  }) => (
    <select value={value} onChange={(e) => onChange(e.target.value)}>
      {options.map((opt) => (
        <option key={opt.value} value={opt.value}>
          {opt.label}
        </option>
      ))}
    </select>
  ),
  Badge: ({ children, variant }: { children: React.ReactNode; variant?: string }) => (
    <span className={`badge badge-${variant || 'secondary'}`}>{children}</span>
  ),
  useToast: () => ({
    success: vi.fn(),
    error: vi.fn(),
    warning: vi.fn(),
    info: vi.fn(),
  }),
  useConfirm: () => vi.fn().mockResolvedValue(true),
}));

describe('SSOSettings Accessibility', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe('aria-describedby attributes', () => {
    it('should have aria-describedby on global SSO Enable checkbox', async () => {
      render(<SSOSettings />);

      // Issue #2128: The global SSO Enable checkbox
      const ssoEnabledInput = await screen.findByRole('checkbox', {
        name: /Enable Global SSO Login/i,
      });
      expect(ssoEnabledInput).toHaveAttribute('aria-describedby', 'globalSSODesc');
    });

    it('should have aria-describedby on Auto Provision checkbox', async () => {
      render(<SSOSettings />);

      // Wait for component to load
      const autoProvisionInput = await screen.findByRole('checkbox', {
        name: /Auto Provision Users/i,
      });
      expect(autoProvisionInput).toHaveAttribute('aria-describedby', 'autoProvisionDesc');
    });

    it('should reference valid description element IDs', async () => {
      render(<SSOSettings />);

      const ssoEnabledInput = await screen.findByRole('checkbox', {
        name: /Enable Global SSO Login/i,
      });
      const ssoDescId = ssoEnabledInput.getAttribute('aria-describedby');
      const ssoDescElement = document.getElementById(ssoDescId || '');
      expect(ssoDescElement).toBeInTheDocument();

      const autoProvisionInput = await screen.findByRole('checkbox', {
        name: /Auto Provision Users/i,
      });
      const autoDescId = autoProvisionInput.getAttribute('aria-describedby');
      const autoDescElement = document.getElementById(autoDescId || '');
      expect(autoDescElement).toBeInTheDocument();
    });
  });

  describe('Visually hidden description elements', () => {
    it('should have SSO description element with visually-hidden class', async () => {
      render(<SSOSettings />);

      const ssoDescElement = await screen.findByText(
        /Control whether SSO login is available on the login page/i
      );
      expect(ssoDescElement).toHaveClass('visually-hidden');
    });

    it('should have Auto Provision description element with visually-hidden class', async () => {
      render(<SSOSettings />);

      const autoDescElement = await screen.findByText(
        /Automatically create user accounts on first SSO login/i
      );
      expect(autoDescElement).toHaveClass('visually-hidden');
    });

    it('should have description elements with correct IDs', async () => {
      const { container } = render(<SSOSettings />);

      // Wait for checkboxes to be rendered
      await screen.findByRole('checkbox', { name: /Enable Global SSO Login/i });

      const ssoDescElement = container.querySelector('#globalSSODesc');
      expect(ssoDescElement).toBeInTheDocument();
      expect(ssoDescElement?.id).toBe('globalSSODesc');

      const autoDescElement = container.querySelector('#autoProvisionDesc');
      expect(autoDescElement).toBeInTheDocument();
      expect(autoDescElement?.id).toBe('autoProvisionDesc');
    });
  });

  describe('Checkbox state and interaction', () => {
    it('should render checkboxes with correct initial state', async () => {
      render(<SSOSettings />);

      const ssoEnabledInput = await screen.findByRole('checkbox', {
        name: /Enable Global SSO Login/i,
      });
      expect(ssoEnabledInput).not.toBeChecked();

      const autoProvisionInput = await screen.findByRole('checkbox', {
        name: /Auto Provision Users/i,
      });
      expect(autoProvisionInput).not.toBeChecked();
    });

    it('should toggle SSO checkbox on click', async () => {
      render(<SSOSettings />);

      const ssoEnabledInput = await screen.findByRole('checkbox', {
        name: /Enable Global SSO Login/i,
      });
      fireEvent.click(ssoEnabledInput);
      expect(ssoEnabledInput).toBeChecked();

      fireEvent.click(ssoEnabledInput);
      expect(ssoEnabledInput).not.toBeChecked();
    });

    it('should toggle Auto Provision checkbox on click', async () => {
      render(<SSOSettings />);

      const autoProvisionInput = await screen.findByRole('checkbox', {
        name: /Auto Provision Users/i,
      });
      fireEvent.click(autoProvisionInput);
      expect(autoProvisionInput).toBeChecked();

      fireEvent.click(autoProvisionInput);
      expect(autoProvisionInput).not.toBeChecked();
    });
  });

  describe('Form accessibility', () => {
    it('should have form element with submit button', async () => {
      render(<SSOSettings />);

      // Issue #2128: Now there are two Save buttons (global SSO and tenant settings)
      // Find the tenant settings Save button by its aria-label
      const tenantSaveButton = await screen.findByRole('button', {
        name: /Save tenant SSO settings/i,
      });

      // The tenant settings form's Save button should be type="submit"
      expect(tenantSaveButton).toBeInTheDocument();
      expect(tenantSaveButton).toHaveAttribute('type', 'submit');
    });

    it('should have global SSO save button', async () => {
      render(<SSOSettings />);

      // Issue #2128: Check for global SSO Save button
      const globalSaveButton = await screen.findByRole('button', {
        name: /Save global SSO setting/i,
      });

      expect(globalSaveButton).toBeInTheDocument();
    });

    it('should have labels properly associated with inputs', async () => {
      render(<SSOSettings />);

      const ssoEnabledInput = await screen.findByRole('checkbox', {
        name: /Enable Global SSO Login/i,
      });
      expect(ssoEnabledInput).toHaveAttribute('id', 'ssoEnabled');

      const autoProvisionInput = await screen.findByRole('checkbox', {
        name: /Auto Provision Users/i,
      });
      expect(autoProvisionInput).toHaveAttribute('id', 'autoProvision');
    });
  });

  describe('Screen reader reading order', () => {
    it('should have description elements positioned after labels', async () => {
      const { container } = render(<SSOSettings />);

      // Wait for checkboxes to be rendered
      await screen.findByRole('checkbox', { name: /Enable Global SSO Login/i });

      // The description span should be in the DOM after the label
      const formCheckDivs = container.querySelectorAll('.form-check');
      // Issue #2128: Now there are 2 form-check elements (global SSO + auto provision)
      expect(formCheckDivs.length).toBe(2);

      formCheckDivs.forEach((div) => {
        const input = div.querySelector('input');
        const label = div.querySelector('label');
        const desc = div.querySelector('span.visually-hidden');

        // Verify structure: input, label, description
        expect(input).toBeInTheDocument();
        expect(label).toBeInTheDocument();
        expect(desc).toBeInTheDocument();

        // Description should come after label in DOM order
        const children = Array.from(div.children);
        const labelIndex = children.indexOf(label!);
        const descIndex = children.indexOf(desc!);
        expect(descIndex).toBeGreaterThan(labelIndex);
      });
    });
  });
});

// Issue #2895: Autofill prevention tests
describe('SSOSettings OAuth Form Autofill Prevention (Issue #2895)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe('Register Provider Modal', () => {
    it('should have form with autoComplete="off"', async () => {
      const { container } = render(<SSOSettings />);

      // Wait for component to load and click "Add Provider" button
      await screen.findByRole('button', { name: /Add Provider/i });
      fireEvent.click(screen.getByRole('button', { name: /Add Provider/i }));

      // Find the register form
      const form = container.querySelector('#register-provider-form');
      expect(form).toBeInTheDocument();
      expect(form).toHaveAttribute('autocomplete', 'off');
    });

    it('should have Client ID input with correct name and autoComplete', async () => {
      const { container } = render(<SSOSettings />);

      await screen.findByRole('button', { name: /Add Provider/i });
      fireEvent.click(screen.getByRole('button', { name: /Add Provider/i }));

      const clientIdInput = container.querySelector('#register-client-id');
      expect(clientIdInput).toBeInTheDocument();
      expect(clientIdInput).toHaveAttribute('name', 'oauth_provider_client_id');
      expect(clientIdInput).toHaveAttribute('autocomplete', 'off');
    });

    it('should have Client Secret input with autoComplete="new-password"', async () => {
      const { container } = render(<SSOSettings />);

      await screen.findByRole('button', { name: /Add Provider/i });
      fireEvent.click(screen.getByRole('button', { name: /Add Provider/i }));

      const clientSecretInput = container.querySelector('#register-client-secret');
      expect(clientSecretInput).toBeInTheDocument();
      expect(clientSecretInput).toHaveAttribute('name', 'oauth_provider_client_secret');
      expect(clientSecretInput).toHaveAttribute('type', 'password');
      expect(clientSecretInput).toHaveAttribute('autocomplete', 'new-password');
    });

    it('should have Client Secret Confirm input with autoComplete="new-password"', async () => {
      const { container } = render(<SSOSettings />);

      await screen.findByRole('button', { name: /Add Provider/i });
      fireEvent.click(screen.getByRole('button', { name: /Add Provider/i }));

      const clientSecretConfirmInput = container.querySelector('#register-client-secret-confirm');
      expect(clientSecretConfirmInput).toBeInTheDocument();
      expect(clientSecretConfirmInput).toHaveAttribute(
        'name',
        'oauth_provider_client_secret_confirmation'
      );
      expect(clientSecretConfirmInput).toHaveAttribute('type', 'password');
      expect(clientSecretConfirmInput).toHaveAttribute('autocomplete', 'new-password');
    });

    it('should have submit button with form attribute pointing to register form', async () => {
      render(<SSOSettings />);

      await screen.findByRole('button', { name: /Add Provider/i });
      fireEvent.click(screen.getByRole('button', { name: /Add Provider/i }));

      // Find the Register button in modal
      const registerButton = await screen.findByRole('button', { name: /Register/i });
      expect(registerButton).toHaveAttribute('type', 'submit');
      expect(registerButton).toHaveAttribute('form', 'register-provider-form');
    });
  });
});
