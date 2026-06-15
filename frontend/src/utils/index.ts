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
} from './format';
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
  run_shell_command: 'Shell',
};

export function formatToolName(name: string): string {
  return TOOL_DISPLAY_NAMES[name] ?? name.charAt(0).toUpperCase() + name.slice(1);
}

export { TOOL_DISPLAY_NAMES };

export { copyToClipboard } from './clipboard';

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
