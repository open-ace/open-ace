/**
 * SSOSettings Component - SSO settings page
 *
 * Features:
 * - List SSO providers
 * - Register new providers
 * - View provider details
 * - Edit provider configuration
 * - Enable/Disable providers
 * - Quick register predefined providers
 * - Test provider connection
 * - Export provider configurations
 */

import React, { useState, useEffect, useRef, useCallback } from 'react';
import { cn } from '@/utils';
import { useLanguage } from '@/store';
import { useAuth } from '@/hooks';
import { t, type Language } from '@/i18n';
import { canManageAllTenants } from '@/utils/permissions';
import { getErrorMessage, isConflictError } from '@/utils/error';
import {
  FALLBACK_PREDEFINED_PROVIDERS,
  getProviderIcon,
  validatePredefinedProviders,
  sortProvidersByName,
} from '@/constants/ssoFallback';
import {
  Card,
  Button,
  Select,
  Loading,
  Error as ErrorDisplay,
  EmptyState,
  Modal,
  TextInput,
  Badge,
  useToast,
} from '@/components/common';
import {
  ssoApi,
  tenantApi,
  type SSOProvider,
  type SSOProviderDetail,
  type PredefinedProvider,
  type RegisterProviderRequest,
  type UpdateProviderRequest,
  type TestConnectionResult,
  type Tenant,
} from '@/api';

type ModalMode = 'detail' | 'edit' | 'register' | 'quick-register';

interface ModalState {
  mode: ModalMode;
  loading: boolean;
  modeLoading: boolean;
  data: SSOProviderDetail | null;
  error: string | null;
  quickRegisterStep: number;
  quickRegisterProvider: string | null;
  domainUrl: string;
  domainPreview: {
    authorization_url: string;
    token_url: string;
    userinfo_url: string;
  } | null;
}

