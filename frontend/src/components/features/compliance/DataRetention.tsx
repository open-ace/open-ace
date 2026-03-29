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
} from '@/api';
import { formatDateTime } from '@/utils';

const DATA_TYPES = [
  { value: 'messages', label: 'Messages', defaultDays: 90 },
  { value: 'sessions', label: 'Sessions', defaultDays: 180 },
  { value: 'audit_logs', label: 'Audit Logs', defaultDays: 365 },
  { value: 'usage_stats', label: 'Usage Stats', defaultDays: 365 },
  { value: 'alerts', label: 'Alerts', defaultDays: 30 },
];

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
  const [editAction, setEditAction] = useState<'delete' | 'archive'>('delete');
  const [previewResult, setPreviewResult] = useState<Record<string, unknown> | null>(null);
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
            value={Object.keys(rules).length.toString()}
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
              {DATA_TYPES.map((dt) => {
                const rule = rules[dt.value];
                return (
                  <tr key={dt.value}>
                    <td>
                      <strong>{dt.label}</strong>
                    </td>
                    <td>
                      {rule ? (
                        <span>
                          {rule.retention_days} {t('days', language)}
                        </span>
                      ) : (
                        <span className="text-muted">{t('notSet', language)}</span>
                      )}
                    </td>
                    <td>
                      {rule ? (
                        <Badge variant={rule.action === 'delete' ? 'danger' : 'info'}>
                          {rule.action}
                        </Badge>
                      ) : (
                        <span className="text-muted">-</span>
                      )}
                    </td>
                    <td>
                      <Button
                        variant="outline-primary"
                        size="sm"
                        onClick={() => handleOpenEdit(dt.value)}
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
                  <td>{s.data_type}</td>
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
              onChange={(e) => setEditAction(e.target.value as 'delete' | 'archive')}
            >
              <option value="delete">{t('delete', language)}</option>
              <option value="archive">{t('archive', language)}</option>
            </select>
          </div>
        </div>
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
        {previewResult && (
          <div>
            <p className="text-muted mb-3">{t('cleanupPreviewDescription', language)}</p>
            <pre className="bg-light p-3 rounded">{JSON.stringify(previewResult, null, 2)}</pre>
          </div>
        )}
      </Modal>
    </div>
  );
};
