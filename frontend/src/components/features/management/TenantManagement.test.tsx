/**
 * TenantManagement Component Tests
 *
 * Issue #3137: Tests for free plan support
 *
 * Tests cover:
 * - Free plan appears in filter, create, and edit form options
 * - Free tenant displays correctly with proper badge style
 * - Editing free tenant preserves plan value
 * - Unknown plan values are handled gracefully with fallback
 */

import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import '@testing-library/jest-dom';
import { vi, describe, it, expect, beforeEach } from 'vitest';

// Import component and API before mocking (Vitest hoists mocks automatically)
import { TenantManagement } from './TenantManagement';
import { tenantApi } from '@/api';

const mockTenantApi = tenantApi as unknown as {
  listTenants: ReturnType<typeof vi.fn>;
  createTenant: ReturnType<typeof vi.fn>;
  updateTenant: ReturnType<typeof vi.fn>;
  deleteTenant: ReturnType<typeof vi.fn>;
  suspendTenant: ReturnType<typeof vi.fn>;
  activateTenant: ReturnType<typeof vi.fn>;
};

// Mock language hook
vi.mock('@/store', () => ({
  useLanguage: () => 'en',
}));

// Mock i18n
vi.mock('@/i18n', () => ({
  t: (key: string) => {
    const translations: Record<string, string> = {
      tenantPlanFree: 'Free',
      tenantPlanStandard: 'Standard',
      tenantPlanPremium: 'Premium',
      tenantPlanEnterprise: 'Enterprise',
      tenantAllPlans: 'All Plans',
      tenantManagement: 'Tenant Management',
      addTenant: 'Add Tenant',
      editTenant: 'Edit Tenant',
      plan: 'Plan',
      status: 'Status',
      save: 'Save',
      cancel: 'Cancel',
      loading: 'Loading...',
      noTenantsFound: 'No tenants found',
      tenantName: 'Tenant Name',
      slug: 'Slug',
      createdAt: 'Created At',
      tableActions: 'Actions',
      edit: 'Edit',
      editQuota: 'Edit Quota',
      suspend: 'Suspend',
      activate: 'Activate',
      delete: 'Delete',
      totalTenants: 'Total Tenants',
      activeTenants: 'Active Tenants',
      suspendedTenants: 'Suspended Tenants',
      trialTenants: 'Trial Tenants',
      confirmSuspendTenant: 'Are you sure you want to suspend this tenant?',
      confirmDeleteTenant: 'Are you sure you want to delete this tenant?',
      tenantNameRequired: 'Tenant name is required',
    };
    return translations[key] || key;
  },
}));

