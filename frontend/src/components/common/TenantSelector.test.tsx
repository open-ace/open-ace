/**
 * TenantSelector Component Tests
 *
 * Issue #3274: Tests for unified tenant selector component
 */

import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { BrowserRouter } from 'react-router-dom';
import { vi } from 'vitest';
import { TenantSelector } from './TenantSelector';

// Mock hooks
vi.mock('@/hooks', () => ({
  useAdminTenant: vi.fn(),
  useUser: vi.fn(),
}));

vi.mock('@/utils/permissions', () => ({
  canManageAllTenants: vi.fn(),
}));

vi.mock('@/i18n', () => ({
  t: (key: string) => key,
}));

// Mock navigate
const mockNavigate = vi.fn();
vi.mock('react-router-dom', async (importOriginal) => {
  const actual = await importOriginal();
  return {
    ...actual,
    useNavigate: () => mockNavigate,
  };
});

// Mock useLanguage
vi.mock('@/store', () => ({
  useLanguage: () => 'en',
}));

import * as hooks from '@/hooks';
import * as permissions from '@/utils/permissions';

const mockUseAdminTenant = hooks.useAdminTenant as vi.MockedFunction<typeof hooks.useAdminTenant>;
const mockUseUser = hooks.useUser as vi.MockedFunction<typeof hooks.useUser>;
const mockCanManageAllTenants = permissions.canManageAllTenants as vi.MockedFunction<
  typeof permissions.canManageAllTenants
>;

const mockTenants = [
  { id: 1, name: 'Tenant A', slug: 'tenant-a', status: 'active', plan: 'standard' },
  { id: 2, name: 'Tenant B', slug: 'tenant-b', status: 'active', plan: 'premium' },
  { id: 3, name: 'Tenant C', slug: 'tenant-c', status: 'active', plan: 'enterprise' },
];

const mockUser = {
  id: 'user-1',
  username: 'admin',
  role: 'platform_admin',
  tenant_id: null,
};

