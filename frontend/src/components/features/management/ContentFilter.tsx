/**
 * ContentFilter Component - Content filter rule management
 */

import React, { useState } from 'react';
import {
  useFilterRules,
  useCreateFilterRule,
  useUpdateFilterRule,
  useDeleteFilterRule,
} from '@/hooks';
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
  Tooltip,
} from '@/components/common';
import type { ContentFilterRule, CreateFilterRuleRequest } from '@/api';

const TYPE_OPTIONS = [
  { value: 'keyword', label: 'Keyword' },
  { value: 'regex', label: 'Regex' },
  { value: 'pii', label: 'PII' },
];

const SEVERITY_OPTIONS = [
  { value: 'low', label: 'Low' },
  { value: 'medium', label: 'Medium' },
  { value: 'high', label: 'High' },
];

const ACTION_OPTIONS = [
  { value: 'warn', label: 'Warn' },
  { value: 'block', label: 'Block' },
  { value: 'redact', label: 'Redact' },
];

export const ContentFilter: React.FC = () => {
  const language = useLanguage();
  const { data: rules, isLoading, isError, error, refetch } = useFilterRules();
  const createRule = useCreateFilterRule();
  const updateRule = useUpdateFilterRule();
  const deleteRule = useDeleteFilterRule();

  const [showModal, setShowModal] = useState(false);
  const [editingRule, setEditingRule] = useState<ContentFilterRule | null>(null);
  const [formData, setFormData] = useState<CreateFilterRuleRequest>({
    pattern: '',
    type: 'keyword',
    severity: 'medium',
    action: 'warn',
    description: '',
    is_enabled: true,
  });

  const handleOpenCreate = () => {
    setEditingRule(null);
    setFormData({
      pattern: '',
      type: 'keyword',
      severity: 'medium',
      action: 'warn',
      description: '',
      is_enabled: true,
    });
    setShowModal(true);
  };

  const handleOpenEdit = (rule: ContentFilterRule) => {
    setEditingRule(rule);
    setFormData({
      pattern: rule.pattern,
      type: rule.type,
      severity: rule.severity,
      action: rule.action,
      description: rule.description ?? '',
      is_enabled: rule.is_enabled,
    });
    setShowModal(true);
  };

  const handleCloseModal = () => {
    setShowModal(false);
    setEditingRule(null);
  };

  const handleSubmit = async () => {
    try {
      if (editingRule) {
        await updateRule.mutateAsync({ ruleId: editingRule.id, data: formData });
      } else {
        await createRule.mutateAsync(formData);
      }
      handleCloseModal();
    } catch (err) {
      console.error('Failed to save rule:', err);
    }
  };

  const handleToggleEnabled = async (rule: ContentFilterRule) => {
    try {
      await updateRule.mutateAsync({
        ruleId: rule.id,
        data: { is_enabled: !rule.is_enabled },
      });
    } catch (err) {
      console.error('Failed to toggle rule:', err);
    }
  };

  const handleDelete = async (ruleId: number) => {
    if (window.confirm(t('confirmDeleteRule', language))) {
      try {
        await deleteRule.mutateAsync(ruleId);
      } catch (err) {
        console.error('Failed to delete rule:', err);
      }
    }
  };

  const getSeverityVariant = (severity: string) => {
    switch (severity) {
      case 'high':
        return 'danger';
      case 'medium':
        return 'warning';
      default:
        return 'info';
    }
  };

  const getActionVariant = (action: string) => {
    switch (action) {
      case 'block':
        return 'danger';
      case 'redact':
        return 'warning';
      default:
        return 'primary';
    }
  };

  if (isLoading) {
    return <Loading size="lg" text={t('loading', language)} />;
  }

  if (isError) {
    return <Error message={error?.message || t('error', language)} onRetry={() => refetch()} />;
  }

  return (
    <div className="content-filter">
      {/* Header */}
      <div className="d-flex justify-content-between align-items-center mb-3">
        <h5>{t('filterRules', language)}</h5>
        <Button variant="primary" size="sm" onClick={handleOpenCreate}>
          <i className="bi bi-plus-lg me-1" />
          {t('addRule', language)}
        </Button>
      </div>

      {/* Rules Table */}
      {!rules || rules.length === 0 ? (
        <EmptyState icon="bi-shield-check" title={t('noFilterRules', language)} />
      ) : (
        <div className="table-responsive">
          <table className="table table-hover">
            <thead>
              <tr>
                <th>
                  {t('tablePattern', language)}
                  <Tooltip content={t('patternHelp', language)} placement="top">
                    <i className="bi bi-question-circle text-muted ms-1" style={{ cursor: 'pointer' }} />
                  </Tooltip>
                </th>
                <th>
                  {t('tableType', language)}
                  <Tooltip content={`${t('keywordTypeHelp', language)}\n${t('regexTypeHelp', language)}\n${t('piiTypeHelp', language)}`} placement="top">
                    <i className="bi bi-question-circle text-muted ms-1" style={{ cursor: 'pointer' }} />
                  </Tooltip>
                </th>
                <th>{t('tableSeverity', language)}</th>
                <th>
                  {t('tableAction', language)}
                  <Tooltip content={`${t('warnActionHelp', language)}\n${t('blockActionHelp', language)}\n${t('redactActionHelp', language)}`} placement="top">
                    <i className="bi bi-question-circle text-muted ms-1" style={{ cursor: 'pointer' }} />
                  </Tooltip>
                </th>
                <th>{t('tableStatus', language)}</th>
                <th>{t('tableActions', language)}</th>
              </tr>
            </thead>
            <tbody>
              {rules.map((rule) => (
                <tr key={rule.id}>
                  <td>
                    <code>{rule.pattern}</code>
                    {rule.description && (
                      <small className="d-block text-muted">{rule.description}</small>
                    )}
                  </td>
                  <td>
                    <Badge variant="secondary">{rule.type}</Badge>
                  </td>
                  <td>
                    <Badge variant={getSeverityVariant(rule.severity)}>{rule.severity}</Badge>
                  </td>
                  <td>
                    <Badge variant={getActionVariant(rule.action)}>{rule.action}</Badge>
                  </td>
                  <td>
                    <div className="form-check form-switch">
                      <input
                        className="form-check-input"
                        type="checkbox"
                        checked={rule.is_enabled}
                        onChange={() => handleToggleEnabled(rule)}
                      />
                    </div>
                  </td>
                  <td>
                    <div className="btn-group btn-group-sm">
                      <Button
                        variant="outline-primary"
                        size="sm"
                        onClick={() => handleOpenEdit(rule)}
                      >
                        <i className="bi bi-pencil" />
                      </Button>
                      <Button
                        variant="outline-danger"
                        size="sm"
                        onClick={() => handleDelete(rule.id)}
                        disabled={deleteRule.isPending}
                      >
                        <i className="bi bi-trash" />
                      </Button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Create/Edit Modal */}
      <Modal
        isOpen={showModal}
        onClose={handleCloseModal}
        title={editingRule ? t('editRule', language) : t('addRule', language)}
        size="md"
        footer={
          <>
            <Button variant="secondary" onClick={handleCloseModal}>
              {t('cancel', language)}
            </Button>
            <Button
              variant="primary"
              onClick={handleSubmit}
              loading={createRule.isPending || updateRule.isPending}
            >
              {t('save', language)}
            </Button>
          </>
        }
      >
        <form
          onSubmit={(e) => {
            e.preventDefault();
            handleSubmit();
          }}
        >
          <div className="row g-3">
            <div className="col-12">
              <label className="form-label">{t('tablePattern', language)}</label>
              <TextInput
                value={formData.pattern}
                onChange={(value: string) => setFormData({ ...formData, pattern: value })}
                placeholder={t('enterPattern', language)}
              />
              <small className="text-muted">{t('patternHelp', language)}</small>
            </div>
            <div className="col-md-4">
              <label className="form-label">{t('tableType', language)}</label>
              <Select
                options={TYPE_OPTIONS}
                value={formData.type}
                onChange={(value) =>
                  setFormData({ ...formData, type: value as CreateFilterRuleRequest['type'] })
                }
              />
              <small className="text-muted">
                {formData.type === 'keyword' && t('keywordTypeHelp', language)}
                {formData.type === 'regex' && t('regexTypeHelp', language)}
                {formData.type === 'pii' && t('piiTypeHelp', language)}
              </small>
            </div>
            <div className="col-md-4">
              <label className="form-label">{t('tableSeverity', language)}</label>
              <Select
                options={SEVERITY_OPTIONS}
                value={formData.severity}
                onChange={(value) =>
                  setFormData({
                    ...formData,
                    severity: value as CreateFilterRuleRequest['severity'],
                  })
                }
              />
            </div>
            <div className="col-md-4">
              <label className="form-label">{t('tableAction', language)}</label>
              <Select
                options={ACTION_OPTIONS}
                value={formData.action}
                onChange={(value) =>
                  setFormData({ ...formData, action: value as CreateFilterRuleRequest['action'] })
                }
              />
              <small className="text-muted">
                {formData.action === 'warn' && t('warnActionHelp', language)}
                {formData.action === 'block' && t('blockActionHelp', language)}
                {formData.action === 'redact' && t('redactActionHelp', language)}
              </small>
            </div>
            <div className="col-12">
              <label className="form-label">{t('description', language)}</label>
              <TextInput
                value={formData.description ?? ''}
                onChange={(value: string) => setFormData({ ...formData, description: value })}
                placeholder={t('enterDescription', language)}
              />
            </div>
            <div className="col-12">
              <div className="form-check">
                <input
                  className="form-check-input"
                  type="checkbox"
                  checked={formData.is_enabled}
                  onChange={(e) => setFormData({ ...formData, is_enabled: e.target.checked })}
                  id="ruleEnabled"
                />
                <label className="form-check-label" htmlFor="ruleEnabled">
                  {t('enabled', language)}
                </label>
              </div>
            </div>
          </div>
        </form>
      </Modal>
    </div>
  );
};
