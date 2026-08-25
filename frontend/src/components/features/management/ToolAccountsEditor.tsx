/**
 * ToolAccountsEditor Component - Edit tool accounts for a user
 *
 * Issue #2761: Enhanced with:
 * - Status visualization (pending/active/stale/conflict)
 * - Message count display
 * - Advanced predeclared account creation with confirmation
 */

import React, { useState, useEffect, useCallback } from 'react';
import { useLanguage } from '@/store';
import { t } from '@/i18n';
import { Button, TextInput, Modal, Badge, useToast, useConfirm } from '@/components/common';
import {
  toolAccountsApi,
  type ToolAccount,
  type UnmappedAccount,
  type ToolType,
  type MappingStatus,
} from '@/api/toolAccounts';

interface ToolAccountsEditorProps {
  userId: number;
  onChange?: () => void;
}

/**
 * Get badge variant based on mapping status
 */
const getStatusBadgeVariant = (
  status?: MappingStatus | null
): 'success' | 'warning' | 'danger' | 'secondary' => {
  switch (status) {
    case 'active':
      return 'success';
    case 'pending':
      return 'warning';
    case 'stale':
      return 'secondary';
    case 'conflict_type':
    case 'conflict_owner':
    case 'conflict_tenant':
      return 'danger';
    default:
      return 'secondary';
  }
};

/**
 * Get status display text
 */
const getStatusDisplay = (status?: MappingStatus | null, language: string = 'en'): string => {
  switch (status) {
    case 'active':
      return language === 'zh' ? '活跃' : 'Active';
    case 'pending':
      return language === 'zh' ? '待激活' : 'Pending';
    case 'stale':
      return language === 'zh' ? '无活动' : 'Stale';
    case 'conflict_type':
      return language === 'zh' ? '类型冲突' : 'Type Conflict';
    case 'conflict_owner':
      return language === 'zh' ? '归属冲突' : 'Owner Conflict';
    case 'conflict_tenant':
      return language === 'zh' ? '租户冲突' : 'Tenant Conflict';
    default:
      return '';
  }
};

