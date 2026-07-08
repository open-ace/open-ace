/**
 * SSOSettings Component - SSO settings page
 *
 * Features:
 * - List SSO providers
 * - Register new providers
 * - Configure OAuth2/OIDC parameters
 * - Enable/Disable providers with toggle
 * - Enable/Disable SSO globally
 * - Auto-provision users setting
 * - Admin users can select tenant to manage
 * - URL validation for provider configuration
 * - Modal data retention with confirmation
 * - Success visual feedback
 */

import React, { useState, useEffect, useRef, useCallback } from 'react';
import { cn } from '@/utils';
import { useLanguage } from '@/store';
import { useAuth } from '@/hooks';
import { t, type Language } from '@/i18n';
import { canManageAllTenants } from '@/utils/permissions';
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
import { useConfirm } from '@/components/common';
import {
  ssoApi,
  tenantApi,
  type SSOProvider,
  type PredefinedProvider,
  type RegisterProviderRequest,
  type Tenant,
} from '@/api';
import type { ApiError } from '@/types';

const PREDEFINED_PROVIDERS = [
  { value: '', label: 'Custom Provider' },
  { value: 'google', label: 'Google' },
  { value: 'microsoft', label: 'Microsoft' },
  { value: 'github', label: 'GitHub' },
  { value: 'okta', label: 'Okta' },
];

// URL validation helper
const isValidUrl = (url: string): boolean => {
  if (!url.trim()) return true; // Empty is valid (non-required fields)
  try {
    const parsed = new URL(url);
    return ['http:', 'https:'].includes(parsed.protocol);
  } catch {
    return false;
  }
};

// Check if there are unsaved changes in the form
const hasUnsavedFormData = (formData: RegisterProviderRequest): boolean => {
  return (
    formData.client_id.trim() !== '' ||
    formData.client_secret.trim() !== '' ||
    (!formData.predefined && formData.name.trim() !== '')
  );
};

// Map API error to localized message
const mapApiError = (error: unknown, language: Language): string => {
  if (error && typeof error === 'object' && 'message' in error) {
    const apiError = error as ApiError;
    // Check for specific error codes
    if (apiError.code) {
      const errorCodeMap: Record<string, string> = {
        provider_already_exists: 'providerAlreadyExists',
        invalid_client_id: 'clientIdInvalid',
        invalid_client_secret: 'clientSecretInvalid',
      };
      const translationKey = errorCodeMap[apiError.code];
      if (translationKey) {
        return t(translationKey, language);
      }
    }
    return apiError.message || t('unknownError', language);
  }
  return t('unknownError', language);
};

const emptyFormData: RegisterProviderRequest = {
  name: '',
  provider_type: 'oauth2',
  client_id: '',
  client_secret: '',
  redirect_uri: '',
  scope: '',
  predefined: false,
  authorization_url: '',
  token_url: '',
  userinfo_url: '',
  issuer_url: '',
};

