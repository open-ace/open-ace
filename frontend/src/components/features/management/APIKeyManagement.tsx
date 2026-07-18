/**
 * APIKeyManagement Component - API key management page
 *
 * Features:
 * - API key list with provider badges and CLI tools
 * - Add API key dialog with CLI settings JSON editor
 * - Edit API key functionality
 * - Delete API key confirmation
 */

import React, { useMemo, useState } from 'react';
import {
  useApiKeys,
  useStoreApiKey,
  useUpdateApiKey,
  useDeleteApiKey,
  usePageRefresh,
} from '@/hooks';
import { useLanguage } from '@/store';
import { t } from '@/i18n';
import type { Language } from '@/types';
import {
  Button,
  Modal,
  TextInput,
  Select,
  Loading,
  Error,
  EmptyState,
  Badge,
  PageRefreshControl,
} from '@/components/common';
import type { BadgeVariant } from '@/components/common';
import { createMatcherConfig } from '@/utils';
import type { ApiKey } from '@/api';

/**
 * JSON Validation Status Indicator Component
 * Shows real-time validation feedback below JSON textarea
 */
interface JsonValidationIndicatorProps {
  jsonStr: string;
  validation: { valid: boolean; error: string | null };
  language: Language;
}

const JsonValidationIndicator: React.FC<JsonValidationIndicatorProps> = ({
  jsonStr,
  validation,
  language,
}) => {
  if (!jsonStr.trim()) return null;

  return (
    <div
      className={`mt-1 d-flex align-items-center ${validation.valid ? 'text-success' : 'text-danger'}`}
    >
      <i className={`bi ${validation.valid ? 'bi-check-circle-fill' : 'bi-x-circle-fill'} me-1`} />
      <small>
        {validation.valid
          ? t('jsonValid', language)
          : `${t('jsonInvalid', language)}: ${validation.error}`}
      </small>
    </div>
  );
};

const providerOptions = [
  { value: 'openai', label: 'OpenAI' },
  { value: 'anthropic', label: 'Anthropic' },
  { value: 'google', label: 'Google' },
];

const providerBadgeVariant: Record<string, BadgeVariant> = {
  openai: 'success',
  anthropic: 'primary',
  google: 'primary',
};

const cliToolOptions = [
  { value: 'claude-code', label: 'Claude Code (Anthropic)' },
  { value: 'qwen-code', label: 'Qwen Code (OpenAI)' },
  { value: 'codex-cli', label: 'Codex (OpenAI)' },
  { value: 'zcode', label: 'ZCode (Anthropic)' },
];

/**
 * Get internationalized Scope options for Select component
 * @param language - Current language
 * @returns Array of Scope options with translated labels
 */
const getScopeOptions = (language: Language) => [
  { value: 'shared', label: t('scopeShared', language) },
  { value: 'local', label: t('scopeLocal', language) },
  { value: 'remote', label: t('scopeRemote', language) },
];

// Default settings templates
const defaultClaudeSettings = `{
  "env": {
    "ANTHROPIC_MODEL": "glm-5",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL": "glm-5",
    "ANTHROPIC_DEFAULT_SONNET_MODEL": "glm-5.1"
  },
  "model": "haiku"
}`;

const defaultQwenSettings = `{
  "modelProviders": {
    "openai": [
      {
        "id": "glm-5",
        "name": "glm-5",
        "envKey": "BAILIAN_CODING_PLAN_API_KEY"
      }
    ]
  },
  "model": {
    "name": "glm-5"
  }
}`;

const defaultCodexSettings = `model_provider = "openace"
model = "glm-5"

[model_providers.openace]
name = "Open ACE Proxy"
wire_api = "responses"`;

const defaultZcodeSettings = `{
  "provider": {
    "zai": {
      "id": "zai",
      "kind": "anthropic",
      "name": "Z.AI (Anthropic-compatible)",
      "options": {}
    }
  },
  "model": {
    "main": "zai/glm-5.2",
    "lite": "zai/glm-4.5-air"
  }
}`;

