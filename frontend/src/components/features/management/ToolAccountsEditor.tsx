/**
 * ToolAccountsEditor Component - Edit tool accounts for a user
 */

import React, { useState, useEffect } from 'react';
import { useLanguage } from '@/store';
import { t } from '@/i18n';
import { Button, TextInput, Modal, Badge } from '@/components/common';
import { toolAccountsApi, type ToolAccount, type UnmappedAccount } from '@/api/toolAccounts';

// Hardcoded tool types (matches backend TOOL_TYPES)
const TOOL_TYPES = [
  { value: 'qwen', display: 'Qwen' },
  { value: 'claude', display: 'Claude' },
  { value: 'openclaw', display: 'Openclaw' },
  { value: 'feishu', display: '飞书' },
  { value: 'slack', display: 'Slack' },
  { value: 'other', display: '其他' },
];

interface ToolAccountsEditorProps {
  userId: number;
  onChange?: () => void;
}

export const ToolAccountsEditor: React.FC<ToolAccountsEditorProps> = ({
  userId,
  onChange,
}) => {
  const language = useLanguage();
  const [toolAccounts, setToolAccounts] = useState<ToolAccount[]>([]);
  const [unmappedAccounts, setUnmappedAccounts] = useState<UnmappedAccount[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [showAddModal, setShowAddModal] = useState(false);
  const [newAccount, setNewAccount] = useState({
    tool_account: '',
    tool_type: '',
    description: '',
  });
  const [showUnmappedModal, setShowUnmappedModal] = useState(false);
  const [selectedUnmapped, setSelectedUnmapped] = useState<string[]>([]);

  useEffect(() => {
    loadData();
  }, [userId]);

  const loadData = async () => {
    setIsLoading(true);
    try {
      const [accounts, unmapped] = await Promise.all([
        toolAccountsApi.getByUser(userId),
        toolAccountsApi.getUnmapped(),
      ]);
      setToolAccounts(accounts);
      setUnmappedAccounts(unmapped);
    } catch (err) {
      console.error('Failed to load tool accounts:', err);
    } finally {
      setIsLoading(false);
    }
  };

  const handleAddAccount = async () => {
    if (!newAccount.tool_account.trim()) return;

    try {
      await toolAccountsApi.create({
        user_id: userId,
        tool_account: newAccount.tool_account,
        tool_type: newAccount.tool_type || undefined,
        description: newAccount.description || undefined,
      });
      setNewAccount({ tool_account: '', tool_type: '', description: '' });
      setShowAddModal(false);
      loadData();
      onChange?.();
    } catch (err) {
      console.error('Failed to add tool account:', err);
    }
  };

  const handleDeleteAccount = async (id: number) => {
    if (!window.confirm(t('confirmDelete', language))) return;

    try {
      await toolAccountsApi.delete(id);
      loadData();
      onChange?.();
    } catch (err) {
      console.error('Failed to delete tool account:', err);
    }
  };

  const handleMapUnmapped = async () => {
    if (selectedUnmapped.length === 0) return;

    try {
      const accounts = selectedUnmapped.map((name) => {
        const account = unmappedAccounts.find((a) => a.sender_name === name);
        return {
          tool_account: name,
          tool_type: account?.tool_type || undefined,
        };
      });

      await toolAccountsApi.batchCreate(userId, accounts);
      setSelectedUnmapped([]);
      setShowUnmappedModal(false);
      loadData();
      onChange?.();
    } catch (err) {
      console.error('Failed to map accounts:', err);
    }
  };

  const toggleUnmappedSelection = (name: string) => {
    setSelectedUnmapped((prev) =>
      prev.includes(name) ? prev.filter((n) => n !== name) : [...prev, name]
    );
  };

  if (isLoading) {
    return <div className="text-muted small">{t('loading', language)}</div>;
  }

  return (
    <div className="tool-accounts-editor">
      {/* Current tool accounts */}
      <div className="mb-2">
        <div className="d-flex align-items-center gap-2 flex-wrap">
          {toolAccounts.length === 0 ? (
            <span className="text-muted small">{t('noToolAccounts', language)}</span>
          ) : (
            toolAccounts.map((account) => (
              <Badge
                key={account.id}
                variant="secondary"
                className="d-flex align-items-center gap-1"
              >
                {account.tool_type_display || account.tool_type || ''}
                {account.tool_type ? ': ' : ''}
                {account.tool_account}
                <button
                  type="button"
                  className="btn-close btn-close-white ms-1"
                  style={{ fontSize: '0.6rem' }}
                  onClick={() => handleDeleteAccount(account.id)}
                  title={t('delete', language)}
                />
              </Badge>
            ))
          )}
        </div>
      </div>

      {/* Action buttons */}
      <div className="d-flex gap-2">
        <Button
          variant="outline-primary"
          size="sm"
          onClick={() => setShowAddModal(true)}
        >
          <i className="bi bi-plus-lg me-1" />
          {t('addToolAccount', language)}
        </Button>
        {unmappedAccounts.length > 0 && (
          <Button
            variant="outline-secondary"
            size="sm"
            onClick={() => setShowUnmappedModal(true)}
          >
            <i className="bi bi-link me-1" />
            {t('mapToUser', language)}
            <Badge variant="secondary" className="ms-1">
              {unmappedAccounts.length}
            </Badge>
          </Button>
        )}
      </div>

      {/* Add account modal */}
      <Modal
        isOpen={showAddModal}
        onClose={() => setShowAddModal(false)}
        title={t('addToolAccount', language)}
        size="md"
        footer={
          <>
            <Button variant="secondary" onClick={() => setShowAddModal(false)}>
              {t('cancel', language)}
            </Button>
            <Button variant="primary" onClick={handleAddAccount}>
              {t('save', language)}
            </Button>
          </>
        }
      >
        <div className="mb-3">
          <label className="form-label">{t('toolAccount', language)}</label>
          <TextInput
            value={newAccount.tool_account}
            onChange={(value) =>
              setNewAccount({ ...newAccount, tool_account: value })
            }
            placeholder="e.g., rhuang-MacBook.local-qwen"
          />
        </div>
        <div className="mb-3">
          <label className="form-label">{t('toolType', language)}</label>
          <select
            className="form-select"
            value={newAccount.tool_type}
            onChange={(e) =>
              setNewAccount({ ...newAccount, tool_type: e.target.value })
            }
          >
            <option value="">-- Select --</option>
            {TOOL_TYPES.map((type) => (
              <option key={type.value} value={type.value}>
                {type.display}
              </option>
            ))}
          </select>
        </div>
        <div className="mb-3">
          <label className="form-label">{t('description', language)}</label>
          <TextInput
            value={newAccount.description}
            onChange={(value) =>
              setNewAccount({ ...newAccount, description: value })
            }
            placeholder="Optional description"
          />
        </div>
      </Modal>

      {/* Unmapped accounts modal */}
      <Modal
        isOpen={showUnmappedModal}
        onClose={() => setShowUnmappedModal(false)}
        title={t('unmappedAccounts', language)}
        size="lg"
        footer={
          <>
            <Button variant="secondary" onClick={() => setShowUnmappedModal(false)}>
              {t('cancel', language)}
            </Button>
            <Button
              variant="primary"
              onClick={handleMapUnmapped}
              disabled={selectedUnmapped.length === 0}
            >
              {t('mapToUser', language)} ({selectedUnmapped.length})
            </Button>
          </>
        }
      >
        <p className="text-muted small mb-3">
          Select accounts to map to this user:
        </p>
        <div
          className="list-group"
          style={{ maxHeight: '400px', overflowY: 'auto' }}
        >
          {unmappedAccounts.map((account) => (
            <label
              key={account.sender_name}
              className="list-group-item list-group-item-action d-flex justify-content-between align-items-center"
            >
              <div className="form-check">
                <input
                  type="checkbox"
                  className="form-check-input me-2"
                  checked={selectedUnmapped.includes(account.sender_name)}
                  onChange={() => toggleUnmappedSelection(account.sender_name)}
                />
                <span>{account.sender_name}</span>
              </div>
              <div>
                {account.tool_type_display && (
                  <Badge variant="secondary" className="me-2">
                    {account.tool_type_display}
                  </Badge>
                )}
                <small className="text-muted">
                  {account.message_count} msgs
                </small>
              </div>
            </label>
          ))}
        </div>
      </Modal>
    </div>
  );
};