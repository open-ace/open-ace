/**
 * CleanupPreviewContent Component - Visualizes data retention cleanup preview results
 *
 * Features:
 * - Statistics summary cards (deleted/archived/anonymized records)
 * - Rules execution details table
 * - Error messages display
 * - Timestamp display
 */

import React from 'react';
import { useLanguage } from '@/store';
import { t } from '@/i18n';
import type { Language } from '@/types';
import { Card, Badge, EmptyState } from '@/components/common';
import { type RetentionReport, type AppliedRule } from '@/api';
import { formatDateTime } from '@/utils';
import { cn } from '@/utils';
import { getDataTypeLabel, getDataTypeIcon } from './DataRetention';

/**
 * Get action badge variant
 */
function getActionBadgeVariant(
  action: 'delete' | 'archive' | 'anonymize'
): 'danger' | 'info' | 'warning' {
  switch (action) {
    case 'delete':
      return 'danger';
    case 'archive':
      return 'info';
    case 'anonymize':
      return 'warning';
  }
}

/**
 * Get action label
 */
function getActionLabel(action: 'delete' | 'archive' | 'anonymize', language: Language): string {
  switch (action) {
    case 'delete':
      return t('actionDelete', language);
    case 'archive':
      return t('actionArchive', language);
    case 'anonymize':
      return t('actionAnonymize', language);
  }
}

interface CleanupPreviewContentProps {
  report: RetentionReport;
}

export const CleanupPreviewContent: React.FC<CleanupPreviewContentProps> = ({ report }) => {
  const language = useLanguage();

  // Calculate total affected records
  const totalAffectedRecords = report.rules_applied.reduce(
    (sum: number, rule: AppliedRule) => sum + rule.records_affected,
    0
  );

  return (
    <div className="cleanup-preview-content">
      {/* Timestamp */}
      <div className="d-flex justify-content-end mb-3">
        <small className="text-muted">
          {t('executedAt', language)}: {formatDateTime(report.timestamp)}
        </small>
      </div>

      {/* Statistics Cards */}
      <div className="row g-3 mb-4">
        <div className="col-6 col-md-3">
          <div className="card border-danger h-100">
            <div className="card-body text-center">
              <i className="bi bi-trash text-danger fs-3 mb-2" />
              <h4 className="mb-1">{report.records_deleted.toLocaleString()}</h4>
              <small className="text-muted">{t('recordsDeleted', language)}</small>
            </div>
          </div>
        </div>
        <div className="col-6 col-md-3">
          <div className="card border-info h-100">
            <div className="card-body text-center">
              <i className="bi bi-archive text-info fs-3 mb-2" />
              <h4 className="mb-1">{report.records_archived.toLocaleString()}</h4>
              <small className="text-muted">{t('recordsArchived', language)}</small>
            </div>
          </div>
        </div>
        <div className="col-6 col-md-3">
          <div className="card border-warning h-100">
            <div className="card-body text-center">
              <i className="bi bi-shield-check text-warning fs-3 mb-2" />
              <h4 className="mb-1">{report.records_anonymized.toLocaleString()}</h4>
              <small className="text-muted">{t('recordsAnonymized', language)}</small>
            </div>
          </div>
        </div>
        <div className="col-6 col-md-3">
          <div className="card border-secondary h-100">
            <div className="card-body text-center">
              <i className="bi bi-database text-secondary fs-3 mb-2" />
              <h4 className="mb-1">{totalAffectedRecords.toLocaleString()}</h4>
              <small className="text-muted">{t('totalAffectedRecords', language)}</small>
            </div>
          </div>
        </div>
      </div>

      {/* Rules Execution Details */}
      <Card title={t('executionDetails', language)} className="mb-3">
        {report.rules_applied.length === 0 ? (
          <EmptyState
            icon="bi-check-circle"
            title={t('noRulesApplied', language)}
            description={t('cleanupPreviewDescription', language)}
          />
        ) : (
          <div className="table-responsive">
            <table className="table table-hover">
              <thead>
                <tr>
                  <th>{t('dataType', language)}</th>
                  <th>{t('action', language)}</th>
                  <th>{t('cutoffDate', language)}</th>
                  <th>{t('affectedRecords', language)}</th>
                </tr>
              </thead>
              <tbody>
                {report.rules_applied.map((rule: AppliedRule, index: number) => (
                  <tr key={index}>
                    <td>
                      <div className="d-flex align-items-center">
                        <i className={cn('bi me-2', getDataTypeIcon(rule.data_type))} />
                        <strong>{getDataTypeLabel(rule.data_type, language)}</strong>
                      </div>
                    </td>
                    <td>
                      <Badge variant={getActionBadgeVariant(rule.action)}>
                        {getActionLabel(rule.action, language)}
                      </Badge>
                    </td>
                    <td>{formatDateTime(rule.cutoff)}</td>
                    <td>{rule.records_affected.toLocaleString()}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {/* Errors */}
      {report.errors && report.errors.length > 0 && (
        <div className="alert alert-warning" role="alert">
          <div className="d-flex align-items-center mb-2">
            <i className="bi bi-exclamation-triangle me-2" />
            <strong>{t('error', language)}</strong>
          </div>
          <ul className="mb-0">
            {report.errors.map((error: string, index: number) => (
              <li key={index}>{error}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
};
