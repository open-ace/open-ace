/**
 * ComplianceMgmt Component - Combined Compliance Report and Data Retention
 *
 * Features:
 * - Tab navigation between Reports and Retention views
 * - Reports: Generate and download compliance reports
 * - Retention: Manage data retention rules and cleanup
 */

import React, { useState, useEffect, useCallback } from 'react';
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
import { formatDateTime } from '@/utils';
import {
  complianceApi,
  type ReportType,
  type SavedReport,
  type RetentionRule,
  type RetentionHistory,
  type StorageEstimate,
} from '@/api';

const FORMAT_OPTIONS = [
  { value: 'json', label: 'JSON' },
  { value: 'csv', label: 'CSV' },
];

const DATA_TYPES = [
  { value: 'messages', label: 'Messages', defaultDays: 90 },
  { value: 'sessions', label: 'Sessions', defaultDays: 180 },
  { value: 'audit_logs', label: 'Audit Logs', defaultDays: 365 },
  { value: 'usage_stats', label: 'Usage Stats', defaultDays: 365 },
  { value: 'alerts', label: 'Alerts', defaultDays: 30 },
];

type TabType = 'reports' | 'retention';

export const ComplianceMgmt: React.FC = () => {
  const language = useLanguage();
  const [activeTab, setActiveTab] = useState<TabType>('reports');

  // --- Reports State ---
  const [reportTypes, setReportTypes] = useState<ReportType[]>([]);
  const [savedReports, setSavedReports] = useState<SavedReport[]>([]);
  const [reportsLoading, setReportsLoading] = useState(true);
  const [isGenerating, setIsGenerating] = useState(false);
  const [reportsError, setReportsError] = useState<string | null>(null);

  const [selectedType, setSelectedType] = useState('');
  const [startDate, setStartDate] = useState('');
  const [endDate, setEndDate] = useState('');
  const [format, setFormat] = useState('json');

  // --- Retention State ---
  const [rules, setRules] = useState<Record<string, RetentionRule>>({});
  const [history, setHistory] = useState<RetentionHistory[]>([]);
  const [storage, setStorage] = useState<StorageEstimate[]>([]);
  const [complianceStatus, setComplianceStatus] = useState<Record<string, unknown>>({});
  const [retentionLoading, setRetentionLoading] = useState(true);
  const [retentionError, setRetentionError] = useState<string | null>(null);

  const [showEditModal, setShowEditModal] = useState(false);
  const [showPreviewModal, setShowPreviewModal] = useState(false);
  const [editingRule, setEditingRule] = useState<string | null>(null);
  const [editDays, setEditDays] = useState(90);
  const [editAction, setEditAction] = useState<'delete' | 'archive'>('delete');
  const [previewResult, setPreviewResult] = useState<Record<string, unknown> | null>(null);
  const [isRunning, setIsRunning] = useState(false);

  // Initialize dates for reports
  useEffect(() => {
    const end = new Date();
    const start = new Date(Date.now() - 30 * 24 * 60 * 60 * 1000);
    setEndDate(end.toISOString().split('T')[0]);
    setStartDate(start.toISOString().split('T')[0]);
  }, []);

  // --- Fetch Reports Data ---
  const fetchReportsData = useCallback(async () => {
    setReportsLoading(true);
    setReportsError(null);
    try {
      const [types, reports] = await Promise.all([
        complianceApi.getReportTypes(),
        complianceApi.getSavedReports(),
      ]);
      setReportTypes(types);
      setSavedReports(reports);
      if (types.length > 0) {
        setSelectedType(types[0].type);
      }
    } catch (err) {
      const errorMessage = err instanceof Error ? (err as Error).message : 'Failed to fetch data';
      setReportsError(errorMessage);
    } finally {
      setReportsLoading(false);
    }
  }, []);

  // --- Fetch Retention Data ---
  const fetchRetentionData = useCallback(async () => {
    setRetentionLoading(true);
    setRetentionError(null);
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
      setRetentionError(errorMessage);
    } finally {
      setRetentionLoading(false);
    }
  }, []);

  // Load data when tab changes
  useEffect(() => {
    if (activeTab === 'reports') {
      fetchReportsData();
    } else {
      fetchRetentionData();
    }
  }, [activeTab, fetchReportsData, fetchRetentionData]);

  // --- Reports Handlers ---
  const handleGenerate = async () => {
    if (!selectedType) return;
    setIsGenerating(true);
    try {
      const report = await complianceApi.generateReport({
        report_type: selectedType,
        period_start: startDate,
        period_end: endDate,
        format: format as 'json' | 'csv',
      });

      // Download the report
      if (format === 'json') {
        const blob = new Blob([JSON.stringify(report, null, 2)], {
          type: 'application/json',
        });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `compliance_report_${selectedType}_${startDate}_${endDate}.json`;
        a.click();
        URL.revokeObjectURL(url);
      }

      // Refresh saved reports
      const reports = await complianceApi.getSavedReports();
      setSavedReports(reports);
    } catch (err) {
      console.error('Failed to generate report:', err);
    } finally {
      setIsGenerating(false);
    }
  };

  const handleDownload = async (reportId: string, reportFormat: 'json' | 'csv') => {
    try {
      const report = await complianceApi.getSavedReport(reportId, reportFormat);
      const blob = new Blob(
        [reportFormat === 'json' ? JSON.stringify(report, null, 2) : (report as unknown as string)],
        { type: reportFormat === 'json' ? 'application/json' : 'text/csv' }
      );
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `compliance_report_${reportId}.${reportFormat}`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      console.error('Failed to download report:', err);
    }
  };

  // --- Retention Handlers ---
  const handleOpenEdit = (dataType: string) => {
    const rule = rules[dataType];
    setEditingRule(dataType);
    setEditDays(rule?.retention_days || 90);
    setEditAction(rule?.action ?? 'delete');
    setShowEditModal(true);
  };

  const handleSaveRule = async () => {
    if (!editingRule) return;
    try {
      await complianceApi.setRetentionRule({
        data_type: editingRule,
        retention_days: editDays,
        action: editAction,
      });
      setShowEditModal(false);
      fetchRetentionData();
    } catch (err) {
      console.error('Failed to save rule:', err);
    }
  };

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

  const handleExecuteCleanup = async () => {
    if (!window.confirm(t('confirmCleanup', language))) return;
    setIsRunning(true);
    try {
      await complianceApi.runCleanup(false);
      setShowPreviewModal(false);
      fetchRetentionData();
    } catch (err) {
      console.error('Failed to execute cleanup:', err);
    } finally {
      setIsRunning(false);
    }
  };

  // --- Render Reports Tab ---
  const renderReportsTab = () => {
    if (reportsLoading) {
      return <Loading size="lg" text={t('loading', language)} />;
    }

    if (reportsError) {
      return <Error message={reportsError} onRetry={fetchReportsData} />;
    }

    return (
      <>
        {/* Compliance Rules Overview */}
        <Card title={t('complianceRules', language)} className="mb-4">
          <div className="compliance-rules-list">
            {Object.keys(rules).length === 0 ? (
              <EmptyState icon="bi-shield-check" title={t('noComplianceRules', language)} />
            ) : (
              <div className="table-responsive">
                <table className="table table-sm">
                  <thead>
                    <tr>
                      <th>{t('dataType', language)}</th>
                      <th>{t('retentionDays', language)}</th>
                      <th>{t('action', language)}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {Object.entries(rules)
                      .slice(0, 5)
                      .map(([dataType, rule]) => (
                        <tr key={dataType}>
                          <td>{dataType}</td>
                          <td>
                            {(rule as { retention_days: number }).retention_days}{' '}
                            {t('days', language)}
                          </td>
                          <td>
                            <Badge
                              variant={
                                (rule as { action: string }).action === 'delete' ? 'danger' : 'info'
                              }
                            >
                              {(rule as { action: string }).action}
                            </Badge>
                          </td>
                        </tr>
                      ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </Card>

        {/* Report Type Selection */}
        <Card title={t('selectReportType', language)} className="mb-4">
          <div className="row g-3">
            {reportTypes.map((type) => (
              <div key={type.type} className="col-md-4">
                <div
                  className={cn(
                    'report-type-card p-3 border rounded cursor-pointer',
                    selectedType === type.type && 'border-primary bg-light'
                  )}
                  onClick={() => setSelectedType(type.type)}
                  style={{ cursor: 'pointer' }}
                >
                  <div className="d-flex align-items-center">
                    <i className={cn('bi me-2', getReportIcon(type.type))} />
                    <strong>{type.name}</strong>
                  </div>
                  <small className="text-muted d-block mt-1">{type.description}</small>
                </div>
              </div>
            ))}
          </div>
        </Card>

        {/* Generate Report */}
        <Card title={t('generateReport', language)} className="mb-4">
          <div className="row g-3">
            <div className="col-md-3">
              <label className="form-label">{t('startDate', language)}</label>
              <input
                type="date"
                className="form-control"
                value={startDate}
                onChange={(e) => setStartDate(e.target.value)}
              />
            </div>
            <div className="col-md-3">
              <label className="form-label">{t('endDate', language)}</label>
              <input
                type="date"
                className="form-control"
                value={endDate}
                onChange={(e) => setEndDate(e.target.value)}
              />
            </div>
            <div className="col-md-3">
              <label className="form-label">{t('format', language)}</label>
              <Select options={FORMAT_OPTIONS} value={format} onChange={setFormat} />
            </div>
            <div className="col-md-3 d-flex align-items-end">
              <Button
                variant="primary"
                onClick={handleGenerate}
                loading={isGenerating}
                disabled={!selectedType || !startDate || !endDate}
              >
                <i className="bi bi-file-earmark-arrow-down me-1" />
                {t('generate', language)}
              </Button>
            </div>
          </div>
        </Card>

        {/* Saved Reports */}
        <Card title={t('savedReports', language)}>
          {savedReports.length === 0 ? (
            <EmptyState icon="bi-file-earmark-text" title={t('noSavedReports', language)} />
          ) : (
            <div className="table-responsive">
              <table className="table table-hover">
                <thead>
                  <tr>
                    <th>{t('reportName', language)}</th>
                    <th>{t('type', language)}</th>
                    <th>{t('generatedAt', language)}</th>
                    <th>{t('period', language)}</th>
                    <th>{t('tableActions', language)}</th>
                  </tr>
                </thead>
                <tbody>
                  {savedReports.map((report) => (
                    <tr key={report.report_id}>
                      <td>
                        <strong>{report.report_type}</strong>
                      </td>
                      <td>
                        <Badge variant="secondary">{report.report_type}</Badge>
                      </td>
                      <td>
                        <small>{formatDateTime(report.generated_at)}</small>
                      </td>
                      <td>
                        <small>
                          {report.period_start} - {report.period_end}
                        </small>
                      </td>
                      <td>
                        <div className="btn-group btn-group-sm">
                          <Button
                            variant="outline-primary"
                            size="sm"
                            onClick={() => handleDownload(report.report_id, 'json')}
                          >
                            <i className="bi bi-filetype-json" />
                          </Button>
                          <Button
                            variant="outline-secondary"
                            size="sm"
                            onClick={() => handleDownload(report.report_id, 'csv')}
                          >
                            <i className="bi bi-filetype-csv" />
                          </Button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      </>
    );
  };

  // --- Render Retention Tab ---
  const renderRetentionTab = () => {
    if (retentionLoading) {
      return <Loading size="lg" text={t('loading', language)} />;
    }

    if (retentionError) {
      return <Error message={retentionError} onRetry={fetchRetentionData} />;
    }

    const totalRecords = storage.reduce((sum, s) => sum + s.record_count, 0);
    const totalSize = storage.reduce((sum, s) => sum + s.estimated_size_mb, 0);
    const complianceScore =
      (complianceStatus as { compliance_score?: number }).compliance_score ?? 100;

    return (
      <>
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
      </>
    );
  };

  return (
    <div className="compliance-mgmt">
      {/* Header */}
      <div className="d-flex justify-content-between align-items-center mb-3">
        <h2>{t('complianceManagement', language)}</h2>
        {activeTab === 'retention' && (
          <div className="d-flex gap-2">
            <Button
              variant="outline-secondary"
              size="sm"
              onClick={handlePreview}
              loading={isRunning}
            >
              <i className="bi bi-eye me-1" />
              {t('preview', language)}
            </Button>
            <Button variant="danger" size="sm" onClick={handleExecuteCleanup} loading={isRunning}>
              <i className="bi bi-trash me-1" />
              {t('runCleanup', language)}
            </Button>
          </div>
        )}
      </div>

      {/* Tab Navigation */}
      <ul className="nav nav-tabs mb-3">
        <li className="nav-item">
          <button
            className={cn('nav-link', activeTab === 'reports' && 'active')}
            onClick={() => setActiveTab('reports')}
          >
            <i className="bi bi-file-earmark-text me-1" />
            {t('complianceReport', language)}
          </button>
        </li>
        <li className="nav-item">
          <button
            className={cn('nav-link', activeTab === 'retention' && 'active')}
            onClick={() => setActiveTab('retention')}
          >
            <i className="bi bi-database me-1" />
            {t('dataRetention', language)}
          </button>
        </li>
      </ul>

      {/* Tab Content */}
      {activeTab === 'reports' ? renderReportsTab() : renderRetentionTab()}
    </div>
  );
};

function getReportIcon(type: string): string {
  const icons: Record<string, string> = {
    usage_summary: 'bi-graph-up',
    user_activity: 'bi-people',
    audit_trail: 'bi-journal-text',
    data_access: 'bi-database',
    security: 'bi-shield',
    quota_usage: 'bi-sliders',
    comprehensive: 'bi-file-earmark-text',
  };
  return icons[type] ?? 'bi-file-text';
}
