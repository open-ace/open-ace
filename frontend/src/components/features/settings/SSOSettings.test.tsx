/**
 * SSOSettings Component Tests - Accessibility
 *
 * Tests cover:
 * - aria-describedby attributes
 * - Visually hidden description elements
 * - Checkbox state and keyboard interaction
 */

import { render, screen, fireEvent } from '@testing-library/react';
import '@testing-library/jest-dom';
import { vi, describe, it, expect, beforeEach } from 'vitest';
import { SSOSettings } from './SSOSettings';

// Mock language hook
vi.mock('@/store', () => ({
  useLanguage: () => 'en',
}));

// Mock auth hook
vi.mock('@/hooks', () => ({
  useAuth: () => ({
    user: { tenant_id: 1, role: 'admin' },
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
      ssoEnabledDesc: 'Enable SSO login for users through configured providers',
      autoProvisionUsers: 'Auto Provision Users',
      autoProvisionDesc: 'Automatically create user accounts on first SSO login',
      refresh: 'Refresh',
      addProvider: 'Add Provider',
      save: 'Save',
      loading: 'Loading...',
      settingsSaved: 'Settings saved successfully',
      registeredProviders: 'Registered Providers',
      noProvidersRegistered: 'No SSO providers registered',
      availableProviders: 'Available Providers',
      providerName: 'Provider Name',
      type: 'Type',
      status: 'Status',
      tableActions: 'Actions',
      enabled: 'Enabled',
      disabled: 'Disabled',
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
    loading,
    disabled,
    variant,
    size,
  }: {
    children: React.ReactNode;
    onClick?: () => void;
    type?: string;
    loading?: boolean;
    disabled?: boolean;
    variant?: string;
    size?: string;
  }) => (
    <button
      type={type || 'button'}
      onClick={onClick}
      disabled={disabled || loading}
      className={`btn btn-${variant || 'primary'} ${size ? `btn-${size}` : ''}`}
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
  }: {
    isOpen: boolean;
    onClose: () => void;
    title: string;
    children: React.ReactNode;
  }) =>
    isOpen ? (
      <div className="modal">
        <div className="modal-header">
          <h5>{title}</h5>
          <button onClick={onClose}>Close</button>
        </div>
        <div className="modal-body">{children}</div>
      </div>
    ) : null,
  TextInput: ({
    value,
    onChange,
    placeholder,
    type,
  }: {
    value: string;
    onChange: (value: string) => void;
    placeholder?: string;
    type?: string;
  }) => (
    <input
      type={type || 'text'}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      placeholder={placeholder}
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
        <option key={opt.value} value={opt.value}>{opt.label}</option>
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
    it('should have aria-describedby on SSO Enable checkbox', async () => {
      render(<SSOSettings />);

      // Wait for component to load
      const ssoEnabledInput = await screen.findByRole('checkbox', { name: /Enable SSO/i });
      expect(ssoEnabledInput).toHaveAttribute('aria-describedby', 'ssoEnabledDesc');
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

      const ssoEnabledInput = await screen.findByRole('checkbox', { name: /Enable SSO/i });
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
        /Enable SSO login for users through configured providers/i
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
      await screen.findByRole('checkbox', { name: /Enable SSO/i });

      const ssoDescElement = container.querySelector('#ssoEnabledDesc');
      expect(ssoDescElement).toBeInTheDocument();
      expect(ssoDescElement?.id).toBe('ssoEnabledDesc');

      const autoDescElement = container.querySelector('#autoProvisionDesc');
      expect(autoDescElement).toBeInTheDocument();
      expect(autoDescElement?.id).toBe('autoProvisionDesc');
    });
  });

  describe('Checkbox state and interaction', () => {
    it('should render checkboxes with correct initial state', async () => {
      render(<SSOSettings />);

      const ssoEnabledInput = await screen.findByRole('checkbox', { name: /Enable SSO/i });
      expect(ssoEnabledInput).not.toBeChecked();

      const autoProvisionInput = await screen.findByRole('checkbox', {
        name: /Auto Provision Users/i,
      });
      expect(autoProvisionInput).not.toBeChecked();
    });

    it('should toggle SSO checkbox on click', async () => {
      render(<SSOSettings />);

      const ssoEnabledInput = await screen.findByRole('checkbox', { name: /Enable SSO/i });
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

      const saveButton = await screen.findByRole('button', { name: /Save/i });
      expect(saveButton).toHaveAttribute('type', 'submit');
    });

    it('should have labels properly associated with inputs', async () => {
      render(<SSOSettings />);

      const ssoEnabledInput = await screen.findByRole('checkbox', { name: /Enable SSO/i });
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
      await screen.findByRole('checkbox', { name: /Enable SSO/i });

      // The description span should be in the DOM after the label
      const formCheckDivs = container.querySelectorAll('.form-check');
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