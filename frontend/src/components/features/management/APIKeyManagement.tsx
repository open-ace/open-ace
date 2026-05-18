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
import { useApiKeys, useStoreApiKey, useUpdateApiKey, useDeleteApiKey } from '@/hooks';
import { useLanguage } from '@/store';
import { t } from '@/i18n';
import {
  Button,
  Modal,
  TextInput,
  Select,
  Loading,
  Error,
  EmptyState,
  Badge,
} from '@/components/common';
import type { BadgeVariant } from '@/components/common';
import type { ApiKey } from '@/api';

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

export const APIKeyManagement: React.FC = () => {
  const language = useLanguage();
  const { data: keysData, isLoading, isError, error, refetch } = useApiKeys();
  const storeApiKey = useStoreApiKey();
  const updateApiKey = useUpdateApiKey();
  const deleteApiKey = useDeleteApiKey();

  const keys = keysData?.keys ?? [];

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
  });

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

      if (tool === 'claude-code' && !formData.claude_settings) {
        newClaudeSettings = defaultClaudeSettings;
      }
      if (tool === 'qwen-code' && !formData.qwen_settings) {
        newQwenSettings = defaultQwenSettings;
      }

      setFormData({
        ...formData,
        cli_tools: [...currentTools, tool],
        claude_settings: newClaudeSettings,
        qwen_settings: newQwenSettings,
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

  const buildCliSettingsJson = (): string => {
    const settings: Record<string, unknown> = {};
    if (formData.cli_tools.includes('claude-code') && formData.claude_settings) {
      try {
        settings['claude-code'] = JSON.parse(formData.claude_settings);
      } catch {
        // Skip invalid JSON
      }
    }
    if (formData.cli_tools.includes('qwen-code') && formData.qwen_settings) {
      try {
        settings['qwen-code'] = JSON.parse(formData.qwen_settings);
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
      setFormError('Claude Code settings JSON is invalid');
      return;
    }
    if (formData.cli_tools.includes('qwen-code') && !validateJsonSettings(formData.qwen_settings)) {
      setFormError('Qwen Code settings JSON is invalid');
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
      setFormError('Claude Code settings JSON is invalid');
      return;
    }
    if (formData.cli_tools.includes('qwen-code') && !validateJsonSettings(formData.qwen_settings)) {
      setFormError('Qwen Code settings JSON is invalid');
      return;
    }

    try {
      await updateApiKey.mutateAsync({
        keyId: editTarget.id,
        key_name: formData.key_name,
        base_url: formData.base_url || undefined,
        cli_tools: JSON.stringify(formData.cli_tools),
        cli_settings: buildCliSettingsJson(),
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
        <Button variant="primary" size="sm" onClick={handleOpenAdd}>
          <i className="bi bi-plus-lg me-1" />
          {t('addApiKey', language)}
        </Button>
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
                <th>{t('baseUrl', language)}</th>
                <th>{t('cliTools', language)}</th>
                <th>{t('keyStatus', language)}</th>
                <th>{t('tableCreatedAt', language)}</th>
                <th>{t('tableActions', language)}</th>
              </tr>
            </thead>
            <tbody>
              {keys.map((key) => {
                const cliTools = parsedCliTools.get(key.id) ?? [];
                return (
                  <tr key={key.id}>
                    <td>
                      <Badge variant={providerBadgeVariant[key.provider] || 'secondary'}>
                        {key.provider}
                      </Badge>
                    </td>
                    <td>
                      <strong>{key.key_name}</strong>
                    </td>
                    <td className="text-muted">{key.base_url ?? '-'}</td>
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
                      <Badge variant={key.is_active ? 'success' : 'secondary'}>
                        {key.is_active ? t('active', language) : t('inactive', language)}
                      </Badge>
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
              className="form-control"
              rows={8}
              value={formData.claude_settings}
              onChange={(e) => setFormData({ ...formData, claude_settings: e.target.value })}
              placeholder={defaultClaudeSettings}
            />
            <small className="text-muted">{t('claudeCodeSettingsHint', language)}</small>
          </div>
        )}

        {/* Qwen Code Settings */}
        {formData.cli_tools.includes('qwen-code') && (
          <div className="mb-3">
            <label className="form-label">{t('qwenCodeSettings', language)}</label>
            <textarea
              className="form-control"
              rows={10}
              value={formData.qwen_settings}
              onChange={(e) => setFormData({ ...formData, qwen_settings: e.target.value })}
              placeholder={defaultQwenSettings}
            />
            <small className="text-muted">{t('qwenCodeSettingsHint', language)}</small>
          </div>
        )}
      </Modal>

      {/* Edit API Key Dialog */}
      <Modal
        isOpen={showEditDialog}
        onClose={() => setShowEditDialog(false)}
        title={t('editApiKey', language) || 'Edit API Key'}
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
              className="form-control"
              rows={8}
              value={formData.claude_settings}
              onChange={(e) => setFormData({ ...formData, claude_settings: e.target.value })}
            />
            <small className="text-muted">{t('claudeCodeSettingsHint', language)}</small>
          </div>
        )}

        {/* Qwen Code Settings */}
        {formData.cli_tools.includes('qwen-code') && (
          <div className="mb-3">
            <label className="form-label">{t('qwenCodeSettings', language)}</label>
            <textarea
              className="form-control"
              rows={10}
              value={formData.qwen_settings}
              onChange={(e) => setFormData({ ...formData, qwen_settings: e.target.value })}
            />
            <small className="text-muted">{t('qwenCodeSettingsHint', language)}</small>
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
