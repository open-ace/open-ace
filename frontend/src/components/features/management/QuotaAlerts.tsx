/**
 * QuotaAlerts Component - Combined Quota Management and Alert Management
 *
 * Features:
 * - Tab navigation between Quota and Alerts views
 * - Quota: User quota management with edit modal
 * - Alerts: Alert list with filters and notification preferences
 */

import React, { useState, useMemo, useEffect, useCallback } from 'react';
import { Link } from 'react-router-dom';
import { cn } from '@/utils';
import { useQuotaUsage, useQuotaStats, useUpdateQuota, usePageRefresh } from '@/hooks';
import { useLanguage, useUser } from '@/store';
import { isAdmin } from '@/utils/permissions';
import { t, type Language } from '@/i18n';
import {
  Card,
  StatCard,
  Button,
  Modal,
  TextInput,
  Select,
  Loading,
  Error,
  EmptyState,
  Progress,
  Badge,
  useToast,
  PageRefreshControl,
} from '@/components/common';
import { useConfirm } from '@/components/common';
import { formatTokens, formatDateTime, formatNumber, createMatcherConfig } from '@/utils';
import { parseApiError } from '@/utils/error';
import {
  QuotaType,
  TOKEN_QUOTA_MULTIPLIER,
  MAX_TOKEN_QUOTA,
  MAX_REQUEST_QUOTA,
} from '@/constants/quota';
import {
  parseAndValidateQuota,
  formatQuotaForDisplay,
  formatNumberAsString,
} from '@/utils/quotaFormatter';
import { alertsApi, type Alert, type NotificationPreferences } from '@/api';
import type { QuotaUsage, UpdateQuotaRequest } from '@/api';

const getTypeOptions = (language: Language) => [
  { value: '', label: t('allTypes', language) },
  { value: 'quota', label: t('quota', language) },
  { value: 'system', label: t('system', language) },
  { value: 'security', label: t('security', language) },
];

const getSeverityOptions = (language: Language) => [
  { value: '', label: t('allSeverities', language) },
  { value: 'critical', label: t('critical', language) },
  { value: 'warning', label: t('warning', language) },
  { value: 'info', label: t('info', language) },
];

const getReadOptions = (language: Language) => [
  { value: '', label: t('all', language) },
  { value: 'unread', label: t('unread', language) },
  { value: 'read', label: t('read', language) },
];

type TabType = 'quota' | 'alerts';