export const SSOSettings: React.FC = () => {
  const language = useLanguage();
  const { user } = useAuth();
  const { success, error: toastError } = useToast();
  const confirm = useConfirm();

  // Admin tenant selection
  const isAdmin = canManageAllTenants(user);
  const [tenants, setTenants] = useState<Tenant[]>([]);
  const [selectedTenantId, setSelectedTenantId] = useState<number | null>(null);
  const selectedTenantIdRef = useRef<number | null>(null);
  const [isLoadingTenants, setIsLoadingTenants] = useState(false);

  // Compute effective tenant ID
  const effectiveTenantId = isAdmin ? selectedTenantId : user?.tenant_id;

  const [registeredProviders, setRegisteredProviders] = useState<SSOProvider[]>([]);
  const [predefinedProviders, setPredefinedProviders] = useState<PredefinedProvider[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // SSO settings state
  const [ssoEnabled, setSsoEnabled] = useState(false);
  const [autoProvision, setAutoProvision] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [successHighlight, setSuccessHighlight] = useState(false);

  // Provider toggle state
  const [toggleLoading, setToggleLoading] = useState<string | null>(null);

  // Modal state
  const [showModal, setShowModal] = useState(false);
  const [registerError, setRegisterError] = useState<string | null>(null);
  const [isRegistering, setIsRegistering] = useState(false);
  const [formData, setFormData] = useState<RegisterProviderRequest>(emptyFormData);

  // URL validation errors
  const [validationErrors, setValidationErrors] = useState<Record<string, string>>({});

  // Clear success highlight after 3 seconds
  useEffect(() => {
    if (successHighlight) {
      const timer = setTimeout(() => setSuccessHighlight(false), 3000);
      return () => clearTimeout(timer);
    }
    return undefined;
  }, [successHighlight]);

  // Fetch tenants list for admin users
  useEffect(() => {
    if (!isAdmin) return;

    setIsLoadingTenants(true);
    tenantApi
      .listTenants({ status: 'active', limit: 100 })
      .then((result) => {
        setTenants(result.tenants);
        // Use ref to check, avoid triggering useEffect again
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
      setPredefinedProviders(result.predefined as PredefinedProvider[]);
    } catch (err) {
      const errorMessage =
        err instanceof Error ? (err as Error).message : 'Failed to fetch providers';
      setError(errorMessage);
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

  // URL validation handler
  const validateUrlField = useCallback(
    (field: string, value: string): void => {
      if (!value.trim()) {
        // Clear error for empty field
        setValidationErrors((prev) => {
          const next = { ...prev };
          delete next[field];
          return next;
        });
        return;
      }

      if (!isValidUrl(value)) {
        setValidationErrors((prev) => ({
          ...prev,
          [field]: t('invalidUrlFormat', language),
        }));
        return;
      }

      // Clear error if valid
      setValidationErrors((prev) => {
        const next = { ...prev };
        delete next[field];
        return next;
      });
    },
    [language]
  );

  // Handlers
  const handleOpenCreate = () => {
    setFormData(emptyFormData);
    setValidationErrors({});
    setRegisterError(null);
    setShowModal(true);
  };

  const handleCloseModalWithConfirm = useCallback(async () => {
    if (hasUnsavedFormData(formData)) {
      const confirmed = await confirm({
        message: t('confirmDiscardChanges', language),
        variant: 'warning',
      });
      if (!confirmed) return;
    }
    setFormData(emptyFormData);
    setValidationErrors({});
    setRegisterError(null);
    setShowModal(false);
  }, [formData, confirm, language]);

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
      setSuccessHighlight(true);
    } catch (err) {
      console.error('Failed to save SSO settings:', err);
      toastError(t('saveFailed', language));
    } finally {
      setIsSaving(false);
    }
  };

  const handlePredefinedChange = (value: string) => {
    const isPredefined = value !== '';
    setFormData({
      ...formData,
      name: value,
      predefined: isPredefined,
      provider_type: value === 'okta' ? 'oidc' : 'oauth2',
    });
  };

  // Validate all URL fields before submit
  const validateAllUrls = (): boolean => {
    const urlFields = [
      'redirect_uri',
      'authorization_url',
      'token_url',
      'userinfo_url',
      'issuer_url',
    ];
    let allValid = true;

    for (const field of urlFields) {
      const value = formData[field as keyof RegisterProviderRequest] as string;
      if (!isValidUrl(value)) {
        allValid = false;
        setValidationErrors((prev) => ({
          ...prev,
          [field]: t('invalidUrlFormat', language),
        }));
      }
    }

    return allValid;
  };

  const handleSubmit = async () => {
    setRegisterError(null);

    // Validate URL fields first
    if (!validateAllUrls()) {
      setRegisterError(t('invalidUrlFormat', language));
      return;
    }

    // Validate required fields
    if (!formData.client_id.trim()) {
      setRegisterError(t('clientIdRequired', language));
      return;
    }

    if (!formData.client_secret.trim()) {
      setRegisterError(t('clientSecretRequired', language));
      return;
    }

    // Custom provider requires name
    if (!formData.predefined && !formData.name.trim()) {
      setRegisterError(t('providerNameRequired', language));
      return;
    }

    setIsRegistering(true);
    try {
      await ssoApi.registerProvider({
        ...formData,
        tenant_id: effectiveTenantId ?? undefined,
      });
      // Clear form and close modal on success
      setFormData(emptyFormData);
      setValidationErrors({});
      setShowModal(false);
      fetchProviders();
      success(t('providerRegistered', language));
    } catch (err) {
      console.error('Failed to register provider:', err);
      const errorMsg = mapApiError(err, language);
      setRegisterError(errorMsg);
    } finally {
      setIsRegistering(false);
    }
  };

  // Handle Enable/Disable toggle
  const handleToggleProvider = useCallback(
    async (providerName: string, currentStatus: boolean) => {
      const confirmMessage = currentStatus
        ? t('confirmDisableProvider', language)
        : t('confirmEnableProvider', language);

      const confirmed = await confirm({
        message: confirmMessage,
        variant: 'warning',
      });

      if (!confirmed) return;

      setToggleLoading(providerName);
      try {
        if (currentStatus) {
          await ssoApi.disableProvider(providerName);
          success(t('providerDisabled', language));
        } else {
          await ssoApi.enableProvider(providerName);
          success(t('providerEnabled', language));
        }
        fetchProviders();
      } catch (err) {
        console.error('Failed to toggle provider:', err);
        const errorMsg = mapApiError(err, language);
        toastError(errorMsg);
      } finally {
        setToggleLoading(null);
      }
    },
    [confirm, language, success, toastError, fetchProviders]
  );

  // Loading state for tenant list (admin only)
  if (isAdmin && isLoadingTenants) {
    return <Loading size="lg" text={t('loading', language)} />;
  }

  // No effective tenant - show appropriate message
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
          <Button variant="primary" size="sm" onClick={handleOpenCreate}>
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
      <Card
        title={t('ssoConfiguration', language)}
        className={cn('mb-4', successHighlight && 'border-success')}
      >
        <form className="sso-form" onSubmit={handleSaveSettings}>
          <div className="row g-3">
            <div className="col-md-6">
              <div className="form-check form-switch">
                <input
                  className="form-check-input"
                  type="checkbox"
                  id="ssoEnabled"
                  checked={ssoEnabled}
                  onChange={(e) => setSsoEnabled(e.target.checked)}
                  aria-describedby="ssoEnabledDesc"
                />
                <label className="form-check-label" htmlFor="ssoEnabled">
                  {t('enableSSO', language)}
                </label>
                <span id="ssoEnabledDesc" className="visually-hidden">
                  Enable SSO login for users through configured providers
                </span>
              </div>
            </div>
            <div className="col-md-6">
              <div className="form-check form-switch">
                <input
                  className="form-check-input"
                  type="checkbox"
                  id="autoProvision"
                  checked={autoProvision}
                  onChange={(e) => setAutoProvision(e.target.checked)}
                  aria-describedby="autoProvisionDesc"
                />
                <label className="form-check-label" htmlFor="autoProvision">
                  {t('autoProvisionUsers', language)}
                </label>
                <span id="autoProvisionDesc" className="visually-hidden">
                  Automatically create user accounts on first SSO login
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
              <Button variant="primary" size="sm" onClick={handleOpenCreate}>
                <i className="bi bi-plus-lg me-1" />
                {t('addFirstProvider', language)}
              </Button>
            }
          />
        ) : (
          <div className="table-responsive">
            <table className="table table-hover">
              <thead>
                <tr>
                  <th>{t('providerName', language)}</th>
                  <th className="d-none d-md-table-cell">{t('type', language)}</th>
                  <th>{t('status', language)}</th>
                  <th className="d-none d-md-table-cell">{t('clientId', language)}</th>
                  <th>{t('tableActions', language)}</th>
                </tr>
              </thead>
              <tbody>
                {registeredProviders.map((provider) => (
                  <tr key={provider.name}>
                    <td>
                      <strong className="text-capitalize">{provider.name}</strong>
                    </td>
                    <td className="d-none d-md-table-cell">
                      <Badge variant="secondary">{provider.type.toUpperCase()}</Badge>
                    </td>
                    <td>
                      <Badge variant={provider.is_enabled ? 'success' : 'danger'}>
                        {provider.is_enabled ? t('enabled', language) : t('disabled', language)}
                      </Badge>
                    </td>
                    <td className="d-none d-md-table-cell">
                      <span
                        className="text-truncate d-inline-block"
                        style={{ maxWidth: '150px' }}
                        title={provider.client_id || '-'}
                      >
                        {provider.client_id
                          ? `${provider.client_id.slice(0, 8)}${provider.client_id.length > 8 ? '...' : ''}`
                          : '-'}
                      </span>
                    </td>
                    <td>
                      <Button
                        variant={provider.is_enabled ? 'outline-warning' : 'outline-success'}
                        size="sm"
                        loading={toggleLoading === provider.name}
                        onClick={() => handleToggleProvider(provider.name, provider.is_enabled)}
                      >
                        <i className={`bi bi-toggle-${provider.is_enabled ? 'off' : 'on'} me-1`} />
                        {provider.is_enabled ? t('disable', language) : t('enable', language)}
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {/* Predefined Providers */}
      <Card title={t('availableProviders', language)}>
        <div className="row g-3">
          {predefinedProviders.map((provider) => (
            <div key={provider.name} className="col-md-4">
              <div className="border rounded p-3">
                <div className="d-flex align-items-center">
                  <i className={cn('bi me-2 fs-4', getProviderIcon(provider.name))} />
                  <div>
                    <strong>{provider.display_name}</strong>
                    <small className="d-block text-muted">{provider.type.toUpperCase()}</small>
                  </div>
                </div>
              </div>
            </div>
          ))}
        </div>
      </Card>

      {/* Register Provider Modal */}
      <Modal
        isOpen={showModal}
        onClose={handleCloseModalWithConfirm}
        title={t('registerProvider', language)}
        size="lg"
        footer={
          <>
            <Button variant="secondary" onClick={handleCloseModalWithConfirm}>
              {t('cancel', language)}
            </Button>
            <Button variant="primary" onClick={handleSubmit} loading={isRegistering}>
              {t('register', language)}
            </Button>
          </>
        }
      >
        {/* Error message */}
        {registerError && (
          <div className="alert alert-danger mb-3">
            <i className="bi bi-exclamation-circle me-2" />
            {registerError}
          </div>
        )}
        <div className="row g-3">
          {/* Predefined Provider Selection */}
          <div className="col-12">
            <label className="form-label">{t('selectProvider', language)}</label>
            <Select
              options={PREDEFINED_PROVIDERS}
              value={formData.predefined ? formData.name : ''}
              onChange={handlePredefinedChange}
              disabled={isRegistering}
            />
          </div>

          {/* Custom Provider Name */}
          {!formData.predefined && (
            <div className="col-md-6">
              <label className="form-label">{t('providerName', language)} *</label>
              <TextInput
                value={formData.name}
                onChange={(value: string) => setFormData({ ...formData, name: value })}
                placeholder={t('enterProviderName', language)}
                disabled={isRegistering}
              />
            </div>
          )}

          {/* Provider Type */}
          <div className="col-md-6">
            <label className="form-label">{t('providerType', language)}</label>
            <Select
              options={[
                { value: 'oauth2', label: 'OAuth 2.0' },
                { value: 'oidc', label: 'OpenID Connect' },
              ]}
              value={formData.provider_type ?? 'oauth2'}
              onChange={(value) =>
                setFormData({ ...formData, provider_type: value as 'oauth2' | 'oidc' })
              }
              disabled={isRegistering}
            />
          </div>

          {/* Client ID */}
          <div className="col-md-6">
            <label className="form-label">{t('clientId', language)} *</label>
            <TextInput
              value={formData.client_id}
              onChange={(value: string) => setFormData({ ...formData, client_id: value })}
              placeholder={t('enterClientId', language)}
              disabled={isRegistering}
            />
          </div>

          {/* Client Secret */}
          <div className="col-md-6">
            <label className="form-label">{t('clientSecret', language)} *</label>
            <TextInput
              type="password"
              value={formData.client_secret}
              onChange={(value: string) => setFormData({ ...formData, client_secret: value })}
              placeholder={t('enterClientSecret', language)}
              disabled={isRegistering}
            />
          </div>

          {/* Redirect URI */}
          <div className="col-md-6">
            <label className="form-label">{t('redirectUri', language)}</label>
            <TextInput
              value={formData.redirect_uri ?? ''}
              onChange={(value: string) => {
                setFormData({ ...formData, redirect_uri: value });
                validateUrlField('redirect_uri', value);
              }}
              placeholder={t('enterRedirectUri', language)}
              error={validationErrors['redirect_uri']}
              disabled={isRegistering}
            />
          </div>

          {/* Scope */}
          <div className="col-md-6">
            <label className="form-label">{t('scope', language)}</label>
            <TextInput
              value={formData.scope ?? ''}
              onChange={(value: string) => setFormData({ ...formData, scope: value })}
              placeholder="openid profile email"
              disabled={isRegistering}
            />
          </div>

          {/* Custom Provider URLs */}
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
                  onChange={(value: string) => {
                    setFormData({ ...formData, authorization_url: value });
                    validateUrlField('authorization_url', value);
                  }}
                  placeholder="https://provider.com/oauth/authorize"
                  error={validationErrors['authorization_url']}
                  disabled={isRegistering}
                />
              </div>
              <div className="col-md-6">
                <label className="form-label">{t('tokenUrl', language)}</label>
                <TextInput
                  value={formData.token_url ?? ''}
                  onChange={(value: string) => {
                    setFormData({ ...formData, token_url: value });
                    validateUrlField('token_url', value);
                  }}
                  placeholder="https://provider.com/oauth/token"
                  error={validationErrors['token_url']}
                  disabled={isRegistering}
                />
              </div>
              <div className="col-md-6">
                <label className="form-label">{t('userinfoUrl', language)}</label>
                <TextInput
                  value={formData.userinfo_url ?? ''}
                  onChange={(value: string) => {
                    setFormData({ ...formData, userinfo_url: value });
                    validateUrlField('userinfo_url', value);
                  }}
                  placeholder="https://provider.com/oauth/userinfo"
                  error={validationErrors['userinfo_url']}
                  disabled={isRegistering}
                />
              </div>
              <div className="col-md-6">
                <label className="form-label">{t('issuerUrl', language)}</label>
                <TextInput
                  value={formData.issuer_url ?? ''}
                  onChange={(value: string) => {
                    setFormData({ ...formData, issuer_url: value });
                    validateUrlField('issuer_url', value);
                  }}
                  placeholder="https://provider.com"
                  error={validationErrors['issuer_url']}
                  disabled={isRegistering}
                />
              </div>
            </>
          )}
        </div>
      </Modal>
    </div>
  );
};

function getProviderIcon(name: string): string {
  const icons: Record<string, string> = {
    google: 'bi-google',
    microsoft: 'bi-microsoft',
    github: 'bi-github',
    okta: 'bi-shield-lock',
  };
  return icons[name.toLowerCase()] || 'bi-key';
}