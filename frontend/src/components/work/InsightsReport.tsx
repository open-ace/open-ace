/**
 * InsightsReport Component - AI conversation insights report page
 *
 * Features:
 * - Generate personalized AI usage analysis reports
 * - Date range quick selection (7/14/30 days)
 * - History report list with click-to-view
 * - Overall score display with color indicator
 * - Strengths, improvement areas, and actionable suggestions
 * - Usage statistics summary
 * - Loading skeleton, error state, empty data state
 */

import React, { useState, useEffect, useCallback, useRef } from 'react';
import { useLanguage } from '@/store';
import { t } from '@/i18n';
import { Card, Button, Loading, Error, Badge } from '@/components/common';
import {
  insightsApi,
  type InsightsReport as InsightsReportData,
  type InsightsHistoryItem,
} from '@/api/insights';
import { formatNumber, formatTokens } from '@/utils';

type DateRange = 7 | 14 | 30;

export const InsightsReport: React.FC = () => {
  const language = useLanguage();

  // State
  const [report, setReport] = useState<InsightsReportData | null>(null);
  const [history, setHistory] = useState<InsightsHistoryItem[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [isHistoryLoading, setIsHistoryLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [dateRange, setDateRange] = useState<DateRange>(7);
  const [insufficientData, setInsufficientData] = useState(false);
  const abortControllerRef = useRef<AbortController | null>(null);

  // Load history on mount
  useEffect(() => {
    const loadHistory = async () => {
      setIsHistoryLoading(true);
      try {
        const data = await insightsApi.getHistory();
        setHistory(data.reports || []);
      } catch {
        // Silently fail for history
      } finally {
        setIsHistoryLoading(false);
      }
    };
    loadHistory();
  }, []);

  // Generate report
  const generateReport = useCallback(async (days?: DateRange) => {
    const range = days || dateRange;
    const end = new Date();
    const start = new Date();
    start.setDate(end.getDate() - range);
    const startDate = start.toISOString().split('T')[0];
    const endDate = end.toISOString().split('T')[0];

    // Cancel previous request
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
    }
    const controller = new AbortController();
    abortControllerRef.current = controller;

    setIsLoading(true);
    setError(null);
    setInsufficientData(false);
    setReport(null);

    try {
      const result = await insightsApi.generateReport(startDate, endDate, controller.signal);

      if (result && 'error' in result && result.error === 'insufficient_data') {
        setInsufficientData(true);
        return;
      }

      if (result && 'error' in result && result.error) {
        setError((result as { error: string; message?: string }).message || result.error);
        return;
      }

      setReport(result as InsightsReportData);

      // Refresh history after successful generation
      try {
        const historyData = await insightsApi.getHistory();
        setHistory(historyData.reports || []);
      } catch {
        // Ignore history refresh error
      }
    } catch (err) {
      const errObj = err as { name?: string; message?: string };
      if (errObj?.name === 'AbortError') return;
      setError(errObj?.message || 'Failed to generate report');
    } finally {
      setIsLoading(false);
    }
  }, [dateRange]);

  // View a specific history report
  const viewHistoryReport = useCallback(async (item: InsightsHistoryItem) => {
    setIsLoading(true);
    setError(null);
    setInsufficientData(false);

    try {
      const result = await insightsApi.generateReport(item.start_date, item.end_date);
      if (result && 'error' in result && result.error === 'insufficient_data') {
        setInsufficientData(true);
        return;
      }
      setReport(result as InsightsReportData);
    } catch (err) {
      const error = err as { message?: string };
      setError(error?.message || 'Failed to load report');
    } finally {
      setIsLoading(false);
    }
  }, []);

  // Delete a report
  const deleteReport = useCallback(async (id: number) => {
    try {
      await insightsApi.deleteReport(id);
      setHistory((prev) => prev.filter((r) => r.id !== id));
      if (report?.id === id) {
        setReport(null);
      }
    } catch {
      // Ignore delete error
    }
  }, [report]);

  // Get score color
  const getScoreColor = (score: number): string => {
    if (score >= 8) return '#10b981';
    if (score >= 5) return '#f59e0b';
    return '#ef4444';
  };

  // Get score label
  const getScoreLabel = (score: number): string => {
    if (score >= 8) return t('excellent', language);
    if (score >= 5) return t('good', language);
    return t('needsImprovement', language);
  };

  const dateRanges: { days: DateRange; label: string }[] = [
    { days: 7, label: '7' },
    { days: 14, label: '14' },
    { days: 30, label: '30' },
  ];

  return (
    <div className="insights-report">
      {/* Header */}
      <div className="d-flex justify-content-between align-items-center mb-4">
        <h2>{t('insightsTitle', language)}</h2>
        <div className="d-flex align-items-center gap-2">
          {/* Date range selector */}
          <div className="btn-group btn-group-sm">
            {dateRanges.map((range) => (
              <button
                key={range.days}
                className={`btn btn-outline-primary ${dateRange === range.days ? 'active' : ''}`}
                onClick={() => setDateRange(range.days)}
                disabled={isLoading}
              >
                {range.label} {t('days', language)}
              </button>
            ))}
          </div>
          <Button
            variant="primary"
            size="sm"
            onClick={() => generateReport()}
            disabled={isLoading}
          >
            {isLoading ? (
              <>
                <span className="spinner-border spinner-border-sm me-1" />
                {t('generating', language)}
              </>
            ) : (
              <>
                <i className="bi bi-lightbulb me-1" />
                {t('generateReport', language)}
              </>
            )}
          </Button>
        </div>
      </div>

      {/* History Reports */}
      {!isHistoryLoading && history.length > 0 && (
        <Card className="mb-4">
          <h6 className="mb-3">
            <i className="bi bi-clock-history me-2" />
            {t('historyReports', language)}
          </h6>
          <div className="d-flex flex-wrap gap-2">
            {history.map((item) => (
              <div
                key={item.id}
                className="d-flex align-items-center gap-2 p-2 border rounded"
                style={{ cursor: 'pointer', minWidth: '200px' }}
              >
                <button
                  className="btn btn-link btn-sm p-0 text-decoration-none"
                  onClick={() => viewHistoryReport(item)}
                  disabled={isLoading}
                >
                  <span className="small">
                    {item.start_date} ~ {item.end_date}
                  </span>
                  <Badge variant={item.overall_score >= 7 ? 'success' : item.overall_score >= 4 ? 'warning' : 'danger'}>
                    {item.overall_score}/10
                  </Badge>
                </button>
                <button
                  className="btn btn-link btn-sm p-0 text-muted"
                  onClick={() => deleteReport(item.id)}
                  title={t('deleteReport', language)}
                >
                  <i className="bi bi-x-lg" style={{ fontSize: '0.7rem' }} />
                </button>
              </div>
            ))}
          </div>
        </Card>
      )}

      {/* Loading State */}
      {isLoading && (
        <div className="text-center py-5">
          <div className="mb-3">
            <Loading />
          </div>
          <p className="text-muted">{t('analyzingConversations', language)}</p>
          <div className="progress mx-auto" style={{ maxWidth: '300px', height: '4px' }}>
            <div
              className="progress-bar progress-bar-striped progress-bar-animated"
              style={{ width: '100%' }}
            />
          </div>
        </div>
      )}

      {/* Error State */}
      {error && !isLoading && (
        <Error message={error} onRetry={() => generateReport()} />
      )}

      {/* Insufficient Data State */}
      {insufficientData && !isLoading && !error && (
        <Card className="text-center py-5">
          <i className="bi bi-chat-text display-1 text-muted mb-3" />
          <h5 className="text-muted">{t('insightsNoData', language)}</h5>
          <p className="text-muted small">{t('insightsNoDataDesc', language)}</p>
        </Card>
      )}

      {/* Empty State */}
      {!report && !isLoading && !error && !insufficientData && (
        <Card className="text-center py-5">
          <i className="bi bi-lightbulb display-1 text-muted mb-3" />
          <h5 className="text-muted">{t('insightsWelcome', language)}</h5>
          <p className="text-muted small">{t('insightsWelcomeDesc', language)}</p>
          <Button variant="primary" onClick={() => generateReport()}>
            <i className="bi bi-lightbulb me-1" />
            {t('generateReport', language)}
          </Button>
        </Card>
      )}

      {/* Report Display */}
      {report && !isLoading && !error && (
        <>
          {/* Overall Score Card */}
          <Card className="mb-4">
            <div className="d-flex align-items-center gap-4">
              <div
                className="text-center p-3 rounded"
                style={{
                  background: `linear-gradient(135deg, ${getScoreColor(report.overall_score)}15, ${getScoreColor(report.overall_score)}30)`,
                  minWidth: '120px',
                }}
              >
                <div
                  className="display-4 fw-bold"
                  style={{ color: getScoreColor(report.overall_score) }}
                >
                  {report.overall_score}
                </div>
                <div className="small text-muted">{t('overallScore', language)}</div>
              </div>
              <div className="flex-grow-1">
                <div className="d-flex align-items-center gap-2 mb-2">
                  <h5 className="mb-0">{getScoreLabel(report.overall_score)}</h5>
                  <Badge
                    variant={
                      report.overall_score >= 7
                        ? 'success'
                        : report.overall_score >= 4
                          ? 'warning'
                          : 'danger'
                    }
                  >
                    {report.overall_score}/10
                  </Badge>
                </div>
                <p className="text-muted mb-0">{report.overall_assessment}</p>
              </div>
            </div>
          </Card>

          {/* Strengths and Improvements */}
          <div className="row g-3 mb-4">
            {/* Strengths */}
            <div className="col-md-6">
              <Card title={t('strengths', language)}>
                <ul className="list-unstyled mb-0">
                  {(report.strengths || []).map((item, idx) => (
                    <li key={idx} className="d-flex align-items-start mb-2">
                      <i className="bi bi-check-circle-fill text-success me-2 mt-1" />
                      <span>{item}</span>
                    </li>
                  ))}
                </ul>
              </Card>
            </div>

            {/* Areas for Improvement */}
            <div className="col-md-6">
              <Card title={t('areasForImprovement', language)}>
                <ul className="list-unstyled mb-0">
                  {(report.areas_for_improvement || []).map((item, idx) => (
                    <li key={idx} className="d-flex align-items-start mb-2">
                      <i className="bi bi-exclamation-triangle-fill text-warning me-2 mt-1" />
                      <span>{item}</span>
                    </li>
                  ))}
                </ul>
              </Card>
            </div>
          </div>

          {/* Suggestions */}
          {(report.suggestions || []).length > 0 && (
            <Card title={t('suggestions', language)} className="mb-4">
              <div className="row g-3">
                {(report.suggestions || []).map((suggestion, idx) => (
                  <div key={idx} className="col-md-6">
                    <div className="border rounded p-3 h-100">
                      <h6 className="mb-2">
                        <i className="bi bi-arrow-right-circle me-1 text-primary" />
                        {suggestion.title}
                      </h6>
                      <p className="text-muted small mb-2">{suggestion.description}</p>
                      {suggestion.example && (
                        <div className="bg-light rounded p-2">
                          <small className="text-muted fw-medium">{t('exampleLabel', language)}：</small>
                          <div className="small mt-1" style={{ whiteSpace: 'pre-wrap' }}>
                            {suggestion.example}
                          </div>
                        </div>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            </Card>
          )}

          {/* Usage Summary */}
          {report.usage_summary && (
            <Card title={t('usageSummary', language)} className="mb-4">
              <div className="row g-3 text-center">
                <div className="col-6 col-md-3">
                  <div className="p-3">
                    <div className="display-6 fw-bold text-primary">
                      {formatNumber(report.usage_summary.total_conversations)}
                    </div>
                    <small className="text-muted">{t('totalConversations', language)}</small>
                  </div>
                </div>
                <div className="col-6 col-md-3">
                  <div className="p-3">
                    <div className="display-6 fw-bold text-success">
                      {formatNumber(report.usage_summary.total_messages)}
                    </div>
                    <small className="text-muted">{t('totalMessages', language)}</small>
                  </div>
                </div>
                <div className="col-6 col-md-3">
                  <div className="p-3">
                    <div className="display-6 fw-bold text-info">
                      {formatTokens(report.usage_summary.total_tokens)}
                    </div>
                    <small className="text-muted">{t('totalTokens', language)}</small>
                  </div>
                </div>
                <div className="col-6 col-md-3">
                  <div className="p-3">
                    <div className="display-6 fw-bold text-warning">
                      {report.usage_summary.avg_messages_per_conversation}
                    </div>
                    <small className="text-muted">{t('avgMessages', language)}</small>
                  </div>
                </div>
              </div>
            </Card>
          )}
        </>
      )}
    </div>
  );
};
