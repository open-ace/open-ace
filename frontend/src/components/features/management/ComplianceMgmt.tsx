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
import type { Language } from '@/types';
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
  PageRefreshControl,
  useToast,
  DatePicker,
} from '@/components/common';
import { useConfirm } from '@/components/common';
import { ReportPreviewModal } from './ReportPreviewModal';
import { CleanupPreviewContent } from '@/components/features/compliance/CleanupPreviewContent';
import { formatDate, formatDateTime, createMatcherConfig } from '@/utils';
import { getReportTypeName, getReportTypeDesc } from '@/utils/compliance';
import {
  complianceApi,
  type ReportType,
  type SavedReport,
  type RetentionRule,
  type RetentionHistory,
  type StorageEstimate,
  type RetentionReport,
} from '@/api';
import { usePageRefresh } from '@/hooks';

const FORMAT_OPTIONS = [
  { value: 'json', label: 'JSON' },
  { value: 'csv', label: 'CSV' },
  { value: 'html', label: 'HTML' },
  { value: 'excel', label: 'Excel' },
];

/**
 * Data type metadata mapping table
 * Maps backend retention rule keys to display labels, icons, and storage estimate keys
 * Synchronized with backend DEFAULT_RULES in app/modules/compliance/retention.py
 */
const DATA_TYPE_META: Record<
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
    storageEstimateKey: 'audit_logs',
  },
  quota_alerts: {
    i18nKey: 'dataTypeQuotaAlerts',
    icon: 'bi-bell',
    fallbackLabel: 'Quota Alerts',
    storageEstimateKey: 'quota_alerts',
  },
  sessions: {
    i18nKey: 'dataTypeSessions',
    icon: 'bi-chat-square',
    fallbackLabel: 'Sessions',
    storageEstimateKey: 'sessions',
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
  users: {
    i18nKey: 'dataTypeUsers',
    icon: 'bi-person',
    fallbackLabel: 'Users',
    storageEstimateKey: 'users',
  },
};

/**
 * Format snake_case key to Title Case for fallback display
 * Example: 'audit_logs' -> 'Audit Logs'
 */
function formatDataTypeKey(key: string): string {
  return key
    .split('_')
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(' ');
}

/**
 * Get display label for a data type key
 * Uses i18n translation if available, otherwise uses fallback label or formatted key
 */