describe('TenantSelector', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockUseUser.mockReturnValue(mockUser as any);
    mockCanManageAllTenants.mockReturnValue(true);
  });

  const renderWithRouter = (component: React.ReactElement) => {
    return render(<BrowserRouter>{component}</BrowserRouter>);
  };

  it('should return null for tenant admins', () => {
    mockCanManageAllTenants.mockReturnValue(false);
    mockUseAdminTenant.mockReturnValue({
      tenants: [],
      selectedTenantId: null,
      selectTenant: vi.fn(),
      clearSelection: vi.fn(),
      retry: vi.fn(),
      isLoading: false,
      error: null,
      effectiveTenantId: 100,
    } as any);

    const { container } = renderWithRouter(<TenantSelector />);
    expect(container.firstChild).toBeNull();
  });

  it('should show loading state', () => {
    mockUseAdminTenant.mockReturnValue({
      tenants: [],
      selectedTenantId: null,
      selectTenant: vi.fn(),
      clearSelection: vi.fn(),
      retry: vi.fn(),
      isLoading: true,
      error: null,
      effectiveTenantId: null,
    } as any);

    renderWithRouter(<TenantSelector />);
    // Use getAllByText since Loading component may have multiple loading texts
    expect(screen.getAllByText('loading').length).toBeGreaterThan(0);
  });

  it('should show error state with retry button', () => {
    const mockRetry = vi.fn();
    mockUseAdminTenant.mockReturnValue({
      tenants: [],
      selectedTenantId: null,
      selectTenant: vi.fn(),
      clearSelection: vi.fn(),
      retry: mockRetry,
      isLoading: false,
      error: 'Failed to load tenants',
      effectiveTenantId: null,
    } as any);

    renderWithRouter(<TenantSelector />);
    expect(screen.getByText('Failed to load tenants')).toBeInTheDocument();

    const retryButton = screen.getByText('Retry');
    fireEvent.click(retryButton);
    expect(mockRetry).toHaveBeenCalled();
  });

  it('should return null when no tenants available', () => {
    mockUseAdminTenant.mockReturnValue({
      tenants: [],
      selectedTenantId: null,
      selectTenant: vi.fn(),
      clearSelection: vi.fn(),
      retry: vi.fn(),
      isLoading: false,
      error: null,
      effectiveTenantId: null,
    } as any);

    const { container } = renderWithRouter(<TenantSelector />);
    expect(container.firstChild).toBeNull();
  });

  it('should display tenant selector with search', () => {
    const mockSelectTenant = vi.fn();
    mockUseAdminTenant.mockReturnValue({
      tenants: mockTenants,
      selectedTenantId: null,
      selectTenant: mockSelectTenant,
      clearSelection: vi.fn(),
      retry: vi.fn(),
      isLoading: false,
      error: null,
      effectiveTenantId: null,
    } as any);

    renderWithRouter(<TenantSelector showSearch={true} />);

    // Check search input (placeholder will be the translation key since t() returns key)
    expect(screen.getByPlaceholderText('searchTenantPlaceholder')).toBeInTheDocument();

    // Check select dropdown
    const select = screen.getByRole('combobox');
    expect(select).toBeInTheDocument();

    // Check that tenants are sorted alphabetically
    const options = screen.getAllByRole('option');
    expect(options[1]).toHaveTextContent('Tenant A'); // First option after placeholder
    expect(options[2]).toHaveTextContent('Tenant B');
    expect(options[3]).toHaveTextContent('Tenant C');
  });

  it('should filter tenants by search term', async () => {
    mockUseAdminTenant.mockReturnValue({
      tenants: mockTenants,
      selectedTenantId: null,
      selectTenant: vi.fn(),
      clearSelection: vi.fn(),
      retry: vi.fn(),
      isLoading: false,
      error: null,
      effectiveTenantId: null,
    } as any);

    renderWithRouter(<TenantSelector showSearch={true} />);

    const searchInput = screen.getByPlaceholderText('searchTenantPlaceholder');
    fireEvent.change(searchInput, { target: { value: 'Tenant B' } });

    await waitFor(() => {
      const options = screen.getAllByRole('option');
      // Should only show Tenant B (and placeholder)
      expect(options.length).toBe(2);
    });
  });

  it('should call selectTenant when tenant is selected', () => {
    const mockSelectTenant = vi.fn();
    const mockOnChange = vi.fn();
    mockUseAdminTenant.mockReturnValue({
      tenants: mockTenants,
      selectedTenantId: null,
      selectTenant: mockSelectTenant,
      clearSelection: vi.fn(),
      retry: vi.fn(),
      isLoading: false,
      error: null,
      effectiveTenantId: null,
    } as any);

    renderWithRouter(<TenantSelector onTenantChange={mockOnChange} />);

    const select = screen.getByRole('combobox');
    fireEvent.change(select, { target: { value: '1' } });

    expect(mockSelectTenant).toHaveBeenCalledWith(1);
    expect(mockOnChange).toHaveBeenCalledWith(1);
  });

  it('should show clear button when tenant is selected', () => {
    const mockClearSelection = vi.fn();
    const mockOnChange = vi.fn();
    mockUseAdminTenant.mockReturnValue({
      tenants: mockTenants,
      selectedTenantId: 1,
      selectTenant: vi.fn(),
      clearSelection: mockClearSelection,
      retry: vi.fn(),
      isLoading: false,
      error: null,
      effectiveTenantId: 1,
    } as any);

    renderWithRouter(<TenantSelector showClearButton={true} onTenantChange={mockOnChange} />);

    // Find the clear button by its icon
    const clearButton = screen.getByRole('button', { name: /clearSelection/i });
    fireEvent.click(clearButton);

    expect(mockClearSelection).toHaveBeenCalled();
    expect(mockOnChange).toHaveBeenCalledWith(null);
  });

  it('should not show clear button when showClearButton is false', () => {
    mockUseAdminTenant.mockReturnValue({
      tenants: mockTenants,
      selectedTenantId: 1,
      selectTenant: vi.fn(),
      clearSelection: vi.fn(),
      retry: vi.fn(),
      isLoading: false,
      error: null,
      effectiveTenantId: 1,
    } as any);

    renderWithRouter(<TenantSelector showClearButton={false} />);

    expect(screen.queryByRole('button', { name: /clear selection/i })).not.toBeInTheDocument();
  });

  it('should disable controls when disabled prop is true', () => {
    mockUseAdminTenant.mockReturnValue({
      tenants: mockTenants,
      selectedTenantId: 1,
      selectTenant: vi.fn(),
      clearSelection: vi.fn(),
      retry: vi.fn(),
      isLoading: false,
      error: null,
      effectiveTenantId: 1,
    } as any);

    renderWithRouter(<TenantSelector disabled={true} />);

    const select = screen.getByRole('combobox');
    expect(select).toBeDisabled();

    const clearButton = screen.queryByRole('button', { name: /clear selection/i });
    if (clearButton) {
      expect(clearButton).toBeDisabled();
    }
  });
});
