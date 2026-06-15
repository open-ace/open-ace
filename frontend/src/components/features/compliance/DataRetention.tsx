/**
 * DataRetention Component - Data retention management page
 *
 * Features:
 * - Retention rules management
 * - Cleanup preview and execution
 * - Cleanup history
 * - Storage estimates
 */

import React, { useState, useEffect } from 'react';
import { useLanguage } from '@/store';
import { t } from '@/i18n';
import type { Language } from '@/types';
import {
  Card,
  StatCard,
  Button,
  Loading,
  Error,
  EmptyState,
  Modal,
  Badge,
} from '@/components/common';
import {
  complianceApi,
  type RetentionRule,
  type RetentionHistory,
  type StorageEstimate,
  type RetentionReport,
} from '@/api';
import { formatDateTime } from '@/utils';
import { cn } from '@/utils';
import { CleanupPreviewContent } from './CleanupPreviewContent';

/**
 * Data type metadata mapping table
 * Maps backend retention rule keys to display labels, icons, and storage estimate keys
 * Synchronized with backend DEFAULT_RULES in app/modules/compliance/retention.py
 */
export const DATA_TYPE_META: Record<
  string,
  {
    i18nKey: string;
    icon: string;
    fallbackLabel: string;
    storageEstimateKey?: string; // Maps retention rule key to storage estimate API key
  }
> = {
  audit_logs: {
    i18nKey: 'dataTypeAuditLogs',
    icon: 'bi-journal-text',
    fallbackLabel: 'Audit Logs',
  },
  quota_alerts: {
    i18nKey: 'dataTypeQuotaAlerts',
    icon: 'bi-bell',
    fallbackLabel: 'Quota Alerts',
  },
  sessions: {
    i18nKey: 'dataTypeSessions',
    icon: 'bi-chat-square',
    fallbackLabel: 'Sessions',
  },
  sso_sessions: {
    i18nKey: 'dataTypeSsoSessions',
    icon: 'bi-key',
    fallbackLabel: 'SSO Sessions',
  },
  usage_data: {
    i18nKey: 'dataTypeUsageData',
    icon: 'bi-bar-chart',
    fallbackLabel: 'Usage Data',
    storageEstimateKey: 'daily_usage',
  },
  messages: {
    i18nKey: 'dataTypeMessages',
    icon: 'bi-envelope',
    fallbackLabel: 'Messages',
    storageEstimateKey: 'daily_messages',
  },
  user_activity: {
    i18nKey: 'dataTypeUserActivity',
    icon: 'bi-person-activity',
    fallbackLabel: 'User Activity',
  },
};

/**
 * Format snake_case key to Title Case for fallback display
 * Example: 'audit_logs' -> 'Audit Logs'
 */
export function formatDataTypeKey(key: string): string {
  return key
    .split('_')
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(' ');
}

/**
 * Get display label for a data type key
 * Uses i18n translation if available, otherwise uses fallback label or formatted key
 */
export function getDataTypeLabel(key: string, language: Language): string {
  const meta = DATA_TYPE_META[key];
  if (meta) {
    // Try i18n translation, fallback to fallbackLabel
    const translated = t(meta.i18nKey, language);
    // If translation returns the key itself (not found), use fallback
    return translated === meta.i18nKey ? meta.fallbackLabel : translated;
  }
  // Unknown type: format snake_case to Title Case
  return formatDataTypeKey(key);
}

/**
 * Get icon for a data type key
 */
export function getDataTypeIcon(key: string): string {
  const meta = DATA_TYPE_META[key];
  return meta?.icon ?? 'bi-database';
}

/**
 * Get storage estimate display label for a storage estimate data type key
 * Maps storage estimate API keys back to retention rule display labels
 */
function getStorageEstimateLabel(storageKey: string, language: Language): string {
  // First, try to find a retention rule key that maps to this storage key
  for (const [ruleKey, meta] of Object.entries(DATA_TYPE_META)) {
    if (meta.storageEstimateKey === storageKey) {
      return getDataTypeLabel(ruleKey, language);
    }
  }
  // If no mapping found, format the storage key itself
  return formatDataTypeKey(storageKey);
}

/**
 * Adapt backend rules object to table data array
 * Filters null/undefined rules, preserves all valid rules including disabled ones
 */
function adaptRulesToTableData(
  rules: Record<string, RetentionRule>,
  language: Language
): Array<{ key: string; label: string; icon: string; rule: RetentionRule }> {
  return Object.entries(rules)
    .filter(([, rule]) => rule !== null && rule !== undefined) // Filter null/undefined
    .map(([key, rule]) => ({
      key,
      label: getDataTypeLabel(key, language),
      icon: getDataTypeIcon(key),
      rule,
    }));
}

