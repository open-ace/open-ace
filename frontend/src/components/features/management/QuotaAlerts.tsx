/**
 * QuotaAlerts Component - Combined Quota Management and Alert Management
 *
 * Features:
 * - Tab navigation between Quota and Alerts views
 * - Quota: User quota management with edit modal
 * - Alerts: Alert list with filters and notification preferences
 */

import React, { useState, useMemo, useEffect, useCallback } from 'react';
import { cn } from '@/utils';
import { useQuotaUsage, useUpdateQuota } from '@/hooks';
import { useLanguage } from '@/store';
import { t } from '@/i18n';
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
} from '@/components/common';
import { formatTokens, formatDateTime, formatNumber } from '@/utils';

// Token quotas are stored in M (millions) units
const TOKEN_QUOTA_MULTIPLIER = 1_000_000;

// Format quota value (stored in M units) to display string
const formatQuotaTokens = (quota: number | undefined | null): string => {
  if (!quota) return '∞';
  return formatTokens(quota * TOKEN_QUOTA_MULTIPLIER);
};
import { alertsApi, type Alert, type NotificationPreferences } from '@/api';
import type { QuotaUsage, UpdateQuotaRequest } from '@/api';

const TYPE_OPTIONS = [
  { value: '', label: 'All Types' },
  { value: 'quota', label: 'Quota' },
  { value: 'system', label: 'System' },
  { value: 'security', label: 'Security' },
];

const SEVERITY_OPTIONS = [
  { value: '', label: 'All Severities' },
  { value: 'critical', label: 'Critical' },
  { value: 'warning', label: 'Warning' },
  { value: 'info', label: 'Info' },
];

const READ_OPTIONS = [
  { value: '', label: 'All' },
  { value: 'unread', label: 'Unread' },
  { value: 'read', label: 'Read' },
];

type TabType = 'quota' | 'alerts';

