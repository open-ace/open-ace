/**
 * EnterpriseReport Component - Enterprise analytics report page
 *
 * Features:
 * - Key metrics cards (summary)
 * - Efficiency metrics cards
 * - Trends analysis table
 * - Anomalies detection table
 * - Breakdown by tool table
 * - Breakdown by host table
 * - CSV/JSON export with admin permission control
 * - Cost/ROI placeholder notice
 *
 * Issue #3078: Management UI for enterprise analytics report
 */

import React, { useState, useMemo } from 'react';
import { cn } from '@/utils';
import { useLanguage } from '@/store';
import { t, type Language } from '@/i18n';
import {
  Card,
  StatCard,
  Button,
  Error,
  EmptyState,
  DatePicker,
  Modal,
  useToast,
} from '@/components/common';
import { formatTokens } from '@/utils';
import { useEnterpriseReport, useEfficiencyMetrics, useAuth } from '@/hooks';
import { analysisApi } from '@/api';
import { isAdmin } from '@/utils/permissions';
import type {
  EnterpriseReportResponse,
  EfficiencyMetricsResponse,
  TrendItem,
  AnomalyItem,
  ToolBreakdown,
  HostBreakdown,
} from '@/api';

// Skeleton components
const MetricsSkeleton: React.FC<{ count?: number }> = ({ count = 6 }) => (
  <div className="row g-3 mb-4">
    {Array.from({ length: count }, (_, i) => (
      <div key={i} className="col-md-2">
        <div className="card">
          <div className="card-body">
            <div className="skeleton" style={{ height: 16, width: '60%', marginBottom: 8 }} />
            <div className="skeleton" style={{ height: 32, width: '80%', marginBottom: 4 }} />
            <div className="skeleton" style={{ height: 12, width: '40%' }} />
          </div>
        </div>
      </div>
    ))}
  </div>
);

const TableSkeleton: React.FC<{ rows?: number }> = ({ rows = 5 }) => (
  <div className="table-responsive">
    <table className="table table-sm">
      <thead>
        <tr>
          <th className="skeleton" style={{ height: 20, width: '20%' }} />
          <th className="skeleton" style={{ height: 20, width: '20%' }} />
          <th className="skeleton" style={{ height: 20, width: '20%' }} />
          <th className="skeleton" style={{ height: 20, width: '20%' }} />
          <th className="skeleton" style={{ height: 20, width: '20%' }} />
        </tr>
      </thead>
      <tbody>
        {Array.from({ length: rows }, (_, i) => (
          <tr key={i}>
            <td className="skeleton" style={{ height: 20 }} />
            <td className="skeleton" style={{ height: 20 }} />
            <td className="skeleton" style={{ height: 20 }} />
            <td className="skeleton" style={{ height: 20 }} />
            <td className="skeleton" style={{ height: 20 }} />
          </tr>
        ))}
      </tbody>
    </table>
  </div>
);

// JSON Preview Modal Component
interface JsonPreviewModalProps {
  isOpen: boolean;
  onClose: () => void;
  data: object | null;
  title: string;
}