export const SSOSettings: React.FC = () => {
  const language = useLanguage();
  const { user } = useAuth();
  const { success, error: toastError } = useToast();

  // Admin tenant selection
  const isAdmin = canManageAllTenants(user);
  const [tenants, setTenants] = useState<Tenant[]>([]);
  const [selectedTenantId, setSelectedTenantId] = useState<number | null>(null);

  // Ref-State synchronization pattern:
  // selectedTenantIdRef is used to avoid stale state in async callbacks and
  // to prevent infinite effect loops when setting the default tenant after
  // the tenant list loads. This is a common React pattern for handling
  // asynchronous initialization that depends on the latest state value.
  const selectedTenantIdRef = useRef<number | null>(null);
  const [isLoadingTenants, setIsLoadingTenants] = useState(false);

  // Compute effective tenant ID
  const effectiveTenantId = isAdmin ? selectedTenantId : user?.tenant_id;

  const [registeredProviders, setRegisteredProviders] = useState<SSOProvider[]>([]);
  // Initialize with fallback to avoid blank UI during initial load
  const [predefinedProviders, setPredefinedProviders] = useState<PredefinedProvider[]>(
    FALLBACK_PREDEFINED_PROVIDERS
  );
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // SSO settings state
  const [ssoEnabled, setSsoEnabled] = useState(false);
  const [autoProvision, setAutoProvision] = useState(false);
  const [isSaving, setIsSaving] = useState(false);

  // Modal state
  const [showModal, setShowModal] = useState(false);
  const [modalState, setModalState] = useState<ModalState>({
    mode: 'register',
    loading: false,
    modeLoading: false,
    data: null,
    error: null,
    quickRegisterStep: 1,
    quickRegisterProvider: null,
    domainUrl: '',
    domainPreview: null,
  });
  const [isSubmitting, setIsSubmitting] = useState(false);

  // Test connection state
  const [testingProvider, setTestingProvider] = useState<string | null>(null);
  const [testResults, setTestResults] = useState<TestConnectionResult[] | null>(null);

  // Form validation state
  const [validationErrors, setValidationErrors] = useState<Record<string, string>>({});
  const [clientSecretConfirm, setClientSecretConfirm] = useState('');

  // Success highlight state
  const [lastSuccessProvider, setLastSuccessProvider] = useState<string | null>(null);

  // Form data for register/edit
  const [formData, setFormData] = useState<RegisterProviderRequest | UpdateProviderRequest>({
    name: '',
    provider_type: 'oauth2',
    client_id: '',
    client_secret: '',
    redirect_uri: '',
    scope: [],
    predefined: false,
    authorization_url: '',
    token_url: '',
    userinfo_url: '',
    issuer_url: '',
  });

  // Quick register form data
  const [quickRegisterData, setQuickRegisterData] = useState({
    client_id: '',
    client_secret: '',
    redirect_uri: '',
  });

  // Fetch tenants list for admin users
  useEffect(() => {
    if (!isAdmin) return;

    setIsLoadingTenants(true);
    tenantApi
      .listTenants({ status: 'active', limit: 100 })
      .then((result) => {
        setTenants(result.tenants);
        if (result.tenants.length > 0 && !selectedTenantIdRef.current) {
          selectedTenantIdRef.current = result.tenants[0].id;
          setSelectedTenantId(result.tenants[0].id);
        }
      })
      .catch((err) => {
        console.error('Failed to fetch tenants:', err);
        toastError(t('failedToLoadTenants', language));
      })
      .finally(() => {
        setIsLoadingTenants(false);
      });
  }, [isAdmin, language, toastError]);

  // Sync ref with state
  useEffect(() => {
    selectedTenantIdRef.current = selectedTenantId;
  }, [selectedTenantId]);

  // Fetch providers and tenant settings
  const fetchProviders = useCallback(async () => {
    if (!effectiveTenantId) {
      setIsLoading(false);
      return;
    }

    setIsLoading(true);
    setError(null);
    try {
      const result = await ssoApi.getProviders(effectiveTenantId);
      setRegisteredProviders(result.registered);
      // Validate and sort predefined providers from API
      const validatedProviders = validatePredefinedProviders(result.predefined as unknown[]);
      const sortedProviders = sortProvidersByName(validatedProviders);
      setPredefinedProviders(sortedProviders);
    } catch (err) {
      const errorMessage = getErrorMessage(err, 'Failed to fetch providers');
      setError(errorMessage);
      // Keep fallback providers on error
    } finally {
      setIsLoading(false);
    }
  }, [effectiveTenantId]);

  // Fetch tenant settings
  useEffect(() => {
    if (!effectiveTenantId) return;

    tenantApi
      .getTenant(effectiveTenantId)
      .then((tenant) => {
        const settings = tenant.settings as Record<string, unknown>;
        setSsoEnabled(Boolean(settings?.sso_enabled ?? false));
        setAutoProvision(Boolean(settings?.auto_provision_users ?? false));
      })
      .catch((err) => {
        console.error('Failed to fetch tenant settings:', err);
      });
  }, [effectiveTenantId]);

  useEffect(() => {
    fetchProviders();
  }, [fetchProviders]);

  // Handlers
  const handleSaveSettings = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!effectiveTenantId) {
      toastError(t('tenantIdRequired', language));
      return;
    }

    setIsSaving(true);
    try {
      await tenantApi.updateSettings(effectiveTenantId, {
        sso_enabled: ssoEnabled,
        auto_provision_users: autoProvision,
      });
      success(t('settingsSaved', language));
    } catch (err) {
      console.error('Failed to save SSO settings:', err);
      toastError(t('saveFailed', language));
    } finally {
      setIsSaving(false);
    }
  };

  // Open modal handlers
  const openDetailModal = async (providerName: string) => {
    setModalState({
      mode: 'detail',
      loading: true,
      modeLoading: false,
      data: null,
      error: null,
      quickRegisterStep: 1,
      quickRegisterProvider: null,
      domainUrl: '',
      domainPreview: null,
    });
    setShowModal(true);

    try {
      const detail = await ssoApi.getProviderDetail(providerName);
      setModalState((prev) => ({
        ...prev,
        loading: false,
        data: detail,
      }));
    } catch (err) {
      setModalState((prev) => ({
        ...prev,
        loading: false,
        error: getErrorMessage(err, 'Failed to load provider details'),
      }));
    }
  };

  const switchToEditMode = async () => {
    if (!modalState.data) return;

    setModalState((prev) => ({ ...prev, modeLoading: true }));

    try {
      // Re-fetch latest data before editing
      const detail = await ssoApi.getProviderDetail(modalState.data.name);
      setFormData({
        client_id: detail.client_id,
        redirect_uri: detail.redirect_uri,
        scope: detail.scope,
        authorization_url: detail.authorization_url,
        token_url: detail.token_url,
        userinfo_url: detail.userinfo_url,
        issuer_url: detail.issuer_url,
        updated_at: detail.updated_at,
      });
      setModalState((prev) => ({
        ...prev,
        mode: 'edit',
        modeLoading: false,
        data: detail,
      }));
    } catch (err) {
      setModalState((prev) => ({
        ...prev,
        modeLoading: false,
        error: getErrorMessage(err, 'Failed to load latest data'),
      }));
    }
  };

  const openRegisterModal = () => {
    setFormData({
      name: '',
      provider_type: 'oauth2',
      client_id: '',
      client_secret: '',
      redirect_uri: '',
      scope: [],
      predefined: false,
      authorization_url: '',
      token_url: '',
      userinfo_url: '',
      issuer_url: '',
    });
    setModalState({
      mode: 'register',
      loading: false,
      modeLoading: false,
      data: null,
      error: null,
      quickRegisterStep: 1,
      quickRegisterProvider: null,
      domainUrl: '',
      domainPreview: null,
    });
    setShowModal(true);
  };

  const openQuickRegisterModal = (providerName: string) => {
    setQuickRegisterData({
      client_id: '',
      client_secret: '',
      redirect_uri: '',
    });
    setModalState({
      mode: 'quick-register',
      loading: false,
      modeLoading: false,
      data: null,
      error: null,
      quickRegisterStep: providerName === 'okta' || providerName === 'auth0' ? 2 : 3,
      quickRegisterProvider: providerName,
      domainUrl: '',
      domainPreview: null,
    });
    setShowModal(true);
  };

  const handleCloseModal = () => {
    setShowModal(false);
    setTestResults(null);
  };

  // URL validation helper
  const validateUrlFormat = (url: string): boolean => {
    if (!url || url.trim() === '') return true;
    try {
      const parsed = new URL(url);
      return parsed.protocol === 'http:' || parsed.protocol === 'https:';
    } catch {
      return false;
    }
  };

  // Handle URL field validation
  const handleValidateUrl = (field: string, value: string) => {
    if (value.trim() !== '' && !validateUrlFormat(value)) {
      setValidationErrors((prev) => ({
        ...prev,
        [field]: t('urlFormatError', language),
      }));
    } else {
      setValidationErrors((prev) => {
        const newErrors = { ...prev };
        delete newErrors[field];
        return newErrors;
      });
    }
  };

  // Form submission handlers
  const handleRegisterSubmit = async () => {
    setModalState((prev) => ({ ...prev, error: null }));
    setIsSubmitting(true);

    try {
      // Validate required fields
      const registerData = formData as RegisterProviderRequest;
      if (!registerData.client_id?.trim()) {
        setModalState((prev) => ({ ...prev, error: t('clientIdRequired', language) }));
        return;
      }
      if (!registerData.client_secret?.trim()) {
        setModalState((prev) => ({ ...prev, error: t('clientSecretRequired', language) }));
        return;
      }
      if (!registerData.predefined && !registerData.name?.trim()) {
        setModalState((prev) => ({ ...prev, error: t('providerNameRequired', language) }));
        return;
      }

      // Validate client secret confirmation
      if (clientSecretConfirm !== registerData.client_secret) {
        setModalState((prev) => ({ ...prev, error: t('secretMismatch', language) }));
        return;
      }

      // Validate URL format
      const urlFields = ['authorization_url', 'token_url', 'userinfo_url', 'redirect_uri'] as const;
      for (const field of urlFields) {
        const value = registerData[field] ?? '';
        if (value.trim() !== '' && !validateUrlFormat(value)) {
          setModalState((prev) => ({ ...prev, error: t('urlFormatError', language) }));
          return;
        }
      }

      await ssoApi.registerProvider({
        ...registerData,
        tenant_id: effectiveTenantId ?? undefined,
      });

      // Clear secret confirmation on success
      setClientSecretConfirm('');
      setValidationErrors({});
      handleCloseModal();
      fetchProviders();
      success(t('providerRegistered', language));
    } catch (err) {
      const errorMsg = getErrorMessage(err, t('registerFailed', language));
      setModalState((prev) => ({ ...prev, error: errorMsg }));
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleEditSubmit = async () => {
    if (!modalState.data) return;

    setModalState((prev) => ({ ...prev, error: null }));
    setIsSubmitting(true);

    try {
      const result = await ssoApi.updateProvider(modalState.data.name, {
        ...formData,
        updated_at: modalState.data.updated_at,
      } as UpdateProviderRequest);

      // Success highlight
      setLastSuccessProvider(modalState.data.name);
      setTimeout(() => setLastSuccessProvider(null), 3000);

      if (result.auto_enabled) {
        success(t('providerAutoEnabled', language));
      }
      success(t('settingsSaved', language));
      handleCloseModal();
      fetchProviders();
    } catch (err: unknown) {
      if (isConflictError(err)) {
        setModalState((prev) => ({
          ...prev,
          error: t('updatedConflict', language),
        }));
      } else {
        setModalState((prev) => ({
          ...prev,
          error: getErrorMessage(err, t('saveFailed', language)),
        }));
      }
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleQuickRegisterSubmit = async () => {
    if (!modalState.quickRegisterProvider) return;

    setModalState((prev) => ({ ...prev, error: null }));
    setIsSubmitting(true);

    try {
      const providerName = modalState.quickRegisterProvider;
      const isOktaOrAuth0 = providerName === 'okta' || providerName === 'auth0';

      const requestData: RegisterProviderRequest = {
        name: providerName,
        client_id: quickRegisterData.client_id,
        client_secret: quickRegisterData.client_secret,
        redirect_uri: quickRegisterData.redirect_uri,
        predefined: true,
        tenant_id: effectiveTenantId ?? undefined,
      };

      // Add URL overrides for Okta/Auth0
      if (isOktaOrAuth0 && modalState.domainPreview) {
        requestData.authorization_url = modalState.domainPreview.authorization_url;
        requestData.token_url = modalState.domainPreview.token_url;
        requestData.userinfo_url = modalState.domainPreview.userinfo_url;
      }

      await ssoApi.registerProvider(requestData);
      handleCloseModal();
      fetchProviders();
      success(t('providerRegistered', language));
    } catch (err) {
      const errorMsg = getErrorMessage(err, t('registerFailed', language));
      setModalState((prev) => ({ ...prev, error: errorMsg }));
    } finally {
      setIsSubmitting(false);
    }
  };

  // Toggle handlers
  const handleToggleProvider = async (providerName: string, currentEnabled: boolean) => {
    try {
      if (currentEnabled) {
        await ssoApi.disableProvider(providerName);
        success(t('providerDisabled', language));
      } else {
        await ssoApi.enableProvider(providerName);
        success(t('providerEnabled', language));
      }
      // Success highlight
      setLastSuccessProvider(providerName);
      setTimeout(() => setLastSuccessProvider(null), 3000);
      fetchProviders();
    } catch (err) {
      console.error('Failed to toggle provider:', err);
      toastError(t('operationFailed', language));
    }
  };

  // Test connection handler
  const handleTestConnection = async (providerName: string) => {
    setTestingProvider(providerName);
    setTestResults(null);

    try {
      const result = await ssoApi.testProviderConnection(providerName);
      setTestResults(result.results);
      if (result.success) {
        success(t('testConnectionSuccess', language));
      }
    } catch (err: unknown) {
      toastError(getErrorMessage(err, t('testConnectionFailed', language)));
    } finally {
      setTestingProvider(null);
    }
  };

  // Export handler
  const handleExportProviders = async () => {
    try {
      const result = await ssoApi.exportProviders(effectiveTenantId ?? undefined);
      const blob = new Blob([JSON.stringify(result, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `sso-providers-${new Date().toISOString().split('T')[0]}.json`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
      success(t('exportSuccess', language));
    } catch (err) {
      console.error('Failed to export providers:', err);
      toastError(t('exportFailed', language));
    }
  };

  // Domain URL validation and preview generation
  const handleDomainUrlChange = (url: string) => {
    setModalState((prev) => ({ ...prev, domainUrl: url }));

    if (!modalState.quickRegisterProvider) return;

    const provider = modalState.quickRegisterProvider;
    let preview: { authorization_url: string; token_url: string; userinfo_url: string } | null =
      null;

    // Validate Okta domain
    if (provider === 'okta') {
      const oktaMatch = url.match(/^https:\/\/([a-zA-Z0-9-]+)\.okta\.com\/?$/);
      if (oktaMatch) {
        const domain = oktaMatch[1];
        preview = {
          authorization_url: `https://${domain}.okta.com/oauth2/v1/authorize`,
          token_url: `https://${domain}.okta.com/oauth2/v1/token`,
          userinfo_url: `https://${domain}.okta.com/oauth2/v1/userinfo`,
        };
      }
    }

    // Validate Auth0 domain
    if (provider === 'auth0') {
      const auth0Match = url.match(/^https:\/\/([a-zA-Z0-9-]+)(?:\.eu)?\.auth0\.com\/?$/);
      if (auth0Match) {
        preview = {
          authorization_url: `${url.replace(/\/$/, '')}/authorize`,
          token_url: `${url.replace(/\/$/, '')}/oauth/token`,
          userinfo_url: `${url.replace(/\/$/, '')}/userinfo`,
        };
      }
    }

    setModalState((prev) => ({
      ...prev,
      domainPreview: preview,
    }));
  };

  // Helper to get provider icon with API data fallback
  const getProviderIconWithFallback = (provider: PredefinedProvider): string => {
    // Priority: API icon > fallback mapping > type-based default
    if (provider.icon) {
      return provider.icon;
    }
    return getProviderIcon(provider.name, provider.type);
  };

  // Generate select options from predefined providers (with Custom Provider option)
  const providerSelectOptions = [
    { value: '', label: t('customProvider', language) ?? 'Custom Provider' },
    ...sortProvidersByName(predefinedProviders).map((p) => ({
      value: p.name,
      label: p.display_name,
    })),
  ];

  // Loading state for tenant list (admin only)
  if (isAdmin && isLoadingTenants) {
    return <Loading size="lg" text={t('loading', language)} />;
  }

  // No effective tenant
  if (!effectiveTenantId) {
    return (
      <div className="sso-settings">
        <h2>{t('ssoSettings', language)}</h2>
        {isAdmin && tenants.length === 0 && (
          <EmptyState
            icon="bi-building"
            title={t('noTenantsAvailable', language)}
            description={t('ssoRequiresTenant', language)}
          />
        )}
        {!isAdmin && (
          <EmptyState
            icon="bi-building"
            title={t('noTenantConfigured', language)}
            description={t('ssoRequiresTenant', language)}
          />
        )}
      </div>
    );
  }

  if (isLoading) {
    return <Loading size="lg" text={t('loading', language)} />;
  }

  if (error) {
    return <ErrorDisplay message={error} onRetry={fetchProviders} />;
  }

  return (
    <div className="sso-settings">
      {/* Header */}
      <div className="d-flex justify-content-between align-items-center mb-4">
        <h2>{t('ssoSettings', language)}</h2>
        <div className="d-flex gap-2">
          <Button variant="outline-secondary" size="sm" onClick={fetchProviders}>
            <i className="bi bi-arrow-clockwise me-1" />
            {t('refresh', language)}
          </Button>
          <Button variant="outline-secondary" size="sm" onClick={handleExportProviders}>
            <i className="bi bi-download me-1" />
            {t('exportProviders', language)}
          </Button>
          <Button variant="primary" size="sm" onClick={openRegisterModal}>
            <i className="bi bi-plus-lg me-1" />
            {t('addProvider', language)}
          </Button>
        </div>
      </div>

      {/* Tenant Selector (Admin only) */}
      {isAdmin && tenants.length > 0 && (
        <Card className="mb-3">
          <div className="d-flex align-items-center gap-3">
            <label className="form-label mb-0 fw-semibold">
              <i className="bi bi-building me-2" />
              {t('selectTenant', language)}
            </label>
            <Select
              options={tenants.map((t) => ({
                value: String(t.id),
                label: t.name,
              }))}
              value={String(selectedTenantId ?? '')}
              onChange={(value) => setSelectedTenantId(Number(value))}
              className="flex-grow-1"
            />
            <small className="text-muted ms-2">{t('tenantSelectionHint', language)}</small>
          </div>
        </Card>
      )}

      {/* SSO Configuration Form */}
      <Card title={t('ssoConfiguration', language)} className="mb-4">
        <form className="sso-form" onSubmit={handleSaveSettings}>
          <div className="row g-3">
            <div className="col-md-6">
              <div className="form-check form-switch">
                <input
                  className="form-check-input"
                  type="checkbox"
                  id="ssoEnabled"
                  aria-describedby="ssoEnabledDesc"
                  checked={ssoEnabled}
                  onChange={(e) => setSsoEnabled(e.target.checked)}
                />
                <label className="form-check-label" htmlFor="ssoEnabled">
                  {t('enableSSO', language)}
                </label>
                <span id="ssoEnabledDesc" className="visually-hidden">
                  {t('ssoEnabledDesc', language)}
                </span>
              </div>
            </div>
            <div className="col-md-6">
              <div className="form-check form-switch">
                <input
                  className="form-check-input"
                  type="checkbox"
                  id="autoProvision"
                  aria-describedby="autoProvisionDesc"
                  checked={autoProvision}
                  onChange={(e) => setAutoProvision(e.target.checked)}
                />
                <label className="form-check-label" htmlFor="autoProvision">
                  {t('autoProvisionUsers', language)}
                </label>
                <span id="autoProvisionDesc" className="visually-hidden">
                  {t('autoProvisionDesc', language)}
                </span>
              </div>
            </div>
          </div>
          <div className="mt-3">
            <Button
              variant="primary"
              type="submit"
              loading={isSaving}
              disabled={!effectiveTenantId}
            >
              <i className="bi bi-check-lg me-1" />
              {t('save', language)}
            </Button>
          </div>
        </form>
      </Card>

      {/* Registered Providers */}
      <Card title={t('registeredProviders', language)} className="mb-4">
        {registeredProviders.length === 0 ? (
          <EmptyState
            icon="bi-key"
            title={t('noProvidersRegistered', language)}
            description={t('noProvidersDescription', language)}
            action={
              <Button variant="primary" size="sm" onClick={openRegisterModal}>
                <i className="bi bi-plus-lg me-1" />
                {t('addProvider', language)}
              </Button>
            }
          />
        ) : (
          <div className="table-responsive">
            <table className="table table-hover">
              <thead>
                <tr>
                  <th>{t('providerName', language)}</th>
                  <th>{t('type', language)}</th>
                  <th>{t('status', language)}</th>
                  <th>{t('tableActions', language)}</th>
                </tr>
              </thead>
              <tbody>
                {registeredProviders.map((provider) => (
                  <tr
                    key={provider.name}
                    className={lastSuccessProvider === provider.name ? 'table-success' : ''}
                    style={{
                      transition: 'background-color 0.3s ease',
                      ...(lastSuccessProvider === provider.name && {
                        animation: 'successPulse 2s ease-out',
                      }),
                    }}
                  >
                    <td>
                      <strong
                        className="text-capitalize text-decoration-underline"
                        style={{ cursor: 'pointer' }}
                        onClick={() => openDetailModal(provider.name)}
                        title={t('clickToViewDetails', language)}
                      >
                        {provider.name}
                      </strong>
                      <i
                        className="bi bi-info-circle text-muted ms-1"
                        style={{ fontSize: '0.75rem' }}
                      />
                    </td>
                    <td>
                      <Badge variant="secondary">{provider.type.toUpperCase()}</Badge>
                    </td>
                    <td>
                      <div className="form-check form-switch">
                        <input
                          className="form-check-input"
                          type="checkbox"
                          id={`toggle-${provider.name}`}
                          checked={provider.is_enabled}
                          onChange={() => handleToggleProvider(provider.name, provider.is_enabled)}
                        />
                        <label className="form-check-label" htmlFor={`toggle-${provider.name}`}>
                          <Badge variant={provider.is_enabled ? 'success' : 'danger'}>
                            {provider.is_enabled ? t('enabled', language) : t('disabled', language)}
                          </Badge>
                        </label>
                      </div>
                    </td>
                    <td>
                      <div className="d-flex gap-1">
                        <Button
                          variant="outline-primary"
                          size="sm"
                          onClick={() => openDetailModal(provider.name)}
                          title={t('ssoProviderDetail', language)}
                        >
                          <i className="bi bi-eye me-1" />
                          {t('view', language)}
                        </Button>
                        <Button
                          variant="outline-secondary"
                          size="sm"
                          onClick={() => handleTestConnection(provider.name)}
                          loading={testingProvider === provider.name}
                          title={t('testConnection', language)}
                        >
                          <i className="bi bi-plug me-1" />
                          {t('test', language)}
                        </Button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {/* Available Providers */}
      <Card title={t('availableProviders', language)}>
        <div className="row g-3">
          {predefinedProviders.map((provider) => (
            <div key={provider.name} className="col-md-4">
              <div className="border rounded p-3 h-100">
                <div className="d-flex align-items-center mb-2">
                  <i className={cn('bi me-2 fs-4', getProviderIconWithFallback(provider))} />
                  <div>
                    <strong>{provider.display_name}</strong>
                    <small className="d-block text-muted">{provider.type.toUpperCase()}</small>
                  </div>
                </div>
                <Button
                  variant="outline-primary"
                  size="sm"
                  className="w-100 mt-2"
                  onClick={() => openQuickRegisterModal(provider.name)}
                >
                  <i className="bi bi-lightning me-1" />
                  {t('quickRegister', language)}
                </Button>
              </div>
            </div>
          ))}
        </div>
      </Card>

      {/* Provider Modal */}
      <Modal
        isOpen={showModal}
        onClose={handleCloseModal}
        title={
          modalState.mode === 'detail'
            ? t('ssoProviderDetail', language)
            : modalState.mode === 'edit'
              ? t('editProvider', language)
              : modalState.mode === 'quick-register'
                ? t('quickRegister', language)
                : t('registerProvider', language)
        }
        size="lg"
        footer={
          modalState.mode === 'detail' ? (
            <>
              <Button variant="secondary" onClick={handleCloseModal}>
                {t('close', language)}
              </Button>
              <Button
                variant="primary"
                onClick={switchToEditMode}
                disabled={modalState.modeLoading}
              >
                {modalState.modeLoading ? (
                  <span className="spinner-border spinner-border-sm me-1" />
                ) : (
                  <i className="bi bi-pencil me-1" />
                )}
                {t('editProvider', language)}
              </Button>
            </>
          ) : modalState.mode === 'edit' ? (
            <>
              <Button
                variant="secondary"
                onClick={() => setModalState((prev) => ({ ...prev, mode: 'detail' }))}
              >
                {t('cancel', language)}
              </Button>
              <Button variant="primary" onClick={handleEditSubmit} loading={isSubmitting}>
                {t('save', language)}
              </Button>
            </>
          ) : modalState.mode === 'quick-register' ? (
            <>
              <Button variant="secondary" onClick={handleCloseModal}>
                {t('cancel', language)}
              </Button>
              <Button
                variant="primary"
                onClick={handleQuickRegisterSubmit}
                loading={isSubmitting}
                disabled={
                  (modalState.quickRegisterProvider === 'okta' ||
                    modalState.quickRegisterProvider === 'auth0') &&
                  !modalState.domainPreview
                }
              >
                {t('register', language)}
              </Button>
            </>
          ) : (
            <>
              <Button variant="secondary" onClick={handleCloseModal}>
                {t('cancel', language)}
              </Button>
              <Button variant="primary" onClick={handleRegisterSubmit} loading={isSubmitting}>
                {t('register', language)}
              </Button>
            </>
          )
        }
      >
        {modalState.loading ? (
          <div className="text-center py-4">
            <span className="spinner-border spinner-border-lg" />
          </div>
        ) : modalState.error ? (
          <div className="alert alert-danger">
            <i className="bi bi-exclamation-circle me-2" />
            {modalState.error}
          </div>
        ) : (
          <>
            {/* Detail Mode */}
            {modalState.mode === 'detail' && modalState.data && (
              <ProviderDetailView
                data={modalState.data}
                language={language}
                onTestConnection={() => handleTestConnection(modalState.data!.name)}
                testing={testingProvider === modalState.data.name}
                testResults={testResults}
              />
            )}

            {/* Edit Mode */}
            {modalState.mode === 'edit' && modalState.data && (
              <ProviderEditForm
                data={modalState.data}
                formData={formData as UpdateProviderRequest}
                onChange={setFormData}
                language={language}
              />
            )}

            {/* Register Mode */}
            {modalState.mode === 'register' && (
              <ProviderRegisterForm
                formData={formData as RegisterProviderRequest}
                onChange={setFormData}
                language={language}
                validationErrors={validationErrors}
                onValidateUrl={handleValidateUrl}
                providerOptions={providerSelectOptions}
                predefinedProviders={predefinedProviders}
              />
            )}

            {/* Quick Register Mode */}
            {modalState.mode === 'quick-register' && (
              <QuickRegisterForm
                step={modalState.quickRegisterStep}
                provider={modalState.quickRegisterProvider!}
                domainUrl={modalState.domainUrl}
                domainPreview={modalState.domainPreview}
                quickRegisterData={quickRegisterData}
                onDomainChange={handleDomainUrlChange}
                onDataChange={setQuickRegisterData}
                language={language}
              />
            )}
          </>
        )}
      </Modal>
    </div>
  );
};

// Sub-components
interface ProviderDetailViewProps {
  data: SSOProviderDetail;
  language: Language;
  onTestConnection: () => void;
  testing: boolean;
  testResults: TestConnectionResult[] | null;
}

const ProviderDetailView: React.FC<ProviderDetailViewProps> = ({
  data,
  language,
  onTestConnection,
  testing,
  testResults,
}) => {
  return (
    <div>
      <div className="row g-3">
        <div className="col-md-6">
          <label className="form-label text-muted">{t('providerName', language)}</label>
          <p className="fw-semibold text-capitalize">{data.name}</p>
        </div>
        <div className="col-md-6">
          <label className="form-label text-muted">{t('type', language)}</label>
          <p>
            <Badge variant="secondary">{data.type.toUpperCase()}</Badge>
          </p>
        </div>
        <div className="col-md-6">
          <label className="form-label text-muted">{t('status', language)}</label>
          <p>
            <Badge variant={data.is_enabled ? 'success' : 'danger'}>
              {data.is_enabled ? t('enabled', language) : t('disabled', language)}
            </Badge>
          </p>
        </div>
        <div className="col-md-6">
          <label className="form-label text-muted">{t('clientId', language)}</label>
          <p className="text-break">{data.client_id}</p>
        </div>
        <div className="col-12">
          <label className="form-label text-muted">{t('authorizationUrl', language)}</label>
          <p className="text-break small">{data.authorization_url}</p>
        </div>
        <div className="col-12">
          <label className="form-label text-muted">{t('tokenUrl', language)}</label>
          <p className="text-break small">{data.token_url}</p>
        </div>
        {data.userinfo_url && (
          <div className="col-12">
            <label className="form-label text-muted">{t('userinfoUrl', language)}</label>
            <p className="text-break small">{data.userinfo_url}</p>
          </div>
        )}
        <div className="col-md-6">
          <label className="form-label text-muted">{t('redirectUri', language)}</label>
          <p className="text-break">{data.redirect_uri ?? '-'}</p>
        </div>
        <div className="col-md-6">
          <label className="form-label text-muted">{t('scope', language)}</label>
          <p>{data.scope?.join(', ') ?? '-'}</p>
        </div>
      </div>

      {/* Test Connection Button */}
      <div className="mt-4">
        <Button variant="outline-primary" onClick={onTestConnection} loading={testing}>
          <i className="bi bi-plug me-1" />
          {t('testConnection', language)}
        </Button>
      </div>

      {/* Test Results */}
      {testResults && (
        <div className="mt-3">
          <h6>{t('testResults', language)}</h6>
          <ul className="list-group">
            {testResults.map((result, index) => (
              <li
                key={index}
                className="list-group-item d-flex justify-content-between align-items-center"
              >
                <span>
                  <i
                    className={cn(
                      'bi me-2',
                      result.success ? 'bi-check-circle text-success' : 'bi-x-circle text-danger'
                    )}
                  />
                  {result.check}
                </span>
                <Badge variant={result.success ? 'success' : 'danger'}>
                  {result.success ? t('passed', language) : (result.error ?? t('failed', language))}
                </Badge>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
};

interface ProviderEditFormProps {
  data: SSOProviderDetail;
  formData: UpdateProviderRequest;
  onChange: (data: UpdateProviderRequest) => void;
  language: Language;
}

const ProviderEditForm: React.FC<ProviderEditFormProps> = ({
  data,
  formData,
  onChange,
  language,
}) => {
  const [showSecretInput, setShowSecretInput] = useState(false);

  return (
    <div className="row g-3">
      <div className="col-md-6">
        <label className="form-label">{t('clientId', language)}</label>
        <TextInput
          value={formData.client_id ?? data.client_id}
          onChange={(value) => onChange({ ...formData, client_id: value })}
          placeholder={t('enterClientId', language)}
        />
      </div>
      <div className="col-md-6">
        <label className="form-label">{t('clientSecret', language)}</label>
        {showSecretInput ? (
          <TextInput
            type="password"
            value={formData.client_secret ?? ''}
            onChange={(value) => onChange({ ...formData, client_secret: value })}
            placeholder={t('enterClientSecret', language)}
          />
        ) : (
          <Button variant="outline-secondary" size="sm" onClick={() => setShowSecretInput(true)}>
            <i className="bi bi-key me-1" />
            {t('changeSecret', language)}
          </Button>
        )}
      </div>
      <div className="col-md-6">
        <label className="form-label">{t('redirectUri', language)}</label>
        <TextInput
          value={formData.redirect_uri ?? data.redirect_uri ?? ''}
          onChange={(value) => onChange({ ...formData, redirect_uri: value })}
          placeholder={t('enterRedirectUri', language)}
        />
      </div>
      <div className="col-md-6">
        <label className="form-label">{t('scope', language)}</label>
        <TextInput
          value={(formData.scope ?? data.scope ?? []).join(' ')}
          onChange={(value) =>
            onChange({
              ...formData,
              scope: value.split(' ').filter((s) => s.trim()),
            })
          }
          placeholder="openid profile email"
        />
      </div>
      <div className="col-md-6">
        <label className="form-label">{t('authorizationUrl', language)}</label>
        <TextInput
          value={formData.authorization_url ?? data.authorization_url}
          onChange={(value) => onChange({ ...formData, authorization_url: value })}
        />
      </div>
      <div className="col-md-6">
        <label className="form-label">{t('tokenUrl', language)}</label>
        <TextInput
          value={formData.token_url ?? data.token_url}
          onChange={(value) => onChange({ ...formData, token_url: value })}
        />
      </div>
    </div>
  );
};

interface ProviderRegisterFormProps {
  formData: RegisterProviderRequest;
  onChange: (data: RegisterProviderRequest) => void;
  language: Language;
  validationErrors?: Record<string, string>;
  onValidateUrl?: (field: string, value: string) => void;
  providerOptions: Array<{ value: string; label: string }>;
  predefinedProviders: PredefinedProvider[];
}

const ProviderRegisterForm: React.FC<ProviderRegisterFormProps> = ({
  formData,
  onChange,
  language,
  validationErrors = {},
  onValidateUrl,
  providerOptions,
  predefinedProviders,
}) => {
  // Local state for client secret confirmation
  const [clientSecretConfirm, setClientSecretConfirm] = useState('');
  const [secretMismatch, setSecretMismatch] = useState(false);

  // Handle client secret confirmation change
  const handleSecretConfirmChange = (value: string) => {
    setClientSecretConfirm(value);
    setSecretMismatch(formData.client_secret !== value);
  };

  // Get provider type from predefined providers
  const getProviderType = (providerName: string): 'oauth2' | 'oidc' => {
    const provider = predefinedProviders.find((p) => p.name === providerName);
    return provider?.type ?? 'oauth2';
  };

  return (
    <div className="row g-3">
      <div className="col-12">
        <label className="form-label">{t('selectProvider', language)}</label>
        <Select
          options={providerOptions}
          value={formData.predefined ? formData.name : ''}
          onChange={(value) => {
            const isPredefined = value !== '';
            onChange({
              ...formData,
              name: value,
              predefined: isPredefined,
              provider_type: isPredefined ? getProviderType(value) : 'oauth2',
            });
          }}
        />
      </div>

      {!formData.predefined && (
        <div className="col-md-6">
          <label className="form-label">{t('providerName', language)} *</label>
          <TextInput
            value={formData.name}
            onChange={(value) => onChange({ ...formData, name: value })}
            placeholder={t('enterProviderName', language)}
          />
        </div>
      )}

      <div className="col-md-6">
        <label className="form-label">{t('providerType', language)}</label>
        <Select
          options={[
            { value: 'oauth2', label: 'OAuth 2.0' },
            { value: 'oidc', label: 'OpenID Connect' },
          ]}
          value={formData.provider_type ?? 'oauth2'}
          onChange={(value) => onChange({ ...formData, provider_type: value as 'oauth2' | 'oidc' })}
        />
      </div>

      <div className="col-md-6">
        <label className="form-label">{t('clientId', language)} *</label>
        <TextInput
          value={formData.client_id}
          onChange={(value) => onChange({ ...formData, client_id: value })}
          placeholder={t('enterClientId', language)}
        />
      </div>

      <div className="col-md-6">
        <label className="form-label">{t('clientSecret', language)} *</label>
        <TextInput
          type="password"
          value={formData.client_secret}
          onChange={(value) => {
            onChange({ ...formData, client_secret: value });
            setSecretMismatch(clientSecretConfirm !== value && clientSecretConfirm !== '');
          }}
          placeholder={t('enterClientSecret', language)}
        />
      </div>

      <div className="col-md-6">
        <label className="form-label">{t('confirmClientSecret', language)} *</label>
        <TextInput
          type="password"
          value={clientSecretConfirm}
          onChange={handleSecretConfirmChange}
          placeholder={t('confirmClientSecretPlaceholder', language)}
          error={secretMismatch ? t('secretMismatch', language) : undefined}
        />
      </div>

      <div className="col-md-6">
        <label className="form-label">{t('redirectUri', language)}</label>
        <TextInput
          value={formData.redirect_uri ?? ''}
          onChange={(value) => {
            onChange({ ...formData, redirect_uri: value });
            onValidateUrl?.('redirect_uri', value);
          }}
          placeholder={t('enterRedirectUri', language)}
          error={validationErrors.redirect_uri}
          hint="https://your-app.com/callback"
        />
      </div>

      <div className="col-md-6">
        <label className="form-label">{t('scope', language)}</label>
        <TextInput
          value={(formData.scope ?? []).join(' ')}
          onChange={(value) =>
            onChange({
              ...formData,
              scope: value.split(' ').filter((s) => s.trim()),
            })
          }
          placeholder="openid profile email"
        />
      </div>

      {!formData.predefined && (
        <>
          <div className="col-12">
            <hr />
            <h6>{t('customProviderUrls', language)}</h6>
          </div>
          <div className="col-md-6">
            <label className="form-label">{t('authorizationUrl', language)}</label>
            <TextInput
              value={formData.authorization_url ?? ''}
              onChange={(value) => {
                onChange({ ...formData, authorization_url: value });
                onValidateUrl?.('authorization_url', value);
              }}
              placeholder="https://provider.com/oauth/authorize"
              error={validationErrors.authorization_url}
            />
          </div>
          <div className="col-md-6">
            <label className="form-label">{t('tokenUrl', language)}</label>
            <TextInput
              value={formData.token_url ?? ''}
              onChange={(value) => {
                onChange({ ...formData, token_url: value });
                onValidateUrl?.('token_url', value);
              }}
              placeholder="https://provider.com/oauth/token"
              error={validationErrors.token_url}
            />
          </div>
          <div className="col-md-6">
            <label className="form-label">{t('userinfoUrl', language)}</label>
            <TextInput
              value={formData.userinfo_url ?? ''}
              onChange={(value) => {
                onChange({ ...formData, userinfo_url: value });
                onValidateUrl?.('userinfo_url', value);
              }}
              placeholder="https://provider.com/oauth/userinfo"
              error={validationErrors.userinfo_url}
            />
          </div>
          <div className="col-md-6">
            <label className="form-label">{t('issuerUrl', language)}</label>
            <TextInput
              value={formData.issuer_url ?? ''}
              onChange={(value) => onChange({ ...formData, issuer_url: value })}
              placeholder="https://provider.com"
            />
          </div>
        </>
      )}
    </div>
  );
};

interface QuickRegisterFormProps {
  step: number;
  provider: string;
  domainUrl: string;
  domainPreview: { authorization_url: string; token_url: string; userinfo_url: string } | null;
  quickRegisterData: { client_id: string; client_secret: string; redirect_uri: string };
  onDomainChange: (url: string) => void;
  onDataChange: (data: { client_id: string; client_secret: string; redirect_uri: string }) => void;
  language: Language;
}

const QuickRegisterForm: React.FC<QuickRegisterFormProps> = ({
  step,
  provider,
  domainUrl,
  domainPreview,
  quickRegisterData,
  onDomainChange,
  onDataChange,
  language,
}) => {
  const isOktaOrAuth0 = provider === 'okta' || provider === 'auth0';

  return (
    <div>
      {/* Step indicator */}
      <div className="mb-4">
        <div className="d-flex justify-content-between">
          <span className={cn('badge', step >= 2 ? 'bg-primary' : 'bg-secondary')}>
            {isOktaOrAuth0 ? t('step2EnterDomain', language) : t('step1SelectProvider', language)}
          </span>
          {isOktaOrAuth0 && (
            <span className={cn('badge', step >= 3 ? 'bg-primary' : 'bg-secondary')}>
              {t('step3EnterCredentials', language)}
            </span>
          )}
        </div>
      </div>

      {/* Domain URL Step (Okta/Auth0 only) */}
      {isOktaOrAuth0 && step === 2 && (
        <div className="row g-3">
          <div className="col-12">
            <label className="form-label">{t('domainUrl', language)} *</label>
            <TextInput
              value={domainUrl}
              onChange={onDomainChange}
              placeholder={
                provider === 'okta' ? 'https://example.okta.com' : 'https://example.auth0.com'
              }
            />
            <small className="text-muted">{t('useStandardDomainHint', language)}</small>
          </div>

          {/* URL Preview */}
          {domainPreview && (
            <div className="col-12">
              <div className="alert alert-info">
                <h6>{t('urlPreview', language)}</h6>
                <small>
                  <strong>Authorization:</strong> {domainPreview.authorization_url}
                  <br />
                  <strong>Token:</strong> {domainPreview.token_url}
                  <br />
                  <strong>Userinfo:</strong> {domainPreview.userinfo_url}
                </small>
              </div>
            </div>
          )}

          {!domainPreview && domainUrl && (
            <div className="col-12">
              <div className="alert alert-warning">
                <i className="bi bi-exclamation-triangle me-2" />
                {t('domainUrlFormatError', language)}
              </div>
            </div>
          )}
        </div>
      )}

      {/* Credentials Step */}
      {step === 3 && (
        <div className="row g-3">
          <div className="col-md-6">
            <label className="form-label">{t('clientId', language)} *</label>
            <TextInput
              value={quickRegisterData.client_id}
              onChange={(value) => onDataChange({ ...quickRegisterData, client_id: value })}
              placeholder={t('enterClientId', language)}
            />
          </div>
          <div className="col-md-6">
            <label className="form-label">{t('clientSecret', language)} *</label>
            <TextInput
              type="password"
              value={quickRegisterData.client_secret}
              onChange={(value) => onDataChange({ ...quickRegisterData, client_secret: value })}
              placeholder={t('enterClientSecret', language)}
            />
          </div>
          <div className="col-12">
            <label className="form-label">{t('redirectUri', language)}</label>
            <TextInput
              value={quickRegisterData.redirect_uri}
              onChange={(value) => onDataChange({ ...quickRegisterData, redirect_uri: value })}
              placeholder={t('enterRedirectUri', language)}
            />
          </div>
        </div>
      )}
    </div>
  );
};
