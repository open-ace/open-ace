/**
 * Compliance Report Type Translation Utilities
 *
 * Provides translation mapping functions for compliance report types.
 * Uses i18n keys for localized display names and descriptions.
 */

import { t } from '@/i18n';
import type { Language } from '@/types';

/**
 * Supported report types - corresponds to backend ReportType enum
 * @see backend/app/models/compliance.py ReportType
 */
export type ReportTypeKey =
  | 'usage_summary'
  | 'user_activity'
  | 'audit_trail'
  | 'data_access'
  | 'security'
  | 'quota_usage'
  | 'comprehensive';

/**
 * Mapping from report type to i18n key for name
 */
const REPORT_TYPE_NAME_KEYS: Record<ReportTypeKey, string> = {
  usage_summary: 'usageSummary',
  user_activity: 'userActivity',
  audit_trail: 'auditTrail',
  data_access: 'dataAccess',
  security: 'securityReport',
  quota_usage: 'quotaUsageReport',
  comprehensive: 'comprehensiveReport',
};

/**
 * Mapping from report type to i18n key for description
 */
const REPORT_TYPE_DESC_KEYS: Record<ReportTypeKey, string> = {
  usage_summary: 'usageSummaryDesc',
  user_activity: 'userActivityDesc',
  audit_trail: 'auditTrailDesc',
  data_access: 'dataAccessDesc',
  security: 'securityReportDesc',
  quota_usage: 'quotaUsageReportDesc',
  comprehensive: 'comprehensiveReportDesc',
};

/**
 * English fallback names for report types
 */
const REPORT_TYPE_FALLBACK_NAMES: Record<ReportTypeKey, string> = {
  usage_summary: 'Usage Summary',
  user_activity: 'User Activity',
  audit_trail: 'Audit Trail',
  data_access: 'Data Access',
  security: 'Security Report',
  quota_usage: 'Quota Usage',
  comprehensive: 'Comprehensive',
};

/**
 * Get translated report type name
 * @param reportType - The report type key (e.g., 'usage_summary')
 * @param language - The language for translation
 * @param fallback - Fallback name if translation not found (defaults to English name)
 * @returns Translated name or fallback
 */
export function getReportTypeName(
  reportType: string,
  language: Language,
  fallback?: string
): string {
  const key = reportType as ReportTypeKey;
  const i18nKey = REPORT_TYPE_NAME_KEYS[key];

  if (!i18nKey) {
    // Unknown report type, return original or provided fallback
    return fallback ?? reportType;
  }

  const translated = t(i18nKey, language);
  // If translation returns the key itself (not found), use fallback
  if (translated === i18nKey) {
    return fallback ?? REPORT_TYPE_FALLBACK_NAMES[key] ?? reportType;
  }

  return translated;
}

/**
 * Get translated report type description
 * @param reportType - The report type key (e.g., 'usage_summary')
 * @param language - The language for translation
 * @returns Translated description or empty string if not found
 */
export function getReportTypeDesc(reportType: string, language: Language): string {
  const key = reportType as ReportTypeKey;
  const i18nKey = REPORT_TYPE_DESC_KEYS[key];

  if (!i18nKey) {
    return '';
  }

  const translated = t(i18nKey, language);
  // If translation returns the key itself (not found), return empty
  if (translated === i18nKey) {
    return '';
  }

  return translated;
}

/**
 * Get both translated name and description for a report type
 * @param reportType - The report type key (e.g., 'usage_summary')
 * @param language - The language for translation
 * @returns Object with name and description
 */
export function getReportTypeInfo(
  reportType: string,
  language: Language
): { name: string; description: string } {
  return {
    name: getReportTypeName(reportType, language),
    description: getReportTypeDesc(reportType, language),
  };
}