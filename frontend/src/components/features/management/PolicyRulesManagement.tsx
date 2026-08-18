/**
 * PolicyRulesManagement Component - Central Policy & Approval Engine management UI.
 *
 * Features:
 * - List current policy rules with enabled/current-version state
 * - Create new rules with validation
 * - Edit rules (creates new version)
 * - Enable/disable rules
 * - View policy decisions by session ID
 *
 * All operations require admin role and policy.enabled=true.
 */

import React, { useState, useEffect } from 'react';
import { cn, createMatcherConfig } from '@/utils';
import {
  usePolicyRules,
  useCreatePolicyRule,
  useUpdatePolicyRule,
  useTogglePolicyRule,
  usePolicyDecisions,
  usePageRefresh,
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
  PageRefreshControl,
} from '@/components/common';
import { useToast, useConfirm } from '@/components/common';
import type {
  PolicyRule,
  CreatePolicyRuleRequest,
  PolicyType,
  PolicyEffect,
  PatternType,
  PolicyDisabledError,
} from '@/api';

// Translation key mappings for policy types
const POLICY_TYPE_LABEL_KEYS: Record<string, string> = {
  model: 'policyTypeModel',
  provider: 'policyTypeProvider',
  tool_action: 'policyTypeToolAction',
  file_path: 'policyTypeFilePath',
  command: 'policyTypeCommand',
};

// Translation key mappings for effects
const EFFECT_LABEL_KEYS: Record<string, string> = {
  allow: 'effectAllow',
  deny: 'effectDeny',
  require_approval: 'effectRequireApproval',
};

// Translation key mappings for pattern types
const PATTERN_TYPE_LABEL_KEYS: Record<string, string> = {
  glob: 'patternTypeGlob',
  regex: 'patternTypeRegex',
};

type TabType = 'rules' | 'decisions';

// Default form data for creating a new rule
const DEFAULT_FORM_DATA: CreatePolicyRuleRequest = {
  rule_key: '',
  name: '',
  policy_type: 'model',
  effect: 'require_approval',
  pattern_type: 'glob',
  pattern: '',
  value_list: [],
  tool_name: '',
  action: '',
  priority: 100,
  is_default: false,
  enabled: true,
  description: '',
};

