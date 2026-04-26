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
  'qwen': 'Qwen',
  'qwen-code': 'Qwen',
  'qwen-code-cli': 'Qwen',
  'claude': 'Claude',
  'claude-code': 'Claude',
  'openclaw': 'OpenClaw',
  'openai': 'OpenAI',
  'run_shell_command': 'Shell',
};

export function formatToolName(name: string): string {
  return TOOL_DISPLAY_NAMES[name] ?? name.charAt(0).toUpperCase() + name.slice(1);
}

export { TOOL_DISPLAY_NAMES };
