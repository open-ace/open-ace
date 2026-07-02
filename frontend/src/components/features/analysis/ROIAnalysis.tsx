/**
 * ROIAnalysis Component - ROI analysis page
 *
 * Features:
 * - ROI metrics cards
 * - ROI trend chart
 * - Cost breakdown
 * - Optimization suggestions
 *
 * Performance optimizations:
 * - Skeleton loading for better perceived performance
 * - Data caching to avoid redundant API calls
 * - Parallel API requests
 */

import React, { useState, useEffect, useMemo, useRef, useCallback } from 'react';
import { cn, createMatcherConfig } from '@/utils';
import { useLanguage } from '@/store';
import { t } from '@/i18n';
import {
  Card,
  StatCard,
  Select,
  Error,
  EmptyState,
  LineChart,
  PieChart,
  BarChart,
  Skeleton,
  SkeletonCard,
  PageRefreshControl,
} from '@/components/common';
import {
  roiApi,
  type ROIMetrics,
  type ROITrend,
  type CostBreakdown,
  type DailyCost,
  type OptimizationSuggestion,
  type EfficiencyReport,
} from '@/api';
import { formatTokens, formatToolName, formatChartDate } from '@/utils';
import { useTools, usePageRefresh } from '@/hooks';

// Cache key generator
const getCacheKey = (startDate: string, endDate: string, tool: string) =>
  `roi_${startDate}_${endDate}_${tool}`;

// Simple cache for ROI data
const roiDataCache = new Map<
  string,
  {
    data: CachedData;
    timestamp: number;
  }
>();

interface CachedData {
  roiMetrics: ROIMetrics | null;
  roiTrend: ROITrend[];
  costBreakdown: CostBreakdown[];
  dailyCosts: DailyCost[];
  suggestions: OptimizationSuggestion[];
  efficiency: EfficiencyReport | null;
}

const CACHE_TTL = 5 * 60 * 1000; // 5 minutes

// Maps suggestion_type -> { title key, description key } for i18n interpolation.
const SUGGESTION_KEY_MAP: Record<string, { title: string; desc: string }> = {
  model_switch: { title: 'suggestionModelSwitchTitle', desc: 'suggestionModelSwitchDesc' },
  usage_pattern: { title: 'suggestionUsagePatternTitle', desc: 'suggestionUsagePatternDesc' },
  quota_adjustment: {
    title: 'suggestionQuotaAdjustmentTitle',
    desc: 'suggestionQuotaAdjustmentDesc',
  },
  tool_consolidation: {
    title: 'suggestionToolConsolidationTitle',
    desc: 'suggestionToolConsolidationDesc',
  },
  time_optimization: {
    title: 'suggestionTimeOptimizationTitle',
    desc: 'suggestionTimeOptimizationDesc',
  },
  token_optimization: {
    title: 'suggestionTokenOptimizationTitle',
    desc: 'suggestionTokenOptimizationDesc',
  },
};

// Maps suggestion_type -> ordered action item i18n keys.
const ACTION_KEY_MAP: Record<string, string[]> = {
  model_switch: ['actionModelSwitch1', 'actionModelSwitch2', 'actionModelSwitch3'],
  time_optimization: [
    'actionTimeOptimization1',
    'actionTimeOptimization2',
    'actionTimeOptimization3',
  ],
  quota_adjustment: ['actionQuotaAdjustment1', 'actionQuotaAdjustment2', 'actionQuotaAdjustment3'],
  tool_consolidation: [
    'actionToolConsolidation1',
    'actionToolConsolidation2',
    'actionToolConsolidation3',
  ],
  token_optimization: [
    'actionTokenOptimization1',
    'actionTokenOptimization2',
    'actionTokenOptimization3',
  ],
};

// Maps priority/impact enum value -> i18n key.
const PRIORITY_KEY_MAP: Record<string, string> = {
  high: 'priorityHigh',
  medium: 'priorityMedium',
  low: 'priorityLow',
};

