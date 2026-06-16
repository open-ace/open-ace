/**
 * AuditLog Component - Audit log viewer
 */

import React, { useState, useMemo } from 'react';
import { useAuditLogs } from '@/hooks';
import { useLanguage } from '@/store';
import { t } from '@/i18n';
import { Card, Button, Select, Loading, Error, EmptyState, Badge } from '@/components/common';
import type { BadgeVariant } from '@/components/common';
import { formatDateTime } from '@/utils';
import type { AuditLogFilters } from '@/api';

const ITEMS_PER_PAGE = 20;

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
  const [filters, setFilters] = useState<AuditLogFilters>({});
  const [page, setPage] = useState(1);

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
    ],
    [language]
  );

  const handleFilterChange = (key: keyof AuditLogFilters, value: string) => {
    setFilters((prev) => ({ ...prev, [key]: value || undefined }));
    setPage(1);
  };

  const handleReset = () => {
    setFilters({});
    setPage(1);
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
                    <td>{log.resource_type}</td>
                    <td>
                      <code>{log.resource_id}</code>
                    </td>
                    <td>
                      <small className="text-muted">{log.ip_address ?? '-'}</small>
                    </td>
                    <td>
                      {log.details && Object.keys(log.details).length > 0 && (
                        <button
                          className="btn btn-link btn-sm p-0"
                          onClick={() => window.alert(JSON.stringify(log.details, null, 2))}
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
    </div>
  );
};