export const QuotaAlerts: React.FC = () => {
  const language = useLanguage();
  const toast = useToast();
  const [activeTab, setActiveTab] = useState<TabType>('quota');

  // --- Quota State ---
  const {
    data: quotaData,
    isLoading: quotaLoading,
    isFetching,
    isError,
    error,
    refetch,
  } = useQuotaUsage();
  const updateQuota = useUpdateQuota();

  const [showQuotaModal, setShowQuotaModal] = useState(false);
  const [editingUser, setEditingUser] = useState<QuotaUsage | null>(null);
  const [formData, setFormData] = useState<UpdateQuotaRequest>({});

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
  });

  // Fetch alerts
  const fetchAlerts = useCallback(async () => {
    setAlertsLoading(true);
    setAlertsError(null);
    try {
      const result = await alertsApi.getAlerts({
        type: typeFilter || undefined,
        severity: severityFilter || undefined,
        unread_only: readFilter === 'unread',
      });
      setAlerts(result.alerts);
      setUnreadCount(result.unread_count);
    } catch (err) {
      const errorMessage = err instanceof Error ? (err as Error).message : 'Failed to fetch alerts';
      setAlertsError(errorMessage);
    } finally {
      setAlertsLoading(false);
    }
  }, [typeFilter, severityFilter, readFilter]);

  // Fetch alerts when tab is active
  useEffect(() => {
    if (activeTab === 'alerts') {
      fetchAlerts();
    }
  }, [activeTab, fetchAlerts]);

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
      daily_token_quota: user.daily_token_quota || undefined,
      monthly_token_quota: user.monthly_token_quota || undefined,
      daily_request_quota: user.daily_request_quota,
      monthly_request_quota: user.monthly_request_quota,
    });
    setShowQuotaModal(true);
  };

  const handleCloseQuotaModal = () => {
    setShowQuotaModal(false);
    setEditingUser(null);
  };

  const handleSubmitQuota = async () => {
    if (!editingUser) return;

    try {
      // Token quotas are stored in M (millions) units directly
      const submitData: UpdateQuotaRequest = {
        daily_token_quota: formData.daily_token_quota || undefined,
        monthly_token_quota: formData.monthly_token_quota || undefined,
        daily_request_quota: formData.daily_request_quota,
        monthly_request_quota: formData.monthly_request_quota,
      };
      await updateQuota.mutateAsync({ userId: editingUser.id, data: submitData });
      toast.success(t('quotaUpdated', language), t('quotaUpdatedDesc', language));
      handleCloseQuotaModal();
    } catch (err) {
      console.error('Failed to update quota:', err);
      const errorMessage = err && typeof err === 'object' && 'message' in err
        ? String((err as { message: string }).message)
        : t('error', language);
      toast.error(t('error', language), errorMessage);
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
      if (readFilter === 'read' && !alert.is_read) return false;
      if (readFilter === 'unread' && alert.is_read) return false;
      return true;
    });
  }, [alerts, typeFilter, severityFilter, readFilter]);

  const alertStats = useMemo(() => {
    const total = filteredAlerts.length;
    const unread = filteredAlerts.filter((a) => !a.is_read).length;
    const critical = filteredAlerts.filter((a) => a.severity === 'critical').length;
    return { total, unread, critical };
  }, [filteredAlerts]);

  const handleMarkAsRead = async (alertId: string) => {
    try {
      await alertsApi.markAsRead(alertId);
      setAlerts((prev) => prev.map((a) => (a.id === alertId ? { ...a, is_read: true } : a)));
      setUnreadCount((prev) => Math.max(0, prev - 1));
    } catch (err) {
      console.error('Failed to mark alert as read:', err);
    }
  };

  const handleMarkAllAsRead = async () => {
    try {
      await alertsApi.markAllAsRead();
      setAlerts((prev) => prev.map((a) => ({ ...a, is_read: true })));
      setUnreadCount(0);
    } catch (err) {
      console.error('Failed to mark all as read:', err);
    }
  };

  const handleDeleteAlert = async (alertId: string) => {
    if (!window.confirm(t('confirmDeleteAlert', language))) return;
    try {
      await alertsApi.deleteAlert(alertId);
      setAlerts((prev) => prev.filter((a) => a.id !== alertId));
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
        {/* Alert Rules Overview */}
        <Card title={t('alertRules', language)} className="mb-4">
          <div className="alert-rules-list">
            {alerts.length === 0 ? (
              <EmptyState icon="bi-bell" title={t('noAlerts', language)} />
            ) : (
              <div className="table-responsive">
                <table className="table table-sm">
                  <thead>
                    <tr>
                      <th>{t('title', language)}</th>
                      <th>{t('severity', language)}</th>
                      <th>{t('time', language)}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {alerts.slice(0, 5).map((alert) => (
                      <tr key={alert.id}>
                        <td>{alert.title}</td>
                        <td>
                          <Badge variant={getSeverityVariant(alert.severity)}>
                            {alert.severity}
                          </Badge>
                        </td>
                        <td>
                          <small className="text-muted">{formatDateTime(alert.created_at)}</small>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </Card>

        {!quotaData || quotaData.length === 0 ? (
          <EmptyState icon="bi-sliders" title={t('noQuotaData', language)} />
        ) : (
          <div className="row g-3">
            {quotaData.map((user) => {
              const dailyTokenPercentage = getUsagePercentage(
                user.tokens_used_today,
                user.daily_token_quota
              );
              const monthlyTokenPercentage = getUsagePercentage(
                user.tokens_used_month,
                user.monthly_token_quota
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
                          {formatQuotaTokens(user.daily_token_quota)}
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
                          {formatQuotaTokens(user.monthly_token_quota)}
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
                          {user.monthly_request_quota ? formatNumber(user.monthly_request_quota) : '∞'}
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
                <div className="col-12">
                  <p className="mb-3">
                    <strong>{t('user', language)}:</strong> {editingUser.username}
                  </p>
                </div>
                <div className="col-md-6">
                  <label className="form-label">{t('dailyTokenQuota', language)} (M)</label>
                  <TextInput
                    type="number"
                    value={formData.daily_token_quota?.toString() ?? ''}
                    onChange={(value: string) =>
                      setFormData({
                        ...formData,
                        daily_token_quota: value ? parseInt(value) : undefined,
                      })
                    }
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') {
                        e.preventDefault();
                        handleSubmitQuota();
                      }
                    }}
                    placeholder={t('unlimited', language)}
                  />
                </div>
                <div className="col-md-6">
                  <label className="form-label">{t('monthlyTokenQuota', language)} (M)</label>
                  <TextInput
                    type="number"
                    value={formData.monthly_token_quota?.toString() ?? ''}
                    onChange={(value: string) =>
                      setFormData({
                        ...formData,
                        monthly_token_quota: value ? parseInt(value) : undefined,
                      })
                    }
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') {
                        e.preventDefault();
                        handleSubmitQuota();
                      }
                    }}
                    placeholder={t('unlimited', language)}
                  />
                </div>
                <div className="col-md-6">
                  <label className="form-label">{t('dailyRequestQuota', language)}</label>
                  <TextInput
                    type="number"
                    value={formData.daily_request_quota?.toString() ?? ''}
                    onChange={(value: string) =>
                      setFormData({
                        ...formData,
                        daily_request_quota: value ? parseInt(value) : undefined,
                      })
                    }
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') {
                        e.preventDefault();
                        handleSubmitQuota();
                      }
                    }}
                    placeholder={t('unlimited', language)}
                  />
                </div>
                <div className="col-md-6">
                  <label className="form-label">{t('monthlyRequestQuota', language)}</label>
                  <TextInput
                    type="number"
                    value={formData.monthly_request_quota?.toString() ?? ''}
                    onChange={(value: string) =>
                      setFormData({
                        ...formData,
                        monthly_request_quota: value ? parseInt(value) : undefined,
                      })
                    }
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') {
                        e.preventDefault();
                        handleSubmitQuota();
                      }
                    }}
                    placeholder={t('unlimited', language)}
                  />
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
              <Select options={TYPE_OPTIONS} value={typeFilter} onChange={setTypeFilter} />
            </div>
            <div className="col-md-3">
              <label className="form-label">{t('severity', language)}</label>
              <Select
                options={SEVERITY_OPTIONS}
                value={severityFilter}
                onChange={setSeverityFilter}
              />
            </div>
            <div className="col-md-3">
              <label className="form-label">{t('readStatus', language)}</label>
              <Select options={READ_OPTIONS} value={readFilter} onChange={setReadFilter} />
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
                    <tr key={alert.id} className={cn(!alert.is_read && 'table-warning')}>
                      <td>
                        <strong>{alert.title}</strong>
                        {!alert.is_read && (
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
                        <Badge variant={getTypeVariant(alert.type)}>{alert.type}</Badge>
                      </td>
                      <td>
                        <Badge variant={getSeverityVariant(alert.severity)}>{alert.severity}</Badge>
                      </td>
                      <td>
                        <small className="text-muted">{formatDateTime(alert.created_at)}</small>
                      </td>
                      <td>
                        <div className="btn-group btn-group-sm">
                          {!alert.is_read && (
                            <Button
                              variant="outline-primary"
                              size="sm"
                              onClick={() => handleMarkAsRead(alert.id)}
                            >
                              <i className="bi bi-check" />
                            </Button>
                          )}
                          <Button
                            variant="outline-danger"
                            size="sm"
                            onClick={() => handleDeleteAlert(alert.id)}
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
                  { value: 'info', label: 'Info' },
                  { value: 'warning', label: 'Warning' },
                  { value: 'critical', label: 'Critical' },
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
          {activeTab === 'quota' && (
            <Button variant="primary" size="sm" onClick={() => refetch()} loading={isFetching}>
              {isFetching ? null : <i className="bi bi-arrow-clockwise me-1" />}
              {t('refresh', language)}
            </Button>
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
