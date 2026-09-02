/**
 * Enum Translation Utilities
 *
 * Provides type-safe translation functions for backend enum values.
 * This ensures that enums like trend directions, anomaly types, metrics,
 * and severities are properly localized in all supported languages.
 */

import { t, type Language, type TranslationKey } from '@/i18n';

/**
 * Translate trend direction enum values
 */
export const getTrendDirectionLabel = (direction: string, language: Language): string => {
  const labels: Record<string, TranslationKey> = {
    up: 'directionUp',
    down: 'directionDown',
    stable: 'directionStable',
  };
  const key = labels[direction];
  return key ? t(key, language) : direction;
};

/**
 * Translate anomaly type enum values
 */
export const getAnomalyTypeLabel = (type: string, language: Language): string => {
  const labels: Record<string, TranslationKey> = {
    spike: 'anomalyTypeSpike',
    drop: 'anomalyTypeDrop',
    unusual_pattern: 'anomalyTypeUnusualPattern',
  };
  const key = labels[type];
  return key ? t(key, language) : type;
};

/**
 * Translate metric name enum values
 */
export const getMetricLabel = (metric: string, language: Language): string => {
  const labels: Record<string, TranslationKey> = {
    tokens: 'metricTokens',
    requests: 'metricRequests',
  };
  const key = labels[metric];
  return key ? t(key, language) : metric;
};

/**
 * Translate severity enum values
 * Reuses existing severityLow/Medium/High translation keys
 */
export const getSeverityLabel = (severity: string, language: Language): string => {
  const labels: Record<string, TranslationKey> = {
    low: 'severityLow',
    medium: 'severityMedium',
    high: 'severityHigh',
  };
  const key = labels[severity];
  return key ? t(key, language) : severity;
};