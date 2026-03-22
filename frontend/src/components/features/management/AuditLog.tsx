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
  login: 'primary',
  logout: 'secondary',
  create: 'success',
  update: 'warning',
  delete: 'danger',
  view: 'info',
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

  const actionOptions = useMemo(
    () => [
      { value: '', label: t('allActions', language) },
      { value: 'login', label: 'Login' },
      { value: 'logout', label: 'Logout' },
      { value: 'create', label: 'Create' },
      { value: 'update', label: 'Update' },
      { value: 'delete', label: 'Delete' },
      { value: 'view', label: 'View' },
    ],
    [language]
  );

  const resourceTypeOptions = useMemo(
    () => [
      { value: '', label: t('allResourceTypes', language) },
      { value: 'user', label: 'User' },
      { value: 'session', label: 'Session' },
      { value: 'message', label: 'Message' },
      { value: 'quota', label: 'Quota' },
      { value: 'settings', label: 'Settings' },
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

  const logs = data?.logs || [];
  const total = data?.total || 0;
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
              value={filters.action || ''}
              onChange={(value) => handleFilterChange('action', value)}
            />
          </div>
          <div className="col-md-3">
            <label className="form-label">{t('resourceType', language)}</label>
            <Select
              options={resourceTypeOptions}
              value={filters.resource_type || ''}
              onChange={(value) => handleFilterChange('resource_type', value)}
            />
          </div>
          <div className="col-md-3">
            <label className="form-label">{t('startDate', language)}</label>
            <input
              type="date"
              className="form-control"
              value={filters.start_date || ''}
              onChange={(e) => handleFilterChange('start_date', e.target.value)}
            />
          </div>
          <div className="col-md-3">
            <label className="form-label">{t('endDate', language)}</label>
            <input
              type="date"
              className="form-control"
              value={filters.end_date || ''}
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
                    <td>{log.username || `User ${log.user_id}`}</td>
                    <td>
                      <Badge variant={ACTION_COLORS[log.action] || 'secondary'}>{log.action}</Badge>
                    </td>
                    <td>{log.resource_type}</td>
                    <td>
                      <code>{log.resource_id}</code>
                    </td>
                    <td>
                      <small className="text-muted">{log.ip_address || '-'}</small>
                    </td>
                    <td>
                      {log.details && Object.keys(log.details).length > 0 && (
                        <button
                          className="btn btn-link btn-sm p-0"
                          onClick={() => alert(JSON.stringify(log.details, null, 2))}
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
