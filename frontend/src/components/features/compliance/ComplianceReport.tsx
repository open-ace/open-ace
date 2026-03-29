/**
 * ComplianceReport Component - Compliance report generation page
 *
 * Features:
 * - Report type selection
 * - Date range selection
 * - Generate reports
 * - View saved reports
 */

import React, { useState, useEffect } from 'react';
import { cn } from '@/utils';
import { useLanguage } from '@/store';
import { t } from '@/i18n';
import { Card, Button, Select, Loading, Error, EmptyState, Badge } from '@/components/common';
import { complianceApi, type ReportType, type SavedReport } from '@/api';
import { formatDateTime } from '@/utils';

const FORMAT_OPTIONS = [
  { value: 'json', label: 'JSON' },
  { value: 'csv', label: 'CSV' },
];

export const ComplianceReport: React.FC = () => {
  const language = useLanguage();
  const [reportTypes, setReportTypes] = useState<ReportType[]>([]);
  const [savedReports, setSavedReports] = useState<SavedReport[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isGenerating, setIsGenerating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [selectedType, setSelectedType] = useState('');
  const [startDate, setStartDate] = useState('');
  const [endDate, setEndDate] = useState('');
  const [format, setFormat] = useState('json');

  // Initialize dates
  useEffect(() => {
    const end = new Date();
    const start = new Date(Date.now() - 30 * 24 * 60 * 60 * 1000);
    setEndDate(end.toISOString().split('T')[0]);
    setStartDate(start.toISOString().split('T')[0]);
  }, []);

  // Fetch data
  useEffect(() => {
    const fetchData = async () => {
      setIsLoading(true);
      setError(null);
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
        setError(errorMessage);
      } finally {
        setIsLoading(false);
      }
    };
    fetchData();
  }, []);

  // Generate report
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

  // Download saved report
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

  if (isLoading) {
    return <Loading size="lg" text={t('loading', language)} />;
  }

  if (error) {
    return <Error message={error} onRetry={() => window.location.reload()} />;
  }

  return (
    <div className="compliance-report">
      {/* Header */}
      <div className="d-flex justify-content-between align-items-center mb-4">
        <h2>{t('complianceReport', language)}</h2>
      </div>

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
  return icons[type] || 'bi-file-text';
}
