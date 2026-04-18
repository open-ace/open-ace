/**
 * APIKeyManagement Component - API key management page
 *
 * Features:
 * - API key list with provider badges
 * - Add API key dialog
 * - Delete API key confirmation
 */

import React, { useState } from 'react';
import { useApiKeys, useStoreApiKey, useDeleteApiKey } from '@/hooks';
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
  anthropic: 'danger',
  google: 'primary',
};

export const APIKeyManagement: React.FC = () => {
  const language = useLanguage();
  const { data: keysData, isLoading, isError, error, refetch } = useApiKeys();
  const storeApiKey = useStoreApiKey();
  const deleteApiKey = useDeleteApiKey();

  const keys = keysData?.keys ?? [];

  // Dialog states
  const [showAddDialog, setShowAddDialog] = useState(false);
  const [showDeleteDialog, setShowDeleteDialog] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<ApiKey | null>(null);
  const [formError, setFormError] = useState<string | null>(null);

  const [formData, setFormData] = useState({
    provider: 'openai',
    key_name: '',
    api_key: '',
    base_url: '',
  });

  const handleOpenAdd = () => {
    setFormError(null);
    setFormData({ provider: 'openai', key_name: '', api_key: '', base_url: '' });
    setShowAddDialog(true);
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

    try {
      await storeApiKey.mutateAsync({
        provider: formData.provider,
        key_name: formData.key_name,
        api_key: formData.api_key,
        base_url: formData.base_url || undefined,
      });
      setShowAddDialog(false);
    } catch (err: any) {
      const msg = err?.message ?? t('error', language);
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
                <th>{t('keyStatus', language)}</th>
                <th>{t('tableCreatedAt', language)}</th>
                <th>{t('tableActions', language)}</th>
              </tr>
            </thead>
            <tbody>
              {keys.map((key) => (
                <tr key={key.id}>
                  <td>
                    <Badge variant={providerBadgeVariant[key.provider] || 'secondary'}>
                      {key.provider}
                    </Badge>
                  </td>
                  <td>
                    <strong>{key.key_name}</strong>
                  </td>
                  <td className="text-muted">{key.base_url || '-'}</td>
                  <td>
                    <Badge variant={key.is_active ? 'success' : 'secondary'}>
                      {key.is_active ? t('active', language) : t('inactive', language)}
                    </Badge>
                  </td>
                  <td>{new Date(key.created_at).toLocaleDateString()}</td>
                  <td>
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
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Add API Key Dialog */}
      <Modal
        isOpen={showAddDialog}
        onClose={() => setShowAddDialog(false)}
        title={t('addApiKey', language)}
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
        <div>
          <label className="form-label">{t('baseUrl', language)}</label>
          <TextInput
            value={formData.base_url}
            onChange={(v) => setFormData({ ...formData, base_url: v })}
            placeholder={t('enterBaseUrl', language)}
          />
        </div>
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
