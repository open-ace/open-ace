/**
 * ModelGatewayConfig Component - LiteLLM-compatible model gateway configuration.
 *
 * Admin-only page to configure the optional model gateway (base URL, encrypted
 * API key, model-prefix options) and test the connection. Part of the removable
 * model_gateway feature.
 *
 * Enhanced with:
 * - Status visibility (5 states: disabled/incomplete, disabled/complete, enabled/incomplete, enabled/complete, env_override)
 * - Enable/disable toggle with confirmation
 * - Environment variable override detection
 * - Configuration completeness check
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
  Alert,
  useToast,
  useConfirm,
} from '@/components/common';
import {
  modelGatewayApi,
  type ModelGatewayStatus,
  type ModelGatewayTestResult,
} from '@/api/modelGateway';

export const ModelGatewayConfig: React.FC = () => {
  const language = useLanguage();
  const toast = useToast();
  const confirm = useConfirm();

  const [status, setStatus] = useState<ModelGatewayStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [toggling, setToggling] = useState(false);

  const [baseUrl, setBaseUrl] = useState('');
  const [apiKey, setApiKey] = useState('');
  const [prefixMode, setPrefixMode] = useState(false);
  const [modelPrefix, setModelPrefix] = useState('');

  useEffect(() => {
    void fetchConfig();
  }, []);

  const fetchConfig = async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await modelGatewayApi.getConfig();
      setStatus(result);
      if (result) {
        setBaseUrl(result.base_url ?? '');
        setApiKey(''); // never populate the secret
        setPrefixMode(Boolean(result.model_prefix_mode));
        setModelPrefix(result.model_prefix ?? '');
      }
    } catch (err) {
      setError((err as Error).message || 'Failed to fetch gateway config');
    } finally {
      setLoading(false);
    }
  };

  const handleSave = async () => {
    if (!baseUrl.trim()) {
      toast.error(t('gatewayBaseUrlRequired', language));
      return;
    }
    setSaving(true);
    try {
      await modelGatewayApi.saveConfig({
        base_url: baseUrl.trim(),
        api_key: apiKey || undefined, // omit to keep existing key when blank
        model_prefix_mode: prefixMode,
        model_prefix: prefixMode ? modelPrefix.trim() || null : null,
      });
      toast.success(t('gatewayConfigSaved', language));
      await fetchConfig();
    } catch (err) {
      toast.error((err as Error).message || 'Save failed');
    } finally {
      setSaving(false);
    }
  };

  const handleTest = async () => {
    setTesting(true);
    try {
      const result: ModelGatewayTestResult = await modelGatewayApi.testConnection({
        base_url: baseUrl.trim() || undefined,
        api_key: apiKey || undefined,
      });
      if (result.ok) {
        toast.success(result.message);
      } else {
        toast.error(result.message);
      }
    } catch (err) {
      toast.error((err as Error).message || 'Test failed');
    } finally {
      setTesting(false);
    }
  };

  const handleDelete = async () => {
    const ok = await confirm({
      message: t('gatewayConfigDeleteConfirm', language),
      variant: 'danger',
    });
    if (!ok) return;
    try {
      await modelGatewayApi.deleteConfig();
      toast.success(t('gatewayConfigDeleted', language));
      await fetchConfig();
    } catch (err) {
      toast.error((err as Error).message || 'Delete failed');
    }
  };

  const handleToggle = async (enable: boolean) => {
    if (!status) return;

    // Check if toggle is disabled
    if (status.env_override) {
      toast.warning(t('gatewayEnvOverrideWarning', language));
      return;
    }

    if (enable && !status.config_complete) {
      toast.warning(t('gatewayConfigIncompleteWarning', language));
      return;
    }

    // Show confirmation dialog
    const message = enable
      ? t('gatewayEnableConfirmMessage', language)
      : t('gatewayDisableConfirmMessage', language);

    const ok = await confirm({
      message,
      variant: enable ? 'primary' : 'danger',
    });

    if (!ok) return;

    setToggling(true);
    try {
      const result = await modelGatewayApi.setEnabled(enable, status.version);
      toast.success(result.message + ' ' + result.effective_time);
      await fetchConfig();
    } catch (err: any) {
      const errorMsg = err?.response?.data?.error ?? (err as Error).message ?? 'Toggle failed';
      const errorCode = err?.response?.data?.error_code;

      if (errorCode === 'version_conflict') {
        toast.error(t('gatewayVersionConflict', language));
        await fetchConfig();
      } else {
        toast.error(errorMsg);
      }
    } finally {
      setToggling(false);
    }
  };

  if (loading) return <Loading />;
  if (error) return <Error message={error} onRetry={fetchConfig} />;

  // Render status alert based on state
  const renderStatusAlert = () => {
    if (!status) return null;

    // Environment variable override
    if (status.env_override) {
      return (
        <Alert variant="info" style={{ marginBottom: '1rem' }}>
          <strong>{t('gatewayEnvOverride', language)}</strong>
          <p style={{ margin: '0.5rem 0 0', fontSize: '0.9em' }}>
            {t('gatewayEnvOverrideHint', language)}
          </p>
        </Alert>
      );
    }

    // Enabled but config incomplete (danger)
    if (status.enabled && !status.config_complete) {
      return (
        <Alert variant="danger" style={{ marginBottom: '1rem' }}>
          <strong>{t('gatewayEnabledIncomplete', language)}</strong>
          <p style={{ margin: '0.5rem 0 0', fontSize: '0.9em' }}>
            {t('gatewayEnabledIncompleteHint', language)}
            {status.missing_fields.length > 0 && (
              <span>
                {' '}
                {t('missingFields', language)}: {status.missing_fields.join(', ')}
              </span>
            )}
          </p>
        </Alert>
      );
    }

    // Enabled and complete (success)
    if (status.enabled && status.config_complete) {
      return (
        <Alert variant="success" style={{ marginBottom: '1rem' }}>
          <strong>{t('gatewayEnabled', language)}</strong>
          <p style={{ margin: '0.5rem 0 0', fontSize: '0.9em' }}>
            {t('gatewayEnabledHint', language)}
          </p>
        </Alert>
      );
    }

    // Disabled but configured (warning)
    if (!status.enabled && status.config_complete) {
      return (
        <Alert variant="warning" style={{ marginBottom: '1rem' }}>
          <strong>{t('gatewayDisabledConfigured', language)}</strong>
          <p style={{ margin: '0.5rem 0 0', fontSize: '0.9em' }}>
            {t('gatewayDisabledConfiguredHint', language)}
          </p>
        </Alert>
      );
    }

    // Disabled and not configured (warning)
    return (
      <Alert variant="warning" style={{ marginBottom: '1rem' }}>
        <strong>{t('gatewayDisabledIncomplete', language)}</strong>
        <p style={{ margin: '0.5rem 0 0', fontSize: '0.9em' }}>
          {t('gatewayDisabledIncompleteHint', language)}
        </p>
      </Alert>
    );
  };

  const canToggle = status && !status.env_override && (status.config_complete || !status.enabled);
  const configSourceText =
    status?.config_source === 'env'
      ? t('gatewayConfigSourceEnv', language)
      : t('gatewayConfigSourceDb', language);

  return (
    <Card>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <h2>{t('modelGatewayConfiguration', language)}</h2>
        <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
          {status && (
            <>
              <Badge variant={status.enabled ? 'success' : 'secondary'}>
                {status.enabled
                  ? t('gatewayStatusEnabled', language)
                  : t('gatewayStatusDisabled', language)}
              </Badge>
              <Badge variant="light" style={{ fontSize: '0.8em' }}>
                v{status.version}
              </Badge>
            </>
          )}
        </div>
      </div>
      <p style={{ color: 'var(--text-secondary, #666)', fontSize: '0.9em' }}>
        {t('modelGatewayDesc', language)}
      </p>

      {/* Status Alert */}
      {renderStatusAlert()}

      {/* Enable/Disable Toggle */}
      {status && (
        <div
          style={{
            marginBottom: '1.5rem',
            padding: '1rem',
            backgroundColor: 'var(--bg-secondary, #f5f5f5)',
            borderRadius: '0.5rem',
          }}
        >
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <div>
              <div style={{ fontWeight: 'bold', marginBottom: '0.25rem' }}>
                {t('gatewayRoutingToggle', language)}
              </div>
              <div style={{ fontSize: '0.85em', color: 'var(--text-secondary, #666)' }}>
                {t('gatewayRoutingToggleDesc', language)}
              </div>
              {status.env_override && (
                <div
                  style={{
                    fontSize: '0.85em',
                    color: 'var(--text-warning, #f0ad4e)',
                    marginTop: '0.25rem',
                  }}
                >
                  {t('gatewayEnvOverrideWarning', language)}
                </div>
              )}
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
              <Button
                variant={status.enabled ? 'danger' : 'success'}
                size="sm"
                onClick={() => handleToggle(!status.enabled)}
                disabled={toggling || !canToggle}
              >
                {toggling
                  ? t('loading', language)
                  : status.enabled
                    ? t('gatewayDisable', language)
                    : t('gatewayEnable', language)}
              </Button>
            </div>
          </div>
        </div>
      )}

      {/* Configuration Form (hide or make read-only if env override) */}
      {!status?.env_override && (
        <div style={{ marginTop: '1rem', display: 'flex', flexDirection: 'column', gap: '1rem' }}>
          <div>
            <label style={{ display: 'block', marginBottom: '0.25rem' }}>
              {t('gatewayBaseUrl', language)} *
            </label>
            <TextInput
              value={baseUrl}
              onChange={(v) => setBaseUrl(v)}
              placeholder="https://litellm.example.com/v1"
              disabled={saving || testing}
            />
          </div>

          <div>
            <label style={{ display: 'block', marginBottom: '0.25rem' }}>
              {t('gatewayApiKey', language)} *
              {status?.api_key_masked && (
                <span style={{ color: 'var(--text-secondary, #666)', fontWeight: 'normal' }}>
                  {' '}
                  ({t('current', language)}: {status.api_key_masked})
                </span>
              )}
            </label>
            <TextInput
              type="password"
              value={apiKey}
              onChange={(v) => setApiKey(v)}
              placeholder={status?.api_key_masked ?? 'sk-...'}
              disabled={saving || testing}
            />
            <div
              style={{
                fontSize: '0.8em',
                color: 'var(--text-secondary, #666)',
                marginTop: '0.25rem',
              }}
            >
              {t('gatewayApiKeyHint', language)}
            </div>
          </div>

          <div>
            <label style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
              <input
                type="checkbox"
                checked={prefixMode}
                onChange={(e) => setPrefixMode(e.target.checked)}
                disabled={saving || testing}
              />
              {t('modelPrefixMode', language)}
            </label>
          </div>

          {prefixMode && (
            <div>
              <label style={{ display: 'block', marginBottom: '0.25rem' }}>
                {t('modelPrefix', language)}
              </label>
              <TextInput
                value={modelPrefix}
                onChange={(v) => setModelPrefix(v)}
                placeholder={t('modelPrefixPlaceholder', language)}
                disabled={saving || testing}
              />
            </div>
          )}

          <p style={{ color: 'var(--text-secondary, #666)', fontSize: '0.85em' }}>
            {t('gatewayEnableHint', language)}
          </p>

          <div style={{ display: 'flex', gap: '0.5rem' }}>
            <Button variant="primary" onClick={handleSave} disabled={saving || testing}>
              {t('save', language)}
            </Button>
            <Button
              variant="secondary"
              onClick={handleTest}
              disabled={saving || testing || !baseUrl}
            >
              {t('testConnection', language)}
            </Button>
            {status && status.db_config_complete && (
              <Button variant="danger" onClick={handleDelete} disabled={saving || testing}>
                {t('delete', language)}
              </Button>
            )}
          </div>
        </div>
      )}

      {/* Env config info */}
      {status?.env_override && (
        <div
          style={{
            marginTop: '1rem',
            padding: '1rem',
            backgroundColor: 'var(--bg-secondary, #f5f5f5)',
            borderRadius: '0.5rem',
            fontSize: '0.9em',
          }}
        >
          <div style={{ fontWeight: 'bold', marginBottom: '0.5rem' }}>
            {t('gatewayEnvConfig', language)}
          </div>
          <div style={{ color: 'var(--text-secondary, #666)' }}>
            <div>
              {t('gatewayConfigSource', language)}: <strong>{configSourceText}</strong>
            </div>
            {status.base_url && (
              <div style={{ marginTop: '0.25rem' }}>
                {t('gatewayBaseUrl', language)}: <code>{status.base_url}</code>
              </div>
            )}
          </div>
        </div>
      )}
    </Card>
  );
};