// Maps recommendation_type -> i18n key.
const RECOMMENDATION_KEY_MAP: Record<string, string> = {
  low_efficiency: 'recommendationLowEfficiency',
  low_output_ratio: 'recommendationLowOutputRatio',
  high_cost_per_request: 'recommendationHighCostPerRequest',
  high_avg_tokens: 'recommendationHighAvgTokens',
  high_model_concentration: 'recommendationHighModelConcentration',
  healthy: 'recommendationHealthy',
};

// Detects an unfilled {placeholder} (incl. digits) so we can fall back to a
// backend-supplied string and never leak a literal {x} to users.
const PLACEHOLDER_LEAK = /\{[a-z0-9_]+\}/;

// Skeleton components for ROI page
const ROIMetricsSkeleton: React.FC = () => (
  <div className="row g-3 mb-4">
    {[1, 2, 3, 4].map((i) => (
      <div key={i} className="col-md-3">
        <div className="card">
          <div className="card-body">
            <Skeleton height={16} width="60%" className="mb-2" />
            <Skeleton height={32} width="80%" className="mb-1" />
            <Skeleton height={12} width="40%" />
          </div>
        </div>
      </div>
    ))}
  </div>
);

const ChartSkeleton: React.FC<{ height?: number }> = ({ height = 300 }) => (
  <div style={{ height }}>
    <Skeleton variant="rectangular" height={height} className="w-100" />
  </div>
);

