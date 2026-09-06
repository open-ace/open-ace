/**
 * TenantSelector Component - Unified tenant selector for platform admins
 *
 * Features:
 * - Automatically shows tenant selector for platform admins
 * - Returns null for tenant admins (auto-resolved)
 * - Supports tenant search
 * - Displays loading and error states
 * - Provides retry functionality
 *
 * Issue #3274: Extracted from RemoteMachineManagement, SSOSettings, and SecurityCenter
 */

import React, { useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { useLanguage } from '@/store';
import { t } from '@/i18n';
import { useAdminTenant, useUser } from '@/hooks';
import { canManageAllTenants } from '@/utils/permissions';
import { Card, Button, Select, Loading, Error } from '@/components/common';

export interface TenantSelectorProps {
  /** Optional callback when tenant selection changes */
  onTenantChange?: (tenantId: number | null) => void;
  /** Whether to show clear button (default: true) */
  showClearButton?: boolean;
  /** Whether to show tenant search (default: true) */
  showSearch?: boolean;
  /** Disabled state */
  disabled?: boolean;
  /** Custom className */
  className?: string;
}

export const TenantSelector: React.FC<TenantSelectorProps> = ({
  onTenantChange,
  showClearButton = true,
  showSearch = true,
  disabled = false,
  className,
}) => {
  const language = useLanguage();
  const navigate = useNavigate();
  const user = useUser();
  const {
    tenants,
    selectedTenantId,
    selectTenant,
    clearSelection,
    isLoading,
    error,
    retry,
  } = useAdminTenant();

  // Check if user is platform admin
  const isPlatformAdmin = user ? canManageAllTenants(user) : false;

  // If not platform admin, don't show selector (tenant is auto-resolved)
  if (!isPlatformAdmin) {
    return null;
  }

  // Loading state
  if (isLoading) {
    return (
      <Card className={className}>
        <Loading size="md" text={t('loading', language)} />
      </Card>
    );
  }

  // Error state
  if (error) {
    return (
      <Card className={className}>
        <Error
          message={error}
          onRetry={retry}
          actions={
            <Button
              variant="outline-secondary"
              size="sm"
              onClick={() => navigate('/')}
              className="ms-2"
            >
              <i className="bi bi-house me-1" />
              {t('backToHome', language)}
            </Button>
          }
        />
      </Card>
    );
  }

  // No tenants available
  if (!tenants || tenants.length === 0) {
    return null;
  }

  // Sort tenants by name for consistent display and search
  const sortedTenants = useMemo(() => {
    return [...tenants].sort((a, b) => a.name.localeCompare(b.name));
  }, [tenants]);

  // Handle tenant selection
  const handleTenantSelect = (value: string) => {
    if (value === '') {
      clearSelection();
      onTenantChange?.(null);
    } else {
      const tenantId = Number(value);
      if (!isNaN(tenantId)) {
        selectTenant(tenantId);
        onTenantChange?.(tenantId);
      }
    }
  };

  // Handle clear selection
  const handleClearSelection = () => {
    clearSelection();
    onTenantChange?.(null);
  };

  // Filter tenants for search
  const [searchTerm, setSearchTerm] = React.useState('');

  const filteredTenants = useMemo(() => {
    if (!showSearch || !searchTerm) {
      return sortedTenants;
    }
    const term = searchTerm.toLowerCase();
    return sortedTenants.filter(
      (tenant) =>
        tenant.name.toLowerCase().includes(term) ||
        tenant.id.toString().includes(term)
    );
  }, [sortedTenants, searchTerm, showSearch]);

  return (
    <Card className={className}>
      <div className="row align-items-center">
        <div className="col-md-3">
          <label className="form-label mb-0 fw-semibold">
            <i className="bi bi-building me-2" />
            {t('selectTenant', language)}
          </label>
        </div>
        <div className="col-md-7">
          {showSearch && (
            <div className="mb-2">
              <input
                type="text"
                className="form-control form-control-sm"
                placeholder={t('searchTenantPlaceholder', language) || 'Search tenant...'}
                value={searchTerm}
                onChange={(e) => setSearchTerm(e.target.value)}
                disabled={disabled}
              />
            </div>
          )}
          <Select
            options={filteredTenants.map((tenant) => ({
              value: tenant.id.toString(),
              label: tenant.name,
            }))}
            value={selectedTenantId?.toString() ?? ''}
            onChange={handleTenantSelect}
            placeholder={t('selectTenantPlaceholder', language)}
            disabled={disabled}
          />
        </div>
        <div className="col-md-2">
          {showClearButton && selectedTenantId && (
            <Button
              variant="outline-secondary"
              size="sm"
              onClick={handleClearSelection}
              disabled={disabled}
              title={t('clearSelection', language)}
            >
              <i className="bi bi-x-lg" />
            </Button>
          )}
        </div>
      </div>
    </Card>
  );
};