export const QuotaAlerts: React.FC = () => {
  const language = useLanguage();
  const user = useUser();
  const toast = useToast();
  const [activeTab, setActiveTab] = useState<TabType>('quota');

  // --- Quota State ---
  const { data: quotaData, isLoading: quotaLoading, isError, error, refetch } = useQuotaUsage();
  const { data: quotaStats } = useQuotaStats();
  const updateQuota = useUpdateQuota();

  // Page refresh control - manages manual refresh for quota and alerts data
  const pageRefresh = usePageRefresh({
    page: '/manage/quota',
    refreshKey: createMatcherConfig(
      [
        ['admin', 'quota'],
        ['admin', 'quota-stats'],
      ],
      'prefix'
    ),
    interval: 0, // Manual refresh only for configuration data
    enabled: false,
  });

  const [showQuotaModal, setShowQuotaModal] = useState(false);
  const [editingUser, setEditingUser] = useState<QuotaUsage | null>(null);
  const [formData, setFormData] = useState<UpdateQuotaRequest>({});
  const [quotaErrors, setQuotaErrors] = useState<{
    daily_token_quota?: string;
    monthly_token_quota?: string;
    daily_request_quota?: string;
    monthly_request_quota?: string;
  }>({});

  // --- Alerts State ---
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [unreadCount, setUnreadCount] = useState(0);
  const [alertsLoading, setAlertsLoading] = useState(true);
  const [alertsError, setAlertsError] = useState<string | null>(null);

  const [typeFilter, setTypeFilter] = useState('');
  const [severityFilter, setSeverityFilter] = useState('');
  const [readFilter, setReadFilter] = useState('');

  const [showPrefsModal, setShowPrefsModal] = useState(false);
  const [preferences, setPreferences] = useState<NotificationPreferences>({
    email_enabled: true,
    push_enabled: true,
    alert_types: ['quota', 'system', 'security'],
    min_severity: 'warning',
    notification_email: '',
    email_verified: false,
  });

  // Fetch alerts
  const fetchAlerts = useCallback(async () => {
    setAlertsLoading(true);
    setAlertsError(null);
    try {
      // Use admin API for quota alerts when user is admin
      if (isAdmin(user) && typeFilter === 'quota') {
        const result = await alertsApi.getAdminQuotaAlerts({
          limit: 100,
        });
        setAlerts(result.alerts);
        setUnreadCount(result.unread_count);
      } else {
        const result = await alertsApi.getAlerts({
          type: typeFilter || undefined,
          severity: severityFilter || undefined,
          unread_only: readFilter === 'unread',
        });
        setAlerts(result.alerts);
        setUnreadCount(result.unread_count);
      }
    } catch (err) {
      const errorMessage = err instanceof Error ? (err as Error).message : 'Failed to fetch alerts';
      setAlertsError(errorMessage);
    } finally {
      setAlertsLoading(false);
    }
  }, [typeFilter, severityFilter, readFilter, user]);

  // Fetch alerts on component mount
  useEffect(() => {
    fetchAlerts();
  }, [fetchAlerts]);

  // Fetch preferences
  useEffect(() => {
    const fetchPrefs = async () => {
      try {
        const prefs = await alertsApi.getPreferences();
        setPreferences(prefs);
      } catch (err) {
        console.error('Failed to fetch preferences:', err);
      }
    };
    fetchPrefs();
  }, []);

  // --- Quota Handlers ---
  const handleOpenEdit = (user: QuotaUsage) => {
    setEditingUser(user);
    // Token quotas are stored in M (millions) units directly
    setFormData({
      daily_token_quota: user.daily_token_quota ?? undefined,
      monthly_token_quota: user.monthly_token_quota ?? undefined,
      daily_request_quota: user.daily_request_quota,
      monthly_request_quota: user.monthly_request_quota,
    });
    setShowQuotaModal(true);
  };

  const handleCloseQuotaModal = () => {
    setShowQuotaModal(false);
    setEditingUser(null);
    setQuotaErrors({});
  };

  // Get available quota hint for input field (shows actual available quota)
  const getAvailableQuotaHint = useCallback(
    (quotaType: QuotaType): string => {
      const typeMap = {
        [QuotaType.DAILY_TOKEN]: {
          remaining: quotaStats?.remaining.daily_token ?? 0,
          current: editingUser?.daily_token_quota ?? 0,
          max: MAX_TOKEN_QUOTA,
          isToken: true,
        },
        [QuotaType.MONTHLY_TOKEN]: {
          remaining: quotaStats?.remaining.monthly_token ?? 0,
          current: editingUser?.monthly_token_quota ?? 0,
          max: MAX_TOKEN_QUOTA,
          isToken: true,
        },
        [QuotaType.DAILY_REQUEST]: {
          remaining: quotaStats?.remaining.daily_request ?? 0,
          current: editingUser?.daily_request_quota ?? 0,
          max: MAX_REQUEST_QUOTA,
          isToken: false,
        },
        [QuotaType.MONTHLY_REQUEST]: {
          remaining: quotaStats?.remaining.monthly_request ?? 0,
          current: editingUser?.monthly_request_quota ?? 0,
          max: MAX_REQUEST_QUOTA,
          isToken: false,
        },
      };

      const info = typeMap[quotaType];
      const available = Math.max(0, Math.min(info.remaining + info.current, info.max));

      if (!quotaStats) {
        return info.isToken ? `Max: ${info.max}M` : `Max: ${formatNumberAsString(info.max)}`;
      }

      if (info.isToken) {
        return `可用: ${available.toFixed(2)}M (上限: ${info.max}M)`;
      }
      return `可用: ${formatNumberAsString(available)} (上限: ${formatNumberAsString(info.max)})`;
    },
    [quotaStats, editingUser]
  );

  // Handle quota input change with validation
  const handleQuotaInputChange = (
    value: string,
    quotaType: QuotaType,
    field: keyof UpdateQuotaRequest
  ) => {
    const result = parseAndValidateQuota(value, quotaType);

    if (value.trim() === '') {
      // Empty input means unlimited
      setFormData({ ...formData, [field]: undefined });
      setQuotaErrors({ ...quotaErrors, [field]: undefined });
    } else if (result.value !== null) {
      // Update form data with parsed value
      setFormData({ ...formData, [field]: result.value });

      // Update error state
      if (!result.validation.isValid) {
        setQuotaErrors({
          ...quotaErrors,
          [field]: result.validation.error,
        });
      } else if (result.validation.warning) {
        // Show warning but allow the value
        setQuotaErrors({
          ...quotaErrors,
          [field]: result.validation.warning,
        });
      } else {
        // Clear error
        setQuotaErrors({ ...quotaErrors, [field]: undefined });
      }
    } else {
      // Invalid input
      setQuotaErrors({
        ...quotaErrors,
        [field]: 'Invalid number format',
      });
    }
  };

  const handleSubmitQuota = async () => {
    if (!editingUser) return;

    // Validate all quota values before submission
    const errors: typeof quotaErrors = {};

    // Validate daily token quota
    if (formData.daily_token_quota !== undefined) {
      const validation = parseAndValidateQuota(
        formData.daily_token_quota.toString(),
        QuotaType.DAILY_TOKEN
      );
      if (!validation.validation.isValid) {
        errors.daily_token_quota = validation.validation.error ?? 'Invalid value';
      }
    }

    // Validate monthly token quota
    if (formData.monthly_token_quota !== undefined) {
      const validation = parseAndValidateQuota(
        formData.monthly_token_quota.toString(),
        QuotaType.MONTHLY_TOKEN
      );
      if (!validation.validation.isValid) {
        errors.monthly_token_quota = validation.validation.error ?? 'Invalid value';
      }
    }

    // Validate daily request quota
    if (formData.daily_request_quota !== undefined) {
      const validation = parseAndValidateQuota(
        formData.daily_request_quota.toString(),
        QuotaType.DAILY_REQUEST
      );
      if (!validation.validation.isValid) {
        errors.daily_request_quota = validation.validation.error ?? 'Invalid value';
      }
    }

    // Validate monthly request quota
    if (formData.monthly_request_quota !== undefined) {
      const validation = parseAndValidateQuota(
        formData.monthly_request_quota.toString(),
        QuotaType.MONTHLY_REQUEST
      );
      if (!validation.validation.isValid) {
        errors.monthly_request_quota = validation.validation.error ?? 'Invalid value';
      }
    }

    // If there are errors, don't submit and show errors
    if (Object.keys(errors).length > 0) {
      setQuotaErrors(errors);
      toast.error(t('validationError', language), t('quotaValidationFailed', language));
      return;
    }

    // Clear errors
    setQuotaErrors({});

    try {
      // Token quotas are stored in M (millions) units directly
      const submitData: UpdateQuotaRequest = {
        daily_token_quota: formData.daily_token_quota ?? undefined,
        monthly_token_quota: formData.monthly_token_quota ?? undefined,
        daily_request_quota: formData.daily_request_quota,
        monthly_request_quota: formData.monthly_request_quota,
      };
      await updateQuota.mutateAsync({ userId: editingUser.id, data: submitData });
      toast.success(t('quotaUpdated', language), t('quotaUpdatedDesc', language));
      handleCloseQuotaModal();
    } catch (err) {
      console.error('Failed to update quota:', err);
      const errorDetail = parseApiError(err);

      // Build detailed error message
      let detailedMessage = errorDetail.message;

      // If details available, add specific information
      if (errorDetail.details && typeof errorDetail.details === 'object') {
        const details = errorDetail.details as {
          quota_type?: string;
          tenant_limit?: number;
          currently_allocated?: number;
          available?: Record<string, number>;
          suggestion?: string;
        };

        const detailParts: string[] = [];

        if (details.quota_type) {
          const quotaTypeMap: Record<string, string> = {
            daily_request: language === 'zh' ? '每日请求配额' : 'Daily request quota',
            daily_token: language === 'zh' ? '每日Token配额' : 'Daily token quota',
            monthly_request: language === 'zh' ? '每月请求配额' : 'Monthly request quota',
            monthly_token: language === 'zh' ? '每月Token配额' : 'Monthly token quota',
          };
          const quotaTypeLabel = quotaTypeMap[details.quota_type] || details.quota_type;
          detailParts.push(
            language === 'zh' ? `类型: ${quotaTypeLabel}` : `Type: ${quotaTypeLabel}`
          );
        }

        if (details.tenant_limit !== undefined) {
          detailParts.push(
            language === 'zh'
              ? `租户限额: ${details.tenant_limit.toLocaleString()}`
              : `Tenant limit: ${details.tenant_limit.toLocaleString()}`
          );
        }

        if (details.currently_allocated !== undefined) {
          detailParts.push(
            language === 'zh'
              ? `已分配: ${details.currently_allocated.toLocaleString()}`
              : `Allocated: ${details.currently_allocated.toLocaleString()}`
          );
        }

        if (
          details.available &&
          details.quota_type &&
          details.available[details.quota_type] !== undefined
        ) {
          const availableValue = details.available[details.quota_type];
          detailParts.push(
            language === 'zh'
              ? `可用余量: ${availableValue.toLocaleString()}`
              : `Available: ${availableValue.toLocaleString()}`
          );
        }

        if (details.suggestion) {
          detailParts.push(
            language === 'zh' ? `建议: ${details.suggestion}` : `Suggestion: ${details.suggestion}`
          );
        }

        if (detailParts.length > 0) {
          detailedMessage += '\n' + detailParts.join('\n');
        }
      }

      toast.error(t('error', language), detailedMessage);
    }
  };

  const getUsagePercentage = (used?: number, limit?: number) => {
    if (!used || !limit || limit === 0) return 0;
    return Math.min((used / limit) * 100, 100);
  };

  const getUsageVariant = (percentage: number) => {
    if (percentage >= 95) return 'danger';
    if (percentage >= 80) return 'warning';
    return 'success';
  };

  // --- Alerts Handlers ---
  const filteredAlerts = useMemo(() => {
    return alerts.filter((alert) => {
      if (typeFilter && alert.type !== typeFilter) return false;
      if (severityFilter && alert.severity !== severityFilter) return false;
      if (readFilter === 'read' && !alert.read) return false;
      if (readFilter === 'unread' && alert.read) return false;
      return true;
    });
  }, [alerts, typeFilter, severityFilter, readFilter]);

  const alertStats = useMemo(() => {
    const total = filteredAlerts.length;
    const unread = filteredAlerts.filter((a) => !a.read).length;
    const critical = filteredAlerts.filter((a) => a.severity === 'critical').length;
    return { total, unread, critical };
  }, [filteredAlerts]);

  const handleMarkAsRead = async (alertId: string) => {
    try {
      await alertsApi.markAsRead(alertId);
      setAlerts((prev) => prev.map((a) => (a.alert_id === alertId ? { ...a, read: true } : a)));
      setUnreadCount((prev) => Math.max(0, prev - 1));
    } catch (err) {
      console.error('Failed to mark alert as read:', err);
    }
  };

  const handleMarkAllAsRead = async () => {
    try {
      await alertsApi.markAllAsRead();
      setAlerts((prev) => prev.map((a) => ({ ...a, read: true })));
      setUnreadCount(0);
    } catch (err) {
      console.error('Failed to mark all as read:', err);
    }
  };

  const confirm = useConfirm();
  const handleDeleteAlert = async (alertId: string) => {
    if (!(await confirm({ message: t('confirmDeleteAlert', language), variant: 'danger' }))) return;
    try {
      await alertsApi.deleteAlert(alertId);
      setAlerts((prev) => prev.filter((a) => a.alert_id !== alertId));
    } catch (err) {
      console.error('Failed to delete alert:', err);
    }
  };

  const handleSavePreferences = async () => {
    try {
      await alertsApi.updatePreferences(preferences);
      setShowPrefsModal(false);
    } catch (err) {
      console.error('Failed to save preferences:', err);
    }
  };

  const getSeverityVariant = (severity: string) => {
    switch (severity) {
      case 'critical':
        return 'danger';
      case 'warning':
        return 'warning';
      default:
        return 'info';
    }
  };

  const getTypeVariant = (type: string) => {
    switch (type) {
      case 'security':
        return 'danger';
      case 'quota':
        return 'warning';
      default:
        return 'primary';
    }
  };

  // --- Render Quota Tab ---
  const renderQuotaTab = () => {
    if (quotaLoading) {
      return <Loading size="lg" text={t('loading', language)} />;
    }

    if (isError) {
      return <Error message={error?.message || t('error', language)} onRetry={() => refetch()} />;
    }

    return (
      <>
        {!quotaData || quotaData.length === 0 ? (
          <EmptyState icon="bi-sliders" title={t('noQuotaData', language)} />
        ) : (
          <div className="row g-3">
            {quotaData.map((user) => {
              const dailyTokenPercentage = getUsagePercentage(
                user.tokens_used_today,
                user.daily_token_quota ? user.daily_token_quota * TOKEN_QUOTA_MULTIPLIER : undefined
              );
              const monthlyTokenPercentage = getUsagePercentage(
                user.tokens_used_month,
                user.monthly_token_quota
                  ? user.monthly_token_quota * TOKEN_QUOTA_MULTIPLIER
                  : undefined
              );
              const dailyRequestPercentage = getUsagePercentage(
                user.requests_today,
                user.daily_request_quota
              );
              const monthlyRequestPercentage = getUsagePercentage(
                user.requests_month,
                user.monthly_request_quota
              );

              return (
                <div key={user.id} className="col-md-6 col-lg-4">
                  <Card className="h-100">
                    <div className="d-flex justify-content-between align-items-start mb-3">
                      <div>
                        <h6 className="mb-1">{user.username}</h6>
                        <small className="text-muted">{user.email}</small>
                      </div>
                      <Button
                        variant="outline-primary"
                        size="sm"
                        onClick={() => handleOpenEdit(user)}
                        title={t('editQuota', language) ?? 'Edit Quota'}
                      >
                        <i className="bi bi-pencil" />
                      </Button>
                    </div>

                    {/* Daily Token Quota */}
                    <div className="mb-2">
                      <div className="d-flex justify-content-between mb-1">
                        <small>{t('dailyTokenQuota', language)}</small>
                        <small>
                          {formatTokens(user.tokens_used_today ?? 0)} /{' '}
                          {formatQuotaForDisplay(user.daily_token_quota, true)}
                        </small>
                      </div>
                      <Progress
                        value={dailyTokenPercentage}
                        variant={getUsageVariant(dailyTokenPercentage)}
                        size="sm"
                      />
                    </div>

                    {/* Monthly Token Quota */}
                    <div className="mb-2">
                      <div className="d-flex justify-content-between mb-1">
                        <small>{t('monthlyTokenQuota', language)}</small>
                        <small>
                          {formatTokens(user.tokens_used_month ?? 0)} /{' '}
                          {formatQuotaForDisplay(user.monthly_token_quota, true)}
                        </small>
                      </div>
                      <Progress
                        value={monthlyTokenPercentage}
                        variant={getUsageVariant(monthlyTokenPercentage)}
                        size="sm"
                      />
                    </div>

                    {/* Daily Request Quota */}
                    <div className="mb-2">
                      <div className="d-flex justify-content-between mb-1">
                        <small>{t('dailyRequestQuota', language)}</small>
                        <small>
                          {formatNumber(user.requests_today ?? 0)} /{' '}
                          {user.daily_request_quota ? formatNumber(user.daily_request_quota) : '∞'}
                        </small>
                      </div>
                      <Progress
                        value={dailyRequestPercentage}
                        variant={getUsageVariant(dailyRequestPercentage)}
                        size="sm"
                      />
                    </div>

                    {/* Monthly Request Quota */}
                    <div className="mb-2">
                      <div className="d-flex justify-content-between mb-1">
                        <small>{t('monthlyRequestQuota', language)}</small>
                        <small>
                          {formatNumber(user.requests_month ?? 0)} /{' '}
                          {user.monthly_request_quota
                            ? formatNumber(user.monthly_request_quota)
                            : '∞'}
                        </small>
                      </div>
                      <Progress
                        value={monthlyRequestPercentage}
                        variant={getUsageVariant(monthlyRequestPercentage)}
                        size="sm"
                      />
                    </div>
                  </Card>
                </div>
              );
            })}
          </div>
        )}

        {/* Edit Modal */}
        <Modal
          isOpen={showQuotaModal}
          onClose={handleCloseQuotaModal}
          title={t('editQuota', language)}
          size="md"
          footer={
            <>
              <Button variant="secondary" onClick={handleCloseQuotaModal}>
                {t('cancel', language)}
              </Button>
              <Button variant="primary" onClick={handleSubmitQuota} loading={updateQuota.isPending}>
                {t('save', language)}
              </Button>
            </>
          }
        >
          {editingUser && (
            <form
              onSubmit={(e) => {
                e.preventDefault();
                handleSubmitQuota();
              }}
            >
              <div className="row g-3">
                {/* Quota Allocation Reference Panel */}
                {quotaStats && (
                  <div className="col-12">
                    <Card className="bg-light border-0 mb-3">
                      <div className="d-flex justify-content-between align-items-center mb-2">
                        <h6 className="mb-0">
                          <i className="bi bi-bar-chart me-1" />
                          {t('quotaAllocationReference', language)}
                        </h6>
                        {/* Status indicator badge */}
                        {quotaStats.percentages.daily_token > 100 ? (
                          <Badge variant="danger">
                            <i className="bi bi-exclamation-triangle-fill me-1" />
                            {language === 'zh' ? '已超限' : 'Over-allocated'}
                          </Badge>
                        ) : quotaStats.percentages.daily_token > 80 ? (
                          <Badge variant="warning">
                            {language === 'zh' ? '接近上限' : 'Near Limit'}
                          </Badge>
                        ) : (
                          <Badge variant="success">{language === 'zh' ? '正常' : 'OK'}</Badge>
                        )}
                      </div>
                      <div className="row g-2 small">
                        <div className="col-6">
                          <span className="text-muted">{t('tenantTotalQuota', language)}:</span>
                          <span className="ms-2">
                            {formatTokens(quotaStats.tenant_quota.daily_token_limit)} /{' '}
                            {t('daily', language)}
                          </span>
                        </div>
                        <div className="col-6">
                          <span className="text-muted">{t('allocated', language)}:</span>
                          <span
                            className={cn(
                              'ms-2',
                              quotaStats.percentages.daily_token > 100 && 'text-danger fw-bold'
                            )}
                          >
                            {quotaStats.allocated.daily_token}M (
                            {quotaStats.percentages.daily_token > 100
                              ? `${language === 'zh' ? '已超限' : 'Over'} ${quotaStats.percentages.daily_token}%`
                              : `${quotaStats.percentages.daily_token}%`}
                            )
                          </span>
                        </div>
                        <div className="col-6">
                          <span className="text-muted">{t('available', language)}:</span>
                          <span
                            className={cn(
                              'ms-2',
                              quotaStats.remaining.daily_token < 0
                                ? 'text-danger fw-bold'
                                : 'text-success'
                            )}
                          >
                            {quotaStats.remaining.daily_token < 0
                              ? `${language === 'zh' ? '超出' : 'Over by'} ${formatTokens(Math.abs(quotaStats.remaining.daily_token))}`
                              : formatTokens(quotaStats.remaining.daily_token)}
                          </span>
                        </div>
                        <div className="col-6">
                          <span className="text-muted">{t('users', language)}:</span>
                          <span className="ms-2">
                            {quotaStats.user_count.active} / {quotaStats.tenant_quota.max_users}
                          </span>
                        </div>
                      </div>
                      {/* Warning banner for over-allocated state */}
                      {quotaStats.percentages.daily_token > 100 && (
                        <div className="alert alert-warning mt-2 mb-0 py-2 small">
                          <i className="bi bi-exclamation-triangle-fill me-1" />
                          {language === 'zh'
                            ? '租户配额已超限分配，建议调整用户配额'
                            : 'Tenant quota is over-allocated. Consider adjusting user quotas.'}
                        </div>
                      )}
                    </Card>
                  </div>
                )}
                <div className="col-12">
                  <p className="mb-3">
                    <strong>{t('user', language)}:</strong> {editingUser.username}
                  </p>
                </div>
                <div className="col-md-6">
                  <label className="form-label">
                    {t('dailyTokenQuota', language)} (M)
                    <small className="text-muted ms-1">
                      ({getAvailableQuotaHint(QuotaType.DAILY_TOKEN)})
                    </small>
                  </label>
                  <TextInput
                    type="text"
                    value={formData.daily_token_quota?.toString() ?? ''}
                    onChange={(value: string) =>
                      handleQuotaInputChange(value, QuotaType.DAILY_TOKEN, 'daily_token_quota')
                    }
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') {
                        e.preventDefault();
                        handleSubmitQuota();
                      }
                    }}
                    placeholder={t('unlimited', language)}
                    error={quotaErrors.daily_token_quota}
                  />
                  {quotaErrors.daily_token_quota && (
                    <small className="text-danger">{quotaErrors.daily_token_quota}</small>
                  )}
                </div>
                <div className="col-md-6">
                  <label className="form-label">
                    {t('monthlyTokenQuota', language)} (M)
                    <small className="text-muted ms-1">
                      ({getAvailableQuotaHint(QuotaType.MONTHLY_TOKEN)})
                    </small>
                  </label>
                  <TextInput
                    type="text"
                    value={formData.monthly_token_quota?.toString() ?? ''}
                    onChange={(value: string) =>
                      handleQuotaInputChange(value, QuotaType.MONTHLY_TOKEN, 'monthly_token_quota')
                    }
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') {
                        e.preventDefault();
                        handleSubmitQuota();
                      }
                    }}
                    placeholder={t('unlimited', language)}
                    error={quotaErrors.monthly_token_quota}
                  />
                  {quotaErrors.monthly_token_quota && (
                    <small className="text-danger">{quotaErrors.monthly_token_quota}</small>
                  )}
                </div>
                <div className="col-md-6">
                  <label className="form-label">
                    {t('dailyRequestQuota', language)}
                    <small className="text-muted ms-1">
                      ({getAvailableQuotaHint(QuotaType.DAILY_REQUEST)})
                    </small>
                  </label>
                  <TextInput
                    type="text"
                    value={formData.daily_request_quota?.toString() ?? ''}
                    onChange={(value: string) =>
                      handleQuotaInputChange(value, QuotaType.DAILY_REQUEST, 'daily_request_quota')
                    }
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') {
                        e.preventDefault();
                        handleSubmitQuota();
                      }
                    }}
                    placeholder={t('unlimited', language)}
                    error={quotaErrors.daily_request_quota}
                  />
                  {quotaErrors.daily_request_quota && (
                    <small className="text-danger">{quotaErrors.daily_request_quota}</small>
                  )}
                </div>
                <div className="col-md-6">
                  <label className="form-label">
                    {t('monthlyRequestQuota', language)}
                    <small className="text-muted ms-1">
                      ({getAvailableQuotaHint(QuotaType.MONTHLY_REQUEST)})
                    </small>
                  </label>
                  <TextInput
                    type="text"
                    value={formData.monthly_request_quota?.toString() ?? ''}
                    onChange={(value: string) =>
                      handleQuotaInputChange(
                        value,
                        QuotaType.MONTHLY_REQUEST,
                        'monthly_request_quota'
                      )
                    }
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') {
                        e.preventDefault();
                        handleSubmitQuota();
                      }
                    }}
                    placeholder={t('unlimited', language)}
                    error={quotaErrors.monthly_request_quota}
                  />
                  {quotaErrors.monthly_request_quota && (
                    <small className="text-danger">{quotaErrors.monthly_request_quota}</small>
                  )}
                </div>
              </div>
            </form>
          )}
        </Modal>
      </>
    );
  };

  // --- Render Alerts Tab ---
  const renderAlertsTab = () => {
    if (alertsLoading) {
      return <Loading size="lg" text={t('loading', language)} />;
    }

    if (alertsError) {
      return <Error message={alertsError} onRetry={fetchAlerts} />;
    }

    return (
      <>
        {/* Statistics Cards */}
        <div className="row g-3 mb-4">
          <div className="col-md-3">
            <StatCard
              label={t('totalAlerts', language)}
              value={alertStats.total.toString()}
              icon={<i className="bi bi-bell fs-4" />}
              variant="primary"
            />
          </div>
          <div className="col-md-3">
            <StatCard
              label={t('unreadAlerts', language)}
              value={alertStats.unread.toString()}
              icon={<i className="bi bi-envelope fs-4" />}
              variant="info"
            />
          </div>
          <div className="col-md-3">
            <StatCard
              label={t('criticalAlerts', language)}
              value={alertStats.critical.toString()}
              icon={<i className="bi bi-exclamation-triangle fs-4" />}
              variant="danger"
            />
          </div>
          <div className="col-md-3">
            <StatCard
              label={t('unreadCount', language)}
              value={unreadCount.toString()}
              icon={<i className="bi bi-envelope-fill fs-4" />}
              variant="warning"
            />
          </div>
        </div>

        {/* Filters */}
        <Card className="mb-4">
          <div className="row g-3">
            <div className="col-md-3">
              <label className="form-label">{t('alertType', language)}</label>
              <Select
                options={getTypeOptions(language)}
                value={typeFilter}
                onChange={setTypeFilter}
              />
            </div>
            <div className="col-md-3">
              <label className="form-label">{t('severity', language)}</label>
              <Select
                options={getSeverityOptions(language)}
                value={severityFilter}
                onChange={setSeverityFilter}
              />
            </div>
            <div className="col-md-3">
              <label className="form-label">{t('readStatus', language)}</label>
              <Select
                options={getReadOptions(language)}
                value={readFilter}
                onChange={setReadFilter}
              />
            </div>
            <div className="col-md-3 d-flex align-items-end">
              <Button variant="secondary" size="sm" onClick={fetchAlerts}>
                <i className="bi bi-arrow-clockwise me-1" />
                {t('refresh', language)}
              </Button>
            </div>
          </div>
        </Card>

        {/* Alert List */}
        {filteredAlerts.length === 0 ? (
          <EmptyState icon="bi-bell" title={t('noAlerts', language)} />
        ) : (
          <Card>
            <div className="table-responsive">
              <table className="table table-hover">
                <thead>
                  <tr>
                    <th>{t('title', language)}</th>
                    <th>{t('message', language)}</th>
                    <th>{t('type', language)}</th>
                    <th>{t('severity', language)}</th>
                    <th>{t('time', language)}</th>
                    <th>{t('tableActions', language)}</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredAlerts.map((alert) => (
                    <tr key={alert.alert_id} className={cn(!alert.read && 'table-warning')}>
                      <td>
                        <strong>{alert.title}</strong>
                        {!alert.read && (
                          <Badge variant="primary" className="ms-2">
                            {t('new', language)}
                          </Badge>
                        )}
                      </td>
                      <td>
                        <span
                          className="text-truncate d-inline-block"
                          style={{ maxWidth: '300px' }}
                        >
                          {alert.message}
                        </span>
                      </td>
                      <td>
                        <Badge variant={getTypeVariant(alert.type)}>
                          {t(alert.type, language)}
                        </Badge>
                      </td>
                      <td>
                        <Badge variant={getSeverityVariant(alert.severity)}>
                          {t(alert.severity, language)}
                        </Badge>
                      </td>
                      <td>
                        <small className="text-muted">{formatDateTime(alert.created_at)}</small>
                      </td>
                      <td>
                        <div className="btn-group btn-group-sm">
                          {!alert.read && (
                            <Button
                              variant="outline-primary"
                              size="sm"
                              onClick={() => handleMarkAsRead(alert.alert_id)}
                              title={t('markAsRead', language) ?? 'Mark as Read'}
                            >
                              <i className="bi bi-check" />
                            </Button>
                          )}
                          <Button
                            variant="outline-danger"
                            size="sm"
                            onClick={() => handleDeleteAlert(alert.alert_id)}
                            title={t('delete', language) ?? 'Delete'}
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
          </Card>
        )}

        {/* Preferences Modal */}
        <Modal
          isOpen={showPrefsModal}
          onClose={() => setShowPrefsModal(false)}
          title={t('notificationPreferences', language)}
          size="md"
          footer={
            <>
              <Button variant="secondary" onClick={() => setShowPrefsModal(false)}>
                {t('cancel', language)}
              </Button>
              <Button variant="primary" onClick={handleSavePreferences}>
                {t('save', language)}
              </Button>
            </>
          }
        >
          <form
            onSubmit={(e) => {
              e.preventDefault();
              handleSavePreferences();
            }}
          >
            <div className="row g-3">
              <div className="col-12">
                <div className="form-check form-switch">
                  <input
                    className="form-check-input"
                    type="checkbox"
                    id="emailEnabled"
                    checked={preferences.email_enabled}
                    onChange={(e) =>
                      setPreferences({ ...preferences, email_enabled: e.target.checked })
                    }
                  />
                  <label className="form-check-label" htmlFor="emailEnabled">
                    {t('emailNotifications', language)}
                  </label>
                </div>
              </div>
              {preferences.email_enabled && (
                <div className="col-12">
                  <label className="form-label">{t('notificationEmail', language)}</label>
                  <input
                    type="email"
                    className="form-control"
                    value={preferences.notification_email ?? ''}
                    onChange={(e) =>
                      setPreferences({ ...preferences, notification_email: e.target.value })
                    }
                    placeholder="your@email.com"
                  />
                  <small className="text-muted">
                    {t('smtpSetupGuide4QuotaPrefix', language)}{' '}
                    <Link to="/manage/settings/smtp">{t('smtpConfiguration', language)}</Link>{' '}
                    {t('smtpSetupGuide4QuotaSuffix', language)}
                  </small>
                </div>
              )}
              <div className="col-12">
                <div className="form-check form-switch">
                  <input
                    className="form-check-input"
                    type="checkbox"
                    id="pushEnabled"
                    checked={preferences.push_enabled}
                    onChange={(e) =>
                      setPreferences({ ...preferences, push_enabled: e.target.checked })
                    }
                  />
                  <label className="form-check-label" htmlFor="pushEnabled">
                    {t('pushNotifications', language)}
                  </label>
                </div>
              </div>
              <div className="col-12">
                <label className="form-label">{t('webhookUrl', language)}</label>
                <input
                  type="url"
                  className="form-control"
                  value={preferences.webhook_url ?? ''}
                  onChange={(e) => setPreferences({ ...preferences, webhook_url: e.target.value })}
                  placeholder="https://example.com/webhook"
                />
              </div>
              <div className="col-12">
                <label className="form-label">{t('minSeverity', language)}</label>
                <Select
                  options={[
                    { value: 'info', label: t('info', language) },
                    { value: 'warning', label: t('warning', language) },
                    { value: 'critical', label: t('critical', language) },
                  ]}
                  value={preferences.min_severity}
                  onChange={(value) =>
                    setPreferences({
                      ...preferences,
                      min_severity: value as 'info' | 'warning' | 'critical',
                    })
                  }
                />
              </div>
            </div>
          </form>
        </Modal>
      </>
    );
  };

  return (
    <div className="quota-alerts">
      {/* Header */}
      <div className="d-flex justify-content-between align-items-center mb-3">
        <h2>{t('quotaAndAlerts', language)}</h2>
        <div className="d-flex gap-2">
          <PageRefreshControl refresh={pageRefresh} compact={true} showLastRefreshTime={true} />
          {activeTab === 'quota' && unreadCount > 0 && (
            <Button variant="outline-info" size="sm" onClick={() => setActiveTab('alerts')}>
              <i className="bi bi-bell me-1" />
              {t('viewAlerts', language, { count: unreadCount })}
            </Button>
          )}
          {activeTab === 'alerts' && (
            <>
              <Button variant="outline-secondary" size="sm" onClick={() => setShowPrefsModal(true)}>
                <i className="bi bi-gear me-1" />
                {t('preferences', language)}
              </Button>
              <Button variant="primary" size="sm" onClick={handleMarkAllAsRead}>
                <i className="bi bi-check-all me-1" />
                {t('markAllRead', language)}
              </Button>
            </>
          )}
        </div>
      </div>

      {/* Tab Navigation */}
      <ul className="nav nav-tabs mb-3">
        <li className="nav-item">
          <button
            className={cn('nav-link', activeTab === 'quota' && 'active')}
            onClick={() => setActiveTab('quota')}
          >
            <i className="bi bi-sliders me-1" />
            {t('quotaManagement', language)}
          </button>
        </li>
        <li className="nav-item">
          <button
            className={cn('nav-link', activeTab === 'alerts' && 'active')}
            onClick={() => setActiveTab('alerts')}
          >
            <i className="bi bi-bell me-1" />
            {t('alertManagement', language)}
          </button>
        </li>
      </ul>

      {/* Tab Content */}
      {activeTab === 'quota' ? renderQuotaTab() : renderAlertsTab()}
    </div>
  );
};