// Mock common components
vi.mock('@/components/common', () => ({
  Card: ({ children }: { children: React.ReactNode }) => <div data-testid="card">{children}</div>,
  StatCard: ({ label, value }: { label: string; value: string }) => (
    <div data-testid="stat-card">
      <div>{label}</div>
      <div>{value}</div>
    </div>
  ),
  Button: ({
    children,
    onClick,
    disabled,
    variant,
    title,
  }: {
    children: React.ReactNode;
    onClick?: () => void;
    disabled?: boolean;
    variant?: string;
    title?: string;
  }) => (
    <button onClick={onClick} disabled={disabled} data-variant={variant} title={title}>
      {children}
    </button>
  ),
  Select: ({
    options,
    value,
    onChange,
  }: {
    options: Array<{ value: string; label: string }>;
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
      data-testid={placeholder}
    />
  ),
  Loading: ({ text }: { text: string }) => <div data-testid="loading">{text}</div>,
  Error: ({ message, onRetry }: { message: string; onRetry?: () => void }) => (
    <div data-testid="error">
      <div>{message}</div>
      {onRetry && <button onClick={onRetry}>Retry</button>}
    </div>
  ),
  EmptyState: ({ title }: { title: string }) => <div data-testid="empty-state">{title}</div>,
  Modal: ({
    isOpen,
    onClose,
    title,
    footer,
    children,
  }: {
    isOpen: boolean;
    onClose: () => void;
    title: string;
    footer?: React.ReactNode;
    children: React.ReactNode;
  }) =>
    isOpen ? (
      <div data-testid="modal" role="dialog">
        <div data-testid="modal-title">{title}</div>
        <div data-testid="modal-content">{children}</div>
        {footer && <div data-testid="modal-footer">{footer}</div>}
        <button onClick={onClose} aria-label="Close">
          ×
        </button>
      </div>
    ) : null,
  Badge: ({ variant, children }: { variant: string; children: React.ReactNode }) => (
    <span data-testid="badge" data-variant={variant}>
      {children}
    </span>
  ),
  PageRefreshControl: () => <div data-testid="page-refresh-control" />,
  useConfirm: () => async () => true,
  useToast: () => ({
    success: vi.fn(),
    error: vi.fn(),
    warning: vi.fn(),
    info: vi.fn(),
  }),
}));

// Mock API
vi.mock('@/api', () => ({
  tenantApi: {
    listTenants: vi.fn(),
    createTenant: vi.fn(),
    updateTenant: vi.fn(),
    deleteTenant: vi.fn(),
    suspendTenant: vi.fn(),
    activateTenant: vi.fn(),
  },
}));

// Mock utils
vi.mock('@/utils', () => ({
  cn: (...args: string[]) => args.join(' '),
  formatDateTime: (date: string) => date,
  createMatcherConfig: () => ({}),
}));

vi.mock('@/utils/quotaFormatter', () => ({
  formatQuotaForDisplay: (value: number) => `${value}M`,
}));

vi.mock('@/constants/quota', () => ({
  TOKEN_QUOTA_MULTIPLIER: 1000000,
}));

// Issue #3203: Mock canManageTenant for permission checks
vi.mock('@/utils/permissions', () => ({
  canManageTenant: () => true,
}));

vi.mock('@/hooks', () => ({
  usePageRefresh: () => ({
    refresh: {},
  }),
  // Issue #3203: Mock useAuth for permission checks
  useAuth: () => ({
    user: {
      id: 'test-user',
      role: 'admin',
      tenant_id: null,
    },
    isAuthenticated: true,
    isLoading: false,
  }),
}));

describe('TenantManagement - Issue #3137', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe('Filter Options', () => {
    it('should include "free" in plan filter dropdown', async () => {
      mockTenantApi.listTenants.mockResolvedValueOnce({ tenants: [], count: 0 });

      render(<TenantManagement />);

      await waitFor(() => {
        expect(screen.queryByTestId('loading')).not.toBeInTheDocument();
      });

      // Find plan filter select
      const selects = screen.getAllByTestId('select');
      const planSelect = selects.find((select) => {
        const options = within(select).getAllByRole('option');
        return options.some((opt) => opt.textContent === 'All Plans');
      });

      expect(planSelect).toBeDefined();

      // Check that free option exists
      const freeOption = within(planSelect!).getByRole('option', { name: 'Free' });
      expect(freeOption).toBeInTheDocument();
      expect(freeOption).toHaveValue('free');
    });

    it('should display free plan options in correct order (free first)', async () => {
      mockTenantApi.listTenants.mockResolvedValueOnce({ tenants: [], count: 0 });

      render(<TenantManagement />);

      await waitFor(() => {
        expect(screen.queryByTestId('loading')).not.toBeInTheDocument();
      });

      const selects = screen.getAllByTestId('select');
      const planSelect = selects.find((select) => {
        const options = within(select).getAllByRole('option');
        return options.some((opt) => opt.textContent === 'All Plans');
      });

      const options = within(planSelect!).getAllByRole('option');
      const optionTexts = options.map((opt) => opt.textContent);

      // Free should come before standard
      const freeIndex = optionTexts.indexOf('Free');
      const standardIndex = optionTexts.indexOf('Standard');
      expect(freeIndex).toBeLessThan(standardIndex);
    });
  });

  describe('Create Tenant Form', () => {
    it('should include "free" in plan options when creating tenant', async () => {
      mockTenantApi.listTenants.mockResolvedValueOnce({ tenants: [], count: 0 });

      render(<TenantManagement />);

      await waitFor(() => {
        expect(screen.queryByTestId('loading')).not.toBeInTheDocument();
      });

      // Click add tenant button
      const addButton = screen.getByText('Add Tenant');
      fireEvent.click(addButton);

      await waitFor(() => {
        expect(screen.getByTestId('modal')).toBeInTheDocument();
      });

      // Find plan select in modal
      const modal = screen.getByTestId('modal');
      const planSelect = within(modal).getByTestId('select');

      // Check that free option exists
      const freeOption = within(planSelect).getByRole('option', { name: 'Free' });
      expect(freeOption).toBeInTheDocument();
    });

    it('should allow selecting "free" plan when creating tenant', async () => {
      mockTenantApi.listTenants.mockResolvedValueOnce({ tenants: [], count: 0 });
      mockTenantApi.createTenant.mockResolvedValueOnce({
        id: 1,
        name: 'Test Tenant',
        slug: 'test-tenant',
        plan: 'free',
        status: 'active',
        created_at: '2024-01-01',
      });

      render(<TenantManagement />);

      await waitFor(() => {
        expect(screen.queryByTestId('loading')).not.toBeInTheDocument();
      });

      // Open create modal
      fireEvent.click(screen.getByText('Add Tenant'));

      await waitFor(() => {
        expect(screen.getByTestId('modal')).toBeInTheDocument();
      });

      // Select free plan
      const modal = screen.getByTestId('modal');
      const planSelect = within(modal).getByTestId('select');
      fireEvent.change(planSelect, { target: { value: 'free' } });

      // Verify free is selected
      expect(planSelect).toHaveValue('free');
    });
  });

  describe('Edit Tenant Form', () => {
    it('should display correct initial plan value when editing free tenant', async () => {
      const freeTenant = {
        id: 1,
        name: 'Free Tenant',
        slug: 'free-tenant',
        plan: 'free',
        status: 'active',
        created_at: '2024-01-01',
      };

      mockTenantApi.listTenants.mockResolvedValueOnce({ tenants: [freeTenant], count: 1 });

      render(<TenantManagement />);

      await waitFor(() => {
        expect(screen.queryByTestId('loading')).not.toBeInTheDocument();
      });

      // Click edit button
      const editButtons = screen.getAllByTitle('Edit');
      fireEvent.click(editButtons[0]);

      await waitFor(() => {
        expect(screen.getByTestId('modal')).toBeInTheDocument();
      });

      // Verify plan select shows free
      const modal = screen.getByTestId('modal');
      const planSelect = within(modal).getByTestId('select');
      expect(planSelect).toHaveValue('free');
    });

    it('should preserve free plan when editing tenant without changing plan', async () => {
      const freeTenant = {
        id: 1,
        name: 'Free Tenant',
        slug: 'free-tenant',
        plan: 'free',
        status: 'active',
        created_at: '2024-01-01',
      };

      mockTenantApi.listTenants.mockResolvedValueOnce({ tenants: [freeTenant], count: 1 });
      mockTenantApi.updateTenant.mockResolvedValueOnce({ ...freeTenant, name: 'Updated Tenant' });

      render(<TenantManagement />);

      await waitFor(() => {
        expect(screen.queryByTestId('loading')).not.toBeInTheDocument();
      });

      // Open edit modal
      const editButtons = screen.getAllByTitle('Edit');
      fireEvent.click(editButtons[0]);

      await waitFor(() => {
        expect(screen.getByTestId('modal')).toBeInTheDocument();
      });

      // Update name without changing plan
      const modal = screen.getByTestId('modal');
      // Note: In real implementation, we would update the name input

      // Click save
      const saveButton = within(modal).getByText('Save');
      fireEvent.click(saveButton);

      await waitFor(() => {
        expect(mockTenantApi.updateTenant).toHaveBeenCalled();
      });

      // Verify that update was called with free plan
      const updateCall = mockTenantApi.updateTenant.mock.calls[0];
      expect(updateCall[1]).toMatchObject({ plan: 'free' });
    });
  });

  describe('Badge Display', () => {
    it('should display correct label for free tenant', async () => {
      const freeTenant = {
        id: 1,
        name: 'Free Tenant',
        slug: 'free-tenant',
        plan: 'free',
        status: 'active',
        created_at: '2024-01-01',
      };

      mockTenantApi.listTenants.mockResolvedValueOnce({ tenants: [freeTenant], count: 1 });

      render(<TenantManagement />);

      await waitFor(() => {
        expect(screen.queryByTestId('loading')).not.toBeInTheDocument();
      });

      // Find badge with "Free" text
      const badges = screen.getAllByTestId('badge');
      const freeBadge = badges.find((badge) => badge.textContent === 'Free');
      expect(freeBadge).toBeInTheDocument();
    });

    it('should use "secondary" variant for free tenant badge', async () => {
      const freeTenant = {
        id: 1,
        name: 'Free Tenant',
        slug: 'free-tenant',
        plan: 'free',
        status: 'active',
        created_at: '2024-01-01',
      };

      mockTenantApi.listTenants.mockResolvedValueOnce({ tenants: [freeTenant], count: 1 });

      render(<TenantManagement />);

      await waitFor(() => {
        expect(screen.queryByTestId('loading')).not.toBeInTheDocument();
      });

      // Find badge with "Free" text
      const badges = screen.getAllByTestId('badge');
      const freeBadge = badges.find((badge) => badge.textContent === 'Free');

      // Verify variant is secondary
      expect(freeBadge).toHaveAttribute('data-variant', 'secondary');
    });
  });

  describe('Fallback Behavior', () => {
    it('should display raw plan value for unknown plan', async () => {
      const unknownPlanTenant = {
        id: 1,
        name: 'Unknown Plan Tenant',
        slug: 'unknown-plan-tenant',
        plan: 'custom_plan',
        status: 'active',
        created_at: '2024-01-01',
      };

      mockTenantApi.listTenants.mockResolvedValueOnce({ tenants: [unknownPlanTenant], count: 1 });

      render(<TenantManagement />);

      await waitFor(() => {
        expect(screen.queryByTestId('loading')).not.toBeInTheDocument();
      });

      // Find badge - should display raw value
      const badges = screen.getAllByTestId('badge');
      const planBadge = badges.find((badge) => badge.textContent === 'custom_plan');
      expect(planBadge).toBeInTheDocument();
    });

    it('should not crash with unknown plan value', async () => {
      const unknownPlanTenant = {
        id: 1,
        name: 'Unknown Plan Tenant',
        slug: 'unknown-plan-tenant',
        plan: 'very_custom_plan',
        status: 'active',
        created_at: '2024-01-01',
      };

      mockTenantApi.listTenants.mockResolvedValueOnce({ tenants: [unknownPlanTenant], count: 1 });

      // Should render without error
      render(<TenantManagement />);

      await waitFor(() => {
        expect(screen.queryByTestId('loading')).not.toBeInTheDocument();
      });

      // Should display table
      expect(screen.getByText('Unknown Plan Tenant')).toBeInTheDocument();
    });
  });
});