const JsonPreviewModal: React.FC<JsonPreviewModalProps> = ({ isOpen, onClose, data, title }) => {
  const language = useLanguage();
  const [copySuccess, setCopySuccess] = useState(false);

  const jsonString = useMemo(() => {
    if (!data) return '';
    return JSON.stringify(data, null, 2);
  }, [data]);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(jsonString);
      setCopySuccess(true);
      setTimeout(() => setCopySuccess(false), 2000);
    } catch {
      // Fallback for older browsers
      const textArea = document.createElement('textarea');
      textArea.value = jsonString;
      document.body.appendChild(textArea);
      textArea.select();
      document.execCommand('copy');
      document.body.removeChild(textArea);
      setCopySuccess(true);
      setTimeout(() => setCopySuccess(false), 2000);
    }
  };

  const handleDownload = () => {
    if (!data) return;
    const blob = new Blob([jsonString], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `analytics_report_${new Date().toISOString().split('T')[0]}.json`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };

  if (!isOpen) return null;

  return (
    <Modal isOpen={isOpen} onClose={onClose} title={title} size="lg">
      <div className="mb-3">
        <div className="btn-group">
          <Button
            variant={copySuccess ? 'success' : 'outline-primary'}
            size="sm"
            onClick={handleCopy}
          >
            <i className={cn('bi me-1', copySuccess ? 'bi-check' : 'bi-clipboard')} />
            {copySuccess ? t('copySuccess', language) : t('copyToClipboard', language)}
          </Button>
          <Button variant="outline-primary" size="sm" onClick={handleDownload}>
            <i className="bi bi-download me-1" />
            {t('export', language)} .json
          </Button>
        </div>
      </div>
      <div
        className="json-preview"
        style={{
          maxHeight: '60vh',
          overflow: 'auto',
          backgroundColor: 'var(--bs-code-bg, #f8f9fa)',
          borderRadius: '4px',
          padding: '1rem',
        }}
      >
        <pre style={{ margin: 0, whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
          <code>{jsonString}</code>
        </pre>
      </div>
    </Modal>
  );
};

// Summary Cards Component
interface SummaryCardsProps {
  summary: EnterpriseReportResponse['summary'];
  language: Language;
}

/**
 * Format peak day value for display.
 * Handles both ISO format (YYYY-MM-DD) and legacy RFC 1123 format from historical data.
 */
const formatPeakDay = (value: string | null): string => {
  if (!value) return '-';
  // Handle RFC 1123 format (e.g., "Tue, 25 Aug 2026 00:00:00 GMT")
  if (value.includes('GMT') || value.includes(', ')) {
    try {
      const date = new Date(value);
      return date.toISOString().split('T')[0];
    } catch {
      return value;
    }
  }
  return value;
};

const SummaryCards: React.FC<SummaryCardsProps> = ({ summary, language }) => (
  <div className="row g-3 mb-4">
    <div className="col-md-2">
      <StatCard
        label={t('totalTokens', language)}
        value={formatTokens(summary.total_tokens)}
        icon={<i className="bi bi-cpu fs-4" />}
        variant="primary"
      />
    </div>
    <div className="col-md-2">
      <StatCard
        label={t('totalRequests', language)}
        value={summary.total_requests.toLocaleString()}
        icon={<i className="bi bi-chat-dots fs-4" />}
        variant="success"
      />
    </div>
    <div className="col-md-2">
      <StatCard
        label={t('activeTools', language)}
        value={summary.unique_tools.toString()}
        icon={<i className="bi bi-tools fs-4" />}
        variant="warning"
      />
    </div>
    <div className="col-md-2">
      <StatCard
        label={t('tableHost', language)}
        value={summary.unique_hosts.toString()}
        icon={<i className="bi bi-hdd fs-4" />}
        variant="info"
      />
    </div>
    <div className="col-md-2">
      <StatCard
        label={t('peakDay', language)}
        value={formatPeakDay(summary.peak_day)}
        icon={<i className="bi bi-calendar-check fs-4" />}
        variant="secondary"
        subtitle={
          summary.peak_tokens > 0
            ? `${t('peakTokens', language)}: ${formatTokens(summary.peak_tokens)}`
            : undefined
        }
      />
    </div>
    <div className="col-md-2">
      <StatCard
        label={t('average', language)}
        value={formatTokens(Math.round(summary.daily_average_tokens))}
        icon={<i className="bi bi-graph-up fs-4" />}
        variant="info"
        helpTooltip={t('avg', language)}
      />
    </div>
  </div>
);

// Efficiency Cards Component
interface EfficiencyCardsProps {
  efficiency: EfficiencyMetricsResponse;
  isLoading: boolean;
  language: Language;
}

const EfficiencyCards: React.FC<EfficiencyCardsProps> = ({ efficiency, isLoading, language }) => {
  if (isLoading) {
    return (
      <Card title={t('efficiencyMetrics', language)} className="mb-4">
        <MetricsSkeleton count={4} />
      </Card>
    );
  }

  if (!efficiency.efficiency_available) {
    return (
      <Card title={t('efficiencyMetrics', language)} className="mb-4">
        <EmptyState icon="bi-speedometer" title={t('noEfficiencyData', language)} />
      </Card>
    );
  }

  return (
    <Card title={t('efficiencyMetrics', language)} className="mb-4">
      <div className="row g-3">
        <div className="col-md-3">
          <StatCard
            label={t('outputRatio', language)}
            value={`${efficiency.output_ratio?.toFixed(1) ?? 0}%`}
            icon={<i className="bi bi-arrow-up-right fs-4" />}
            variant="primary"
            helpTooltip={t('outputRatioHelp', language)}
          />
        </div>
        <div className="col-md-3">
          <StatCard
            label={t('tokensPerRequest', language)}
            value={efficiency.tokens_per_request?.toFixed(0) ?? '0'}
            icon={<i className="bi bi-hash fs-4" />}
            variant="success"
          />
        </div>
        <div className="col-md-3">
          <StatCard
            label={t('outputPerRequest', language)}
            value={efficiency.output_per_request?.toFixed(0) ?? '0'}
            icon={<i className="bi bi-output fs-4" />}
            variant="info"
          />
        </div>
        <div className="col-md-3">
          <StatCard
            label={t('inputOutputRatio', language)}
            value={efficiency.input_output_ratio?.toFixed(2) ?? '0'}
            icon={<i className="bi bi-arrows fs-4" />}
            variant="warning"
            helpTooltip={t('inputOutputRatioHelp', language)}
          />
        </div>
      </div>
    </Card>
  );
};

// Trends Table Component
interface TrendsTableProps {
  trends: TrendItem[];
  language: Language;
}

const TrendsTable: React.FC<TrendsTableProps> = ({ trends, language }) => {
  if (trends.length === 0) {
    return (
      <Card title={t('trendAnalysis', language)} className="mb-4">
        <EmptyState icon="bi-graph-up" title={t('noTrendData', language)} />
      </Card>
    );
  }

  const getDirectionBadge = (direction: string) => {
    const badges: Record<string, string> = {
      up: 'bg-success',
      down: 'bg-danger',
      stable: 'bg-secondary',
    };
    return badges[direction] || 'bg-secondary';
  };

  const getDirectionIcon = (direction: string) => {
    const icons: Record<string, string> = {
      up: 'bi-arrow-up',
      down: 'bi-arrow-down',
      stable: 'bi-dash',
    };
    return icons[direction] || 'bi-dash';
  };

  return (
    <Card title={t('trendAnalysis', language)} className="mb-4">
      <div className="table-responsive">
        <table className="table table-sm table-hover">
          <thead>
            <tr>
              <th>{t('metric', language)}</th>
              <th>{t('direction', language)}</th>
              <th className="text-end">{t('changePercentage', language)}</th>
              <th className="text-end">{t('current', language)}</th>
              <th className="text-end">{t('previous', language)}</th>
              <th className="text-end">{t('confidence', language)}</th>
            </tr>
          </thead>
          <tbody>
            {trends.map((trend, index) => (
              <tr key={index}>
                <td>{trend.metric}</td>
                <td>
                  <span className={cn('badge', getDirectionBadge(trend.direction))}>
                    <i className={cn('bi', getDirectionIcon(trend.direction), 'me-1')} />
                    {trend.direction}
                  </span>
                </td>
                <td className="text-end">
                  <span
                    className={cn(trend.change_percentage > 0 ? 'text-success' : 'text-danger')}
                  >
                    {trend.change_percentage > 0 ? '+' : ''}
                    {trend.change_percentage.toFixed(1)}%
                  </span>
                </td>
                <td className="text-end">{trend.current_value.toLocaleString()}</td>
                <td className="text-end">{trend.previous_value.toLocaleString()}</td>
                <td className="text-end">{(trend.confidence * 100).toFixed(0)}%</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
};

// Anomalies Table Component
interface AnomaliesTableProps {
  anomalies: AnomalyItem[];
  language: Language;
}

const AnomaliesTable: React.FC<AnomaliesTableProps> = ({ anomalies, language }) => {
  if (anomalies.length === 0) {
    return (
      <Card title={t('anomalyDetection', language)} className="mb-4">
        <div className="text-center py-3">
          <i className="bi bi-check-circle text-success fs-4" />
          <p className="mb-0 text-muted">{t('noAnomaliesDetected', language)}</p>
        </div>
      </Card>
    );
  }

  const getSeverityBadge = (severity: string) => {
    const badges: Record<string, string> = {
      high: 'bg-danger',
      medium: 'bg-warning',
      low: 'bg-info',
    };
    return badges[severity] || 'bg-secondary';
  };

  const getTypeBadge = (type: string) => {
    const badges: Record<string, string> = {
      spike: 'bg-danger',
      drop: 'bg-info',
      unusual_pattern: 'bg-warning',
    };
    return badges[type] || 'bg-secondary';
  };

  return (
    <Card title={t('anomalyDetection', language)} className="mb-4">
      <div className="table-responsive">
        <table className="table table-sm table-hover">
          <thead>
            <tr>
              <th>{t('tableDate', language)}</th>
              <th>{t('type', language)}</th>
              <th>{t('metric', language)}</th>
              <th className="text-end">{t('expected', language)}</th>
              <th className="text-end">{t('actual', language)}</th>
              <th className="text-end">{t('deviation', language)}</th>
              <th>{t('severity', language)}</th>
            </tr>
          </thead>
          <tbody>
            {anomalies.slice(0, 10).map((anomaly, index) => (
              <tr key={index}>
                <td>{anomaly.date}</td>
                <td>
                  <span className={cn('badge', getTypeBadge(anomaly.type))}>{anomaly.type}</span>
                </td>
                <td>{anomaly.metric}</td>
                <td className="text-end">{anomaly.expected_value.toLocaleString()}</td>
                <td className="text-end">{anomaly.actual_value.toLocaleString()}</td>
                <td className="text-end">
                  <span
                    className={cn(anomaly.deviation_percentage > 0 ? 'text-danger' : 'text-info')}
                  >
                    {anomaly.deviation_percentage.toFixed(1)}%
                  </span>
                </td>
                <td>
                  <span className={cn('badge', getSeverityBadge(anomaly.severity))}>
                    {anomaly.severity}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {anomalies.length > 10 && (
        <p className="text-muted small mt-2 mb-0">
          {t('showingFirst', language)} 10 {t('of', language)} {anomalies.length}
        </p>
      )}
    </Card>
  );
};

// Breakdown Table Component (generic)
interface BreakdownTableProps {
  title: string;
  data: Record<string, ToolBreakdown | HostBreakdown>;
  type: 'tool' | 'host';
  language: Language;
}

const BreakdownTable: React.FC<BreakdownTableProps> = ({ title, data, type, language }) => {
  const entries = Object.entries(data);

  if (entries.length === 0) {
    return (
      <Card title={title} className="mb-4">
        <EmptyState icon="bi-table" title={t('noData', language)} />
      </Card>
    );
  }

  return (
    <Card title={title} className="mb-4">
      <div className="table-responsive">
        <table className="table table-sm table-hover">
          <thead>
            <tr>
              <th>{type === 'tool' ? t('tableTool', language) : t('tableHost', language)}</th>
              <th className="text-end">{t('tableTokens', language)}</th>
              <th className="text-end">{t('tableRequests', language)}</th>
              <th className="text-end">{t('daysActive', language)}</th>
              {type === 'tool' && (
                <>
                  <th className="text-end">{t('inputTokens', language)}</th>
                  <th className="text-end">{t('outputTokens', language)}</th>
                </>
              )}
            </tr>
          </thead>
          <tbody>
            {entries.slice(0, 15).map(([name, breakdown]) => (
              <tr key={name}>
                <td className="text-truncate" style={{ maxWidth: 200 }}>
                  {name}
                </td>
                <td className="text-end">{formatTokens(breakdown.tokens)}</td>
                <td className="text-end">{breakdown.requests.toLocaleString()}</td>
                <td className="text-end">{breakdown.days_active}</td>
                {type === 'tool' && 'input_tokens' in breakdown && (
                  <>
                    <td className="text-end">
                      {formatTokens((breakdown as ToolBreakdown).input_tokens)}
                    </td>
                    <td className="text-end">
                      {formatTokens((breakdown as ToolBreakdown).output_tokens)}
                    </td>
                  </>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {entries.length > 15 && (
        <p className="text-muted small mt-2 mb-0">
          {t('showingFirst', language)} 15 {t('of', language)} {entries.length}
        </p>
      )}
    </Card>
  );
};

// Cost/ROI Placeholder Component
const CostRoiPlaceholder: React.FC<{ language: Language }> = ({ language }) => (
  <Card className="mb-4">
    <div
      className="alert alert-info d-flex align-items-start mb-0"
      style={{ borderRadius: 'var(--bs-card-inner-border-radius, calc(0.375rem - 1px))' }}
    >
      <i className="bi bi-info-circle fs-5 me-2" />
      <div>
        <strong>{t('costRoiNotAvailable', language)}</strong>
        <p className="mb-0 mt-1">{t('costRoiNotAvailableDesc', language)}</p>
      </div>
    </div>
  </Card>
);

// Main Component
export const EnterpriseReport: React.FC = () => {
  const language = useLanguage();
  const toast = useToast();
  const { user } = useAuth();
  const userIsAdmin = isAdmin(user);

  // Date range state
  const [quickRange, setQuickRange] = useState<'7' | '30' | '90'>('30');
  const initialDateRange = useMemo(() => {
    const end = new Date();
    const start = new Date(Date.now() - 30 * 24 * 60 * 60 * 1000);
    return {
      start: start.toISOString().split('T')[0],
      end: end.toISOString().split('T')[0],
    };
  }, []);

  const [startDate, setStartDate] = useState(initialDateRange.start);
  const [endDate, setEndDate] = useState(initialDateRange.end);

  // Fetch data
  const {
    data: reportData,
    isLoading: isReportLoading,
    isError: isReportError,
    error: reportError,
    refetch: refetchReport,
  } = useEnterpriseReport(startDate, endDate);

  const {
    data: efficiencyData,
    isLoading: isEfficiencyLoading,
    refetch: refetchEfficiency,
  } = useEfficiencyMetrics(startDate, endDate);

  // Export state
  const [isExporting, setIsExporting] = useState(false);
  const [jsonModalOpen, setJsonModalOpen] = useState(false);
  const [jsonData, setJsonData] = useState<object | null>(null);

  // Handle quick range change
  const handleQuickRangeChange = (range: '7' | '30' | '90') => {
    setQuickRange(range);
    const end = new Date();
    const days = parseInt(range);
    const start = new Date(Date.now() - days * 24 * 60 * 60 * 1000);
    setStartDate(start.toISOString().split('T')[0]);
    setEndDate(end.toISOString().split('T')[0]);
  };

  // Handle refresh
  const handleRefresh = () => {
    refetchReport();
    refetchEfficiency();
  };

  // Handle CSV export
  const handleExportCsv = async () => {
    if (!userIsAdmin) return;
    setIsExporting(true);
    try {
      const blob = await analysisApi.exportReport({
        startDate,
        endDate,
        format: 'csv',
      });
      if (blob instanceof Blob) {
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `analytics_${startDate}_${endDate}.csv`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
      }
    } catch (error) {
      console.error('Export CSV failed:', error);
      toast.error(t('exportFailed', language));
    } finally {
      setIsExporting(false);
    }
  };

  // Handle JSON export
  const handleExportJson = async () => {
    if (!userIsAdmin) return;
    setIsExporting(true);
    try {
      const result = await analysisApi.exportReport({
        startDate,
        endDate,
        format: 'json',
      });
      if (!(result instanceof Blob)) {
        setJsonData(result);
        setJsonModalOpen(true);
      }
    } catch (error) {
      console.error('Export JSON failed:', error);
      toast.error(t('exportFailed', language));
    } finally {
      setIsExporting(false);
    }
  };

  // Loading state
  if (isReportLoading) {
    return (
      <div className="enterprise-report">
        <div className="page-header d-flex justify-content-between align-items-center mb-4">
          <h2>{t('enterpriseReport', language)}</h2>
        </div>
        <Card className="mb-4">
          <div className="row g-3">
            <div className="col-md-4">
              <div className="skeleton" style={{ height: 38 }} />
            </div>
            <div className="col-md-4">
              <div className="skeleton" style={{ height: 38 }} />
            </div>
          </div>
        </Card>
        <MetricsSkeleton />
        <Card className="mb-4">
          <TableSkeleton rows={10} />
        </Card>
      </div>
    );
  }

  // Error state
  if (isReportError) {
    return <Error message={reportError?.message || t('error', language)} onRetry={handleRefresh} />;
  }

  return (
    <div className="enterprise-report">
      {/* Header */}
      <div className="page-header d-flex justify-content-between align-items-center mb-4">
        <h2>{t('enterpriseReport', language)}</h2>
        <div className="page-header-controls">
          <Button
            variant="outline-secondary"
            size="sm"
            onClick={handleRefresh}
            icon={<i className="bi bi-arrow-clockwise" />}
          >
            {t('refresh', language)}
          </Button>
        </div>
      </div>

      {/* Filters */}
      <Card className="mb-4">
        <div className="row g-3">
          {/* Quick Date Range Buttons */}
          <div className="col-12">
            <label className="form-label">{t('quickDateRange', language)}</label>
            <div className="btn-group" role="group">
              <button
                type="button"
                className={cn('btn', quickRange === '7' ? 'btn-primary' : 'btn-outline-primary')}
                onClick={() => handleQuickRangeChange('7')}
              >
                7 {t('days', language)}
              </button>
              <button
                type="button"
                className={cn('btn', quickRange === '30' ? 'btn-primary' : 'btn-outline-primary')}
                onClick={() => handleQuickRangeChange('30')}
              >
                30 {t('days', language)}
              </button>
              <button
                type="button"
                className={cn('btn', quickRange === '90' ? 'btn-primary' : 'btn-outline-primary')}
                onClick={() => handleQuickRangeChange('90')}
              >
                90 {t('days', language)}
              </button>
            </div>
          </div>
          {/* Date Range */}
          <div className="col-md-3">
            <label className="form-label">{t('startDate', language)}</label>
            <DatePicker
              value={startDate}
              onChange={(v) => {
                setStartDate(v);
                setQuickRange('30');
              }}
            />
          </div>
          <div className="col-md-3">
            <label className="form-label">{t('endDate', language)}</label>
            <DatePicker
              value={endDate}
              onChange={(v) => {
                setEndDate(v);
                setQuickRange('30');
              }}
            />
          </div>
        </div>
      </Card>

      {/* Summary Cards */}
      {reportData && <SummaryCards summary={reportData.summary} language={language} />}

      {/* Efficiency Cards */}
      <EfficiencyCards
        efficiency={efficiencyData ?? { efficiency_available: false }}
        isLoading={isEfficiencyLoading}
        language={language}
      />

      {/* Trends Table */}
      {reportData && <TrendsTable trends={reportData.trends} language={language} />}

      {/* Anomalies Table */}
      {reportData && <AnomaliesTable anomalies={reportData.anomalies} language={language} />}

      {/* Breakdown Tables */}
      <div className="row">
        <div className="col-md-6">
          {reportData && (
            <BreakdownTable
              title={t('breakdownByTool', language)}
              data={reportData.breakdown_by_tool}
              type="tool"
              language={language}
            />
          )}
        </div>
        <div className="col-md-6">
          {reportData && (
            <BreakdownTable
              title={t('breakdownByHost', language)}
              data={reportData.breakdown_by_host}
              type="host"
              language={language}
            />
          )}
        </div>
      </div>

      {/* Export Buttons (Admin Only) */}
      {userIsAdmin && (
        <Card title={t('exportReport', language)} className="mb-4">
          <div className="d-flex gap-2">
            <Button
              variant="outline-primary"
              onClick={handleExportCsv}
              loading={isExporting}
              icon={isExporting ? undefined : <i className="bi bi-filetype-csv" />}
            >
              {t('export', language)} CSV
            </Button>
            <Button
              variant="outline-primary"
              onClick={handleExportJson}
              loading={isExporting}
              icon={isExporting ? undefined : <i className="bi bi-filetype-json" />}
            >
              {t('export', language)} JSON
            </Button>
          </div>
        </Card>
      )}

      {/* Cost/ROI Placeholder */}
      <CostRoiPlaceholder language={language} />

      {/* JSON Preview Modal */}
      <JsonPreviewModal
        isOpen={jsonModalOpen}
        onClose={() => setJsonModalOpen(false)}
        data={jsonData}
        title={t('jsonExportPreview', language)}
      />
    </div>
  );
};
