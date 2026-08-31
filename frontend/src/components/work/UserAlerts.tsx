/**
 * UserAlerts Component - Personal Alert Management
 *
 * Features:
 * - Alert list with filters
 * - Mark as read / Mark all as read
 * - Delete alerts
 * - Notification preferences
 *
 * This component is for regular users (user/readonly roles).
 * Admin/Manager users should use /manage/quota?tab=alerts instead.
 */

import React, { useState, useMemo, useEffect, useCallback } from 'react';
import { Link } from 'react-router-dom';
import { cn } from '@/utils';
import { useLanguage } from '@/store';
import { t, type Language } from '@/i18n';
import {
  Card,
  StatCard,
  Button,
  Modal,
  Select,
  Loading,
  Error,
  EmptyState,
  Badge,
  useToast,
} from '@/components/common';
import { useConfirm } from '@/components/common';
import { formatDateTime } from '@/utils';
import { getErrorMessage } from '@/utils/error';
import { alertsApi, type Alert, type NotificationPreferences } from '@/api';

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

export const UserAlerts: React.FC = () => {
  const language = useLanguage();
  const toast = useToast();
  const confirm = useConfirm();

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
      // Regular users: view their own alerts
      const result = await alertsApi.getAlerts({
        type: typeFilter || undefined,
        severity: severityFilter || undefined,
        unread_only: readFilter === 'unread',
        limit: 100,
      });
      setAlerts(result.alerts);
      setUnreadCount(result.unread_count);
    } catch (err) {
      const errorMessage = getErrorMessage(err, 'Failed to fetch alerts');
      setAlertsError(errorMessage);
    } finally {
      setAlertsLoading(false);
    }
  }, [typeFilter, severityFilter, readFilter]);

  // Fetch alerts on component mount and filter changes
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

  // --- Filters ---
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

  // --- Handlers ---
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
      toast.success(t('success', language), t('allAlertsMarkedRead', language));
    } catch (err) {
      console.error('Failed to mark all as read:', err);
      toast.error(t('error', language), getErrorMessage(err, 'Failed to mark all as read'));
    }
  };

  const handleDeleteAlert = async (alertId: string) => {
    if (!(await confirm({ message: t('confirmDeleteAlert', language), variant: 'danger' })))
      return;
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
      toast.success(t('success', language), t('preferencesSaved', language));
    } catch (err) {
      console.error('Failed to save preferences:', err);
      toast.error(t('error', language), getErrorMessage(err, 'Failed to save preferences'));
    }
  };

  // --- Helpers ---
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

  // --- Render ---
  if (alertsLoading) {
    return <Loading size="lg" text={t('loading', language)} />;
  }

  if (alertsError) {
    return <Error message={alertsError} onRetry={fetchAlerts} />;
  }

  return (
    <div className="user-alerts p-3">
      {/* Header */}
      <div className="d-flex justify-content-between align-items-center mb-3">
        <h2>{t('myAlerts', language)}</h2>
        <div className="d-flex gap-2">
          <Button
            variant="outline-secondary"
            size="sm"
            onClick={() => setShowPrefsModal(true)}
          >
            <i className="bi bi-gear me-1" />
            {t('preferences', language)}
          </Button>
          {unreadCount > 0 && (
            <Button variant="outline-primary" size="sm" onClick={handleMarkAllAsRead}>
              <i className="bi bi-check-all me-1" />
              {t('markAllAsRead', language)}
            </Button>
          )}
        </div>
      </div>

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
        <EmptyState
          icon="bi-bell"
          title={t('noAlerts', language)}
          description={t('noAlertsDescription', language)}
        />
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
    </div>
  );
};