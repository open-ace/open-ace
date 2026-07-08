/**
 * Utils Module - Export all utility functions
 */

export { cn } from './cn';
export {
  formatTokens,
  formatNumber,
  formatPercentage,
  formatDate,
  formatDateTime,
  formatTimestampWithSeconds,
  formatRelativeTime,
  formatBytes,
  formatDuration,
  formatChartDate,
  displaySessionId,
} from './format';
export {
  getDefaultDateRange,
  toLocalDateString,
  DEFAULT_DATE_RANGE_DAYS,
  type DateRange,
} from './dateRange';

export {
  onMetric,
  getMetrics,
  clearMetrics,
  trackTiming,
  startMeasure,
  trackApiCall,
  trackRender,
  getPerformanceSummary,
  initPerformanceMonitoring,
} from './performance';

const TOOL_DISPLAY_NAMES: Record<string, string> = {
  qwen: 'Qwen',
  claude: 'Claude',
  openclaw: 'OpenClaw',
  openai: 'OpenAI',
  codex: 'Codex',
  zcode: 'ZCode',
  run_shell_command: 'Shell',
};

export function formatToolName(name: string): string {
  return TOOL_DISPLAY_NAMES[name] ?? name.charAt(0).toUpperCase() + name.slice(1);
}

export { TOOL_DISPLAY_NAMES };

export { copyToClipboard } from './clipboard';

// Icon utilities
export {
  getProviderIcon,
  hasProviderIcon,
  getSupportedProviders,
} from './icons';

// Error utilities
export {
  mapApiError,
  getErrorMessage,
  isApiErrorCode,
} from './error';

// Query key utilities
export {
  hashQueryKey,
  exactMatch,
  prefixMatch,
  shouldExclude,
  matchQueryKey,
  filterQueryKeys,
  createMatcherConfig,
  type QueryKeyMatchMode,
  type QueryKeyMatcherConfig,
} from './queryKeyMatcher';

export {
  queryKeyRegistry,
  initializeQueryKeyRegistry,
  queryKeys,
  type RefreshScope,
  type QueryKeyRegistration,
} from './queryKeyRegistry';
