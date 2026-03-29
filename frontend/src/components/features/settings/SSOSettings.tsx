/**
 * SSOSettings Component - SSO settings page
 *
 * Features:
 * - List SSO providers
 * - Register new providers
 * - Configure OAuth2/OIDC parameters
 * - Enable/Disable providers
 */

import React, { useState, useEffect } from 'react';
import { cn } from '@/utils';
import { useLanguage } from '@/store';
import { t } from '@/i18n';
import {
  Card,
  Button,
  Select,
  Loading,
  Error,
  EmptyState,
  Modal,
  TextInput,
  Badge,
} from '@/components/common';
import {
  ssoApi,
  type SSOProvider,
  type PredefinedProvider,
  type RegisterProviderRequest,
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
  const [registeredProviders, setRegisteredProviders] = useState<SSOProvider[]>([]);
  const [predefinedProviders, setPredefinedProviders] = useState<PredefinedProvider[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [showModal, setShowModal] = useState(false);
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

  // Fetch providers
  const fetchProviders = React.useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const result = await ssoApi.getProviders();
      setRegisteredProviders(result.registered);
      setPredefinedProviders(result.predefined);
    } catch (err) {
      const errorMessage =
        err instanceof Error ? (err as Error).message : 'Failed to fetch providers';
      setError(errorMessage);
    } finally {
      setIsLoading(false);
    }
  }, []);

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
    setShowModal(true);
  };

  const handleCloseModal = () => {
    setShowModal(false);
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
    try {
      await ssoApi.registerProvider(formData);
      handleCloseModal();
      fetchProviders();
    } catch (err) {
      console.error('Failed to register provider:', err);
    }
  };

  const handleDisable = async (providerName: string) => {
    if (!window.confirm(t('confirmDisableProvider', language))) return;
    try {
      await ssoApi.disableProvider(providerName);
      fetchProviders();
    } catch (err) {
      console.error('Failed to disable provider:', err);
    }
  };

  if (isLoading) {
    return <Loading size="lg" text={t('loading', language)} />;
  }

  if (error) {
    return <Error message={error} onRetry={fetchProviders} />;
  }

  return (
    <div className="sso-settings">
      {/* Header */}
      <div className="d-flex justify-content-between align-items-center mb-4">
        <h2>{t('ssoSettings', language)}</h2>
        <Button variant="primary" size="sm" onClick={handleOpenCreate}>
          <i className="bi bi-plus-lg me-1" />
          {t('addProvider', language)}
        </Button>
      </div>

      {/* Registered Providers */}
      <Card title={t('registeredProviders', language)} className="mb-4">
        {registeredProviders.length === 0 ? (
          <EmptyState icon="bi-key" title={t('noProvidersRegistered', language)} />
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
                      <Button
                        variant="outline-danger"
                        size="sm"
                        onClick={() => handleDisable(provider.name)}
                      >
                        <i className="bi bi-x-lg" />
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
        onClose={handleCloseModal}
        title={t('registerProvider', language)}
        size="lg"
        footer={
          <>
            <Button variant="secondary" onClick={handleCloseModal}>
              {t('cancel', language)}
            </Button>
            <Button variant="primary" onClick={handleSubmit}>
              {t('register', language)}
            </Button>
          </>
        }
      >
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