export const APIKeyManagement: React.FC = () => {
  const language = useLanguage();
  const { data: keysData, isLoading, isError, error, refetch } = useApiKeys();
  const storeApiKey = useStoreApiKey();
  const updateApiKey = useUpdateApiKey();
  const deleteApiKey = useDeleteApiKey();

  const keys = useMemo(() => keysData?.keys ?? [], [keysData?.keys]);

  // Page refresh control - manual refresh for API keys
  const pageRefresh = usePageRefresh({
    page: '/manage/settings/api-keys',
    refreshKey: createMatcherConfig([['api-keys']], 'prefix'),
    interval: 0, // No auto refresh - manual only
    enabled: false,
  });

  // Memoize parsed CLI tools to avoid repeated JSON.parse in render
  const parsedCliTools = useMemo(() => {
    const map = new Map<number, string[]>();
    for (const key of keys) {
      if (key.cli_tools) {
        try {
          map.set(key.id, JSON.parse(key.cli_tools));
        } catch {
          map.set(key.id, []);
        }
      } else {
        map.set(key.id, []);
      }
    }
    return map;
  }, [keys]);

  // Dialog states
  const [showAddDialog, setShowAddDialog] = useState(false);
  const [showEditDialog, setShowEditDialog] = useState(false);
  const [showDeleteDialog, setShowDeleteDialog] = useState(false);
  const [editTarget, setEditTarget] = useState<ApiKey | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<ApiKey | null>(null);
  const [formError, setFormError] = useState<string | null>(null);

  // Form data with CLI settings
  const [formData, setFormData] = useState({
    provider: 'anthropic',
    key_name: '',
    api_key: '',
    base_url: '',
    cli_tools: [] as string[],
    claude_settings: '',
    qwen_settings: '',
    codex_settings: '',
    zcode_settings: '',
    scope: 'shared' as string,
    priority: 0,
    weight: 100,
    showAdvanced: false,
  });

  // JSON validation function (defined before useMemo to avoid ReferenceError)
  const getJsonValidationResult = (jsonStr: string): { valid: boolean; error: string | null } => {
    if (!jsonStr.trim()) return { valid: true, error: null };
    try {
      JSON.parse(jsonStr);
      return { valid: true, error: null };
    } catch (e) {
      const errorMessage = e instanceof SyntaxError ? e.message : 'Invalid JSON';
      return { valid: false, error: errorMessage };
    }
  };

  // Cached JSON validation results (avoid repeated parsing on each render)
  const claudeValidation = useMemo(
    () => getJsonValidationResult(formData.claude_settings),
    [formData.claude_settings]
  );

  const qwenValidation = useMemo(
    () => getJsonValidationResult(formData.qwen_settings),
    [formData.qwen_settings]
  );

  const zcodeValidation = useMemo(
    () => getJsonValidationResult(formData.zcode_settings),
    [formData.zcode_settings]
  );

  const stringifyCodexSettings = (value: unknown): string => {
    if (typeof value === 'string') return value;
    if (!value || typeof value !== 'object' || Array.isArray(value)) return '';

    const formatKey = (key: string) =>
      /^[A-Za-z0-9_-]+$/.test(key) ? key : `"${key.replace(/\\/g, '\\\\').replace(/"/g, '\\"')}"`;

    const formatValue = (input: unknown): string => {
      if (typeof input === 'string')
        return `"${input.replace(/\\/g, '\\\\').replace(/"/g, '\\"')}"`;
      if (typeof input === 'boolean') return input ? 'true' : 'false';
      if (typeof input === 'number') return String(input);
      if (Array.isArray(input)) return `[${input.map((item) => formatValue(item)).join(', ')}]`;
      return `"${String(input)}"`;
    };

    const emitTable = (obj: Record<string, unknown>, path: string[] = []): string[] => {
      const lines: string[] = [];
      const scalarEntries = Object.entries(obj).filter(
        ([, val]) => !(val && typeof val === 'object' && !Array.isArray(val))
      );
      const tableEntries = Object.entries(obj).filter(
        ([, val]) => val && typeof val === 'object' && !Array.isArray(val)
      );

      if (path.length) {
        lines.push(`[${path.map(formatKey).join('.')}]`);
      }
      for (const [key, val] of scalarEntries) {
        lines.push(`${formatKey(key)} = ${formatValue(val)}`);
      }
      if (scalarEntries.length && tableEntries.length) lines.push('');
      tableEntries.forEach(([key, val], index) => {
        lines.push(...emitTable(val as Record<string, unknown>, [...path, key]));
        if (index !== tableEntries.length - 1) lines.push('');
      });
      return lines;
    };

    return emitTable(value as Record<string, unknown>).join('\n');
  };

  const handleOpenAdd = () => {
    setFormError(null);
    setFormData({
      provider: 'anthropic',
      key_name: '',
      api_key: '',
      base_url: '',
      cli_tools: [],
      claude_settings: '',
      qwen_settings: '',
      codex_settings: '',
      zcode_settings: '',
      scope: 'shared',
      priority: 0,
      weight: 100,
      showAdvanced: false,
    });
    setShowAddDialog(true);
  };

  const handleOpenEdit = (key: ApiKey) => {
    setFormError(null);
    setEditTarget(key);

    // Parse existing cli_tools and cli_settings
    let cliTools: string[] = [];
    let claudeSettings = '';
    let qwenSettings = '';
    let codexSettings = '';
    let zcodeSettings = '';

    if (key.cli_tools) {
      try {
        cliTools = JSON.parse(key.cli_tools);
      } catch {
        cliTools = [];
      }
    }

    if (key.cli_settings) {
      try {
        const settings = JSON.parse(key.cli_settings);
        if (settings['claude-code']) {
          claudeSettings = JSON.stringify(settings['claude-code'], null, 2);
        }
        if (settings['qwen-code']) {
          qwenSettings = JSON.stringify(settings['qwen-code'], null, 2);
        }
        if (settings['codex-cli']) {
          codexSettings = stringifyCodexSettings(settings['codex-cli']);
        }
        if (settings['zcode']) {
          zcodeSettings = JSON.stringify(settings['zcode'], null, 2);
        }
      } catch {
        // Ignore parse errors
      }
    }

    setFormData({
      provider: key.provider,
      key_name: key.key_name,
      api_key: '', // Don't show existing key
      base_url: key.base_url ?? '',
      cli_tools: cliTools,
      claude_settings: claudeSettings,
      qwen_settings: qwenSettings,
      codex_settings: codexSettings,
      zcode_settings: zcodeSettings,
      scope: key.scope || 'shared',
      priority: key.priority ?? 0,
      weight: key.weight ?? 100,
      showAdvanced: (key.priority ?? 0) !== 0 || (key.weight ?? 100) !== 100,
    });
    setShowEditDialog(true);
  };

  const toggleCliTool = (tool: string) => {
    const currentTools = formData.cli_tools;
    if (currentTools.includes(tool)) {
      setFormData({
        ...formData,
        cli_tools: currentTools.filter((t) => t !== tool),
      });
    } else {
      // Add tool and set default settings if empty
      let newClaudeSettings = formData.claude_settings;
      let newQwenSettings = formData.qwen_settings;
      let newCodexSettings = formData.codex_settings;
      let newZcodeSettings = formData.zcode_settings;

      if (tool === 'claude-code' && !formData.claude_settings) {
        newClaudeSettings = defaultClaudeSettings;
      }
      if (tool === 'qwen-code' && !formData.qwen_settings) {
        newQwenSettings = defaultQwenSettings;
      }
      if (tool === 'codex-cli' && !formData.codex_settings) {
        newCodexSettings = defaultCodexSettings;
      }
      if (tool === 'zcode' && !formData.zcode_settings) {
        newZcodeSettings = defaultZcodeSettings;
      }

      setFormData({
        ...formData,
        cli_tools: [...currentTools, tool],
        claude_settings: newClaudeSettings,
        qwen_settings: newQwenSettings,
        codex_settings: newCodexSettings,
        zcode_settings: newZcodeSettings,
      });
    }
  };

  const validateJsonSettings = (jsonStr: string): boolean => {
    if (!jsonStr.trim()) return true;
    try {
      JSON.parse(jsonStr);
      return true;
    } catch {
      return false;
    }
  };

  // Fields that should never be stored in cli_settings —
  // API credentials are injected via environment variables.
  const SENSITIVE_ENV_KEYS = new Set([
    'ANTHROPIC_API_KEY',
    'ANTHROPIC_BASE_URL',
    'OPENAI_API_KEY',
    'OPENAI_BASE_URL',
  ]);

  const stripSensitiveFields = (toolSettings: Record<string, unknown>): Record<string, unknown> => {
    const cleaned = { ...toolSettings };
    const modelProviders = cleaned.modelProviders as Record<string, unknown[]> | undefined;

    // Collect dynamic env key names from modelProviders (qwen-code)
    // e.g. envKey: "ZAI_API_KEY" or "BAILIAN_CODING_PLAN_API_KEY"
    const dynamicEnvKeys = new Set<string>();
    if (modelProviders && typeof modelProviders === 'object') {
      for (const models of Object.values(modelProviders)) {
        if (Array.isArray(models)) {
          for (const model of models) {
            if (
              model &&
              typeof model === 'object' &&
              typeof (model as Record<string, unknown>).envKey === 'string'
            ) {
              dynamicEnvKeys.add((model as Record<string, unknown>).envKey as string);
            }
          }
        }
      }
    }

    const allSensitive = new Set([...SENSITIVE_ENV_KEYS, ...dynamicEnvKeys]);

    // Strip sensitive env keys
    const env = cleaned.env as Record<string, unknown> | undefined;
    if (env && typeof env === 'object') {
      const cleanEnv = { ...env };
      for (const key of Object.keys(cleanEnv)) {
        if (allSensitive.has(key)) {
          delete cleanEnv[key];
        }
      }
      cleaned.env = cleanEnv;
    }

    // Strip baseUrl from modelProviders (qwen-code)
    if (modelProviders && typeof modelProviders === 'object') {
      for (const models of Object.values(modelProviders)) {
        if (Array.isArray(models)) {
          for (const model of models) {
            if (model && typeof model === 'object' && 'baseUrl' in model) {
              delete (model as Record<string, unknown>).baseUrl;
            }
          }
        }
      }
    }
    return cleaned;
  };

  const buildCliSettingsJson = (): string => {
    const settings: Record<string, unknown> = {};
    if (formData.cli_tools.includes('claude-code') && formData.claude_settings) {
      try {
        settings['claude-code'] = stripSensitiveFields(JSON.parse(formData.claude_settings));
      } catch {
        // Skip invalid JSON
      }
    }
    if (formData.cli_tools.includes('qwen-code') && formData.qwen_settings) {
      try {
        settings['qwen-code'] = stripSensitiveFields(JSON.parse(formData.qwen_settings));
      } catch {
        // Skip invalid JSON
      }
    }
    if (formData.cli_tools.includes('codex-cli') && formData.codex_settings.trim()) {
      // Codex uses TOML as its native config format, so we preserve the
      // raw editor text here and let the backend validate/parse it.
      settings['codex-cli'] = formData.codex_settings;
    }
    if (formData.cli_tools.includes('zcode') && formData.zcode_settings) {
      try {
        settings['zcode'] = stripSensitiveFields(JSON.parse(formData.zcode_settings));
      } catch {
        // Skip invalid JSON
      }
    }
    return JSON.stringify(settings);
  };

  const handleAddKey = async () => {
    setFormError(null);

    if (!formData.key_name.trim()) {
      setFormError(t('enterKeyName', language));
      return;
    }
    if (!formData.api_key.trim()) {
      setFormError(t('enterApiKey', language));
      return;
    }

    // Validate JSON settings
    if (
      formData.cli_tools.includes('claude-code') &&
      !validateJsonSettings(formData.claude_settings)
    ) {
      setFormError(t('claudeSettingsInvalid', language));
      return;
    }
    if (formData.cli_tools.includes('qwen-code') && !validateJsonSettings(formData.qwen_settings)) {
      setFormError(t('qwenSettingsInvalid', language));
      return;
    }
    if (formData.cli_tools.includes('zcode') && !validateJsonSettings(formData.zcode_settings)) {
      setFormError(t('zcodeSettingsInvalid', language));
      return;
    }

    try {
      await storeApiKey.mutateAsync({
        provider: formData.provider,
        key_name: formData.key_name,
        api_key: formData.api_key,
        base_url: formData.base_url || undefined,
        cli_tools: JSON.stringify(formData.cli_tools),
        cli_settings: buildCliSettingsJson(),
        scope: formData.scope,
        priority: formData.priority,
        weight: formData.weight,
      });
      setShowAddDialog(false);
      refetch();
    } catch (err: unknown) {
      const msg = (err as Error)?.message ?? t('error', language);
      setFormError(msg);
    }
  };

  const handleEditKey = async () => {
    if (!editTarget) return;
    setFormError(null);

    if (!formData.key_name.trim()) {
      setFormError(t('enterKeyName', language));
      return;
    }

    // Validate JSON settings
    if (
      formData.cli_tools.includes('claude-code') &&
      !validateJsonSettings(formData.claude_settings)
    ) {
      setFormError(t('claudeSettingsInvalid', language));
      return;
    }
    if (formData.cli_tools.includes('qwen-code') && !validateJsonSettings(formData.qwen_settings)) {
      setFormError(t('qwenSettingsInvalid', language));
      return;
    }
    if (formData.cli_tools.includes('zcode') && !validateJsonSettings(formData.zcode_settings)) {
      setFormError(t('zcodeSettingsInvalid', language));
      return;
    }

    try {
      await updateApiKey.mutateAsync({
        keyId: editTarget.id,
        key_name: formData.key_name,
        base_url: formData.base_url || undefined,
        cli_tools: JSON.stringify(formData.cli_tools),
        cli_settings: buildCliSettingsJson(),
        scope: formData.scope,
        priority: formData.priority,
        weight: formData.weight,
      });
      setShowEditDialog(false);
      setEditTarget(null);
      refetch();
    } catch (err: unknown) {
      const msg = (err as Error)?.message ?? t('error', language);
      setFormError(msg);
    }
  };

  const handleOpenDelete = (key: ApiKey) => {
    setDeleteTarget(key);
    setShowDeleteDialog(true);
  };

  const handleDelete = async () => {
    if (!deleteTarget) return;
    try {
      await deleteApiKey.mutateAsync({ keyId: deleteTarget.id });
      setShowDeleteDialog(false);
      setDeleteTarget(null);
      refetch();
    } catch (err) {
      console.error('Failed to delete API key:', err);
    }
  };

  if (isLoading) {
    return <Loading size="lg" text={t('loading', language)} />;
  }

  if (isError) {
    return <Error message={error?.message || t('error', language)} onRetry={() => refetch()} />;
  }

  return (
    <div className="api-key-management">
      {/* Header */}
      <div className="d-flex justify-content-between align-items-center mb-3">
        <h2>{t('apiKeys', language)}</h2>
        <div className="d-flex gap-2">
          <PageRefreshControl
            refresh={pageRefresh}
            compact={true}
            showAutoRefreshToggle={false}
            showIntervalSelector={false}
            showLastRefreshTime={true}
          />
          <Button variant="primary" size="sm" onClick={handleOpenAdd}>
            <i className="bi bi-plus-lg me-1" />
            {t('addApiKey', language)}
          </Button>
        </div>
      </div>

      {/* Key Table */}
      {keys.length === 0 ? (
        <EmptyState
          icon="bi-key"
          title={t('noApiKeys', language)}
          description={t('noApiKeysDescription', language)}
        />
      ) : (
        <div className="table-responsive">
          <table className="table table-hover">
            <thead>
              <tr>
                <th>{t('provider', language)}</th>
                <th>{t('keyName', language)}</th>
                <th>{t('scope', language)}</th>
                <th>{t('cliTools', language)}</th>
                <th>{t('keyStatus', language)}</th>
                <th>{t('tableCreatedAt', language)}</th>
                <th>{t('tableActions', language)}</th>
              </tr>
            </thead>
            <tbody>
              {keys.map((key) => {
                const cliTools = parsedCliTools.get(key.id) ?? [];
                const scopeLabel =
                  key.scope === 'shared'
                    ? t('scopeBadgeShared', language)
                    : key.scope === 'local'
                      ? t('scopeBadgeLocal', language)
                      : t('scopeBadgeRemote', language);
                const scopeVariant =
                  key.scope === 'shared' ? 'success' : key.scope === 'local' ? 'info' : 'warning';
                return (
                  <tr key={key.id}>
                    <td>
                      <Badge variant={providerBadgeVariant[key.provider] || 'secondary'}>
                        {key.provider}
                      </Badge>
                    </td>
                    <td>
                      <strong>{key.key_name}</strong>
                      {key.priority > 0 && (
                        <small className="text-muted ms-1">P{key.priority}</small>
                      )}
                    </td>
                    <td>
                      <Badge variant={scopeVariant}>{scopeLabel}</Badge>
                    </td>
                    <td>
                      {cliTools.length > 0 ? (
                        cliTools.map((tool) => (
                          <Badge key={tool} variant="info" className="me-1">
                            {tool}
                          </Badge>
                        ))
                      ) : (
                        <span className="text-muted">-</span>
                      )}
                    </td>
                    <td>
                      <div className="d-flex align-items-center gap-2">
                        <div className="form-check form-switch mb-0">
                          <input
                            className="form-check-input"
                            type="checkbox"
                            role="switch"
                            checked={key.is_active}
                            onChange={() => {
                              updateApiKey.mutate({
                                keyId: key.id,
                                is_active: !key.is_active,
                              });
                            }}
                            disabled={updateApiKey.isPending}
                          />
                        </div>
                        <Badge variant={key.is_active ? 'success' : 'secondary'}>
                          {key.is_active ? t('active', language) : t('inactive', language)}
                        </Badge>
                      </div>
                    </td>
                    <td>{new Date(key.created_at).toLocaleDateString()}</td>
                    <td>
                      <Button
                        variant="outline-primary"
                        size="sm"
                        onClick={() => handleOpenEdit(key)}
                        className="me-1"
                      >
                        <i className="bi bi-pencil" />
                      </Button>
                      <Button
                        variant="outline-danger"
                        size="sm"
                        onClick={() => handleOpenDelete(key)}
                        disabled={deleteApiKey.isPending}
                      >
                        <i className="bi bi-trash" />
                      </Button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {/* Add API Key Dialog */}
      <Modal
        isOpen={showAddDialog}
        onClose={() => setShowAddDialog(false)}
        title={t('addApiKey', language)}
        size="lg"
        footer={
          <>
            <Button variant="secondary" onClick={() => setShowAddDialog(false)}>
              {t('cancel', language)}
            </Button>
            <Button variant="primary" onClick={handleAddKey} loading={storeApiKey.isPending}>
              {t('save', language)}
            </Button>
          </>
        }
      >
        {formError && (
          <div className="alert alert-danger mb-3" role="alert">
            <i className="bi bi-exclamation-triangle-fill me-2" />
            {formError}
          </div>
        )}

        <div className="mb-3">
          <label className="form-label">{t('provider', language)}</label>
          <Select
            options={providerOptions}
            value={formData.provider}
            onChange={(v) => setFormData({ ...formData, provider: v })}
          />
        </div>
        <div className="mb-3">
          <label className="form-label">{t('keyName', language)}</label>
          <TextInput
            value={formData.key_name}
            onChange={(v) => setFormData({ ...formData, key_name: v })}
            placeholder={t('enterKeyName', language)}
          />
        </div>
        <div className="mb-3">
          <label className="form-label">{t('apiKey', language)}</label>
          <TextInput
            type="password"
            value={formData.api_key}
            onChange={(v) => setFormData({ ...formData, api_key: v })}
            placeholder={t('enterApiKey', language)}
          />
        </div>
        <div className="mb-3">
          <label className="form-label">{t('baseUrl', language)}</label>
          <TextInput
            value={formData.base_url}
            onChange={(v) => setFormData({ ...formData, base_url: v })}
            placeholder={t('enterBaseUrl', language)}
          />
        </div>

        {/* Scope Selection */}
        <div className="mb-3">
          <label className="form-label">{t('scope', language)}</label>
          <Select
            options={getScopeOptions(language)}
            value={formData.scope}
            onChange={(v) => setFormData({ ...formData, scope: v })}
          />
          <small className="text-muted">{t('scopeHelp', language)}</small>
        </div>

        {/* Advanced Settings (collapsed by default) */}
        <div className="mb-3">
          <button
            type="button"
            className="btn btn-link p-0 text-decoration-none"
            onClick={() => setFormData({ ...formData, showAdvanced: !formData.showAdvanced })}
          >
            <i className={`bi bi-chevron-${formData.showAdvanced ? 'up' : 'down'} me-1`} />
            {t('advancedSettings', language)}
          </button>
          {formData.showAdvanced && (
            <div className="api-key-advanced-settings mt-2 p-3 border rounded bg-light">
              <div className="row">
                <div className="col-6">
                  <label className="form-label">{t('priority', language)}</label>
                  <TextInput
                    type="number"
                    value={String(formData.priority)}
                    onChange={(v) => setFormData({ ...formData, priority: parseInt(v) || 0 })}
                    placeholder="0"
                  />
                  <small className="text-muted">{t('priorityHelp', language)}</small>
                </div>
                <div className="col-6">
                  <label className="form-label">{t('weight', language)}</label>
                  <TextInput
                    type="number"
                    value={String(formData.weight)}
                    onChange={(v) => setFormData({ ...formData, weight: parseInt(v) || 100 })}
                    placeholder="100"
                  />
                  <small className="text-muted">{t('weightHelp', language)}</small>
                </div>
              </div>
            </div>
          )}
        </div>

        {/* CLI Tools Section */}
        <div className="mb-3">
          <label className="form-label">{t('cliTools', language)}</label>
          <div className="d-flex gap-3">
            {cliToolOptions.map((tool) => (
              <div key={tool.value} className="form-check">
                <input
                  type="checkbox"
                  className="form-check-input"
                  checked={formData.cli_tools.includes(tool.value)}
                  onChange={() => toggleCliTool(tool.value)}
                />
                <label className="form-check-label">{tool.label}</label>
              </div>
            ))}
          </div>
          <small className="text-muted">{t('cliToolsDescription', language)}</small>
        </div>

        {/* Claude Code Settings */}
        {formData.cli_tools.includes('claude-code') && (
          <div className="mb-3">
            <label className="form-label">{t('claudeCodeSettings', language)}</label>
            <textarea
              className={`form-control ${
                formData.claude_settings.trim() && !claudeValidation.valid ? 'is-invalid' : ''
              }`}
              rows={8}
              value={formData.claude_settings}
              onChange={(e) => setFormData({ ...formData, claude_settings: e.target.value })}
              placeholder={defaultClaudeSettings}
            />
            <JsonValidationIndicator
              jsonStr={formData.claude_settings}
              validation={claudeValidation}
              language={language}
            />
            <small className="text-muted">{t('claudeCodeSettingsHint', language)}</small>
          </div>
        )}

        {/* Qwen Code Settings */}
        {formData.cli_tools.includes('qwen-code') && (
          <div className="mb-3">
            <label className="form-label">{t('qwenCodeSettings', language)}</label>
            <textarea
              className={`form-control ${
                formData.qwen_settings.trim() && !qwenValidation.valid ? 'is-invalid' : ''
              }`}
              rows={10}
              value={formData.qwen_settings}
              onChange={(e) => setFormData({ ...formData, qwen_settings: e.target.value })}
              placeholder={defaultQwenSettings}
            />
            <JsonValidationIndicator
              jsonStr={formData.qwen_settings}
              validation={qwenValidation}
              language={language}
            />
            <small className="text-muted">{t('qwenCodeSettingsHint', language)}</small>
          </div>
        )}

        {formData.cli_tools.includes('codex-cli') && (
          <div className="mb-3">
            <label className="form-label">{t('codexSettings', language)}</label>
            <textarea
              className="form-control"
              rows={8}
              value={formData.codex_settings}
              onChange={(e) => setFormData({ ...formData, codex_settings: e.target.value })}
              placeholder={defaultCodexSettings}
            />
            <small className="text-muted">{t('codexSettingsHint', language)}</small>
          </div>
        )}

        {/* ZCode Settings */}
        {formData.cli_tools.includes('zcode') && (
          <div className="mb-3">
            <label className="form-label">{t('zcodeSettings', language)}</label>
            <textarea
              className={`form-control ${
                formData.zcode_settings.trim() && !zcodeValidation.valid ? 'is-invalid' : ''
              }`}
              rows={10}
              value={formData.zcode_settings}
              onChange={(e) => setFormData({ ...formData, zcode_settings: e.target.value })}
              placeholder={defaultZcodeSettings}
            />
            <JsonValidationIndicator
              jsonStr={formData.zcode_settings}
              validation={zcodeValidation}
              language={language}
            />
            <small className="text-muted">{t('zcodeSettingsHint', language)}</small>
          </div>
        )}
      </Modal>

      {/* Edit API Key Dialog */}
      <Modal
        isOpen={showEditDialog}
        onClose={() => setShowEditDialog(false)}
        title={t('editApiKey', language)}
        size="lg"
        footer={
          <>
            <Button variant="secondary" onClick={() => setShowEditDialog(false)}>
              {t('cancel', language)}
            </Button>
            <Button variant="primary" onClick={handleEditKey} loading={updateApiKey.isPending}>
              {t('save', language)}
            </Button>
          </>
        }
      >
        {formError && (
          <div className="alert alert-danger mb-3" role="alert">
            <i className="bi bi-exclamation-triangle-fill me-2" />
            {formError}
          </div>
        )}

        <div className="mb-3">
          <label className="form-label">{t('provider', language)}</label>
          <Badge variant={providerBadgeVariant[formData.provider] || 'secondary'}>
            {formData.provider}
          </Badge>
          <small className="text-muted d-block mt-1">{t('providerCannotChange', language)}</small>
        </div>
        <div className="mb-3">
          <label className="form-label">{t('keyName', language)}</label>
          <TextInput
            value={formData.key_name}
            onChange={(v) => setFormData({ ...formData, key_name: v })}
            placeholder={t('enterKeyName', language)}
          />
        </div>
        <div className="mb-3">
          <label className="form-label">{t('baseUrl', language)}</label>
          <TextInput
            value={formData.base_url}
            onChange={(v) => setFormData({ ...formData, base_url: v })}
            placeholder={t('enterBaseUrl', language)}
          />
        </div>

        {/* Scope Selection */}
        <div className="mb-3">
          <label className="form-label">{t('scope', language)}</label>
          <Select
            options={getScopeOptions(language)}
            value={formData.scope}
            onChange={(v) => setFormData({ ...formData, scope: v })}
          />
          <small className="text-muted">{t('scopeHelp', language)}</small>
        </div>

        {/* Advanced Settings (collapsed by default) */}
        <div className="mb-3">
          <button
            type="button"
            className="btn btn-link p-0 text-decoration-none"
            onClick={() => setFormData({ ...formData, showAdvanced: !formData.showAdvanced })}
          >
            <i className={`bi bi-chevron-${formData.showAdvanced ? 'up' : 'down'} me-1`} />
            {t('advancedSettings', language)}
          </button>
          {formData.showAdvanced && (
            <div className="api-key-advanced-settings mt-2 p-3 border rounded bg-light">
              <div className="row">
                <div className="col-6">
                  <label className="form-label">{t('priority', language)}</label>
                  <TextInput
                    type="number"
                    value={String(formData.priority)}
                    onChange={(v) => setFormData({ ...formData, priority: parseInt(v) || 0 })}
                    placeholder="0"
                  />
                  <small className="text-muted">{t('priorityHelp', language)}</small>
                </div>
                <div className="col-6">
                  <label className="form-label">{t('weight', language)}</label>
                  <TextInput
                    type="number"
                    value={String(formData.weight)}
                    onChange={(v) => setFormData({ ...formData, weight: parseInt(v) || 100 })}
                    placeholder="100"
                  />
                  <small className="text-muted">{t('weightHelp', language)}</small>
                </div>
              </div>
            </div>
          )}
        </div>

        {/* CLI Tools Section */}
        <div className="mb-3">
          <label className="form-label">{t('cliTools', language)}</label>
          <div className="d-flex gap-3">
            {cliToolOptions.map((tool) => (
              <div key={tool.value} className="form-check">
                <input
                  type="checkbox"
                  className="form-check-input"
                  checked={formData.cli_tools.includes(tool.value)}
                  onChange={() => toggleCliTool(tool.value)}
                />
                <label className="form-check-label">{tool.label}</label>
              </div>
            ))}
          </div>
        </div>

        {/* Claude Code Settings */}
        {formData.cli_tools.includes('claude-code') && (
          <div className="mb-3">
            <label className="form-label">{t('claudeCodeSettings', language)}</label>
            <textarea
              className={`form-control ${
                formData.claude_settings.trim() && !claudeValidation.valid ? 'is-invalid' : ''
              }`}
              rows={8}
              value={formData.claude_settings}
              onChange={(e) => setFormData({ ...formData, claude_settings: e.target.value })}
            />
            <JsonValidationIndicator
              jsonStr={formData.claude_settings}
              validation={claudeValidation}
              language={language}
            />
            <small className="text-muted">{t('claudeCodeSettingsHint', language)}</small>
          </div>
        )}

        {/* Qwen Code Settings */}
        {formData.cli_tools.includes('qwen-code') && (
          <div className="mb-3">
            <label className="form-label">{t('qwenCodeSettings', language)}</label>
            <textarea
              className={`form-control ${
                formData.qwen_settings.trim() && !qwenValidation.valid ? 'is-invalid' : ''
              }`}
              rows={10}
              value={formData.qwen_settings}
              onChange={(e) => setFormData({ ...formData, qwen_settings: e.target.value })}
            />
            <JsonValidationIndicator
              jsonStr={formData.qwen_settings}
              validation={qwenValidation}
              language={language}
            />
            <small className="text-muted">{t('qwenCodeSettingsHint', language)}</small>
          </div>
        )}

        {formData.cli_tools.includes('codex-cli') && (
          <div className="mb-3">
            <label className="form-label">{t('codexSettings', language)}</label>
            <textarea
              className="form-control"
              rows={8}
              value={formData.codex_settings}
              onChange={(e) => setFormData({ ...formData, codex_settings: e.target.value })}
            />
            <small className="text-muted">{t('codexSettingsHint', language)}</small>
          </div>
        )}

        {formData.cli_tools.includes('zcode') && (
          <div className="mb-3">
            <label className="form-label">{t('zcodeSettings', language)}</label>
            <textarea
              className={`form-control ${
                formData.zcode_settings.trim() && !zcodeValidation.valid ? 'is-invalid' : ''
              }`}
              rows={10}
              value={formData.zcode_settings}
              onChange={(e) => setFormData({ ...formData, zcode_settings: e.target.value })}
              placeholder={defaultZcodeSettings}
            />
            <JsonValidationIndicator
              jsonStr={formData.zcode_settings}
              validation={zcodeValidation}
              language={language}
            />
            <small className="text-muted">{t('zcodeSettingsHint', language)}</small>
          </div>
        )}
      </Modal>

      {/* Delete Confirm Dialog */}
      <Modal
        isOpen={showDeleteDialog}
        onClose={() => setShowDeleteDialog(false)}
        title={t('deleteApiKey', language)}
        footer={
          <>
            <Button variant="secondary" onClick={() => setShowDeleteDialog(false)}>
              {t('cancel', language)}
            </Button>
            <Button variant="danger" onClick={handleDelete} loading={deleteApiKey.isPending}>
              {t('delete', language)}
            </Button>
          </>
        }
      >
        <p>{t('deleteApiKeyConfirm', language)}</p>
        {deleteTarget && (
          <p>
            <Badge variant={providerBadgeVariant[deleteTarget.provider] || 'secondary'}>
              {deleteTarget.provider}
            </Badge>{' '}
            <strong>{deleteTarget.key_name}</strong>
          </p>
        )}
      </Modal>
    </div>
  );
};