export const PolicyRulesManagement: React.FC = () => {
  const language = useLanguage();
  const toast = useToast();
  const confirm = useConfirm();
  const [activeTab, setActiveTab] = useState<TabType>('rules');

  // Generate translated options for Select components
  const getPolicyTypeOptions = () => [
    { value: 'model', label: t('policyTypeModel', language) },
    { value: 'provider', label: t('policyTypeProvider', language) },
    { value: 'tool_action', label: t('policyTypeToolAction', language) },
    { value: 'file_path', label: t('policyTypeFilePath', language) },
    { value: 'command', label: t('policyTypeCommand', language) },
  ];

  const getEffectOptions = () => [
    { value: 'allow', label: t('effectAllow', language) },
    { value: 'deny', label: t('effectDeny', language) },
    { value: 'require_approval', label: t('effectRequireApproval', language) },
  ];

  const getPatternTypeOptions = () => [
    { value: 'glob', label: t('patternTypeGlob', language) },
    { value: 'regex', label: t('patternTypeRegex', language) },
  ];

  // Translation helper functions
  const getPolicyTypeLabel = (type: string) => t(POLICY_TYPE_LABEL_KEYS[type] || type, language);
  const getEffectLabel = (effect: string) => t(EFFECT_LABEL_KEYS[effect] || effect, language);
  const getPatternTypeLabel = (patternType: string) =>
    t(PATTERN_TYPE_LABEL_KEYS[patternType] || patternType, language);

  // --- Page Refresh Control ---
  const pageRefresh = usePageRefresh({
    page: '/manage/policy/rules',
    refreshKey: createMatcherConfig([['admin', 'policy-rules']], 'prefix'),
    interval: 0,
    enabled: false,
  });

  // --- Rules State ---
  const {
    data: rulesResponse,
    isLoading: rulesLoading,
    isError: rulesError,
    error: rulesErrorMsg,
    refetch: refetchRules,
  } = usePolicyRules();
  const createRule = useCreatePolicyRule();
  const updateRule = useUpdatePolicyRule();
  const toggleRule = useTogglePolicyRule();

  // Extract rules array from response (handle disabled state)
  const rules = rulesResponse?.data ?? [];
  const isDisabled = rulesResponse?.error?.name === 'PolicyDisabledError';

  // --- Decisions State ---
  const [sessionIdInput, setSessionIdInput] = useState('');
  const [sessionIdSearch, setSessionIdSearch] = useState('');
  const {
    data: decisions,
    isLoading: decisionsLoading,
    isError: decisionsError,
    error: decisionsErrorMsg,
    refetch: refetchDecisions,
  } = usePolicyDecisions(sessionIdSearch || null);

  // --- Modal State ---
  const [showRuleModal, setShowRuleModal] = useState(false);
  const [editingRule, setEditingRule] = useState<PolicyRule | null>(null);
  const [formData, setFormData] = useState<CreatePolicyRuleRequest>(DEFAULT_FORM_DATA);
  const [formErrors, setFormErrors] = useState<Record<string, string>>({});

  // --- Handlers ---
  const handleOpenCreateRule = () => {
    setEditingRule(null);
    setFormData(DEFAULT_FORM_DATA);
    setFormErrors({});
    setShowRuleModal(true);
  };

  const handleOpenEditRule = (rule: PolicyRule) => {
    setEditingRule(rule);
    setFormData({
      rule_key: rule.rule_key,
      name: rule.name,
      policy_type: rule.policy_type as PolicyType,
      effect: rule.effect as PolicyEffect,
      pattern_type: rule.pattern_type as PatternType,
      pattern: rule.pattern || '',
      value_list: rule.value_list || [],
      tool_name: rule.tool_name || '',
      action: rule.action || '',
      tenant_id: rule.tenant_id,
      project_path: rule.project_path || '',
      machine_id: rule.machine_id || '',
      user_id: rule.user_id,
      team_id: rule.team_id || '',
      priority: rule.priority,
      is_default: rule.is_default,
      enabled: rule.enabled,
      approval_ttl_seconds: rule.approval_ttl_seconds,
      description: rule.description || '',
    });
    setFormErrors({});
    setShowRuleModal(true);
  };

  const handleCloseRuleModal = () => {
    setShowRuleModal(false);
    setEditingRule(null);
    setFormErrors({});
  };

  // Validate form data
  const validateForm = (): boolean => {
    const errors: Record<string, string> = {};

    if (!formData.rule_key?.trim()) {
      errors.rule_key = t('ruleKeyRequired', language);
    }
    if (!formData.name?.trim()) {
      errors.name = t('ruleNameRequired', language);
    }
    if (!formData.policy_type) {
      errors.policy_type = t('policyTypeRequired', language);
    }
    if (!formData.effect) {
      errors.effect = t('effectRequired', language);
    }

    // Validate regex pattern
    if (formData.pattern_type === 'regex' && formData.pattern) {
      try {
        new RegExp(formData.pattern);
      } catch {
        errors.pattern = t('invalidRegexPattern', language);
      }
    }

    // tool_action type requires tool_name and action
    if (formData.policy_type === 'tool_action') {
      if (!formData.tool_name?.trim()) {
        errors.tool_name = t('toolNameRequired', language);
      }
      if (!formData.action?.trim()) {
        errors.action = t('actionRequired', language);
      }
    }

    // Pattern-based types require pattern or value_list
    const patternBasedTypes: PolicyType[] = ['model', 'provider', 'file_path', 'command'];
    if (patternBasedTypes.includes(formData.policy_type)) {
      if (!formData.pattern?.trim() && (!formData.value_list || formData.value_list.length === 0)) {
        errors.pattern = t('patternOrValueRequired', language);
      }
    }

    setFormErrors(errors);
    return Object.keys(errors).length === 0;
  };

  const handleSubmitRule = async () => {
    if (!validateForm()) {
      return;
    }

    try {
      if (editingRule) {
        await updateRule.mutateAsync({
          ruleKey: editingRule.rule_key,
          data: formData,
        });
        toast.success(t('ruleUpdatedSuccess', language));
      } else {
        await createRule.mutateAsync(formData);
        toast.success(t('ruleCreatedSuccess', language));
      }
      handleCloseRuleModal();
    } catch (err) {
      console.error('Failed to save rule:', err);
      // Error toast is shown by mutation
    }
  };

  const handleToggleEnabled = async (rule: PolicyRule) => {
    const action = !rule.enabled ? 'enable' : 'disable';
    const confirmed = await confirm({
      message: action === 'enable' ? t('confirmEnableRule', language) : t('confirmDisableRule', language),
      variant: rule.enabled ? 'warning' : 'primary',
    });
    if (!confirmed) return;

    try {
      await toggleRule.mutateAsync({ ruleId: rule.id!, enabled: !rule.enabled });
      toast.success(t('ruleToggledSuccess', language));
    } catch (err) {
      console.error('Failed to toggle rule:', err);
      toast.error(t('ruleToggledFailed', language));
    }
  };

  const handleSearchDecisions = () => {
    if (sessionIdInput.trim()) {
      setSessionIdSearch(sessionIdInput.trim());
    }
  };

  // Get badge variant for effect
  const getEffectVariant = (effect: string): 'success' | 'danger' | 'warning' => {
    switch (effect) {
      case 'allow':
        return 'success';
      case 'deny':
        return 'danger';
      default:
        return 'warning';
    }
  };

  // --- Render Rules Tab ---
  const renderRulesTab = () => {
    if (isDisabled) {
      return (
        <div className="text-center py-5">
          <i className="bi bi-shield-x display-4 text-muted mb-3" />
          <h5>{t('policyRulesDisabled', language)}</h5>
          <p className="text-muted">{t('contactAdmin', language)}</p>
        </div>
      );
    }

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

    if (!rules || rules.length === 0) {
      return (
        <EmptyState
          icon="bi-shield-check"
          title={t('noPolicyRules', language)}
          subtitle={t('noPolicyRulesHelp', language)}
        />
      );
    }

    return (
      <>
        <div className="table-responsive">
          <table className="table table-hover">
            <thead>
              <tr>
                <th>{t('ruleKey', language)}</th>
                <th>{t('ruleName', language)}</th>
                <th>{t('policyType', language)}</th>
                <th>{t('effect', language)}</th>
                <th>{t('priority', language)}</th>
                <th>{t('policyVersion', language)}</th>
                <th>{t('enabled', language)}</th>
                <th>{t('tableActions', language)}</th>
              </tr>
            </thead>
            <tbody>
              {rules.map((rule) => (
                <tr key={rule.id}>
                  <td>
                    <code>{rule.rule_key}</code>
                  </td>
                  <td>{rule.name}</td>
                  <td>
                    <Badge variant="secondary">{getPolicyTypeLabel(rule.policy_type)}</Badge>
                  </td>
                  <td>
                    <Badge variant={getEffectVariant(rule.effect)}>
                      {getEffectLabel(rule.effect)}
                    </Badge>
                  </td>
                  <td>{rule.priority}</td>
                  <td>
                    <span>
                      v{rule.version}
                      {rule.is_current && (
                        <Badge variant="info" className="ms-1">
                          {t('policyCurrentVersion', language)}
                        </Badge>
                      )}
                    </span>
                  </td>
                  <td>
                    <div className="form-check form-switch">
                      <input
                        className="form-check-input"
                        type="checkbox"
                        checked={rule.enabled}
                        onChange={() => handleToggleEnabled(rule)}
                        disabled={toggleRule.isPending}
                      />
                    </div>
                  </td>
                  <td>
                    <Button
                      variant="outline-primary"
                      size="sm"
                      onClick={() => handleOpenEditRule(rule)}
                    >
                      <i className="bi bi-pencil" />
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {/* Create/Edit Modal */}
        <Modal
          isOpen={showRuleModal}
          onClose={handleCloseRuleModal}
          title={
            editingRule
              ? t('editPolicyRuleVersioned', language)
              : t('createPolicyRule', language)
          }
          size="lg"
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
            {/* Version hint for editing */}
            {editingRule && (
              <div className="alert alert-info mb-3">
                <i className="bi bi-info-circle me-2" />
                {t('policyVersionedEditHint', language)}
              </div>
            )}

            {/* API Error display */}
            {(createRule.error || updateRule.error) && (
              <div className="alert alert-danger mb-3">
                {(createRule.error as Error)?.message || (updateRule.error as Error)?.message}
              </div>
            )}

            <div className="row g-3">
              {/* Rule Key */}
              <div className="col-md-6">
                <label className="form-label">{t('ruleKey', language)} *</label>
                <TextInput
                  value={formData.rule_key}
                  onChange={(value: string) => setFormData({ ...formData, rule_key: value })}
                  placeholder="e.g., deny-gpt4"
                  disabled={!!editingRule}
                />
                {formErrors.rule_key && (
                  <small className="text-danger">{formErrors.rule_key}</small>
                )}
              </div>

              {/* Rule Name */}
              <div className="col-md-6">
                <label className="form-label">{t('ruleName', language)} *</label>
                <TextInput
                  value={formData.name}
                  onChange={(value: string) => setFormData({ ...formData, name: value })}
                  placeholder="e.g., Deny GPT-4 usage"
                />
                {formErrors.name && <small className="text-danger">{formErrors.name}</small>}
              </div>

              {/* Policy Type */}
              <div className="col-md-6">
                <label className="form-label">{t('policyType', language)} *</label>
                <Select
                  options={getPolicyTypeOptions()}
                  value={formData.policy_type}
                  onChange={(value) =>
                    setFormData({
                      ...formData,
                      policy_type: value as PolicyType,
                    })
                  }
                />
                {formErrors.policy_type && (
                  <small className="text-danger">{formErrors.policy_type}</small>
                )}
              </div>

              {/* Effect */}
              <div className="col-md-6">
                <label className="form-label">{t('effect', language)} *</label>
                <Select
                  options={getEffectOptions()}
                  value={formData.effect}
                  onChange={(value) =>
                    setFormData({
                      ...formData,
                      effect: value as PolicyEffect,
                    })
                  }
                />
                {formErrors.effect && (
                  <small className="text-danger">{formErrors.effect}</small>
                )}
              </div>

              {/* Pattern Type */}
              <div className="col-md-6">
                <label className="form-label">{t('patternType', language)}</label>
                <Select
                  options={getPatternTypeOptions()}
                  value={formData.pattern_type}
                  onChange={(value) =>
                    setFormData({
                      ...formData,
                      pattern_type: value as PatternType,
                    })
                  }
                />
              </div>

              {/* Pattern */}
              <div className="col-md-6">
                <label className="form-label">{t('pattern', language)}</label>
                <TextInput
                  value={formData.pattern || ''}
                  onChange={(value: string) => setFormData({ ...formData, pattern: value })}
                  placeholder={formData.pattern_type === 'regex' ? 'e.g., ^gpt-4.*' : 'e.g., gpt-*'}
                />
                {formErrors.pattern && (
                  <small className="text-danger">{formErrors.pattern}</small>
                )}
                {formData.pattern_type === 'regex' && (
                  <small className="text-muted">{t('regexPatternHelp', language)}</small>
                )}
              </div>

              {/* Tool Name (for tool_action type) */}
              {formData.policy_type === 'tool_action' && (
                <>
                  <div className="col-md-6">
                    <label className="form-label">{t('toolName', language)} *</label>
                    <TextInput
                      value={formData.tool_name || ''}
                      onChange={(value: string) => setFormData({ ...formData, tool_name: value })}
                      placeholder="e.g., bash"
                    />
                    {formErrors.tool_name && (
                      <small className="text-danger">{formErrors.tool_name}</small>
                    )}
                  </div>
                  <div className="col-md-6">
                    <label className="form-label">{t('action', language)} *</label>
                    <TextInput
                      value={formData.action || ''}
                      onChange={(value: string) => setFormData({ ...formData, action: value })}
                      placeholder="e.g., run"
                    />
                    {formErrors.action && (
                      <small className="text-danger">{formErrors.action}</small>
                    )}
                  </div>
                </>
              )}

              {/* Priority */}
              <div className="col-md-6">
                <label className="form-label">{t('priority', language)}</label>
                <TextInput
                  type="number"
                  value={String(formData.priority || 100)}
                  onChange={(value: string) =>
                    setFormData({ ...formData, priority: parseInt(value) || 100 })
                  }
                />
              </div>

              {/* Approval TTL */}
              <div className="col-md-6">
                <label className="form-label">{t('approvalTtl', language)}</label>
                <TextInput
                  type="number"
                  value={formData.approval_ttl_seconds?.toString() || ''}
                  onChange={(value: string) =>
                    setFormData({
                      ...formData,
                      approval_ttl_seconds: value ? parseInt(value) : undefined,
                    })
                  }
                  placeholder="Leave empty for default"
                />
              </div>

              {/* Description */}
              <div className="col-12">
                <label className="form-label">{t('description', language)}</label>
                <TextInput
                  value={formData.description || ''}
                  onChange={(value: string) => setFormData({ ...formData, description: value })}
                  placeholder="Optional description"
                />
              </div>

              {/* Enabled */}
              <div className="col-12">
                <div className="form-check">
                  <input
                    className="form-check-input"
                    type="checkbox"
                    checked={formData.enabled}
                    onChange={(e) => setFormData({ ...formData, enabled: e.target.checked })}
                    id="ruleEnabled"
                  />
                  <label className="form-check-label" htmlFor="ruleEnabled">
                    {t('enabled', language)}
                  </label>
                </div>
              </div>

              {/* Is Default */}
              <div className="col-12">
                <div className="form-check">
                  <input
                    className="form-check-input"
                    type="checkbox"
                    checked={formData.is_default}
                    onChange={(e) => setFormData({ ...formData, is_default: e.target.checked })}
                    id="ruleDefault"
                  />
                  <label className="form-check-label" htmlFor="ruleDefault">
                    {t('isDefault', language)}
                  </label>
                </div>
              </div>
            </div>
          </form>
        </Modal>
      </>
    );
  };

  // --- Render Decisions Tab ---
  const renderDecisionsTab = () => {
    return (
      <>
        <div className="mb-3">
          <div className="row g-2">
            <div className="col-auto flex-grow-1">
              <TextInput
                value={sessionIdInput}
                onChange={(value: string) => setSessionIdInput(value)}
                placeholder={t('sessionId', language)}
              />
            </div>
            <div className="col-auto">
              <Button variant="primary" onClick={handleSearchDecisions}>
                {t('search', language)}
              </Button>
            </div>
          </div>
        </div>

        {!sessionIdSearch && (
          <EmptyState
            icon="bi-search"
            title={t('policyDecisions', language)}
            subtitle={t('enterSessionId', language)}
          />
        )}

        {sessionIdSearch && decisionsLoading && (
          <Loading size="lg" text={t('loading', language)} />
        )}

        {sessionIdSearch && decisionsError && (
          <Error
            message={decisionsErrorMsg?.message || t('error', language)}
            onRetry={() => refetchDecisions()}
          />
        )}

        {sessionIdSearch && decisions && decisions.length > 0 && (
          <div className="table-responsive">
            <table className="table table-hover">
              <thead>
                <tr>
                  <th>{t('decisionId', language)}</th>
                  <th>{t('decision', language)}</th>
                  <th>{t('toolName', language)}</th>
                  <th>{t('action', language)}</th>
                  <th>{t('reason', language)}</th>
                  <th>{t('created', language)}</th>
                </tr>
              </thead>
              <tbody>
                {decisions.map((decision) => (
                  <tr key={decision.id}>
                    <td>
                      <code>{decision.decision_id}</code>
                    </td>
                    <td>
                      <Badge variant={decision.decision === 'allow' ? 'success' : decision.decision === 'deny' ? 'danger' : 'warning'}>
                        {decision.decision}
                      </Badge>
                    </td>
                    <td>{decision.tool_name || '-'}</td>
                    <td>{decision.action || '-'}</td>
                    <td>{decision.reason || '-'}</td>
                    <td>{decision.created_at || '-'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {sessionIdSearch && decisions && decisions.length === 0 && (
          <EmptyState
            icon="bi-clipboard-x"
            title={t('noDataAvailable', language)}
          />
        )}
      </>
    );
  };

  return (
    <div className="policy-rules-management">
      {/* Header */}
      <div className="d-flex justify-content-between align-items-center mb-3">
        <h2>{t('policyRules', language)}</h2>
        <div className="d-flex gap-2 align-items-center">
          {/* Page Refresh Control - compact mode */}
          <PageRefreshControl
            refresh={pageRefresh}
            compact={true}
            showAutoRefreshToggle={false}
            showIntervalSelector={false}
            showLastRefreshTime={true}
          />
          {activeTab === 'rules' && (
            <Button variant="primary" size="sm" onClick={handleOpenCreateRule}>
              <i className="bi bi-plus-lg me-1" />
              {t('createPolicyRule', language)}
            </Button>
          )}
        </div>
      </div>

      {/* Tab Navigation */}
      <ul className="nav nav-tabs mb-3">
        <li className="nav-item">
          <button
            className={cn('nav-link', activeTab === 'rules' && 'active')}
            onClick={() => setActiveTab('rules')}
          >
            <i className="bi bi-shield-check me-1" />
            {t('policyRules', language)}
          </button>
        </li>
        <li className="nav-item">
          <button
            className={cn('nav-link', activeTab === 'decisions' && 'active')}
            onClick={() => setActiveTab('decisions')}
          >
            <i className="bi bi-list-check me-1" />
            {t('policyDecisions', language)}
          </button>
        </li>
      </ul>

      {/* Tab Content */}
      {activeTab === 'rules' && renderRulesTab()}
      {activeTab === 'decisions' && renderDecisionsTab()}
    </div>
  );
};