function getDataTypeLabel(key: string, language: Language): string {
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
function getDataTypeIcon(key: string): string {
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

type TabType = 'reports' | 'retention';

export const ComplianceMgmt: React.FC = () => {
  const language = useLanguage();
  const toast = useToast();
  const [activeTab, setActiveTab] = useState<TabType>('reports');

  // --- Page Refresh Control ---
  const pageRefresh = usePageRefresh({
    page: '/manage/compliance',
    refreshKey: createMatcherConfig([['compliance']], 'prefix'),
    interval: 0, // No auto refresh - manual only for compliance data
    enabled: false,
    onRefresh: () => {
      fetchReportsData();
      fetchRetentionData();
    },
  });

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
  const [showCleanupPreviewModal, setShowCleanupPreviewModal] = useState(false);
  const [showReportPreviewModal, setShowReportPreviewModal] = useState(false);
  const [editingRule, setEditingRule] = useState<string | null>(null);
  const [editDays, setEditDays] = useState(90);
  const [editAction, setEditAction] = useState<'delete' | 'archive' | 'anonymize'>('delete');
  const [previewResult, setPreviewResult] = useState<RetentionReport | null>(null);
  const [isRunning, setIsRunning] = useState(false);

  // Report preview state
  const [previewHtmlContent, setPreviewHtmlContent] = useState<string>('');
  const [previewReportType, setPreviewReportType] = useState<string>('');
  const [previewReportId, setPreviewReportId] = useState<string>('');
  const [isPreviewLoading, setIsPreviewLoading] = useState(false);

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
        format: format as 'json' | 'csv' | 'html' | 'excel',
        language: language as 'en' | 'zh' | 'ja' | 'ko',
      });

      // Handle different formats
      if (format === 'html') {
        // Preview HTML content
        setPreviewHtmlContent(report as string);
        setPreviewReportType(selectedType);
        setPreviewReportId('');
        setShowReportPreviewModal(true);
      } else if (format === 'excel') {
        // Download Excel file
        const blob = report as Blob;
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        const timestamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
        a.download = `compliance_report_${selectedType}_${timestamp}.xlsx`;
        a.click();
        URL.revokeObjectURL(url);
      } else {
        // JSON or CSV - download
        const isCsv = format === 'csv';
        const blob = new Blob([isCsv ? (report as string) : JSON.stringify(report, null, 2)], {
          type: isCsv ? 'text/csv' : 'application/json',
        });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `compliance_report_${selectedType}_${startDate}_${endDate}.${format}`;
        a.click();
        URL.revokeObjectURL(url);
      }

      // Refresh saved reports
      const reports = await complianceApi.getSavedReports();
      setSavedReports(reports);
    } catch (err) {
      const errorMessage =
        err instanceof Error ? (err as Error).message : 'Failed to generate report';
      setReportsError(errorMessage);
      console.error('Failed to generate report:', err);
    } finally {
      setIsGenerating(false);
    }
  };

  // Preview saved report
  const handlePreviewSavedReport = async (reportId: string, reportType: string) => {
    setIsPreviewLoading(true);
    try {
      const htmlContent = await complianceApi.getSavedReport(
        reportId,
        'html',
        language as 'en' | 'zh' | 'ja' | 'ko'
      );
      setPreviewHtmlContent(htmlContent as string);
      setPreviewReportType(reportType);
      setPreviewReportId(reportId);
      setShowReportPreviewModal(true);
    } catch (err) {
      console.error('Failed to preview report:', err);
    } finally {
      setIsPreviewLoading(false);
    }
  };

  // Download saved report in different formats
  const handleDownloadSavedReport = async (
    reportId: string,
    _reportType: string,
    downloadFormat: 'json' | 'csv' | 'html' | 'excel'
  ) => {
    try {
      const report = await complianceApi.getSavedReport(
        reportId,
        downloadFormat,
        language as 'en' | 'zh' | 'ja' | 'ko'
      );

      if (downloadFormat === 'excel') {
        const blob = report as Blob;
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        const timestamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
        a.download = `compliance_report_${reportId}_${timestamp}.xlsx`;
        a.click();
        URL.revokeObjectURL(url);
      } else if (downloadFormat === 'html') {
        const htmlContent = report as string;
        const blob = new Blob([htmlContent], { type: 'text/html' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        const timestamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
        a.download = `compliance_report_${reportId}_${timestamp}.html`;
        a.click();
        URL.revokeObjectURL(url);
      } else {
        const isCsv = downloadFormat === 'csv';
        const blob = new Blob([isCsv ? (report as string) : JSON.stringify(report, null, 2)], {
          type: isCsv ? 'text/csv' : 'application/json',
        });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `compliance_report_${reportId}.${downloadFormat}`;
        a.click();
        URL.revokeObjectURL(url);
      }
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
      setShowCleanupPreviewModal(true);
    } catch (err) {
      console.error('Failed to preview cleanup:', err);
      toast.error(t('cleanupPreviewFailed', language));
    } finally {
      setIsRunning(false);
    }
  };

  const confirm = useConfirm();
  const handleExecuteCleanup = async () => {
    if (!(await confirm({ message: t('confirmCleanup', language), variant: 'danger' }))) return;
    setIsRunning(true);
    try {
      const result = await complianceApi.runCleanup(false);
      fetchRetentionData();
      // HTTP 200 does not guarantee the cleanup fully succeeded: per-rule
      // failures are collected in report.errors. When present, keep the preview
      // modal open and show the actual execute report so the user can inspect
      // the error details in CleanupPreviewContent (the report was not
      // persisted on save failure, so it won't appear in the history table).
      if (result?.errors?.length) {
        setPreviewResult(result);
        toast.warning(
          t('cleanupCompletedWithErrors', language),
          t('cleanupErrorsDescription', language, { count: result.errors.length })
        );
      } else {
        setShowCleanupPreviewModal(false);
        toast.success(t('cleanupSuccess', language));
      }
    } catch (err) {
      console.error('Failed to execute cleanup:', err);
      toast.error(t('cleanupFailed', language));
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
        {/* Report Type Selection */}
        <Card title={t('selectReportType', language)} className="mb-4">
          <div className="row g-3">
            {reportTypes.map((type) => (
              <div key={type.type} className="col-md-4">
                <div
                  className={cn(
                    'report-type-card p-3 border rounded cursor-pointer',
                    selectedType === type.type && 'report-type-card-selected border-primary'
                  )}
                  onClick={() => setSelectedType(type.type)}
                  style={{ cursor: 'pointer' }}
                >
                  <div className="d-flex align-items-center">
                    <i className={cn('bi me-2', getReportIcon(type.type))} />
                    <strong>{getReportTypeName(type.type, language, type.name)}</strong>
                  </div>
                  <small className="text-muted d-block mt-1">
                    {getReportTypeDesc(type.type, language) || type.description}
                  </small>
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
              <DatePicker
                value={startDate}
                onChange={setStartDate}
              />
            </div>
            <div className="col-md-3">
              <label className="form-label">{t('endDate', language)}</label>
              <DatePicker
                value={endDate}
                onChange={setEndDate}
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
                        <strong>
                          {getReportTypeName(report.report_type, language, report.report_type)}
                        </strong>
                      </td>
                      <td>
                        <Badge variant="secondary">
                          {getReportTypeName(report.report_type, language, report.report_type)}
                        </Badge>
                      </td>
                      <td>
                        <small>{formatDateTime(report.generated_at, language)}</small>
                      </td>
                      <td>
                        <small>
                          {formatDate(report.period_start, 'short', language)} -{' '}
                          {formatDate(report.period_end, 'short', language)}
                        </small>
                      </td>
                      <td>
                        <div className="btn-group btn-group-sm">
                          <Button
                            variant="outline-info"
                            size="sm"
                            onClick={() =>
                              handlePreviewSavedReport(report.report_id, report.report_type)
                            }
                            loading={isPreviewLoading}
                            title={t('preview', language)}
                          >
                            <i className="bi bi-eye" />
                          </Button>
                          <Button
                            variant="outline-primary"
                            size="sm"
                            onClick={() =>
                              handleDownloadSavedReport(
                                report.report_id,
                                report.report_type,
                                'json'
                              )
                            }
                          >
                            <i className="bi bi-filetype-json" />
                          </Button>
                          <Button
                            variant="outline-secondary"
                            size="sm"
                            onClick={() =>
                              handleDownloadSavedReport(report.report_id, report.report_type, 'csv')
                            }
                          >
                            <i className="bi bi-filetype-csv" />
                          </Button>
                          <Button
                            variant="outline-warning"
                            size="sm"
                            onClick={() =>
                              handleDownloadSavedReport(
                                report.report_id,
                                report.report_type,
                                'html'
                              )
                            }
                          >
                            <i className="bi bi-filetype-html" />
                          </Button>
                          <Button
                            variant="outline-success"
                            size="sm"
                            onClick={() =>
                              handleDownloadSavedReport(
                                report.report_id,
                                report.report_type,
                                'excel'
                              )
                            }
                          >
                            <i className="bi bi-filetype-xlsx" />
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

    // Adapt backend rules to table data
    const adaptedRules = adaptRulesToTableData(rules, language);

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
                                : rule.action === 'archive'
                                  ? 'info'
                                  : 'secondary'
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
                      <td>{formatDateTime(h.executed_at, language)}</td>
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
        </Modal>

        {/* Cleanup Preview Modal */}
        <Modal
          isOpen={showCleanupPreviewModal}
          onClose={() => setShowCleanupPreviewModal(false)}
          title={t('cleanupPreview', language)}
          size="lg"
          footer={
            <>
              <Button variant="secondary" onClick={() => setShowCleanupPreviewModal(false)}>
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
              <CleanupPreviewContent report={previewResult} />
            </div>
          )}
        </Modal>

        {/* Report Preview Modal */}
        <ReportPreviewModal
          isOpen={showReportPreviewModal}
          onClose={() => setShowReportPreviewModal(false)}
          htmlContent={previewHtmlContent}
          reportType={previewReportType}
          reportId={previewReportId}
          onDownload={(fmt) => {
            if (previewReportId) {
              handleDownloadSavedReport(previewReportId, previewReportType, fmt);
            } else {
              // For newly generated report without saved ID, generate and download
              handleGenerate();
            }
          }}
          isDownloading={isGenerating}
        />
      </>
    );
  };

  return (
    <div className="compliance-mgmt">
      {/* Header */}
      <div className="d-flex justify-content-between align-items-center mb-3">
        <h2>{t('complianceManagement', language)}</h2>
        <div className="d-flex gap-2 align-items-center">
          {/* Page Refresh Control */}
          <PageRefreshControl
            refresh={pageRefresh}
            compact={true}
            showAutoRefreshToggle={false}
            showIntervalSelector={false}
            showLastRefreshTime={true}
          />
          {activeTab === 'retention' && (
            <>
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
            </>
          )}
        </div>
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
