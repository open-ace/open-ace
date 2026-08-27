/**
 * FeishuConfig Component - Feishu Configuration Management
 *
 * Features:
 * - Feishu app configuration form
 * - Test connection functionality
 * - Auto sync settings
 */

import React, { useState, useEffect } from 'react';
import { useLanguage } from '@/store';
import { t } from '@/i18n';
import {
  Card,
  Button,
  TextInput,
  Loading,
  Error,
  Badge,
  useToast,
  useConfirm,
} from '@/components/common';
import { feishuConfigApi, type FeishuConfigResponse } from '@/api/feishuConfig';

export const FeishuConfig: React.FC<{ compact?: boolean }> = ({ compact = false }) => {
  const language = useLanguage();
  const toast = useToast();
  const confirm = useConfirm();

  // State
  const [config, setConfig] = useState<FeishuConfigResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);

  // Form state
  const [formData, setFormData] = useState({
    app_id: '',
    app_secret: '',
    org_sync_tenant_id: '',
    org_sync_enabled: false,
    org_sync_interval_minutes: 60,
  });

  // Password visibility state
  const [showSecret, setShowSecret] = useState(false);

  // Fetch config on mount
  useEffect(() => {
    fetchConfig();
  }, []);

  const fetchConfig = async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await feishuConfigApi.getConfig();
      setConfig(result);
      if (result) {
        setFormData({
          app_id: result.app_id,
          app_secret: '', // Don't populate secret
          org_sync_tenant_id: result.org_sync_tenant_id?.toString() ?? '',
          org_sync_enabled: result.org_sync_enabled,
          org_sync_interval_minutes: result.org_sync_interval_minutes,
        });
      }
    } catch (err) {
      const errorMessage = (err as Error).message || 'Failed to fetch Feishu config';
      setError(errorMessage);
    } finally {
      setLoading(false);
    }
  };

  const handleSave = async () => {
    // Validate required fields
    if (!formData.app_id) {
      toast.error(t('validationError', language), t('feishuAppIdRequired', language));
      return;
    }

    // Check if app_id changed, require app_secret
    const appIdChanged = config && formData.app_id !== config.app_id;
    if (!config || appIdChanged) {
      if (!formData.app_secret) {
        toast.error(t('validationError', language), t('feishuAppSecretRequired', language));
        return;
      }
    }

    // Validate sync_interval
    if (
      formData.org_sync_enabled &&
      (formData.org_sync_interval_minutes < 1 || formData.org_sync_interval_minutes > 1440)
    ) {
      toast.error(t('validationError', language), t('feishuSyncIntervalRange', language));
      return;
    }

    // Build change summary
    const changes: string[] = [];
    if (config) {
      if (formData.app_id !== config.app_id) {
        changes.push(`${t('feishuAppId', language)}: ${config.app_id} → ${formData.app_id}`);
      }
      if (formData.app_secret) {
        changes.push(`${t('feishuAppSecret', language)}: (updated)`);
      }
      if (formData.org_sync_tenant_id !== (config.org_sync_tenant_id?.toString() ?? '')) {
        changes.push(
          `${t('syncTargetTenant', language)}: ${config.org_sync_tenant_id ?? '(empty)'} → ${formData.org_sync_tenant_id || '(empty)'}`
        );
      }
      if (formData.org_sync_enabled !== config.org_sync_enabled) {
        changes.push(
          `${t('autoSync', language)}: ${config.org_sync_enabled ? t('enabled', language) : t('disabled', language)} → ${formData.org_sync_enabled ? t('enabled', language) : t('disabled', language)}`
        );
      }
      if (formData.org_sync_interval_minutes !== config.org_sync_interval_minutes) {
        changes.push(
          `${t('syncInterval', language)}: ${config.org_sync_interval_minutes} → ${formData.org_sync_interval_minutes}`
        );
      }
    }

    // Show confirmation if changes exist
    if (changes.length > 0) {
      const confirmMessage = `${t('confirmSaveFeishuConfig', language)}\n\n${changes.join('\n')}`;
      if (!(await confirm({ message: confirmMessage, variant: 'primary' }))) {
        return;
      }
    }

    setSaving(true);
    try {
      const saved = await feishuConfigApi.saveConfig({
        app_id: formData.app_id,
        app_secret: formData.app_secret || undefined,
        org_sync_tenant_id: formData.org_sync_tenant_id
          ? parseInt(formData.org_sync_tenant_id, 10)
          : undefined,
        org_sync_enabled: formData.org_sync_enabled,
        org_sync_interval_minutes: formData.org_sync_interval_minutes,
      });

      setConfig(saved);
      toast.success(t('feishuConfigSaved', language), t('feishuConfigSavedDesc', language));

      // Clear secret field after save
      setFormData({ ...formData, app_secret: '' });
    } catch (err: unknown) {
      const errorMessage = (err as Error).message || 'Failed to save Feishu config';
      toast.error(t('error', language), errorMessage);
    } finally {
      setSaving(false);
    }
  };

  const handleTestConnection = async () => {
    if (!formData.app_id) {
      toast.error(t('validationError', language), t('feishuAppIdRequired', language));
      return;
    }

    // Check if app_id changed, require app_secret
    const appIdChanged = config && formData.app_id !== config.app_id;
    if (!config || appIdChanged) {
      if (!formData.app_secret) {
        toast.error(t('validationError', language), t('feishuAppSecretRequired', language));
        return;
      }
    }

    setTesting(true);
    try {
      const result = await feishuConfigApi.testConnection({
        app_id: formData.app_id,
        app_secret: formData.app_secret || undefined,
      });

      if (result.success) {
        toast.success(t('feishuTestSuccess', language), result.message);
        // Refresh config to get updated verification status
        fetchConfig();
      } else {
        toast.error(t('feishuTestFailed', language), result.message);
      }
    } catch (err: unknown) {
      const errorMessage = (err as Error).message || 'Failed to test Feishu connection';
      toast.error(t('error', language), errorMessage);
    } finally {
      setTesting(false);
    }
  };

  const handleDelete = async () => {
    if (!(await confirm({ message: t('confirmDeleteFeishuConfig', language), variant: 'danger' })))
      return;

    try {
      await feishuConfigApi.deleteConfig();
      setConfig(null);
      setFormData({
        app_id: '',
        app_secret: '',
        org_sync_tenant_id: '',
        org_sync_enabled: false,
        org_sync_interval_minutes: 60,
      });
      toast.success(t('feishuConfigDeleted', language), t('feishuConfigDeletedDesc', language));
    } catch (err: unknown) {
      const errorMessage = (err as Error).message || 'Failed to delete Feishu config';
      toast.error(t('error', language), errorMessage);
    }
  };

  // Get status badge based on verification status
  const getStatusBadge = () => {
    if (!config) {
      return <Badge variant="secondary">{t('feishuNotConfigured', language)}</Badge>;
    }
    switch (config.verification_status) {
      case 'connected':
        return <Badge variant="success">{t('feishuConnected', language)}</Badge>;
      case 'connection_failed':
        return <Badge variant="danger">{t('feishuConnectionFailed', language)}</Badge>;
      case 'configuration_error':
        return <Badge variant="warning">{t('feishuConfigurationError', language)}</Badge>;
      case 'configured_unverified':
      default:
        return <Badge variant="secondary">{t('feishuConfiguredUnverified', language)}</Badge>;
    }
  };

  if (loading) {
    return <Loading size="lg" text={t('loading', language)} />;
  }

  return (
    <div className="feishu-config">
      {!compact && (
        <>
          {/* Header */}
          <div className="d-flex justify-content-between align-items-center mb-3">
            <h2>{t('feishuIntegration', language)}</h2>
            {getStatusBadge()}
          </div>

          {error && <Error message={error} onRetry={fetchConfig} />}

          {/* Existing config info */}
          {config && (
            <Card className="mb-3">
              <div className="d-flex align-items-center">
                <i className="bi bi-info-circle text-info me-2" />
                <span className="text-muted">{t('feishuConfigExists', language)}</span>
              </div>
            </Card>
          )}
        </>
      )}
      {compact && error && <Error message={error} onRetry={fetchConfig} />}

      {/* Configuration Form */}
      <Card className="mb-4">
        <form
          onSubmit={(e) => {
            e.preventDefault();
            handleSave();
          }}
        >
          <div className="row g-3">
            {/* App ID */}
            <div className="col-md-6">
              <label className="form-label">{t('feishuAppId', language)} *</label>
              <TextInput
                value={formData.app_id}
                onChange={(value) => setFormData({ ...formData, app_id: value })}
                placeholder={t('enterFeishuAppId', language)}
              />
            </div>

            {/* App Secret */}
            <div className="col-md-6">
              <label className="form-label">{t('feishuAppSecret', language)} *</label>
              <div className="input-group">
                <input
                  type={showSecret ? 'text' : 'password'}
                  className="form-control"
                  value={formData.app_secret}
                  onChange={(e) => setFormData({ ...formData, app_secret: e.target.value })}
                  placeholder={config?.app_secret_masked ?? t('enterFeishuAppSecret', language)}
                />
                <Button
                  variant="outline-secondary"
                  onClick={() => setShowSecret(!showSecret)}
                  title={showSecret ? t('hide', language) : t('show', language)}
                >
                  <i className={`bi ${showSecret ? 'bi-eye-slash' : 'bi-eye'}`} />
                </Button>
              </div>
              {config?.app_secret_masked && !formData.app_secret && (
                <small className="text-muted">
                  {t('currentSecret', language)}: {config.app_secret_masked}
                </small>
              )}
            </div>

            {/* Tenant ID */}
            <div className="col-md-6">
              <label className="form-label">{t('syncTargetTenant', language)}</label>
              <TextInput
                value={formData.org_sync_tenant_id}
                onChange={(value) => setFormData({ ...formData, org_sync_tenant_id: value })}
                placeholder={t('enterFeishuTenantId', language)}
              />
              <small className="text-muted">
                <i className="bi bi-info-circle me-1" />
                {t('feishuTenantIdHint', language)}
              </small>
            </div>

            {/* Auto Sync */}
            <div className="col-md-6">
              <div className="form-check form-switch mt-4">
                <input
                  className="form-check-input"
                  type="checkbox"
                  id="autoSync"
                  checked={formData.org_sync_enabled}
                  onChange={(e) => setFormData({ ...formData, org_sync_enabled: e.target.checked })}
                />
                <label className="form-check-label" htmlFor="autoSync">
                  {t('autoSync', language)}
                </label>
              </div>
              <small className="text-muted d-block mt-1">
                <i className="bi bi-info-circle me-1" />
                {t('feishuAutoSyncHint', language)}
              </small>
            </div>

            {/* Sync Interval */}
            {formData.org_sync_enabled && (
              <div className="col-md-6">
                <label className="form-label">{t('syncInterval', language)}</label>
                <div className="input-group">
                  <input
                    type="number"
                    className="form-control"
                    value={formData.org_sync_interval_minutes}
                    onChange={(e) =>
                      setFormData({
                        ...formData,
                        org_sync_interval_minutes: parseInt(e.target.value, 10) || 60,
                      })
                    }
                    min={1}
                    max={1440}
                  />
                  <span className="input-group-text">{t('minutes', language)}</span>
                </div>
                <small className="text-muted">
                  <i className="bi bi-info-circle me-1" />
                  {t('feishuSyncIntervalHint', language)}
                </small>
              </div>
            )}

            {/* Actions */}
            <div className="col-12">
              <div className="d-flex gap-2">
                <Button variant="primary" onClick={handleSave} loading={saving}>
                  <i className="bi bi-save me-1" />
                  {t('save', language)}
                </Button>
                <Button
                  variant="outline-secondary"
                  onClick={handleTestConnection}
                  loading={testing}
                >
                  <i className="bi bi-plug me-1" />
                  {t('testConnection', language)}
                </Button>
                {config && (
                  <Button variant="outline-danger" onClick={handleDelete}>
                    <i className="bi bi-trash me-1" />
                    {t('delete', language)}
                  </Button>
                )}
              </div>
            </div>
          </div>
        </form>
      </Card>

      {!compact && (
        /* Help */
        <Card title={t('help', language)} className="mt-4">
          <div className="alert alert-info">
            <h6 className="alert-heading">{t('feishuSetupGuide', language)}</h6>
            <ol className="mb-0">
              <li>{t('feishuSetupGuide1', language)}</li>
              <li>{t('feishuSetupGuide2', language)}</li>
              <li>{t('feishuSetupGuide3', language)}</li>
              <li>{t('feishuSetupGuide4', language)}</li>
            </ol>
          </div>
        </Card>
      )}
    </div>
  );
};
