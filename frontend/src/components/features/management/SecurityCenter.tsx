/**
 * SecurityCenter Component - Combined Content Filter and Security Settings
 *
 * Features:
 * - Tab navigation between Filter Rules and Security Settings views
 * - Filter Rules: Manage content filtering rules
 * - Security Settings: Configure security policies
 */

import React, { useState } from 'react';
import { cn } from '@/utils';
import {
  useFilterRules,
  useCreateFilterRule,
  useUpdateFilterRule,
  useDeleteFilterRule,
  useSecuritySettings,
  useUpdateSecuritySettings,
} from '@/hooks';
import { useLanguage } from '@/store';
import { t } from '@/i18n';
import {
  Card,
  Button,
  Modal,
  TextInput,
  Select,
  Loading,
  Error,
  EmptyState,
  Badge,
} from '@/components/common';
import { useToast } from '@/components/common';
import { FilterRuleTableHeader } from './FilterRuleTableHeader';
import type {
  ContentFilterRule,
  CreateFilterRuleRequest,
  SecuritySettings as SecuritySettingsType,
} from '@/api';

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

type TabType = 'filter' | 'settings';

export const SecurityCenter: React.FC = () => {
  const language = useLanguage();
  const toast = useToast();
  const [activeTab, setActiveTab] = useState<TabType>('filter');

  // --- Filter Rules State ---
  const {
    data: rules,
    isLoading: rulesLoading,
    isError: rulesError,
    error: rulesErrorMsg,
    refetch: refetchRules,
  } = useFilterRules();
  const createRule = useCreateFilterRule();
  const updateRule = useUpdateFilterRule();
  const deleteRule = useDeleteFilterRule();

  const [showRuleModal, setShowRuleModal] = useState(false);
  const [editingRule, setEditingRule] = useState<ContentFilterRule | null>(null);
  const [ruleFormData, setRuleFormData] = useState<CreateFilterRuleRequest>({
    pattern: '',
    type: 'keyword',
    severity: 'medium',
    action: 'warn',
    description: '',
    is_enabled: true,
  });

  // --- Security Settings State ---
  const {
    data: settings,
    isLoading: settingsLoading,
    isError: settingsError,
    error: settingsErrorMsg,
    refetch: refetchSettings,
  } = useSecuritySettings();
  const updateSettings = useUpdateSecuritySettings();

  const [settingsFormData, setSettingsFormData] = useState<Partial<SecuritySettingsType>>({});

  // --- Filter Rules Handlers ---
  const handleOpenCreateRule = () => {
    setEditingRule(null);
    setRuleFormData({
      pattern: '',
      type: 'keyword',
      severity: 'medium',
      action: 'warn',
      description: '',
      is_enabled: true,
    });
    setShowRuleModal(true);
  };

  const handleOpenEditRule = (rule: ContentFilterRule) => {
    setEditingRule(rule);
    setRuleFormData({
      pattern: rule.pattern,
      type: rule.type,
      severity: rule.severity,
      action: rule.action,
      description: rule.description ?? '',
      is_enabled: rule.is_enabled,
    });
    setShowRuleModal(true);
  };

  const handleCloseRuleModal = () => {
    setShowRuleModal(false);
    setEditingRule(null);
  };

  const handleSubmitRule = async () => {
    try {
      if (editingRule) {
        await updateRule.mutateAsync({ ruleId: editingRule.id, data: ruleFormData });
      } else {
        await createRule.mutateAsync(ruleFormData);
      }
      handleCloseRuleModal();
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

  const handleDeleteRule = async (ruleId: number) => {
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

  // --- Security Settings Handlers ---
  const handleSettingsInputChange = (key: keyof SecuritySettingsType, value: unknown) => {
    setSettingsFormData((prev) => ({ ...prev, [key]: value }));
  };

  const handleSaveSettings = async () => {
    try {
      await updateSettings.mutateAsync(settingsFormData);
      toast.success(t('settingsSaved', language));
      setSettingsFormData({});
    } catch (err) {
      console.error('Failed to save settings:', err);
      toast.error(t('error', language));
    }
  };

  const handleResetSettings = () => {
    setSettingsFormData({});
  };

  // Merge current settings with form changes
  const currentSettings: SecuritySettingsType = {
    session_timeout: 30,
    max_login_attempts: 5,
    password_min_length: 8,
    password_require_uppercase: true,
    password_require_lowercase: true,
    password_require_number: true,
    password_require_special: false,
    two_factor_enabled: false,
    ip_whitelist: [],
    ...settings,
    ...settingsFormData,
  };

  // --- Render Filter Tab ---
  const renderFilterTab = () => {
    if (rulesLoading) {
      return <Loading size="lg" text={t('loading', language)} />;
    }

    if (rulesError) {
      return (
        <Error
          message={rulesErrorMsg?.message || t('error', language)}
          onRetry={() => refetchRules()}
        />
      );
    }

    return (
      <>
        {/* Rules Table */}
        {!rules || rules.length === 0 ? (
          <EmptyState icon="bi-shield-check" title={t('noFilterRules', language)} />
        ) : (
          <div className="table-responsive">
            <table className="table table-hover">
              <FilterRuleTableHeader />
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
                          onClick={() => handleOpenEditRule(rule)}
                        >
                          <i className="bi bi-pencil" />
                        </Button>
                        <Button
                          variant="outline-danger"
                          size="sm"
                          onClick={() => handleDeleteRule(rule.id)}
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
          isOpen={showRuleModal}
          onClose={handleCloseRuleModal}
          title={editingRule ? t('editRule', language) : t('addRule', language)}
          size="md"
          footer={
            <>
              <Button variant="secondary" onClick={handleCloseRuleModal}>
                {t('cancel', language)}
              </Button>
              <Button
                variant="primary"
                onClick={handleSubmitRule}
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
              handleSubmitRule();
            }}
          >
            <div className="row g-3">
              <div className="col-12">
                <label className="form-label">{t('tablePattern', language)}</label>
                <TextInput
                  value={ruleFormData.pattern}
                  onChange={(value: string) => setRuleFormData({ ...ruleFormData, pattern: value })}
                  placeholder={t('enterPattern', language)}
                />
                <small className="text-muted">{t('patternHelp', language)}</small>
              </div>
              <div className="col-md-4">
                <label className="form-label">{t('tableType', language)}</label>
                <Select
                  options={TYPE_OPTIONS}
                  value={ruleFormData.type}
                  onChange={(value) =>
                    setRuleFormData({
                      ...ruleFormData,
                      type: value as CreateFilterRuleRequest['type'],
                    })
                  }
                />
                <small className="text-muted">
                  {ruleFormData.type === 'keyword' && t('keywordTypeHelp', language)}
                  {ruleFormData.type === 'regex' && t('regexTypeHelp', language)}
                  {ruleFormData.type === 'pii' && t('piiTypeHelp', language)}
                </small>
              </div>
              <div className="col-md-4">
                <label className="form-label">{t('tableSeverity', language)}</label>
                <Select
                  options={SEVERITY_OPTIONS}
                  value={ruleFormData.severity}
                  onChange={(value) =>
                    setRuleFormData({
                      ...ruleFormData,
                      severity: value as CreateFilterRuleRequest['severity'],
                    })
                  }
                />
              </div>
              <div className="col-md-4">
                <label className="form-label">{t('tableAction', language)}</label>
                <Select
                  options={ACTION_OPTIONS}
                  value={ruleFormData.action}
                  onChange={(value) =>
                    setRuleFormData({
                      ...ruleFormData,
                      action: value as CreateFilterRuleRequest['action'],
                    })
                  }
                />
                <small className="text-muted">
                  {ruleFormData.action === 'warn' && t('warnActionHelp', language)}
                  {ruleFormData.action === 'block' && t('blockActionHelp', language)}
                  {ruleFormData.action === 'redact' && t('redactActionHelp', language)}
                </small>
              </div>
              <div className="col-12">
                <label className="form-label">{t('description', language)}</label>
                <TextInput
                  value={ruleFormData.description ?? ''}
                  onChange={(value: string) =>
                    setRuleFormData({ ...ruleFormData, description: value })
                  }
                  placeholder={t('enterDescription', language)}
                />
              </div>
              <div className="col-12">
                <div className="form-check">
                  <input
                    className="form-check-input"
                    type="checkbox"
                    checked={ruleFormData.is_enabled}
                    onChange={(e) =>
                      setRuleFormData({ ...ruleFormData, is_enabled: e.target.checked })
                    }
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
      </>
    );
  };

  // --- Render Settings Tab ---
  const renderSettingsTab = () => {
    if (settingsLoading) {
      return <Loading size="lg" text={t('loading', language)} />;
    }

    if (settingsError) {
      return (
        <Error
          message={settingsErrorMsg?.message || t('error', language)}
          onRetry={() => refetchSettings()}
        />
      );
    }

    return (
      <>
        {/* Session Settings */}
        <Card title={t('sessionSettings', language)} className="mb-4">
          <div className="row g-3">
            <div className="col-md-6">
              <label className="form-label">
                {t('sessionTimeout', language)} ({t('minutes', language)})
              </label>
              <TextInput
                type="number"
                value={currentSettings.session_timeout.toString()}
                onChange={(value: string) =>
                  handleSettingsInputChange('session_timeout', parseInt(value) || 30)
                }
              />
              <small className="text-muted">{t('sessionTimeoutHelp', language)}</small>
            </div>
            <div className="col-md-6">
              <label className="form-label">{t('maxLoginAttempts', language)}</label>
              <TextInput
                type="number"
                value={currentSettings.max_login_attempts.toString()}
                onChange={(value: string) =>
                  handleSettingsInputChange('max_login_attempts', parseInt(value) || 5)
                }
              />
              <small className="text-muted">{t('maxLoginAttemptsHelp', language)}</small>
            </div>
          </div>
        </Card>

        {/* Password Policy */}
        <Card title={t('passwordPolicy', language)} className="mb-4">
          <div className="row g-3">
            <div className="col-md-6">
              <label className="form-label">{t('passwordMinLength', language)}</label>
              <TextInput
                type="number"
                value={currentSettings.password_min_length.toString()}
                onChange={(value: string) =>
                  handleSettingsInputChange('password_min_length', parseInt(value) || 8)
                }
              />
            </div>
            <div className="col-md-6">
              <label className="form-label">{t('passwordRequirements', language)}</label>
              <div className="d-flex flex-column gap-2 mt-2">
                <div className="form-check">
                  <input
                    className="form-check-input"
                    type="checkbox"
                    checked={currentSettings.password_require_uppercase}
                    onChange={(e) =>
                      handleSettingsInputChange('password_require_uppercase', e.target.checked)
                    }
                    id="requireUppercase"
                  />
                  <label className="form-check-label" htmlFor="requireUppercase">
                    {t('requireUppercase', language)}
                  </label>
                </div>
                <div className="form-check">
                  <input
                    className="form-check-input"
                    type="checkbox"
                    checked={currentSettings.password_require_lowercase}
                    onChange={(e) =>
                      handleSettingsInputChange('password_require_lowercase', e.target.checked)
                    }
                    id="requireLowercase"
                  />
                  <label className="form-check-label" htmlFor="requireLowercase">
                    {t('requireLowercase', language)}
                  </label>
                </div>
                <div className="form-check">
                  <input
                    className="form-check-input"
                    type="checkbox"
                    checked={currentSettings.password_require_number}
                    onChange={(e) =>
                      handleSettingsInputChange('password_require_number', e.target.checked)
                    }
                    id="requireNumber"
                  />
                  <label className="form-check-label" htmlFor="requireNumber">
                    {t('requireNumber', language)}
                  </label>
                </div>
                <div className="form-check">
                  <input
                    className="form-check-input"
                    type="checkbox"
                    checked={currentSettings.password_require_special}
                    onChange={(e) =>
                      handleSettingsInputChange('password_require_special', e.target.checked)
                    }
                    id="requireSpecial"
                  />
                  <label className="form-check-label" htmlFor="requireSpecial">
                    {t('requireSpecial', language)}
                  </label>
                </div>
              </div>
            </div>
          </div>
        </Card>

        {/* Two-Factor Authentication */}
        <Card title={t('twoFactorAuth', language)} className="mb-4">
          <div className="form-check form-switch">
            <input
              className="form-check-input"
              type="checkbox"
              checked={currentSettings.two_factor_enabled}
              onChange={(e) => handleSettingsInputChange('two_factor_enabled', e.target.checked)}
              id="twoFactorEnabled"
            />
            <label className="form-check-label" htmlFor="twoFactorEnabled">
              {t('enableTwoFactor', language)}
            </label>
          </div>
          <small className="text-muted">{t('twoFactorHelp', language)}</small>
        </Card>

        {/* IP Whitelist */}
        <Card title={t('ipWhitelist', language)} className="mb-4">
          <div className="mb-3">
            <label className="form-label">{t('allowedIpAddresses', language)}</label>
            <textarea
              className="form-control"
              rows={4}
              value={(currentSettings.ip_whitelist || []).join('\n')}
              onChange={(e) =>
                handleSettingsInputChange(
                  'ip_whitelist',
                  e.target.value.split('\n').filter((ip) => ip.trim())
                )
              }
              placeholder={'192.168.1.1\n10.0.0.0/24'}
            />
            <small className="text-muted">{t('ipWhitelistHelp', language)}</small>
          </div>
        </Card>

        {/* Save/Reset Buttons */}
        <div className="d-flex gap-2 justify-content-end">
          <Button variant="secondary" onClick={handleResetSettings}>
            {t('reset', language)}
          </Button>
          <Button variant="primary" onClick={handleSaveSettings} loading={updateSettings.isPending}>
            {t('save', language)}
          </Button>
        </div>
      </>
    );
  };

  return (
    <div className="security-center">
      {/* Header */}
      <div className="d-flex justify-content-between align-items-center mb-3">
        <h2>{t('securityCenter', language)}</h2>
        {activeTab === 'filter' && (
          <Button variant="primary" size="sm" onClick={handleOpenCreateRule}>
            <i className="bi bi-plus-lg me-1" />
            {t('addRule', language)}
          </Button>
        )}
      </div>

      {/* Tab Navigation */}
      <ul className="nav nav-tabs mb-3">
        <li className="nav-item">
          <button
            className={cn('nav-link', activeTab === 'filter' && 'active')}
            onClick={() => setActiveTab('filter')}
          >
            <i className="bi bi-shield-check me-1" />
            {t('contentFilter', language)}
          </button>
        </li>
        <li className="nav-item">
          <button
            className={cn('nav-link', activeTab === 'settings' && 'active')}
            onClick={() => setActiveTab('settings')}
          >
            <i className="bi bi-gear me-1" />
            {t('securitySettings', language)}
          </button>
        </li>
      </ul>

      {/* Tab Content */}
      {activeTab === 'filter' ? renderFilterTab() : renderSettingsTab()}
    </div>
  );
};
