/**
 * AlertManagement Component - Alert management page
 *
 * Features:
 * - Alert statistics cards
 * - Alert list with filters
 * - Mark as read / delete actions
 * - Notification preferences modal
 */

import React, { useState, useMemo } from 'react';
import { cn } from '@/utils';
import { useLanguage } from '@/store';
import { t } from '@/i18n';
import {
  Card,
  StatCard,
  Button,
  Select,
  Loading,
  Error,
  EmptyState,
  Modal,
  Badge,
} from '@/components/common';
import { alertsApi, type Alert, type NotificationPreferences } from '@/api';
import { formatDateTime } from '@/utils';

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

export const AlertManagement: React.FC = () => {
  const language = useLanguage();
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [unreadCount, setUnreadCount] = useState(0);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

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
  const fetchAlerts = React.useCallback(async () => {
    setIsLoading(true);
    setError(null);
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
      setError(errorMessage);
    } finally {
      setIsLoading(false);
    }
  }, [typeFilter, severityFilter, readFilter]);

  React.useEffect(() => {
    fetchAlerts();
  }, [fetchAlerts]);

  // Fetch preferences
  React.useEffect(() => {
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

  // Filter alerts
  const filteredAlerts = useMemo(() => {
    return alerts.filter((alert) => {
      if (typeFilter && alert.type !== typeFilter) return false;
      if (severityFilter && alert.severity !== severityFilter) return false;
      if (readFilter === 'read' && !alert.is_read) return false;
      if (readFilter === 'unread' && alert.is_read) return false;
      return true;
    });
  }, [alerts, typeFilter, severityFilter, readFilter]);

  // Statistics
  const stats = useMemo(() => {
    const total = filteredAlerts.length;
    const unread = filteredAlerts.filter((a) => !a.is_read).length;
    const critical = filteredAlerts.filter((a) => a.severity === 'critical').length;
    return { total, unread, critical };
  }, [filteredAlerts]);

  // Handlers
  const handleMarkAsRead = async (alertId: string) => {
    try {
      await alertsApi.markAsRead(alertId);
      setAlerts((prev) =>
        prev.map((a) => (a.id === alertId ? { ...a, is_read: true } : a))
      );
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

  const handleDelete = async (alertId: string) => {
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

  if (isLoading) {
    return <Loading size="lg" text={t('loading', language)} />;
  }

  if (error) {
    return <Error message={error} onRetry={fetchAlerts} />;
  }

  return (
    <div className="alert-management">
      {/* Header */}
      <div className="d-flex justify-content-between align-items-center mb-4">
        <h2>{t('alertManagement', language)}</h2>
        <div className="d-flex gap-2">
          <Button variant="outline-secondary" size="sm" onClick={() => setShowPrefsModal(true)}>
            <i className="bi bi-gear me-1" />
            {t('preferences', language)}
          </Button>
          <Button variant="primary" size="sm" onClick={handleMarkAllAsRead}>
            <i className="bi bi-check-all me-1" />
            {t('markAllRead', language)}
          </Button>
        </div>
      </div>

      {/* Statistics Cards */}
      <div className="row g-3 mb-4">
        <div className="col-md-3">
          <StatCard
            label={t('totalAlerts', language)}
            value={stats.total.toString()}
            icon={<i className="bi bi-bell fs-4" />}
            variant="primary"
          />
        </div>
        <div className="col-md-3">
          <StatCard
            label={t('unreadAlerts', language)}
            value={stats.unread.toString()}
            icon={<i className="bi bi-envelope fs-4" />}
            variant="info"
          />
        </div>
        <div className="col-md-3">
          <StatCard
            label={t('criticalAlerts', language)}
            value={stats.critical.toString()}
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
            <Select options={SEVERITY_OPTIONS} value={severityFilter} onChange={setSeverityFilter} />
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
                      <span className="text-truncate d-inline-block" style={{ maxWidth: '300px' }}>
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
                          onClick={() => handleDelete(alert.id)}
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
              value={preferences.webhook_url || ''}
              onChange={(e) =>
                setPreferences({ ...preferences, webhook_url: e.target.value })
              }
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
      </Modal>
    </div>
  );
};