/**
 * TenantManagement Component - Tenant management page
 *
 * Features:
 * - Tenant list with filters
 * - Create/Edit/Delete tenants
 * - Quota management
 * - Suspend/Activate tenants
 * - Quota checking with result modal (Issue #3132)
 * - Tenant settings management with content filter toggle (Issue #3203)
 * - Audit log settings management (Issue #3204)
 */

import React, { useState, useEffect, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { cn, createMatcherConfig } from '@/utils';
import { useLanguage } from '@/store';
import { t, type Language } from '@/i18n';
import {
  Card,
  StatCard,
  Button,
  Select,
  Loading,
  Error,
  EmptyState,
  Modal,
  TextInput,
  Badge,
  PageRefreshControl,
} from '@/components/common';
import { useConfirm, useToast } from '@/components/common';
import {
  tenantApi,
  type Tenant,
  type TenantPlan,
  type TenantSettings,
  type CreateTenantRequest,
  type UpdateTenantRequest,
} from '@/api';
import { formatDateTime } from '@/utils';
import { usePageRefresh, useAuth } from '@/hooks';
import { useTenantQuotaCheck } from '@/hooks/useTenantQuotaCheck';
import { TOKEN_QUOTA_MULTIPLIER } from '@/constants/quota';
import { formatQuotaForDisplay, formatNumberAsString } from '@/utils/quotaFormatter';
import { canManageTenant } from '@/utils/permissions';
import { QuotaCheckResultModal } from './QuotaCheckResultModal';

// Tenant i18n fixes (Issue #1500)
// Type-safe label mappings for plan and status
// Issue #3137: Add 'free' plan support
const PLAN_LABELS: Record<string, string> = {
  free: 'tenantPlanFree',
  standard: 'tenantPlanStandard',
  premium: 'tenantPlanPremium',
  enterprise: 'tenantPlanEnterprise',
};

const STATUS_LABELS: Record<string, string> = {
  active: 'tenantStatusActive',
  suspended: 'tenantStatusSuspended',
  trial: 'tenantStatusTrial',
};

// Helper functions for translation with fallback
const getPlanLabel = (plan: string, language: Language): string => {
  const key = PLAN_LABELS[plan];
  return key ? t(key, language) : plan;
};

const getStatusLabel = (status: string, language: Language): string => {
  const key = STATUS_LABELS[status];
  return key ? t(key, language) : status;
};

// Dynamic options generators
const getTenantStatusOptions = (language: Language) => [
  { value: '', label: t('tenantAllStatuses', language) },
  { value: 'active', label: t('tenantStatusActive', language) },
  { value: 'suspended', label: t('tenantStatusSuspended', language) },
  { value: 'trial', label: t('tenantStatusTrial', language) },
];

const getTenantPlanOptions = (language: Language, includeAll: boolean = true) => {
  const options = includeAll ? [{ value: '', label: t('tenantAllPlans', language) }] : [];
  return [
    ...options,
    { value: 'free', label: t('tenantPlanFree', language) },
    { value: 'standard', label: t('tenantPlanStandard', language) },
    { value: 'premium', label: t('tenantPlanPremium', language) },
    { value: 'enterprise', label: t('tenantPlanEnterprise', language) },
  ];
};

// Tenant API error translation map (moved outside component to avoid recreation)
const TENANT_ERROR_MAP: Record<string, string> = {
  'Failed to create tenant': 'failedToCreateTenant',
  'Tenant slug already exists': 'tenantSlugExists',
  'Failed to save tenant': 'failedToCreateTenant',
  'Tenant not found': 'tenantNotFound',
  'Failed to update tenant': 'failedToUpdateTenant',
  'Failed to update tenant quota': 'failedToUpdateTenantQuota',
  'Request body required': 'requestBodyRequired',
  'No valid fields to update': 'noValidFieldsToUpdate',
};

const translateTenantError = (error: string, lang: Language): string => {
  const key = TENANT_ERROR_MAP[error];
  return key ? t(key, lang) : error;
};

// Trial days validation (Issue #1501)
const validateTrialDays = (
  value: string | number | undefined,
  language: Language
): string | null => {
  if (value === undefined || value === null || value === '') {
    return null; // Optional field, empty is valid
  }
  const num = Number(value);
  if (!Number.isInteger(num) || num <= 0) {
    return t('validationTrialDaysPositive', language);
  }
  if (num > 365) {
    return t('validationTrialDaysMax', language);
  }
  return null;
};

// Audit log retention days validation (Issue #3204)
const validateRetentionDays = (
  value: string | number | undefined,
  language: Language
): string | null => {
  if (value === undefined || value === null || value === '') {
    return null; // Optional field, empty uses default
  }
  const num = Number(value);
  if (!Number.isInteger(num) || num < 1 || num > 365) {
    return t('validationRetentionDaysRange', language);
  }
  return null;
};

export const TenantManagement: React.FC = () => {
  const language = useLanguage();
  const toast = useToast();
  const { user } = useAuth();
  const navigate = useNavigate();
  const [tenants, setTenants] = useState<Tenant[]>([]);

  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [statusFilter, setStatusFilter] = useState('');
  const [planFilter, setPlanFilter] = useState('');

  const [showModal, setShowModal] = useState(false);
  const [showQuotaModal, setShowQuotaModal] = useState(false);
  const [editingTenant, setEditingTenant] = useState<Tenant | null>(null);
  const [formData, setFormData] = useState<CreateTenantRequest>({
    name: '',
    slug: '',
    plan: 'standard',
    contact_email: '',
    contact_name: '',
    trial_days: undefined,
  });
  const [createAdminAccount, setCreateAdminAccount] = useState(false);
  const [adminFormData, setAdminFormData] = useState({
    admin_username: '',
    admin_password: '',
    admin_email: '',
  });
  const [quotaData, setQuotaData] = useState({
    daily_token_limit: 1,
    monthly_token_limit: 30,
    daily_request_limit: 10000,
    monthly_request_limit: 300000,
    max_users: 100,
    max_sessions_per_user: 5,
  });
  // Issue #3203: Settings and original data for change detection
  // Issue #3204: Add audit_log_enabled and audit_log_retention_days
  const [settingsData, setSettingsData] = useState<TenantSettings>({
    content_filter_enabled: true,
    audit_log_enabled: true,
    audit_log_retention_days: 90,
  });
  const [originalQuota, setOriginalQuota] = useState({
    daily_token_limit: 1,
    monthly_token_limit: 30,
    daily_request_limit: 10000,
    monthly_request_limit: 300000,
    max_users: 100,
    max_sessions_per_user: 5,
  });
  const [originalSettings, setOriginalSettings] = useState<TenantSettings>({
    content_filter_enabled: true,
    audit_log_enabled: true,
    audit_log_retention_days: 90,
  });
  const [isSaving, setIsSaving] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [trialDaysError, setTrialDaysError] = useState<string | null>(null);
  // Issue #3204: Retention days validation error
  const [retentionDaysError, setRetentionDaysError] = useState<string | null>(null);

  // Issue #3132: Quota check state
  const { checkingTenants, checkResult, checkQuota, clearResult } = useTenantQuotaCheck();
  const [showCheckResultModal, setShowCheckResultModal] = useState(false);

  // Issue #3200: Billing period reset state
  const [resettingTenants, setResettingTenants] = useState<Set<number>>(new Set());

  // Dynamic options with useMemo for performance (Issue #1500)
  const statusOptions = useMemo(() => getTenantStatusOptions(language), [language]);
  const planOptions = useMemo(() => getTenantPlanOptions(language), [language]);
  const modalPlanOptions = useMemo(() => getTenantPlanOptions(language, false), [language]);

  // Fetch tenants - defined before pageRefresh for onRefresh callback reference
  const fetchTenants = React.useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const result = await tenantApi.listTenants({
        status: statusFilter || undefined,
        plan: planFilter || undefined,
      });
      setTenants(result.tenants);
    } catch (err: unknown) {
      // API client throws ApiError objects (plain objects with message property)
      // not Error instances. Check for message property first.
      let errorMessage: string;
      if (err && typeof err === 'object' && 'message' in err) {
        errorMessage = String((err as { message: string }).message);
      } else if (err instanceof Error) {
        errorMessage = (err as Error).message;
      } else {
        errorMessage = 'Failed to fetch tenants';
      }
      setError(errorMessage);
    } finally {
      setIsLoading(false);
    }
  }, [statusFilter, planFilter]);

  // Page refresh control - manual refresh for tenant management
  const pageRefresh = usePageRefresh({
    page: '/manage/tenants',
    refreshKey: createMatcherConfig([['admin', 'tenants']], 'prefix'),
    interval: 0, // No auto refresh - manual only
    enabled: false,
    onRefresh: fetchTenants,
  });

  // Form validation
  const isFormValid = formData.name.trim().length > 0;

  useEffect(() => {
    fetchTenants();
  }, [fetchTenants]);

  // Statistics - Issue #3197: Add total users, tokens, requests
  const stats = useMemo(() => {
    const total = tenants.length;
    const active = tenants.filter((t) => t.status === 'active').length;
    const suspended = tenants.filter((t) => t.status === 'suspended').length;
    const trial = tenants.filter((t) => t.status === 'trial').length;
    const totalUsers = tenants.reduce((sum, t) => sum + (t.user_count ?? 0), 0);
    const totalTokens = tenants.reduce((sum, t) => sum + (t.total_tokens_used ?? 0), 0);
    const totalRequests = tenants.reduce((sum, t) => sum + (t.total_requests_made ?? 0), 0);
    return { total, active, suspended, trial, totalUsers, totalTokens, totalRequests };
  }, [tenants]);

  // Handlers
  const handleOpenCreate = () => {
    setEditingTenant(null);
    setFormError(null);
    setTrialDaysError(null);
    setFormData({
      name: '',
      slug: '',
      plan: 'standard',
      contact_email: '',
      contact_name: '',
      trial_days: undefined,
    });
    setCreateAdminAccount(false);
    setAdminFormData({
      admin_username: '',
      admin_password: '',
      admin_email: '',
    });
    setShowModal(true);
  };

  const handleOpenEdit = (tenant: Tenant) => {
    setEditingTenant(tenant);
    setFormError(null);
    setTrialDaysError(null);
    setFormData({
      name: tenant.name,
      slug: tenant.slug,
      plan: tenant.plan,
      contact_email: tenant.contact_email ?? '',
      contact_name: tenant.contact_name ?? '',
    });
    setCreateAdminAccount(false);
    setAdminFormData({
      admin_username: '',
      admin_password: '',
      admin_email: '',
    });
    setShowModal(true);
  };

  const handleOpenQuota = (tenant: Tenant) => {
    setEditingTenant(tenant);
    // Issue #3203: Load quota data and save original values
    const quota = {
      daily_token_limit: Math.floor(
        (tenant.quota?.daily_token_limit ?? 1000000) / TOKEN_QUOTA_MULTIPLIER
      ),
      monthly_token_limit: Math.floor(
        (tenant.quota?.monthly_token_limit ?? 30000000) / TOKEN_QUOTA_MULTIPLIER
      ),
      daily_request_limit: tenant.quota?.daily_request_limit ?? 10000,
      monthly_request_limit: tenant.quota?.monthly_request_limit ?? 300000,
      max_users: tenant.quota?.max_users ?? 100,
      max_sessions_per_user: tenant.quota?.max_sessions_per_user ?? 5,
    };
    setQuotaData(quota);
    setOriginalQuota(quota);

    // Issue #3203: Load settings data with default value fallback
    // Issue #3204: Add audit_log_enabled and audit_log_retention_days
    const settings: TenantSettings = {
      content_filter_enabled: tenant.settings?.content_filter_enabled ?? true,
      audit_log_enabled: tenant.settings?.audit_log_enabled ?? true,
      audit_log_retention_days: tenant.settings?.audit_log_retention_days ?? 90,
    };
    setSettingsData(settings);
    setOriginalSettings(settings);

    // Issue #3204: Clear validation errors
    setRetentionDaysError(null);

    setShowQuotaModal(true);
  };

  const handleCloseModal = () => {
    setShowModal(false);
    setEditingTenant(null);
    setCreateAdminAccount(false);
    setAdminFormData({
      admin_username: '',
      admin_password: '',
      admin_email: '',
    });
  };

  const handleCloseQuotaModal = () => {
    setShowQuotaModal(false);
    setEditingTenant(null);
  };

  const handleSubmit = async () => {
    if (!formData.name.trim()) {
      setFormError(t('tenantNameRequired', language));
      return;
    }

    // Validate trial days (Issue #1501)
    if (!editingTenant && formData.trial_days !== undefined) {
      const error = validateTrialDays(formData.trial_days, language);
      if (error) {
        setTrialDaysError(error);
        return;
      }
    }

    // Validate admin account fields if creating admin
    if (!editingTenant && createAdminAccount) {
      if (!adminFormData.admin_username.trim()) {
        setFormError(t('adminUsernameRequired', language));
        return;
      }
      if (!adminFormData.admin_password.trim()) {
        setFormError(t('adminPasswordRequired', language));
        return;
      }
      if (adminFormData.admin_password.length < 8) {
        setFormError(t('passwordTooShort', language));
        return;
      }
    }

    try {
      if (editingTenant) {
        await tenantApi.updateTenant(editingTenant.id, formData as UpdateTenantRequest);
      } else {
        // Include admin account fields when creating tenant
        const createData: CreateTenantRequest = {
          ...formData,
          ...(createAdminAccount && {
            admin_username: adminFormData.admin_username,
            admin_password: adminFormData.admin_password,
            admin_email: adminFormData.admin_email || undefined,
          }),
        };
        await tenantApi.createTenant(createData);
      }
      handleCloseModal();
      fetchTenants();
    } catch (err: unknown) {
      // API client throws ApiError objects (plain objects with message property),
      // not Error instances. Check for message property first.
      let errorMessage: string;
      if (err && typeof err === 'object' && 'message' in err) {
        errorMessage = String((err as { message: string }).message);
      } else if (err instanceof Error) {
        errorMessage = (err as Error).message;
      } else {
        errorMessage = 'Failed to save tenant';
      }
      // 翻译后端返回的错误信息
      const translatedError = translateTenantError(errorMessage, language);
      setFormError(translatedError);
    }
  };

  // Issue #3203: Change detection functions
  const hasQuotaChanges = () => {
    return (
      quotaData.daily_token_limit !== originalQuota.daily_token_limit ||
      quotaData.monthly_token_limit !== originalQuota.monthly_token_limit ||
      quotaData.daily_request_limit !== originalQuota.daily_request_limit ||
      quotaData.monthly_request_limit !== originalQuota.monthly_request_limit ||
      quotaData.max_users !== originalQuota.max_users ||
      quotaData.max_sessions_per_user !== originalQuota.max_sessions_per_user
    );
  };

  // Issue #3204: Extended to include audit_log_enabled and audit_log_retention_days
  const hasSettingsChanges = () => {
    return (
      settingsData.content_filter_enabled !== originalSettings.content_filter_enabled ||
      settingsData.audit_log_enabled !== originalSettings.audit_log_enabled ||
      settingsData.audit_log_retention_days !== originalSettings.audit_log_retention_days
    );
  };

  // Issue #3203: Intelligent save with change detection
  const handleSaveQuota = async () => {
    if (!editingTenant) return;

    // Issue #3204: Validate retention days before saving
    const error = validateRetentionDays(settingsData.audit_log_retention_days, language);
    if (error) {
      setRetentionDaysError(error);
      return;
    }

    setIsSaving(true);

    try {
      const needSaveSettings = hasSettingsChanges();
      const needSaveQuota = hasQuotaChanges();

      // No changes, just close the modal
      if (!needSaveSettings && !needSaveQuota) {
        handleCloseQuotaModal();
        return;
      }

      // Save settings first if changed
      // Issue #3204: Include audit_log_enabled and audit_log_retention_days
      if (needSaveSettings) {
        await tenantApi.updateSettings(editingTenant.id, {
          content_filter_enabled: settingsData.content_filter_enabled,
          audit_log_enabled: settingsData.audit_log_enabled,
          audit_log_retention_days: settingsData.audit_log_retention_days,
        });
      }

      // Save quota if changed
      if (needSaveQuota) {
        await tenantApi.updateQuota(editingTenant.id, {
          daily_token_limit: quotaData.daily_token_limit * TOKEN_QUOTA_MULTIPLIER,
          monthly_token_limit: quotaData.monthly_token_limit * TOKEN_QUOTA_MULTIPLIER,
          daily_request_limit: quotaData.daily_request_limit,
          monthly_request_limit: quotaData.monthly_request_limit,
          max_users: quotaData.max_users,
          max_sessions_per_user: quotaData.max_sessions_per_user,
        });
      }

      toast.success(t('settingsSaved', language));
      handleCloseQuotaModal();
      fetchTenants();
    } catch (err: unknown) {
      // API client throws ApiError objects (plain objects with message property)
      let errorMessage: string;
      if (err && typeof err === 'object' && 'message' in err) {
        errorMessage = String((err as { message: string }).message);
      } else if (err instanceof Error) {
        errorMessage = (err as Error).message;
      } else {
        errorMessage = 'Failed to save settings';
      }
      toast.error(t('settingsSaveFailed', language) + ': ' + errorMessage);
    } finally {
      setIsSaving(false);
    }
  };

  const confirm = useConfirm();
  const handleSuspend = async (tenant: Tenant) => {
    if (!(await confirm({ message: t('confirmSuspendTenant', language), variant: 'warning' })))
      return;
    try {
      await tenantApi.suspendTenant(tenant.id);
      fetchTenants();
    } catch (err) {
      console.error('Failed to suspend tenant:', err);
    }
  };

  const handleActivate = async (tenant: Tenant) => {
    try {
      await tenantApi.activateTenant(tenant.id);
      fetchTenants();
    } catch (err) {
      console.error('Failed to activate tenant:', err);
    }
  };

  const handleDelete = async (tenant: Tenant) => {
    if (!(await confirm({ message: t('confirmDeleteTenant', language), variant: 'danger' })))
      return;
    try {
      await tenantApi.deleteTenant(tenant.id);
      fetchTenants();
    } catch (err) {
      console.error('Failed to delete tenant:', err);
    }
  };

  // Issue #3132: Handle quota check button click
  const handleCheckQuota = async (tenant: Tenant) => {
    await checkQuota(tenant);
    // Open result modal after check completes
    setShowCheckResultModal(true);
  };

  // Issue #3132: Handle closing the check result modal
  const handleCloseCheckResultModal = () => {
    setShowCheckResultModal(false);
    clearResult();
  };

  // Issue #3132: Handle "Edit Quota" button click from the check result modal
  const handleEditQuotaFromModal = (tenant: Tenant) => {
    handleCloseCheckResultModal();
    handleOpenQuota(tenant);
  };

  // Issue #3200: Handle billing period reset
  const handleResetPeriod = async (tenant: Tenant) => {
    const confirmMessage = t('confirmResetBillingPeriod', language).replace('{name}', tenant.name);
    if (!(await confirm({ message: confirmMessage, variant: 'warning' }))) {
      return;
    }

    // Prevent duplicate requests
    let shouldProceed = false;
    setResettingTenants((prev) => {
      if (prev.has(tenant.id)) {
        return prev;
      }
      shouldProceed = true;
      return new Set(prev).add(tenant.id);
    });

    if (!shouldProceed) {
      return;
    }

    try {
      const result = await tenantApi.resetBillingPeriod(tenant.id);
      if (result.success) {
        toast.success(t('billingPeriodResetSuccess', language));
        fetchTenants();
      } else {
        toast.error(t('billingPeriodResetFailed', language));
      }
    } catch (err: unknown) {
      let errorMessage: string;
      if (err && typeof err === 'object' && 'message' in err) {
        errorMessage = String((err as { message: string }).message);
      } else if (err instanceof Error) {
        errorMessage = (err as Error).message;
      } else {
        errorMessage = 'Failed to reset billing period';
      }
      toast.error(t('billingPeriodResetFailed', language) + ': ' + errorMessage);
    } finally {
      setResettingTenants((prev) => {
        const next = new Set(prev);
        next.delete(tenant.id);
        return next;
      });
    }
  };

  const getStatusVariant = (status: string) => {
    switch (status) {
      case 'active':
        return 'success';
      case 'suspended':
        return 'danger';
      case 'trial':
        return 'warning';
      default:
        return 'secondary';
    }
  };

  const getPlanVariant = (plan: string) => {
    switch (plan) {
      case 'free':
        return 'secondary';
      case 'enterprise':
        return 'primary';
      case 'premium':
        return 'info';
      default:
        return 'secondary';
    }
  };

  if (isLoading) {
    return <Loading size="lg" text={t('loading', language)} />;
  }

  if (error) {
    return <Error message={error} onRetry={fetchTenants} />;
  }

  return (
    <div className="tenant-management">
      {/* Header */}
      <div className="d-flex justify-content-between align-items-center mb-4">
        <h2>{t('tenantManagement', language)}</h2>
        <div className="d-flex gap-2">
          <PageRefreshControl
            refresh={pageRefresh}
            compact={true}
            showAutoRefreshToggle={false}
            showIntervalSelector={false}
            showLastRefreshTime={true}
          />
          <Button variant="primary" size="sm" onClick={handleOpenCreate}>
            <i className="bi bi-plus-lg me-1" />
            {t('addTenant', language)}
          </Button>
        </div>
      </div>

      {/* Statistics */}
      <div className="row g-3 mb-4">
        <div className="col-md-3">
          <StatCard
            label={t('totalTenants', language)}
            value={stats.total.toString()}
            icon={<i className="bi bi-building fs-4" />}
            variant="primary"
          />
        </div>
        <div className="col-md-3">
          <StatCard
            label={t('activeTenants', language)}
            value={stats.active.toString()}
            icon={<i className="bi bi-check-circle fs-4" />}
            variant="success"
          />
        </div>
        <div className="col-md-3">
          <StatCard
            label={t('suspendedTenants', language)}
            value={stats.suspended.toString()}
            icon={<i className="bi bi-pause-circle fs-4" />}
            variant="danger"
          />
        </div>
        <div className="col-md-3">
          <StatCard
            label={t('trialTenants', language)}
            value={stats.trial.toString()}
            icon={<i className="bi bi-clock fs-4" />}
            variant="warning"
          />
        </div>
      </div>

      {/* Issue #3197: Usage Summary Statistics */}
      <div className="row g-3 mb-4">
        <div className="col-md-4">
          <StatCard
            label={t('tenantTotalUsers', language)}
            value={stats.totalUsers.toString()}
            icon={<i className="bi bi-people fs-4" />}
            variant="info"
          />
        </div>
        <div className="col-md-4">
          <StatCard
            label={t('tenantTotalTokens', language)}
            value={formatQuotaForDisplay(stats.totalTokens / TOKEN_QUOTA_MULTIPLIER, true)}
            icon={<i className="bi bi-cpu fs-4" />}
            variant="primary"
          />
        </div>
        <div className="col-md-4">
          <StatCard
            label={t('tenantTotalRequests', language)}
            value={formatNumberAsString(stats.totalRequests)}
            icon={<i className="bi bi-lightning fs-4" />}
            variant="secondary"
          />
        </div>
      </div>

      {/* Filters */}
      <Card className="mb-4">
        <div className="row g-3">
          <div className="col-md-3">
            <label className="form-label">{t('status', language)}</label>
            <Select options={statusOptions} value={statusFilter} onChange={setStatusFilter} />
          </div>
          <div className="col-md-3">
            <label className="form-label">{t('plan', language)}</label>
            <Select options={planOptions} value={planFilter} onChange={setPlanFilter} />
          </div>
        </div>
      </Card>

      {/* Tenant List */}
      {tenants.length === 0 ? (
        <EmptyState icon="bi-building" title={t('noTenantsFound', language)} />
      ) : (
        <Card>
          <div className="table-responsive">
            <table className="table table-hover">
              <thead>
                <tr>
                  <th>{t('tenantName', language)}</th>
                  <th>{t('slug', language)}</th>
                  <th>{t('plan', language)}</th>
                  <th>{t('status', language)}</th>
                  <th>{t('tenantUserCount', language)}</th>
                  <th>{t('tenantUsageStats', language)}</th>
                  <th>{t('createdAt', language)}</th>
                  <th>{t('tableActions', language)}</th>
                </tr>
              </thead>
              <tbody>
                {tenants.map((tenant) => (
                  <tr key={tenant.id}>
                    <td>
                      <strong>{tenant.name}</strong>
                      {tenant.contact_email && (
                        <small className="d-block text-muted">{tenant.contact_email}</small>
                      )}
                    </td>
                    <td>
                      <code>{tenant.slug}</code>
                    </td>
                    <td>
                      <Badge variant={getPlanVariant(tenant.plan)}>
                        {getPlanLabel(tenant.plan, language)}
                      </Badge>
                    </td>
                    <td>
                      <Badge variant={getStatusVariant(tenant.status)}>
                        {getStatusLabel(tenant.status, language)}
                      </Badge>
                    </td>
                    {/* Issue #3197: User count column */}
                    <td>
                      <span className="text-nowrap">
                        {tenant.user_count ?? 0} / {tenant.quota?.max_users ?? 0}
                      </span>
                      {tenant.quota &&
                        tenant.user_count !== undefined &&
                        tenant.quota.max_users > 0 && (
                          <div className="progress mt-1" style={{ height: '4px' }}>
                            <div
                              className={cn(
                                'progress-bar',
                                (tenant.user_count / tenant.quota.max_users) * 100 >= 100
                                  ? 'bg-danger'
                                  : (tenant.user_count / tenant.quota.max_users) * 100 >= 80
                                    ? 'bg-warning'
                                    : 'bg-success'
                              )}
                              style={{
                                width: `${Math.min(100, (tenant.user_count / tenant.quota.max_users) * 100)}%`,
                              }}
                            />
                          </div>
                        )}
                    </td>
                    {/* Issue #3197: Usage statistics column - Token and Request cumulative usage */}
                    <td>
                      <div className="small">
                        <div className="mb-1">
                          <span className="text-muted">{t('tenantTokenUsage', language)}:</span>{' '}
                          <span title={t('tenantCumulativeUsageTooltip', language)}>
                            {formatQuotaForDisplay(
                              (tenant.total_tokens_used ?? 0) / TOKEN_QUOTA_MULTIPLIER,
                              true
                            )}
                          </span>
                        </div>
                        <div>
                          <span className="text-muted">{t('tenantRequestUsage', language)}:</span>{' '}
                          <span title={t('tenantCumulativeUsageTooltip', language)}>
                            {(tenant.total_requests_made ?? 0).toLocaleString()}
                          </span>
                        </div>
                      </div>
                    </td>
                    <td>
                      <small>{formatDateTime(tenant.created_at)}</small>
                    </td>
                    <td>
                      <div className="btn-group btn-group-sm">
                        {/* Issue #3197: View details button */}
                        <Button
                          variant="outline-info"
                          size="sm"
                          onClick={() => navigate(`/manage/tenants/${tenant.id}`)}
                          title={t('tenantViewDetails', language) ?? 'View Details'}
                        >
                          <i className="bi bi-eye" />
                        </Button>
                        <Button
                          variant="outline-primary"
                          size="sm"
                          onClick={() => handleOpenEdit(tenant)}
                          title={t('edit', language) ?? 'Edit'}
                        >
                          <i className="bi bi-pencil" />
                        </Button>
                        {/* Issue #3203: Permission check for quota/settings button */}
                        {canManageTenant(user, tenant.id) && (
                          <Button
                            variant="outline-secondary"
                            size="sm"
                            onClick={() => handleOpenQuota(tenant)}
                            title={t('tenantSettingsModal', language) ?? 'Tenant Settings'}
                          >
                            <i className="bi bi-sliders" />
                          </Button>
                        )}
                        <Button
                          variant="outline-info"
                          size="sm"
                          onClick={() => handleCheckQuota(tenant)}
                          disabled={checkingTenants.has(tenant.id)}
                          title={t('checkQuota', language) ?? 'Check Quota'}
                        >
                          {checkingTenants.has(tenant.id) ? (
                            <>
                              <span
                                className="spinner-border spinner-border-sm me-1"
                                role="status"
                                aria-hidden="true"
                              />
                              {t('checkingQuota', language)}
                            </>
                          ) : (
                            <i className="bi bi-speedometer2" />
                          )}
                        </Button>
                        {/* Issue #3200: Reset billing period button */}
                        <Button
                          variant="outline-secondary"
                          size="sm"
                          onClick={() => handleResetPeriod(tenant)}
                          disabled={resettingTenants.has(tenant.id)}
                          title={t('resetBillingPeriod', language) ?? 'Reset Billing Period'}
                        >
                          {resettingTenants.has(tenant.id) ? (
                            <span
                              className="spinner-border spinner-border-sm"
                              role="status"
                              aria-hidden="true"
                            />
                          ) : (
                            <i className="bi bi-arrow-repeat" />
                          )}
                        </Button>
                        {tenant.status === 'suspended' ? (
                          <Button
                            variant="outline-success"
                            size="sm"
                            onClick={() => handleActivate(tenant)}
                            title={t('activate', language) ?? 'Activate'}
                          >
                            <i className="bi bi-play" />
                          </Button>
                        ) : (
                          <Button
                            variant="outline-warning"
                            size="sm"
                            onClick={() => handleSuspend(tenant)}
                            title={t('suspend', language) ?? 'Suspend'}
                          >
                            <i className="bi bi-pause" />
                          </Button>
                        )}
                        <Button
                          variant="outline-danger"
                          size="sm"
                          onClick={() => handleDelete(tenant)}
                          title={t('delete', language) ?? 'Delete'}
                        >
                          <i className="bi bi-trash" />
                        </Button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      {/* Create/Edit Modal */}
      <Modal
        isOpen={showModal}
        onClose={handleCloseModal}
        title={editingTenant ? t('editTenant', language) : t('addTenant', language)}
        size="md"
        footer={
          <>
            <Button variant="secondary" onClick={handleCloseModal}>
              {t('cancel', language)}
            </Button>
            <Button variant="primary" onClick={handleSubmit} disabled={!isFormValid}>
              {t('save', language)}
            </Button>
          </>
        }
      >
        <form
          onSubmit={(e) => {
            e.preventDefault();
            handleSubmit();
          }}
        >
          {formError && (
            <div className="alert alert-danger mb-3" role="alert">
              {formError}
            </div>
          )}
          <div className="row g-3">
            <div className="col-12">
              <label className="form-label">{t('tenantName', language)} *</label>
              <TextInput
                value={formData.name}
                onChange={(value: string) => setFormData({ ...formData, name: value })}
                placeholder={t('enterTenantName', language)}
              />
            </div>
            <div className="col-md-6">
              <label className="form-label">{t('slug', language)}</label>
              <TextInput
                value={formData.slug ?? ''}
                onChange={(value: string) => setFormData({ ...formData, slug: value })}
                placeholder={t('enterSlug', language)}
              />
            </div>
            <div className="col-md-6">
              <label className="form-label">{t('plan', language)}</label>
              <Select
                options={modalPlanOptions}
                value={formData.plan ?? 'standard'}
                onChange={(value) => setFormData({ ...formData, plan: value as TenantPlan })}
              />
            </div>
            <div className="col-md-6">
              <label className="form-label">{t('contactEmail', language)}</label>
              <TextInput
                value={formData.contact_email ?? ''}
                onChange={(value: string) => setFormData({ ...formData, contact_email: value })}
                placeholder={t('enterEmail', language)}
              />
            </div>
            <div className="col-md-6">
              <label className="form-label">{t('contactName', language)}</label>
              <TextInput
                value={formData.contact_name ?? ''}
                onChange={(value: string) => setFormData({ ...formData, contact_name: value })}
                placeholder={t('enterContactName', language)}
              />
            </div>
            {/* Trial Days - Only for new tenants (Issue #1501) */}
            {!editingTenant && (
              <div className="col-md-6">
                <label className="form-label">{t('tenantTrialDays', language)}</label>
                <div className="input-group">
                  <input
                    type="number"
                    className={`form-control ${trialDaysError ? 'is-invalid' : ''}`}
                    min="1"
                    max="365"
                    placeholder="7"
                    value={formData.trial_days ?? ''}
                    onChange={(e) => {
                      const value = e.target.value ? parseInt(e.target.value) : undefined;
                      setFormData({ ...formData, trial_days: value });
                      // Validate on change
                      const error = validateTrialDays(value, language);
                      setTrialDaysError(error);
                    }}
                  />
                  <span className="input-group-text">{t('days', language)}</span>
                </div>
                <small className="text-muted">{t('tenantTrialDaysHelp', language)}</small>
                {trialDaysError && <div className="invalid-feedback d-block">{trialDaysError}</div>}
              </div>
            )}
            {/* Admin Account Creation - Only for new tenants */}
            {!editingTenant && (
              <>
                <div className="col-12">
                  <hr className="my-2" />
                  <div className="form-check">
                    <input
                      type="checkbox"
                      className="form-check-input"
                      id="createAdminAccount"
                      checked={createAdminAccount}
                      onChange={(e) => setCreateAdminAccount(e.target.checked)}
                    />
                    <label className="form-check-label" htmlFor="createAdminAccount">
                      {t('createAdminAccount', language) ?? 'Create Admin Account'}
                    </label>
                  </div>
                </div>
                {createAdminAccount && (
                  <>
                    <div className="col-md-6">
                      <label className="form-label">
                        {t('adminUsername', language) ?? 'Admin Username'} *
                      </label>
                      <TextInput
                        value={adminFormData.admin_username}
                        onChange={(value: string) =>
                          setAdminFormData({ ...adminFormData, admin_username: value })
                        }
                        placeholder={t('enterUsername', language)}
                      />
                    </div>
                    <div className="col-md-6">
                      <label className="form-label">
                        {t('adminPassword', language) ?? 'Admin Password'} *
                      </label>
                      <TextInput
                        type="password"
                        value={adminFormData.admin_password}
                        onChange={(value: string) =>
                          setAdminFormData({ ...adminFormData, admin_password: value })
                        }
                        placeholder={t('enterPassword', language)}
                      />
                      <small className="text-muted">
                        {t('passwordMinLength', language) ?? 'Minimum 8 characters'}
                      </small>
                    </div>
                    <div className="col-md-6">
                      <label className="form-label">
                        {t('adminEmail', language) ?? 'Admin Email'}
                      </label>
                      <TextInput
                        type="email"
                        value={adminFormData.admin_email}
                        onChange={(value: string) =>
                          setAdminFormData({ ...adminFormData, admin_email: value })
                        }
                        placeholder={t('enterEmail', language)}
                      />
                      <small className="text-muted">
                        {t('adminEmailOptional', language) ??
                          'Optional, will be auto-generated if empty'}
                      </small>
                    </div>
                  </>
                )}
              </>
            )}
          </div>
        </form>
      </Modal>

      {/* Quota & Settings Modal - Issue #3203 */}
      <Modal
        isOpen={showQuotaModal}
        onClose={handleCloseQuotaModal}
        title={t('tenantSettingsModal', language)}
        size="lg"
        footer={
          <>
            <Button variant="secondary" onClick={handleCloseQuotaModal} disabled={isSaving}>
              {t('cancel', language)}
            </Button>
            <Button
              variant="primary"
              onClick={handleSaveQuota}
              loading={isSaving}
              disabled={isSaving}
            >
              {t('save', language)}
            </Button>
          </>
        }
      >
        <form
          onSubmit={(e) => {
            e.preventDefault();
            handleSaveQuota();
          }}
        >
          {/* Quota Section */}
          <div className="row g-3">
            <div className="col-md-6">
              <label className="form-label">{t('dailyTokenLimit', language)} (M)</label>
              <input
                type="number"
                className="form-control"
                value={quotaData.daily_token_limit}
                disabled={isSaving}
                onChange={(e) =>
                  setQuotaData({
                    ...quotaData,
                    daily_token_limit: parseInt(e.target.value) || 0,
                  })
                }
              />
            </div>
            <div className="col-md-6">
              <label className="form-label">{t('monthlyTokenLimit', language)} (M)</label>
              <input
                type="number"
                className="form-control"
                value={quotaData.monthly_token_limit}
                disabled={isSaving}
                onChange={(e) =>
                  setQuotaData({
                    ...quotaData,
                    monthly_token_limit: parseInt(e.target.value) || 0,
                  })
                }
              />
            </div>
            <div className="col-md-6">
              <label className="form-label">{t('dailyRequestLimit', language)}</label>
              <input
                type="number"
                className="form-control"
                value={quotaData.daily_request_limit}
                disabled={isSaving}
                onChange={(e) =>
                  setQuotaData({
                    ...quotaData,
                    daily_request_limit: parseInt(e.target.value) || 0,
                  })
                }
              />
            </div>
            <div className="col-md-6">
              <label className="form-label">{t('monthlyRequestLimit', language)}</label>
              <input
                type="number"
                className="form-control"
                value={quotaData.monthly_request_limit}
                disabled={isSaving}
                onChange={(e) =>
                  setQuotaData({
                    ...quotaData,
                    monthly_request_limit: parseInt(e.target.value) || 0,
                  })
                }
              />
            </div>
            <div className="col-md-6">
              <label className="form-label">{t('maxUsers', language)}</label>
              <input
                type="number"
                className="form-control"
                value={quotaData.max_users}
                disabled={isSaving}
                onChange={(e) =>
                  setQuotaData({ ...quotaData, max_users: parseInt(e.target.value) || 1 })
                }
              />
            </div>
            <div className="col-md-6">
              <label className="form-label">{t('maxSessionsPerUser', language)}</label>
              <input
                type="number"
                className="form-control"
                value={quotaData.max_sessions_per_user}
                disabled={isSaving}
                onChange={(e) =>
                  setQuotaData({
                    ...quotaData,
                    max_sessions_per_user: parseInt(e.target.value) || 1,
                  })
                }
              />
            </div>
          </div>

          {/* Divider */}
          <hr className="my-4" />

          {/* Settings Section - Issue #3203 */}
          <h6 className="mb-3">{t('settingsSection', language)}</h6>
          <div className="row g-3">
            <div className="col-12">
              <div className="form-check form-switch">
                <input
                  className="form-check-input"
                  type="checkbox"
                  id="contentFilterEnabled"
                  checked={settingsData.content_filter_enabled}
                  disabled={isSaving}
                  onChange={(e) =>
                    setSettingsData({
                      ...settingsData,
                      content_filter_enabled: e.target.checked,
                    })
                  }
                />
                <label className="form-check-label" htmlFor="contentFilterEnabled">
                  {t('contentFilterEnabled', language)}
                </label>
              </div>
              <small className="text-muted d-block mt-1">
                {t('contentFilterEnabledDesc', language)}{' '}
                <a href="/security">{t('contentFilter', language)}</a>
              </small>
            </div>

            {/* Issue #3204: Audit Log Settings */}
            <div className="col-12">
              <div className="form-check form-switch">
                <input
                  className="form-check-input"
                  type="checkbox"
                  id="auditLogEnabled"
                  checked={settingsData.audit_log_enabled}
                  disabled={isSaving}
                  onChange={(e) =>
                    setSettingsData({
                      ...settingsData,
                      audit_log_enabled: e.target.checked,
                    })
                  }
                />
                <label className="form-check-label" htmlFor="auditLogEnabled">
                  {t('auditLogEnabled', language)}
                </label>
              </div>
              <small className="text-muted d-block mt-1">
                {t('auditLogEnabledDesc', language)}
              </small>
            </div>

            {/* Issue #3204: Audit Log Retention Days */}
            <div className="col-md-6">
              <label className="form-label">{t('auditLogRetentionDays', language)}</label>
              <input
                type="number"
                className={`form-control ${retentionDaysError ? 'is-invalid' : ''}`}
                min="1"
                max="365"
                value={settingsData.audit_log_retention_days ?? 90}
                disabled={isSaving}
                onChange={(e) => {
                  const value = parseInt(e.target.value, 10);
                  setSettingsData({
                    ...settingsData,
                    audit_log_retention_days: isNaN(value) ? 90 : value,
                  });
                  // Validate on change
                  const error = validateRetentionDays(e.target.value, language);
                  setRetentionDaysError(error);
                }}
              />
              {retentionDaysError && <div className="invalid-feedback">{retentionDaysError}</div>}
              <small className="text-muted d-block mt-1">
                {t('auditLogRetentionDaysHelp', language)}
              </small>
            </div>
          </div>
        </form>
      </Modal>

      {/* Issue #3132: Quota Check Result Modal */}
      <QuotaCheckResultModal
        isOpen={showCheckResultModal}
        result={checkResult}
        onClose={handleCloseCheckResultModal}
        onEditQuota={handleEditQuotaFromModal}
        language={language}
      />
    </div>
  );
};