export const ToolAccountsEditor: React.FC<ToolAccountsEditorProps> = ({ userId, onChange }) => {
  const language = useLanguage();
  const [toolAccounts, setToolAccounts] = useState<ToolAccount[]>([]);
  const [unmappedAccounts, setUnmappedAccounts] = useState<UnmappedAccount[]>([]);
  const [toolTypes, setToolTypes] = useState<ToolType[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [showAddModal, setShowAddModal] = useState(false);
  const [newAccount, setNewAccount] = useState({
    tool_account: '',
    tool_type: '',
    description: '',
  });
  const [showUnmappedModal, setShowUnmappedModal] = useState(false);
  const [selectedUnmapped, setSelectedUnmapped] = useState<string[]>([]);
  const [isAdding, setIsAdding] = useState(false);
  const [addError, setAddError] = useState<string | null>(null);
  const [showPredeclaredConfirm, setShowPredeclaredConfirm] = useState(false);
  const toast = useToast();

  const loadData = useCallback(async () => {
    setIsLoading(true);
    try {
      const [accounts, unmapped, types] = await Promise.all([
        toolAccountsApi.getByUser(userId),
        toolAccountsApi.getUnmapped(),
        toolAccountsApi.getToolTypes(),
      ]);
      setToolAccounts(accounts);
      setUnmappedAccounts(unmapped);
      setToolTypes(types);
    } catch (err) {
      console.error('Failed to load tool accounts:', err);
    } finally {
      setIsLoading(false);
    }
  }, [userId]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  const handleAddAccount = async () => {
    setAddError(null);

    // Validate required fields
    if (!newAccount.tool_account.trim()) {
      setAddError(t('toolAccountRequired', language));
      return;
    }

    setIsAdding(true);
    try {
      // Issue #2761: Create as predeclared account with pending status
      await toolAccountsApi.create({
        user_id: userId,
        tool_account: newAccount.tool_account,
        tool_type: newAccount.tool_type || undefined,
        description: newAccount.description || undefined,
        mapping_source: 'predeclared',
        mapping_status: 'pending',
      });
      setNewAccount({ tool_account: '', tool_type: '', description: '' });
      setShowAddModal(false);
      setShowPredeclaredConfirm(false);
      loadData();
      onChange?.();
      toast.success(t('addToolAccountSuccess', language));
    } catch (err) {
      console.error('Failed to add tool account:', err);
      const errorMsg = err instanceof Error ? err.message : t('addToolAccountFailed', language);
      setAddError(errorMsg);
      toast.error(errorMsg);
    } finally {
      setIsAdding(false);
    }
  };

  const confirm = useConfirm();
  const handleDeleteAccount = async (id: number) => {
    if (!(await confirm({ message: t('confirmDelete', language), variant: 'danger' }))) return;

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
          tool_type: account?.tool_type ?? undefined,
        };
      });

      const result = await toolAccountsApi.batchCreate(userId, accounts);

      // Handle failures
      if (result.failed_count > 0) {
        const errorMessages = result.failed
          .map((f) => `${f.tool_account}: ${f.error}`)
          .join('\n');
        toast.error(
          language === 'zh'
            ? `映射失败 ${result.failed_count} 个账号：\n${errorMessages}`
            : `Failed to map ${result.failed_count} accounts:\n${errorMessages}`
        );
      }

      // Show success message only when there are successes
      if (result.created_count > 0) {
        toast.success(
          language === 'zh'
            ? `成功映射 ${result.created_count} 个账号`
            : `Successfully mapped ${result.created_count} accounts`
        );
      }

      setSelectedUnmapped([]);
      setShowUnmappedModal(false);
      loadData();
      onChange?.();
    } catch (err) {
      console.error('Failed to map accounts:', err);
      toast.error(language === 'zh' ? '映射操作失败' : 'Failed to map accounts');
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
      {/* Current tool accounts with status visualization */}
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
                {/* Issue #2761: Show status badge */}
                {account.mapping_status && (
                  <Badge
                    variant={getStatusBadgeVariant(account.mapping_status)}
                    className="me-1"
                    style={{ fontSize: '0.65rem' }}
                  >
                    {getStatusDisplay(account.mapping_status, language)}
                  </Badge>
                )}
                {account.tool_type_display ?? account.tool_type ?? ''}
                {account.tool_type ? ': ' : ''}
                {account.tool_account}
                {/* Issue #2761: Show message count */}
                {account.observed_message_count !== undefined &&
                  account.observed_message_count > 0 && (
                    <span className="ms-1 text-muted" style={{ fontSize: '0.65rem' }}>
                      ({account.observed_message_count})
                    </span>
                  )}
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
        {/* Issue #2761: Change "Add" to "Advanced Preconfigure" */}
        <Button
          variant="outline-secondary"
          onClick={() => {
            setAddError(null);
            setShowPredeclaredConfirm(true);
          }}
        >
          <i className="bi bi-gear me-1" />
          {language === 'zh' ? '高级预配置' : 'Advanced Preconfigure'}
        </Button>
        {unmappedAccounts.length > 0 && (
          <Button variant="outline-primary" onClick={() => setShowUnmappedModal(true)}>
            <i className="bi bi-link me-1" />
            {t('mapToUser', language)}
            <Badge variant="secondary" className="ms-1">
              {unmappedAccounts.length}
            </Badge>
          </Button>
        )}
      </div>

      {/* Issue #2761: Predeclared account confirmation modal */}
      <Modal
        isOpen={showPredeclaredConfirm}
        onClose={() => setShowPredeclaredConfirm(false)}
        title={language === 'zh' ? '高级预配置确认' : 'Advanced Preconfigure Confirmation'}
        size="md"
        footer={
          <>
            <Button variant="secondary" onClick={() => setShowPredeclaredConfirm(false)}>
              {t('cancel', language)}
            </Button>
            <Button
              variant="warning"
              onClick={() => {
                setShowPredeclaredConfirm(false);
                setShowAddModal(true);
              }}
            >
              {language === 'zh' ? '我了解，继续' : 'I Understand, Continue'}
            </Button>
          </>
        }
      >
        <div className="alert alert-warning">
          <strong>{language === 'zh' ? '重要提示：' : 'Important:'}</strong>
        </div>
        <ul className="mb-3">
          <li>
            {language === 'zh'
              ? '预配置账号当前未在数据中发现，需要等待采集数据到达后才会激活。'
              : 'The predeclared account is not currently found in the data. It will only activate when matching data arrives.'}
          </li>
          <li>
            {language === 'zh'
              ? '保存此配置不会创建 Linux 用户、工具进程或历史数据。'
              : 'Saving this configuration does NOT create a Linux user, tool process, or historical data.'}
          </li>
          <li>
            {language === 'zh'
              ? '只有未来收到完全相同的账号标识时才会生效。'
              : 'It will only take effect when an identical account identifier is received in the future.'}
          </li>
          <li>
            {language === 'zh'
              ? '错误的预配置可能导致数据归属错误。'
              : 'Incorrect preconfiguration may cause incorrect data attribution.'}
          </li>
        </ul>
        <p className="text-muted small">
          {language === 'zh'
            ? '建议：如果您不确定账号名称，请使用"映射到用户"按钮从已发现的未映射账号列表中选择。'
            : 'Recommendation: If you are unsure about the account name, use the "Map to User" button to select from the discovered unmapped accounts list.'}
        </p>
      </Modal>

      {/* Add account modal */}
      <Modal
        isOpen={showAddModal}
        onClose={() => setShowAddModal(false)}
        title={language === 'zh' ? '预配置工具账号' : 'Preconfigure Tool Account'}
        size="md"
        footer={
          <>
            <Button variant="secondary" onClick={() => setShowAddModal(false)}>
              {t('cancel', language)}
            </Button>
            <Button
              variant="primary"
              onClick={handleAddAccount}
              loading={isAdding}
              disabled={isAdding}
            >
              {t('save', language)}
            </Button>
          </>
        }
      >
        <form
          onSubmit={(e) => {
            e.preventDefault();
            handleAddAccount();
          }}
        >
          {addError && <div className="alert alert-danger mb-3">{addError}</div>}
          <div className="mb-3">
            <label className="form-label">{t('toolAccount', language)}</label>
            <TextInput
              value={newAccount.tool_account}
              onChange={(value) => setNewAccount({ ...newAccount, tool_account: value })}
              placeholder="e.g., rhuang-MacBook.local-qwen"
            />
          </div>
          <div className="mb-3">
            <label className="form-label">{t('toolType', language)}</label>
            <select
              className="form-select"
              value={newAccount.tool_type}
              onChange={(e) => setNewAccount({ ...newAccount, tool_type: e.target.value })}
            >
              <option value="">-- Select --</option>
              {toolTypes.map((type) => (
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
              onChange={(value) => setNewAccount({ ...newAccount, description: value })}
              placeholder="Optional description"
            />
          </div>
        </form>
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
          {language === 'zh'
            ? '选择要映射到此用户的已发现账号：'
            : 'Select discovered accounts to map to this user:'}
        </p>
        <div className="list-group" style={{ maxHeight: '400px', overflowY: 'auto' }}>
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
                <small className="text-muted">{account.message_count} msgs</small>
              </div>
            </label>
          ))}
        </div>
      </Modal>
    </div>
  );
};
