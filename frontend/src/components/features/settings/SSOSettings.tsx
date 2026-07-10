/**
 * SSOSettings Component - SSO settings page
 *
 * Features:
 * - List SSO providers
 * - Register new providers
 * - Configure OAuth2/OIDC parameters
 * - Enable/Disable providers
 * - Enable/Disable SSO globally
 * - Auto-provision users setting
 * - Admin users can select tenant to manage
 */

import React, { useState, useEffect, useRef } from 'react';
import { cn } from '@/utils';
import { useLanguage } from '@/store';
import { useAuth } from '@/hooks';
import { t } from '@/i18n';
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
  type SSOProviderDetail,
  type UpdateProviderRequest,
  type Tenant,
} from '@/api';

const PREDEFINED_PROVIDERS = [
  { value: '', label: 'Custom Provider' },
  { value: 'google', label: 'Google' },
  { value: 'microsoft', label: 'Microsoft' },
  { value: 'github', label: 'GitHub' },
  { value: 'okta', label: 'Okta' },
];

export const SSOSettings: React.FC = () => {
  const language = useLanguage();
  const { user } = useAuth();
  const { success, error: toastError } = useToast();

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

  const [showModal, setShowModal] = useState(false);
  const [registerError, setRegisterError] = useState<string | null>(null);
  const [isRegistering, setIsRegistering] = useState(false);
  const [clientSecretConfirm, setClientSecretConfirm] = useState('');
  const [formData, setFormData] = useState<RegisterProviderRequest>({
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
  });

  // Provider detail modal state
  const [showDetailModal, setShowDetailModal] = useState(false);
  const [providerDetail, setProviderDetail] = useState<SSOProviderDetail | null>(null);
  const [isLoadingDetail, setIsLoadingDetail] = useState(false);

  // Provider edit modal state
  const [showEditModal, setShowEditModal] = useState(false);
  const [editError, setEditError] = useState<string | null>(null);
  const [isUpdating, setIsUpdating] = useState(false);
  const [editClientSecretConfirm, setEditClientSecretConfirm] = useState('');
  const [editFormData, setEditFormData] = useState<UpdateProviderRequest>({
    client_id: '',
    client_secret: '',
    redirect_uri: '',
    scope: '',
    authorization_url: '',
    token_url: '',
    userinfo_url: '',
    issuer_url: '',
  });

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
  const fetchProviders = React.useCallback(async () => {
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

  // Handlers
  const handleOpenCreate = () => {
    setFormData({
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
    });
    setClientSecretConfirm('');
    setShowModal(true);
  };

  const handleCloseModal = () => {
    setShowModal(false);
    setClientSecretConfirm('');
  };

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

  const handlePredefinedChange = (value: string) => {
    const isPredefined = value !== '';
    setFormData({
      ...formData,
      name: value,
      predefined: isPredefined,
      provider_type: value === 'okta' ? 'oidc' : 'oauth2',
    });
  };

  const handleSubmit = async () => {
    setRegisterError(null);

    // Validate required fields
    if (!formData.client_id.trim()) {
      setRegisterError(t('clientIdRequired', language));
      return;
    }

    if (!formData.client_secret.trim()) {
      setRegisterError(t('clientSecretRequired', language));
      return;
    }

    // Validate client_secret confirmation
    if (formData.client_secret !== clientSecretConfirm) {
      setRegisterError(t('clientSecretMismatch', language));
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
      handleCloseModal();
      fetchProviders();
      success(t('providerRegistered', language));
    } catch (err) {
      console.error('Failed to register provider:', err);
      const errorMsg = err instanceof Error ? err.message : t('registerFailed', language);
      setRegisterError(errorMsg);
    } finally {
      setIsRegistering(false);
    }
  };

  const confirm = useConfirm();
  const handleDisable = async (providerName: string) => {
    if (!(await confirm({ message: t('confirmDisableProvider', language), variant: 'warning' })))
      return;
    try {
      await ssoApi.disableProvider(providerName);
      fetchProviders();
    } catch (err) {
      console.error('Failed to disable provider:', err);
    }
  };

  // Show provider detail
  const handleShowDetail = async (providerName: string) => {
    setIsLoadingDetail(true);
    setShowDetailModal(true);
    setProviderDetail(null);

    try {
      const detail = await ssoApi.getProviderDetail(providerName);
      setProviderDetail(detail);
    } catch (err) {
      console.error('Failed to fetch provider detail:', err);
      toastError(t('failedToLoadProviderDetail', language));
      setShowDetailModal(false);
    } finally {
      setIsLoadingDetail(false);
    }
  };

  // Show provider edit modal
  const handleShowEdit = async (providerName: string) => {
    setIsLoadingDetail(true);
    setShowEditModal(true);
    setEditError(null);

    try {
      const detail = await ssoApi.getProviderDetail(providerName);

      // Initialize edit form with current values
      setEditFormData({
        client_id: detail.client_id ?? '',
        client_secret: '',
        redirect_uri: detail.redirect_uri ?? '',
        scope: detail.scope ?? '',
        authorization_url: detail.authorization_url ?? '',
        token_url: detail.token_url ?? '',
        userinfo_url: detail.userinfo_url ?? '',
        issuer_url: detail.issuer_url ?? '',
        updated_at: detail.updated_at,
      });
      setEditClientSecretConfirm('');
      setProviderDetail(detail);
    } catch (err) {
      console.error('Failed to fetch provider detail for edit:', err);
      toastError(t('failedToLoadProviderDetail', language));
      setShowEditModal(false);
    } finally {
      setIsLoadingDetail(false);
    }
  };

  // Update provider
  const handleUpdateProvider = async () => {
    setEditError(null);

    if (!providerDetail) {
      setEditError(t('providerNotFound', language));
      return;
    }

    // Validate required fields
    if (!editFormData.client_id?.trim()) {
      setEditError(t('clientIdRequired', language));
      return;
    }

    // If client_secret is provided, validate confirmation
    if (editFormData.client_secret && editFormData.client_secret !== editClientSecretConfirm) {
      setEditError(t('clientSecretMismatch', language));
      return;
    }

    setIsUpdating(true);
    try {
      await ssoApi.updateProvider(providerDetail.name, editFormData);
      setShowEditModal(false);
      fetchProviders();
      success(t('providerUpdated', language));
    } catch (err) {
      console.error('Failed to update provider:', err);
      const errorMsg = err instanceof Error ? err.message : t('updateFailed', language);
      setEditError(errorMsg);
    } finally {
      setIsUpdating(false);
    }
  };

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
            description={t('noProvidersHint', language)}
            action={
              <Button variant="primary" size="sm" onClick={handleOpenCreate}>
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
                  <tr key={provider.name}>
                    <td>
                      <strong className="text-capitalize">{provider.name}</strong>
                    </td>
                    <td>
                      <Badge variant="secondary">{provider.type.toUpperCase()}</Badge>
                    </td>
                    <td>
                      <Badge variant={provider.is_enabled ? 'success' : 'danger'}>
                        {provider.is_enabled ? t('enabled', language) : t('disabled', language)}
                      </Badge>
                    </td>
                    <td>
                      <div className="d-flex gap-2">
                        <Button
                          variant="outline-primary"
                          size="sm"
                          onClick={() => handleShowDetail(provider.name)}
                        >
                          <i className="bi bi-eye me-1" />
                          {t('view', language)}
                        </Button>
                        <Button
                          variant="outline-secondary"
                          size="sm"
                          onClick={() => handleShowEdit(provider.name)}
                        >
                          <i className="bi bi-pencil me-1" />
                          {t('edit', language)}
                        </Button>
                        <Button
                          variant="outline-danger"
                          size="sm"
                          onClick={() => handleDisable(provider.name)}
                        >
                          <i className="bi bi-x-lg me-1" />
                          {t('disable', language)}
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
        onClose={handleCloseModal}
        title={t('registerProvider', language)}
        size="lg"
        footer={
          <>
            <Button variant="secondary" onClick={handleCloseModal}>
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
            />
          </div>

          {/* Client ID */}
          <div className="col-md-6">
            <label className="form-label">{t('clientId', language)} *</label>
            <TextInput
              value={formData.client_id}
              onChange={(value: string) => setFormData({ ...formData, client_id: value })}
              placeholder={t('enterClientId', language)}
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
            />
          </div>

          {/* Client Secret Confirm */}
          <div className="col-md-6">
            <label className="form-label">{t('clientSecretConfirm', language)} *</label>
            <TextInput
              type="password"
              value={clientSecretConfirm}
              onChange={(value: string) => setClientSecretConfirm(value)}
              placeholder={t('enterClientSecretConfirm', language)}
            />
          </div>

          {/* Redirect URI */}
          <div className="col-md-6">
            <label className="form-label">{t('redirectUri', language)}</label>
            <TextInput
              value={formData.redirect_uri ?? ''}
              onChange={(value: string) => setFormData({ ...formData, redirect_uri: value })}
              placeholder={t('enterRedirectUri', language)}
            />
          </div>

          {/* Scope */}
          <div className="col-md-6">
            <label className="form-label">{t('scope', language)}</label>
            <TextInput
              value={formData.scope ?? ''}
              onChange={(value: string) => setFormData({ ...formData, scope: value })}
              placeholder="openid profile email"
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
                  onChange={(value: string) =>
                    setFormData({ ...formData, authorization_url: value })
                  }
                  placeholder="https://provider.com/oauth/authorize"
                />
              </div>
              <div className="col-md-6">
                <label className="form-label">{t('tokenUrl', language)}</label>
                <TextInput
                  value={formData.token_url ?? ''}
                  onChange={(value: string) => setFormData({ ...formData, token_url: value })}
                  placeholder="https://provider.com/oauth/token"
                />
              </div>
              <div className="col-md-6">
                <label className="form-label">{t('userinfoUrl', language)}</label>
                <TextInput
                  value={formData.userinfo_url ?? ''}
                  onChange={(value: string) => setFormData({ ...formData, userinfo_url: value })}
                  placeholder="https://provider.com/oauth/userinfo"
                />
              </div>
              <div className="col-md-6">
                <label className="form-label">{t('issuerUrl', language)}</label>
                <TextInput
                  value={formData.issuer_url ?? ''}
                  onChange={(value: string) => setFormData({ ...formData, issuer_url: value })}
                  placeholder="https://provider.com"
                />
              </div>
            </>
          )}
        </div>
      </Modal>

      {/* Provider Detail Modal */}
      <Modal
        isOpen={showDetailModal}
        onClose={() => setShowDetailModal(false)}
        title={t('providerDetail', language)}
        size="lg"
        footer={
          <Button variant="secondary" onClick={() => setShowDetailModal(false)}>
            {t('close', language)}
          </Button>
        }
      >
        {isLoadingDetail ? (
          <Loading size="md" text={t('loading', language)} />
        ) : providerDetail ? (
          <div className="provider-detail">
            <div className="row g-3">
              <div className="col-md-6">
                <label className="form-label fw-semibold">{t('providerName', language)}</label>
                <p className="form-control-static text-capitalize">{providerDetail.name}</p>
              </div>
              <div className="col-md-6">
                <label className="form-label fw-semibold">{t('type', language)}</label>
                <p className="form-control-static">
                  <Badge variant="secondary">{providerDetail.type.toUpperCase()}</Badge>
                </p>
              </div>
              <div className="col-md-6">
                <label className="form-label fw-semibold">{t('status', language)}</label>
                <p className="form-control-static">
                  <Badge variant={providerDetail.is_enabled ? 'success' : 'danger'}>
                    {providerDetail.is_enabled ? t('enabled', language) : t('disabled', language)}
                  </Badge>
                </p>
              </div>
              <div className="col-md-6">
                <label className="form-label fw-semibold">{t('predefined', language)}</label>
                <p className="form-control-static">
                  <Badge variant={providerDetail.is_predefined ? 'info' : 'secondary'}>
                    {providerDetail.is_predefined ? t('yes', language) : t('no', language)}
                  </Badge>
                </p>
              </div>
              <div className="col-md-6">
                <label className="form-label fw-semibold">{t('clientId', language)}</label>
                <p className="form-control-static">
                  <code>{providerDetail.client_id ?? '-'}</code>
                </p>
              </div>
              <div className="col-md-6">
                <label className="form-label fw-semibold">{t('redirectUri', language)}</label>
                <p className="form-control-static">
                  <code>{providerDetail.redirect_uri ?? '-'}</code>
                </p>
              </div>
              <div className="col-md-12">
                <label className="form-label fw-semibold">{t('scope', language)}</label>
                <p className="form-control-static">
                  <code>{providerDetail.scope ?? '-'}</code>
                </p>
              </div>
              <div className="col-md-6">
                <label className="form-label fw-semibold">{t('authorizationUrl', language)}</label>
                <p className="form-control-static">
                  <small>{providerDetail.authorization_url ?? '-'}</small>
                </p>
              </div>
              <div className="col-md-6">
                <label className="form-label fw-semibold">{t('tokenUrl', language)}</label>
                <p className="form-control-static">
                  <small>{providerDetail.token_url ?? '-'}</small>
                </p>
              </div>
              <div className="col-md-6">
                <label className="form-label fw-semibold">{t('userinfoUrl', language)}</label>
                <p className="form-control-static">
                  <small>{providerDetail.userinfo_url ?? '-'}</small>
                </p>
              </div>
              <div className="col-md-6">
                <label className="form-label fw-semibold">{t('issuerUrl', language)}</label>
                <p className="form-control-static">
                  <small>{providerDetail.issuer_url ?? '-'}</small>
                </p>
              </div>
              {providerDetail.created_at && (
                <div className="col-md-6">
                  <label className="form-label fw-semibold">{t('createdAt', language)}</label>
                  <p className="form-control-static">
                    <small className="text-muted">{providerDetail.created_at}</small>
                  </p>
                </div>
              )}
              {providerDetail.updated_at && (
                <div className="col-md-6">
                  <label className="form-label fw-semibold">{t('updatedAt', language)}</label>
                  <p className="form-control-static">
                    <small className="text-muted">{providerDetail.updated_at}</small>
                  </p>
                </div>
              )}
            </div>
          </div>
        ) : (
          <EmptyState
            icon="bi-exclamation-circle"
            title={t('providerNotFound', language)}
            description={t('failedToLoadProviderDetail', language)}
          />
        )}
      </Modal>

      {/* Provider Edit Modal */}
      <Modal
        isOpen={showEditModal}
        onClose={() => setShowEditModal(false)}
        title={t('editProvider', language)}
        size="lg"
        footer={
          <>
            <Button variant="secondary" onClick={() => setShowEditModal(false)}>
              {t('cancel', language)}
            </Button>
            <Button variant="primary" onClick={handleUpdateProvider} loading={isUpdating}>
              {t('save', language)}
            </Button>
          </>
        }
      >
        {isLoadingDetail ? (
          <Loading size="md" text={t('loading', language)} />
        ) : providerDetail ? (
          <>
            {editError && (
              <div className="alert alert-danger mb-3">
                <i className="bi bi-exclamation-circle me-2" />
                {editError}
              </div>
            )}
            <div className="row g-3">
              <div className="col-md-6">
                <label className="form-label">{t('providerName', language)}</label>
                <p className="form-control-static text-capitalize">{providerDetail.name}</p>
              </div>
              <div className="col-md-6">
                <label className="form-label">{t('type', language)}</label>
                <p className="form-control-static">
                  <Badge variant="secondary">{providerDetail.type.toUpperCase()}</Badge>
                </p>
              </div>

              {/* Client ID */}
              <div className="col-md-6">
                <label className="form-label">{t('clientId', language)} *</label>
                <TextInput
                  value={editFormData.client_id ?? ''}
                  onChange={(value: string) =>
                    setEditFormData({ ...editFormData, client_id: value })
                  }
                  placeholder={t('enterClientId', language)}
                />
              </div>

              {/* Client Secret (optional for edit) */}
              <div className="col-md-6">
                <label className="form-label">{t('clientSecret', language)}</label>
                <small className="text-muted d-block mb-1">
                  {t('clientSecretEditHint', language)}
                </small>
                <TextInput
                  type="password"
                  value={editFormData.client_secret ?? ''}
                  onChange={(value: string) =>
                    setEditFormData({ ...editFormData, client_secret: value })
                  }
                  placeholder={t('enterClientSecret', language)}
                />
              </div>

              {/* Client Secret Confirm */}
              {editFormData.client_secret && (
                <div className="col-md-6">
                  <label className="form-label">{t('clientSecretConfirm', language)} *</label>
                  <TextInput
                    type="password"
                    value={editClientSecretConfirm}
                    onChange={(value: string) => setEditClientSecretConfirm(value)}
                    placeholder={t('enterClientSecretConfirm', language)}
                  />
                </div>
              )}

              {/* Redirect URI */}
              <div className="col-md-6">
                <label className="form-label">{t('redirectUri', language)}</label>
                <TextInput
                  value={editFormData.redirect_uri ?? ''}
                  onChange={(value: string) =>
                    setEditFormData({ ...editFormData, redirect_uri: value })
                  }
                  placeholder={t('enterRedirectUri', language)}
                />
              </div>

              {/* Scope */}
              <div className="col-md-6">
                <label className="form-label">{t('scope', language)}</label>
                <TextInput
                  value={editFormData.scope ?? ''}
                  onChange={(value: string) => setEditFormData({ ...editFormData, scope: value })}
                  placeholder="openid profile email"
                />
              </div>

              {/* Provider URLs */}
              <div className="col-12">
                <hr />
                <h6>{t('providerUrls', language)}</h6>
              </div>
              <div className="col-md-6">
                <label className="form-label">{t('authorizationUrl', language)}</label>
                <TextInput
                  value={editFormData.authorization_url ?? ''}
                  onChange={(value: string) =>
                    setEditFormData({ ...editFormData, authorization_url: value })
                  }
                  placeholder="https://provider.com/oauth/authorize"
                />
              </div>
              <div className="col-md-6">
                <label className="form-label">{t('tokenUrl', language)}</label>
                <TextInput
                  value={editFormData.token_url ?? ''}
                  onChange={(value: string) =>
                    setEditFormData({ ...editFormData, token_url: value })
                  }
                  placeholder="https://provider.com/oauth/token"
                />
              </div>
              <div className="col-md-6">
                <label className="form-label">{t('userinfoUrl', language)}</label>
                <TextInput
                  value={editFormData.userinfo_url ?? ''}
                  onChange={(value: string) =>
                    setEditFormData({ ...editFormData, userinfo_url: value })
                  }
                  placeholder="https://provider.com/oauth/userinfo"
                />
              </div>
              <div className="col-md-6">
                <label className="form-label">{t('issuerUrl', language)}</label>
                <TextInput
                  value={editFormData.issuer_url ?? ''}
                  onChange={(value: string) =>
                    setEditFormData({ ...editFormData, issuer_url: value })
                  }
                  placeholder="https://provider.com"
                />
              </div>
            </div>
          </>
        ) : (
          <EmptyState
            icon="bi-exclamation-circle"
            title={t('providerNotFound', language)}
            description={t('failedToLoadProviderDetail', language)}
          />
        )}
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
