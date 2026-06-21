/**
 * AuditLog Component - Audit log viewer
 */

import React, { useState, useMemo } from 'react';
import { useAuditLogs } from '@/hooks';
import { useLanguage } from '@/store';
import { t } from '@/i18n';
import { Card, Button, Select, Loading, Error, EmptyState, Badge, Modal } from '@/components/common';
import type { BadgeVariant } from '@/components/common';
import { formatDateTime, getDefaultDateRange } from '@/utils';
import type { AuditLogFilters, AuditLog as AuditLogType } from '@/api/governance';

const ITEMS_PER_PAGE = 20;
// Default audit-log filter window: the last 7 days. Used both on initial
// load and after Reset so the page always queries a bounded range instead
// of an unbounded one (issue #838). 7 days balances coverage and payload
// size for an audit trail.
const AUDIT_LOG_DEFAULT_RANGE_DAYS = 7;

const getDefaultAuditFilters = (): AuditLogFilters => {
  const { start, end } = getDefaultDateRange(AUDIT_LOG_DEFAULT_RANGE_DAYS);
  return { start_date: start, end_date: end };
};

// Map backend resource_type codes to i18n keys for human-readable display.
// Mirrors the resourceTypeOptions values; a new resource type must add an
// entry here, in resourceTypeOptions, and in i18n (en/zh/ja/ko).
const RESOURCE_TYPE_LABELS: Record<string, string> = {
  audit_logs: 'resourceAuditLogs',
  quota_alert: 'resourceQuotaAlert',
  content: 'resourceContent',
  content_filter: 'resourceContentFilter',
  filter_rule: 'resourceFilterRule',
  security_settings: 'resourceSecuritySettings',
  analytics_report: 'resourceAnalyticsReport',
  analytics: 'resourceAnalytics',
  ai_agent_settings: 'resourceAiAgentSettings',
  compliance_report: 'resourceComplianceReport',
  agent_token: 'resourceAgentToken',
  remote_machine: 'resourceRemoteMachine',
  data: 'resourceData',
  session: 'resourceSession',
  user: 'resourceUser',
};

const ACTION_COLORS: Record<string, BadgeVariant> = {
  // Authentication
  login: 'primary',
  logout: 'secondary',
  login_failed: 'danger',
  session_expired: 'warning',
  // User management
  user_create: 'success',
  user_update: 'warning',
  user_delete: 'danger',
  user_password_change: 'warning',
  user_role_change: 'info',
  user_status_change: 'warning',
  // Permission
  permission_grant: 'success',
  permission_revoke: 'danger',
  // Quota
  quota_update: 'warning',
  quota_alert: 'warning',
  quota_exceeded: 'danger',
  // Data access
  data_view: 'info',
  data_export: 'primary',
  data_import: 'success',
  data_delete: 'danger',
  // System
  system_config_change: 'warning',
  system_start: 'success',
  system_stop: 'secondary',
  // Content filter
  content_blocked: 'danger',
  content_flagged: 'warning',
  // Remote agent
  agent_register: 'success',
  agent_token_rotate: 'warning',
  agent_token_revoke: 'danger',
  agent_auth_failure: 'danger',
  agent_reconnect: 'info',
};