export const DataRetention: React.FC = () => {
  const language = useLanguage();
  const [rules, setRules] = useState<Record<string, RetentionRule>>({});
  const [history, setHistory] = useState<RetentionHistory[]>([]);
  const [storage, setStorage] = useState<StorageEstimate[]>([]);
  const [complianceStatus, setComplianceStatus] = useState<Record<string, unknown>>({});
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [showEditModal, setShowEditModal] = useState(false);
  const [showPreviewModal, setShowPreviewModal] = useState(false);
  const [editingRule, setEditingRule] = useState<string | null>(null);
  const [editDays, setEditDays] = useState(90);
  const [editAction, setEditAction] = useState<'delete' | 'archive' | 'anonymize'>('delete');
  const [previewResult, setPreviewResult] = useState<RetentionReport | null>(null);
  const [isRunning, setIsRunning] = useState(false);

  // Fetch data
  const fetchData = React.useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const [rulesData, historyData, storageData, statusData] = await Promise.all([
        complianceApi.getRetentionRules(),
        complianceApi.getRetentionHistory(30),
        complianceApi.getStorageEstimates(),
        complianceApi.getRetentionStatus(),
      ]);
      setRules(rulesData);
      setHistory(historyData);
      setStorage(storageData);
      setComplianceStatus(statusData);
    } catch (err) {
      const errorMessage = err instanceof Error ? (err as Error).message : 'Failed to fetch data';
      setError(errorMessage);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  // Open edit modal
  const handleOpenEdit = (dataType: string) => {
    const rule = rules[dataType];
    setEditingRule(dataType);
    setEditDays(rule?.retention_days ?? 90);
    setEditAction(rule?.action ?? 'delete');
    setShowEditModal(true);
  };

  // Save rule
  const handleSaveRule = async () => {
    if (!editingRule) return;
    try {
      await complianceApi.setRetentionRule({
        data_type: editingRule,
        retention_days: editDays,
        action: editAction,
      });
      setShowEditModal(false);
      fetchData();
    } catch (err) {
      console.error('Failed to save rule:', err);
    }
  };

  // Preview cleanup
  const handlePreview = async () => {
    setIsRunning(true);
    try {
      const result = await complianceApi.runCleanup(true);
      setPreviewResult(result);
      setShowPreviewModal(true);
    } catch (err) {
      console.error('Failed to preview cleanup:', err);
    } finally {
      setIsRunning(false);
    }
  };

  // Execute cleanup
  const handleExecuteCleanup = async () => {
    if (!window.confirm(t('confirmCleanup', language))) return;
    setIsRunning(true);
    try {
      await complianceApi.runCleanup(false);
      setShowPreviewModal(false);
      fetchData();
    } catch (err) {
      console.error('Failed to execute cleanup:', err);
    } finally {
      setIsRunning(false);
    }
  };

  if (isLoading) {
    return <Loading size="lg" text={t('loading', language)} />;
  }

  if (error) {
    return <Error message={error} onRetry={fetchData} />;
  }

  // Adapt backend rules to table data
  const adaptedRules = adaptRulesToTableData(rules, language);

  // Calculate stats
  const totalRecords = storage.reduce((sum, s) => sum + s.record_count, 0);
  const totalSize = storage.reduce((sum, s) => sum + s.estimated_size_mb, 0);
  const complianceScore =
    (complianceStatus as { compliance_score?: number }).compliance_score ?? 100;

  return (
    <div className="data-retention">
      {/* Header */}
      <div className="d-flex justify-content-between align-items-center mb-4">
        <h2>{t('dataRetention', language)}</h2>
        <div className="d-flex gap-2">
          <Button variant="outline-secondary" size="sm" onClick={handlePreview} loading={isRunning}>
            <i className="bi bi-eye me-1" />
            {t('preview', language)}
          </Button>
          <Button variant="danger" size="sm" onClick={handleExecuteCleanup} loading={isRunning}>
            <i className="bi bi-trash me-1" />
            {t('runCleanup', language)}
          </Button>
        </div>
      </div>

      {/* Statistics */}
      <div className="row g-3 mb-4">
        <div className="col-md-3">
          <StatCard
            label={t('complianceScore', language)}
            value={`${complianceScore}%`}
            icon={<i className="bi bi-shield-check fs-4" />}
            variant={
              complianceScore >= 80 ? 'success' : complianceScore >= 60 ? 'warning' : 'danger'
            }
          />
        </div>
        <div className="col-md-3">
          <StatCard
            label={t('totalRecords', language)}
            value={totalRecords.toLocaleString()}
            icon={<i className="bi bi-database fs-4" />}
            variant="primary"
          />
        </div>
        <div className="col-md-3">
          <StatCard
            label={t('estimatedStorage', language)}
            value={`${totalSize.toFixed(1)} MB`}
            icon={<i className="bi bi-hdd fs-4" />}
            variant="info"
          />
        </div>
        <div className="col-md-3">
          <StatCard
            label={t('retentionRules', language)}
            value={adaptedRules.length.toString()}
            icon={<i className="bi bi-gear fs-4" />}
            variant="default"
          />
        </div>
      </div>

      {/* Retention Rules */}
      <Card title={t('retentionRules', language)} className="mb-4">
        <div className="table-responsive">
          <table className="table table-hover">
            <thead>
              <tr>
                <th>{t('dataType', language)}</th>
                <th>{t('retentionDays', language)}</th>
                <th>{t('action', language)}</th>
                <th>{t('tableActions', language)}</th>
              </tr>
            </thead>
            <tbody>
              {adaptedRules.map(({ key, label, icon, rule }) => {
                return (
                  <tr key={key}>
                    <td>
                      <div className="d-flex align-items-center">
                        <i className={cn('bi me-2', icon)} />
                        <strong>{label}</strong>
                      </div>
                    </td>
                    <td>
                      {rule.retention_days > 0 ? (
                        <span>
                          {rule.retention_days} {t('days', language)}
                        </span>
                      ) : (
                        <span className="text-muted">{t('notSet', language)}</span>
                      )}
                    </td>
                    <td>
                      <Badge
                        variant={
                          rule.action === 'delete'
                            ? 'danger'
                            : rule.action === 'anonymize'
                              ? 'warning'
                              : 'info'
                        }
                      >
                        {t(
                          `action${rule.action.charAt(0).toUpperCase() + rule.action.slice(1)}`,
                          language
                        )}
                      </Badge>
                    </td>
                    <td>
                      <Button
                        variant="outline-primary"
                        size="sm"
                        onClick={() => handleOpenEdit(key)}
                      >
                        <i className="bi bi-pencil" />
                      </Button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </Card>

      {/* Storage Estimates */}
      <Card title={t('storageEstimates', language)} className="mb-4">
        <div className="table-responsive">
          <table className="table table-sm">
            <thead>
              <tr>
                <th>{t('dataType', language)}</th>
                <th>{t('recordCount', language)}</th>
                <th>{t('estimatedSize', language)}</th>
              </tr>
            </thead>
            <tbody>
              {storage.map((s) => (
                <tr key={s.data_type}>
                  <td>
                    <div className="d-flex align-items-center">
                      <i className={cn('bi me-2', getDataTypeIcon(s.data_type))} />
                      {getStorageEstimateLabel(s.data_type, language)}
                    </div>
                  </td>
                  <td>{s.record_count.toLocaleString()}</td>
                  <td>{s.estimated_size_mb.toFixed(2)} MB</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      {/* Cleanup History */}
      <Card title={t('cleanupHistory', language)}>
        {history.length === 0 ? (
          <EmptyState icon="bi-clock-history" title={t('noCleanupHistory', language)} />
        ) : (
          <div className="table-responsive">
            <table className="table table-hover">
              <thead>
                <tr>
                  <th>{t('executedAt', language)}</th>
                  <th>{t('cleanupType', language)}</th>
                  <th>{t('recordsDeleted', language)}</th>
                  <th>{t('status', language)}</th>
                </tr>
              </thead>
              <tbody>
                {history.map((h, index) => (
                  <tr key={index}>
                    <td>{formatDateTime(h.executed_at)}</td>
                    <td>{h.cleanup_type}</td>
                    <td>{h.records_deleted.toLocaleString()}</td>
                    <td>
                      <Badge variant={h.status === 'success' ? 'success' : 'danger'}>
                        {h.status}
                      </Badge>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {/* Edit Rule Modal */}
      <Modal
        isOpen={showEditModal}
        onClose={() => setShowEditModal(false)}
        title={t('editRetentionRule', language)}
        size="md"
        footer={
          <>
            <Button variant="secondary" onClick={() => setShowEditModal(false)}>
              {t('cancel', language)}
            </Button>
            <Button variant="primary" onClick={handleSaveRule}>
              {t('save', language)}
            </Button>
          </>
        }
      >
        <form
          onSubmit={(e) => {
            e.preventDefault();
            handleSaveRule();
          }}
        >
          <div className="row g-3">
            <div className="col-12">
              <label className="form-label">{t('retentionDays', language)}</label>
              <input
                type="number"
                className="form-control"
                value={editDays}
                onChange={(e) => setEditDays(parseInt(e.target.value) || 0)}
                min={1}
                max={3650}
              />
            </div>
            <div className="col-12">
              <label className="form-label">{t('action', language)}</label>
              <select
                className="form-select"
                value={editAction}
                onChange={(e) =>
                  setEditAction(e.target.value as 'delete' | 'archive' | 'anonymize')
                }
              >
                <option value="delete">{t('actionDelete', language)}</option>
                <option value="archive">{t('actionArchive', language)}</option>
                <option value="anonymize">{t('actionAnonymize', language)}</option>
              </select>
            </div>
          </div>
        </form>
      </Modal>

      {/* Preview Modal */}
      <Modal
        isOpen={showPreviewModal}
        onClose={() => setShowPreviewModal(false)}
        title={t('cleanupPreview', language)}
        size="lg"
        footer={
          <>
            <Button variant="secondary" onClick={() => setShowPreviewModal(false)}>
              {t('cancel', language)}
            </Button>
            <Button variant="danger" onClick={handleExecuteCleanup} loading={isRunning}>
              {t('executeCleanup', language)}
            </Button>
          </>
        }
      >
        {previewResult ? (
          <CleanupPreviewContent report={previewResult} />
        ) : (
          <EmptyState icon="bi-hourglass" title={t('loading', language)} />
        )}
      </Modal>
    </div>
  );
};
