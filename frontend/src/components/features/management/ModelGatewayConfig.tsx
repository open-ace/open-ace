/**
 * ModelGatewayConfig Component - LiteLLM-compatible model gateway configuration.
 *
 * Admin-only page to configure the optional model gateway (base URL, encrypted
 * API key, model-prefix options) and test the connection. Part of the removable
 * model_gateway feature.
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
import {
  modelGatewayApi,
  type ModelGatewayConfig as ModelGatewayConfigType,
  type ModelGatewayTestResult,
} from '@/api/modelGateway';

export const ModelGatewayConfig: React.FC = () => {
  const language = useLanguage();
  const toast = useToast();
  const confirm = useConfirm();

  const [config, setConfig] = useState<ModelGatewayConfigType | null>(null);
  const [enabled, setEnabled] = useState<boolean>(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);

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
      setEnabled(result.enabled);
      setConfig(result.data);
      if (result.data) {
        setBaseUrl(result.data.base_url ?? '');
        setApiKey(''); // never populate the secret
        setPrefixMode(Boolean(result.data.model_prefix_mode));
        setModelPrefix(result.data.model_prefix ?? '');
      }
    } catch (err) {
      setError((err as Error).message || 'Failed to fetch gateway config');
    } finally {
      setLoading(false);
    }
  };

  // Get status info for Alert/Badge display (three-level simplified)
  const getStatusInfo = () => {
    if (!enabled) {
      return {
        variant: 'warning' as const,
        text: t('gatewayDisabled', language),
        showInstructions: true,
      };
    }
    if (!config) {
      return {
        variant: 'warning' as const,
        text: t('gatewayEnabledNoConfig', language),
        showInstructions: false,
      };
    }
    return {
      variant: 'success' as const,
      text: t('gatewayEnabled', language),
      showInstructions: false,
    };
  };

  const handleSave = async () => {
    if (!baseUrl.trim()) {
      toast.error(t('gatewayBaseUrl', language));
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
      message: t('gatewayConfigDeleted', language),
      variant: 'danger',
    });
    if (!ok) return;
    try {
      await modelGatewayApi.deleteConfig();
      toast.success(t('gatewayConfigDeleted', language));
      setConfig(null);
      setBaseUrl('');
      setApiKey('');
      setPrefixMode(false);
      setModelPrefix('');
    } catch (err) {
      toast.error((err as Error).message || 'Delete failed');
    }
  };

  if (loading) return <Loading />;
  if (error) return <Error message={error} onRetry={fetchConfig} />;

  const statusInfo = getStatusInfo();

  return (
    <Card>
      {/* Status Alert/Badge at the top */}
      <div
        style={{
          marginBottom: '1rem',
          padding: '0.75rem',
          borderRadius: '0.25rem',
          backgroundColor:
            statusInfo.variant === 'success'
              ? 'var(--color-success-bg, #d4edda)'
              : 'var(--color-warning-bg, #fff3cd)',
          borderLeft:
            statusInfo.variant === 'success'
              ? '4px solid var(--color-success, #28a745)'
              : '4px solid var(--color-warning, #ffc107)',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
          {statusInfo.variant === 'success' ? (
            <Badge variant="success">{statusInfo.text}</Badge>
          ) : (
            <span style={{ fontWeight: 500 }}>{statusInfo.text}</span>
          )}
        </div>
        {statusInfo.showInstructions && (
          <p style={{ marginTop: '0.5rem', marginBottom: 0, fontSize: '0.9em' }}>
            {t('gatewayEnableInstructions', language)}
          </p>
        )}
      </div>

      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <h2>{t('modelGatewayConfiguration', language)}</h2>
        {config && <Badge variant="success">{config.mode}</Badge>}
      </div>
      <p style={{ color: 'var(--text-secondary, #666)', fontSize: '0.9em' }}>
        {t('modelGatewayDesc', language)}
      </p>

      <div style={{ marginTop: '1rem', display: 'flex', flexDirection: 'column', gap: '1rem' }}>
        <div>
          <label style={{ display: 'block', marginBottom: '0.25rem' }}>
            {t('gatewayBaseUrl', language)}
          </label>
          <TextInput
            value={baseUrl}
            onChange={(v) => setBaseUrl(v)}
            placeholder="https://litellm.example.com/v1"
          />
        </div>

        <div>
          <label style={{ display: 'block', marginBottom: '0.25rem' }}>
            {t('gatewayApiKey', language)}
            {config?.api_key_masked ? ` (${config.api_key_masked})` : ''}
          </label>
          <TextInput
            type="password"
            value={apiKey}
            onChange={(v) => setApiKey(v)}
            placeholder={config?.api_key_masked ?? 'sk-...'}
          />
        </div>

        <div>
          <label style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
            <input
              type="checkbox"
              checked={prefixMode}
              onChange={(e) => setPrefixMode(e.target.checked)}
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
            />
          </div>
        )}

        <div style={{ display: 'flex', gap: '0.5rem' }}>
          <Button variant="primary" onClick={handleSave} disabled={saving || testing}>
            {t('save', language)}
          </Button>
          <Button variant="secondary" onClick={handleTest} disabled={saving || testing || !baseUrl}>
            {t('testConnection', language)}
          </Button>
          {config && (
            <Button variant="danger" onClick={handleDelete} disabled={saving || testing}>
              {t('delete', language)}
            </Button>
          )}
        </div>
      </div>
    </Card>
  );
};