export const ROIAnalysis: React.FC = () => {
  const language = useLanguage();
  const [roiMetrics, setRoiMetrics] = useState<ROIMetrics | null>(null);
  const [roiTrend, setRoiTrend] = useState<ROITrend[]>([]);
  const [costBreakdown, setCostBreakdown] = useState<CostBreakdown[]>([]);
  const [dailyCosts, setDailyCosts] = useState<DailyCost[]>([]);
  const [suggestions, setSuggestions] = useState<OptimizationSuggestion[]>([]);
  const [efficiency, setEfficiency] = useState<EfficiencyReport | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [startDate, setStartDate] = useState('');
  const [endDate, setEndDate] = useState('');
  const [selectedTool, setSelectedTool] = useState('');

  // Expanded action-item rows (by suggestion_id)
  const [expandedIds, setExpandedIds] = useState<Set<string>>(new Set());

  // Track if this is the initial load
  const isInitialLoad = useRef(true);

  // Page refresh control - manual refresh for ROI analysis
  const pageRefresh = usePageRefresh({
    page: '/manage/analysis/roi',
    refreshKey: createMatcherConfig([['analysis', 'roi']], 'prefix'),
    interval: 0, // No auto refresh - manual only
    enabled: false,
    // Note: fetchData defined below, use arrow function to avoid hoisting issues
    // forceRefresh=true to bypass cache and fetch fresh data
    onRefresh: () => fetchData(true),
  });

  // Initialize dates
  useEffect(() => {
    const end = new Date();
    const start = new Date(Date.now() - 30 * 24 * 60 * 60 * 1000);
    setEndDate(end.toISOString().split('T')[0]);
    setStartDate(start.toISOString().split('T')[0]);
  }, []);

  // Translate suggestion title/description based on suggestion_type + dynamic params.
  const translateSuggestion = useCallback(
    (s: OptimizationSuggestion) => {
      const keys = SUGGESTION_KEY_MAP[s.suggestion_type];
      if (!keys) {
        return s; // Unknown suggestion_type: keep backend strings
      }
      const params = s.params ?? {};
      // Render the localized template, then fall back to the backend-supplied
      // string if a {placeholder} could not be filled (e.g. older backend that
      // does not emit params). This guarantees no literal {x} ever leaks.
      const localize = (key: string, fallback: string) => {
        const result = t(key, language, params);
        return PLACEHOLDER_LEAK.test(result) ? fallback : result;
      };
      return {
        ...s,
        title: localize(keys.title, s.title),
        description: localize(keys.desc, s.description),
      };
    },
    [language]
  );

  // Localize a priority/impact enum value (e.g. 'high' -> 'High' / '高').
  const translatePriority = useCallback(
    (value: string) => {
      const key = PRIORITY_KEY_MAP[value];
      return key ? t(key, language) : value;
    },
    [language]
  );

  // Build localized action items for a suggestion type using its dynamic params.
  // Falls back to the backend-supplied action_items[i] if a {placeholder} cannot
  // be filled, mirroring translateSuggestion's leak protection.
  const getActionItems = useCallback(
    (
      suggestionType: string,
      params: Record<string, string | number>,
      fallbackItems: string[] = []
    ) => {
      const actionKeys = ACTION_KEY_MAP[suggestionType];
      if (!actionKeys) return [];
      return actionKeys.map((key, i) => {
        const result = t(key, language, params);
        return PLACEHOLDER_LEAK.test(result) && fallbackItems[i] ? fallbackItems[i] : result;
      });
    },
    [language]
  );

  // Localize structured efficiency recommendation items, falling back to the
  // deprecated string list when structured items are unavailable.
  const translateRecommendations = useCallback(
    (eff: EfficiencyReport | null) => {
      if (!eff) return [] as string[];
      if (eff.recommendation_items && eff.recommendation_items.length > 0) {
        return eff.recommendation_items.map((item) => {
          const key = RECOMMENDATION_KEY_MAP[item.type];
          return key ? t(key, language, item.params ?? {}) : item.type;
        });
      }
      return eff.recommendations ?? [];
    },
    [language]
  );

  // Toggle an expanded action-item row.
  const toggleExpand = useCallback((suggestionId: string) => {
    setExpandedIds((prev) => {
      const next = new Set(prev);
      if (next.has(suggestionId)) {
        next.delete(suggestionId);
      } else {
        next.add(suggestionId);
      }
      return next;
    });
  }, []);

  // Fetch data with caching
  const fetchData = useCallback(
    async (forceRefresh = false) => {
      if (!startDate || !endDate) return;

      const cacheKey = getCacheKey(startDate, endDate, selectedTool);
      const cached = roiDataCache.get(cacheKey);
      const now = Date.now();

      // Use cache if valid and not forcing refresh
      if (!forceRefresh && cached && now - cached.timestamp < CACHE_TTL) {
        const data = cached.data;
        setRoiMetrics(data.roiMetrics);
        setRoiTrend(data.roiTrend);
        setCostBreakdown(data.costBreakdown);
        setDailyCosts(data.dailyCosts);
        setSuggestions(data.suggestions);
        setEfficiency(data.efficiency);
        setIsLoading(false);
        return;
      }

      if (isInitialLoad.current) {
        setIsLoading(true);
      }
      setError(null);

      try {
        const [roi, trend, breakdown, daily, sugg, eff] = await Promise.all([
          roiApi.getROI({
            start_date: startDate,
            end_date: endDate,
            tool_name: selectedTool || undefined,
          }),
          roiApi.getROITrend(6),
          roiApi.getCostBreakdown({ start_date: startDate, end_date: endDate }),
          roiApi.getDailyCosts({ start_date: startDate, end_date: endDate }),
          roiApi.getOptimizationSuggestions(30),
          roiApi.getEfficiencyReport(30),
        ]);

        const data: CachedData = {
          roiMetrics: roi,
          roiTrend: trend,
          costBreakdown: breakdown.breakdown,
          dailyCosts: daily,
          suggestions: sugg,
          efficiency: eff,
        };

        // Update cache
        roiDataCache.set(cacheKey, { data, timestamp: now });

        setRoiMetrics(roi);
        setRoiTrend(trend);
        setCostBreakdown(breakdown.breakdown);
        setDailyCosts(daily);
        setSuggestions(sugg);
        setEfficiency(eff);
        isInitialLoad.current = false;
      } catch (err) {
        const errorMessage = err instanceof Error ? (err as Error).message : 'Failed to fetch data';
        setError(errorMessage);
      } finally {
        setIsLoading(false);
      }
    },
    [startDate, endDate, selectedTool]
  );

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  // Get tools for filter
  const { data: toolsData } = useTools();
  const tools = useMemo(() => toolsData ?? [], [toolsData]);

  // Tool options
  const toolOptions = useMemo(
    () => [
      { value: '', label: t('dashboardFilterAllTools', language) },
      ...tools.map((tool) => ({ value: tool, label: formatToolName(tool) })),
    ],
    [tools, language]
  );

  // Chart data
  const roiTrendData = useMemo(() => {
    if (!roiTrend.length) return { labels: [], data: [] };
    return {
      labels: roiTrend.map((item) => item.month ?? item.period?.split(' to ')[0] ?? ''),
      data: roiTrend.map((item) => item.roi_percentage),
    };
  }, [roiTrend]);

  const dailyCostData = useMemo(() => {
    if (!dailyCosts.length) return { labels: [], tooltipLabels: [], data: [] };
    const rawDates = dailyCosts.map((d) => d.date);
    // d.date is backend-normalized to YYYY-MM-DD, so the first 7 chars
    // (YYYY-MM) identify the month. When every tick shares the same month,
    // render day-only axis labels to avoid repeating the month prefix; the
    // month/year stays visible in the chart title and the full date in the
    // tooltip.
    const dayOnly = new Set(rawDates.map((d) => (d ?? '').slice(0, 7))).size === 1;
    return {
      labels: rawDates.map((d) => formatChartDate(d, language, { dayOnly })),
      // Full YYYY-MM-DD for the tooltip so hover keeps year context.
      tooltipLabels: rawDates,
      data: dailyCosts.map((d) => d.cost ?? d.total_cost ?? 0),
    };
  }, [dailyCosts, language]);

  const costBreakdownData = useMemo(() => {
    if (!costBreakdown.length) return { labels: [], data: [] };
    // Defensive client-side aggregation: collapse any duplicate tool entries
    // (same tool split across models/sources) so the pie never renders two
    // slices for one tool, even if the backend regresses. Merge by the
    // normalized tool_name, sort by cost desc, and render labels via
    // formatToolName for consistent display + i18n casing.
    const merged = costBreakdown.reduce<Record<string, number>>((acc, c) => {
      const key = c.tool_name ?? c.category ?? c.model ?? 'unknown';
      acc[key] = (acc[key] ?? 0) + (c.total_cost ?? 0);
      return acc;
    }, {});
    const entries = Object.entries(merged).sort((a, b) => b[1] - a[1]);
    return {
      labels: entries.map(([key]) => formatToolName(key)),
      data: entries.map(([, cost]) => cost),
    };
  }, [costBreakdown]);

  const getImpactVariant = (impact: string) => {
    switch (impact) {
      case 'high':
        return 'danger';
      case 'medium':
        return 'warning';
      default:
        return 'info';
    }
  };

  // Show skeleton on initial load
  if (isLoading && isInitialLoad.current) {
    return (
      <div className="roi-analysis">
        <div className="d-flex justify-content-between align-items-center mb-4">
          <Skeleton height={32} width={200} />
          <Skeleton height={32} width={100} />
        </div>

        <Card className="mb-4">
          <div className="row g-3">
            {[1, 2, 3].map((i) => (
              <div key={i} className="col-md-3">
                <Skeleton height={38} />
              </div>
            ))}
          </div>
        </Card>

        <ROIMetricsSkeleton />

        <div className="row mb-4">
          <div className="col-md-8">
            <Card title={t('roiTrend', language)}>
              <ChartSkeleton height={300} />
            </Card>
          </div>
          <div className="col-md-4">
            <Card title={t('costBreakdown', language)}>
              <ChartSkeleton height={300} />
            </Card>
          </div>
        </div>

        <Card title={t('dailyCosts', language)} className="mb-4">
          <ChartSkeleton height={200} />
        </Card>

        <SkeletonCard />
      </div>
    );
  }

  if (error) {
    return <Error message={error} onRetry={() => fetchData(true)} />;
  }

  return (
    <div className="roi-analysis">
      {/* Header */}
      <div className="d-flex justify-content-between align-items-center mb-4">
        <h2>{t('roiAnalysis', language)}</h2>
        <PageRefreshControl refresh={pageRefresh} compact={true} showLastRefreshTime={true} />
      </div>

      {/* Filters */}
      <Card className="mb-4">
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
            <label className="form-label">{t('tableTool', language)}</label>
            <Select options={toolOptions} value={selectedTool} onChange={setSelectedTool} />
          </div>
        </div>
      </Card>

      {/* ROI Metrics */}
      {roiMetrics && (
        <div className="row g-3 mb-4">
          <div className="col-md-3">
            <StatCard
              label={t('totalCost', language)}
              value={`$${roiMetrics.total_cost.toFixed(2)}`}
              icon={<i className="bi bi-currency-dollar fs-4" />}
              variant="primary"
            />
          </div>
          <div className="col-md-3">
            <StatCard
              label={t('totalSavings', language)}
              value={`$${(roiMetrics.estimated_savings || 0).toFixed(2)}`}
              icon={<i className="bi bi-piggy-bank fs-4" />}
              variant="success"
            />
          </div>
          <div className="col-md-3">
            <StatCard
              label={t('roiPercentage', language)}
              value={
                roiMetrics.roi_percentage < -1000
                  ? 'N/A'
                  : `${roiMetrics.roi_percentage.toFixed(1)}%`
              }
              icon={<i className="bi bi-graph-up-arrow fs-4" />}
              variant={roiMetrics.roi_percentage >= 0 ? 'success' : 'danger'}
            />
            {roiMetrics.roi_percentage < 0 && roiMetrics.roi_percentage >= -1000 && (
              <div className="text-muted small mt-1">
                <i className="bi bi-info-circle me-1" />
                {t('roiNegativeHint', language)}
              </div>
            )}
            {roiMetrics.roi_percentage < -1000 && (
              <div className="text-danger small mt-1">
                <i className="bi bi-exclamation-triangle me-1" />
                {t('roiDataAnomaly', language)}
              </div>
            )}
          </div>
          <div className="col-md-3">
            <StatCard
              label={t('efficiencyScore', language)}
              value={`${(roiMetrics.efficiency_score ?? 0).toFixed(0)}%`}
              icon={<i className="bi bi-speedometer2 fs-4" />}
              variant={
                (roiMetrics.efficiency_score ?? 0) >= 80
                  ? 'success'
                  : (roiMetrics.efficiency_score ?? 0) >= 60
                    ? 'warning'
                    : 'danger'
              }
            />
          </div>
        </div>
      )}

      {/* Data Anomaly Warning - only show for extreme negative ROI */}
      {roiMetrics && roiMetrics.roi_percentage < -1000 && (
        <div className="alert alert-warning mb-4" role="alert">
          <i className="bi bi-exclamation-triangle me-2" />
          <strong>{t('dataAnomalyDetected', language)}:</strong>{' '}
          {t('tokenAccumulationWarning', language)}
        </div>
      )}

      {/* Charts */}
      <div className="row mb-4">
        <div className="col-md-8">
          <Card title={t('roiTrend', language)}>
            {roiTrendData.labels.length > 0 ? (
              <LineChart
                labels={roiTrendData.labels}
                datasets={[{ label: t('roi', language), data: roiTrendData.data }]}
                height={300}
              />
            ) : (
              <EmptyState icon="bi-graph-up" title={t('noData', language)} />
            )}
          </Card>
        </div>
        <div className="col-md-4">
          <Card title={t('costBreakdown', language)}>
            {costBreakdownData.labels.length > 0 ? (
              <PieChart
                labels={costBreakdownData.labels}
                data={costBreakdownData.data}
                height={300}
              />
            ) : (
              <EmptyState icon="bi-pie-chart" title={t('noData', language)} />
            )}
          </Card>
        </div>
      </div>

      {/* Daily Costs */}
      <Card title={t('dailyCosts', language)} className="mb-4">
        {dailyCostData.labels.length > 0 ? (
          <BarChart
            labels={dailyCostData.labels}
            tooltipLabels={dailyCostData.tooltipLabels}
            datasets={[{ label: t('cost', language), data: dailyCostData.data }]}
            height={200}
          />
        ) : (
          <EmptyState icon="bi-bar-chart" title={t('noData', language)} />
        )}
      </Card>

      {/* Efficiency Report */}
      {efficiency && (
        <Card title={t('efficiencyReport', language)} className="mb-4">
          <div className="row">
            <div className="col-md-6">
              <table className="table table-sm">
                <tbody>
                  <tr>
                    <td>{t('overallEfficiency', language)}</td>
                    <td className="text-end">{(efficiency.overall_efficiency ?? 0).toFixed(1)}%</td>
                  </tr>
                  <tr>
                    <td>{t('avgTokensPerRequest', language)}</td>
                    <td className="text-end">
                      {formatTokens(efficiency.avg_tokens_per_request ?? 0)}
                    </td>
                  </tr>
                  <tr>
                    <td>{t('avgCostPerRequest', language)}</td>
                    <td className="text-end">
                      ${(efficiency.avg_cost_per_request ?? 0).toFixed(4)}
                    </td>
                  </tr>
                  <tr>
                    <td>{t('wastePercentage', language)}</td>
                    <td className="text-end">{(efficiency.waste_percentage ?? 0).toFixed(1)}%</td>
                  </tr>
                </tbody>
              </table>
            </div>
            <div className="col-md-6">
              <h6>{t('recommendations', language)}</h6>
              <ul className="list-unstyled">
                {translateRecommendations(efficiency).map((rec, index) => (
                  <li key={index} className="mb-1">
                    <i className="bi bi-lightbulb text-warning me-2" />
                    {rec}
                  </li>
                ))}
              </ul>
            </div>
          </div>
        </Card>
      )}

      {/* Optimization Suggestions */}
      <Card title={t('optimizationSuggestions', language)}>
        {suggestions.length > 0 ? (
          <div className="table-responsive">
            <table className="table table-hover">
              <thead>
                <tr>
                  <th>{t('title', language)}</th>
                  <th>{t('description', language)}</th>
                  <th>{t('impact', language)}</th>
                  <th>{t('potentialSavings', language)}</th>
                </tr>
              </thead>
              <tbody>
                {suggestions.map((s, index) => {
                  const translated = translateSuggestion(s);
                  // Badge variant uses the RAW enum value (never the localized
                  // label), so the color stays correct across languages.
                  const priorityValue = translated.priority ?? translated.impact ?? 'low';
                  const actions = getActionItems(
                    translated.suggestion_type,
                    translated.params ?? {},
                    translated.action_items
                  );
                  const expanded = expandedIds.has(translated.suggestion_id);
                  return (
                    <React.Fragment key={translated.suggestion_id || index}>
                      <tr>
                        <td>
                          <div className="d-flex align-items-center gap-2">
                            {actions.length > 0 && (
                              <button
                                type="button"
                                className="btn btn-sm btn-link p-0 text-decoration-none"
                                onClick={() => toggleExpand(translated.suggestion_id)}
                                aria-expanded={expanded}
                                aria-label={t(expanded ? 'hideActions' : 'showActions', language)}
                              >
                                <i
                                  className={cn(
                                    'bi',
                                    expanded ? 'bi-chevron-down' : 'bi-chevron-right'
                                  )}
                                />
                              </button>
                            )}
                            <strong>{translated.title}</strong>
                          </div>
                        </td>
                        <td>{translated.description}</td>
                        <td>
                          <span className={cn('badge', `bg-${getImpactVariant(priorityValue)}`)}>
                            {translatePriority(priorityValue)}
                          </span>
                        </td>
                        <td>${(translated.potential_savings ?? 0).toFixed(2)}</td>
                      </tr>
                      {expanded && actions.length > 0 && (
                        <tr className="table-light">
                          <td colSpan={4}>
                            <div className="small">
                              <div className="fw-semibold mb-1">{t('actionItems', language)}</div>
                              <ul className="mb-0 ps-3">
                                {actions.map((a, i) => (
                                  <li key={i}>{a}</li>
                                ))}
                              </ul>
                            </div>
                          </td>
                        </tr>
                      )}
                    </React.Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState icon="bi-lightbulb" title={t('noSuggestions', language)} />
        )}
      </Card>
    </div>
  );
};
