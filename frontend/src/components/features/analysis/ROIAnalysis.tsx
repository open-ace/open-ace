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
  LineChart,
  PieChart,
  BarChart,
  Skeleton,
  SkeletonCard,
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
import { formatTokens } from '@/utils';

// Cache key generator
const getCacheKey = (startDate: string, endDate: string, tool: string) =>
  `roi_${startDate}_${endDate}_${tool}`;

// Simple cache for ROI data
const roiDataCache = new Map<string, {
  data: CachedData;
  timestamp: number;
}>();

interface CachedData {
  roiMetrics: ROIMetrics | null;
  roiTrend: ROITrend[];
  costBreakdown: CostBreakdown[];
  dailyCosts: DailyCost[];
  suggestions: OptimizationSuggestion[];
  efficiency: EfficiencyReport | null;
}

const CACHE_TTL = 5 * 60 * 1000; // 5 minutes

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
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [startDate, setStartDate] = useState('');
  const [endDate, setEndDate] = useState('');
  const [selectedTool, setSelectedTool] = useState('');

  // Track if this is the initial load
  const isInitialLoad = useRef(true);

  // Initialize dates
  useEffect(() => {
    const end = new Date();
    const start = new Date(Date.now() - 30 * 24 * 60 * 60 * 1000);
    setEndDate(end.toISOString().split('T')[0]);
    setStartDate(start.toISOString().split('T')[0]);
  }, []);

  // Fetch data with caching
  const fetchData = useCallback(async (forceRefresh = false) => {
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
    } else {
      setIsRefreshing(true);
    }
    setError(null);

    try {
      const [roi, trend, breakdown, daily, sugg, eff] = await Promise.all([
        roiApi.getROI({ start_date: startDate, end_date: endDate, tool_name: selectedTool || undefined }),
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
      setIsRefreshing(false);
    }
  }, [startDate, endDate, selectedTool]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  // Tool options
  const toolOptions = useMemo(
    () => [
      { value: '', label: t('dashboardFilterAllTools', language) },
      { value: 'openclaw', label: 'OpenClaw' },
      { value: 'claude', label: 'Claude' },
      { value: 'qwen', label: 'Qwen' },
    ],
    [language]
  );

  // Chart data
  const roiTrendData = useMemo(() => {
    if (!roiTrend.length) return { labels: [], data: [] };
    return {
      labels: roiTrend.map((item) => item.month || item.period?.split(' to ')[0] || ''),
      data: roiTrend.map((item) => item.roi_percentage),
    };
  }, [roiTrend]);

  const dailyCostData = useMemo(() => {
    if (!dailyCosts.length) return { labels: [], data: [] };
    return {
      labels: dailyCosts.map((d) => d.date),
      data: dailyCosts.map((d) => d.cost || d.total_cost || 0),
    };
  }, [dailyCosts]);

  const costBreakdownData = useMemo(() => {
    if (!costBreakdown.length) return { labels: [], data: [] };
    return {
      labels: costBreakdown.map((c) => c.category || c.tool_name || c.model || 'Unknown'),
      data: costBreakdown.map((c) => c.total_cost),
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
        <Button
          variant="primary"
          size="sm"
          onClick={() => fetchData(true)}
          disabled={isRefreshing}
        >
          {isRefreshing ? (
            <>
              <Loading size="sm" className="me-1" />
              {t('loading', language)}
            </>
          ) : (
            <>
              <i className="bi bi-arrow-clockwise me-1" />
              {t('refresh', language)}
            </>
          )}
        </Button>
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
              value={`${roiMetrics.roi_percentage.toFixed(1)}%`}
              icon={<i className="bi bi-graph-up-arrow fs-4" />}
              variant={roiMetrics.roi_percentage >= 0 ? 'success' : 'danger'}
            />
          </div>
          <div className="col-md-3">
            <StatCard
              label={t('efficiencyScore', language)}
              value={`${(roiMetrics.efficiency_score || 85).toFixed(0)}%`}
              icon={<i className="bi bi-speedometer2 fs-4" />}
              variant={(roiMetrics.efficiency_score || 85) >= 80 ? 'success' : (roiMetrics.efficiency_score || 85) >= 60 ? 'warning' : 'danger'}
            />
          </div>
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
                    <td className="text-end">{(efficiency.overall_efficiency || 0).toFixed(1)}%</td>
                  </tr>
                  <tr>
                    <td>{t('avgTokensPerRequest', language)}</td>
                    <td className="text-end">{formatTokens(efficiency.avg_tokens_per_request || 0)}</td>
                  </tr>
                  <tr>
                    <td>{t('avgCostPerRequest', language)}</td>
                    <td className="text-end">${(efficiency.avg_cost_per_request || 0).toFixed(4)}</td>
                  </tr>
                  <tr>
                    <td>{t('wastePercentage', language)}</td>
                    <td className="text-end">{(efficiency.waste_percentage || 0).toFixed(1)}%</td>
                  </tr>
                </tbody>
              </table>
            </div>
            <div className="col-md-6">
              <h6>{t('recommendations', language)}</h6>
              <ul className="list-unstyled">
                {(efficiency.recommendations || []).map((rec, index) => (
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
                {suggestions.map((s, index) => (
                  <tr key={index}>
                    <td>
                      <strong>{s.title}</strong>
                    </td>
                    <td>{s.description}</td>
                    <td>
                      <span className={cn('badge', `bg-${getImpactVariant(s.impact || s.priority || 'low')}`)}>
                        {s.impact || s.priority || 'low'}
                      </span>
                    </td>
                    <td>${(s.potential_savings || 0).toFixed(2)}</td>
                  </tr>
                ))}
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