export const AuditLog: React.FC = () => {
  const language = useLanguage();
  const [filters, setFilters] = useState<AuditLogFilters>(getDefaultAuditFilters);
  const [page, setPage] = useState(1);
  const [selectedLog, setSelectedLog] = useState<AuditLogType | null>(null);

  const { data, isLoading, isFetching, isError, error, refetch } = useAuditLogs({
    ...filters,
    page,
    limit: ITEMS_PER_PAGE,
  });

  // Action options matching backend AuditAction enum
  const actionOptions = useMemo(
    () => [
      { value: '', label: t('allActions', language) },
      // Authentication
      { value: 'login', label: t('actionLogin', language) },
      { value: 'logout', label: t('actionLogout', language) },
      { value: 'login_failed', label: t('actionLoginFailed', language) },
      { value: 'session_expired', label: t('actionSessionExpired', language) },
      // User management
      { value: 'user_create', label: t('actionUserCreate', language) },
      { value: 'user_update', label: t('actionUserUpdate', language) },
      { value: 'user_delete', label: t('actionUserDelete', language) },
      { value: 'user_password_change', label: t('actionUserPasswordChange', language) },
      { value: 'user_role_change', label: t('actionUserRoleChange', language) },
      { value: 'user_status_change', label: t('actionUserStatusChange', language) },
      // Permission
      { value: 'permission_grant', label: t('actionPermissionGrant', language) },
      { value: 'permission_revoke', label: t('actionPermissionRevoke', language) },
      // Quota
      { value: 'quota_update', label: t('actionQuotaUpdate', language) },
      { value: 'quota_alert', label: t('actionQuotaAlert', language) },
      { value: 'quota_exceeded', label: t('actionQuotaExceeded', language) },
      // Data access
      { value: 'data_view', label: t('actionDataView', language) },
      { value: 'data_export', label: t('actionDataExport', language) },
      { value: 'data_import', label: t('actionDataImport', language) },
      { value: 'data_delete', label: t('actionDataDelete', language) },
      // System
      { value: 'system_config_change', label: t('actionSystemConfigChange', language) },
      { value: 'system_start', label: t('actionSystemStart', language) },
      { value: 'system_stop', label: t('actionSystemStop', language) },
      // Content filter
      { value: 'content_blocked', label: t('actionContentBlocked', language) },
      { value: 'content_flagged', label: t('actionContentFlagged', language) },
      // Remote agent
      { value: 'agent_register', label: t('actionAgentRegister', language) },
      { value: 'agent_token_rotate', label: t('actionAgentTokenRotate', language) },
      { value: 'agent_token_revoke', label: t('actionAgentTokenRevoke', language) },
      { value: 'agent_auth_failure', label: t('actionAgentAuthFailure', language) },
      { value: 'agent_reconnect', label: t('actionAgentReconnect', language) },
    ],
    [language]
  );

  // Resource type options matching backend actual usage
  const resourceTypeOptions = useMemo(
    () => [
      { value: '', label: t('allResourceTypes', language) },
      { value: 'audit_logs', label: t('resourceAuditLogs', language) },
      { value: 'quota_alert', label: t('resourceQuotaAlert', language) },
      { value: 'content', label: t('resourceContent', language) },
      { value: 'content_filter', label: t('resourceContentFilter', language) },
      { value: 'filter_rule', label: t('resourceFilterRule', language) },
      { value: 'security_settings', label: t('resourceSecuritySettings', language) },
      { value: 'analytics_report', label: t('resourceAnalyticsReport', language) },
      { value: 'analytics', label: t('resourceAnalytics', language) },
      { value: 'ai_agent_settings', label: t('resourceAiAgentSettings', language) },
      { value: 'compliance_report', label: t('resourceComplianceReport', language) },
      { value: 'agent_token', label: t('resourceAgentToken', language) },
      { value: 'remote_machine', label: t('resourceRemoteMachine', language) },
      { value: 'data', label: t('resourceData', language) },
      { value: 'session', label: t('resourceSession', language) },
      { value: 'user', label: t('resourceUser', language) },
    ],
    [language]
  );

  const handleFilterChange = (key: keyof AuditLogFilters, value: string) => {
    setFilters((prev) => {
      const next = { ...prev, [key]: value || undefined };
      // Clear an out-of-order end date, mirroring Messages.tsx, so the
      // range stays valid as the user edits the date inputs.
      if (next.start_date && next.end_date && next.end_date < next.start_date) {
        next.end_date = undefined;
      }
      return next;
    });
    setPage(1);
  };

  const handleReset = () => {
    setFilters(getDefaultAuditFilters());
    setPage(1);
  };

  // Render a localized resource-type label, falling back to the raw code.
  const renderResourceType = (code: string | null | undefined): string => {
    if (!code) return '-';
    const key = RESOURCE_TYPE_LABELS[code];
    return key ? t(key, language) : code;
  };

  // details is normalized to an object by the backend, but guard defensively
  // against legacy/malformed rows so Object.keys never degrades.
  const getDetails = (log: AuditLogType): Record<string, unknown> =>
    log.details && typeof log.details === 'object' ? (log.details as Record<string, unknown>) : {};

  const hasDetails = (log: AuditLogType): boolean => Object.keys(getDetails(log)).length > 0;

  const getResourceName = (log: AuditLogType): string | null => {
    const name = getDetails(log).resource_name;
    return typeof name === 'string' && name ? name : null;
  };

  if (isError) {
    return <Error message={error?.message || t('error', language)} onRetry={() => refetch()} />;
  }

  const logs = data?.logs ?? [];
  const total = data?.total ?? 0;
  const totalPages = Math.ceil(total / ITEMS_PER_PAGE);

  return (
    <div className="audit-log">
      {/* Header */}
      <div className="d-flex justify-content-between align-items-center mb-3">
        <h5>{t('auditLog', language)}</h5>
        <Button variant="primary" size="sm" onClick={() => refetch()} loading={isFetching}>
          {isFetching ? null : <i className="bi bi-arrow-clockwise me-1" />}
          {t('refresh', language)}
        </Button>
      </div>

      {/* Filters */}
      <Card className="mb-3">
        <div className="row g-3">
          <div className="col-md-3">
            <label className="form-label">{t('tableAction', language)}</label>
            <Select
              options={actionOptions}
              value={filters.action ?? ''}
              onChange={(value) => handleFilterChange('action', value)}
            />
          </div>
          <div className="col-md-3">
            <label className="form-label">{t('resourceType', language)}</label>
            <Select
              options={resourceTypeOptions}
              value={filters.resource_type ?? ''}
              onChange={(value) => handleFilterChange('resource_type', value)}
            />
          </div>
          <div className="col-md-3">
            <label className="form-label">{t('startDate', language)}</label>
            <input
              type="date"
              className="form-control"
              value={filters.start_date ?? ''}
              onChange={(e) => handleFilterChange('start_date', e.target.value)}
            />
          </div>
          <div className="col-md-3">
            <label className="form-label">{t('endDate', language)}</label>
            <input
              type="date"
              className="form-control"
              value={filters.end_date ?? ''}
              onChange={(e) => handleFilterChange('end_date', e.target.value)}
            />
          </div>
        </div>
        <div className="mt-3">
          <Button variant="secondary" size="sm" onClick={handleReset}>
            {t('reset', language)}
          </Button>
        </div>
      </Card>

      {/* Stats */}
      {total > 0 && (
        <div className="mb-3">
          <span className="text-muted">
            {t('total', language)}: {total.toLocaleString()} {t('records', language)}
          </span>
        </div>
      )}

      {/* Log List */}
      {isLoading ? (
        <Loading size="lg" text={t('loading', language)} />
      ) : logs.length === 0 ? (
        <EmptyState icon="bi-journal-text" title={t('noAuditLogs', language)} />
      ) : (
        <>
          <div className="table-responsive">
            <table className="table table-hover">
              <thead>
                <tr>
                  <th>{t('tableTimestamp', language)}</th>
                  <th>{t('tableUser', language)}</th>
                  <th>{t('tableAction', language)}</th>
                  <th>{t('resourceType', language)}</th>
                  <th>{t('resourceId', language)}</th>
                  <th>{t('tableIpAddress', language)}</th>
                  <th>{t('tableDetails', language)}</th>
                </tr>
              </thead>
              <tbody>
                {logs.map((log) => (
                  <tr key={log.id}>
                    <td>
                      <small>{formatDateTime(log.timestamp)}</small>
                    </td>
                    <td>{log.username ?? `User ${log.user_id}`}</td>
                    <td>
                      <Badge variant={ACTION_COLORS[log.action] ?? 'secondary'}>{log.action}</Badge>
                    </td>
                    <td>{renderResourceType(log.resource_type)}</td>
                    <td>
                      {log.resource_id ? (
                        <code title={getResourceName(log) ?? undefined}>{log.resource_id}</code>
                      ) : (
                        <span className="text-muted">-</span>
                      )}
                    </td>
                    <td>
                      <small className="text-muted">{log.ip_address ?? '-'}</small>
                    </td>
                    <td>
                      {hasDetails(log) && (
                        <button
                          className="btn btn-link btn-sm p-0"
                          onClick={() => setSelectedLog(log)}
                          title={t('tableDetails', language)}
                        >
                          <i className="bi bi-eye" />
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Pagination */}
          {totalPages > 1 && (
            <div className="d-flex justify-content-center mt-4">
              <nav>
                <ul className="pagination">
                  <li className={`page-item ${page === 1 ? 'disabled' : ''}`}>
                    <button
                      className="page-link"
                      onClick={() => setPage(page - 1)}
                      disabled={page === 1}
                    >
                      {t('previous', language)}
                    </button>
                  </li>
                  {Array.from({ length: Math.min(5, totalPages) }, (_, i) => {
                    const pageNum = i + 1;
                    return (
                      <li key={pageNum} className={`page-item ${page === pageNum ? 'active' : ''}`}>
                        <button className="page-link" onClick={() => setPage(pageNum)}>
                          {pageNum}
                        </button>
                      </li>
                    );
                  })}
                  <li className={`page-item ${page === totalPages ? 'disabled' : ''}`}>
                    <button
                      className="page-link"
                      onClick={() => setPage(page + 1)}
                      disabled={page === totalPages}
                    >
                      {t('next', language)}
                    </button>
                  </li>
                </ul>
              </nav>
            </div>
          )}
        </>
      )}

      {/* Details viewer (replaces window.alert) */}
      <Modal
        isOpen={selectedLog !== null}
        onClose={() => setSelectedLog(null)}
        title={
          selectedLog
            ? `${renderResourceType(selectedLog.resource_type)}${
                selectedLog.resource_id ? ` · ${selectedLog.resource_id}` : ''
              }`
            : ''
        }
        size="lg"
      >
        {selectedLog && (
          <pre
            className="mb-0"
            style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}
          >
            {JSON.stringify(selectedLog.details, null, 2)}
          </pre>
        )}
      </Modal>
    </div>
  );
};
